from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from layer1_intake import IntakeResult

from ..hitl.dashboard import (
    build_governance_hitl_dashboard_snapshot,
    governance_hitl_dashboard_snapshot_to_json,
)
from .ports import RuntimeArtifactPaths, RuntimeArtifactStorePort
from ..domain import TaskState
from ..observability import (
    build_layer2_runtime_trace,
    layer2_trace_to_json,
)


# Keep runtime persistence local and deterministic by default.
def get_default_runtime_artifacts_root() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "artifacts" / "runtime"


# Keep the default checkpoint file under the runtime artifacts root.
def get_default_checkpointer_path() -> Path:
    return get_default_runtime_artifacts_root() / "checkpoints" / "layer2_checkpoints.sqlite3"


# Create the official LangGraph SQLite saver used by every durable runtime process.
def build_sqlite_checkpointer(
    *,
    serde: JsonPlusSerializer,
    storage_path: Path | None = None,
) -> SqliteSaver:
    checkpoint_path = Path(storage_path) if storage_path is not None else get_default_checkpointer_path()
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        checkpoint_path,
        check_same_thread=False,
        timeout=30,
    )
    # Let independent CLI and dashboard processes wait briefly for SQLite write locks.
    connection.execute("PRAGMA busy_timeout = 30000")
    saver = SqliteSaver(connection, serde=serde)
    saver.setup()
    return saver


class RuntimeArtifactStore:
    # Persist trace, dashboard, and HITL artifacts per thread as first-class runtime evidence.
    def __init__(self, root_path: Path | None = None) -> None:
        self.root_path = root_path or get_default_runtime_artifacts_root()

    # Persist the current runtime surfaces for one thread and return the written paths.
    def persist(
        self,
        *,
        thread_id: str,
        intake_result: IntakeResult,
        task_state: TaskState,
    ) -> RuntimeArtifactPaths:
        paths = self.build_paths(thread_id)
        paths.thread_dir.mkdir(parents=True, exist_ok=True)

        runtime_trace = build_layer2_runtime_trace(
            thread_id=thread_id,
            intake_result=intake_result,
            task_state=task_state,
        )
        dashboard_snapshot = build_governance_hitl_dashboard_snapshot(task_state)

        paths.full_trace_path.write_text(layer2_trace_to_json(runtime_trace), encoding="utf-8")
        paths.dashboard_snapshot_path.write_text(
            governance_hitl_dashboard_snapshot_to_json(dashboard_snapshot),
            encoding="utf-8",
        )
        paths.runtime_summary_path.write_text(
            json.dumps(
                {
                    "artifact_version": "layer2_runtime_artifacts_v2",
                    "thread_id": thread_id,
                    "runtime_state": task_state.current_state.value,
                    "runtime_thread_id": task_state.runtime_thread_id,
                    "has_pending_human_review": task_state.pending_human_review_request is not None,
                    "task_goal": intake_result.structured_task.task_spec.goal,
                    "review_summary": (
                        task_state.pending_human_review_request.review_summary
                        if task_state.pending_human_review_request is not None
                        else None
                    ),
                    "pending_request_id": (
                        task_state.pending_human_review_request.request_id
                        if task_state.pending_human_review_request is not None
                        else None
                    ),
                    "allowed_decisions": (
                        [
                            decision.value
                            for decision in task_state.pending_human_review_request.allowed_decisions
                        ]
                        if task_state.pending_human_review_request is not None
                        else []
                    ),
                    "external_communication_phase": (
                        task_state.external_communication.phase.value
                        if task_state.external_communication is not None
                        else None
                    ),
                    "execution_intent": task_state.execution_intent.value,
                    "external_capability_id": (
                        task_state.external_operation_proposal.capability_id
                        if task_state.external_operation_proposal is not None
                        else None
                    ),
                    "external_execution_status": (
                        task_state.last_external_execution_receipt.status.value
                        if task_state.last_external_execution_receipt is not None
                        else None
                    ),
                    "external_execution_reference": (
                        task_state.last_external_execution_receipt.external_reference
                        if task_state.last_external_execution_receipt is not None
                        else None
                    ),
                    "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        return paths

    # Persist a recoverable diagnostic marker without fabricating a successful final state.
    def persist_failure(
        self,
        *,
        thread_id: str,
        error: Exception,
        task_state: TaskState | None,
    ) -> Path:
        paths = self.build_paths(thread_id)
        paths.thread_dir.mkdir(parents=True, exist_ok=True)
        failure_path = paths.thread_dir / "runtime_failure.json"
        failure_payload = {
            "artifact_version": "layer2_runtime_failure_v1",
            "thread_id": thread_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "runtime_state": (
                task_state.current_state.value if task_state is not None else None
            ),
            "actor_turn_count": (
                task_state.actor_turn_count if task_state is not None else None
            ),
            "tool_call_count": (
                task_state.tool_call_count if task_state is not None else None
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        failure_path.write_text(
            json.dumps(failure_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return failure_path

    # Reconstruct the canonical artifact locations for one thread.
    def build_paths(self, thread_id: str) -> RuntimeArtifactPaths:
        thread_dir = self.root_path / thread_id
        return RuntimeArtifactPaths(
            thread_dir=thread_dir,
            runtime_summary_path=thread_dir / "runtime_summary.json",
            full_trace_path=thread_dir / "full-trace-2.json",
            dashboard_snapshot_path=thread_dir / "dashboard_snapshot.json",
            pending_human_review_path=thread_dir / "pending_human_review.json",
        )

    # Expose whether persisted artifacts already exist for one thread.
    def exists(self, thread_id: str) -> bool:
        return self.build_paths(thread_id).thread_dir.exists()

    # List thread ids that already have a persisted artifact directory.
    def list_thread_ids(self) -> list[str]:
        if not self.root_path.exists():
            return []
        return sorted(path.name for path in self.root_path.iterdir() if path.is_dir())

    # Read the persisted runtime summary for one thread when available.
    def read_runtime_summary(self, thread_id: str) -> dict[str, Any] | None:
        return self._read_json_artifact(self.build_paths(thread_id).runtime_summary_path)

    # Read the persisted dashboard snapshot for one thread when available.
    def read_dashboard_snapshot(self, thread_id: str) -> dict[str, Any] | None:
        return self._read_json_artifact(self.build_paths(thread_id).dashboard_snapshot_path)

    # Read the retired standalone payload only for pre-consolidation artifact compatibility.
    def read_pending_human_review(self, thread_id: str) -> dict[str, Any] | None:
        return self._read_json_artifact(self.build_paths(thread_id).pending_human_review_path)

    # Keep artifact reads defensive so incomplete runtime writes do not break the dashboard.
    def _read_json_artifact(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None


# Persist the current runtime artifacts through the default local store.
def persist_runtime_artifacts(
    *,
    thread_id: str,
    intake_result: IntakeResult,
    task_state: TaskState,
    artifact_store: RuntimeArtifactStorePort | None = None,
) -> RuntimeArtifactPaths:
    resolved_store = artifact_store or RuntimeArtifactStore()
    return resolved_store.persist(
        thread_id=thread_id,
        intake_result=intake_result,
        task_state=task_state,
    )


__all__ = [
    "build_sqlite_checkpointer",
    "RuntimeArtifactPaths",
    "RuntimeArtifactStore",
    "get_default_checkpointer_path",
    "get_default_runtime_artifacts_root",
    "persist_runtime_artifacts",
]
