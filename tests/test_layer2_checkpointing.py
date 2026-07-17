from __future__ import annotations

import sys
import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from layer2_runtime_governance.infrastructure.checkpoint_retention import (
    CheckpointRetentionSettings,
    prune_expired_checkpoint_threads,
)
from layer2_runtime_governance.infrastructure.checkpointing import (
    CheckpointBackend,
    CheckpointSettings,
    build_default_checkpointer,
)


class _RecordingCheckpointer:
    def __init__(self) -> None:
        self.deleted_thread_ids: list[str] = []

    # Record the official saver operation requested by retention.
    def delete_thread(self, thread_id: str) -> None:
        self.deleted_thread_ids.append(thread_id)


class _SummaryArtifactStore:
    def __init__(self, summaries: dict[str, dict[str, object]]) -> None:
        self.summaries = summaries

    # Expose the same read-only thread listing used by RuntimeArtifactStore.
    def list_thread_ids(self) -> list[str]:
        return list(self.summaries)

    # Return the audit summary without exposing full trace payloads to retention.
    def read_runtime_summary(self, thread_id: str) -> dict[str, object] | None:
        return self.summaries.get(thread_id)


class Layer2CheckpointingTests(unittest.TestCase):
    # Verify local runs use LangGraph's official SQLite saver.
    def test_default_checkpointer_is_official_sqlite_saver(self) -> None:
        temp_dir = REPO_ROOT / f"_checkpoint_backend_test_{uuid4().hex}"
        saver = None
        try:
            saver = build_default_checkpointer(
                storage_path=Path(temp_dir) / "checkpoints.sqlite3"
            )
            self.assertIsInstance(saver, SqliteSaver)
        finally:
            if saver is not None:
                saver.conn.close()
            shutil.rmtree(temp_dir, ignore_errors=True)

    # Verify the production backend fails clearly before graph construction if no URI exists.
    def test_postgres_backend_requires_connection_uri(self) -> None:
        settings = CheckpointSettings(
            backend=CheckpointBackend.POSTGRES,
            postgres_uri=None,
        )

        with self.assertRaisesRegex(RuntimeError, "LAYER2_CHECKPOINT_POSTGRES_URI"):
            build_default_checkpointer(settings=settings)

    # Verify retention deletes only expired terminal checkpoints and preserves interrupts.
    def test_retention_preserves_pending_review_and_active_threads(self) -> None:
        summaries = {
            "expired-complete": {
                "runtime_state": "completed",
                "has_pending_human_review": False,
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            "pending-review": {
                "runtime_state": "waiting_human_review",
                "has_pending_human_review": True,
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            "active": {
                "runtime_state": "acting",
                "has_pending_human_review": False,
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            "recent-abort": {
                "runtime_state": "aborted",
                "has_pending_human_review": False,
                "updated_at": "2026-07-10T00:00:00+00:00",
            },
        }
        checkpointer = _RecordingCheckpointer()

        report = prune_expired_checkpoint_threads(
            checkpointer=checkpointer,
            artifact_store=_SummaryArtifactStore(summaries),
            settings=CheckpointRetentionSettings(completed_thread_days=30),
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

        self.assertEqual(checkpointer.deleted_thread_ids, ["expired-complete"])
        self.assertEqual(report.deleted_thread_ids, ("expired-complete",))
        self.assertEqual(report.retained_thread_count, 3)


if __name__ == "__main__":
    unittest.main()
