from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# Canonical domain contract extracted from the legacy state facade.
class RuntimeState(str, Enum):
    PLANNING = "planning"
    ACTING = "acting"
    READY_NEXT_STEP = "ready_next_step"
    READY_TOOL_EXECUTION = "ready_tool_execution"
    READY_GOVERNANCE = "ready_governance"
    READY_EXTERNAL_EXECUTION = "ready_external_execution"
    REPLANNING = "replanning"
    WAITING_HUMAN_REVIEW = "waiting_human_review"
    COMPLETED = "completed"
    ABORTED = "aborted"


# Classify why a run reached a terminal or failed runtime boundary.
class TerminationReason(str, Enum):
    GOVERNANCE_DENIED = "governance_denied"
    HUMAN_ABORT = "human_abort"
    GOVERNANCE_INVARIANT_FAILURE = "governance_invariant_failure"
    EXTERNAL_EXECUTION_REJECTED = "external_execution_rejected"
    EXTERNAL_EXECUTION_FAILED = "external_execution_failed"
    RUNTIME_FAILURE = "runtime_failure"


# Canonical domain contract extracted from the legacy state facade.
class RuntimeFlag(str, Enum):
    CLARIFICATION_REQUIRED = "clarification_required"
    INCOMPLETE_INTAKE = "incomplete_intake"
    READ_ONLY_EXECUTION = "read_only_execution"
    HIGH_SENSITIVITY = "high_sensitivity"
    EXTRACTABILITY_LIMITED = "extractability_limited"
    EXTERNAL_EXECUTION_RECONCILIATION_REQUIRED = (
        "external_execution_reconciliation_required"
    )


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
    "TerminationReason",
    "RuntimeFlag",
    "StepRecord"
]
