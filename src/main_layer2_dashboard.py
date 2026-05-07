from __future__ import annotations

import argparse
import html
import json
import threading
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from layer1_intake import IntakeResult
from layer2_runtime_governance import (
    HumanReviewDecision,
    HumanReviewDecisionType,
    build_default_checkpointer,
    build_governance_hitl_dashboard_snapshot,
    governance_hitl_dashboard_snapshot_to_dict,
    governance_hitl_dashboard_snapshot_to_json,
)
from layer2_runtime_governance.state import TaskState

from main_layer2 import run_layer2_flow, resume_layer2_flow


# Default host for the local dashboard server.
DEFAULT_HOST = "127.0.0.1"


# Default port for the local dashboard server.
DEFAULT_PORT = 8022


# DashboardRunner is the callable used to start or resume a Layer 2 run.
DashboardRunner = Callable[..., dict[str, Any]]


# DashboardSession keeps the active run, its checkpoint store, and the last result.
@dataclass
class DashboardSession:
    thread_id: str
    intake_result: IntakeResult
    final_result: dict[str, Any]
    checkpointer: Any
    resume_flow: DashboardRunner = resume_layer2_flow
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


# DashboardLaunchHandle keeps the background server thread and launch metadata.
@dataclass
class DashboardLaunchHandle:
    thread_id: str
    host: str
    port: int
    url: str
    thread: threading.Thread


_DASHBOARD_LAUNCH_LOCK = threading.Lock()
_DASHBOARD_LAUNCHES: dict[str, DashboardLaunchHandle] = {}
_DASHBOARD_ASSETS_DIR = Path(__file__).resolve().parent / "dashboard_assets"
_DASHBOARD_HTML_TEMPLATE = (_DASHBOARD_ASSETS_DIR / "dashboard.html").read_text(encoding="utf-8")
_DASHBOARD_CSS = (_DASHBOARD_ASSETS_DIR / "dashboard.css").read_text(encoding="utf-8")
_DASHBOARD_JS = (_DASHBOARD_ASSETS_DIR / "dashboard.js").read_text(encoding="utf-8")


# Create an initial dashboard session by running Layer 2 once.
def create_dashboard_session(
    *,
    raw_task_text: str,
    context_hint: str | None = None,
    policy_profile: str | None = None,
    thread_id: str | None = None,
    demo_intake_profile: str | None = None,
    run_flow: DashboardRunner = run_layer2_flow,
    resume_flow: DashboardRunner = resume_layer2_flow,
    checkpointer: Any | None = None,
) -> DashboardSession:
    resolved_checkpointer = checkpointer or build_default_checkpointer()
    resolved_thread_id = thread_id or f"layer2-dashboard-{_short_token(raw_task_text)}"

    run_result = run_flow(
        raw_task_text=raw_task_text,
        context_hint=context_hint,
        policy_profile=policy_profile,
        thread_id=resolved_thread_id,
        demo_intake_profile=demo_intake_profile,
        checkpointer=resolved_checkpointer,
    )

    return DashboardSession(
        thread_id=run_result["thread_id"],
        intake_result=run_result["intake_result"],
        final_result=run_result["final_result"],
        checkpointer=resolved_checkpointer,
        resume_flow=resume_flow,
    )


# Rebuild a dashboard session from an existing trace payload.
def build_dashboard_session_from_trace(
    trace: dict[str, Any],
    *,
    checkpointer: Any,
    resume_flow: DashboardRunner = resume_layer2_flow,
) -> DashboardSession:
    return DashboardSession(
        thread_id=trace["thread_id"],
        intake_result=trace["intake_result"],
        final_result=trace["final_result"],
        checkpointer=checkpointer,
        resume_flow=resume_flow,
    )


# Resume the active dashboard session with one typed human decision.
def resume_dashboard_session(
    session: DashboardSession,
    human_review_decision: HumanReviewDecision,
) -> dict[str, Any]:
    with session.lock:
        final_result = session.resume_flow(
            thread_id=session.thread_id,
            human_review_decision=human_review_decision,
            checkpointer=session.checkpointer,
        )
        session.final_result = final_result["final_result"]
        return final_result


# Return the canonical TaskState currently held by the dashboard session.
def get_dashboard_task_state(session: DashboardSession) -> TaskState:
    with session.lock:
        return _get_dashboard_task_state_unlocked(session)


# Build the dashboard snapshot for the current session.
def build_dashboard_snapshot(session: DashboardSession):
    return build_governance_hitl_dashboard_snapshot(get_dashboard_task_state(session))


# Render a local HTML control plane from the current dashboard snapshot.
def render_dashboard_page(session: DashboardSession) -> str:
    with session.lock:
        task_state = _get_dashboard_task_state_unlocked(session)
        snapshot = build_governance_hitl_dashboard_snapshot(task_state)
        snapshot_dict = governance_hitl_dashboard_snapshot_to_dict(snapshot)
        snapshot_json = governance_hitl_dashboard_snapshot_to_json(snapshot)
        actor_turn_count = task_state.actor_turn_count
        tool_call_count = task_state.tool_call_count
        dashboard_mode = _resolve_dashboard_mode(snapshot.focus.value)
        auto_refresh_enabled = dashboard_mode in {"live", "paused"}
        rendered_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return _render_html_document(
        title="Governance HITL Control Plane",
        dashboard_mode=dashboard_mode,
        auto_refresh_enabled=auto_refresh_enabled,
        rendered_at=rendered_at,
        snapshot_json=snapshot_json if auto_refresh_enabled else None,
        body=_render_dashboard_body(
            session=session,
            snapshot=snapshot,
            snapshot_dict=snapshot_dict,
            actor_turn_count=actor_turn_count,
            tool_call_count=tool_call_count,
            dashboard_mode=dashboard_mode,
            auto_refresh_enabled=auto_refresh_enabled,
            rendered_at=rendered_at,
        ),
    )


# Render a machine-readable snapshot for debugging and future integrations.
def render_dashboard_snapshot_json(session: DashboardSession) -> str:
    with session.lock:
        snapshot = build_governance_hitl_dashboard_snapshot(
            _get_dashboard_task_state_unlocked(session)
        )
        return governance_hitl_dashboard_snapshot_to_json(snapshot)


# Read the current TaskState without taking the session lock again.
def _get_dashboard_task_state_unlocked(session: DashboardSession) -> TaskState:
    task_state = session.final_result.get("task_state")
    if not isinstance(task_state, TaskState):
        raise ValueError("Dashboard session does not contain a canonical task_state.")
    return task_state


# Serve the dashboard over HTTP using a shared in-memory session.
def serve_dashboard(
    *,
    session: DashboardSession,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
) -> None:
    handler = _build_request_handler(session=session)
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"Dashboard server listening on {url}", flush=True)
    print(f"Current thread id: {session.thread_id}", flush=True)

    if open_browser:
        webbrowser.open_new_tab(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


# Launch the dashboard in a background thread and reuse the same server for the same thread id.
def launch_dashboard_session(
    *,
    session: DashboardSession,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    serve_dashboard_fn: Callable[..., None] = serve_dashboard,
) -> DashboardLaunchHandle:
    with _DASHBOARD_LAUNCH_LOCK:
        existing_handle = _DASHBOARD_LAUNCHES.get(session.thread_id)
        if existing_handle is not None:
            return existing_handle

        url = f"http://{host}:{port}"

        def _serve() -> None:
            # Run the HTTP server in the background so the CLI can continue after auto-launch.
            serve_dashboard_fn(
                session=session,
                host=host,
                port=port,
                open_browser=open_browser,
            )

        server_thread = threading.Thread(
            target=_serve,
            name=f"hitl-dashboard-{session.thread_id}",
            daemon=False,
        )
        handle = DashboardLaunchHandle(
            thread_id=session.thread_id,
            host=host,
            port=port,
            url=url,
            thread=server_thread,
        )
        _DASHBOARD_LAUNCHES[session.thread_id] = handle
        server_thread.start()
        return handle


# Build the HTTP handler class around a specific dashboard session.
def _build_request_handler(*, session: DashboardSession):
    class DashboardRequestHandler(BaseHTTPRequestHandler):
        dashboard_session = session

        # Serve GET endpoints for HTML and JSON snapshot views.
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/index.html"}:
                self._write_html(render_dashboard_page(self.dashboard_session))
                return
            if parsed.path == "/assets/dashboard.css":
                self._write_text(_DASHBOARD_CSS, content_type="text/css; charset=utf-8")
                return
            if parsed.path == "/assets/dashboard.js":
                self._write_text(_DASHBOARD_JS, content_type="application/javascript; charset=utf-8")
                return
            if parsed.path == "/snapshot.json":
                self._write_json(render_dashboard_snapshot_json(self.dashboard_session))
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown dashboard route")

        # Serve the typed human review resume endpoint.
        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/resume":
                self._handle_resume_request()
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown dashboard route")

        # Silence the default HTTP request logging so the control plane stays readable.
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        # Handle one human review submission and redirect back to the dashboard view.
        def _handle_resume_request(self) -> None:
            try:
                payload = self._read_form_payload()
                decision = _parse_human_review_decision(payload, session=self.dashboard_session)
                resume_dashboard_session(self.dashboard_session, decision)
                self._redirect("/")
            except ValueError as exc:
                self._write_html(
                    _render_html_document(
                        title="Resume error",
                        body=_render_error_message(str(exc)),
                        dashboard_mode="terminal",
                        auto_refresh_enabled=False,
                        rendered_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        snapshot_json=None,
                    ),
                    status=HTTPStatus.BAD_REQUEST,
                )

        # Read application/x-www-form-urlencoded request bodies.
        def _read_form_payload(self) -> dict[str, str]:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length).decode("utf-8")
            parsed = parse_qs(raw_body, keep_blank_values=True)
            return {key: values[0] if values else "" for key, values in parsed.items()}

        # Write one HTML response body.
        def _write_html(self, body: str, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            self._write_text(body, content_type="text/html; charset=utf-8", status=status)

        # Write one text-based response body for HTML, JS, and CSS.
        def _write_text(
            self,
            body: str,
            *,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            for header_name, header_value in _build_response_headers(content_type).items():
                self.send_header(header_name, header_value)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        # Write one JSON response body.
        def _write_json(self, body: str, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            for header_name, header_value in _build_response_headers(
                "application/json; charset=utf-8"
            ).items():
                self.send_header(header_name, header_value)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        # Redirect the browser back to the dashboard shell after a successful submit.
        def _redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            self.end_headers()

    return DashboardRequestHandler


# Parse command line options for the local dashboard server.
def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Layer 2 HITL control plane.")
    parser.add_argument(
        "--task",
        default="Find official sources about the Digital Markets Act and produce a short summary.",
        help="Raw task text to parse through Layer 1 and bootstrap into Layer 2.",
    )
    parser.add_argument(
        "--context",
        default="Prefer official EU sources.",
        help="Optional context hint for Layer 1.",
    )
    parser.add_argument(
        "--policy-profile",
        default=None,
        help="Optional named execution policy profile.",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Optional LangGraph thread identifier.",
    )
    parser.add_argument(
        "--demo-intake-profile",
        choices=["research", "clarification"],
        default=None,
        help="Use a local deterministic intake payload instead of invoking Layer 1 via OpenAI.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="HTTP host for the dashboard server.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="HTTP port for the dashboard server.",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the dashboard in the default browser after startup.",
    )
    return parser.parse_args()


# Start the dashboard from command line arguments.
def main() -> None:
    args = parse_cli_args()
    session = create_dashboard_session(
        raw_task_text=args.task,
        context_hint=args.context,
        policy_profile=args.policy_profile,
        thread_id=args.thread_id,
        demo_intake_profile=args.demo_intake_profile,
    )
    serve_dashboard(session=session, host=args.host, port=args.port, open_browser=args.open_browser)


# Render the page body with the task header and the runtime panels.
def _render_dashboard_body(
    *,
    session: DashboardSession,
    snapshot,
    snapshot_dict: dict[str, Any],
    actor_turn_count: int,
    tool_call_count: int,
    dashboard_mode: str,
    auto_refresh_enabled: bool,
    rendered_at: str,
) -> str:
    task_spec = session.intake_result.structured_task.task_spec
    metadata = session.intake_result.metadata
    focus_title, focus_summary = _describe_dashboard_focus(snapshot, auto_refresh_enabled)

    sections = [
        _render_dashboard_banner(
            session=session,
            snapshot=snapshot,
            focus_title=focus_title,
            focus_summary=focus_summary,
            task_spec_goal=task_spec.goal,
            task_spec_context=task_spec.context,
            policy_profile=session.intake_result.structured_task.execution_policy.policy_profile_name,
            parse_status=metadata.parse_status.value,
            actor_turn_count=actor_turn_count,
            tool_call_count=tool_call_count,
            dashboard_mode=dashboard_mode,
            auto_refresh_enabled=auto_refresh_enabled,
            rendered_at=rendered_at,
        ),
        """
        <div class="dashboard-grid">
        """
        + _render_plan_card(snapshot.current_plan_step)
        + _render_governance_card(snapshot.governance)
        + _render_oversight_card(snapshot.oversight)
        + _render_human_review_card(snapshot, snapshot_dict, session)
        + _render_snapshot_card(snapshot_dict)
        + """
        </div>
        """,
    ]

    return "\n".join(section for section in sections if section)


# Render the top overview card and live status banner for the active task.
def _render_dashboard_banner(
    *,
    session: DashboardSession,
    snapshot,
    focus_title: str,
    focus_summary: str,
    task_spec_goal: str,
    task_spec_context: str,
    policy_profile: str,
    parse_status: str,
    actor_turn_count: int,
    tool_call_count: int,
    dashboard_mode: str,
    auto_refresh_enabled: bool,
    rendered_at: str,
) -> str:
    status_strip = f"""
      <div class="status-strip">
        <span class="status-pill status-pill-{html.escape(dashboard_mode)}">{html.escape(dashboard_mode)}</span>
        <span class="status-meta">Auto refresh: {_format_bool(auto_refresh_enabled)}</span>
        <span class="status-meta">Last checked: <time id="dashboard-last-checked">{html.escape(rendered_at)}</time></span>
      </div>
    """
    fact_grid = _render_fact_grid(
        [
            ("Thread", session.thread_id),
            ("Focus", snapshot.focus.value),
            ("Runtime state", snapshot.runtime_state),
            ("Resume state", snapshot.resume_state or "n/a"),
            ("Pending review", "yes" if snapshot.has_pending_review else "no"),
            ("Actor turns", str(actor_turn_count)),
            ("Tool calls", str(tool_call_count)),
        ]
    )
    metadata_rows = [
        ("Task goal", task_spec_goal),
        ("Task context", task_spec_context or "n/a"),
        ("Policy profile", policy_profile),
        ("Parse status", parse_status),
        ("Terminal state", snapshot.terminal_state or "n/a"),
    ]

    return _render_card(
        "Overview",
        f"""
        <div class="hero-copy">
          <p class="eyebrow">Layer 2 HITL control plane</p>
          <h2>{html.escape(focus_title)}</h2>
          <p class="lede">{html.escape(focus_summary)}</p>
        </div>
        {status_strip}
        {fact_grid}
        <div class="subsection">
          <h3>Task summary</h3>
          {_render_rows(metadata_rows)}
        </div>
        """,
        class_name="card card-hero card-wide",
    )


# Render the current plan step card.
def _render_plan_card(current_plan_step) -> str:
    if current_plan_step is None:
        return _render_card("Current Plan Step", "<p class=\"note\">No current step is available.</p>")

    rows = [
        ("Step ID", current_plan_step.step_id),
        ("Objective", current_plan_step.objective),
        ("Rationale", current_plan_step.rationale),
        ("Expected outcome", current_plan_step.expected_outcome),
        ("Status", current_plan_step.status),
    ]
    return _render_card("Current Plan Step", _render_rows(rows))


# Render the governance panel card.
def _render_governance_card(governance_panel) -> str:
    if governance_panel is None:
        return _render_card("Governance", "<p class=\"note\">No governance decision has been recorded yet.</p>")

    rows = [
        ("Directive", governance_panel.directive),
        ("Policy decision", governance_panel.policy_decision),
        ("Reason codes", ", ".join(governance_panel.reason_codes) or "n/a"),
        ("Summary", governance_panel.summary),
        ("Constrains capability use", _format_bool(governance_panel.constrains_capability_use)),
        ("Policy directive", governance_panel.policy_directive),
        ("Oversight recommendation", governance_panel.oversight_recommended_directive),
        ("Actor result type", governance_panel.evaluated_actor_result_type),
        ("Tool", governance_panel.evaluated_tool_name or "n/a"),
        ("Policy engine", governance_panel.policy_engine_name),
        ("Policy bundle", governance_panel.policy_bundle_version),
        ("Oversight summary", governance_panel.oversight_summary or "n/a"),
    ]
    return _render_card("Governance", _render_rows(rows))


# Render the oversight panel card.
def _render_oversight_card(oversight_panel) -> str:
    if oversight_panel is None:
        return _render_card("Oversight", "<p class=\"note\">No oversight assessment has been recorded yet.</p>")

    rows = [
        ("Evidence quality", _format_score(oversight_panel.evidence_quality_score)),
        ("Evidence level", oversight_panel.evidence_quality_level),
        ("Evidence reasons", ", ".join(oversight_panel.evidence_quality_reason_codes) or "n/a"),
        ("Operational risk", _format_score(oversight_panel.operational_risk_score)),
        ("Risk level", oversight_panel.operational_risk_level),
        ("Risk reasons", ", ".join(oversight_panel.operational_risk_reason_codes) or "n/a"),
        ("Trajectory health", _format_score(oversight_panel.trajectory_health_score)),
        ("Health level", oversight_panel.trajectory_health_level),
        ("Health reasons", ", ".join(oversight_panel.trajectory_health_reason_codes) or "n/a"),
        ("Recommended directive", oversight_panel.recommended_directive),
        ("Summary", oversight_panel.summary),
        ("Reason codes", ", ".join(oversight_panel.reason_codes) or "n/a"),
    ]
    return _render_card("Oversight", _render_rows(rows))


# Render the human review panel and action form.
def _render_human_review_card(snapshot, snapshot_dict: dict[str, Any], session: DashboardSession) -> str:
    human_review = snapshot.human_review
    if human_review is None:
        return _render_card(
            "Human Review",
            "<p class=\"note\">No human review is pending.</p>",
            class_name="card card-wide",
        )

    pending_request = human_review.pending_request
    callout = """
      <div class="callout callout-warning">
        <strong>Human review required.</strong>
        <span>The runtime is paused and will resume only after a typed decision is submitted.</span>
      </div>
    """
    request_rows = [
        ("Resume state", human_review.resume_state or "n/a"),
        ("Allowed decisions", ", ".join(decision.value for decision in human_review.allowed_decisions)),
        ("Review summary", human_review.review_summary or "n/a"),
        ("Last request", human_review.last_request.request_id if human_review.last_request else "n/a"),
        ("Last decision", human_review.last_decision.decision_type.value if human_review.last_decision else "n/a"),
        ("Decision history", str(len(human_review.decision_history))),
    ]

    payload_html = callout + _render_rows(request_rows)
    if pending_request is not None:
        payload_html += _render_pending_request_block(pending_request)
        payload_html += _render_resume_form(pending_request, human_review.allowed_decisions)

    payload_html += _render_small_json_block("Pending snapshot", snapshot_dict.get("human_review", {}))
    return _render_card("Human Review", payload_html, class_name="card card-wide")


# Render the pending review request details.
def _render_pending_request_block(pending_request: Any) -> str:
    rows = [
        ("Request ID", pending_request.request_id),
        ("Thread ID", pending_request.thread_id),
        ("Current state", pending_request.current_state),
        ("Current plan step", pending_request.current_plan_step_id or "n/a"),
        ("Actor result type", pending_request.actor_result_type or "n/a"),
        ("Governance directive", pending_request.governance_directive or "n/a"),
        ("Governance summary", pending_request.governance_summary or "n/a"),
        ("Oversight summary", pending_request.oversight_summary or "n/a"),
        ("Reason codes", ", ".join(pending_request.reason_codes) or "n/a"),
    ]

    tool_request_html = ""
    if pending_request.current_tool_request is not None:
        tool_request_html = _render_small_json_block(
            "Current tool request",
            pending_request.current_tool_request,
        )

    return _render_subsection("Pending request", _render_rows(rows) + tool_request_html)


# Render the resume form with one typed button per allowed decision.
def _render_resume_form(
    pending_request: Any,
    allowed_decisions: tuple[HumanReviewDecisionType, ...],
) -> str:
    button_html: list[str] = []
    for decision in allowed_decisions:
        button_label = decision.value.replace("_", " ")
        button_html.append(
            f"<button class=\"decision-button\" type=\"submit\" name=\"decision_type\" value=\"{html.escape(decision.value)}\">{html.escape(button_label)}</button>"
        )

    buttons = "".join(button_html)
    return f"""
      <form class="resume-form" method="post" action="/resume">
        <p class="note resume-note">Choose one typed decision to resume the runtime from the paused checkpoint.</p>
        <div class="resume-fields">
          <input type="hidden" name="request_id" value="{html.escape(pending_request.request_id)}" />
          <input type="hidden" name="thread_id" value="{html.escape(pending_request.thread_id)}" />
        <label>
          Reviewer ID
          <input type="text" name="reviewer_id" placeholder="operator-1" />
        </label>
        <label>
          Note
          <textarea name="note" rows="3" placeholder="Optional operator note"></textarea>
        </label>
        <label>
          Clarification text
          <textarea name="clarification_text" rows="3" placeholder="Optional clarification payload"></textarea>
        </label>
        </div>
        <div class="decision-row">{buttons}</div>
      </form>
    """


# Render the raw snapshot JSON in a collapsible block.
def _render_snapshot_card(snapshot_dict: dict[str, Any]) -> str:
    return _render_card(
        "Raw Snapshot",
        """
        <p class="note">Full JSON snapshot for audit, debugging, and trace alignment.</p>
        """
        + _render_small_json_block("Governance HITL snapshot", snapshot_dict),
        class_name="card card-wide card-muted",
    )


# Render one card wrapper with a title, body, and optional layout modifier.
def _render_card(title: str, body: str, *, class_name: str = "card") -> str:
    return f"""
      <section class="{html.escape(class_name)}">
        <h2>{html.escape(title)}</h2>
        {body}
      </section>
    """


# Render one subsection inside a card.
def _render_subsection(title: str, body: str) -> str:
    return f"""
      <div class="subsection">
        <h3>{html.escape(title)}</h3>
        {body}
      </div>
    """


# Render a key/value row set as a compact table.
def _render_rows(rows: list[tuple[str, str]]) -> str:
    row_html = []
    for label, value in rows:
        row_html.append(
            f"<tr><th>{html.escape(label)}</th><td>{html.escape(str(value))}</td></tr>"
        )
    return f"<table class=\"kv-table\">{''.join(row_html)}</table>"


# Render JSON content inside a collapsible details block.
def _render_small_json_block(title: str, payload: Any) -> str:
    pretty = json.dumps(_to_jsonable(payload), indent=2, ensure_ascii=False)
    return f"""
      <details class="json-block">
        <summary>{html.escape(title)}</summary>
        <pre>{html.escape(pretty)}</pre>
      </details>
    """


# Render a compact grid of key facts for the dashboard header and review panels.
def _render_fact_grid(rows: list[tuple[str, str]]) -> str:
    chips = []
    for label, value in rows:
        chips.append(
            f"""
            <div class="fact-chip">
              <span>{html.escape(label)}</span>
              <strong>{html.escape(str(value))}</strong>
            </div>
            """
        )
    return f'<div class="fact-grid">{"".join(chips)}</div>'


# Describe the current dashboard focus in one operator-friendly sentence.
def _describe_dashboard_focus(snapshot, auto_refresh_enabled: bool) -> tuple[str, str]:
    if snapshot.focus.value == "waiting_human_review":
        return (
            "Paused for human review",
            "The runtime has stopped at a typed review gate. Review the pending request and choose an allowed decision to resume the run.",
        )
    if snapshot.focus.value == "completed":
        return (
            "Run completed",
            "The runtime reached a terminal completion state. The page stays read-only and no longer polls for updates.",
        )
    if snapshot.focus.value == "aborted":
        return (
            "Run aborted",
            "The runtime reached a terminal denial state. The page stays read-only and no longer polls for updates.",
        )
    if auto_refresh_enabled:
        return (
            "Running normally",
            "The runtime is actively progressing. The dashboard is polling the current snapshot and will refresh when the state changes.",
        )
    return (
        "Runtime snapshot",
        "The current session is visible below. Refreshing is disabled for this state.",
    )


# Render the full HTML shell around the dashboard body.
def _render_html_document(
    *,
    title: str,
    body: str,
    dashboard_mode: str,
    auto_refresh_enabled: bool,
    rendered_at: str,
    snapshot_json: str | None,
) -> str:
    snapshot_node = ""
    if snapshot_json is not None:
        snapshot_node = (
            f'<textarea id="dashboard-snapshot-data" hidden>{html.escape(snapshot_json)}</textarea>'
        )
    return (
        _DASHBOARD_HTML_TEMPLATE
        .replace("__TITLE__", html.escape(title))
        .replace("__DASHBOARD_MODE__", html.escape(dashboard_mode))
        .replace("__AUTO_REFRESH_ENABLED__", "true" if auto_refresh_enabled else "false")
        .replace("__RENDERED_AT__", html.escape(rendered_at))
        .replace("__BODY__", body)
        .replace("__SNAPSHOT_NODE__", snapshot_node)
    )


# Render a compact error message card.
def _render_error_message(message: str) -> str:
    return f"<div class=\"error\">{html.escape(message)}</div>"


# Build cache-control headers that keep the live dashboard from sticking on stale pages.
def _build_response_headers(content_type: str) -> dict[str, str]:
    return {
        "Content-Type": content_type,
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }


# Convert runtime objects into JSON-safe primitives for the HTML view.
def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict, is_dataclass

        if is_dataclass(value):
            return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value") and not isinstance(value, (str, bytes)):
        try:
            return value.value
        except Exception:  # pragma: no cover - defensive fallback
            return str(value)
    return value


# Resolve the display mode exposed by the dashboard banner and polling logic.
def _resolve_dashboard_mode(focus_value: str) -> str:
    if focus_value in {"completed", "aborted"}:
        return "terminal"
    if focus_value == "waiting_human_review":
        return "paused"
    return "live"


# Parse the typed human review decision from the submitted form payload.
def _parse_human_review_decision(
    payload: dict[str, str],
    *,
    session: DashboardSession,
) -> HumanReviewDecision:
    task_state = get_dashboard_task_state(session)
    pending_request = task_state.pending_human_review_request
    if pending_request is None:
        raise ValueError("No pending human review request is available.")

    request_id = payload.get("request_id", "").strip()
    if request_id != pending_request.request_id:
        raise ValueError("Submitted request_id does not match the pending review request.")

    decision_type_value = payload.get("decision_type", "").strip()
    if not decision_type_value:
        raise ValueError("Missing decision_type in the submitted form.")

    try:
        decision_type = HumanReviewDecisionType(decision_type_value)
    except ValueError as exc:
        raise ValueError(f"Unsupported human review decision_type: {decision_type_value}") from exc

    if decision_type not in pending_request.allowed_decisions:
        raise ValueError(f"Decision '{decision_type.value}' is not allowed for this review request.")

    return HumanReviewDecision(
        request_id=request_id,
        decision_type=decision_type,
        note=_optional_text(payload.get("note")),
        clarification_text=_optional_text(payload.get("clarification_text")),
        reviewer_id=_optional_text(payload.get("reviewer_id")),
    )


# Normalize optional text form fields to None when blank.
def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


# Build a short stable token from the task text for thread ids and logs.
def _short_token(raw_task_text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in raw_task_text.lower())
    collapsed = "-".join(part for part in cleaned.split("-") if part)
    return collapsed[:24] or "dashboard"


# Format a floating-point score for a compact human-readable dashboard.
def _format_score(score: float) -> str:
    return f"{score:.2f}"


# Format booleans as readable dashboard text.
def _format_bool(value: bool) -> str:
    return "yes" if value else "no"


if __name__ == "__main__":
    main()
