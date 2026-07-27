from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..actor_contract import ActorResultType

# Canonical domain contract extracted from the legacy state facade.
class ScratchpadEntryType(str, Enum):
    # These are audit categories, not hidden reasoning transcripts.
    OBSERVATION = "observation"
    TOOL_DECISION = "tool_decision"
    PROGRESS_NOTE = "progress_note"
    UNCERTAINTY = "uncertainty"
    COMPLETION_RATIONALE = "completion_rationale"


# Canonical domain contract extracted from the legacy state facade.
# Canonical domain contract extracted from the legacy state facade.
@dataclass
class ExecutedPlanStep:
    # Keep a compact trace of each plan step the actor has already completed.
    step_id: str
    objective: str
    result_type: ActorResultType
    summary: str
    output_text: str | None = None


# Canonical domain contract extracted from the legacy state facade.
@dataclass
class ScratchpadEntry:
    # Store audit-safe working memory without preserving hidden chain-of-thought.
    turn_index: int
    plan_step_id: str
    entry_type: ScratchpadEntryType
    summary: str
    evidence_refs: list[str] = field(default_factory=list)


# Canonical domain contract extracted from the legacy state facade.
@dataclass(frozen=True)
class StepValidation:
    # Preserve an audit-safe completion attestation; routing comes from ActorResult and OPA.
    plan_step_id: str
    actor_self_confidence: float = 0.0
    rationale_summary: str = ""
    evidence_refs: tuple[str, ...] = ()
    residual_uncertainty: str | None = None


__all__ = [
    "ScratchpadEntryType",
    "ExecutedPlanStep",
    "ScratchpadEntry",
    "StepValidation"
]
