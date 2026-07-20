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
    ActorResult,
    ActorResultType,
    CapabilityAccessMode,
    EvidenceSourceKind,
    EvidenceSupportLevel,
    PlanStep,
    PlanStepKind,
    PlanStepStatus,
    RuntimeState,
    StepValidation,
    StepValidationDecision,
    TaskState,
    ToolName,
    build_policy_evaluation_input,
)
from layer2_runtime_governance.signal_services import (
    build_evidence_signal,
    build_risk_signal,
    build_trajectory_signal,
)
from layer2_runtime_governance.tool_contract import ObservedEvidence


class Layer2OversightRawFactsTests(unittest.TestCase):
    # Verify Python exports evidence facts without computing a parallel score or directive.
    def test_evidence_signal_is_unscored_and_preserves_typed_support(self) -> None:
        state = _build_state()
        state.observed_evidence.append(
            ObservedEvidence(
                evidence_id="tool_evidence_1",
                plan_step_id="step_1",
                title="Authoritative source",
                locator="https://example.test/source",
                excerpt="A detailed fact collected by the tool.",
                source_kind=EvidenceSourceKind.TOOL_OBSERVATION,
                source_tool=ToolName.FETCH_URL_TEXT,
                support_level=EvidenceSupportLevel.DETAILED_EXTRACTED,
            )
        )

        signal = build_evidence_signal(task_state=state, thresholds_version="policy.v1")

        self.assertIsNone(signal["diagnostic_score"])
        self.assertEqual(signal["diagnostic_level"], "unscored")
        self.assertNotIn("recommended_directive", signal)
        self.assertEqual(signal["raw_facts"]["support_level_required"], "detailed_extracted")
        self.assertEqual(signal["raw_facts"]["support_level_available"], "detailed_extracted")

    # Verify operational risk is expressed as raw policy facts only.
    def test_risk_signal_does_not_load_weights_or_choose_directive(self) -> None:
        state = _build_state()
        policy_input = build_policy_evaluation_input(task_state=state)

        signal = build_risk_signal(policy_input=policy_input, thresholds_version="policy.v1")

        self.assertIsNone(signal["diagnostic_score"])
        self.assertEqual(signal["raw_facts"]["tool_risk_level"], "none")
        self.assertFalse(signal["raw_facts"]["write_capability"])
        self.assertNotIn("recommended_directive", signal)

    # Verify trajectory counters stay factual and leave threshold interpretation to OPA.
    def test_trajectory_signal_contains_counters_without_python_scoring(self) -> None:
        state = _build_state()
        state.actor_turn_count = 3
        policy_input = build_policy_evaluation_input(task_state=state)

        signal = build_trajectory_signal(
            task_state=state,
            policy_input=policy_input,
            thresholds_version="policy.v1",
        )

        self.assertEqual(signal["raw_facts"]["actor_turn_count_for_step"], 3)
        self.assertTrue(signal["raw_facts"]["completion_attempt"])
        self.assertIsNone(signal["diagnostic_score"])
        self.assertNotIn("recommended_directive", signal)


# Build a deterministic actor-ready state without invoking any provider.
def _build_state() -> TaskState:
    intake = IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Summarize the supplied material.",
                context="Use only grounded evidence.",
                constraints=["Do not invent facts."],
                domain_type=DomainType.SUMMARIZATION,
                domain_label="summary",
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Return a concise supported summary."],
            ),
            execution_policy=ExecutionPolicy(
                policy_profile_name="default",
                confidence_threshold=0.65,
                budget=BudgetConfig(max_steps=None, max_tool_calls=None),
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
            step_kind=PlanStepKind.DETAILED_EXTRACTION,
            evidence_requirement=EvidenceSupportLevel.DETAILED_EXTRACTED,
            claim_precision_ceiling=EvidenceSupportLevel.DETAILED_EXTRACTED,
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    state.current_plan_step_index = 0
    validation = StepValidation(
        plan_step_id="step_1",
        objective_satisfied=True,
        evidence_sufficient=True,
        confidence=0.9,
        next_action=StepValidationDecision.COMPLETE_STEP,
        rationale_summary="The evidence supports the summary.",
    )
    state.actor_result = ActorResult(
        plan_step_id="step_1",
        result_type=ActorResultType.FINAL_OUTPUT,
        summary="Prepared the grounded summary.",
        capability_mode=CapabilityAccessMode.NONE,
        output_text="Grounded summary.",
        step_validation=validation,
    )
    state.step_validations.append(validation)
    return state


if __name__ == "__main__":
    unittest.main()
