from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from .task_state import TaskState


@dataclass(frozen=True)
class ObservationLineage:
    # Lineage binds one canonical observation to the runtime action that produced it.
    request_id: str
    plan_step_id: str
    tool_name: str
    content_truncated: bool
    extractability_limited: bool


# Reconstruct canonical provenance and boundedness from immutable tool results.
def build_observation_lineage(
    *,
    task_state: TaskState,
) -> dict[str, ObservationLineage]:
    lineage: dict[str, ObservationLineage] = {}
    for result in task_state.tool_results_history:
        tool_name = (
            result.request.tool_name.value
            if hasattr(result.request.tool_name, "value")
            else str(result.request.tool_name)
        )
        for observation in result.observations:
            lineage[observation.observation_id] = ObservationLineage(
                request_id=result.request.request_id,
                plan_step_id=result.request.plan_step_id,
                tool_name=tool_name,
                content_truncated=result.truncated,
                extractability_limited=result.extractability_limited,
            )
    return lineage


# Build one order-independent manifest for the complete canonical observation universe.
def build_evidence_snapshot_manifest(
    *,
    task_state: TaskState,
    observation_lineage: dict[str, ObservationLineage] | None = None,
) -> list[dict[str, object]]:
    lineage = (
        observation_lineage
        if observation_lineage is not None
        else build_observation_lineage(task_state=task_state)
    )
    return [
        {
            "observation_id": item.observation_id,
            "content_digest": digest_text(item.content),
            "locator": item.locator,
            "retrieval_kind": getattr(item.retrieval_kind, "value", item.retrieval_kind),
            "source_timestamp": _source_timestamp_iso(
                getattr(item, "source_timestamp", None)
            ),
            "plan_step_id": (
                lineage[item.observation_id].plan_step_id
                if item.observation_id in lineage
                else None
            ),
            "request_id": (
                lineage[item.observation_id].request_id
                if item.observation_id in lineage
                else None
            ),
            "tool_name": (
                lineage[item.observation_id].tool_name
                if item.observation_id in lineage
                else None
            ),
            "content_truncated": (
                lineage[item.observation_id].content_truncated
                if item.observation_id in lineage
                else False
            ),
            "extractability_limited": (
                lineage[item.observation_id].extractability_limited
                if item.observation_id in lineage
                else False
            ),
        }
        for item in sorted(
            task_state.observations,
            key=lambda value: (
                value.observation_id,
                digest_text(value.content),
                value.locator or "",
                getattr(value.retrieval_kind, "value", value.retrieval_kind) or "",
            ),
        )
    ]


# Preserve a source timestamp in the digest without exposing a Python datetime object.
def _source_timestamp_iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


# Bind actor-declared reliance and the external payload to one evidence snapshot.
def build_evidence_selection_digest(
    *,
    selected_refs: tuple[str, ...],
    evidence_snapshot_digest: str,
    external_operation_digest: str | None,
) -> str:
    return digest_json(
        {
            "selected_refs": list(selected_refs),
            "available_snapshot_digest": evidence_snapshot_digest,
            "external_operation_digest": external_operation_digest,
        }
    )


# Hash canonical projections so policy facts can bind content without exposing it.
def digest_json(value: object) -> str:
    return digest_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


# Produce one stable UTF-8 SHA-256 digest.
def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "ObservationLineage",
    "build_evidence_selection_digest",
    "build_evidence_snapshot_manifest",
    "build_observation_lineage",
    "digest_json",
    "digest_text",
]
