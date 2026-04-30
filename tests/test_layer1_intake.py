from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from layer1_intake.infrastructure import JsonExecutionPolicyRepository
from layer1_intake.models import (
    DomainType,
    ParseStatus,
    SensitivityLevel,
    TaskSpec,
)
from layer1_intake.service import (
    IntakeCommand,
    IntakeService,
    InputNormalizer,
    IntakeResultValidator,
    PolicyProfile,
    PolicyResolver,
    TaskSpecBuilder,
)


class StubParser:
    # This stub returns a pre-built TaskSpec so tests stay deterministic.
    parser_name = "stub_parser"
    prompt_version = "test_v1"

    def __init__(self, task_spec: TaskSpec) -> None:
        # Store the canned parser output used by the test.
        self._task_spec = task_spec

    def parse(self, *, raw_task_text: str, context_hint: str | None = None) -> TaskSpec:
        # Ignore the raw text and return the exact TaskSpec chosen by the test.
        return self._task_spec


class FixedClock:
    # This clock freezes metadata timestamps for predictable assertions.
    def now(self) -> datetime:
        return datetime(2026, 4, 13, 10, 30, 0, tzinfo=timezone.utc)


class FixedFingerprintService:
    # This fingerprint service produces stable, easy-to-read test values.
    def fingerprint(self, raw_text: str) -> str:
        return f"fp::{raw_text}"


class Layer1IntakeTests(unittest.TestCase):
    # These tests cover the core intake contracts and policy resolution rules.
    def test_intake_command_normalizes_raw_input(self) -> None:
        # Raw user input should be normalized before any parsing happens.
        command = IntakeCommand(
            raw_task_text="  find   official  sources   ",
            context_hint="  EU only   ",
            policy_profile=" default ",
        )

        self.assertEqual(command.raw_task_text, "find official sources")
        self.assertEqual(command.context_hint, "EU only")
        self.assertEqual(command.policy_profile, "default")

    def test_builder_marks_high_sensitivity_without_constraints_as_needs_clarification(self) -> None:
        # High-sensitivity tasks without constraints should escalate above PARTIAL.
        parser = StubParser(
            TaskSpec(
                goal="Review a customer contract and judge whether it can be signed.",
                context="Contract review request.",
                constraints=[],
                domain_type=DomainType.ANALYSIS,
                domain_label="contract_review",
                sensitivity=SensitivityLevel.HIGH,
                success_criteria=["Provide a decision."],
            )
        )

        builder = TaskSpecBuilder(parser=parser)
        result = builder.build(IntakeCommand(raw_task_text="Review this contract."))

        self.assertEqual(result.parse_status, ParseStatus.NEEDS_CLARIFICATION)
        self.assertIn("constraints", result.missing_information)

    def test_intake_service_returns_canonical_v3_aligned_payload(self) -> None:
        # The service should return one canonical IntakeResult with parser and policy metadata attached.
        parser = StubParser(
            TaskSpec(
                goal="Find official EU sources about the DMA and produce a short summary.",
                context="The user prefers official EU sources.",
                constraints=["Prefer official EU sources."],
                domain_type=DomainType.RESEARCH,
                domain_label="dma_official_source_research",
                sensitivity=SensitivityLevel.LOW,
                success_criteria=[
                    "List official sources.",
                    "Produce a short grounded summary.",
                ],
            )
        )

        service = IntakeService(
            normalizer=InputNormalizer(),
            task_spec_builder=TaskSpecBuilder(parser=parser),
            policy_resolver=PolicyResolver(repository=_StubPolicyRepository()),
            validator=IntakeResultValidator(),
            clock=FixedClock(),
            fingerprint_service=FixedFingerprintService(),
        )

        result = service.execute(
            IntakeCommand(
                raw_task_text="  find official EU sources on the DMA and summarize them ",
                context_hint="  official sources only ",
            )
        )

        self.assertEqual(result.structured_task.task_spec.domain_type, DomainType.RESEARCH)
        self.assertEqual(
            result.structured_task.execution_policy.policy_profile_name,
            "default",
        )
        self.assertEqual(result.metadata.parse_status, ParseStatus.COMPLETE)
        self.assertEqual(
            result.metadata.raw_input_fingerprint,
            "fp::find official EU sources on the DMA and summarize them",
        )
        self.assertEqual(result.metadata.parser_name, "stub_parser")
        self.assertEqual(result.metadata.prompt_version, "test_v1")

    def test_empty_context_remains_warning_and_not_partial(self) -> None:
        # Missing context alone is a warning, not enough to downgrade parse_status.
        parser = StubParser(
            TaskSpec(
                goal="Find official EU sources about the DMA and produce a short summary.",
                context="",
                constraints=["Prefer official EU sources."],
                domain_type=DomainType.RESEARCH,
                domain_label="dma_official_source_research",
                sensitivity=SensitivityLevel.LOW,
                success_criteria=[
                    "List official sources.",
                    "Produce a short grounded summary.",
                ],
            )
        )

        service = IntakeService(
            normalizer=InputNormalizer(),
            task_spec_builder=TaskSpecBuilder(parser=parser),
            policy_resolver=PolicyResolver(repository=_StubPolicyRepository()),
            validator=IntakeResultValidator(),
            clock=FixedClock(),
            fingerprint_service=FixedFingerprintService(),
        )

        result = service.execute(
            IntakeCommand(
                raw_task_text="find official EU sources on the DMA and summarize them",
            )
        )

        self.assertEqual(result.metadata.parse_status, ParseStatus.COMPLETE)
        self.assertIn("Task context is empty.", result.metadata.warnings)
        self.assertNotIn("context", result.metadata.missing_information)

    def test_policy_repository_merges_domain_and_sensitivity_overrides(self) -> None:
        # Domain and sensitivity overrides should compose into one resolved policy.
        config = {
            "default_policy": {
                "confidence_threshold": 0.65,
                "budget": {"max_steps": 5, "max_tool_calls": 10},
                "read_only_mode": False,
            },
            "named_profiles": {},
            "domain_overrides": {
                "external_communication": {
                    "read_only_mode": True,
                }
            },
            "sensitivity_overrides": {
                "high": {
                    "confidence_threshold": 0.8,
                    "budget": {"max_steps": 4, "max_tool_calls": 6},
                }
            },
            "domain_sensitivity_overrides": {
                "external_communication": {
                    "high": {
                        "confidence_threshold": 0.9,
                        "budget": {"max_steps": 2, "max_tool_calls": 3},
                    }
                }
            },
        }

        config_path = REPO_ROOT / "tests" / "_tmp_policy_config.json"
        try:
            config_path.write_text(json.dumps(config), encoding="utf-8")

            repository = JsonExecutionPolicyRepository(config_path=config_path)
            profile = repository.get_policy_profile(
                profile_name=None,
                domain_type=DomainType.EXTERNAL_COMMUNICATION,
                sensitivity=SensitivityLevel.HIGH,
            )
        finally:
            if config_path.exists():
                config_path.unlink()

        self.assertEqual(profile.name, "default")
        self.assertEqual(profile.confidence_threshold, 0.9)
        self.assertEqual(profile.max_steps, 2)
        self.assertEqual(profile.max_tool_calls, 3)
        self.assertTrue(profile.read_only_mode)


class _StubPolicyRepository:
    # This repository stub lets service tests focus on intake behavior rather than config files.
    def get_policy_profile(
        self,
        *,
        profile_name: str | None,
        domain_type: DomainType,
        sensitivity: SensitivityLevel,
    ) -> PolicyProfile:
        # Always return the same simple default-like profile for tests.
        return PolicyProfile(
            name=profile_name or "default",
            confidence_threshold=0.65,
            max_steps=5,
            max_tool_calls=10,
            read_only_mode=False,
        )


if __name__ == "__main__":
    unittest.main()
