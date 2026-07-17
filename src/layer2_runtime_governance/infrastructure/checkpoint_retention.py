from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .runtime_artifacts import RuntimeArtifactStore
from .environment import get_env_value


class ThreadDeletingCheckpointer(Protocol):
    # Retention depends only on the official saver thread-deletion contract.
    def delete_thread(self, thread_id: str) -> None: ...


@dataclass(frozen=True)
class CheckpointRetentionSettings:
    # Completed checkpoint threads remain resumable/debuggable for a bounded period.
    completed_thread_days: int = 30

    # Resolve retention policy at the maintenance boundary.
    @classmethod
    def from_env(cls) -> "CheckpointRetentionSettings":
        value = get_env_value("LAYER2_COMPLETED_THREAD_RETENTION_DAYS", "30") or "30"
        return cls(completed_thread_days=max(1, int(value)))


@dataclass(frozen=True)
class CheckpointRetentionReport:
    # Report exactly which official LangGraph threads were pruned.
    deleted_thread_ids: tuple[str, ...]
    retained_thread_count: int


# Delete only old completed/aborted checkpoint threads while preserving audit artifacts.
def prune_expired_checkpoint_threads(
    *,
    checkpointer: ThreadDeletingCheckpointer,
    artifact_store: RuntimeArtifactStore,
    settings: CheckpointRetentionSettings | None = None,
    now: datetime | None = None,
) -> CheckpointRetentionReport:
    resolved_settings = settings or CheckpointRetentionSettings.from_env()
    resolved_now = now or datetime.now(timezone.utc)
    cutoff = resolved_now - timedelta(days=resolved_settings.completed_thread_days)
    deleted: list[str] = []
    retained = 0

    for thread_id in artifact_store.list_thread_ids():
        summary = artifact_store.read_runtime_summary(thread_id)
        if not summary or summary.get("has_pending_human_review"):
            retained += 1
            continue
        if summary.get("runtime_state") not in {"completed", "aborted"}:
            retained += 1
            continue
        updated_at = _parse_updated_at(summary.get("updated_at"))
        if updated_at is None or updated_at >= cutoff:
            retained += 1
            continue
        checkpointer.delete_thread(thread_id)
        deleted.append(thread_id)

    return CheckpointRetentionReport(
        deleted_thread_ids=tuple(deleted),
        retained_thread_count=retained,
    )


# Parse persisted UTC timestamps defensively so retention fails safe.
def _parse_updated_at(value: object) -> datetime | None:
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
