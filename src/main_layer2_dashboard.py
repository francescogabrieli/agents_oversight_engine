from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from layer1_intake import IntakeResult
from layer2_runtime_governance import (
    ExternalCommunicationDraft,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    HumanReviewRequest,
    build_default_checkpointer,
    build_runtime_graph,
)
from layer2_runtime_governance.persistence import RuntimeArtifactStore
from layer2_runtime_governance.state import TaskState

from main_layer2 import resume_layer2_flow


# Keep the local dashboard bound to loopback by default.
DEFAULT_HOST = "127.0.0.1"


# Keep the local dashboard port stable for operator workflows.
DEFAULT_PORT = 8022


# Resolve the built React application relative to the repo root.
DEFAULT_STATIC_ROOT = Path(__file__).resolve().parents[1] / "frontend" / "dashboard" / "dist"


# DashboardRunner is the callable used to resume a paused Layer 2 run.
DashboardRunner = Callable[..., dict[str, Any]]


@dataclass
class DashboardSession:
    # Keep the typed session shape available for checkpoint-backed resume flows.
    thread_id: str
    intake_result: IntakeResult
    final_result: dict[str, Any]
    checkpointer: Any
    resume_flow: DashboardRunner = resume_layer2_flow
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


@dataclass(frozen=True)
class DashboardServerContext:
    # Group the server dependencies so the request handler can stay stateless.
    artifact_store: RuntimeArtifactStore
    checkpointer: Any
    resume_flow: DashboardRunner
    static_root: Path


# Rebuild a checkpoint-backed dashboard session from an in-memory trace payload.
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


# Rebuild a dashboard session from one persisted LangGraph thread id.
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


# Resume one checkpoint-backed session with a typed human review decision.
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


# Return the canonical TaskState held by a checkpoint-backed session.
def get_dashboard_task_state(session: DashboardSession) -> TaskState:
    with session.lock:
        task_state = session.final_result.get("task_state")
        if not isinstance(task_state, TaskState):
            raise ValueError("Dashboard session does not contain a canonical task_state.")
        return task_state


# Build cache-control headers that keep both API and static responses fresh.
def _build_response_headers(content_type: str) -> dict[str, str]:
    return {
        "Content-Type": content_type,
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }


# Normalize optional text fields to None when the payload provides blanks.
def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    text = value.strip()
    return text or None


# Normalize free-form metadata into a JSON object or fail with a typed validation error.
def _coerce_metadata_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("External communication metadata must be valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("External communication metadata must be a JSON object.")
        return {str(key): item for key, item in parsed.items()}
    raise ValueError("External communication metadata must be a JSON object.")


# Parse the external communication edit accepted by modify_and_continue.
def _parse_external_communication_edit(
    payload: dict[str, Any],
    *,
    pending_request: HumanReviewRequest,
) -> HumanReviewExternalEdit:
    current_draft = pending_request.external_communication_draft
    if current_draft is None:
        raise ValueError("modify_and_continue requires an external communication draft.")

    edit_payload = payload.get("external_communication_edit")
    edit_payload = edit_payload if isinstance(edit_payload, dict) else {}
    draft_payload = edit_payload.get("draft")
    draft_payload = draft_payload if isinstance(draft_payload, dict) else {}

    channel = (
        _optional_text(draft_payload.get("channel"))
        or _optional_text(payload.get("external_communication_channel"))
        or current_draft.channel
    )
    body = (
        _optional_text(draft_payload.get("body"))
        or _optional_text(payload.get("external_communication_body"))
        or current_draft.body
    )
    metadata = current_draft.metadata
    if "metadata" in draft_payload:
        metadata = _coerce_metadata_payload(draft_payload.get("metadata"))
    elif "external_communication_metadata" in payload:
        metadata = _coerce_metadata_payload(payload.get("external_communication_metadata"))

    if not channel:
        raise ValueError("External communication channel is required for modify_and_continue.")
    if not body:
        raise ValueError("External communication body is required for modify_and_continue.")

    return HumanReviewExternalEdit(
        draft=ExternalCommunicationDraft(
            channel=channel,
            recipient=_optional_text(draft_payload.get("recipient")) or current_draft.recipient,
            subject=_optional_text(draft_payload.get("subject")) or current_draft.subject,
            body=body,
            metadata=metadata,
        ),
        note=_optional_text(edit_payload.get("note")) or _optional_text(payload.get("note")),
    )


# Convert one JSON payload into the canonical typed HumanReviewDecision contract.
def _parse_human_review_decision(
    payload: dict[str, Any],
    *,
    session: DashboardSession,
) -> HumanReviewDecision:
    task_state = get_dashboard_task_state(session)
    pending_request = task_state.pending_human_review_request
    if pending_request is None:
        raise ValueError("No pending human review request is available.")

    payload_thread_id = _optional_text(payload.get("thread_id"))
    if payload_thread_id is not None and payload_thread_id != session.thread_id:
        raise ValueError("Submitted thread_id does not match the requested review thread.")

    request_id = _optional_text(payload.get("request_id"))
    if request_id != pending_request.request_id:
        raise ValueError("Submitted request_id does not match the pending review request.")

    decision_type_value = _optional_text(payload.get("decision_type"))
    if decision_type_value is None:
        raise ValueError("Missing decision_type in the submitted payload.")

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


# Build a compact operator feedback message aligned with the typed decision semantics.
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


# Read one JSON request body and keep validation errors user-facing.
def _read_json_request(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    raw_body = handler.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    return payload


# Load one persisted review detail from the runtime artifact store.
def _load_review_detail(
    thread_id: str,
    *,
    artifact_store: RuntimeArtifactStore,
) -> dict[str, Any] | None:
    runtime_summary = artifact_store.read_runtime_summary(thread_id)
    dashboard_snapshot = artifact_store.read_dashboard_snapshot(thread_id)
    pending_review = artifact_store.read_pending_human_review(thread_id)
    if runtime_summary is None and dashboard_snapshot is None:
        return None
    return {
        "thread_id": thread_id,
        "runtime_summary": runtime_summary,
        "dashboard_snapshot": dashboard_snapshot,
        "pending_human_review": pending_review,
    }


# Normalize one runtime summary into the compact inbox entry rendered by React.
def _build_review_inbox_entry(
    thread_id: str,
    *,
    runtime_summary: dict[str, Any] | None,
    dashboard_snapshot: dict[str, Any] | None,
    pending_review: dict[str, Any] | None,
) -> dict[str, Any] | None:
    pending_request = pending_review
    if pending_request is None and isinstance(dashboard_snapshot, dict):
        human_review = dashboard_snapshot.get("human_review")
        if isinstance(human_review, dict):
            candidate = human_review.get("pending_request")
            if isinstance(candidate, dict):
                pending_request = candidate

    has_pending_human_review = bool(
        runtime_summary and runtime_summary.get("has_pending_human_review")
    ) or pending_request is not None
    if not has_pending_human_review:
        return None

    allowed_decisions = []
    if isinstance(runtime_summary, dict):
        allowed_decisions = runtime_summary.get("allowed_decisions") or []
    if not allowed_decisions and isinstance(pending_request, dict):
        allowed_decisions = pending_request.get("allowed_decisions") or []

    external_communication_phase = None
    if isinstance(runtime_summary, dict):
        external_communication_phase = runtime_summary.get("external_communication_phase")
    if external_communication_phase is None and isinstance(dashboard_snapshot, dict):
        external_communication = dashboard_snapshot.get("external_communication")
        if isinstance(external_communication, dict):
            external_communication_phase = external_communication.get("phase")

    return {
        "thread_id": thread_id,
        "runtime_state": (
            runtime_summary.get("runtime_state")
            if isinstance(runtime_summary, dict)
            else dashboard_snapshot.get("runtime_state")
            if isinstance(dashboard_snapshot, dict)
            else None
        ),
        "updated_at": runtime_summary.get("updated_at") if isinstance(runtime_summary, dict) else None,
        "task_goal": runtime_summary.get("task_goal") if isinstance(runtime_summary, dict) else None,
        "review_summary": (
            runtime_summary.get("review_summary")
            if isinstance(runtime_summary, dict)
            else pending_request.get("review_summary")
            if isinstance(pending_request, dict)
            else None
        ),
        "allowed_decisions": allowed_decisions,
        "request_id": (
            runtime_summary.get("pending_request_id")
            if isinstance(runtime_summary, dict)
            else pending_request.get("request_id")
            if isinstance(pending_request, dict)
            else None
        ),
        "external_communication_phase": external_communication_phase,
    }


# Load the pending-review inbox by scanning persisted runtime artifact directories.
def _list_pending_reviews(*, artifact_store: RuntimeArtifactStore) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for thread_id in artifact_store.list_thread_ids():
        detail = _load_review_detail(thread_id, artifact_store=artifact_store)
        if detail is None:
            continue
        entry = _build_review_inbox_entry(
            thread_id,
            runtime_summary=detail["runtime_summary"],
            dashboard_snapshot=detail["dashboard_snapshot"],
            pending_review=detail["pending_human_review"],
        )
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=lambda item: item.get("updated_at") or "", reverse=True)


# Resolve one static file path while keeping reads inside the built frontend root.
def _resolve_static_path(static_root: Path, request_path: str) -> Path:
    stripped_path = request_path.lstrip("/") or "index.html"
    candidate = (static_root / stripped_path).resolve()
    static_root_resolved = static_root.resolve()
    if static_root_resolved not in {candidate, *candidate.parents}:
        raise FileNotFoundError("Static asset path escapes the dashboard root.")
    return candidate


# Serve the standalone dashboard with API and built frontend assets.
def serve_dashboard(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    artifact_store: RuntimeArtifactStore | None = None,
    checkpointer: Any | None = None,
    resume_flow: DashboardRunner = resume_layer2_flow,
    static_root: Path = DEFAULT_STATIC_ROOT,
    open_browser: bool = False,
) -> None:
    httpd = build_dashboard_httpd(
        host=host,
        port=port,
        artifact_store=artifact_store,
        checkpointer=checkpointer,
        resume_flow=resume_flow,
        static_root=static_root,
    )
    url = f"http://{host}:{port}"
    print(f"Dashboard server listening on {url}", flush=True)

    if open_browser:
        webbrowser.open_new_tab(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


# Build the HTTP server so tests can start and stop it deterministically.
def build_dashboard_httpd(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    artifact_store: RuntimeArtifactStore | None = None,
    checkpointer: Any | None = None,
    resume_flow: DashboardRunner = resume_layer2_flow,
    static_root: Path = DEFAULT_STATIC_ROOT,
) -> ThreadingHTTPServer:
    context = DashboardServerContext(
        artifact_store=artifact_store or RuntimeArtifactStore(),
        checkpointer=checkpointer or build_default_checkpointer(),
        resume_flow=resume_flow,
        static_root=static_root,
    )
    return ThreadingHTTPServer((host, port), _build_request_handler(context=context))


# Build one request handler class bound to the shared dashboard server context.
def _build_request_handler(*, context: DashboardServerContext):
    class DashboardRequestHandler(BaseHTTPRequestHandler):
        server_context = context

        # Route API and static GET requests for the standalone dashboard.
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/health":
                self._write_json({"status": "ok"})
                return
            if parsed.path == "/api/reviews":
                self._write_json({"items": _list_pending_reviews(artifact_store=self.server_context.artifact_store)})
                return
            if parsed.path.startswith("/api/reviews/"):
                self._handle_review_detail(parsed.path)
                return
            self._serve_static(parsed.path)

        # Route typed resume requests for one pending HITL thread.
        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/reviews/") and parsed.path.endswith("/resume"):
                self._handle_resume(parsed.path)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown dashboard route")

        # Silence the default HTTP access log so the operator output stays readable.
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        # Return one review detail payload for the requested thread id.
        def _handle_review_detail(self, request_path: str) -> None:
            thread_id = request_path.removeprefix("/api/reviews/").strip("/")
            if not thread_id or "/" in thread_id:
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown dashboard route")
                return
            detail = _load_review_detail(thread_id, artifact_store=self.server_context.artifact_store)
            if detail is None:
                self.send_error(HTTPStatus.NOT_FOUND, "No persisted review thread was found.")
                return
            self._write_json(detail)

        # Apply one typed human review decision and return the refreshed persisted thread state.
        def _handle_resume(self, request_path: str) -> None:
            thread_id = request_path.removeprefix("/api/reviews/").removesuffix("/resume").strip("/")
            if not thread_id or "/" in thread_id:
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown dashboard route")
                return

            try:
                session = build_dashboard_session_from_thread_id(
                    thread_id,
                    checkpointer=self.server_context.checkpointer,
                    resume_flow=self.server_context.resume_flow,
                )
                payload = _read_json_request(self)
                decision = _parse_human_review_decision(payload, session=session)
                resume_dashboard_session(session, decision)
                detail = _load_review_detail(thread_id, artifact_store=self.server_context.artifact_store)
                if detail is None:
                    task_state = get_dashboard_task_state(session)
                    detail = {
                        "thread_id": thread_id,
                        "runtime_summary": {
                            "thread_id": thread_id,
                            "runtime_state": task_state.current_state.value,
                            "has_pending_human_review": task_state.pending_human_review_request is not None,
                        },
                        "dashboard_snapshot": None,
                        "pending_human_review": None,
                    }
                detail["operator_feedback"] = _build_operator_feedback_message(decision)
                self._write_json(detail)
            except ValueError as exc:
                self._write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

        # Serve the built React application and fall back to index.html for client routes.
        def _serve_static(self, request_path: str) -> None:
            static_root = self.server_context.static_root
            if not static_root.exists():
                self._write_text(
                    _render_missing_frontend_page(static_root),
                    content_type="text/html; charset=utf-8",
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return

            try:
                asset_path = _resolve_static_path(static_root, request_path)
            except FileNotFoundError:
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown dashboard route")
                return

            if request_path in {"/", ""} or (not asset_path.exists() and "." not in request_path.rsplit("/", 1)[-1]):
                asset_path = static_root / "index.html"
            if not asset_path.exists() or not asset_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown dashboard route")
                return

            content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
            self._write_bytes(asset_path.read_bytes(), content_type=f"{content_type}; charset=utf-8" if content_type.startswith(("text/", "application/javascript")) else content_type)

        # Write one JSON response body using the shared no-cache policy.
        def _write_json(
            self,
            payload: dict[str, Any],
            *,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self._write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                content_type="application/json; charset=utf-8",
                status=status,
            )

        # Write one text response body.
        def _write_text(
            self,
            body: str,
            *,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self._write_bytes(body.encode("utf-8"), content_type=content_type, status=status)

        # Write one binary response body.
        def _write_bytes(
            self,
            body: bytes,
            *,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self.send_response(status)
            for header_name, header_value in _build_response_headers(content_type).items():
                self.send_header(header_name, header_value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return DashboardRequestHandler


# Render a small fallback page when the React build has not been generated yet.
def _render_missing_frontend_page(static_root: Path) -> str:
    return (
        "<!doctype html>"
        "<html><head><meta charset=\"utf-8\"><title>Dashboard build missing</title></head>"
        "<body style=\"font-family: sans-serif; padding: 2rem; background: #f7f3ea; color: #1f2937;\">"
        "<h1>Dashboard frontend build missing</h1>"
        f"<p>The server expected static assets under <code>{static_root}</code>.</p>"
        "<p>Run <code>npm install</code> and <code>npm run build</code> inside <code>frontend/dashboard</code>.</p>"
        "</body></html>"
    )


# Parse CLI options for the standalone dashboard server.
def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Layer 2 HITL control plane.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP host for the dashboard server.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP port for the dashboard server.")
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the dashboard in the default browser after startup.",
    )
    return parser.parse_args()


# Start the standalone dashboard server from the CLI.
def main() -> None:
    args = parse_cli_args()
    serve_dashboard(host=args.host, port=args.port, open_browser=args.open_browser)


if __name__ == "__main__":
    main()
