from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from layer1_intake import (
    DomainType,
    IntakeService,
    JsonExecutionPolicyRepository,
    ParseStatus,
    SensitivityLevel,
    TaskSpec,
    get_default_policy_config_path,
)
from layer1_intake.service import (
    InputNormalizer,
    IntakeResultValidator,
    PolicyResolver,
    TaskSpecBuilder,
)
from layer2_runtime_governance import RuntimeFlag, RuntimeState
from main_layer2 import run_layer2_flow


class StubParser:
    # This stub injects a deterministic TaskSpec into the full Layer 1 -> Layer 2 path.
    parser_name = "stub_parser"
    prompt_version = "integration_v1"

    def __init__(self, task_spec: TaskSpec) -> None:
        # Store the parser output chosen by the integration test.
        self._task_spec = task_spec

    def parse(self, *, raw_task_text: str, context_hint: str | None = None) -> TaskSpec:
        # Return the canned TaskSpec so integration assertions stay stable.
        return self._task_spec


class FixedClock:
    # This clock freezes timestamps for deterministic integration assertions.
    def now(self) -> datetime:
        return datetime(2026, 4, 14, 9, 0, 0, tzinfo=timezone.utc)


class FixedFingerprintService:
    # This fingerprint service makes raw input assertions easier to read.
    def fingerprint(self, raw_text: str) -> str:
        return f"fp::{raw_text}"


class Layer1Layer2IntegrationTests(unittest.TestCase):
    # These tests validate the full handoff from IntakeService into the Layer 2 bootstrap graph.
    def test_research_task_bootstraps_end_to_end(self) -> None:
        # A complete research task should survive the full intake-to-bootstrap path unchanged.
        trace = run_layer2_flow(
            raw_task_text="  Find official sources about the DMA and summarize them.  ",
            context_hint="  Prefer official EU sources.  ",
            thread_id="layer1-layer2-bootstrap-research",
            intake_service=_build_intake_service(
                TaskSpec(
                    goal="Find official sources about the DMA and summarize them.",
                    context="Prefer official EU sources.",
                    constraints=["Prefer official EU sources."],
                    domain_type=DomainType.RESEARCH,
                    domain_label="dma_research",
                    sensitivity=SensitivityLevel.LOW,
                    success_criteria=["Produce a grounded short summary."],
                )
            ),
        )

        intake_result = trace["intake_result"]
        task_state = trace["final_result"]["task_state"]

        self.assertEqual(intake_result.metadata.parse_status, ParseStatus.COMPLETE)
        self.assertEqual(
            intake_result.metadata.raw_input_fingerprint,
            "fp::Find official sources about the DMA and summarize them.",
        )
        self.assertEqual(task_state.current_state, RuntimeState.PLANNING)
        self.assertTrue(task_state.bootstrap_completed)
        self.assertEqual(task_state.task_spec.goal, intake_result.structured_task.task_spec.goal)
        self.assertEqual(task_state.execution_policy, intake_result.structured_task.execution_policy)
        self.assertEqual(task_state.intake_metadata, intake_result.metadata)

    def test_clarification_task_bootstraps_end_to_end(self) -> None:
        # A clarification-required task should surface the correct initial runtime state and flags.
        trace = run_layer2_flow(
            raw_task_text="Review this contract and tell me whether I should sign it.",
            context_hint="No additional context.",
            thread_id="layer1-layer2-bootstrap-clarification",
            intake_service=_build_intake_service(
                TaskSpec(
                    goal="Review this contract and tell me whether I should sign it.",
                    context="Contract review request.",
                    constraints=[],
                    domain_type=DomainType.ANALYSIS,
                    domain_label="contract_review",
                    sensitivity=SensitivityLevel.HIGH,
                    success_criteria=[],
                )
            ),
        )

        intake_result = trace["intake_result"]
        task_state = trace["final_result"]["task_state"]

        self.assertEqual(intake_result.metadata.parse_status, ParseStatus.NEEDS_CLARIFICATION)
        self.assertEqual(task_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertTrue(task_state.bootstrap_completed)
        self.assertIn(RuntimeFlag.CLARIFICATION_REQUIRED, task_state.runtime_flags)
        self.assertIn(RuntimeFlag.HIGH_SENSITIVITY, task_state.runtime_flags)
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            ["bootstrap_from_intake", "finalize_bootstrap"],
        )


def _build_intake_service(task_spec: TaskSpec) -> IntakeService:
    # Build a deterministic IntakeService so the integration tests avoid real OpenAI calls.
    return IntakeService(
        normalizer=InputNormalizer(),
        task_spec_builder=TaskSpecBuilder(parser=StubParser(task_spec)),
        policy_resolver=PolicyResolver(
            repository=JsonExecutionPolicyRepository(
                config_path=get_default_policy_config_path()
            )
        ),
        validator=IntakeResultValidator(),
        clock=FixedClock(),
        fingerprint_service=FixedFingerprintService(),
    )


if __name__ == "__main__":
    unittest.main()
