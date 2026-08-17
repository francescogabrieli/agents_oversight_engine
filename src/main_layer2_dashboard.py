# Dashboard validation uses ValueError so malformed requests map to HTTP 400 responses.
# ruff: noqa: TRY004
from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import webbrowser
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from json_serialization import to_jsonable
from layer1_intake import IntakeResult
from layer2_runtime_governance import (
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewRequest,
    build_default_checkpointer,
    coerce_human_review_decision_payload,
)
from layer2_runtime_governance.composition import (
    Layer2RuntimeDependencies,
    compose_layer2_runtime,
)
from layer2_runtime_governance.domain import (
    TaskState,
)
from layer2_runtime_governance.hitl.decision_inbox import (
    ReviewDecisionInboxPort,
    ReviewDecisionSubmission,
)
from layer4_state_evidence_observability.infrastructure.checkpointing import (
    close_checkpointer,
)
from layer4_state_evidence_observability.infrastructure.review_decision_inbox import (
    SQLiteReviewDecisionInbox,
)
from layer4_state_evidence_observability.infrastructure.runtime_artifacts import (
    RuntimeArtifactStore,
)

# Keep the local dashboard bound to loopback by default.
DEFAULT_HOST = "127.0.0.1"


# Keep the local dashboard port stable for operator workflows.
DEFAULT_PORT = 8022


# Hide stale pending reviews from the inbox after a short operational cutoff.
REVIEW_INBOX_CUTOFF = timedelta(minutes=2)


# Keep recently submitted review actions visible but non-actionable for two minutes.
REVIEW_COMPLETED_CUTOFF = timedelta(minutes=2)


# Resolve the built React application relative to the repo root.
DEFAULT_STATIC_ROOT = Path(__file__).resolve().parents[1] / "frontend" / "dashboard" / "dist"


@dataclass
class DashboardSession:
    # Keep the typed session shape available for checkpoint-backed validation.
    thread_id: str
    intake_result: IntakeResult | None
    final_result: dict[str, Any]
    checkpointer: Any
    artifact_store: RuntimeArtifactStore
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


@dataclass(frozen=True)
class DashboardServerContext:
    # Group the server dependencies so the request handler can stay stateless.
    artifact_store: RuntimeArtifactStore
    checkpointer: Any
    decision_inbox: ReviewDecisionInboxPort
    static_root: Path


class Layer2DashboardHTTPServer(ThreadingHTTPServer):
    # Own and release infrastructure created by the dashboard server factory.
    def __init__(
        self,
        *args,
        owned_checkpointer: Any | None = None,
        owned_decision_inbox: ReviewDecisionInboxPort | None = None,
        **kwargs,
    ) -> None:
        self._owned_checkpointer = owned_checkpointer
        self._owned_decision_inbox = owned_decision_inbox
        super().__init__(*args, **kwargs)

    # Close the saver connection or pool together with the HTTP server.
    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            if self._owned_checkpointer is not None:
                close_checkpointer(self._owned_checkpointer)
                self._owned_checkpointer = None
            if self._owned_decision_inbox is not None:
                self._owned_decision_inbox.close()
                self._owned_decision_inbox = None


# Rebuild a checkpoint-backed dashboard session from an in-memory trace payload.
def build_dashboard_session_from_trace(
    trace: dict[str, Any],
    *,
    checkpointer: Any,
    artifact_store: RuntimeArtifactStore | None = None,
) -> DashboardSession:
    return DashboardSession(
        thread_id=trace["thread_id"],
        intake_result=trace["intake_result"],
        final_result=trace["final_result"],
        checkpointer=checkpointer,
        artifact_store=artifact_store or RuntimeArtifactStore(),
    )


# Rebuild a dashboard session from one persisted LangGraph thread id.
def build_dashboard_session_from_thread_id(
    thread_id: str,
    *,
    checkpointer: Any | None = None,
    artifact_store: RuntimeArtifactStore | None = None,
) -> DashboardSession:
    resolved_checkpointer = checkpointer or build_default_checkpointer()
    resolved_artifact_store = artifact_store or RuntimeArtifactStore()
    runtime_graph = compose_layer2_runtime(
        Layer2RuntimeDependencies(checkpointer=resolved_checkpointer)
    )
    snapshot = runtime_graph.get_state({"configurable": {"thread_id": thread_id}})
    snapshot_values = snapshot.values if snapshot is not None else {}
    if not isinstance(snapshot_values, dict):
        raise ValueError(f"No persisted runtime state is available for thread '{thread_id}'.")

    task_state = snapshot_values.get("task_state")
    if isinstance(task_state, TaskState):
        return DashboardSession(
            thread_id=thread_id,
            intake_result=task_state.intake_payload,
            final_result=dict(snapshot_values),
            checkpointer=resolved_checkpointer,
            artifact_store=resolved_artifact_store,
        )

    # A trace artifact is read-only observability data, never a source for LangGraph resume.
    raise ValueError(f"Persisted thread '{thread_id}' does not contain a canonical LangGraph checkpoint.")


# Return the canonical TaskState held by a checkpoint-backed session.
def get_dashboard_task_state(session: DashboardSession) -> TaskState:
    with session.lock:
        task_state = session.final_result.get("task_state")
        if not isinstance(task_state, TaskState):
            raise ValueError("Dashboard session does not contain a canonical task_state.")
        return task_state


# Recover the pending review payload even when the checkpoint snapshot is missing the full TaskState.
def get_dashboard_pending_review_request(session: DashboardSession) -> HumanReviewRequest:
    with session.lock:
        task_state = session.final_result.get("task_state")
        if isinstance(task_state, TaskState):
            pending_request = task_state.pending_human_review_request
            if pending_request is None:
                raise ValueError("No pending human review request is available.")
            return pending_request

        pending_request = session.final_result.get("pending_human_review_request")
        if isinstance(pending_request, HumanReviewRequest):
            return pending_request
        raise ValueError("No pending human review request is available.")


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


# Convert one JSON payload into the canonical typed HumanReviewDecision contract.
def _parse_human_review_decision(
    payload: dict[str, Any],
    *,
    session: DashboardSession,
) -> HumanReviewDecision:
    pending_request = get_dashboard_pending_review_request(session)

    payload_thread_id = _optional_text(payload.get("thread_id"))
    if payload_thread_id is not None and payload_thread_id != session.thread_id:
        raise ValueError("Submitted thread_id does not match the requested review thread.")

    return coerce_human_review_decision_payload(
        payload,
        pending_request=pending_request,
    )


# Build a compact operator feedback message aligned with the typed decision semantics.
def _build_operator_feedback_message(decision: HumanReviewDecision) -> str:
    if decision.decision_type == HumanReviewDecisionType.MODIFY_AND_CONTINUE:
        return "Decision submitted successfully. The runtime owner will apply the edited proposal."
    if decision.decision_type == HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY:
        return "Decision submitted successfully. The runtime owner will continue read-only."
    if decision.decision_type == HumanReviewDecisionType.APPROVE_CONTINUE:
        return "Decision submitted successfully. The runtime owner will continue the approved path."
    if decision.decision_type == HumanReviewDecisionType.REQUEST_MORE_EVIDENCE:
        return "Decision submitted successfully. The runtime owner will gather more evidence."
    if decision.decision_type == HumanReviewDecisionType.REPLAN:
        return "Decision submitted successfully. The runtime owner will enter replanning."
    if decision.decision_type == HumanReviewDecisionType.ASK_CLARIFICATION:
        return "Decision submitted successfully. The clarification request is queued for the runtime owner."
    return "Decision submitted successfully. The runtime owner will apply the abort decision."


# Persist one validated decision for the sole runtime owner instead of invoking the graph here.
def submit_dashboard_decision(
    session: DashboardSession,
    decision: HumanReviewDecision,
    *,
    decision_inbox: ReviewDecisionInboxPort,
) -> ReviewDecisionSubmission:
    pending_request = get_dashboard_pending_review_request(session)
    payload = to_jsonable(decision)
    if not isinstance(payload, dict):
        raise ValueError("The validated human review decision is not JSON-serializable.")
    task_goal = None
    if session.intake_result is not None:
        task_goal = session.intake_result.structured_task.task_spec.goal
    return decision_inbox.submit(
        thread_id=session.thread_id,
        request_id=decision.request_id,
        decision_type=decision.decision_type.value,
        decision_payload=payload,
        reviewer_id=decision.reviewer_id,
        task_goal=task_goal,
        review_summary=pending_request.review_summary,
    )


# Serialize one decision-delivery record for dashboard diagnostics.
def _decision_submission_to_dict(
    submission: ReviewDecisionSubmission,
    *,
    owner_available: bool,
) -> dict[str, Any]:
    return {
        "submission_id": submission.submission_id,
        "thread_id": submission.thread_id,
        "request_id": submission.request_id,
        "decision_type": submission.decision_type,
        "reviewer_id": submission.reviewer_id,
        "submitted_at": submission.submitted_at,
        "delivery_status": submission.status.value,
        "owner_available": owner_available,
        "error": submission.error,
    }


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
    decision_inbox: ReviewDecisionInboxPort | None = None,
) -> dict[str, Any] | None:
    runtime_summary = artifact_store.read_runtime_summary(thread_id)
    dashboard_snapshot = artifact_store.read_dashboard_snapshot(thread_id)
    legacy_pending_review = artifact_store.read_pending_human_review(thread_id)
    if runtime_summary is None and dashboard_snapshot is None:
        return None
    # Legacy standalone requests are projected into the sole dashboard DTO location.
    if legacy_pending_review is not None:
        dashboard_snapshot = dict(dashboard_snapshot or {})
        human_review = dict(dashboard_snapshot.get("human_review") or {})
        human_review.setdefault("pending_request", legacy_pending_review)
        dashboard_snapshot["human_review"] = human_review
    detail = {
        "thread_id": thread_id,
        "runtime_summary": runtime_summary,
        "dashboard_snapshot": dashboard_snapshot,
    }
    if decision_inbox is None or not isinstance(dashboard_snapshot, dict):
        return detail

    human_review = dashboard_snapshot.get("human_review")
    pending_request = (
        human_review.get("pending_request")
        if isinstance(human_review, dict)
        else None
    )
    request_id = (
        pending_request.get("request_id")
        if isinstance(pending_request, dict)
        else None
    )
    if not isinstance(request_id, str):
        return detail
    submission = decision_inbox.get_for_request(
        thread_id=thread_id,
        request_id=request_id,
    )
    if submission is None:
        return detail

    # Mask only dashboard actionability; the persisted TaskState remains authoritative.
    projected_snapshot = deepcopy(dashboard_snapshot)
    projected_human_review = dict(projected_snapshot.get("human_review") or {})
    projected_human_review["pending_request"] = None
    projected_human_review["allowed_decisions"] = []
    projected_snapshot["human_review"] = projected_human_review
    projected_snapshot["has_pending_review"] = False
    projected_summary = deepcopy(runtime_summary) if isinstance(runtime_summary, dict) else None
    if isinstance(projected_summary, dict):
        projected_summary["has_pending_human_review"] = False
        projected_summary["allowed_decisions"] = []
    detail["runtime_summary"] = projected_summary
    detail["dashboard_snapshot"] = projected_snapshot
    detail["decision_submission"] = _decision_submission_to_dict(
        submission,
        owner_available=decision_inbox.get_live_owner(thread_id=thread_id) is not None,
    )
    return detail


# Normalize one runtime summary into the compact inbox entry rendered by React.
def _build_review_inbox_entry(
    thread_id: str,
    *,
    runtime_summary: dict[str, Any] | None,
    dashboard_snapshot: dict[str, Any] | None,
    pending_review: dict[str, Any] | None,
) -> dict[str, Any] | None:
    updated_at = _parse_runtime_summary_updated_at(runtime_summary)
    if updated_at is None or _is_review_inbox_entry_stale(updated_at):
        return None

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
    execution_intent = (
        runtime_summary.get("execution_intent")
        if isinstance(runtime_summary, dict)
        else None
    )
    external_execution_status = (
        runtime_summary.get("external_execution_status")
        if isinstance(runtime_summary, dict)
        else None
    )
    if isinstance(dashboard_snapshot, dict):
        external_execution = dashboard_snapshot.get("external_execution")
        if isinstance(external_execution, dict):
            execution_intent = execution_intent or external_execution.get(
                "execution_intent"
            )
            external_execution_status = (
                external_execution_status or external_execution.get("status")
            )

    return {
        "thread_id": thread_id,
        "runtime_state": (
            runtime_summary.get("runtime_state")
            if isinstance(runtime_summary, dict)
            else dashboard_snapshot.get("runtime_state")
            if isinstance(dashboard_snapshot, dict)
            else None
        ),
        "updated_at": updated_at.isoformat(timespec="seconds"),
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
        "execution_intent": execution_intent,
        "external_execution_status": external_execution_status,
    }


# Parse the runtime summary timestamp defensively so stale inbox entries can be filtered.
def _parse_runtime_summary_updated_at(
    runtime_summary: dict[str, Any] | None,
) -> datetime | None:
    if not isinstance(runtime_summary, dict):
        return None

    raw_updated_at = runtime_summary.get("updated_at")
    if not isinstance(raw_updated_at, str) or not raw_updated_at.strip():
        return None

    normalized = raw_updated_at.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


# Keep the inbox focused on currently actionable reviews only.
def _is_review_inbox_entry_stale(updated_at: datetime) -> bool:
    return datetime.now(timezone.utc) - updated_at > REVIEW_INBOX_CUTOFF


# Load the pending-review inbox by scanning persisted runtime artifact directories.
def _list_pending_reviews(
    *,
    artifact_store: RuntimeArtifactStore,
    decision_inbox: ReviewDecisionInboxPort,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for thread_id in artifact_store.list_thread_ids():
        detail = _load_review_detail(
            thread_id,
            artifact_store=artifact_store,
            decision_inbox=decision_inbox,
        )
        if detail is None:
            continue
        entry = _build_review_inbox_entry(
            thread_id,
            runtime_summary=detail["runtime_summary"],
            dashboard_snapshot=detail["dashboard_snapshot"],
            pending_review=None,
        )
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=lambda item: item.get("updated_at") or "", reverse=True)


# Project one recently submitted review as completed control-plane work, not a terminal run.
def _build_completed_review_entry(
    submission: ReviewDecisionSubmission,
    *,
    decision_inbox: ReviewDecisionInboxPort,
) -> dict[str, Any]:
    return {
        "thread_id": submission.thread_id,
        "runtime_state": "review_completed",
        "updated_at": submission.submitted_at,
        "task_goal": submission.task_goal,
        "review_summary": submission.review_summary,
        "allowed_decisions": [],
        "request_id": submission.request_id,
        "external_communication_phase": None,
        "execution_intent": None,
        "external_execution_status": None,
        "decision_type": submission.decision_type,
        "delivery_status": submission.status.value,
        "owner_available": (
            decision_inbox.get_live_owner(thread_id=submission.thread_id) is not None
        ),
    }


# Return active and recently completed review actions as distinct dashboard sections.
def _list_review_inbox(
    *,
    artifact_store: RuntimeArtifactStore,
    decision_inbox: ReviewDecisionInboxPort,
) -> dict[str, list[dict[str, Any]]]:
    recent = decision_inbox.list_recent(
        within_seconds=int(REVIEW_COMPLETED_CUTOFF.total_seconds())
    )
    return {
        "items": _list_pending_reviews(
            artifact_store=artifact_store,
            decision_inbox=decision_inbox,
        ),
        "completed_items": [
            _build_completed_review_entry(item, decision_inbox=decision_inbox)
            for item in recent
        ],
    }


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
    decision_inbox: ReviewDecisionInboxPort | None = None,
    static_root: Path = DEFAULT_STATIC_ROOT,
    open_browser: bool = False,
) -> None:
    httpd = build_dashboard_httpd(
        host=host,
        port=port,
        artifact_store=artifact_store,
        checkpointer=checkpointer,
        decision_inbox=decision_inbox,
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
    decision_inbox: ReviewDecisionInboxPort | None = None,
    static_root: Path = DEFAULT_STATIC_ROOT,
) -> ThreadingHTTPServer:
    owns_checkpointer = checkpointer is None
    owns_decision_inbox = decision_inbox is None
    resolved_checkpointer = checkpointer or build_default_checkpointer()
    resolved_decision_inbox = decision_inbox or SQLiteReviewDecisionInbox()
    context = DashboardServerContext(
        artifact_store=artifact_store or RuntimeArtifactStore(),
        checkpointer=resolved_checkpointer,
        decision_inbox=resolved_decision_inbox,
        static_root=static_root,
    )
    return Layer2DashboardHTTPServer(
        (host, port),
        _build_request_handler(context=context),
        owned_checkpointer=resolved_checkpointer if owns_checkpointer else None,
        owned_decision_inbox=(
            resolved_decision_inbox if owns_decision_inbox else None
        ),
    )


# Build one request handler class bound to the shared dashboard server context.
def _build_request_handler(*, context: DashboardServerContext):
    class DashboardRequestHandler(BaseHTTPRequestHandler):
        server_context = context

        # Route API and static GET requests for the standalone dashboard.
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/health":
                self._write_json({"status": "ok"})
                return
            if parsed.path == "/api/reviews":
                self._write_json(
                    _list_review_inbox(
                        artifact_store=self.server_context.artifact_store,
                        decision_inbox=self.server_context.decision_inbox,
                    )
                )
                return
            if parsed.path.startswith("/api/reviews/"):
                self._handle_review_detail(parsed.path)
                return
            self._serve_static(parsed.path)

        # Route typed resume requests for one pending HITL thread.
        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/reviews/") and parsed.path.endswith("/resume"):
                self._handle_resume(parsed.path)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown dashboard route")

        # Silence the default HTTP access log so the operator output stays readable.
        def log_message(self, _format: str, *_args: Any) -> None:
            return

        # Return one review detail payload for the requested thread id.
        def _handle_review_detail(self, request_path: str) -> None:
            thread_id = request_path.removeprefix("/api/reviews/").strip("/")
            if not thread_id or "/" in thread_id:
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown dashboard route")
                return
            detail = _load_review_detail(
                thread_id,
                artifact_store=self.server_context.artifact_store,
                decision_inbox=self.server_context.decision_inbox,
            )
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
                    artifact_store=self.server_context.artifact_store,
                )
                payload = _read_json_request(self)
                decision = _parse_human_review_decision(payload, session=session)
                submission = submit_dashboard_decision(
                    session,
                    decision,
                    decision_inbox=self.server_context.decision_inbox,
                )
                detail = _load_review_detail(
                    thread_id,
                    artifact_store=self.server_context.artifact_store,
                    decision_inbox=self.server_context.decision_inbox,
                )
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
                    }
                detail["operator_feedback"] = _build_operator_feedback_message(decision)
                detail["decision_submission"] = _decision_submission_to_dict(
                    submission,
                    owner_available=(
                        self.server_context.decision_inbox.get_live_owner(
                            thread_id=thread_id
                        )
                        is not None
                    ),
                )
                self._write_json(detail)
            except ValueError as exc:
                self._write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # noqa: BLE001  # pragma: no cover
                # Keep backend failures visible to the operator instead of dropping the connection.
                self._write_json(
                    {"error": f"Unable to submit the human review decision: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )

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
