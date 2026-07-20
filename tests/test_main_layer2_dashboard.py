from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import shutil
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib import request


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from layer1_intake import (
    BudgetConfig,
    DomainType,
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
    GovernanceDecision,
    GovernanceDirective,
    GovernanceReasonCode,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewRequest,
    OperationalRiskAssessment,
    OversightAssessment,
    OversightReasonCode,
    OversightRecommendedDirective,
    OversightSignalLevel,
    PlanStep,
    PlanStepStatus,
    PolicyDecision,
    RuntimeState,
    TaskState,
    ToolName,
    ToolRequest,
)
from layer2_runtime_governance import EvidenceQualityAssessment, TrajectoryHealthAssessment
from layer2_runtime_governance.infrastructure.runtime_artifacts import (
    RuntimeArtifactStore,
    persist_runtime_artifacts,
)
from main_layer2_dashboard import (
    DashboardSession,
    _build_response_headers,
    _parse_human_review_decision,
    build_dashboard_session_from_thread_id,
    build_dashboard_httpd,
    resume_dashboard_session,
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

    # Clean up test artifacts and stop any leftover dashboard server.
    def tearDown(self) -> None:
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

    # Verify resume applies a typed decision and returns the refreshed persisted thread state.
    def test_api_resume_returns_refreshed_thread(self) -> None:
        _persist_review_artifacts(self.artifact_store, thread_id="thread-resume", updated_at="2026-06-05T10:00:00+00:00")
        session = _build_paused_session(thread_id="thread-resume")

        def _fake_resume_dashboard_session(
            session: DashboardSession,
            human_review_decision: HumanReviewDecision,
        ) -> dict[str, object]:
            task_state = session.final_result["task_state"]
            task_state.current_state = RuntimeState.ACTING
            task_state.pending_human_review_request = None
            task_state.human_review_resume_state = RuntimeState.ACTING
            task_state.external_communication.phase = ExternalCommunicationPhase.DRAFT_IN_PROGRESS
            persist_runtime_artifacts(
                thread_id=session.thread_id,
                intake_result=session.intake_result,
                task_state=task_state,
                artifact_store=self.artifact_store,
            )
            return {
                "thread_id": session.thread_id,
                "final_result": {"task_state": task_state},
                "decision": human_review_decision,
            }

        with mock.patch(
            "main_layer2_dashboard.build_dashboard_session_from_thread_id",
            return_value=session,
        ), mock.patch(
            "main_layer2_dashboard.resume_dashboard_session",
            side_effect=_fake_resume_dashboard_session,
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

        self.assertEqual(payload["runtime_summary"]["runtime_state"], "acting")
        self.assertEqual(
            payload["operator_feedback"],
            "The runtime resumed on the approved execution path.",
        )

        inbox = self._request_json("/api/reviews")
        self.assertEqual(inbox["items"], [])

    # Verify the dashboard forwards resume persistence through its bound artifact store.
    def test_resume_dashboard_session_uses_session_artifact_store(self) -> None:
        session = _build_paused_session(
            thread_id="thread-forward-store",
            artifact_store=self.artifact_store,
        )
        captured: dict[str, object] = {}
        pending_request = session.final_result["task_state"].pending_human_review_request
        assert pending_request is not None
        decision = HumanReviewDecision(
            request_id=pending_request.request_id,
            decision_type=HumanReviewDecisionType.APPROVE_CONTINUE,
            reviewer_id="operator-1",
        )

        def _resume_flow(**kwargs):
            captured.update(kwargs)
            return {
                "thread_id": session.thread_id,
                "final_result": {"task_state": session.final_result["task_state"]},
            }

        session.resume_flow = _resume_flow

        resume_dashboard_session(session, decision)

        self.assertIs(captured["artifact_store"], self.artifact_store)
        self.assertEqual(captured["thread_id"], "thread-forward-store")
        self.assertEqual(captured["human_review_decision"], decision)

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
    task_state.last_governance_decision = _governance_decision()
    task_state.last_oversight_assessment = _oversight_assessment()
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
    task_state.last_governance_decision = _governance_decision()
    task_state.last_oversight_assessment = _oversight_assessment()
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
                domain_type=DomainType.EXTERNAL_COMMUNICATION,
                domain_label="dashboard_test_external_comm",
                sensitivity=SensitivityLevel.HIGH,
                success_criteria=["Expose a typed dashboard snapshot."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                budget=BudgetConfig(max_steps=4, max_tool_calls=3),
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


# Build the governance decision used by the dashboard tests.
def _governance_decision() -> GovernanceDecision:
    return GovernanceDecision(
        directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        policy_decision=PolicyDecision.REQUIRE_HUMAN_REVIEW,
        reason_codes=(GovernanceReasonCode.HUMAN_REVIEW_REQUIRED,),
        summary="Human review is required.",
        constrains_capability_use=True,
        evaluated_actor_result_type=ActorResultType.PROPOSE_ACTION,
        evaluated_tool_name=ToolName.WEB_SEARCH,
        policy_engine_name="test_policy",
        policy_bundle_version="test_v1",
        policy_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        oversight_recommended_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        oversight_summary="Oversight recommends escalation.",
    )


# Build the deterministic oversight assessment used by the dashboard tests.
def _oversight_assessment() -> OversightAssessment:
    return OversightAssessment(
        evidence_quality=EvidenceQualityAssessment(
            score=0.52,
            level=OversightSignalLevel.MODERATE,
            reason_codes=(OversightReasonCode.EVIDENCE_MARKED_SUFFICIENT,),
        ),
        operational_risk=OperationalRiskAssessment(
            score=0.78,
            level=OversightSignalLevel.HIGH,
            reason_codes=(OversightReasonCode.TOOL_REQUIRES_HUMAN_REVIEW,),
        ),
        trajectory_health=TrajectoryHealthAssessment(
            score=0.44,
            level=OversightSignalLevel.DEGRADED,
            reason_codes=(OversightReasonCode.RECOMMEND_HUMAN_REVIEW,),
        ),
        recommended_directive=OversightRecommendedDirective.WAIT_HUMAN_REVIEW,
        summary="Evidence and risk warrant human review.",
        reason_codes=(OversightReasonCode.RECOMMEND_HUMAN_REVIEW,),
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
