from __future__ import annotations

import json
import unittest

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
    DashboardFocus,
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    ExternalCommunicationState,
    GovernanceDecision,
    GovernanceDirective,
    GovernanceHitlDashboardSnapshot,
    GovernanceReasonCode,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    HumanReviewRequest,
    PlanStep,
    PlanStepStatus,
    PolicyDecision,
    RuntimeState,
    TaskState,
    ToolName,
    ToolRequest,
    build_governance_hitl_dashboard_snapshot,
    governance_hitl_dashboard_snapshot_to_dict,
    governance_hitl_dashboard_snapshot_to_json,
)
from tests.dashboard_test_factories import (
    build_dashboard_governance_decision,
    build_dashboard_oversight_assessment,
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
        task_state.last_governance_decision = build_dashboard_governance_decision()
        task_state.last_oversight_assessment = build_dashboard_oversight_assessment()
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
            tuple(
                decision
                for decision in HumanReviewDecisionType
                if decision != HumanReviewDecisionType.MODIFY_AND_CONTINUE
            ),
        )
        self.assertEqual(payload["focus"], "waiting_human_review")
        self.assertEqual(decoded["human_review"]["pending_request"]["request_id"], "thread-1:hitl")
        self.assertEqual(decoded["reason_codes"], ["human_review_required", "tool_requires_human_review"])
        self.assertIsNone(snapshot.external_communication)

    def test_dashboard_snapshot_marks_terminal_states_explicitly(self) -> None:
        task_state = TaskState.from_intake(_intake_result())
        task_state.current_state = RuntimeState.ABORTED
        task_state.last_governance_decision = _denied_governance_decision()
        task_state.last_oversight_assessment = build_dashboard_oversight_assessment()
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

    def test_dashboard_snapshot_exposes_external_communication_phase(self) -> None:
        task_state = TaskState.from_intake(_external_communication_intake_result())
        task_state.external_communication = ExternalCommunicationState(
            phase=ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        )
        task_state.external_communication.phase = ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        task_state.external_communication.draft = ExternalCommunicationDraft(
            channel="email",
            recipient="ops@example.com",
            subject="Draft update",
            body="Please review this draft.",
            metadata={"priority": "high"},
        )
        task_state.external_communication.last_applied_edit = HumanReviewExternalEdit(
            draft=ExternalCommunicationDraft(
                channel="email",
                recipient="ops@example.com",
                subject="Updated draft",
                body="Please review the updated draft.",
                metadata={"priority": "high"},
            ),
            note="Clarified the request before submission.",
        )
        task_state.external_communication.last_transition_summary = (
            "A draft proposal reached the governed review gate."
        )

        snapshot = build_governance_hitl_dashboard_snapshot(task_state)

        self.assertIsNotNone(snapshot.external_communication)
        self.assertEqual(snapshot.external_communication.phase, "human_review_required")
        self.assertEqual(snapshot.external_communication.draft.channel, "email")
        self.assertEqual(
            snapshot.external_communication.last_applied_edit.note,
            "Clarified the request before submission.",
        )
        self.assertIn("governed review gate", snapshot.external_communication.last_transition_summary)

    def test_dashboard_snapshot_coerces_restored_external_draft_dict(self) -> None:
        task_state = TaskState.from_intake(_external_communication_intake_result())
        task_state.external_communication = ExternalCommunicationState(
            phase=ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        )
        task_state.external_communication.phase = ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        task_state.external_communication.draft = {
            "channel": "email",
            "recipient": "ops@example.com",
            "subject": "Restored subject",
            "body": "Restored body",
            "metadata": {"source": "checkpoint"},
        }

        snapshot = build_governance_hitl_dashboard_snapshot(task_state)

        self.assertEqual(snapshot.external_communication.draft.channel, "email")
        self.assertEqual(snapshot.external_communication.draft.recipient, "ops@example.com")


# Build a canonical IntakeResult for dashboard tests.
def _intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Inspect the HITL dashboard contract.",
                context="Use deterministic fixtures.",
                constraints=["Stay read-only."],
                sensitivity=SensitivityLevel.MEDIUM,
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


# Build an external communication intake so the snapshot can expose the typed branch.
def _external_communication_intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Prepare a cautious external email draft.",
                context="Do not send anything automatically.",
                constraints=["Keep the draft local."],
                execution_intent=ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
                sensitivity=SensitivityLevel.HIGH,
                success_criteria=["Pause before any outward-facing execution."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.75,
                policy_profile_name="external_comm_profile",
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
        external_communication_target_type=None,
        external_communication_draft=None,
        allowed_decisions=tuple(
            decision
            for decision in HumanReviewDecisionType
            if decision != HumanReviewDecisionType.MODIFY_AND_CONTINUE
        ),
        review_summary="The task should pause for operator review.",
    )


if __name__ == "__main__":
    unittest.main()
