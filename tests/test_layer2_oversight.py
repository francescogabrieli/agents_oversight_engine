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
from layer2_runtime_governance.signal_services import (
    build_evidence_signal,
    build_risk_signal,
    build_trajectory_signal,
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


if __name__ == "__main__":
    unittest.main()
