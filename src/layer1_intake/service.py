from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import (
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

# PolicyProfile is the repository-facing shape before conversion to ExecutionPolicy.
@dataclass(frozen=True)
class PolicyProfile:
    name: str
    confidence_threshold: float
    max_steps: int
    max_tool_calls: int | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
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
        domain_type: DomainType,
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

# InputNormalizer keeps command cleanup in one place before parsing starts.
class InputNormalizer:
    # This helper centralizes command normalization before parsing and policy resolution.
    def normalize(self, command: IntakeCommand) -> IntakeCommand:
        # Return a normalized copy so the original command remains untouched.
        return command.model_copy(
            update={
                "raw_task_text": self._normalize_optional_text(command.raw_task_text) or "",
                "context_hint": self._normalize_optional_text(command.context_hint),
                "policy_profile": self._normalize_optional_text(command.policy_profile),
            }
        )

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        # Collapse whitespace and convert empty strings into null-like values.
        if value is None:
            return None
        cleaned = " ".join(value.strip().split())
        return cleaned or None


@dataclass(frozen=True)
class TaskSpecBuildResult:
    task_spec: TaskSpec
    parse_status: ParseStatus
    warnings: list[str]
    missing_information: list[str]

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
        # Parse the request and derive warnings plus missing information in one place.
        task_spec = self._parser.parse(
            raw_task_text=command.raw_task_text,
            context_hint=command.context_hint,
        )

        warnings: list[str] = []
        missing_information: list[str] = []

        if not task_spec.context:
            warnings.append("Task context is empty.")

        if not task_spec.success_criteria:
            warnings.append("No explicit success criteria were extracted.")
            missing_information.append("success_criteria")

        if task_spec.domain_type == DomainType.OTHER:
            warnings.append("Domain taxonomy fallback OTHER was used.")

        if (
            task_spec.sensitivity in {SensitivityLevel.HIGH, SensitivityLevel.CRITICAL}
            and not task_spec.constraints
        ):
            warnings.append("High-sensitivity task without explicit constraints.")
            missing_information.append("constraints")

        return TaskSpecBuildResult(
            task_spec=task_spec,
            parse_status=self._resolve_parse_status(
                task_spec=task_spec,
                missing_information=missing_information,
            ),
            warnings=sorted(set(warnings)),
            missing_information=sorted(set(missing_information)),
        )

    @staticmethod
    def _resolve_parse_status(
        *,
        task_spec: TaskSpec,
        missing_information: list[str],
    ) -> ParseStatus:
        # Sensitive or broadly incomplete tasks are escalated above a simple PARTIAL state.
        if not missing_information:
            return ParseStatus.COMPLETE

        if task_spec.sensitivity == SensitivityLevel.CRITICAL:
            return ParseStatus.NEEDS_CLARIFICATION

        if (
            "constraints" in missing_information
            and task_spec.sensitivity == SensitivityLevel.HIGH
        ):
            return ParseStatus.NEEDS_CLARIFICATION

        if len(missing_information) >= 2:
            return ParseStatus.NEEDS_CLARIFICATION

        return ParseStatus.PARTIAL


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
            domain_type=task_spec.domain_type,
            sensitivity=task_spec.sensitivity,
        )

        return ExecutionPolicy(
            policy_profile_name=profile.name,
            confidence_threshold=profile.confidence_threshold,
            budget=BudgetConfig(
                max_steps=profile.max_steps,
                max_tool_calls=profile.max_tool_calls,
            ),
            allowed_tools=profile.allowed_tools,
            disallowed_tools=profile.disallowed_tools,
            read_only_mode=profile.read_only_mode,
        )

# IntakeResultValidator reconciles parser metadata with the final structured payload.
class IntakeResultValidator:
    # This validator enforces coherence after task parsing and policy attachment.
    def validate(
        self,
        *,
        structured_task: StructuredTask,
        metadata: IntakeMetadata,
    ) -> IntakeResult:
        # Normalize derived warnings and parse status one last time before returning the payload.
        warnings = list(metadata.warnings)
        missing_information = list(metadata.missing_information)
        parse_status = metadata.parse_status

        if (
            structured_task.task_spec.domain_type == DomainType.OTHER
            and "Domain taxonomy fallback OTHER was used." not in warnings
        ):
            warnings.append("Domain taxonomy fallback OTHER was used.")

        if (
            not structured_task.task_spec.success_criteria
            and "success_criteria" not in missing_information
        ):
            missing_information.append("success_criteria")

        if parse_status == ParseStatus.COMPLETE and missing_information:
            parse_status = ParseStatus.PARTIAL

        normalized_metadata = metadata.model_copy(
            update={
                "warnings": sorted(set(warnings)),
                "missing_information": sorted(set(missing_information)),
                "parse_status": parse_status,
            }
        )

        return IntakeResult(
            structured_task=structured_task,
            metadata=normalized_metadata,
        )

class IntakeService:
    # This service is the orchestration entrypoint of Layer 1.
    def __init__(
        self,
        *,
        normalizer: InputNormalizer,
        task_spec_builder: TaskSpecBuilder,
        policy_resolver: PolicyResolver,
        validator: IntakeResultValidator,
        clock: ClockPort,
        fingerprint_service: InputFingerprintPort,
        schema_version: str = "1.0.0",
    ) -> None:
        self._normalizer = normalizer
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
        normalized_command = self._normalizer.normalize(command)
        build_result = self._task_spec_builder.build(normalized_command)
        execution_policy = self._policy_resolver.resolve(
            task_spec=build_result.task_spec,
            profile_name=normalized_command.policy_profile,
        )

        structured_task = StructuredTask(
            task_spec=build_result.task_spec,
            execution_policy=execution_policy,
        )

        metadata = IntakeMetadata(
            raw_input_fingerprint=self._fingerprint_service.fingerprint(
                normalized_command.raw_task_text
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
    "InputNormalizer",
    "IntakeCommand",
    "IntakeResultValidator",
    "IntakeService",
    "PolicyProfile",
    "PolicyResolver",
    "TaskSpecBuildResult",
    "TaskSpecBuilder",
    "TaskSpecParserPort",
]
