from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Domain models for layer 1 intake, including task specifications, execution policies, and intake results.
class DomainType(str, Enum):
    RESEARCH = "research"
    ANALYSIS = "analysis"
    SUMMARIZATION = "summarization"
    DATA_EXTRACTION = "data_extraction"
    CONTENT_GENERATION = "content_generation"
    EXTERNAL_COMMUNICATION = "external_communication"
    CUSTOMER_SUPPORT = "customer_support"
    PRODUCTIVITY = "productivity"
    SOFTWARE_ENGINEERING = "software_engineering"
    OTHER = "other"

# Sensitivity levels for tasks, which can inform downstream handling and execution policies.
class SensitivityLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

# Parse status for the intake process, indicating whether the task specification was fully extracted, partially extracted, needs clarification, or failed to parse. This can be used for monitoring and feedback loops.
class ParseStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NEEDS_CLARIFICATION = "needs_clarification"
    FAILED = "failed"

# Exceptions and domain models for task specifications, execution policies, and intake results. This separes python error from domain errors.
class IntakeDomainError(Exception):
    """Base exception for layer 1 domain errors."""


class InvalidTaskSpecError(IntakeDomainError):
    """Raised when the parsed task spec is invalid or inconsistent."""


class InvalidExecutionPolicyError(IntakeDomainError):
    """Raised when the execution policy is invalid."""


class IntakeValidationError(IntakeDomainError):
    """Raised when the final intake result cannot be validated."""

# Model for operative budget
class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(..., ge=1, description="Maximum number of steps allowed.")
    max_tool_calls: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional upper bound for tool calls.",
    )

# Allows to create coherent policy
class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_profile_name: str = Field(
        default="default",
        min_length=1,
        description="Name of the policy profile applied to the task.",
    )
    confidence_threshold: float = Field(..., ge=0.0, le=1.0)
    budget: BudgetConfig
    allowed_tools: list[str] = Field(default_factory=list)
    disallowed_tools: list[str] = Field(default_factory=list)
    read_only_mode: bool = Field(
        default=False,
        description="If true, only non-mutating actions should be allowed downstream.",
    )

    @field_validator("policy_profile_name", mode="before")
    @classmethod
    def normalize_policy_profile_name(cls, value: object) -> str:
        if value is None:
            return "default"
        if not isinstance(value, str):
            raise TypeError("policy_profile_name must be a string.")
        cleaned = " ".join(value.strip().split())
        return cleaned or "default"

    @field_validator("allowed_tools", "disallowed_tools", mode="before")
    @classmethod
    def normalize_tool_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("Tool lists must be lists of strings.")

        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError("Each tool name must be a string.")
            cleaned = item.strip()
            if cleaned:
                normalized.append(cleaned)

        return sorted(set(normalized))

    @model_validator(mode="after")
    def validate_tool_overlap(self) -> "ExecutionPolicy":
        overlap = set(self.allowed_tools) & set(self.disallowed_tools)
        if overlap:
            raise ValueError(
                f"Tools cannot be both allowed and disallowed: {sorted(overlap)}"
            )
        return self

# Task's semantic heart
class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(..., min_length=1)
    context: str = Field(default="")
    constraints: list[str] = Field(default_factory=list)
    domain_type: DomainType = Field(default=DomainType.OTHER)
    domain_label: str = Field(
        default="other",
        min_length=1,
        description="Free-form domain label inferred from the task.",
    )
    sensitivity: SensitivityLevel = Field(default=SensitivityLevel.MEDIUM)
    success_criteria: list[str] = Field(default_factory=list)

    @field_validator("goal", "context", "domain_label", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: object) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise TypeError("Text fields must be strings.")
        return " ".join(value.strip().split())

    @field_validator("constraints", "success_criteria", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("Expected a list of strings.")

        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError("Each item must be a string.")
            cleaned = " ".join(item.strip().split())
            if cleaned:
                normalized.append(cleaned)

        return normalized

    @model_validator(mode="after")
    def validate_task_spec(self) -> "TaskSpec":
        if not self.goal:
            raise ValueError("TaskSpec.goal cannot be empty.")

        if not self.domain_label:
            self.domain_label = self.domain_type.value

        return self

# Unifies task specification with execution policy to obtain ready task
class StructuredTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_spec: TaskSpec
    execution_policy: ExecutionPolicy

# Intake metadata
class IntakeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(
        default_factory=lambda: str(uuid4()),
        min_length=1,
        description="Unique identifier for the intake request.",
    )
    raw_input_fingerprint: Optional[str] = Field(
        default=None,
        description="Stable fingerprint of the original raw input, if available.",
    )
    parse_status: ParseStatus = ParseStatus.COMPLETE
    parser_name: str = "llm_task_parser"
    prompt_version: str = "v1"
    schema_version: str = "1.0.0"
    warnings: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(
        "request_id",
        "parser_name",
        "prompt_version",
        "schema_version",
        mode="before",
    )
    @classmethod
    def normalize_metadata_text_fields(cls, value: object) -> str:
        if value is None:
            raise TypeError("Metadata text fields cannot be null.")
        if not isinstance(value, str):
            raise TypeError("Metadata text fields must be strings.")
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("Metadata text fields cannot be empty.")
        return cleaned

    @field_validator("raw_input_fingerprint", mode="before")
    @classmethod
    def normalize_optional_fingerprint(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("raw_input_fingerprint must be a string.")
        cleaned = " ".join(value.strip().split())
        return cleaned or None

    @field_validator("warnings", "missing_information", mode="before")
    @classmethod
    def normalize_metadata_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("Expected a list of strings.")

        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError("Each item must be a string.")
            cleaned = " ".join(item.strip().split())
            if cleaned:
                normalized.append(cleaned)

        return sorted(set(normalized))

    @model_validator(mode="after")
    def validate_parse_status_consistency(self) -> "IntakeMetadata":
        if self.parse_status == ParseStatus.COMPLETE and self.missing_information:
            raise ValueError(
                "A COMPLETE parse_status cannot include missing_information."
            )
        return self

# Final intake result combining structured task and metadata
class IntakeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    structured_task: StructuredTask
    metadata: IntakeMetadata


__all__ = [
    "BudgetConfig",
    "DomainType",
    "ExecutionPolicy",
    "IntakeDomainError",
    "IntakeMetadata",
    "IntakeResult",
    "IntakeValidationError",
    "InvalidExecutionPolicyError",
    "InvalidTaskSpecError",
    "ParseStatus",
    "SensitivityLevel",
    "StructuredTask",
    "TaskSpec",
]
