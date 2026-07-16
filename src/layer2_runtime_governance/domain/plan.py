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
class PlanStepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


# Canonical domain contract extracted from the legacy state facade.
class PlanStepKind(str, Enum):
    # Step kinds classify the semantic role of the current plan step.
    LOCAL_TRANSFORMATION = "local_transformation"
    SOURCE_DISCOVERY = "source_discovery"
    HIGH_LEVEL_SUMMARY = "high_level_summary"
    DETAILED_EXTRACTION = "detailed_extraction"
    COMPLIANCE_OR_LEGAL = "compliance_or_legal"
    FINAL_SYNTHESIS = "final_synthesis"


# Canonical domain contract extracted from the legacy state facade.
@dataclass
class PlanStep:
    # Planner output stays semantic so actor can later choose the concrete action.
    step_id: str
    objective: str
    rationale: str
    expected_outcome: str
    step_kind: PlanStepKind | None = None
    evidence_requirement: EvidenceSupportLevel | None = None
    claim_precision_ceiling: EvidenceSupportLevel | None = None
    status: PlanStepStatus = PlanStepStatus.PENDING


# Canonical domain contract extracted from the legacy state facade.
@dataclass
class PlannerDecision:
    # The planner returns a typed semantic planning snapshot plus traceability metadata.
    plan_steps: list[PlanStep]
    rationale: str
    planner_name: str
    prompt_version: str


__all__ = [
    "PlanStepStatus",
    "PlanStepKind",
    "PlanStep",
    "PlannerDecision"
]
