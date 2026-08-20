from __future__ import annotations

import unittest
from uuid import uuid4

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
from layer2_runtime_governance.actor_contract import (
    ActorResult,
    ActorResultType,
)
from layer2_runtime_governance.domain import (
    EvidenceBurden,
    ExternalCommunicationDraft,
    PlanStep,
    PlanStepStatus,
    StepValidation,
    TaskState,
    normalize_task_state_serialization,
)
from layer2_runtime_governance.governance_components import build_policy_evaluation_input
from layer2_runtime_governance.signal_services.evidence_service import build_evidence_signal
from layer2_runtime_governance.signal_services.trajectory_service import build_trajectory_signal
from layer3_tool_bus_external_capabilities.external_operations import (
    normalize_communication_operation,
)
from layer3_tool_bus_external_capabilities.tool_contract import (
    ToolErrorCode,
    ToolExecutionResult,
    ToolName,
    ToolObservation,
    ToolRequest,
)


def _build_test_task_state(*, produces_factual_claims: bool = False, evidence_burden: EvidenceBurden | None = None) -> TaskState:
    intake = IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Test evidence and trajectory contracts v2",
                context="Unit test fixtures",
                constraints=["Deterministic execution"],
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Verify structural facts"],
                execution_intent=ExecutionIntent.PROPOSAL_ONLY,
            ),
            execution_policy=ExecutionPolicy(
                policy_profile_name="test_policy",
                confidence_threshold=0.7,
                read_only_mode=False,
            ),
        ),
        metadata=IntakeMetadata(
            request_id=f"req-{uuid4()}",
            parse_status=ParseStatus.COMPLETE,
            parser_name="test_parser",
            prompt_version="v1",
            schema_version="1.0.0",
        ),
    )
    state = TaskState.from_intake(intake)
    step = PlanStep(
        step_id="step_1",
        objective="Collect official data",
        rationale="Step needs evidence",
        expected_outcome="Data collected",
        status=PlanStepStatus.IN_PROGRESS,
        produces_factual_claims=produces_factual_claims,
        evidence_burden=evidence_burden,
    )
    state.plan_steps.append(step)
    state.current_plan_step_index = 0
    return state


class EvidenceTrajectoryV2ContractsTests(unittest.TestCase):
    def test_evidence_burden_fail_open_when_not_declared(self) -> None:
        state = _build_test_task_state(produces_factual_claims=False, evidence_burden=None)
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Completed without explicit factual burden",
            output_text="The answer is general.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=(),
                rationale_summary="General knowledge used.",
            ),
        )
        signal = build_evidence_signal(task_state=state, thresholds_version="v1")
        facts = signal["raw_facts"]

        self.assertFalse(facts["coverage_evaluated"])
        self.assertTrue(facts["independent_sources_satisfied"])
        self.assertTrue(facts["retrieved_content_satisfied"])
        self.assertTrue(facts["burden_satisfied"])
        self.assertNotIn("insufficient_independent_sources", facts["deficit_codes"])
        self.assertNotIn("retrieved_content_missing", facts["deficit_codes"])
        self.assertEqual(signal["mode"], "active")
        self.assertTrue(signal["decision_influence"])

    def test_evidence_burden_fail_closed_on_insufficient_sources(self) -> None:
        burden = EvidenceBurden(min_independent_sources=2, require_retrieved_content=True)
        state = _build_test_task_state(produces_factual_claims=True, evidence_burden=burden)

        obs1 = ToolObservation(observation_id="obs-1", content="Fact from domain A", locator="https://source-a.org/data")
        state.observations.append(obs1)

        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Claim made with only one source",
            output_text="Revenue reached $100M in 2026.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-1",),
                rationale_summary="Grounded on source A.",
            ),
        )
        signal = build_evidence_signal(task_state=state, thresholds_version="v1")
        facts = signal["raw_facts"]

        self.assertTrue(facts["coverage_evaluated"])
        self.assertEqual(facts["min_independent_sources_required"], 2)
        self.assertEqual(facts["distinct_independent_sources_count"], 1)
        self.assertFalse(facts["independent_sources_satisfied"])
        self.assertFalse(facts["burden_satisfied"])
        self.assertIn("insufficient_independent_sources", facts["deficit_codes"])

    def test_literal_anchoring_identifies_unanchored_tokens(self) -> None:
        state = _build_test_task_state()
        obs1 = ToolObservation(observation_id="obs-1", content="Growth was 15% in 2025.", locator="https://source-a.org/data")
        state.observations.append(obs1)

        draft = ExternalCommunicationDraft(
            channel="email",
            recipient="partner@example.com",
            subject="Results",
            body="Growth was 15% and revenue reached $500M on 2026-05-10 at https://secret.org.",
        )
        state.external_operation_proposal = normalize_communication_operation(
            operation_id="op-1",
            plan_step_id="step_1",
            capability_id="communication.email.send",
            draft=draft,
            execution_candidate=True,
        )

        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Report prepared",
            output_text="Prepared email.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.8,
                evidence_refs=("obs-1",),
                rationale_summary="Report references obs-1.",
            ),
        )

        signal = build_evidence_signal(task_state=state, thresholds_version="v1")
        facts = signal["raw_facts"]

        self.assertTrue(facts["has_unanchored_literals_in_selection"])
        self.assertTrue(facts["has_unanchored_literals_in_universe"])
        self.assertIn("unanchored_literals_in_selection", facts["deficit_codes"])
        self.assertIn("unanchored_literals_in_universe", facts["deficit_codes"])
        # Check that extracted tokens include $500M or 2026-05-10 or https://secret.org
        unanchored_univ = facts["unanchored_literals_in_universe"]
        self.assertTrue(any("500" in tok or "secret.org" in tok or "2026" in tok for tok in unanchored_univ))

    def test_trajectory_convergence_rules_facts(self) -> None:
        state = _build_test_task_state()
        # Add 3 consecutive tool failures
        for i in range(3):
            tr = ToolExecutionResult(
                request=ToolRequest(plan_step_id="step_1", tool_name=ToolName.WEB_SEARCH, reason="Search", query=f"query {i}"),
                success=False,
                summary=f"Search failed for query {i}",
                error_message="Search API failed",
                error_code=ToolErrorCode.TIMEOUT,
                observations=(),
            )
            state.tool_results_history.append(tr)

        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.PROPOSE_ACTION,
            summary="Proposing another tool action",
        )

        policy_input = build_policy_evaluation_input(task_state=state)
        signal = build_trajectory_signal(task_state=state, policy_input=policy_input, thresholds_version="v1")
        facts = signal["raw_facts"]

        self.assertEqual(facts["consecutive_tool_failures"], 3)
        self.assertEqual(facts["replan_count"], 0)

    def test_task_state_serialization_roundtrip_preserves_new_fields(self) -> None:
        burden = EvidenceBurden(min_independent_sources=2, require_retrieved_content=True, freshness_window_days=7)
        state = _build_test_task_state(produces_factual_claims=True, evidence_burden=burden)
        state.evidence_request_count = 2

        normalized = normalize_task_state_serialization(state)
        self.assertEqual(normalized.evidence_request_count, 2)
        self.assertTrue(normalized.plan_steps[0].produces_factual_claims)
        self.assertEqual(normalized.plan_steps[0].evidence_burden.min_independent_sources, 2)


if __name__ == "__main__":
    unittest.main()
