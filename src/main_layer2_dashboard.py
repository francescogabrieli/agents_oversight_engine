from __future__ import annotations

import argparse
import html
import json
import re
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
    ExternalCommunicationDraft,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    HumanReviewRequest,
    build_default_checkpointer,
    build_runtime_graph,
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


# Rebuild a dashboard session from a persisted checkpointed thread id.
def build_dashboard_session_from_thread_id(
    thread_id: str,
    *,
    checkpointer: Any | None = None,
    resume_flow: DashboardRunner = resume_layer2_flow,
) -> DashboardSession:
    resolved_checkpointer = checkpointer or build_default_checkpointer()
    runtime_graph = build_runtime_graph(checkpointer=resolved_checkpointer)
    snapshot = runtime_graph.get_state({"configurable": {"thread_id": thread_id}})
    snapshot_values = snapshot.values if snapshot is not None else {}
    if not isinstance(snapshot_values, dict):
        raise ValueError(f"No persisted runtime state is available for thread '{thread_id}'.")

    task_state = snapshot_values.get("task_state")
    if not isinstance(task_state, TaskState):
        raise ValueError(f"Persisted thread '{thread_id}' does not contain a canonical task_state.")

    return DashboardSession(
        thread_id=thread_id,
        intake_result=task_state.intake_payload,
        final_result=dict(snapshot_values),
        checkpointer=resolved_checkpointer,
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
    return _render_dashboard_page_internal(session=session)


# Render the dashboard page with optional transient operator feedback and form state.
def _render_dashboard_page_internal(
    *,
    session: DashboardSession,
    form_state: dict[str, str] | None = None,
    form_error: str | None = None,
    operator_feedback: str | None = None,
) -> str:
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
            form_state=form_state,
            form_error=form_error,
            operator_feedback=operator_feedback,
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
            payload = self._read_form_payload()
            try:
                decision = _parse_human_review_decision(payload, session=self.dashboard_session)
                resume_dashboard_session(self.dashboard_session, decision)
                self._write_html(
                    _render_dashboard_page_internal(
                        session=self.dashboard_session,
                        operator_feedback=_build_operator_feedback_message(decision),
                    )
                )
            except ValueError as exc:
                self._write_html(
                    _render_dashboard_page_internal(
                        session=self.dashboard_session,
                        form_state=payload,
                        form_error=str(exc),
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
        "--load-thread-id",
        default=None,
        help="Reload an existing persisted thread instead of starting a new run.",
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
    if args.load_thread_id:
        session = build_dashboard_session_from_thread_id(args.load_thread_id)
    else:
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
    form_state: dict[str, str] | None,
    form_error: str | None,
    operator_feedback: str | None,
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
        _render_operator_feedback_banner(operator_feedback),
        """
        <div class="control-layout">
        """
        + _render_operator_actions_card(
            snapshot=snapshot,
            snapshot_dict=snapshot_dict,
            session=session,
            form_state=form_state,
            form_error=form_error,
        )
        + _render_technical_details_card(snapshot=snapshot, snapshot_dict=snapshot_dict)
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
            ("State", snapshot.runtime_state),
            ("Resume state", snapshot.resume_state or "n/a"),
            ("Pending review", "yes" if snapshot.has_pending_review else "no"),
            (
                "Last decision",
                (
                    snapshot.human_review.last_decision.decision_type.value
                    if snapshot.human_review is not None
                    and snapshot.human_review.last_decision is not None
                    else "n/a"
                ),
            ),
        ]
    )

    return _render_card(
        "Run",
        f"""
        <div class="hero-copy">
          <p class="eyebrow">Layer 2 HITL control plane</p>
          <h2>{html.escape(focus_title)}</h2>
          <p class="lede">{html.escape(focus_summary)}</p>
        </div>
        {status_strip}
        {fact_grid}
        <p class="task-line"><strong>Task:</strong> {html.escape(_compact_text(task_spec_goal, 220))}</p>
        """,
        class_name="card card-hero",
    )


# Render one success banner after a typed operator action resumes the runtime.
def _render_operator_feedback_banner(operator_feedback: str | None) -> str:
    if operator_feedback is None:
        return ""
    return _render_card(
        "Operator Outcome",
        f"""
        <div class="callout callout-success">
          <strong>Operator action accepted.</strong>
          <span>{html.escape(operator_feedback)}</span>
        </div>
        """,
        class_name="card card-wide",
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


# Render the external communication phase when the task uses that governed branch.
def _render_external_communication_card(external_communication_panel) -> str:
    if external_communication_panel is None:
        return _render_card(
            "External Communication",
            "<p class=\"note\">This task does not use the external communication branch.</p>",
        )

    rows = [
        ("Phase", external_communication_panel.phase),
        (
            "Last transition",
            external_communication_panel.last_transition_summary or "n/a",
        ),
    ]
    if external_communication_panel.draft is not None:
        rows.extend(
            [
                ("Draft channel", external_communication_panel.draft.channel),
                ("Draft recipient", external_communication_panel.draft.recipient or "n/a"),
                ("Draft subject", external_communication_panel.draft.subject or "n/a"),
            ]
        )
    if external_communication_panel.last_applied_edit is not None:
        rows.extend(
            [
                (
                    "Last applied edit note",
                    external_communication_panel.last_applied_edit.note or "n/a",
                ),
                (
                    "Last edited channel",
                    external_communication_panel.last_applied_edit.draft.channel,
                ),
            ]
        )
    return _render_card("External Communication", _render_rows(rows))


# Render the primary operator actions card when the runtime is paused for review.
def _render_operator_actions_card(
    *,
    snapshot,
    snapshot_dict: dict[str, Any],
    session: DashboardSession,
    form_state: dict[str, str] | None,
    form_error: str | None,
) -> str:
    human_review = snapshot.human_review
    if snapshot.focus.value != "waiting_human_review" or human_review is None:
        return ""

    pending_request = human_review.pending_request
    if pending_request is None:
        return ""

    review_summary = (
        human_review.review_summary
        or pending_request.governance_summary
        or pending_request.oversight_summary
        or "Human review is required before the run can continue."
    )
    body = f"""
      <div class="review-panel">
        <div>
          <p class="eyebrow">Operator action required</p>
          <h2>Review and resume</h2>
          <p class="lede">{html.escape(_compact_text(review_summary, 340))}</p>
        </div>
        {_render_review_chips(snapshot=snapshot, pending_request=pending_request)}
      </div>
    """
    if form_error is not None:
        body += f"""
        <div class="callout callout-danger">
          <strong>Submission blocked.</strong>
          <span>{html.escape(form_error)}</span>
        </div>
        """
    body += _render_resume_form(
        pending_request,
        human_review.allowed_decisions,
        form_state=form_state,
    )
    return _render_card(
        "Operator Review",
        body,
        class_name="card card-control",
    )


# Render the few runtime facts an operator needs before choosing an action.
def _render_review_chips(*, snapshot, pending_request: Any) -> str:
    chips: list[tuple[str, str, str]] = [
        ("Resume", snapshot.resume_state or pending_request.resume_state or "n/a", "neutral"),
        ("Target", pending_request.external_communication_target_type or "runtime", "neutral"),
    ]
    if snapshot.oversight is not None:
        chips.extend(
            [
                (
                    "Evidence",
                    snapshot.oversight.evidence_quality_level,
                    _chip_tone(snapshot.oversight.evidence_quality_level),
                ),
                (
                    "Risk",
                    snapshot.oversight.operational_risk_level,
                    _chip_tone(snapshot.oversight.operational_risk_level),
                ),
            ]
        )
    rendered = []
    for label, value, tone in chips:
        rendered.append(
            f"""
            <div class="signal-chip signal-chip-{html.escape(tone)}">
              <span>{html.escape(label)}</span>
              <strong>{html.escape(str(value))}</strong>
            </div>
            """
        )
    return f'<div class="signal-grid">{"".join(rendered)}</div>'


# Map compact status values to visual tone classes.
def _chip_tone(value: str) -> str:
    normalized = str(value).lower()
    if normalized in {"high", "weak", "degraded", "critical"}:
        return "danger"
    if normalized in {"moderate", "medium", "partial"}:
        return "warning"
    if normalized in {"healthy", "strong", "low"}:
        return "success"
    return "neutral"


# Render decision buttons in operator-oriented groups without hiding typed values.
def _render_decision_buttons(allowed_decisions: tuple[HumanReviewDecisionType, ...]) -> str:
    primary = [
        HumanReviewDecisionType.MODIFY_AND_CONTINUE,
        HumanReviewDecisionType.APPROVE_CONTINUE,
    ]
    secondary = [
        HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY,
        HumanReviewDecisionType.REQUEST_MORE_EVIDENCE,
        HumanReviewDecisionType.REPLAN,
        HumanReviewDecisionType.ASK_CLARIFICATION,
    ]
    terminal = [HumanReviewDecisionType.ABORT]
    groups = [
        ("Resume now", primary),
        ("Conservative", secondary),
        ("Stop", terminal),
    ]
    rendered_groups = []
    for title, decisions in groups:
        buttons = []
        for decision in decisions:
            if decision not in allowed_decisions:
                continue
            buttons.append(
                "<button "
                f"class=\"decision-button {html.escape(_decision_button_class(decision))}\" "
                f"type=\"submit\" name=\"decision_type\" value=\"{html.escape(decision.value)}\">"
                f"{html.escape(_decision_button_label(decision))}</button>"
            )
        if buttons:
            rendered_groups.append(
                f"""
                <div class="action-group">
                  <span>{html.escape(title)}</span>
                  <div class="decision-row">{''.join(buttons)}</div>
                </div>
                """
            )
    return f'<div class="action-bar">{"".join(rendered_groups)}</div>'


# Keep action labels short while preserving canonical submit values.
def _decision_button_label(decision: HumanReviewDecisionType) -> str:
    labels = {
        HumanReviewDecisionType.MODIFY_AND_CONTINUE: "Submit edited draft",
        HumanReviewDecisionType.APPROVE_CONTINUE: "Approve",
        HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY: "Approve read-only",
        HumanReviewDecisionType.REQUEST_MORE_EVIDENCE: "More evidence",
        HumanReviewDecisionType.REPLAN: "Replan",
        HumanReviewDecisionType.ASK_CLARIFICATION: "Ask clarification",
        HumanReviewDecisionType.ABORT: "Abort",
    }
    return labels.get(decision, decision.value.replace("_", " "))


# Render the human review context and current draft without mutating the operator form state.
def _render_human_review_card(
    snapshot,
    snapshot_dict: dict[str, Any],
    session: DashboardSession,
    *,
    form_state: dict[str, str] | None,
    form_error: str | None,
) -> str:
    human_review = snapshot.human_review
    if human_review is None:
        return _render_card(
            "Human Review",
            "<p class=\"note\">No human review is pending.</p>",
            class_name="card card-wide",
        )

    pending_request = human_review.pending_request
    request_rows = [
        ("Resume state", human_review.resume_state or "n/a"),
        ("Allowed decisions", ", ".join(decision.value for decision in human_review.allowed_decisions)),
        ("Review summary", human_review.review_summary or "n/a"),
        ("Last request", human_review.last_request.request_id if human_review.last_request else "n/a"),
        ("Last decision", human_review.last_decision.decision_type.value if human_review.last_decision else "n/a"),
        ("Decision history", str(len(human_review.decision_history))),
    ]

    payload_html = _render_rows(request_rows)
    if pending_request is not None:
        payload_html += _render_pending_request_block(pending_request)
        payload_html += _render_external_draft_review_block(
            pending_request=pending_request,
            allowed_decisions=human_review.allowed_decisions,
            form_state=form_state,
        )

    if human_review.last_decision is not None:
        payload_html += _render_last_decision_block(human_review.last_decision)
    return _render_card("Human Review", payload_html, class_name="card card-wide")


# Render the pending review request details.
def _render_pending_request_block(pending_request: Any) -> str:
    rows = [
        ("Request ID", pending_request.request_id),
        ("Thread ID", pending_request.thread_id),
        ("Current state", pending_request.current_state),
        ("Current plan step", pending_request.current_plan_step_id or "n/a"),
        ("Actor result type", pending_request.actor_result_type or "n/a"),
        ("External target", pending_request.external_communication_target_type or "n/a"),
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

    return _render_subsection(
        "Pending request",
        _render_rows(rows) + tool_request_html,
    )


# Render the review-time external draft comparison when the branch supports typed edit-and-submit.
def _render_external_draft_review_block(
    *,
    pending_request: Any,
    allowed_decisions: tuple[HumanReviewDecisionType, ...],
    form_state: dict[str, str] | None,
) -> str:
    original_draft = pending_request.external_communication_draft
    if original_draft is None:
        return ""

    can_modify_external = HumanReviewDecisionType.MODIFY_AND_CONTINUE in allowed_decisions
    editable_draft = _build_editable_external_draft(original_draft, form_state=form_state)
    original_block = _render_external_draft_summary(
        title="Current draft",
        draft=original_draft,
        note="This is the governed draft currently stored in the runtime.",
    )

    if not can_modify_external:
        return _render_subsection("External draft", original_block)

    editable_block = _render_external_draft_summary(
        title="Editable draft",
        draft=editable_draft,
        note="These are the values that will be submitted if you choose modify_and_continue.",
    )
    return _render_subsection(
        "External draft review",
        f"""
        <div class="draft-compare-grid">
          {original_block}
          {editable_block}
        </div>
        """,
    )


# Render a compact draft summary card for either the stored or editable version.
def _render_external_draft_summary(*, title: str, draft: ExternalCommunicationDraft, note: str) -> str:
    return f"""
      <section class="draft-panel">
        <h4>{html.escape(title)}</h4>
        {_render_rows(
            [
                ("Channel", draft.channel),
                ("Recipient", draft.recipient or "n/a"),
                ("Subject", draft.subject or "n/a"),
                ("Body", draft.body or "n/a"),
            ]
        )}
        <p class="note">{html.escape(note)}</p>
        {_render_small_json_block("Draft metadata", draft.metadata)}
      </section>
    """


# Build the in-form editable draft view while preserving invalid submit values after an error.
def _build_editable_external_draft(
    original_draft: ExternalCommunicationDraft,
    *,
    form_state: dict[str, str] | None,
) -> ExternalCommunicationDraft:
    if form_state is None:
        return original_draft

    metadata = dict(original_draft.metadata)
    metadata_raw = _optional_text(form_state.get("external_communication_metadata"))
    if metadata_raw is not None:
        try:
            parsed_metadata = json.loads(metadata_raw)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed_metadata, dict):
                metadata = {str(key): value for key, value in parsed_metadata.items()}

    return ExternalCommunicationDraft(
        channel=_resolve_form_value(form_state, "external_communication_channel", original_draft.channel),
        recipient=_resolve_form_value(
            form_state,
            "external_communication_recipient",
            original_draft.recipient or "",
        ) or None,
        subject=_resolve_form_value(
            form_state,
            "external_communication_subject",
            original_draft.subject or "",
        ) or None,
        body=_resolve_form_value(form_state, "external_communication_body", original_draft.body or ""),
        metadata=metadata,
    )


# Render the resume form with one typed button per allowed decision.
def _render_resume_form(
    pending_request: Any,
    allowed_decisions: tuple[HumanReviewDecisionType, ...],
    *,
    form_state: dict[str, str] | None,
) -> str:
    external_edit_html = ""
    can_modify_external = (
        pending_request.external_communication_draft is not None
        and HumanReviewDecisionType.MODIFY_AND_CONTINUE in allowed_decisions
    )
    if can_modify_external:
        external_edit_html = _render_external_communication_edit_fields(
            pending_request.external_communication_draft,
            form_state=form_state,
        )
    operator_fields = f"""
      <div class="operator-sidebar">
        <div class="operator-sidebar-header">
          <h3>Review decision</h3>
          <p class="note">Choose one allowed action. The optional note is stored for audit; clarification text is used only with <strong>Ask clarification</strong>.</p>
        </div>
        <div class="resume-fields compact-fields">
          <input type="hidden" name="request_id" value="{html.escape(pending_request.request_id)}" />
          <input type="hidden" name="thread_id" value="{html.escape(pending_request.thread_id)}" />
          <label>
            Reviewer ID
            <input type="text" name="reviewer_id" placeholder="operator-1" value="{html.escape(_resolve_form_value(form_state, 'reviewer_id', ''))}" />
          </label>
          <label>
            Audit note
            <textarea name="note" rows="3" placeholder="Why you chose this action, for the trace">{html.escape(_resolve_form_value(form_state, 'note', ''))}</textarea>
          </label>
          <label class="clarification-field" data-clarification-field>
            Clarification text
            <textarea name="clarification_text" rows="3" placeholder="Question or missing information to resolve before continuing">{html.escape(_resolve_form_value(form_state, 'clarification_text', ''))}</textarea>
            <span class="field-help">Submitted only when the selected action is Ask clarification.</span>
          </label>
        </div>
        {_render_decision_buttons(allowed_decisions)}
      </div>
    """
    if can_modify_external:
        form_body = f"""
          <div class="operator-workspace">
            {external_edit_html}
            {operator_fields}
          </div>
        """
    else:
        form_body = operator_fields
    clarification_active = (
        "true"
        if _resolve_form_value(form_state, "decision_type", "")
        == HumanReviewDecisionType.ASK_CLARIFICATION.value
        else "false"
    )
    return f"""
      <form class="resume-form" method="post" action="/resume" data-clarification-active="{clarification_active}">
        {form_body}
      </form>
    """


# Build operator-facing draft field values without mutating the canonical draft.
def _build_external_draft_form_values(
    draft: ExternalCommunicationDraft,
    *,
    form_state: dict[str, str] | None,
) -> dict[str, str]:
    derived_subject, derived_recipient, derived_body = _derive_external_draft_header_fields(draft)
    return {
        "channel": _resolve_form_value(
            form_state,
            "external_communication_channel",
            draft.channel,
        ),
        "recipient": _resolve_form_value(
            form_state,
            "external_communication_recipient",
            draft.recipient or derived_recipient,
        ),
        "subject": _resolve_form_value(
            form_state,
            "external_communication_subject",
            draft.subject or derived_subject,
        ),
        "body": _resolve_form_value(
            form_state,
            "external_communication_body",
            derived_body,
        ),
    }


# Derive visible email-like fields when the LLM placed headers inside the body.
def _derive_external_draft_header_fields(draft: ExternalCommunicationDraft) -> tuple[str, str, str]:
    body = draft.body or ""
    subject = ""
    body_without_subject = body
    subject_match = re.match(
        r"^\s*Subject:\s*(?P<subject>.+?)(?=(?:\r?\n|Dear\s+[A-Z]|$))",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if subject_match is not None:
        subject = " ".join(subject_match.group("subject").split())
        body_without_subject = body[subject_match.end() :].lstrip(" \r\n")

    recipient = ""
    recipient_match = re.search(
        r"\bDear\s+(?P<recipient>[^,\n\r]+)",
        body_without_subject,
        flags=re.IGNORECASE,
    )
    if recipient_match is not None:
        recipient = " ".join(recipient_match.group("recipient").split())

    return subject, recipient, body_without_subject


# Render the editable external communication fields for modify-and-continue.
def _render_external_communication_edit_fields(
    draft: ExternalCommunicationDraft,
    *,
    form_state: dict[str, str] | None,
) -> str:
    field_values = _build_external_draft_form_values(draft, form_state=form_state)
    metadata_value = _resolve_form_value(
        form_state,
        "external_communication_metadata",
        json.dumps(_to_jsonable(draft.metadata), indent=2, ensure_ascii=False),
    )
    return f"""
      <fieldset class="edit-fieldset draft-editor">
        <legend>External draft</legend>
        <div class="draft-header">
          <p class="note">Review or edit the governed draft. <strong>Submit edited draft</strong> applies these values and resumes the run.</p>
        </div>
        <div class="resume-fields">
          <label>
            Channel
            <input type="text" name="external_communication_channel" value="{html.escape(field_values['channel'])}" />
          </label>
          <label>
            Recipient
            <input type="text" name="external_communication_recipient" value="{html.escape(field_values['recipient'])}" />
          </label>
          <label>
            Subject
            <input type="text" name="external_communication_subject" value="{html.escape(field_values['subject'])}" />
          </label>
          <label>
            Body
            <textarea name="external_communication_body" rows="12">{html.escape(field_values['body'])}</textarea>
          </label>
          <input type="hidden" name="external_communication_metadata" value="{html.escape(metadata_value)}" />
        </div>
      </fieldset>
    """


# Render one short decision guide so operator intent is explicit before submit.
def _render_decision_guidance(allowed_decisions: tuple[HumanReviewDecisionType, ...]) -> str:
    guidance_rows = []
    for decision in allowed_decisions:
        guidance_rows.append(
            f"""
            <div class="decision-guide decision-guide-{html.escape(_decision_family(decision))}">
              <strong>{html.escape(decision.value)}</strong>
              <span>{html.escape(_decision_help_text(decision))}</span>
            </div>
            """
        )
    return f'<div class="decision-guide-grid">{"".join(guidance_rows)}</div>'


# Render the last typed human decision in a compact audit-safe block.
def _render_last_decision_block(last_decision: HumanReviewDecision) -> str:
    rows = [
        ("Decision type", last_decision.decision_type.value),
        ("Reviewer", last_decision.reviewer_id or "n/a"),
        ("Note", last_decision.note or "n/a"),
        ("Clarification", last_decision.clarification_text or "n/a"),
    ]
    if last_decision.external_communication_edit is not None:
        rows.extend(
            [
                (
                    "Edited channel",
                    last_decision.external_communication_edit.draft.channel,
                ),
                (
                    "Edited recipient",
                    last_decision.external_communication_edit.draft.recipient or "n/a",
                ),
            ]
        )
    return _render_subsection("Last human decision", _render_rows(rows))


# Classify decisions for styling and operator emphasis.
def _decision_family(decision: HumanReviewDecisionType) -> str:
    if decision in {
        HumanReviewDecisionType.APPROVE_CONTINUE,
        HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY,
        HumanReviewDecisionType.MODIFY_AND_CONTINUE,
    }:
        return "approval"
    if decision == HumanReviewDecisionType.ABORT:
        return "terminal"
    return "conservative"


# Map decisions to button classes with stronger operator affordances.
def _decision_button_class(decision: HumanReviewDecisionType) -> str:
    family = _decision_family(decision)
    if family == "approval":
        return "decision-button-approval"
    if family == "terminal":
        return "decision-button-terminal"
    return "decision-button-conservative"


# Keep the operator-facing semantics explicit next to each canonical decision.
def _decision_help_text(decision: HumanReviewDecisionType) -> str:
    if decision == HumanReviewDecisionType.APPROVE_CONTINUE:
        return "Resume the runtime on the stored path."
    if decision == HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY:
        return "Resume under a stricter read-only execution envelope."
    if decision == HumanReviewDecisionType.MODIFY_AND_CONTINUE:
        return "Apply the edited external draft and resume immediately."
    if decision == HumanReviewDecisionType.REQUEST_MORE_EVIDENCE:
        return "Send the run back to gather stronger evidence."
    if decision == HumanReviewDecisionType.REPLAN:
        return "Invalidate the current trajectory and request replanning."
    if decision == HumanReviewDecisionType.ASK_CLARIFICATION:
        return "Keep the run paused until missing information is resolved."
    return "Terminate the run in a hard stopped state."


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


# Render audit material behind one collapsed control so the operator UI stays readable.
def _render_technical_details_card(*, snapshot, snapshot_dict: dict[str, Any]) -> str:
    plan_html = _render_plan_card(snapshot.current_plan_step)
    governance_html = _render_governance_card(snapshot.governance)
    oversight_html = _render_oversight_card(snapshot.oversight)
    external_html = _render_external_communication_card(snapshot.external_communication)
    review_rows = []
    if snapshot.human_review is not None:
        review_rows = [
            ("Resume state", snapshot.human_review.resume_state or "n/a"),
            (
                "Allowed decisions",
                ", ".join(decision.value for decision in snapshot.human_review.allowed_decisions),
            ),
            ("Decision history", str(len(snapshot.human_review.decision_history))),
        ]
    review_html = _render_card(
        "Human Review",
        _render_rows(review_rows) if review_rows else "<p class=\"note\">No human review payload.</p>",
    )
    return f"""
      <details class="technical-details">
        <summary>Technical details</summary>
        <div class="technical-grid">
          {plan_html}
          {governance_html}
          {oversight_html}
          {external_html}
          {review_html}
          {_render_snapshot_card(snapshot_dict)}
        </div>
      </details>
    """


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

    clarification_text = None
    if decision_type == HumanReviewDecisionType.ASK_CLARIFICATION:
        clarification_text = _optional_text(payload.get("clarification_text"))
        if clarification_text is None:
            raise ValueError("Clarification text is required when asking for clarification.")

    external_edit = None
    if decision_type == HumanReviewDecisionType.MODIFY_AND_CONTINUE:
        external_edit = _parse_external_communication_edit(
            payload,
            pending_request=pending_request,
        )

    return HumanReviewDecision(
        request_id=request_id,
        decision_type=decision_type,
        note=_optional_text(payload.get("note")),
        clarification_text=clarification_text,
        external_communication_edit=external_edit,
        reviewer_id=_optional_text(payload.get("reviewer_id")),
    )


# Parse the external draft edits submitted by the dashboard form.
def _parse_external_communication_edit(
    payload: dict[str, str],
    *,
    pending_request: HumanReviewRequest,
) -> HumanReviewExternalEdit:
    current_draft = pending_request.external_communication_draft
    if current_draft is None:
        raise ValueError("modify_and_continue requires an external communication draft.")

    channel = _optional_text(payload.get("external_communication_channel")) or current_draft.channel
    body = _optional_text(payload.get("external_communication_body")) or current_draft.body
    if not channel:
        raise ValueError("External communication channel is required for modify_and_continue.")
    if not body:
        raise ValueError("External communication body is required for modify_and_continue.")

    metadata_raw = _optional_text(payload.get("external_communication_metadata"))
    if metadata_raw is None:
        metadata = dict(current_draft.metadata)
    else:
        try:
            metadata_payload = json.loads(metadata_raw)
        except json.JSONDecodeError as exc:
            raise ValueError("External communication metadata must be valid JSON.") from exc
        if not isinstance(metadata_payload, dict):
            raise ValueError("External communication metadata must be a JSON object.")
        metadata = {str(key): value for key, value in metadata_payload.items()}

    return HumanReviewExternalEdit(
        draft=ExternalCommunicationDraft(
            channel=channel,
            recipient=_optional_text(payload.get("external_communication_recipient")),
            subject=_optional_text(payload.get("external_communication_subject")),
            body=body,
            metadata=metadata,
        ),
        note=_optional_text(payload.get("note")),
    )


# Build a short success message that stays aligned with the typed decision semantics.
def _build_operator_feedback_message(decision: HumanReviewDecision) -> str:
    if decision.decision_type == HumanReviewDecisionType.MODIFY_AND_CONTINUE:
        return "The edited external draft was accepted and the runtime resumed on the governed path."
    if decision.decision_type == HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY:
        return "The runtime resumed under the approved read-only envelope."
    if decision.decision_type == HumanReviewDecisionType.APPROVE_CONTINUE:
        return "The runtime resumed on the approved execution path."
    if decision.decision_type == HumanReviewDecisionType.REQUEST_MORE_EVIDENCE:
        return "The runtime resumed in a more conservative evidence-gathering path."
    if decision.decision_type == HumanReviewDecisionType.REPLAN:
        return "The runtime resumed by invalidating the current path and entering replanning."
    if decision.decision_type == HumanReviewDecisionType.ASK_CLARIFICATION:
        return "The clarification request was accepted and the run remains paused for resolution."
    return "The abort decision was accepted and the run is now stopped."


# Normalize optional text form fields to None when blank.
def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


# Reuse submitted values after an inline validation error without mutating TaskState.
def _resolve_form_value(
    form_state: dict[str, str] | None,
    field_name: str,
    fallback: str,
) -> str:
    if form_state is None:
        return fallback
    value = form_state.get(field_name)
    if value is None:
        return fallback
    return value


# Build a short stable token from the task text for thread ids and logs.
def _short_token(raw_task_text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in raw_task_text.lower())
    collapsed = "-".join(part for part in cleaned.split("-") if part)
    return collapsed[:24] or "dashboard"


# Shorten long runtime summaries so the main operator surface stays scannable.
def _compact_text(value: str, max_chars: int) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


# Format a floating-point score for a compact human-readable dashboard.
def _format_score(score: float) -> str:
    return f"{score:.2f}"


# Format booleans as readable dashboard text.
def _format_bool(value: bool) -> str:
    return "yes" if value else "no"


if __name__ == "__main__":
    main()
