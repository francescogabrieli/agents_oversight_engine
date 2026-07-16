from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from layer1_intake import (
    DomainType,
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    TaskSpec,
)

from ..tool_contract import (
    EvidenceSupportLevel,
    ObservedEvidence,
    ToolExecutionResult,
    ToolRequest,
)

if TYPE_CHECKING:
    from .actor_contract import ActorResult, ActorResultType
    from .behavioral import BehavioralAssessment
    from .judge import JudgeAssessment
    from .oversight import DynamicPolicyAssessment, OversightAssessment


# Layer 2 currently covers bootstrap, planning, and the actor/tool observation loop.
# This file keeps the canonical runtime state shared by the graph nodes.

# Canonical domain contract extracted from the legacy state facade.
class RuntimeState(str, Enum):
    PLANNING = "planning"
    ACTING = "acting"
    READY_NEXT_STEP = "ready_next_step"
    READY_TOOL_EXECUTION = "ready_tool_execution"
    REPLANNING = "replanning"
    WAITING_HUMAN_REVIEW = "waiting_human_review"
    COMPLETED = "completed"
    ABORTED = "aborted"


# Canonical domain contract extracted from the legacy state facade.
class RuntimeFlag(str, Enum):
    CLARIFICATION_REQUIRED = "clarification_required"
    INCOMPLETE_INTAKE = "incomplete_intake"
    READ_ONLY_EXECUTION = "read_only_execution"
    HIGH_SENSITIVITY = "high_sensitivity"
    EXTRACTABILITY_LIMITED = "extractability_limited"


# Canonical domain contract extracted from the legacy state facade.
@dataclass
class StepRecord:
    # This is the minimal audit entry recorded while Layer 2 performs runtime transitions.
    step_index: int
    runtime_state: RuntimeState
    node_name: str
    summary: str


__all__ = [
    "RuntimeState",
    "RuntimeFlag",
    "StepRecord"
]
