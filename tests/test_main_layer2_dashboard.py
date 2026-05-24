from __future__ import annotations

import sys
import unittest
from pathlib import Path


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
    CapabilityAccessMode,
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
    TrajectoryHealthAssessment,
    build_governance_hitl_dashboard_snapshot,
    build_default_checkpointer,
)
from main_layer2_dashboard import (
    DashboardSession,
    _build_operator_feedback_message,
    _render_dashboard_page_internal,
    build_dashboard_session_from_thread_id,
    create_dashboard_session,
    _build_response_headers,
    _parse_human_review_decision,
    render_dashboard_page,
    resume_dashboard_session,
)
from layer2_runtime_governance import EvidenceQualityAssessment, TrajectoryHealthAssessment


class MainLayer2DashboardTests(unittest.TestCase):
    def test_render_dashboard_page_contains_review_payload_and_actions(self) -> None:
        session = _build_paused_session()

        html = render_dashboard_page(session)

        self.assertIn("Governance HITL Control Plane", html)
        self.assertIn("Layer 2 HITL control plane", html)
        self.assertIn("thread-1", html)
        self.assertIn("Operator Review", html)
        self.assertIn("Review and resume", html)
        self.assertIn("Submit edited draft", html)
        self.assertIn("More evidence", html)
        self.assertIn("Technical details", html)
        self.assertIn("Current Plan Step", html)
        self.assertIn("Raw Snapshot", html)
        self.assertIn('class="card card-hero"', html)
        self.assertIn('class="card card-control"', html)
        self.assertIn('class="control-layout"', html)
        self.assertIn('class="fact-grid"', html)
        self.assertIn('class="resume-fields"', html)
        self.assertIn("External draft", html)
        self.assertIn('name="external_communication_body"', html)
        self.assertIn("thread-1:hitl", html)
        self.assertIn('/assets/dashboard.css', html)
        self.assertIn('/assets/dashboard.js', html)
        self.assertIn('id="dashboard-snapshot-data"', html)
        self.assertIn('id="dashboard-last-checked"', html)

    def test_parse_human_review_decision_supports_external_modify_and_continue(self) -> None:
        session = _build_paused_session()
        payload = {
            "request_id": "thread-1:hitl",
            "thread_id": "thread-1",
            "decision_type": "modify_and_continue",
            "reviewer_id": "operator-1",
            "note": "Submit the revised draft.",
            "external_communication_channel": "slack",
            "external_communication_recipient": "#ops",
            "external_communication_subject": "Updated subject",
            "external_communication_body": "Updated body text.",
            "external_communication_metadata": '{"priority": "critical"}',
        }

        decision = _parse_human_review_decision(payload, session=session)

        self.assertEqual(decision.decision_type, HumanReviewDecisionType.MODIFY_AND_CONTINUE)
        self.assertIsNotNone(decision.external_communication_edit)
        self.assertEqual(
            decision.external_communication_edit.draft.channel,
            "slack",
        )
        self.assertEqual(
            decision.external_communication_edit.draft.recipient,
            "#ops",
        )
        self.assertEqual(
            decision.external_communication_edit.draft.metadata["priority"],
            "critical",
        )
        self.assertEqual(decision.external_communication_edit.note, "Submit the revised draft.")

    def test_parse_human_review_decision_rejects_request_id_mismatch(self) -> None:
        session = _build_paused_session()

        with self.assertRaises(ValueError):
            _parse_human_review_decision(
                {
                    "request_id": "wrong-request-id",
                    "decision_type": "approve_continue",
                },
                session=session,
            )

    def test_render_dashboard_page_shows_inline_form_error_and_preserves_values(self) -> None:
        session = _build_paused_session()
        form_state = {
            "request_id": "thread-1:hitl",
            "decision_type": "modify_and_continue",
            "reviewer_id": "operator-2",
            "note": "Keep these edits.",
            "external_communication_channel": "slack",
            "external_communication_recipient": "#ops",
            "external_communication_subject": "Updated subject",
            "external_communication_body": "",
            "external_communication_metadata": '{"priority": "critical"',
        }

        html = _render_dashboard_page_internal(
            session=session,
            form_state=form_state,
            form_error="External communication metadata must be valid JSON.",
        )

        self.assertIn("Submission blocked.", html)
        self.assertIn("External communication metadata must be valid JSON.", html)
        self.assertIn('value="operator-2"', html)
        self.assertIn('value="slack"', html)
        self.assertIn('value="#ops"', html)
        self.assertIn("Keep these edits.", html)
        self.assertIn("{&quot;priority&quot;: &quot;critical&quot;", html)

    def test_render_dashboard_page_hides_external_edit_form_for_non_external_review(self) -> None:
        session = _build_non_external_paused_session()

        html = render_dashboard_page(session)

        self.assertIn("Operator Review", html)
        self.assertNotIn("Submit edited draft", html)
        self.assertNotIn('name="external_communication_body"', html)

    def test_render_dashboard_page_can_show_post_submit_feedback(self) -> None:
        session = _build_live_session()
        decision = HumanReviewDecision(
            request_id="thread-1:hitl",
            decision_type=HumanReviewDecisionType.MODIFY_AND_CONTINUE,
        )

        html = _render_dashboard_page_internal(
            session=session,
            operator_feedback=_build_operator_feedback_message(decision),
        )

        self.assertIn("Operator action accepted.", html)
        self.assertIn("edited external draft was accepted", html)

    def test_render_dashboard_page_enables_live_refresh_for_active_sessions(self) -> None:
        session = _build_live_session()

        html = render_dashboard_page(session)

        self.assertIn('data-dashboard-mode="live"', html)
        self.assertIn('data-auto-refresh-enabled="true"', html)
        self.assertIn('id="dashboard-snapshot-data"', html)
        self.assertIn('/assets/dashboard.js', html)

    def test_render_dashboard_page_disables_live_refresh_for_terminal_sessions(self) -> None:
        session = _build_terminal_session()

        html = render_dashboard_page(session)

        self.assertIn('data-dashboard-mode="terminal"', html)
        self.assertIn('data-auto-refresh-enabled="false"', html)
        self.assertNotIn("dashboard-snapshot-data", html)
        self.assertIn('/assets/dashboard.js', html)

    def test_dashboard_response_headers_disable_caching(self) -> None:
        html_headers = _build_response_headers("text/html; charset=utf-8")
        json_headers = _build_response_headers("application/json; charset=utf-8")

        for headers in (html_headers, json_headers):
            self.assertEqual(headers["Cache-Control"], "no-store, max-age=0")
            self.assertEqual(headers["Pragma"], "no-cache")
            self.assertEqual(headers["Expires"], "0")

    def test_resume_dashboard_session_uses_typed_human_review_decision(self) -> None:
        captured: dict[str, object] = {}
        session = _build_paused_session(
            resume_flow=_fake_resume_flow(captured),
        )
        decision = HumanReviewDecision(
            request_id="thread-1:hitl",
            decision_type=HumanReviewDecisionType.REQUEST_MORE_EVIDENCE,
            note="Need another source.",
            clarification_text=None,
            reviewer_id="operator-1",
        )

        result = resume_dashboard_session(session, decision)

        self.assertEqual(captured["thread_id"], "thread-1")
        self.assertIs(captured["decision"], decision)
        self.assertEqual(session.final_result, result["final_result"])
        self.assertEqual(result["final_result"]["task_state"].current_state, RuntimeState.ACTING)

    def test_create_dashboard_session_uses_runner_injections(self) -> None:
        captured: dict[str, object] = {}
        session = create_dashboard_session(
            raw_task_text="Test dashboard task.",
            demo_intake_profile="clarification",
            run_flow=_fake_run_flow(captured),
            resume_flow=_fake_resume_flow(captured),
            checkpointer=object(),
        )

        self.assertEqual(captured["run_thread_id"], session.thread_id)
        self.assertEqual(session.final_result["task_state"].current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertIsInstance(build_governance_hitl_dashboard_snapshot(session.final_result["task_state"]).focus.value, str)

    def test_dashboard_session_can_be_rebuilt_from_persisted_thread(self) -> None:
        with self.subTest("persisted thread reload"):
            temp_checkpoint = REPO_ROOT / "artifacts" / "test-temp-dashboard-checkpoints.pkl"
            if temp_checkpoint.exists():
                temp_checkpoint.unlink()
            checkpointer = build_default_checkpointer(storage_path=temp_checkpoint)
            session = create_dashboard_session(
                raw_task_text="Reload this external communication dashboard thread.",
                context_hint="Keep the draft local.",
                demo_intake_profile="clarification",
                resume_flow=_fake_resume_flow({}),
                checkpointer=checkpointer,
            )

            reloaded = build_dashboard_session_from_thread_id(
                session.thread_id,
                checkpointer=checkpointer,
                resume_flow=_fake_resume_flow({}),
            )

            self.assertEqual(reloaded.thread_id, session.thread_id)
            self.assertEqual(
                reloaded.final_result["task_state"].current_state,
                RuntimeState.WAITING_HUMAN_REVIEW,
            )

            if temp_checkpoint.exists():
                temp_checkpoint.unlink()


# Build a paused session with a review payload and a controllable resume runner.
def _build_paused_session(*, resume_flow=None) -> DashboardSession:
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
    task_state.pending_human_review_request = _human_review_request()
    task_state.last_human_review_request = task_state.pending_human_review_request
    task_state.external_communication.phase = ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
    task_state.external_communication.last_transition_summary = (
        "A local draft proposal is waiting for human review."
    )

    return DashboardSession(
        thread_id="thread-1",
        intake_result=intake_result,
        final_result={"task_state": task_state},
        checkpointer=object(),
        resume_flow=resume_flow or _fake_resume_flow({}),
    )


# Build a paused non-external session so the rich external edit form stays hidden.
def _build_non_external_paused_session() -> DashboardSession:
    intake_result = _analysis_intake_result()
    task_state = TaskState.from_intake(intake_result)
    task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
    task_state.human_review_resume_state = RuntimeState.ACTING
    task_state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Collect one more source.",
            rationale="The operator must decide the next research step.",
            expected_outcome="A safer next action.",
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    task_state.current_plan_step_index = 0
    task_state.last_governance_decision = _governance_decision()
    task_state.last_oversight_assessment = _oversight_assessment()
    task_state.pending_human_review_request = HumanReviewRequest(
        request_id="thread-analysis:hitl",
        thread_id="thread-analysis",
        current_state=RuntimeState.WAITING_HUMAN_REVIEW,
        resume_state=RuntimeState.ACTING,
        current_plan_step_id="step_1",
        current_plan_step_objective="Collect one more source.",
        actor_result_type=ActorResultType.PROPOSE_ACTION,
        current_tool_request=ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.WEB_SEARCH,
            reason="Collect one more source.",
            query="official source",
        ),
        governance_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        governance_summary="Human review is required.",
        oversight_summary="Evidence and risk warrant human review.",
        reason_codes=("human_review_required",),
        allowed_decisions=(
            HumanReviewDecisionType.APPROVE_CONTINUE,
            HumanReviewDecisionType.REQUEST_MORE_EVIDENCE,
            HumanReviewDecisionType.REPLAN,
            HumanReviewDecisionType.ASK_CLARIFICATION,
            HumanReviewDecisionType.ABORT,
        ),
        review_summary="The task should pause for operator review.",
    )
    task_state.last_human_review_request = task_state.pending_human_review_request

    return DashboardSession(
        thread_id="thread-analysis",
        intake_result=intake_result,
        final_result={"task_state": task_state},
        checkpointer=object(),
        resume_flow=_fake_resume_flow({}),
    )


# Build a live session that should enable polling but not render review controls.
def _build_live_session() -> DashboardSession:
    intake_result = _intake_result()
    task_state = TaskState.from_intake(intake_result)
    task_state.current_state = RuntimeState.ACTING
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

    return DashboardSession(
        thread_id="thread-live",
        intake_result=intake_result,
        final_result={"task_state": task_state},
        checkpointer=object(),
        resume_flow=_fake_resume_flow({}),
    )


# Build a terminal session that should never poll for updates.
def _build_terminal_session() -> DashboardSession:
    intake_result = _intake_result()
    task_state = TaskState.from_intake(intake_result)
    task_state.current_state = RuntimeState.ABORTED
    task_state.last_governance_decision = _governance_decision()
    task_state.last_oversight_assessment = _oversight_assessment()

    return DashboardSession(
        thread_id="thread-terminal",
        intake_result=intake_result,
        final_result={"task_state": task_state},
        checkpointer=object(),
        resume_flow=_fake_resume_flow({}),
    )


# Build a canonical IntakeResult for dashboard entrypoint tests.
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


# Build a non-external IntakeResult for generic review rendering tests.
def _analysis_intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Review a governed research step.",
                context="Use deterministic fixtures and stay read-only.",
                constraints=["Stay read-only."],
                domain_type=DomainType.ANALYSIS,
                domain_label="dashboard_test_analysis",
                sensitivity=SensitivityLevel.MEDIUM,
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


# Build the pending human review payload for the dashboard tests.
def _human_review_request() -> HumanReviewRequest:
    return HumanReviewRequest(
        request_id="thread-1:hitl",
        thread_id="thread-1",
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


# Build a fake resume runner that records the typed decision and returns a paused-safe result.
def _fake_resume_flow(captured: dict[str, object]):
    def _resume_flow(*, thread_id, human_review_decision, checkpointer):
        captured["thread_id"] = thread_id
        captured["decision"] = human_review_decision
        captured["checkpointer"] = checkpointer
        task_state = _build_resume_result_state()
        return {
            "thread_id": thread_id,
            "final_result": {"task_state": task_state},
        }

    return _resume_flow


# Build a fake starter runner that records the thread id and returns a waiting-review result.
def _fake_run_flow(captured: dict[str, object]):
    def _run_flow(*, raw_task_text, context_hint, policy_profile, thread_id, demo_intake_profile, checkpointer):
        captured["run_thread_id"] = thread_id
        captured["run_task"] = raw_task_text
        captured["run_context"] = context_hint
        captured["run_policy_profile"] = policy_profile
        captured["run_demo_profile"] = demo_intake_profile
        captured["run_checkpointer"] = checkpointer
        task_state = TaskState.from_intake(_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.pending_human_review_request = _human_review_request()
        task_state.last_governance_decision = _governance_decision()
        task_state.last_oversight_assessment = _oversight_assessment()
        return {
            "thread_id": thread_id,
            "intake_result": _intake_result(),
            "final_result": {"task_state": task_state},
        }

    return _run_flow


# Build the resumed runtime state used by the fake resume flow.
def _build_resume_result_state() -> TaskState:
    task_state = TaskState.from_intake(_intake_result())
    task_state.current_state = RuntimeState.ACTING
    task_state.human_review_resume_state = RuntimeState.ACTING
    task_state.last_governance_decision = _governance_decision()
    task_state.last_oversight_assessment = _oversight_assessment()
    task_state.pending_human_review_request = None
    return task_state


if __name__ == "__main__":
    unittest.main()
