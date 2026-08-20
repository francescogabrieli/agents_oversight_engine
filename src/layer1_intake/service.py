from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import (
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    IntakeValidationError,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskCompletenessAssessment,
    TaskSpec,
)
from .completeness import assess_task_completeness

# PolicyProfile is the repository-facing shape before conversion to ExecutionPolicy.
@dataclass(frozen=True)
class PolicyProfile:
    name: str
    confidence_threshold: float
    read_only_mode: bool = False


class TaskSpecParserPort(Protocol):
    # This port isolates the intake service from the concrete parsing implementation.
    @property
    def parser_name(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    def parse(self, *, raw_task_text: str, context_hint: str | None = None) -> TaskSpec: ...


class ExecutionPolicyRepositoryPort(Protocol):
    # This port isolates policy lookup from storage and configuration format details.
    def get_policy_profile(
        self,
        *,
        profile_name: str | None,
        sensitivity: SensitivityLevel,
    ) -> PolicyProfile: ...


class ClockPort(Protocol):
    # This port makes timestamps deterministic in tests.
    def now(self) -> datetime: ...


class InputFingerprintPort(Protocol):
    # This port keeps fingerprint generation replaceable and testable.
    def fingerprint(self, raw_text: str) -> str: ...

# IntakeCommand is the external input contract for the Layer 1 service.
class IntakeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_task_text: str = Field(..., min_length=1)
    context_hint: str | None = None
    policy_profile: str | None = None

    @field_validator("raw_task_text", mode="before")
    @classmethod
    def validate_raw_task_text(cls, value: object) -> str:
        # The intake flow starts from raw_task_text, so it must always be non-empty.
        if not isinstance(value, str):
            raise TypeError("raw_task_text must be a string.")
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("raw_task_text cannot be empty.")
        return cleaned

    @field_validator("context_hint", "policy_profile", mode="before")
    @classmethod
    def normalize_optional_text_fields(cls, value: object) -> str | None:
        # Optional textual inputs are normalized so service logic can stay simple.
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("Optional text fields must be strings.")
        cleaned = " ".join(value.strip().split())
        return cleaned or None

@dataclass(frozen=True)
class TaskSpecBuildResult:
    task_spec: TaskSpec
    assessment: TaskCompletenessAssessment

    @property
    def parse_status(self) -> ParseStatus:
        # Preserve the established build-result API while storing one assessment contract.
        return self.assessment.parse_status

    @property
    def warnings(self) -> list[str]:
        # Return the canonical assessment warnings without recomputing completeness.
        return self.assessment.warnings

    @property
    def missing_information(self) -> list[str]:
        # Return the canonical missing-information list without recomputing completeness.
        return self.assessment.missing_information

# TaskSpecBuilder enriches parser output with warnings and completeness signals.
class TaskSpecBuilder:
    # This component wraps the parser and turns parser output into a richer build result.
    def __init__(self, parser: TaskSpecParserPort) -> None:
        self._parser = parser

    @property
    def parser_name(self) -> str:
        # Expose parser metadata so it can be copied into IntakeMetadata.
        return self._parser.parser_name

    @property
    def prompt_version(self) -> str:
        # Expose parser prompt version for traceability.
        return self._parser.prompt_version

    def build(self, command: IntakeCommand) -> TaskSpecBuildResult:
        # Parse the request and delegate all completeness rules to the pure assessor.
        task_spec = self._parser.parse(
            raw_task_text=command.raw_task_text,
            context_hint=command.context_hint,
        )
        return TaskSpecBuildResult(
            task_spec=task_spec,
            assessment=assess_task_completeness(task_spec),
        )


class PolicyResolver:
    # This component converts repository policy data into the canonical ExecutionPolicy model.
    def __init__(self, repository: ExecutionPolicyRepositoryPort) -> None:
        self._repository = repository

    def resolve(
        self,
        *,
        task_spec: TaskSpec,
        profile_name: str | None = None,
    ) -> ExecutionPolicy:
        # Policy selection is system-controlled and based on task attributes plus optional profile.
        profile = self._repository.get_policy_profile(
            profile_name=profile_name,
            sensitivity=task_spec.sensitivity,
        )

        return ExecutionPolicy(
            policy_profile_name=profile.name,
            confidence_threshold=profile.confidence_threshold,
            read_only_mode=profile.read_only_mode,
        )

# IntakeResultValidator verifies the final canonical contract without repairing inputs.
class IntakeResultValidator:
    # This validator enforces coherence after task parsing and policy attachment.
    def validate(
        self,
        *,
        structured_task: StructuredTask,
        metadata: IntakeMetadata,
    ) -> IntakeResult:
        # Reuse the canonical assessor so validation never recreates completeness rules.
        expected = assess_task_completeness(structured_task.task_spec)
        inconsistent_fields: list[str] = []
        if metadata.parse_status != expected.parse_status:
            inconsistent_fields.append("parse_status")
        if metadata.missing_information != expected.missing_information:
            inconsistent_fields.append("missing_information")
        if metadata.warnings != expected.warnings:
            inconsistent_fields.append("warnings")

        # Reject inconsistent caller-provided metadata without silently repairing it.
        if inconsistent_fields:
            fields = ", ".join(inconsistent_fields)
            raise IntakeValidationError(
                f"Intake metadata is inconsistent with TaskSpec completeness: {fields}."
            )

        return IntakeResult(structured_task=structured_task, metadata=metadata)

class IntakeService:
    # This service is the orchestration entrypoint of Layer 1.
    def __init__(
        self,
        *,
        task_spec_builder: TaskSpecBuilder,
        policy_resolver: PolicyResolver,
        validator: IntakeResultValidator,
        clock: ClockPort,
        fingerprint_service: InputFingerprintPort,
        schema_version: str = "1.0.0",
    ) -> None:
        self._task_spec_builder = task_spec_builder
        self._policy_resolver = policy_resolver
        self._validator = validator
        self._clock = clock
        self._fingerprint_service = fingerprint_service
        self._schema_version = schema_version

    @property
    def schema_version(self) -> str:
        # Expose schema version so callers can reuse it when building traces.
        return self._schema_version

    def execute(self, command: IntakeCommand) -> IntakeResult:
        # Run the full Layer 1 flow and return one canonical IntakeResult.
        build_result = self._task_spec_builder.build(command)
        execution_policy = self._policy_resolver.resolve(
            task_spec=build_result.task_spec,
            profile_name=command.policy_profile,
        )

        structured_task = StructuredTask(
            task_spec=build_result.task_spec,
            execution_policy=execution_policy,
        )

        metadata = IntakeMetadata(
            raw_input_fingerprint=self._fingerprint_service.fingerprint(
                command.raw_task_text
            ),
            parse_status=build_result.parse_status,
            parser_name=self._task_spec_builder.parser_name,
            prompt_version=self._task_spec_builder.prompt_version,
            schema_version=self._schema_version,
            warnings=build_result.warnings,
            missing_information=build_result.missing_information,
            created_at=self._clock.now(),
        )

        return self._validator.validate(
            structured_task=structured_task,
            metadata=metadata,
        )


__all__ = [
    "ClockPort",
    "ExecutionPolicyRepositoryPort",
    "InputFingerprintPort",
    "IntakeCommand",
    "IntakeResultValidator",
    "IntakeService",
    "PolicyProfile",
    "PolicyResolver",
    "TaskSpecBuildResult",
    "TaskCompletenessAssessment",
    "TaskSpecBuilder",
    "assess_task_completeness",
    "TaskSpecParserPort",
]
