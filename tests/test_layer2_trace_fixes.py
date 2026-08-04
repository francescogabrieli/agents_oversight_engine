from __future__ import annotations

import unittest
from unittest.mock import MagicMock

# Fixtures must be imported first because they inject src/ into sys.path.
from tests.layer2_runtime_fixtures import _build_policy_input_task_state

from layer1_intake import (
    BudgetConfig,
    ExecutionIntent,
    ExecutionPolicy,
    IntakeMetadata,
    SensitivityLevel,
    TaskSpec,
)
from layer2_runtime_governance.actor_contract import (
    ActorPlanStep,
    ActorProtocolFeedback,
    ActorResult,
    ActorResultType,
    ActorTask,
    build_actor_task,
)
from layer2_runtime_governance.domain import (
    AgentWorkspace,
    ExternalCommunicationDraft,
    ExternalCommunicationState,
    PlanStepStatus,
    ReplanCheckpoint,
    RuntimeState,
    StepValidation,
    TaskState,
)
from layer2_runtime_governance.domain.serialization import (
    normalize_task_state_serialization,
)
from layer2_runtime_governance.execution.errors import ActorExecutionError
from layer2_runtime_governance.execution.prompt_projection import (
    build_actor_prompt_payload,
)
from layer2_runtime_governance.execution.result_builder import (
    _selected_evidence_refs,
    build_provider_protocol_failure_result,
)
from layer3_tool_bus_external_capabilities.external_operations import (
    CommunicationOperationPayload,
    DestinationClass,
    OperationBoundary,
    ExternalOperationEffect,
    ExternalOperationFamily,
    ExternalOperationProposal,
    ExternalOperationTarget,
    GovernanceActionFeedback,
    JudgeFindingSnapshot,
    OperationFactsProvenance,
    compute_payload_digest,
)
from layer2_runtime_governance.governance_components import (
    GovernanceDecision,
    GovernanceDirective,
    GovernanceReasonCode,
    PolicyDecision,
    build_policy_evaluation_input,
)
from layer2_runtime_governance.judge import (
    JudgeContext,
    build_judge_system_prompt,
    build_judge_user_prompt,
)
from layer2_runtime_governance.runtime.actor_service import actor
from layer2_runtime_governance.runtime.governance_service import (
    _apply_governance_decision,
)
from layer2_runtime_governance.signal_services.evidence_service import (
    build_evidence_signal,
)
from layer2_runtime_governance.signal_services.judge_service import (
    build_judge_context,
)
from layer2_runtime_governance.signal_services.trajectory_service import (
    build_trajectory_signal,
)
from layer3_tool_bus_external_capabilities.tool_contract import (
    ToolExecutionResult,
    ToolName,
    ToolObservation,
    ToolRequest,
)

def _build_test_task_state() -> TaskState:
    return _build_policy_input_task_state()


class TraceFixesIntegrationTests(unittest.TestCase):
    def test_fix1_unknown_evidence_ref_raises_structured_actor_execution_error(self) -> None:
        state = _build_test_task_state()
        obs = ToolObservation(observation_id="obs-valid-1", content="Concrete AI benchmark results")
        state.observations.append(obs)
        actor_task = build_actor_task(task_state=state)

        with self.assertRaises(ActorExecutionError) as ctx:
            _selected_evidence_refs(
                actor_task=actor_task,
                arguments={"evidence_refs": ["obs-valid-1", "obs-nonexistent-99"]},
            )

        err = ctx.exception
        self.assertEqual(err.error_code, "unknown_evidence_reference")
        self.assertEqual(err.error_category, "evidence_reference")
        self.assertTrue(err.recoverable)
        self.assertIn("unknown_evidence_ids", err.details)
        self.assertEqual(err.details["unknown_evidence_ids"], ["obs-nonexistent-99"])
        self.assertIn("obs-valid-1", err.details["valid_evidence_ids"])

    def test_fix1_bounded_local_retry_and_state_persistence(self) -> None:
        state = _build_test_task_state()
        state.current_state = RuntimeState.ACTING
        obs = ToolObservation(observation_id="obs-1", content="Benchmark score 95%")
        state.observations.append(obs)

        class MockProtocolFailingActor:
            actor_name = "mock_failing_actor"
            prompt_version = "v1"
            attempts = 0

            def act(self, *, actor_task: ActorTask) -> ActorResult:
                self.attempts += 1
                if self.attempts == 1:
                    err = ActorExecutionError(
                        "Unknown evidence ID obs-bad-99",
                        error_code="unknown_evidence_reference",
                        error_category="evidence_reference",
                        details={"unknown_evidence_ids": ["obs-bad-99"], "valid_evidence_ids": ["obs-1"]},
                    )
                    return build_provider_protocol_failure_result(
                        actor_task=actor_task,
                        detail=str(err),
                        error=err,
                    )
                # Second attempt succeeds using valid evidence ID
                return ActorResult(
                    plan_step_id=actor_task.current_plan_step.step_id,
                    result_type=ActorResultType.STEP_COMPLETED,
                    summary="Completed step with valid evidence",
                    output_text="Materialized step output text",
                    step_validation=StepValidation(
                        plan_step_id=actor_task.current_plan_step.step_id,
                        actor_self_confidence=0.9,
                        evidence_refs=("obs-1",),
                        rationale_summary="Valid evidence reference provided",
                        residual_uncertainty="None",
                    ),
                )

        mock_actor = MockProtocolFailingActor()
        res_state = actor(state, actor_service=mock_actor) # type: ignore[arg-type]

        self.assertEqual(mock_actor.attempts, 2)
        self.assertFalse(res_state.actor_result.provider_protocol_failure) # type: ignore[union-attr]
        self.assertEqual(res_state.actor_protocol_retry_count, 1)

        # Check serialization survival
        norm_state = normalize_task_state_serialization(res_state)
        self.assertEqual(norm_state.actor_protocol_retry_count, 1)

    def test_fix2_full_judge_feedback_in_prompt_projection(self) -> None:
        state = _build_test_task_state()
        finding = JudgeFindingSnapshot(
            category="completeness",
            code="email_body_not_evidently_within_500_words",
            severity="medium",
            summary="Email body is 650 words, exceeds target limit",
            evidence_refs=("obs-1",),
        )
        feedback = GovernanceActionFeedback(
            operation_id="op-1",
            invalidated_payload_digest="digest-123",
            finding_codes=("email_body_not_evidently_within_500_words",),
            summary="Judge flagged length defect",
            revision_index=1,
            findings=(finding,),
            judge_summary="Draft email body length defect",
            judge_rationale="The requested draft requires concise summary under 500 words.",
        )
        state.governance_action_feedback.append(feedback)

        actor_task = build_actor_task(task_state=state)
        payload = build_actor_prompt_payload(actor_task=actor_task)

        proj_fb = payload["governance_feedback"][0]
        self.assertEqual(proj_fb["judge_summary"], "Draft email body length defect")
        self.assertEqual(proj_fb["judge_rationale"], "The requested draft requires concise summary under 500 words.")
        self.assertEqual(len(proj_fb["findings"]), 1)
        self.assertEqual(proj_fb["findings"][0]["code"], "email_body_not_evidently_within_500_words")

    def test_fix3_rejected_draft_projected_during_revision(self) -> None:
        state = _build_test_task_state()
        draft = ExternalCommunicationDraft(
            channel="email",
            recipient="user@example.com",
            subject="AI News Update",
            body="Initial draft content over 500 words...",
        )
        state.external_communication = ExternalCommunicationState(
            phase=state.external_communication.phase if state.external_communication else None, # type: ignore[arg-type]
            draft=draft,
        )
        state.governance_action_feedback.append(
            GovernanceActionFeedback(
                operation_id="op-1",
                invalidated_payload_digest="digest-xyz",
                finding_codes=("length_defect",),
                summary="Rejected draft",
                revision_index=1,
            )
        )

        actor_task = build_actor_task(task_state=state)
        payload = build_actor_prompt_payload(actor_task=actor_task)

        self.assertIsNotNone(payload["previous_rejected_draft"])
        self.assertEqual(payload["previous_rejected_draft"]["recipient"], "user@example.com")
        self.assertEqual(payload["previous_rejected_draft"]["body"], "Initial draft content over 500 words...")

    def test_fix6_evidence_facts_calculation(self) -> None:
        state = _build_test_task_state()

        search_tr = ToolExecutionResult(
            request=ToolRequest(plan_step_id="step_1", tool_name=ToolName.WEB_SEARCH, reason="Search news", query="AI news"),
            success=True,
            summary="Found search results",
            observations=(
                ToolObservation(observation_id="obs-search-1", content="Search result title: Top AI Models 2026", locator="search:ai-news"),
                ToolObservation(observation_id="obs-search-2", content="Search result title: Benchmark Report", locator="search:benchmarks"),
            ),
        )
        fetch_tr = ToolExecutionResult(
            request=ToolRequest(plan_step_id="step_1", tool_name=ToolName.FETCH_URL_TEXT, reason="Fetch full article", url="https://example.com/article"),
            success=True,
            summary="Fetched full text",
            observations=(
                ToolObservation(observation_id="obs-fetch-1", content="Detailed full text of AI benchmarks...", locator="url:https://example.com/article"),
            ),
        )
        state.tool_results_history.extend([search_tr, fetch_tr])
        state.observations.extend(list(search_tr.observations) + list(fetch_tr.observations))

        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Step complete",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.85,
                evidence_refs=("obs-search-1", "obs-fetch-1"),
                rationale_summary="Supported by search title and fetched document",
                residual_uncertainty="None",
            ),
        )

        signal = build_evidence_signal(task_state=state, thresholds_version="v1")
        facts = signal["raw_facts"]

        self.assertEqual(facts["observation_count"], 3)
        self.assertEqual(facts["selected_evidence_ref_count"], 2)
        self.assertAlmostEqual(facts["selected_ref_ratio"], 2 / 3, places=3)
        self.assertEqual(facts["search_title_only_ref_count"], 1)

    def test_fix7_full_communication_body_passed_to_judge(self) -> None:
        state = _build_test_task_state()
        long_body = "SENTINEL_START_" + ("x" * 5000) + "_SENTINEL_END"
        payload = CommunicationOperationPayload(
            channel="email",
            recipient="eval@example.com",
            subject="Detailed Report",
            body=long_body,
            destination_class=DestinationClass.INTERNAL,
        )
        digest = compute_payload_digest(payload)
        proposal = ExternalOperationProposal(
            operation_id="op-long-body",
            plan_step_id="step_1",
            capability_id="email.send",
            boundary=OperationBoundary.GOVERNED_EXTERNAL,
            operation_family=ExternalOperationFamily.COMMUNICATION,
            normalized_effect=ExternalOperationEffect.SEND,
            execution_candidate=True,
            target=ExternalOperationTarget(kind="email", value="eval@example.com", destination_class=DestinationClass.INTERNAL),
            payload=payload,
            payload_preview=long_body[:4096],
            payload_digest=digest,
            facts_provenance=OperationFactsProvenance(normalizer_id="test", normalizer_version="v1", source_kind="actor_draft"),
        )
        state.external_operation_proposal = proposal

        judge_context = build_judge_context(
            task_state=state,
            external_operation=proposal,
            invocation_reason="test_full_body",
        )

        self.assertEqual(judge_context.external_communication_body, long_body)
        self.assertEqual(
            judge_context.operation_stage.value,
            "communication_draft_review",
        )

        user_prompt = build_judge_user_prompt(context=judge_context)
        self.assertIn("SENTINEL_START_", user_prompt)
        self.assertIn("_SENTINEL_END", user_prompt)
        self.assertIn(long_body, user_prompt)

    # Double protocol failure during the same turn escalates as one typed recovery event.
    def test_fix8_double_protocol_failure_escalates_needs_replan(self) -> None:
        state = _build_test_task_state()
        state.current_state = RuntimeState.ACTING
        obs = ToolObservation(observation_id="obs-1", content="concrete evidence")
        state.observations.append(obs)

        class AlwaysFailingActor:
            actor_name = "always_failing_actor"
            prompt_version = "v1"
            attempts = 0

            def act(self, *, actor_task: ActorTask) -> ActorResult:
                self.attempts += 1
                return build_provider_protocol_failure_result(
                    actor_task=actor_task,
                    detail="emitted a malformed envelope",
                )

        mock_actor = AlwaysFailingActor()
        res_state = actor(state, actor_service=mock_actor) # type: ignore[arg-type]

        self.assertEqual(mock_actor.attempts, 2)
        self.assertIsNotNone(res_state.actor_result)
        self.assertEqual(res_state.actor_result.result_type, ActorResultType.NEEDS_REPLAN)
        self.assertTrue(res_state.actor_result.provider_protocol_failure)
        self.assertIn("local protocol recovery is exhausted", res_state.actor_result.blocking_reason)
        self.assertEqual(res_state.current_state, RuntimeState.REPLANNING)
        self.assertEqual(res_state.consecutive_provider_protocol_failures, 1)
        # Audit trail records the local retry attempt.
        retry_records = [
            record for record in res_state.step_history
            if "retried locally" in (record.summary or "")
        ]
        self.assertGreaterEqual(len(retry_records), 1)

    # An exhausted NEEDS_REPLAN with protocol failure follows the OPA directive
    # instead of being unconditionally forced to replanning.
    def test_fix9_exhausted_protocol_needs_replan_follows_opa_directive(self) -> None:
        state = _build_test_task_state()
        state.actor_result = ActorResult(
            plan_step_id=state.current_plan_step.step_id,
            result_type=ActorResultType.NEEDS_REPLAN,
            summary="Provider recovery exhausted its one local correction.",
            blocking_reason="local protocol recovery is exhausted",
            provider_protocol_failure=True,
        )
        policy_input = build_policy_evaluation_input(task_state=state)
        # OPA returned WAIT_HUMAN_REVIEW (e.g. replan_request_stalled).
        decision = GovernanceDecision(
            directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
            policy_decision=PolicyDecision.REQUIRE_HUMAN_REVIEW,
            reason_codes=(GovernanceReasonCode.REPLAN_REQUEST_STALLED,),
            summary="The actor repeated a replan request without a new observable basis.",
            constrains_capability_use=False,
            evaluated_actor_result_type=ActorResultType.NEEDS_REPLAN,
            evaluated_tool_name=None,
            policy_engine_name="test_policy",
            policy_bundle_version="test_v1",
            policy_directive=GovernanceDirective.REPLAN,
            oversight_recommended_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        )

        _apply_governance_decision(
            state,
            governance_decision=decision,
            policy_input=policy_input,
        )

        # The short-circuit no longer intercepts NEEDS_REPLAN; OPA directive wins.
        self.assertEqual(state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)

        # Control: BLOCKED protocol failure still short-circuits.
        blocked_state = _build_test_task_state()
        blocked_state.actor_result = ActorResult(
            plan_step_id=blocked_state.current_plan_step.step_id,
            result_type=ActorResultType.BLOCKED,
            summary="No usable runtime action.",
            blocking_reason="no tool use",
            provider_protocol_failure=True,
        )
        blocked_policy_input = build_policy_evaluation_input(task_state=blocked_state)
        _apply_governance_decision(
            blocked_state,
            governance_decision=decision,
            policy_input=blocked_policy_input,
        )
        self.assertEqual(blocked_state.current_state, RuntimeState.REPLANNING)

    # After fix 5, replay-identical checkpoints do not self-admit a new replan.
    def test_fix10_observable_change_requires_new_execution_events(self) -> None:
        state = _build_test_task_state()
        state.progress_epoch = 2
        state.plan_steps = [
            state.plan_steps[0],
            type(state.plan_steps[0])(
                step_id="step_2",
                objective="Replacement step.",
                rationale="Replan changed trajectory.",
                expected_outcome="New strategy.",
                status=PlanStepStatus.PENDING,
            ),
        ]
        state.replan_checkpoints.append(
            ReplanCheckpoint(
                actor_turn_count=state.actor_turn_count,
                tool_result_count=0,
                observation_count=0,
                completed_step_count=0,
                human_review_count=0,
                plan_signature="different-signature",
                request_reason="initial replan",
                progress_epoch=0,
            )
        )
        policy_input = build_policy_evaluation_input(task_state=state)

        signal = build_trajectory_signal(
            task_state=state,
            policy_input=policy_input,
            thresholds_version="test",
        )
        # No new tool results, observations, completed steps, or human reviews since
        # the checkpoint — even though progress_epoch and plan signature changed.
        self.assertFalse(signal["raw_facts"]["observable_change_since_last_replan"])

        # A new tool result is an observable execution event.
        state.tool_results_history.append(
            ToolExecutionResult(
                request=ToolRequest(
                    plan_step_id="step_1",
                    tool_name=ToolName.WEB_SEARCH,
                    reason="Collect fresh evidence.",
                    query="new evidence",
                ),
                success=True,
                summary="New evidence collected after replan.",
            )
        )
        signal2 = build_trajectory_signal(
            task_state=state,
            policy_input=policy_input,
            thresholds_version="test",
        )
        self.assertTrue(signal2["raw_facts"]["observable_change_since_last_replan"])


if __name__ == "__main__":
    unittest.main()
