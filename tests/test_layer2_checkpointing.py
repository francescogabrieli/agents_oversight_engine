from __future__ import annotations

import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver


REPO_ROOT = Path(__file__).resolve().parents[1]

from layer4_state_evidence_observability.infrastructure.checkpoint_retention import (
    CheckpointRetentionSettings,
    prune_expired_checkpoint_threads,
)
from layer4_state_evidence_observability.infrastructure.checkpointing import (
    CheckpointBackend,
    CheckpointSettings,
    build_default_checkpointer,
    build_runtime_serializer,
)
from layer3_tool_bus_external_capabilities.tool_contract import RetrievalKind, ToolErrorCode
from layer3_tool_bus_external_capabilities.external_operations import CommunicationBodyFormat
from layer2_runtime_governance.domain import TerminationReason
from layer2_runtime_governance.governance_components import JudgeAdmission
from layer2_runtime_governance.judge import (
    JudgeClaimAssessment,
    JudgeClaimStatus,
)


# Write one minimal checkpoint with a caller-controlled `ts`, the only "last touched"
# signal a thread with no artifact directory can offer.
def _put_checkpoint(saver: SqliteSaver, *, thread_id: str, ts: str) -> None:
    saver.put(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        {
            "v": 1,
            "id": str(uuid4()),
            "ts": ts,
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
            "updated_channels": None,
        },
        {"source": "update", "step": 0, "parents": {}},
        {},
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

    # Every typed tool failure must survive checkpoint serialization as its enum.
    def test_tool_error_code_is_allowlisted_for_checkpoint_roundtrip(self) -> None:
        serializer = build_runtime_serializer()

        type_tag, payload = serializer.dumps_typed(ToolErrorCode.DUPLICATE_REQUEST)
        restored = serializer.loads_typed((type_tag, payload))

        self.assertIs(restored, ToolErrorCode.DUPLICATE_REQUEST)

    # Retrieval kind, communication format, and termination reason must survive checkpoint roundtrip.
    def test_runtime_enums_are_allowlisted_for_checkpoint_roundtrip(self) -> None:
        serializer = build_runtime_serializer()
        enums = (
            RetrievalKind.SEARCH_SNIPPET,
            RetrievalKind.FETCHED_DOCUMENT,
            CommunicationBodyFormat.PLAIN,
            CommunicationBodyFormat.MARKDOWN,
            TerminationReason.GOVERNANCE_DENIED,
            TerminationReason.HUMAN_ABORT,
        )

        for enum_val in enums:
            type_tag, payload = serializer.dumps_typed(enum_val)
            restored = serializer.loads_typed((type_tag, payload))
            self.assertIs(restored, enum_val)

    # Staged admission and claim grounding must survive interrupt/resume checkpoints.
    def test_new_judge_contracts_are_allowlisted_for_checkpoint_roundtrip(self) -> None:
        serializer = build_runtime_serializer()
        values = (
            JudgeAdmission(
                required=True,
                reason="governed_external_semantic_candidate",
            ),
            JudgeClaimAssessment(
                claim_summary="The payload contains one grounded claim.",
                status=JudgeClaimStatus.SUPPORTED_BY_SELECTED,
                selected_evidence_refs=("observation-1",),
                supporting_evidence_refs=("observation-1",),
            ),
        )

        restored = [
            serializer.loads_typed(serializer.dumps_typed(value))
            for value in values
        ]

        self.assertEqual(restored, list(values))

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
                "updated_at": "2026-07-16T23:30:00+00:00",
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

    # A HITL pause nobody resumes is abandoned, not active - only the checkpoint (never
    # the artifact directory) is reclaimed once it outlives the review window.
    def test_retention_reclaims_stale_pending_review_threads(self) -> None:
        summaries = {
            "abandoned-review": {
                "runtime_state": "waiting_human_review",
                "has_pending_human_review": True,
                "updated_at": "2026-07-17T08:00:00+00:00",
            },
            "fresh-review": {
                "runtime_state": "waiting_human_review",
                "has_pending_human_review": True,
                "updated_at": "2026-07-17T09:50:00+00:00",
            },
        }
        checkpointer = _RecordingCheckpointer()

        report = prune_expired_checkpoint_threads(
            checkpointer=checkpointer,
            artifact_store=_SummaryArtifactStore(summaries),
            settings=CheckpointRetentionSettings(stale_pending_review_hours=1),
            now=datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(report.deleted_thread_ids, ("abandoned-review",))
        self.assertEqual(report.retained_thread_count, 1)

    # A checkpoint with no matching artifact directory - a crash before the run ever
    # persisted, or a dev/test thread reusing the production database - has no audit
    # trail to protect, so it is discovered from the checkpoint storage itself and
    # reclaimed on the same clock as everything else abandoned.
    def test_retention_reclaims_orphan_checkpoint_threads(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "orphans.sqlite3"
            connection = sqlite3.connect(str(db_path), check_same_thread=False)
            serde = build_runtime_serializer()
            saver = SqliteSaver(connection, serde=serde)
            saver.setup()

            _put_checkpoint(saver, thread_id="crashed-old", ts="2026-07-17T08:00:00+00:00")
            _put_checkpoint(saver, thread_id="crashed-recent", ts="2026-07-17T09:50:00+00:00")

            report = prune_expired_checkpoint_threads(
                checkpointer=saver,
                artifact_store=_SummaryArtifactStore({}),
                settings=CheckpointRetentionSettings(orphan_thread_hours=1),
                now=datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(report.deleted_thread_ids, ("crashed-old",))
            self.assertEqual(report.retained_thread_count, 1)
            remaining = {
                row[0]
                for row in connection.execute("SELECT DISTINCT thread_id FROM checkpoints")
            }
            self.assertEqual(remaining, {"crashed-recent"})
            connection.close()

    # Terminal checkpoints default to immediate cleanup after their audit artifacts exist.
    def test_default_retention_keeps_only_resumable_threads(self) -> None:
        with patch.dict(
            "os.environ",
            {"LAYER2_COMPLETED_THREAD_RETENTION_DAYS": "0"},
        ):
            settings = CheckpointRetentionSettings.from_env()

        self.assertEqual(settings.completed_thread_days, 0)


if __name__ == "__main__":
    unittest.main()
