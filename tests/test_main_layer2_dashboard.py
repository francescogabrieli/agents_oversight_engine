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
)
from main_layer2_dashboard import (
    DashboardSession,
    create_dashboard_session,
    _build_response_headers,
    render_dashboard_page,
    resume_dashboard_session,
)
from layer2_runtime_governance import EvidenceQualityAssessment, TrajectoryHealthAssessment


class MainLayer2DashboardTests(unittest.TestCase):
    def test_render_dashboard_page_contains_review_payload_and_actions(self) -> None:
        session = _build_paused_session()

        html = render_dashboard_page(session)

        self.assertIn("Governance HITL Control Plane", html)
        self.assertIn("thread-1", html)
        self.assertIn("Current Plan Step", html)
        self.assertIn("Human Review", html)
        self.assertIn("approve continue", html)
        self.assertIn("request more evidence", html)
        self.assertIn("thread-1:hitl", html)
        self.assertIn('id="dashboard-snapshot-data"', html)
        self.assertIn('fetch("/snapshot.json"', html)
        self.assertIn('id="dashboard-last-checked"', html)

    def test_render_dashboard_page_enables_live_refresh_for_active_sessions(self) -> None:
        session = _build_live_session()

        html = render_dashboard_page(session)

        self.assertIn('data-dashboard-mode="live"', html)
        self.assertIn('data-auto-refresh-enabled="true"', html)
        self.assertIn('id="dashboard-snapshot-data"', html)
        self.assertIn('fetch("/snapshot.json"', html)

    def test_render_dashboard_page_disables_live_refresh_for_terminal_sessions(self) -> None:
        session = _build_terminal_session()

        html = render_dashboard_page(session)

        self.assertIn('data-dashboard-mode="terminal"', html)
        self.assertIn('data-auto-refresh-enabled="false"', html)
        self.assertNotIn("dashboard-snapshot-data", html)
        self.assertNotIn('fetch("/snapshot.json"', html)

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
    task_state.pending_human_review_request = _human_review_request()
    task_state.last_human_review_request = task_state.pending_human_review_request

    return DashboardSession(
        thread_id="thread-1",
        intake_result=intake_result,
        final_result={"task_state": task_state},
        checkpointer=object(),
        resume_flow=resume_flow or _fake_resume_flow({}),
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
                goal="Inspect the HITL dashboard contract.",
                context="Use deterministic fixtures.",
                constraints=["Stay read-only."],
                domain_type=DomainType.ANALYSIS,
                domain_label="dashboard_test",
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
