from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any

from ..hitl.decision_inbox import (
    ReviewDecisionStatus,
    ReviewDecisionSubmission,
    RuntimeOwnerLease,
)
from .runtime_artifacts import get_default_runtime_artifacts_root


# Keep HITL command transport separate from the canonical LangGraph checkpoint database.
def get_default_review_decision_inbox_path() -> Path:
    return get_default_runtime_artifacts_root() / "review_decisions.sqlite3"


class SQLiteReviewDecisionInbox:
    # Provide durable single-owner delivery between the dashboard and runtime CLI.
    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        lease_timeout_seconds: int = 10,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.storage_path = Path(storage_path or get_default_review_decision_inbox_path())
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.lease_timeout_seconds = lease_timeout_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.storage_path,
            check_same_thread=False,
            timeout=30,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._setup()

    # Create the bounded transport tables without modifying checkpoint storage.
    def _setup(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_owners (
                    thread_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    process_id INTEGER NOT NULL,
                    terminal_label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_decisions (
                    submission_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    decision_type TEXT NOT NULL,
                    decision_payload_json TEXT NOT NULL,
                    reviewer_id TEXT,
                    task_goal TEXT,
                    review_summary TEXT,
                    submitted_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    owner_id TEXT,
                    claimed_at TEXT,
                    consumed_at TEXT,
                    error TEXT,
                    UNIQUE(thread_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS idx_review_decisions_delivery
                    ON review_decisions(thread_id, status, submitted_at);
                CREATE INDEX IF NOT EXISTS idx_review_decisions_recent
                    ON review_decisions(submitted_at);
                """
            )
            self._connection.commit()

    # Register a new owner, fencing out another owner whose lease is still live.
    def acquire_owner(
        self,
        *,
        thread_id: str,
        owner_id: str,
        process_id: int,
        terminal_label: str,
        status: str,
    ) -> RuntimeOwnerLease:
        now = self._now()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM runtime_owners WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                if row is not None and row["owner_id"] != owner_id and self._owner_row_is_live(row, now):
                    raise ValueError(
                        f"Thread '{thread_id}' already has a live runtime owner."
                    )
                heartbeat_at = self._format_time(now)
                self._connection.execute(
                    """
                    INSERT INTO runtime_owners(
                        thread_id, owner_id, process_id, terminal_label, status, heartbeat_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        owner_id = excluded.owner_id,
                        process_id = excluded.process_id,
                        terminal_label = excluded.terminal_label,
                        status = excluded.status,
                        heartbeat_at = excluded.heartbeat_at
                    """,
                    (thread_id, owner_id, process_id, terminal_label, status, heartbeat_at),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return RuntimeOwnerLease(
            thread_id=thread_id,
            owner_id=owner_id,
            process_id=process_id,
            terminal_label=terminal_label,
            status=status,
            heartbeat_at=heartbeat_at,
        )

    # Renew only the owner holding the current fencing identity.
    def heartbeat_owner(
        self,
        *,
        thread_id: str,
        owner_id: str,
        status: str,
    ) -> RuntimeOwnerLease:
        heartbeat_at = self._format_time(self._now())
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE runtime_owners
                SET status = ?, heartbeat_at = ?
                WHERE thread_id = ? AND owner_id = ?
                """,
                (status, heartbeat_at, thread_id, owner_id),
            )
            self._connection.commit()
            if cursor.rowcount != 1:
                raise ValueError(f"Runtime ownership for thread '{thread_id}' was lost.")
            row = self._connection.execute(
                "SELECT * FROM runtime_owners WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Runtime ownership for thread '{thread_id}' was lost.")
        return self._owner_from_row(row)

    # Remove only the matching lease so a stale process cannot evict a new owner.
    def release_owner(self, *, thread_id: str, owner_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM runtime_owners WHERE thread_id = ? AND owner_id = ?",
                (thread_id, owner_id),
            )
            self._connection.commit()

    # Project the current owner only while its heartbeat remains within the lease.
    def get_live_owner(self, *, thread_id: str) -> RuntimeOwnerLease | None:
        now = self._now()
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM runtime_owners WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        if row is None or not self._owner_row_is_live(row, now):
            return None
        return self._owner_from_row(row)

    # Persist one decision idempotently and reject a conflicting second response.
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
    ) -> ReviewDecisionSubmission:
        serialized = json.dumps(
            decision_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        submission_id = sha256(
            f"{thread_id}\n{request_id}\n{serialized}".encode("utf-8")
        ).hexdigest()
        submitted_at = self._format_time(self._now())
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    """
                    SELECT * FROM review_decisions
                    WHERE thread_id = ? AND request_id = ?
                    """,
                    (thread_id, request_id),
                ).fetchone()
                if existing is not None:
                    if existing["submission_id"] != submission_id:
                        raise ValueError("This review request already has a different decision.")
                    self._connection.commit()
                    return self._submission_from_row(existing)
                self._connection.execute(
                    """
                    INSERT INTO review_decisions(
                        submission_id, thread_id, request_id, decision_type,
                        decision_payload_json, reviewer_id, task_goal, review_summary,
                        submitted_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        submission_id,
                        thread_id,
                        request_id,
                        decision_type,
                        serialized,
                        reviewer_id,
                        task_goal,
                        review_summary,
                        submitted_at,
                        ReviewDecisionStatus.PENDING.value,
                    ),
                )
                row = self._connection.execute(
                    "SELECT * FROM review_decisions WHERE submission_id = ?",
                    (submission_id,),
                ).fetchone()
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        if row is None:
            raise RuntimeError("The submitted review decision could not be reloaded.")
        return self._submission_from_row(row)

    # Claim or recover the exact checkpoint-requested command for the live owner.
    def claim_next(
        self,
        *,
        thread_id: str,
        request_id: str,
        owner_id: str,
    ) -> ReviewDecisionSubmission | None:
        claimed_at = self._format_time(self._now())
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                owner = self._connection.execute(
                    "SELECT * FROM runtime_owners WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                if owner is None or owner["owner_id"] != owner_id or not self._owner_row_is_live(owner, self._now()):
                    raise ValueError(f"Thread '{thread_id}' has no matching live runtime owner.")
                row = self._connection.execute(
                    """
                    SELECT * FROM review_decisions
                    WHERE thread_id = ? AND request_id = ? AND status IN (?, ?, ?)
                    ORDER BY submitted_at ASC
                    LIMIT 1
                    """,
                    (
                        thread_id,
                        request_id,
                        ReviewDecisionStatus.PENDING.value,
                        ReviewDecisionStatus.CLAIMED.value,
                        ReviewDecisionStatus.FAILED.value,
                    ),
                ).fetchone()
                if row is None:
                    self._connection.commit()
                    return None
                self._connection.execute(
                    """
                    UPDATE review_decisions
                    SET status = ?, owner_id = ?, claimed_at = ?, error = NULL
                    WHERE submission_id = ? AND status IN (?, ?, ?)
                    """,
                    (
                        ReviewDecisionStatus.CLAIMED.value,
                        owner_id,
                        claimed_at,
                        row["submission_id"],
                        ReviewDecisionStatus.PENDING.value,
                        ReviewDecisionStatus.CLAIMED.value,
                        ReviewDecisionStatus.FAILED.value,
                    ),
                )
                claimed = self._connection.execute(
                    "SELECT * FROM review_decisions WHERE submission_id = ?",
                    (row["submission_id"],),
                ).fetchone()
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return self._submission_from_row(claimed) if claimed is not None else None

    # Commit successful delivery only for the owner that claimed the command.
    def mark_consumed(self, *, submission_id: str, owner_id: str) -> None:
        consumed_at = self._format_time(self._now())
        self._transition_claimed(
            submission_id=submission_id,
            owner_id=owner_id,
            status=ReviewDecisionStatus.CONSUMED,
            consumed_at=consumed_at,
            error=None,
        )

    # Preserve a bounded failure explanation for operator diagnostics.
    def mark_failed(self, *, submission_id: str, owner_id: str, error: str) -> None:
        self._transition_claimed(
            submission_id=submission_id,
            owner_id=owner_id,
            status=ReviewDecisionStatus.FAILED,
            consumed_at=None,
            error=" ".join(str(error).split())[:2000],
        )

    # Apply one terminal transport-state transition with owner fencing.
    def _transition_claimed(
        self,
        *,
        submission_id: str,
        owner_id: str,
        status: ReviewDecisionStatus,
        consumed_at: str | None,
        error: str | None,
    ) -> None:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE review_decisions
                SET status = ?, consumed_at = ?, error = ?
                WHERE submission_id = ? AND owner_id = ? AND status = ?
                """,
                (
                    status.value,
                    consumed_at,
                    error,
                    submission_id,
                    owner_id,
                    ReviewDecisionStatus.CLAIMED.value,
                ),
            )
            self._connection.commit()
            if cursor.rowcount != 1:
                raise ValueError("The review decision is not claimed by this runtime owner.")

    # Return the submission that makes one pending request non-actionable in the UI.
    def get_for_request(
        self,
        *,
        thread_id: str,
        request_id: str,
    ) -> ReviewDecisionSubmission | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM review_decisions
                WHERE thread_id = ? AND request_id = ?
                """,
                (thread_id, request_id),
            ).fetchone()
        return self._submission_from_row(row) if row is not None else None

    # Keep resolved reviews visible for one bounded dashboard window.
    def list_recent(self, *, within_seconds: int) -> tuple[ReviewDecisionSubmission, ...]:
        cutoff = self._format_time(self._now() - timedelta(seconds=within_seconds))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM review_decisions
                WHERE submitted_at >= ?
                ORDER BY submitted_at DESC
                """,
                (cutoff,),
            ).fetchall()
        return tuple(self._submission_from_row(row) for row in rows)

    # Close the local connection at the owning process boundary.
    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # Normalize the injected clock to aware UTC for stable lease comparisons.
    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    # Serialize timestamps with microseconds so short polling windows remain ordered.
    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    # Parse one persisted UTC timestamp defensively.
    @staticmethod
    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    # Evaluate owner liveness against the configured fencing timeout.
    def _owner_row_is_live(self, row: sqlite3.Row, now: datetime) -> bool:
        heartbeat = self._parse_time(str(row["heartbeat_at"]))
        return now - heartbeat <= timedelta(seconds=self.lease_timeout_seconds)

    # Convert one owner row into the public typed projection.
    @staticmethod
    def _owner_from_row(row: sqlite3.Row) -> RuntimeOwnerLease:
        return RuntimeOwnerLease(
            thread_id=str(row["thread_id"]),
            owner_id=str(row["owner_id"]),
            process_id=int(row["process_id"]),
            terminal_label=str(row["terminal_label"]),
            status=str(row["status"]),
            heartbeat_at=str(row["heartbeat_at"]),
        )

    # Convert one decision row into the immutable transport contract.
    @staticmethod
    def _submission_from_row(row: sqlite3.Row) -> ReviewDecisionSubmission:
        payload = json.loads(str(row["decision_payload_json"]))
        if not isinstance(payload, dict):
            raise ValueError("Persisted review decision payload must be a JSON object.")
        return ReviewDecisionSubmission(
            submission_id=str(row["submission_id"]),
            thread_id=str(row["thread_id"]),
            request_id=str(row["request_id"]),
            decision_type=str(row["decision_type"]),
            decision_payload=payload,
            reviewer_id=row["reviewer_id"],
            task_goal=row["task_goal"],
            review_summary=row["review_summary"],
            submitted_at=str(row["submitted_at"]),
            status=ReviewDecisionStatus(str(row["status"])),
            owner_id=row["owner_id"],
            claimed_at=row["claimed_at"],
            consumed_at=row["consumed_at"],
            error=row["error"],
        )


__all__ = [
    "SQLiteReviewDecisionInbox",
    "get_default_review_decision_inbox_path",
]
