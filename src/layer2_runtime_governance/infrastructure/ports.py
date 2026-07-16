from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from layer1_intake import IntakeResult

from ..domain import TaskState


@dataclass(frozen=True)
class RuntimeArtifactPaths:
    # Identify persisted artifact surfaces without prescribing local disk storage.
    thread_dir: Path
    runtime_summary_path: Path
    full_trace_path: Path
    compact_trace_path: Path
    dashboard_snapshot_path: Path
    pending_human_review_path: Path


class RuntimeArtifactStorePort(Protocol):
    # Persist one canonical runtime snapshot through an interchangeable adapter.
    def persist(
        self,
        *,
        thread_id: str,
        intake_result: IntakeResult,
        task_state: TaskState,
    ) -> RuntimeArtifactPaths: ...

    # Reconstruct adapter-specific artifact locations for one thread.
    def build_paths(self, thread_id: str) -> RuntimeArtifactPaths: ...


__all__ = ["RuntimeArtifactPaths", "RuntimeArtifactStorePort"]
