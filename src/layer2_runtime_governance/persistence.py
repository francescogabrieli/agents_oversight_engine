from __future__ import annotations

import json
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from layer1_intake import IntakeResult

from .hitl_dashboard import (
    build_governance_hitl_dashboard_snapshot,
    governance_hitl_dashboard_snapshot_to_dict,
    governance_hitl_dashboard_snapshot_to_json,
)
from .state import TaskState
from .trace import (
    build_compact_trace_from_runtime_trace,
    build_layer2_runtime_trace,
    layer2_trace_to_json,
)


# Keep runtime persistence local and deterministic by default.
def get_default_runtime_artifacts_root() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "artifacts" / "runtime"


# Keep the default checkpoint file under the runtime artifacts root.
def get_default_checkpointer_path() -> Path:
    return get_default_runtime_artifacts_root() / "checkpoints" / "layer2_checkpoints.json"


@dataclass(frozen=True)
class RuntimeArtifactPaths:
    # Expose the main persisted files so tests and operator tooling can inspect them directly.
    thread_dir: Path
    runtime_summary_path: Path
    full_trace_path: Path
    compact_trace_path: Path
    dashboard_snapshot_path: Path
    pending_human_review_path: Path


class DiskBackedMemorySaver(InMemorySaver):
    # Persist the in-memory LangGraph saver to disk after every mutating operation.
    def __init__(
        self,
        *,
        serde: JsonPlusSerializer | None = None,
        storage_path: Path | None = None,
    ) -> None:
        super().__init__(serde=serde)
        self._serde = serde or JsonPlusSerializer()
        self.storage_path = storage_path or get_default_checkpointer_path()
        self._lock = threading.RLock()
        self._ensure_parent_dir()
        self._load_from_disk()

    # Persist checkpoint writes durably after the normal in-memory put.
    def put(self, config, checkpoint, metadata, new_versions):
        with self._lock:
            result = super().put(config, checkpoint, metadata, new_versions)
            self._sync_to_disk()
            return result

    # Persist pending writes durably after the normal in-memory write.
    def put_writes(self, config, writes, task_id, task_path=""):
        with self._lock:
            super().put_writes(config, writes, task_id, task_path)
            self._sync_to_disk()

    # Persist thread deletion durably so restarts stay in sync.
    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            super().delete_thread(thread_id)
            self._sync_to_disk()

    # Reload persisted checkpoint contents so long-lived processes can observe external writes.
    def refresh_from_disk(self) -> None:
        with self._lock:
            self.storage = defaultdict(lambda: defaultdict(dict))
            self.writes = defaultdict(dict)
            self.blobs = {}
            self._load_from_disk()

    # Load previously persisted saver contents back into the in-memory structures.
    def _load_from_disk(self) -> None:
        if not self.storage_path.exists():
            return

        raw_payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        self.storage = self._restore_storage(
            self._deserialize_storage(raw_payload.get("storage", {}))
        )
        self.writes = self._restore_writes(
            self._deserialize_writes(raw_payload.get("writes", []))
        )
        self.blobs = self._deserialize_blobs(raw_payload.get("blobs", {}))

    # Write a JSON snapshot to disk using an atomic temp file replacement.
    def _sync_to_disk(self) -> None:
        payload = {
            "storage": self._serialize_storage(self._snapshot_storage()),
            "writes": self._serialize_writes(self._snapshot_writes()),
            "blobs": self._serialize_blobs(dict(self.blobs)),
        }
        temp_path = self.storage_path.with_name(
            f"{self.storage_path.name}.{uuid4().hex}.tmp"
        )
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        temp_path.replace(self.storage_path)

    # Ensure the checkpoint parent directory exists before reads or writes.
    def _ensure_parent_dir(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert nested defaultdict storage into plain dicts for stable JSON serialization.
    def _snapshot_storage(self) -> dict[str, dict[str, dict[str, Any]]]:
        return {
            thread_id: {
                checkpoint_ns: dict(checkpoints)
                for checkpoint_ns, checkpoints in namespaces.items()
            }
            for thread_id, namespaces in self.storage.items()
        }

    # Convert nested defaultdict writes into plain dicts for stable JSON serialization.
    def _snapshot_writes(self) -> dict[tuple[str, str, str], dict[tuple[str, int], Any]]:
        return {
            outer_key: dict(entries)
            for outer_key, entries in self.writes.items()
        }

    # Rebuild the nested storage defaultdict shape expected by InMemorySaver.
    def _restore_storage(self, raw_storage: dict[str, dict[str, dict[str, Any]]]):
        restored = defaultdict(lambda: defaultdict(dict))
        for thread_id, namespaces in raw_storage.items():
            for checkpoint_ns, checkpoints in namespaces.items():
                restored[thread_id][checkpoint_ns] = dict(checkpoints)
        return restored

    # Rebuild the writes defaultdict shape expected by InMemorySaver.
    def _restore_writes(self, raw_writes: dict[tuple[str, str, str], dict[tuple[str, int], Any]]):
        restored = defaultdict(dict)
        for outer_key, entries in raw_writes.items():
            restored[outer_key] = dict(entries)
        return restored

    # Serialize storage checkpoint values to JSON-safe form via serde.
    def _serialize_storage(
        self,
        storage: dict[str, dict[str, dict[str, Any]]],
    ) -> dict[str, dict[str, dict[str, Any]]]:
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for thread_id, namespaces in storage.items():
            result[thread_id] = {}
            for ns, checkpoints in namespaces.items():
                result[thread_id][ns] = {}
                for ckpt_id, ckpt_value in checkpoints.items():
                    result[thread_id][ns][ckpt_id] = self._serialize_value(ckpt_value)
        return result

    # Deserialize storage checkpoint values back from JSON-safe form.
    def _deserialize_storage(
        self,
        raw: dict[str, dict[str, dict[str, Any]]],
    ) -> dict[str, dict[str, dict[str, Any]]]:
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for thread_id, namespaces in raw.items():
            result[thread_id] = {}
            for ns, checkpoints in namespaces.items():
                result[thread_id][ns] = {}
                for ckpt_id, ckpt_value in checkpoints.items():
                    result[thread_id][ns][ckpt_id] = self._deserialize_value(ckpt_value)
        return result

    # Serialize writes values to JSON-safe form. Keys are tuples stored as JSON arrays.
    def _serialize_writes(
        self,
        writes: dict[tuple[str, str, str], dict[tuple[str, int], Any]],
    ) -> list[Any]:
        result = []
        for outer_key, entries in writes.items():
            serialized_entries = []
            for inner_key, value in entries.items():
                serialized_entries.append({
                    "k": list(inner_key),
                    "v": self._serialize_value(value),
                })
            result.append({"k": list(outer_key), "entries": serialized_entries})
        return result

    # Deserialize writes values back from JSON-safe form. Keys are restored from JSON arrays.
    def _deserialize_writes(
        self,
        raw: list[Any],
    ) -> dict[tuple[str, str, str], dict[tuple[str, int], Any]]:
        result: dict[Any, dict[Any, Any]] = {}
        for item in raw:
            outer_key = tuple(item["k"])
            entries: dict[Any, Any] = {}
            for entry in item.get("entries", []):
                inner_key = tuple(entry["k"])
                entries[inner_key] = self._deserialize_value(entry["v"])
            result[outer_key] = entries
        return result

    # Serialize blob values to JSON-safe form. Keys are tuples stored as JSON arrays.
    def _serialize_blobs(self, blobs: dict[Any, Any]) -> list[Any]:
        result = []
        for key, value in blobs.items():
            serialized_key = list(key) if isinstance(key, tuple) else key
            result.append({"k": serialized_key, "v": self._serialize_value(value)})
        return result

    # Deserialize blob values back from JSON-safe form.
    def _deserialize_blobs(self, raw: list[Any]) -> dict[Any, Any]:
        result: dict[Any, Any] = {}
        for item in raw:
            raw_key = item["k"]
            key = tuple(raw_key) if isinstance(raw_key, list) else raw_key
            result[key] = self._deserialize_value(item["v"])
        return result

    # Serialize one arbitrary checkpoint value to a JSON-safe envelope using serde.
    def _serialize_value(self, value: Any) -> Any:
        if value is None:
            return None
        try:
            type_name, serialized = self._serde.dumps_typed(value)
            if isinstance(serialized, bytes):
                import base64
                return {"__serde_type__": type_name, "__serde_bytes__": base64.b64encode(serialized).decode("ascii")}
            return {"__serde_type__": type_name, "__serde_str__": serialized}
        except Exception:
            return value

    # Deserialize one checkpoint value from its JSON-safe envelope using serde.
    def _deserialize_value(self, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "__serde_type__" not in value:
            return value
        type_name = value["__serde_type__"]
        try:
            if "__serde_bytes__" in value:
                import base64
                raw = base64.b64decode(value["__serde_bytes__"])
                return self._serde.loads_typed((type_name, raw))
            if "__serde_str__" in value:
                return self._serde.loads_typed((type_name, value["__serde_str__"]))
        except Exception:
            pass
        return value


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
        compact_trace = build_compact_trace_from_runtime_trace(runtime_trace)
        dashboard_snapshot = build_governance_hitl_dashboard_snapshot(task_state)
        dashboard_snapshot_dict = governance_hitl_dashboard_snapshot_to_dict(dashboard_snapshot)
        pending_request = (
            dashboard_snapshot_dict.get("human_review", {}).get("pending_request")
            if task_state.pending_human_review_request is not None
            else None
        )

        paths.full_trace_path.write_text(layer2_trace_to_json(runtime_trace), encoding="utf-8")
        paths.compact_trace_path.write_text(
            json.dumps(compact_trace, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
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
                    "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        if pending_request is None:
            if paths.pending_human_review_path.exists():
                paths.pending_human_review_path.unlink()
        else:
            paths.pending_human_review_path.write_text(
                json.dumps(pending_request, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        return paths

    # Reconstruct the canonical artifact locations for one thread.
    def build_paths(self, thread_id: str) -> RuntimeArtifactPaths:
        thread_dir = self.root_path / thread_id
        return RuntimeArtifactPaths(
            thread_dir=thread_dir,
            runtime_summary_path=thread_dir / "runtime_summary.json",
            full_trace_path=thread_dir / "full-trace-2.json",
            compact_trace_path=thread_dir / "trace.compact.json",
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

    # Read the persisted pending human review payload for one thread when available.
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
    artifact_store: RuntimeArtifactStore | None = None,
) -> RuntimeArtifactPaths:
    resolved_store = artifact_store or RuntimeArtifactStore()
    return resolved_store.persist(
        thread_id=thread_id,
        intake_result=intake_result,
        task_state=task_state,
    )


__all__ = [
    "DiskBackedMemorySaver",
    "RuntimeArtifactPaths",
    "RuntimeArtifactStore",
    "get_default_checkpointer_path",
    "get_default_runtime_artifacts_root",
    "persist_runtime_artifacts",
]
