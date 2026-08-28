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


class ToolDisposition(str, Enum):
    # This records a local capability decision without conflating it with a backend call.
    EXECUTED_SUCCESS = "executed_success"
    EXECUTED_RETRYABLE_FAILURE = "executed_retryable_failure"
    EXECUTED_TERMINAL_FAILURE = "executed_terminal_failure"
    CACHE_HIT = "cache_hit"
    REJECTED_DUPLICATE = "rejected_duplicate"
    REJECTED_CIRCUIT = "rejected_circuit"
    REJECTED_UNANCHORED_TARGET = "rejected_unanchored_target"

    # A disposition either reached the capability backend or was settled locally.
    # Convergence rules must classify by this split rather than by an allowlist,
    # so a new local rejection can never fall outside every progress guard.
    @property
    def reached_backend(self) -> bool:
        return self in _BACKEND_DISPOSITIONS


_BACKEND_DISPOSITIONS = frozenset(
    {
        ToolDisposition.EXECUTED_SUCCESS,
        ToolDisposition.EXECUTED_RETRYABLE_FAILURE,
        ToolDisposition.EXECUTED_TERMINAL_FAILURE,
    }
)


@dataclass(frozen=True)
class StepArtifact:
    # A durable, step-owned result is distinct from the immutable audit trace.
    artifact_id: str
    step_id: str
    content: str
    evidence_refs: tuple[str, ...] = ()
    revision: int = 1
    provenance: str = "actor"


@dataclass
class AgentWorkspace:
    # The workspace is the operational read model used to continue work across steps.
    artifacts: list[StepArtifact] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    revision: int = 0


@dataclass(frozen=True)
class ToolAttempt:
    # An attempt is made for every requested capability, including locally rejected work.
    action_key: str
    plan_step_id: str
    progress_epoch: int
    disposition: ToolDisposition


@dataclass(frozen=True)
class ToolInvocation:
    # An invocation exists only when the backend was actually called.
    action_key: str
    plan_step_id: str
    progress_epoch: int
    retryable_failure: bool = False


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


@dataclass(frozen=True)
class StepCompletionCandidate:
    # A completion candidate is immutable until governance accepts its exact result.
    candidate_id: str
    step_id: str
    summary: str
    output_text: str
    evidence_refs: tuple[str, ...] = ()
    external_operation_id: str | None = None
    external_operation_digest: str | None = None
    evidence_snapshot_digest: str | None = None
    evidence_selection_digest: str | None = None


@dataclass(frozen=True)
class ReplanCheckpoint:
    # Record the observable boundary after one replacement trajectory is installed.
    # The tool figure counts backend-reaching calls, never locally settled outcomes:
    # a cache hit or a rejected target changed nothing in the world, so it must not
    # read as a new observable basis admitting the next replan.
    actor_turn_count: int
    tool_call_count: int
    observation_count: int
    completed_step_count: int
    human_review_count: int
    plan_signature: str
    request_reason: str
    progress_epoch: int = 0


__all__ = [
    "AgentWorkspace",
    "ScratchpadEntryType",
    "ExecutedPlanStep",
    "ReplanCheckpoint",
    "ScratchpadEntry",
    "StepValidation",
    "StepCompletionCandidate",
    "StepArtifact",
    "ToolAttempt",
    "ToolDisposition",
    "ToolInvocation",
]
