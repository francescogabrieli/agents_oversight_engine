from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class ReviewDecisionStatus(str, Enum):
    # Submission state is transport state, never a LangGraph runtime state.
    PENDING = "pending"
    CLAIMED = "claimed"
    CONSUMED = "consumed"
    FAILED = "failed"


@dataclass(frozen=True)
class ReviewDecisionSubmission:
    # Preserve the validated human input and its delivery lifecycle for audit and replay.
    submission_id: str
    thread_id: str
    request_id: str
    decision_type: str
    decision_payload: dict[str, Any]
    submitted_at: str
    status: ReviewDecisionStatus
    reviewer_id: str | None = None
    task_goal: str | None = None
    review_summary: str | None = None
    owner_id: str | None = None
    claimed_at: str | None = None
    consumed_at: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class RuntimeOwnerLease:
    # Identify the one process currently authorized to advance a runtime thread.
    thread_id: str
    owner_id: str
    process_id: int
    terminal_label: str
    status: str
    heartbeat_at: str


class ReviewDecisionInboxPort(Protocol):
    # Register or renew the sole runtime owner for one thread.
    def acquire_owner(
        self,
        *,
        thread_id: str,
        owner_id: str,
        process_id: int,
        terminal_label: str,
        status: str,
    ) -> RuntimeOwnerLease: ...

    # Refresh a live owner's status while it waits or executes.
    def heartbeat_owner(
        self,
        *,
        thread_id: str,
        owner_id: str,
        status: str,
    ) -> RuntimeOwnerLease: ...

    # Release ownership only when the matching process finishes.
    def release_owner(self, *, thread_id: str, owner_id: str) -> None: ...

    # Return the current non-expired owner when one is available.
    def get_live_owner(self, *, thread_id: str) -> RuntimeOwnerLease | None: ...

    # Store one validated, idempotent human decision for delivery to the owner.
    def submit(
        self,
        *,
        thread_id: str,
        request_id: str,
        decision_type: str,
        decision_payload: dict[str, Any],
        reviewer_id: str | None,
        task_goal: str | None,
        review_summary: str | None,
    ) -> ReviewDecisionSubmission: ...

    # Atomically claim the next decision for the matching runtime owner.
    def claim_next(
        self,
        *,
        thread_id: str,
        request_id: str,
        owner_id: str,
    ) -> ReviewDecisionSubmission | None: ...

    # Mark a claimed decision as durably applied to the checkpoint.
    def mark_consumed(self, *, submission_id: str, owner_id: str) -> None: ...

    # Record a failed resume attempt without silently retrying an external effect.
    def mark_failed(self, *, submission_id: str, owner_id: str, error: str) -> None: ...

    # Find whether one exact review request already has a submitted decision.
    def get_for_request(
        self,
        *,
        thread_id: str,
        request_id: str,
    ) -> ReviewDecisionSubmission | None: ...

    # List recently resolved review actions for the transient dashboard section.
    def list_recent(self, *, within_seconds: int) -> tuple[ReviewDecisionSubmission, ...]: ...

    # Release adapter resources owned by the composition boundary.
    def close(self) -> None: ...


__all__ = [
    "ReviewDecisionInboxPort",
    "ReviewDecisionStatus",
    "ReviewDecisionSubmission",
    "RuntimeOwnerLease",
]
