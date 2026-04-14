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
from layer2_runtime_governance import RuntimeFlag, RuntimeState, build_runtime_graph


class Layer2RuntimeGraphTests(unittest.TestCase):
    # These tests verify the bootstrap-only Layer 2 graph behavior.
    def test_complete_intake_bootstraps_to_planning(self) -> None:
        # A complete intake should bootstrap into the planning state with no extra flags.
        graph = build_runtime_graph()
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-bootstrap-complete"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.PLANNING)
        self.assertTrue(task_state.bootstrap_completed)
        self.assertEqual(len(task_state.step_history), 2)
        self.assertEqual(task_state.step_history[0].node_name, "bootstrap_from_intake")
        self.assertEqual(task_state.step_history[1].node_name, "finalize_bootstrap")
        self.assertEqual(task_state.runtime_flags, [])

    def test_needs_clarification_bootstraps_to_waiting_human_review(self) -> None:
        # A clarification-required intake should bootstrap into waiting_human_review.
        graph = build_runtime_graph()
        result = graph.invoke(
            {"intake_result": _build_clarification_intake_result()},
            config={"configurable": {"thread_id": "layer2-bootstrap-clarification"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertTrue(task_state.bootstrap_completed)
        self.assertIn(RuntimeFlag.CLARIFICATION_REQUIRED, task_state.runtime_flags)
        self.assertIn(RuntimeFlag.HIGH_SENSITIVITY, task_state.runtime_flags)
        self.assertEqual(task_state.step_history[0].runtime_state, RuntimeState.WAITING_HUMAN_REVIEW)


def _build_research_intake_result() -> IntakeResult:
    # Build a canonical complete intake payload for bootstrap tests.
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Find official sources about the Digital Markets Act and summarize them.",
                context="Use official EU sources.",
                constraints=["Use official EU sources."],
                domain_type=DomainType.RESEARCH,
                domain_label="dma_research",
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Produce a grounded short summary."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                budget=BudgetConfig(max_steps=5, max_tool_calls=10),
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="stub_parser",
            prompt_version="test_v1",
            schema_version="1.0.0",
        ),
    )


def _build_clarification_intake_result() -> IntakeResult:
    # Build a canonical clarification-required intake payload for bootstrap tests.
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Review this contract and tell me whether I should sign it.",
                context="Contract review request.",
                constraints=[],
                domain_type=DomainType.ANALYSIS,
                domain_label="contract_review",
                sensitivity=SensitivityLevel.HIGH,
                success_criteria=[],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.8,
                budget=BudgetConfig(max_steps=4, max_tool_calls=6),
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.NEEDS_CLARIFICATION,
            parser_name="stub_parser",
            prompt_version="test_v1",
            schema_version="1.0.0",
            missing_information=["constraints", "success_criteria"],
        ),
    )


if __name__ == "__main__":
    unittest.main()
