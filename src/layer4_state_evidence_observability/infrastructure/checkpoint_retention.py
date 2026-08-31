from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from langgraph.checkpoint.sqlite import SqliteSaver

from .runtime_artifacts import RuntimeArtifactStore
from .environment import get_env_value


class ThreadDeletingCheckpointer(Protocol):
    # Retention depends only on the official saver thread-deletion contract.
    def delete_thread(self, thread_id: str) -> None: ...


@dataclass(frozen=True)
class CheckpointRetentionSettings:
    # Terminal runs are audit artifacts, while only active/HITL runs need resumable checkpoints.
    completed_thread_days: int = 0
    # A HITL pause nobody ever resumes still held its checkpoint open forever under the
    # previous policy: after this many hours without a resume, the pause counts as
    # abandoned rather than active, and the checkpoint (never the audit artifact) is
    # reclaimed. The `--attach-thread` recovery path stops working for it at that point.
    stale_pending_review_hours: int = 1
    # A checkpoint thread with no matching artifact directory came from a run that
    # crashed before it ever persisted, or from a dev/test thread id that wrote into the
    # production database. It carries no audit trail that deleting it could destroy.
    orphan_thread_hours: int = 1

    # Resolve retention policy at the maintenance boundary.
    @classmethod
    def from_env(cls) -> "CheckpointRetentionSettings":
        days = get_env_value("LAYER2_COMPLETED_THREAD_RETENTION_DAYS", "0") or "0"
        pending_hours = get_env_value("LAYER2_STALE_PENDING_REVIEW_HOURS", "1") or "1"
        orphan_hours = get_env_value("LAYER2_ORPHAN_THREAD_RETENTION_HOURS", "1") or "1"
        return cls(
            completed_thread_days=max(0, int(days)),
            stale_pending_review_hours=max(0, int(pending_hours)),
            orphan_thread_hours=max(0, int(orphan_hours)),
        )


@dataclass(frozen=True)
class CheckpointRetentionReport:
    # Report exactly which official LangGraph threads were pruned.
    deleted_thread_ids: tuple[str, ...]
    retained_thread_count: int


# Delete checkpoints for abandoned threads while preserving audit artifacts and any
# thread a human could still plausibly resume.
def prune_expired_checkpoint_threads(
    *,
    checkpointer: ThreadDeletingCheckpointer,
    artifact_store: RuntimeArtifactStore,
    settings: CheckpointRetentionSettings | None = None,
    now: datetime | None = None,
) -> CheckpointRetentionReport:
    resolved_settings = settings or CheckpointRetentionSettings.from_env()
    resolved_now = now or datetime.now(timezone.utc)
    completed_cutoff = resolved_now - timedelta(days=resolved_settings.completed_thread_days)
    pending_cutoff = resolved_now - timedelta(hours=resolved_settings.stale_pending_review_hours)
    orphan_cutoff = resolved_now - timedelta(hours=resolved_settings.orphan_thread_hours)

    deleted: list[str] = []
    retained = 0

    known_thread_ids = set(artifact_store.list_thread_ids())
    # A checkpoint can exist with no matching artifact directory - a crash before the run
    # ever persisted its summary, or a dev/test thread id sharing the production
    # database. It is invisible to the artifact store, so without reading the checkpoint
    # storage directly it could accumulate forever regardless of age.
    orphan_thread_ids = _list_checkpoint_thread_ids(checkpointer) - known_thread_ids

    for thread_id in sorted(known_thread_ids):
        summary = artifact_store.read_runtime_summary(thread_id)
        if not summary:
            retained += 1
            continue
        if summary.get("has_pending_human_review"):
            updated_at = _parse_timestamp(summary.get("updated_at"))
            if updated_at is None or updated_at >= pending_cutoff:
                retained += 1
                continue
            checkpointer.delete_thread(thread_id)
            deleted.append(thread_id)
            continue
        if summary.get("runtime_state") not in {"completed", "aborted"}:
            retained += 1
            continue
        updated_at = _parse_timestamp(summary.get("updated_at"))
        if updated_at is None or updated_at >= completed_cutoff:
            retained += 1
            continue
        checkpointer.delete_thread(thread_id)
        deleted.append(thread_id)

    for thread_id in sorted(orphan_thread_ids):
        last_touched = _last_checkpoint_timestamp(checkpointer, thread_id)
        if last_touched is None or last_touched >= orphan_cutoff:
            retained += 1
            continue
        checkpointer.delete_thread(thread_id)
        deleted.append(thread_id)

    return CheckpointRetentionReport(
        deleted_thread_ids=tuple(deleted),
        retained_thread_count=retained,
    )


# Enumerate checkpoint threads directly from official SQLite storage. Any other
# checkpointer implementation (including test stubs) simply contributes no orphans,
# rather than retention failing on a capability it cannot assume.
def _list_checkpoint_thread_ids(checkpointer: object) -> set[str]:
    if not isinstance(checkpointer, SqliteSaver):
        return set()
    rows = checkpointer.conn.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()
    return {str(row[0]) for row in rows}


# Read the `ts` LangGraph embeds in its own checkpoint payload - the only notion of
# "last touched" a thread with no artifact directory can offer.
def _last_checkpoint_timestamp(checkpointer: object, thread_id: str) -> datetime | None:
    if not isinstance(checkpointer, SqliteSaver):
        return None
    row = checkpointer.conn.execute(
        "SELECT type, checkpoint FROM checkpoints WHERE thread_id = ? "
        "ORDER BY checkpoint_id DESC LIMIT 1",
        (thread_id,),
    ).fetchone()
    if row is None:
        return None
    checkpoint_type, blob = row
    try:
        payload = checkpointer.serde.loads_typed((checkpoint_type, blob))
    except Exception:
        return None
    return _parse_timestamp(payload.get("ts") if isinstance(payload, dict) else None)


# Parse persisted UTC timestamps defensively so retention fails safe.
def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = [
    "CheckpointRetentionReport",
    "CheckpointRetentionSettings",
    "prune_expired_checkpoint_threads",
]
