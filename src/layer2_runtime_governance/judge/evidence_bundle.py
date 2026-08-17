from __future__ import annotations

import json
from dataclasses import asdict

from json_serialization import to_jsonable

from ..domain import digest_json
from .contracts import JudgeContext, JudgeEvidenceBundle


# Build the one canonical evidence pool shared by prompt projection and run artifacts.
def build_judge_evidence_bundle(*, context: JudgeContext) -> JudgeEvidenceBundle:
    if context.evidence_snapshot_digest is None or context.evidence_selection_digest is None:
        raise ValueError("Judge evidence bundle requires both Evidence binding digests.")
    allowed_evidence_ids = tuple(item.evidence_id for item in context.observations)
    if len(set(allowed_evidence_ids)) != len(allowed_evidence_ids):
        raise ValueError("Judge evidence bundle requires unique evidence identifiers.")
    if not set(context.selected_evidence_refs).issubset(set(allowed_evidence_ids)):
        raise ValueError("Judge evidence bundle contains an unknown selected evidence ref.")

    # The bundle digest covers exactly the model-visible pool, excluding its own digest.
    core = {
        "artifact_version": "judge_evidence_bundle.v1",
        "external_operation_digest": context.external_operation.payload_digest,
        "evidence_snapshot_digest": context.evidence_snapshot_digest,
        "evidence_selection_digest": context.evidence_selection_digest,
        "evidence_base_complete": context.evidence_base_complete,
        "observation_count": context.observation_count,
        "included_observation_count": context.included_observation_count,
        "omitted_observation_ids": list(context.omitted_observation_ids),
        "selected_evidence_refs": list(context.selected_evidence_refs),
        "allowed_evidence_ids": list(allowed_evidence_ids),
        "observations": [to_jsonable(asdict(item)) for item in context.observations],
    }
    return JudgeEvidenceBundle(
        artifact_version=str(core["artifact_version"]),
        bundle_digest=digest_json(core),
        external_operation_digest=context.external_operation.payload_digest,
        evidence_snapshot_digest=context.evidence_snapshot_digest,
        evidence_selection_digest=context.evidence_selection_digest,
        evidence_base_complete=context.evidence_base_complete,
        observation_count=context.observation_count,
        included_observation_count=context.included_observation_count,
        omitted_observation_ids=context.omitted_observation_ids,
        selected_evidence_refs=context.selected_evidence_refs,
        allowed_evidence_ids=allowed_evidence_ids,
        observations=context.observations,
    )


# Serialize the bundle without adding runtime narration or alternate evidence identifiers.
def judge_evidence_bundle_to_dict(bundle: JudgeEvidenceBundle) -> dict[str, object]:
    return to_jsonable(asdict(bundle))


# Persist and replay the exact same JSON object supplied to the semantic prompt.
def judge_evidence_bundle_to_json(bundle: JudgeEvidenceBundle) -> str:
    return json.dumps(
        judge_evidence_bundle_to_dict(bundle),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )


__all__ = [
    "build_judge_evidence_bundle",
    "judge_evidence_bundle_to_dict",
    "judge_evidence_bundle_to_json",
]
