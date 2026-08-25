from __future__ import annotations

import unittest
from dataclasses import replace

from layer1_intake import (
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskSpec,
)
from layer2_runtime_governance import (
    ActorResult,
    ActorResultType,
    CapabilityAccessMode,
    PlanStep,
    PlanStepStatus,
    RuntimeState,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepValidation,
    TaskState,
    ToolObservation,
    build_policy_evaluation_input,
)
from layer2_runtime_governance.domain import (
    HumanReviewDecision,
    HumanReviewDecisionType,
    ReplanCheckpoint,
    StepRecord,
    ToolAttempt,
    ToolDisposition,
)
from layer2_runtime_governance.signal_services import (
    build_evidence_signal,
    build_risk_signal,
    build_trajectory_signal,
)
from layer3_tool_bus_external_capabilities.external_operations import (
    ExternalOperationEffect,
    ExternalOperationProposal,
    OperationBoundary,
    normalize_generic_external_operation,
)


class Layer2OversightRawFactsTests(unittest.TestCase):
    # Verify Python exports evidence facts without computing a parallel score or directive.
    def test_evidence_signal_is_unscored_and_preserves_concrete_observation(self) -> None:
        state = _build_state()
        state.observations.append(
            ToolObservation(
                observation_id="tool_evidence_1",
                locator="https://example.test/source",
                content="A detailed fact collected by the tool.",
            )
        )

        signal = build_evidence_signal(task_state=state, thresholds_version="policy.v1")

        self.assertIsNone(signal["diagnostic_score"])
        self.assertEqual(signal["diagnostic_level"], "unscored")
        self.assertNotIn("recommended_directive", signal)
        self.assertTrue(signal["raw_facts"]["selected_evidence_refs_valid"])
        self.assertTrue(signal["raw_facts"]["selected_observations_have_content"])
        self.assertEqual(signal["raw_facts"]["observation_count"], 1)

    # Verify operational risk is expressed as raw policy facts only.
    def test_risk_signal_does_not_load_weights_or_choose_directive(self) -> None:
        state = _build_state()
        policy_input = build_policy_evaluation_input(task_state=state)

        signal = build_risk_signal(
            policy_input=policy_input,
            external_operation=None,
            thresholds_version="policy.v1",
        )

        self.assertIsNone(signal["diagnostic_score"])
        self.assertEqual(signal["raw_facts"]["mode"], "shadow")
        self.assertFalse(signal["raw_facts"]["decision_influence"])
        self.assertEqual(signal["raw_facts"]["applicability"], "not_applicable")
        self.assertNotIn("recommended_directive", signal)

    # Verify trajectory counters stay factual and leave threshold interpretation to OPA.
    def test_trajectory_signal_contains_counters_without_python_scoring(self) -> None:
        state = _build_state()
        state.actor_turn_count = 3
        state.scratchpad.extend(
            ScratchpadEntry(
                turn_index=index,
                plan_step_id="step_1",
                entry_type=ScratchpadEntryType.PROGRESS_NOTE,
                summary=f"Per-step actor turn {index}.",
            )
            for index in range(1, 4)
        )
        policy_input = build_policy_evaluation_input(task_state=state)

        signal = build_trajectory_signal(
            task_state=state,
            policy_input=policy_input,
            thresholds_version="policy.v1",
        )

        # Point confidence is contextual uncertainty in base_facts, never trajectory.
        self.assertNotIn("configured_confidence_threshold", signal["raw_facts"])
        self.assertNotIn("actor_self_confidence", signal["raw_facts"])
        self.assertEqual(signal["raw_facts"]["actor_turn_count"], 3)
        self.assertEqual(signal["raw_facts"]["completed_step_count"], 0)
        self.assertNotIn("consecutive_no_progress_turns", signal["raw_facts"])
        self.assertFalse(signal["raw_facts"]["needs_replan_signal"])
        self.assertEqual(signal["raw_facts"]["external_action_revision_count"], 0)
        self.assertIsNone(signal["diagnostic_score"])
        self.assertNotIn("recommended_directive", signal)

    # Synthetic cache and guard outcomes remain diagnostic, never backend failures.
    def test_trajectory_failure_streak_counts_only_executed_tool_attempts(self) -> None:
        state = _build_state()
        state.tool_attempts.extend(
            (
                ToolAttempt(
                    action_key="fetch_url_text:https://example.test",
                    plan_step_id="step_1",
                    progress_epoch=0,
                    disposition=ToolDisposition.EXECUTED_TERMINAL_FAILURE,
                ),
                ToolAttempt(
                    action_key="fetch_url_text:https://example.test",
                    plan_step_id="step_1",
                    progress_epoch=0,
                    disposition=ToolDisposition.REJECTED_DUPLICATE,
                ),
                ToolAttempt(
                    action_key="fetch_url_text:https://other.test",
                    plan_step_id="step_1",
                    progress_epoch=0,
                    disposition=ToolDisposition.REJECTED_CIRCUIT,
                ),
            )
        )

        signal = build_trajectory_signal(
            task_state=state,
            policy_input=build_policy_evaluation_input(task_state=state),
            thresholds_version="policy.v1",
        )

        self.assertEqual(signal["raw_facts"]["failed_tool_call_count"], 1)
        self.assertEqual(signal["raw_facts"]["consecutive_tool_failures"], 1)
        self.assertEqual(signal["raw_facts"]["synthetic_tool_result_count"], 2)

        # Human review and other material progress boundaries advance the epoch,
        # so an acknowledged old failure streak cannot immediately re-escalate.
        state.progress_epoch += 1
        released_signal = build_trajectory_signal(
            task_state=state,
            policy_input=build_policy_evaluation_input(task_state=state),
            thresholds_version="policy.v1",
        )
        self.assertEqual(
            released_signal["raw_facts"]["consecutive_tool_failures"],
            0,
        )

    # Discovery reads in one actor batch are evidence collection, not action oscillation.
    def test_action_churn_ignores_queued_evidence_read_variants(self) -> None:
        state = _build_state()
        first_read = _operation_proposal(
            marker="https://example.test/source-a",
            boundary=OperationBoundary.EVIDENCE_READ,
        )
        second_read = _operation_proposal(
            marker="https://example.test/source-b",
            boundary=OperationBoundary.EVIDENCE_READ,
        )
        state.external_operation_history.extend(
            (first_read, second_read, first_read, second_read)
        )
        state.external_operation_proposal = second_read

        signal = build_trajectory_signal(
            task_state=state,
            policy_input=build_policy_evaluation_input(task_state=state),
            thresholds_version="policy.v1",
        )

        self.assertEqual(signal["raw_facts"]["action_churn_count"], 0)
        self.assertFalse(signal["raw_facts"]["external_candidate"])

    # True write-candidate oscillation remains visible to the Trajectory policy.
    def test_action_churn_preserves_governed_external_write_variants(self) -> None:
        state = _build_state()
        first_read = _operation_proposal(
            marker="https://example.test/source-a",
            boundary=OperationBoundary.EVIDENCE_READ,
        )
        second_read = _operation_proposal(
            marker="https://example.test/source-b",
            boundary=OperationBoundary.EVIDENCE_READ,
        )
        first_write = _operation_proposal(
            marker="recipient-a@example.test",
            boundary=OperationBoundary.GOVERNED_EXTERNAL,
        )
        second_write = _operation_proposal(
            marker="recipient-b@example.test",
            boundary=OperationBoundary.GOVERNED_EXTERNAL,
        )
        state.external_operation_history.extend(
            (
                first_read,
                second_read,
                first_read,
                first_write,
                second_write,
                first_write,
                second_write,
            )
        )
        state.external_operation_proposal = second_write

        signal = build_trajectory_signal(
            task_state=state,
            policy_input=build_policy_evaluation_input(task_state=state),
            thresholds_version="policy.v1",
        )

        self.assertEqual(signal["raw_facts"]["action_churn_count"], 2)
        self.assertTrue(signal["raw_facts"]["external_candidate"])

    # A reviewed run starts a fresh replan episode even when older checkpoints remain.
    def test_replan_facts_use_only_the_current_human_review_episode(self) -> None:
        state = _build_state()
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.NEEDS_REPLAN,
            summary="A fresh recovery trajectory is required.",
            capability_mode=CapabilityAccessMode.NONE,
            blocking_reason="The approved continuation needs a new source path.",
        )
        for index in range(2):
            state.replan_checkpoints.append(
                ReplanCheckpoint(
                    actor_turn_count=index + 1,
                    tool_result_count=0,
                    observation_count=0,
                    completed_step_count=0,
                    human_review_count=0,
                    plan_signature=f"old-episode-{index}",
                    request_reason="Old episode recovery.",
                )
            )
            state.step_history.append(
                StepRecord(
                    step_index=index,
                    runtime_state=RuntimeState.REPLANNING,
                    node_name="replanner",
                    summary="Installed an old-episode replacement trajectory.",
                )
            )
        state.human_review_history.append(
            HumanReviewDecision(
                request_id="review:replan-ceiling",
                decision_type=HumanReviewDecisionType.APPROVE_CONTINUE,
            )
        )

        fresh_episode = build_trajectory_signal(
            task_state=state,
            policy_input=build_policy_evaluation_input(task_state=state),
            thresholds_version="policy.v1",
        )

        self.assertEqual(fresh_episode["raw_facts"]["replan_count"], 2)
        self.assertEqual(fresh_episode["raw_facts"]["replan_episode_count"], 0)
        self.assertTrue(fresh_episode["raw_facts"]["first_replan_request"])
        self.assertTrue(
            fresh_episode["raw_facts"]["observable_change_since_last_replan"]
        )

        state.replan_checkpoints.append(
            ReplanCheckpoint(
                actor_turn_count=3,
                tool_result_count=0,
                observation_count=0,
                completed_step_count=0,
                human_review_count=1,
                plan_signature="current-episode-0",
                request_reason="Current episode recovery.",
            )
        )
        current_episode = build_trajectory_signal(
            task_state=state,
            policy_input=build_policy_evaluation_input(task_state=state),
            thresholds_version="policy.v1",
        )

        self.assertEqual(current_episode["raw_facts"]["replan_episode_count"], 1)
        self.assertFalse(current_episode["raw_facts"]["first_replan_request"])
        self.assertFalse(
            current_episode["raw_facts"]["observable_change_since_last_replan"]
        )

    # Keep the produced Trajectory fact inventory explicit so new fields cannot silently drift.
    def test_trajectory_fact_inventory_is_explicit(self) -> None:
        state = _build_state()
        signal = build_trajectory_signal(
            task_state=state,
            policy_input=build_policy_evaluation_input(task_state=state),
            thresholds_version="policy.v1",
        )

        self.assertEqual(
            set(signal["raw_facts"]),
            {
                "actor_turn_count",
                "progress_epoch",
                "completed_step_count",
                "tool_call_count",
                "successful_tool_call_count",
                "failed_tool_call_count",
                "synthetic_tool_result_count",
                "consecutive_tool_failures",
                "action_churn_count",
                "observation_count",
                "replan_count",
                "replan_episode_count",
                "consecutive_provider_protocol_failures",
                "needs_replan_signal",
                "replan_reason_present",
                "first_replan_request",
                "observable_change_since_last_replan",
                "completion_signal",
                "external_candidate",
                "external_action_revision_count",
                "repeated_rejected_payload_digest",
                "unchanged_resubmission",
                "unresolvable_revision",
                "provider_recovery_active",
                "provider_protocol_retry_exhausted",
            },
        )


# Build a deterministic actor-ready state without invoking any provider.
def _build_state() -> TaskState:
    intake = IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Summarize the supplied material.",
                context="Use only grounded evidence.",
                constraints=["Do not invent facts."],
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Return a concise supported summary."],
            ),
            execution_policy=ExecutionPolicy(
                policy_profile_name="default",
                confidence_threshold=0.65,
                read_only_mode=True,
            ),
        ),
        metadata=IntakeMetadata(parse_status=ParseStatus.COMPLETE),
    )
    state = TaskState.from_intake(intake)
    state.current_state = RuntimeState.ACTING
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Produce a detailed grounded summary.",
            rationale="The task needs a supported synthesis.",
            expected_outcome="A concise summary.",
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    state.current_plan_step_index = 0
    validation = StepValidation(
        plan_step_id="step_1",
        actor_self_confidence=0.9,
        evidence_refs=("tool_evidence_1",),
        rationale_summary="The evidence supports the summary.",
    )
    state.actor_result = ActorResult(
        plan_step_id="step_1",
        result_type=ActorResultType.STEP_COMPLETED,
        summary="Prepared the grounded summary.",
        capability_mode=CapabilityAccessMode.NONE,
        output_text="Grounded summary.",
        step_validation=validation,
    )
    state.step_validations.append(validation)
    return state


# Build alternating candidates with one stable logical operation identity.
def _operation_proposal(
    *, marker: str, boundary: OperationBoundary
) -> ExternalOperationProposal:
    proposal = normalize_generic_external_operation(
        operation_id="trajectory-operation",
        plan_step_id="step_1",
        capability_id=(
            "fetch_url_text"
            if boundary == OperationBoundary.EVIDENCE_READ
            else "external_write"
        ),
        action=(
            "fetch_url_text"
            if boundary == OperationBoundary.EVIDENCE_READ
            else "update_external_target"
        ),
        target=marker,
        fields=(("candidate", marker),),
        effect=(
            ExternalOperationEffect.READ
            if boundary == OperationBoundary.EVIDENCE_READ
            else ExternalOperationEffect.UPDATE
        ),
        execution_candidate=True,
    )
    return replace(proposal, boundary=boundary)


if __name__ == "__main__":
    unittest.main()
