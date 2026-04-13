from __future__ import annotations

import sys
import unittest
from pathlib import Path

from langgraph.types import Command


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
from layer2_runtime_governance import RuntimeState, build_runtime_graph


class Layer2RuntimeGraphTests(unittest.TestCase):
    def test_read_path_completes_without_human_interrupt(self) -> None:
        graph = build_runtime_graph()
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-read-path"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertNotIn("__interrupt__", result)
        self.assertEqual(task_state.final_output["last_governance_decision"], "continue")
        self.assertEqual(task_state.final_output["evidence_count"], 1)

    def test_write_path_interrupts_and_completes_after_resume(self) -> None:
        graph = build_runtime_graph()
        initial_result = graph.invoke(
            {"intake_result": _build_external_communication_intake_result()},
            config={"configurable": {"thread_id": "layer2-write-path"}},
        )

        self.assertIn("__interrupt__", initial_result)
        self.assertEqual(
            initial_result["task_state"].current_state,
            RuntimeState.WAITING_HUMAN_REVIEW,
        )

        resumed_result = graph.invoke(
            Command(
                resume={
                    "decision": "approve",
                    "comment": "Approved inside automated test.",
                }
            ),
            config={"configurable": {"thread_id": "layer2-write-path"}},
        )

        task_state = resumed_result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(
            task_state.final_output["last_governance_decision"],
            "wait_human_review",
        )
        self.assertEqual(task_state.final_output["evidence_count"], 1)


def _build_research_intake_result() -> IntakeResult:
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


def _build_external_communication_intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Draft and send a delivery confirmation email to the customer.",
                context="The communication is customer-facing and externally visible.",
                constraints=["Use a professional tone."],
                domain_type=DomainType.EXTERNAL_COMMUNICATION,
                domain_label="customer_delivery_email",
                sensitivity=SensitivityLevel.HIGH,
                success_criteria=["Produce the customer message."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.8,
                budget=BudgetConfig(max_steps=4, max_tool_calls=6),
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="stub_parser",
            prompt_version="test_v1",
            schema_version="1.0.0",
        ),
    )


if __name__ == "__main__":
    unittest.main()
