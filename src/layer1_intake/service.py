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

# Policy data class for resolved execution policies, which can be used to inform the execution layer about constraints and allowances for task execution.
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
    @property
    def parser_name(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    def parse(self, *, raw_task_text: str, context_hint: str | None = None) -> TaskSpec: ...


class ExecutionPolicyRepositoryPort(Protocol):
    def get_policy_profile(
        self,
        *,
        profile_name: str | None,
        domain_type: DomainType,
        sensitivity: SensitivityLevel,
    ) -> PolicyProfile: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class InputFingerprintPort(Protocol):
    def fingerprint(self, raw_text: str) -> str: ...

# Input command for the intake service, representing the raw user input and optional hints for parsing and policy resolution.
class IntakeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_task_text: str = Field(..., min_length=1)
    context_hint: str | None = None
    policy_profile: str | None = None

    @field_validator("raw_task_text", mode="before")
    @classmethod
    def validate_raw_task_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("raw_task_text must be a string.")
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("raw_task_text cannot be empty.")
        return cleaned

# Normalizes IntakeCommand's fields
class InputNormalizer:
    def normalize(self, command: IntakeCommand) -> IntakeCommand:
        return command.model_copy(
            update={
                "raw_task_text": self._normalize_optional_text(command.raw_task_text) or "",
                "context_hint": self._normalize_optional_text(command.context_hint),
                "policy_profile": self._normalize_optional_text(command.policy_profile),
            }
        )

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
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

# Calls the parser and collect warnings or missing information
class TaskSpecBuilder:
    def __init__(self, parser: TaskSpecParserPort) -> None:
        self._parser = parser

    @property
    def parser_name(self) -> str:
        return self._parser.parser_name

    @property
    def prompt_version(self) -> str:
        return self._parser.prompt_version

    def build(self, command: IntakeCommand) -> TaskSpecBuildResult:
        task_spec = self._parser.parse(
            raw_task_text=command.raw_task_text,
            context_hint=command.context_hint,
        )

        warnings: list[str] = []
        missing_information: list[str] = []

        if not task_spec.context:
            warnings.append("Task context is empty.")
            missing_information.append("context")

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
        if not missing_information:
            return ParseStatus.COMPLETE

        if task_spec.sensitivity == SensitivityLevel.CRITICAL:
            return ParseStatus.NEEDS_CLARIFICATION

        if len(missing_information) >= 2:
            return ParseStatus.NEEDS_CLARIFICATION

        return ParseStatus.PARTIAL


class PolicyResolver:
    def __init__(self, repository: ExecutionPolicyRepositoryPort) -> None:
        self._repository = repository

    def resolve(
        self,
        *,
        task_spec: TaskSpec,
        profile_name: str | None = None,
    ) -> ExecutionPolicy:
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

# Checks for coherence between task and metadata
class IntakeResultValidator:
    def validate(
        self,
        *,
        structured_task: StructuredTask,
        metadata: IntakeMetadata,
    ) -> IntakeResult:
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

        if (
            not structured_task.task_spec.context
            and "context" not in missing_information
        ):
            missing_information.append("context")

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

# Intake service orchestrating the intake process, from normalization to parsing, policy resolution, and validation.
class IntakeService:
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
        return self._schema_version

    def execute(self, command: IntakeCommand) -> IntakeResult:
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
