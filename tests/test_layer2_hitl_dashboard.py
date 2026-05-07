from __future__ import annotations

import json
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
    DashboardFocus,
    EvidenceQualityAssessment,
    GovernanceDecision,
    GovernanceDirective,
    GovernanceHitlDashboardSnapshot,
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
    StepValidation,
    StepValidationDecision,
    TaskState,
    ToolName,
    ToolRequest,
    TrajectoryHealthAssessment,
    build_governance_hitl_dashboard_snapshot,
    governance_hitl_dashboard_snapshot_to_dict,
    governance_hitl_dashboard_snapshot_to_json,
)


class Layer2HitlDashboardTests(unittest.TestCase):
    def test_dashboard_snapshot_exposes_active_review_payload(self) -> None:
        task_state = TaskState.from_intake(_intake_result())
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

        snapshot = build_governance_hitl_dashboard_snapshot(task_state)
        payload = governance_hitl_dashboard_snapshot_to_dict(snapshot)
        encoded = governance_hitl_dashboard_snapshot_to_json(snapshot)
        decoded = json.loads(encoded)

        self.assertIsInstance(snapshot, GovernanceHitlDashboardSnapshot)
        self.assertEqual(snapshot.focus, DashboardFocus.WAITING_HUMAN_REVIEW)
        self.assertEqual(snapshot.runtime_state, "waiting_human_review")
        self.assertEqual(snapshot.current_plan_step.step_id, "step_1")
        self.assertEqual(snapshot.governance.directive, "wait_human_review")
        self.assertEqual(snapshot.oversight.recommended_directive, "wait_human_review")
        self.assertEqual(snapshot.human_review.resume_state, "acting")
        self.assertEqual(
            snapshot.human_review.allowed_decisions,
            tuple(HumanReviewDecisionType),
        )
        self.assertEqual(payload["focus"], "waiting_human_review")
        self.assertEqual(decoded["human_review"]["pending_request"]["request_id"], "thread-1:hitl")
        self.assertEqual(decoded["reason_codes"], ["human_review_required", "tool_requires_human_review"])
        self.assertEqual(snapshot.external_communication_state, "not_implemented")

    def test_dashboard_snapshot_marks_terminal_states_explicitly(self) -> None:
        task_state = TaskState.from_intake(_intake_result())
        task_state.current_state = RuntimeState.ABORTED
        task_state.last_governance_decision = _denied_governance_decision()
        task_state.last_oversight_assessment = _oversight_assessment()
        task_state.human_review_history = [
            HumanReviewDecision(
                request_id="thread-1:hitl",
                decision_type=HumanReviewDecisionType.ABORT,
                note="Stop the task.",
                reviewer_id="operator-1",
            )
        ]
        task_state.last_human_review_decision = task_state.human_review_history[-1]

        snapshot = build_governance_hitl_dashboard_snapshot(task_state)

        self.assertEqual(snapshot.focus, DashboardFocus.ABORTED)
        self.assertTrue(snapshot.is_terminal)
        self.assertEqual(snapshot.terminal_state, "aborted")
        self.assertIsNotNone(snapshot.human_review)
        self.assertEqual(snapshot.human_review.last_decision.decision_type, HumanReviewDecisionType.ABORT)
        self.assertEqual(snapshot.reason_codes, ("denied_by_policy",))


# Build a canonical IntakeResult for dashboard tests.
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


# Build the governance decision used by the dashboard snapshot.
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


# Build a terminal denial governance decision for the aborted-state test.
def _denied_governance_decision() -> GovernanceDecision:
    return GovernanceDecision(
        directive=GovernanceDirective.STOP,
        policy_decision=PolicyDecision.DENY,
        reason_codes=(GovernanceReasonCode.DENIED_BY_POLICY,),
        summary="The task is denied by policy.",
        constrains_capability_use=True,
        evaluated_actor_result_type=ActorResultType.BLOCKED,
        evaluated_tool_name=None,
        policy_engine_name="test_policy",
        policy_bundle_version="test_v1",
        policy_directive=GovernanceDirective.STOP,
        oversight_recommended_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        oversight_summary="Oversight recommends review, but policy denial wins.",
    )


# Build the deterministic oversight assessment used by the dashboard snapshot.
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


# Build the pending human review payload used by the dashboard snapshot.
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


if __name__ == "__main__":
    unittest.main()
