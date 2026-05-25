from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# DomainType keeps the layer 1 taxonomy stable for policy resolution and tracing.
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

# SensitivityLevel captures the coarse risk class used by downstream controls.
class SensitivityLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

# ParseStatus records how complete the intake output is before runtime starts.
class ParseStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NEEDS_CLARIFICATION = "needs_clarification"
    FAILED = "failed"

# Keep domain validation failures separate from generic Python exceptions.
class IntakeDomainError(Exception):
    """Base exception for layer 1 domain errors."""


class InvalidTaskSpecError(IntakeDomainError):
    """Raised when the parsed task spec is invalid or inconsistent."""


class InvalidExecutionPolicyError(IntakeDomainError):
    """Raised when the execution policy is invalid."""


class IntakeValidationError(IntakeDomainError):
    """Raised when the final intake result cannot be validated."""

# BudgetConfig defines the execution envelope chosen by policy, not by the user task.
class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional upper bound for semantic steps.",
    )
    max_tool_calls: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional upper bound for tool calls.",
    )

# ExecutionPolicy is the canonical policy object consumed by later layers.
class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_profile_name: str = Field(
        default="default",
        min_length=1,
        description="Name of the policy profile applied to the task.",
    )
    confidence_threshold: float = Field(..., ge=0.0, le=1.0)
    budget: BudgetConfig
    read_only_mode: bool = Field(
        default=False,
        description="If true, only non-mutating actions should be allowed downstream.",
    )

    @field_validator("policy_profile_name", mode="before")
    @classmethod
    def normalize_policy_profile_name(cls, value: object) -> str:
        # Normalize missing or noisy profile names into one canonical value.
        if value is None:
            return "default"
        if not isinstance(value, str):
            raise TypeError("policy_profile_name must be a string.")
        cleaned = " ".join(value.strip().split())
        return cleaned or "default"

# TaskSpec is the semantic core extracted from the raw request.
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
        # Normalize free text early to keep the canonical payload stable.
        if value is None:
            return ""
        if not isinstance(value, str):
            raise TypeError("Text fields must be strings.")
        return " ".join(value.strip().split())

    @field_validator("constraints", "success_criteria", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: object) -> list[str]:
        # Remove duplicates and empty items so these lists behave as stable contracts.
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("Expected a list of strings.")

        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise TypeError("Each item must be a string.")
            cleaned = " ".join(item.strip().split())
            if cleaned and cleaned not in seen:
                normalized.append(cleaned)
                seen.add(cleaned)

        return normalized

    @model_validator(mode="after")
    def validate_task_spec(self) -> "TaskSpec":
        # Keep the semantic core of the task always usable by downstream layers.
        if not self.goal:
            raise ValueError("TaskSpec.goal cannot be empty.")

        if not self.domain_label:
            self.domain_label = self.domain_type.value

        return self

# StructuredTask combines semantic intent with system-controlled execution limits.
class StructuredTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_spec: TaskSpec
    execution_policy: ExecutionPolicy

# IntakeMetadata carries tracing data and parser-level quality signals.
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
        # Metadata identifiers must be present and normalized because they drive tracing.
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
        # Empty fingerprints are treated as missing, not as meaningful identifiers.
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("raw_input_fingerprint must be a string.")
        cleaned = " ".join(value.strip().split())
        return cleaned or None

    @field_validator("warnings", "missing_information", mode="before")
    @classmethod
    def normalize_metadata_lists(cls, value: object) -> list[str]:
        # Warnings and missing fields should be deterministic lists with no duplicates.
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("Expected a list of strings.")

        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise TypeError("Each item must be a string.")
            cleaned = " ".join(item.strip().split())
            if cleaned and cleaned not in seen:
                normalized.append(cleaned)
                seen.add(cleaned)

        return normalized

    @model_validator(mode="after")
    def validate_parse_status_consistency(self) -> "IntakeMetadata":
        # A fully complete parse cannot still report missing information.
        if self.parse_status == ParseStatus.COMPLETE and self.missing_information:
            raise ValueError(
                "A COMPLETE parse_status cannot include missing_information."
            )
        return self

# IntakeResult is the canonical output contract of Layer 1.
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
