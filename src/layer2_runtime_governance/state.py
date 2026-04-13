from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from layer1_intake import (
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    TaskSpec,
)

# Layer 2 keeps one shared runtime state.
# This file defines the state schema plus the small decision models that
# planner, governance, HITL, and finalization will exchange.
class RuntimeState(str, Enum):
    PLANNING = "planning"
    ACTING = "acting"
    OBSERVING = "observing"
    REPLANNING = "replanning"
    WAITING_HUMAN_REVIEW = "waiting_human_review"
    COMPLETED = "completed"
    ABORTED = "aborted"

# Governance can continue, replan, stop, or route the task to human review.
class GovernanceDecisionType(str, Enum):
    CONTINUE = "continue"
    CONTINUE_READ_ONLY = "continue_read_only"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REPLAN = "replan"
    WAIT_HUMAN_REVIEW = "wait_human_review"
    ASK_USER_CLARIFICATION = "ask_user_clarification"
    STOP = "stop"

# Human review can approve, reject, edit, or ask for more evidence.
class HumanDecisionType(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    STOP = "stop"

# One history entry per executed node.
# This is the minimum audit trail for replay and debugging.
@dataclass
class StepRecord:
    step_index: int
    runtime_state: RuntimeState
    node_name: str
    summary: str


# The planner writes the next intended action here.
# The payload can later be forwarded to the Tool Bus or to a human reviewer.
@dataclass
class ProposedAction:
    action_name: str
    action_type: str
    rationale: str
    payload: dict[str, Any] = field(default_factory=dict)


# Governance output explains what the runtime should do next and why.
@dataclass
class GovernanceDecision:
    decision: GovernanceDecisionType
    rationale: str
    triggered_rules: list[str] = field(default_factory=list)


# Human review is stored in a canonical shape so the runtime can resume deterministically.
@dataclass
class HumanDecision:
    decision: HumanDecisionType
    comment: str | None = None
    edited_action: ProposedAction | None = None


# TaskState is the shared Layer 2 state.
# It contains the Layer 1 handoff plus the runtime fields updated by each node.
@dataclass
class TaskState:
    # Direct handoff from Layer 1
    intake_payload: IntakeResult
    task_spec: TaskSpec
    execution_policy: ExecutionPolicy
    intake_metadata: IntakeMetadata

    # Runtime status
    current_state: RuntimeState
    step_index: int = 0
    step_history: list[StepRecord] = field(default_factory=list)

    # Runtime working memory
    collected_evidence: list[dict[str, Any]] = field(default_factory=list)
    proposed_action: ProposedAction | None = None
    last_tool_result: dict[str, Any] | None = None
    governance_output: GovernanceDecision | None = None
    human_decision: HumanDecision | None = None

    # Finalization / control
    final_output: dict[str, Any] | None = None
    termination_reason: str | None = None
    runtime_flags: list[str] = field(default_factory=list)

    @classmethod
    def from_intake(cls, intake_result: IntakeResult) -> "TaskState":
        # The bootstrap rules are deterministic.
        # Layer 1 parse status decides the initial runtime status and flags.
        parse_status = intake_result.metadata.parse_status
        runtime_flags: list[str] = []

        if parse_status == ParseStatus.NEEDS_CLARIFICATION:
            current_state = RuntimeState.WAITING_HUMAN_REVIEW
            runtime_flags.append("clarification_required")
        elif parse_status == ParseStatus.PARTIAL:
            current_state = RuntimeState.PLANNING
            runtime_flags.append("incomplete_intake")
        else:
            current_state = RuntimeState.PLANNING

        if intake_result.structured_task.execution_policy.read_only_mode:
            runtime_flags.append("read_only_execution")

        # Sensitivity becomes a runtime hint immediately, even before governance runs.
        if intake_result.structured_task.task_spec.sensitivity in {
            SensitivityLevel.HIGH,
            SensitivityLevel.CRITICAL,
        }:
            runtime_flags.append("high_sensitivity")

        return cls(
            intake_payload=intake_result,
            task_spec=intake_result.structured_task.task_spec,
            execution_policy=intake_result.structured_task.execution_policy,
            intake_metadata=intake_result.metadata,
            current_state=current_state,
            runtime_flags=runtime_flags,
        )


__all__ = [
    "GovernanceDecision",
    "GovernanceDecisionType",
    "HumanDecision",
    "HumanDecisionType",
    "ProposedAction",
    "RuntimeState",
    "StepRecord",
    "TaskState",
]
