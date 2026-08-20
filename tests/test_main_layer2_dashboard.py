from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import shutil
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib import request


REPO_ROOT = Path(__file__).resolve().parents[1]

from layer1_intake import (
    ExecutionIntent,
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskSpec,
)
from layer2_runtime_governance import (
    ActorResultType,
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    ExternalCommunicationState,
    GovernanceDirective,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewRequest,
    PlanStep,
    PlanStepStatus,
    RuntimeState,
    TaskState,
    ToolName,
    ToolRequest,
)
from layer4_state_evidence_observability.infrastructure.runtime_artifacts import (
    RuntimeArtifactStore,
    persist_runtime_artifacts,
)
from layer4_state_evidence_observability.infrastructure.review_decision_inbox import (
    SQLiteReviewDecisionInbox,
)
from main_layer2_dashboard import (
    DashboardSession,
    _build_response_headers,
    _parse_human_review_decision,
    build_dashboard_session_from_thread_id,
    build_dashboard_httpd,
    submit_dashboard_decision,
)
from tests.dashboard_test_factories import (
    build_dashboard_governance_decision,
    build_dashboard_oversight_assessment,
)


class MainLayer2DashboardTests(unittest.TestCase):
    # Keep server tests isolated under a temporary artifact root and static build directory.
    def setUp(self) -> None:
        self.temp_root = REPO_ROOT / "_test_dashboard_runtime"
        self.static_root = REPO_ROOT / "_test_dashboard_static"
        shutil.rmtree(self.temp_root, ignore_errors=True)
        shutil.rmtree(self.static_root, ignore_errors=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.static_root.mkdir(parents=True, exist_ok=True)
        (self.static_root / "index.html").write_text(
            "<!doctype html><html><body><div id='root'>dashboard</div></body></html>",
            encoding="utf-8",
        )
        self.artifact_store = RuntimeArtifactStore(root_path=self.temp_root)
        self.decision_inbox = SQLiteReviewDecisionInbox(
            storage_path=self.temp_root / "review_decisions.sqlite3"
        )

    # Clean up test artifacts and stop any leftover dashboard server.
    def tearDown(self) -> None:
        self.decision_inbox.close()
        shutil.rmtree(self.temp_root, ignore_errors=True)
        shutil.rmtree(self.static_root, ignore_errors=True)

    # Verify the typed parser accepts nested external draft edits for modify_and_continue.
    def test_parse_human_review_decision_supports_nested_external_modify_and_continue(self) -> None:
        session = _build_paused_session()

        decision = _parse_human_review_decision(
            {
                "thread_id": "thread-1",
                "request_id": "thread-1:hitl",
                "decision_type": "modify_and_continue",
                "reviewer_id": "operator-1",
                "note": "Apply the revised draft.",
                "external_communication_edit": {
                    "draft": {
                        "channel": "email",
                        "recipient": "ops@example.com",
                        "subject": "Draft update",
                        "body": "Updated body text.",
                        "metadata": {"priority": "critical"},
                    }
                },
            },
            session=session,
        )

        self.assertEqual(decision.decision_type, HumanReviewDecisionType.MODIFY_AND_CONTINUE)
        self.assertIsNotNone(decision.external_communication_edit)
        self.assertEqual(decision.external_communication_edit.draft.body, "Updated body text.")
        self.assertEqual(
            decision.external_communication_edit.draft.metadata["priority"],
            "critical",
        )

    # Verify request id mismatches still fail before any resume logic runs.
    def test_parse_human_review_decision_rejects_request_id_mismatch(self) -> None:
        session = _build_paused_session()

        with self.assertRaisesRegex(ValueError, "request_id"):
            _parse_human_review_decision(
                {
                    "thread_id": "thread-1",
                    "request_id": "wrong-request-id",
                    "decision_type": "approve_continue",
                },
                session=session,
            )

    # Verify clarification requests remain typed and cannot omit their required text.
    def test_parse_human_review_decision_requires_text_for_clarification(self) -> None:
        session = _build_paused_session()

        with self.assertRaisesRegex(ValueError, "Clarification text is required"):
            _parse_human_review_decision(
                {
                    "thread_id": "thread-1",
                    "request_id": "thread-1:hitl",
                    "decision_type": "ask_clarification",
                    "clarification_text": "",
                },
                session=session,
            )

    # Verify legacy flat metadata still fails cleanly when it is not valid JSON.
    def test_parse_human_review_decision_rejects_invalid_metadata(self) -> None:
        session = _build_paused_session()

        with self.assertRaisesRegex(ValueError, "valid JSON"):
            _parse_human_review_decision(
                {
                    "thread_id": "thread-1",
                    "request_id": "thread-1:hitl",
                    "decision_type": "modify_and_continue",
                    "external_communication_metadata": "{not-json}",
                },
                session=session,
            )

    # Verify both API and static responses disable caching for live operator workflows.
    def test_dashboard_response_headers_disable_caching(self) -> None:
        html_headers = _build_response_headers("text/html; charset=utf-8")
        json_headers = _build_response_headers("application/json; charset=utf-8")

        for headers in (html_headers, json_headers):
            self.assertEqual(headers["Cache-Control"], "no-store, max-age=0")
            self.assertEqual(headers["Pragma"], "no-cache")
            self.assertEqual(headers["Expires"], "0")

    # Verify the inbox endpoint filters only pending reviews and sorts by newest first.
    def test_api_reviews_lists_only_pending_sorted(self) -> None:
        now = datetime.now(timezone.utc)
        _persist_review_artifacts(
            self.artifact_store,
            thread_id="thread-a",
            updated_at=(now - timedelta(seconds=90)).isoformat(timespec="seconds"),
        )
        _persist_review_artifacts(
            self.artifact_store,
            thread_id="thread-b",
            updated_at=(now - timedelta(seconds=30)).isoformat(timespec="seconds"),
        )
        _persist_non_review_artifacts(
            self.artifact_store,
            thread_id="thread-c",
            updated_at=(now - timedelta(seconds=45)).isoformat(timespec="seconds"),
        )

        payload = self._request_json("/api/reviews")

        self.assertEqual([item["thread_id"] for item in payload["items"]], ["thread-b", "thread-a"])
        self.assertEqual(payload["items"][0]["runtime_state"], "waiting_human_review")

    # Verify stale pending reviews fall out of the inbox after the operational cutoff.
    def test_api_reviews_excludes_stale_pending_threads(self) -> None:
        now = datetime.now(timezone.utc)
        _persist_review_artifacts(
            self.artifact_store,
            thread_id="thread-fresh",
            updated_at=(now - timedelta(seconds=45)).isoformat(timespec="seconds"),
        )
        _persist_review_artifacts(
            self.artifact_store,
            thread_id="thread-stale",
            updated_at=(now - timedelta(minutes=3)).isoformat(timespec="seconds"),
        )

        payload = self._request_json("/api/reviews")

        self.assertEqual([item["thread_id"] for item in payload["items"]], ["thread-fresh"])

    # Verify the detail endpoint returns the persisted snapshot and thread summary.
    def test_api_review_detail_returns_snapshot(self) -> None:
        _persist_review_artifacts(self.artifact_store, thread_id="thread-detail", updated_at="2026-06-05T10:00:00+00:00")

        payload = self._request_json("/api/reviews/thread-detail")

        self.assertEqual(payload["thread_id"], "thread-detail")
        self.assertEqual(payload["runtime_summary"]["task_goal"], "Prepare a cautious external communication draft.")
        self.assertEqual(payload["dashboard_snapshot"]["runtime_state"], "waiting_human_review")
        self.assertNotIn("pending_human_review", payload)
        self.assertIsNotNone(payload["dashboard_snapshot"]["human_review"]["pending_request"])

    # Verify session loading uses the canonical state supplied by the LangGraph checkpointer.
    def test_build_dashboard_session_uses_canonical_checkpoint_state(self) -> None:
        task_state = _build_paused_session(thread_id="thread-refresh").final_result["task_state"]

        class _RefreshableCheckpointer:
            def __init__(self) -> None:
                self.refreshed = False

        class _FakeGraph:
            def __init__(self, checkpointer) -> None:
                self.checkpointer = checkpointer

            def get_state(self, _config):
                return mock.Mock(values={"task_state": task_state})

        checkpointer = _RefreshableCheckpointer()

        with mock.patch(
            "main_layer2_dashboard.compose_layer2_runtime",
            side_effect=lambda dependencies: _FakeGraph(dependencies.checkpointer),
        ):
            session = build_dashboard_session_from_thread_id(
                "thread-refresh",
                checkpointer=checkpointer,
                artifact_store=self.artifact_store,
            )

        self.assertEqual(session.thread_id, "thread-refresh")
        self.assertEqual(session.final_result["task_state"].current_state, RuntimeState.WAITING_HUMAN_REVIEW)

    # Verify artifacts cannot be used as a substitute for a canonical LangGraph checkpoint.
    def test_build_dashboard_session_rejects_artifact_only_state(self) -> None:
        _persist_review_artifacts(
            self.artifact_store,
            thread_id="thread-artifact-fallback",
            updated_at="2026-06-05T10:00:00+00:00",
        )

        class _RefreshableCheckpointer:
            def refresh_from_disk(self) -> None:
                return None

        class _FakeGraph:
            def get_state(self, _config):
                return mock.Mock(values={"thread_id": "thread-artifact-fallback"})

        with mock.patch(
            "main_layer2_dashboard.compose_layer2_runtime",
            side_effect=lambda checkpointer: _FakeGraph(),
        ), self.assertRaisesRegex(ValueError, "canonical LangGraph checkpoint"):
            build_dashboard_session_from_thread_id(
                "thread-artifact-fallback",
                checkpointer=_RefreshableCheckpointer(),
                artifact_store=self.artifact_store,
            )

    # Verify the API queues one decision and immediately makes the review non-actionable.
    def test_api_resume_queues_decision_for_runtime_owner(self) -> None:
        _persist_review_artifacts(self.artifact_store, thread_id="thread-resume", updated_at="2026-06-05T10:00:00+00:00")
        session = _build_paused_session(thread_id="thread-resume")
        self.decision_inbox.acquire_owner(
            thread_id="thread-resume",
            owner_id="cli-owner",
            process_id=100,
            terminal_label="test-terminal",
            status="waiting_human_review",
        )

        with mock.patch(
            "main_layer2_dashboard.build_dashboard_session_from_thread_id",
            return_value=session,
        ):
            payload = self._request_json(
                "/api/reviews/thread-resume/resume",
                method="POST",
                body={
                    "thread_id": "thread-resume",
                    "request_id": "thread-resume:hitl",
                    "decision_type": "approve_continue",
                    "reviewer_id": "operator-1",
                },
            )

        self.assertEqual(payload["runtime_summary"]["runtime_state"], "waiting_human_review")
        self.assertFalse(payload["dashboard_snapshot"]["has_pending_review"])
        self.assertEqual(
            payload["operator_feedback"],
            "Decision submitted successfully. The runtime owner will continue the approved path.",
        )
        self.assertEqual(payload["decision_submission"]["delivery_status"], "pending")
        self.assertTrue(payload["decision_submission"]["owner_available"])
        self.assertEqual(
            session.final_result["task_state"].current_state,
            RuntimeState.WAITING_HUMAN_REVIEW,
        )

        inbox = self._request_json("/api/reviews")
        self.assertEqual(inbox["items"], [])
        self.assertEqual(len(inbox["completed_items"]), 1)
        self.assertEqual(inbox["completed_items"][0]["decision_type"], "approve_continue")

        claimed = self.decision_inbox.claim_next(
            thread_id="thread-resume",
            request_id="thread-resume:hitl",
            owner_id="cli-owner",
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.decision_payload["request_id"], "thread-resume:hitl")

    # Verify submission persists the typed decision without invoking the runtime graph.
    def test_submit_dashboard_decision_uses_decision_inbox(self) -> None:
        session = _build_paused_session(
            thread_id="thread-forward-store",
            artifact_store=self.artifact_store,
        )
        pending_request = session.final_result["task_state"].pending_human_review_request
        assert pending_request is not None
        decision = HumanReviewDecision(
            request_id=pending_request.request_id,
            decision_type=HumanReviewDecisionType.APPROVE_CONTINUE,
            reviewer_id="operator-1",
        )
        submission = submit_dashboard_decision(
            session,
            decision,
            decision_inbox=self.decision_inbox,
        )

        self.assertEqual(submission.thread_id, "thread-forward-store")
        self.assertEqual(submission.request_id, pending_request.request_id)
        self.assertEqual(submission.decision_type, "approve_continue")
        self.assertEqual(submission.decision_payload["reviewer_id"], "operator-1")

    # Verify the static React entrypoint is served with the no-cache headers.
    def test_static_index_is_served_with_no_cache_headers(self) -> None:
        status, headers, body = self._request_raw("/")

        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store, max-age=0")
        self.assertIn("dashboard", body.decode("utf-8"))

    # Start a temporary dashboard HTTP server, send one request, and return the decoded JSON payload.
    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        status, _, raw_body = self._request_raw(path, method=method, body=body)
        self.assertEqual(status, 200)
        return json.loads(raw_body.decode("utf-8"))

    # Start a temporary dashboard HTTP server and return the raw status, headers, and response body.
    def _request_raw(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        httpd = build_dashboard_httpd(
            host="127.0.0.1",
            port=0,
            artifact_store=self.artifact_store,
            checkpointer=object(),
            decision_inbox=self.decision_inbox,
            static_root=self.static_root,
        )
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
        try:
            url = f"http://127.0.0.1:{httpd.server_port}{path}"
            encoded_body = None
            headers = {}
            if body is not None:
                encoded_body = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            response = request.urlopen(
                request.Request(url, data=encoded_body, headers=headers, method=method)
            )
            return response.status, dict(response.headers.items()), response.read()
        finally:
            httpd.shutdown()
            httpd.server_close()
            server_thread.join(timeout=2)


# Build a canonical paused session with a pending external communication review payload.
def _build_paused_session(
    *,
    thread_id: str = "thread-1",
    artifact_store: RuntimeArtifactStore | None = None,
) -> DashboardSession:
    intake_result = _intake_result()
    task_state = TaskState.from_intake(intake_result)
    task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
    task_state.human_review_resume_state = RuntimeState.ACTING
    task_state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Collect official evidence.",
            rationale="The task should begin with grounding.",
            expected_outcome="Evidence gathered.",
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    task_state.current_plan_step_index = 0
    task_state.last_governance_decision = build_dashboard_governance_decision()
    task_state.last_oversight_assessment = build_dashboard_oversight_assessment()
    task_state.external_communication = ExternalCommunicationState(
        phase=ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
    )
    task_state.external_communication.draft = ExternalCommunicationDraft(
        channel="email",
        recipient="ops@example.com",
        subject="Draft update",
        body="Please review this draft before sending.",
        metadata={"priority": "high"},
    )
    task_state.pending_human_review_request = _human_review_request(thread_id=thread_id)
    task_state.last_human_review_request = task_state.pending_human_review_request
    task_state.external_communication.phase = ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
    task_state.external_communication.last_transition_summary = (
        "A local draft proposal is waiting for human review."
    )

    return DashboardSession(
        thread_id=thread_id,
        intake_result=intake_result,
        final_result={"task_state": task_state},
        checkpointer=object(),
        artifact_store=artifact_store or RuntimeArtifactStore(root_path=REPO_ROOT / "_test_dashboard_session_runtime"),
    )


# Persist one pending-review artifact set and optionally override its summary timestamp.
def _persist_review_artifacts(
    artifact_store: RuntimeArtifactStore,
    *,
    thread_id: str,
    updated_at: str,
) -> None:
    session = _build_paused_session(thread_id=thread_id)
    persist_runtime_artifacts(
        thread_id=thread_id,
        intake_result=session.intake_result,
        task_state=session.final_result["task_state"],
        artifact_store=artifact_store,
    )
    _rewrite_summary_timestamp(artifact_store=artifact_store, thread_id=thread_id, updated_at=updated_at)


# Persist one non-review artifact set so inbox filtering can exclude it.
def _persist_non_review_artifacts(
    artifact_store: RuntimeArtifactStore,
    *,
    thread_id: str,
    updated_at: str,
) -> None:
    intake_result = _intake_result()
    task_state = TaskState.from_intake(intake_result)
    task_state.current_state = RuntimeState.COMPLETED
    task_state.last_governance_decision = build_dashboard_governance_decision()
    task_state.last_oversight_assessment = build_dashboard_oversight_assessment()
    persist_runtime_artifacts(
        thread_id=thread_id,
        intake_result=intake_result,
        task_state=task_state,
        artifact_store=artifact_store,
    )
    _rewrite_summary_timestamp(artifact_store=artifact_store, thread_id=thread_id, updated_at=updated_at)


# Override one runtime summary timestamp to make inbox ordering deterministic.
def _rewrite_summary_timestamp(
    *,
    artifact_store: RuntimeArtifactStore,
    thread_id: str,
    updated_at: str,
) -> None:
    summary_path = artifact_store.build_paths(thread_id).runtime_summary_path
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["updated_at"] = updated_at
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


# Build a canonical IntakeResult for dashboard tests.
def _intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Prepare a cautious external communication draft.",
                context="Use deterministic fixtures and keep the draft local.",
                constraints=["Stay read-only."],
                execution_intent=ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
                sensitivity=SensitivityLevel.HIGH,
                success_criteria=["Expose a typed dashboard snapshot."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                policy_profile_name="dashboard_profile",
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="dashboard_test_intake",
            prompt_version="dashboard_test_v1",
            schema_version="1.0.0",
            warnings=[],
            missing_information=[],
        ),
    )


# Build the pending human review payload used by the dashboard tests.
def _human_review_request(*, thread_id: str) -> HumanReviewRequest:
    return HumanReviewRequest(
        request_id=f"{thread_id}:hitl",
        thread_id=thread_id,
        current_state=RuntimeState.WAITING_HUMAN_REVIEW,
        resume_state=RuntimeState.ACTING,
        current_plan_step_id="step_1",
        current_plan_step_objective="Collect official evidence.",
        actor_result_type=ActorResultType.PROPOSE_ACTION,
        current_tool_request=ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.WEB_SEARCH,
            reason="Collect official evidence.",
            query="official source",
        ),
        governance_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        governance_summary="Human review is required.",
        oversight_summary="Evidence and risk warrant human review.",
        reason_codes=("human_review_required", "tool_requires_human_review"),
        external_communication_target_type="email",
        external_communication_draft=ExternalCommunicationDraft(
            channel="email",
            recipient="ops@example.com",
            subject="Draft update",
            body="Please review this draft before sending.",
            metadata={"priority": "high"},
        ),
        allowed_decisions=tuple(HumanReviewDecisionType),
        review_summary="The task should pause for operator review.",
    )


if __name__ == "__main__":
    unittest.main()
