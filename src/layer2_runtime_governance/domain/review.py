from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from ..tool_contract import ToolRequest
from .lifecycle import RuntimeState

if TYPE_CHECKING:
    from ..actor_contract import ActorResultType
    from ..governance_components.contracts import GovernanceDirective


# Canonical domain contract extracted from the legacy state facade.
class HumanReviewDecisionType(str, Enum):
    # Human review stays typed so the runtime can pause and resume deterministically.
    APPROVE_CONTINUE = "approve_continue"
    APPROVE_CONTINUE_READ_ONLY = "approve_continue_read_only"
    MODIFY_AND_CONTINUE = "modify_and_continue"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REPLAN = "replan"
    ASK_CLARIFICATION = "ask_clarification"
    ABORT = "abort"


# Canonical domain contract extracted from the legacy state facade.
class ExternalCommunicationPhase(str, Enum):
    # External communication remains a gated subflow, even before real execution is added.
    EVIDENCE_COLLECTION = "evidence_collection"
    DRAFT_IN_PROGRESS = "draft_in_progress"
    DRAFT_PROPOSAL = "draft_proposal"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    APPROVED_FOR_EXECUTION = "approved_for_execution"
    BLOCKED = "blocked"


# Canonical domain contract extracted from the legacy state facade.
@dataclass(frozen=True)
class ExternalCommunicationDraft:
    # Keep the external payload explicit so HITL can edit the real outbound content.
    channel: str
    recipient: str | None = None
    subject: str | None = None
    body: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# Canonical domain contract extracted from the legacy state facade.
@dataclass(frozen=True)
class HumanReviewExternalEdit:
    # Human edits are carried as a typed full draft, not as a free-form patch.
    draft: ExternalCommunicationDraft
    note: str | None = None


# Canonical domain contract extracted from the legacy state facade.
@dataclass(frozen=True)
class HumanReviewRequest:
    # Human review requests are persisted so interrupts can resume on the same checkpoint.
    request_id: str
    thread_id: str
    current_state: RuntimeState
    resume_state: RuntimeState
    current_plan_step_id: str | None
    current_plan_step_objective: str | None
    actor_result_type: ActorResultType | None
    current_tool_request: ToolRequest | None
    governance_directive: GovernanceDirective | None
    governance_summary: str | None
    static_policy_summary: str | None = None
    dynamic_policy_summary: str | None = None
    behavioral_summary: str | None = None
    judge_summary: str | None = None
    oversight_summary: str | None = None
    external_communication_target_type: str | None = None
    external_communication_draft: ExternalCommunicationDraft | None = None
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    allowed_decisions: tuple[HumanReviewDecisionType, ...] = field(default_factory=tuple)
    review_summary: str | None = None


# Canonical domain contract extracted from the legacy state facade.
@dataclass(frozen=True)
class HumanReviewDecision:
    # Human review decisions are canonical and typed for audit-safe resume.
    request_id: str
    decision_type: HumanReviewDecisionType
    note: str | None = None
    clarification_text: str | None = None
    external_communication_edit: HumanReviewExternalEdit | None = None
    reviewer_id: str | None = None


# Canonical domain contract extracted from the legacy state facade.
@dataclass
class ExternalCommunicationState:
    # Keep the externally impactful draft/review boundary explicit in TaskState.
    phase: ExternalCommunicationPhase
    review_ready: bool = False
    draft: ExternalCommunicationDraft | None = None
    last_applied_edit: HumanReviewExternalEdit | None = None
    edit_history: list[HumanReviewExternalEdit] = field(default_factory=list)
    last_transition_summary: str | None = None


__all__ = [
    "HumanReviewDecisionType",
    "ExternalCommunicationPhase",
    "ExternalCommunicationDraft",
    "HumanReviewExternalEdit",
    "HumanReviewRequest",
    "HumanReviewDecision",
    "ExternalCommunicationState"
]
