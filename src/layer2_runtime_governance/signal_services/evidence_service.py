from typing import Any

from layer2_runtime_governance.actor_contract import ActorResultType
from layer2_runtime_governance.domain import (
    TaskState,
    build_evidence_selection_digest,
    build_evidence_snapshot_manifest,
    build_observation_lineage,
    digest_json,
)


# Build concrete observed facts; OPA decides whether the event needs evidence and how to react.
def build_evidence_signal(*, task_state: TaskState, thresholds_version: str) -> dict[str, Any]:
    result = task_state.actor_result
    validation = result.step_validation if result is not None else None
    selected_refs = validation.evidence_refs if validation is not None else ()
    evidence_by_id = {
        item.observation_id: item for item in task_state.observations
    }
    selected = [evidence_by_id[item] for item in selected_refs if item in evidence_by_id]
    factual_completion = bool(
        result and result.result_type == ActorResultType.STEP_COMPLETED
    )
    unique_selected = tuple(dict.fromkeys(selected_refs))
    available_obs_ids = {item.observation_id for item in task_state.observations}
    selected_ref_ratio = (
        len(unique_selected) / len(available_obs_ids)
        if available_obs_ids
        else 0.0
    )

    observation_lineage = build_observation_lineage(task_state=task_state)

    search_title_only_ref_count = 0
    for item in selected:
        if (
            item.content.startswith("Search result title:")
            or (item.locator and item.locator.startswith("search:"))
        ):
            search_title_only_ref_count += 1

    selected_known_step_ids = [
        observation_lineage[item.observation_id].plan_step_id
        for item in selected
        if item.observation_id in observation_lineage
    ]
    # Replanning replaces live steps but never invalidates run-local acquisition lineage.
    known_step_ids = (
        {step.step_id for step in task_state.plan_steps}
        | {step.step_id for step in task_state.executed_steps}
        | {result.request.plan_step_id for result in task_state.tool_results_history}
    )
    provenance_resolvable = all(
        item.observation_id in observation_lineage
        for item in selected
    )
    known_steps = provenance_resolvable and all(
        step_id in known_step_ids for step_id in selected_known_step_ids
    )
    snapshot_manifest = build_evidence_snapshot_manifest(
        task_state=task_state,
        observation_lineage=observation_lineage,
    )
    snapshot_digest = digest_json(snapshot_manifest)
    external_operation_digest = (
        task_state.external_operation_proposal.payload_digest
        if task_state.external_operation_proposal is not None
        else None
    )
    selection_digest = build_evidence_selection_digest(
        selected_refs=tuple(selected_refs),
        evidence_snapshot_digest=snapshot_digest,
        external_operation_digest=external_operation_digest,
    )
    completion_candidate = task_state.pending_completion_candidate
    candidate_binding_required = factual_completion
    candidate_binding_present = bool(
        completion_candidate is not None
        and completion_candidate.evidence_snapshot_digest
        and completion_candidate.evidence_selection_digest
    )
    candidate_snapshot_matches = bool(
        candidate_binding_present
        and completion_candidate is not None
        and completion_candidate.evidence_snapshot_digest == snapshot_digest
    )
    candidate_selection_matches = bool(
        candidate_binding_present
        and completion_candidate is not None
        and completion_candidate.evidence_selection_digest == selection_digest
    )
    candidate_operation_matches = bool(
        completion_candidate is not None
        and completion_candidate.external_operation_digest
        == external_operation_digest
    )
    observation_ids_unique = len(available_obs_ids) == len(task_state.observations)
    raw_facts = {
        "observation_count": len(task_state.observations),
        "observation_ids_unique": observation_ids_unique,
        "selected_evidence_ref_count": len(selected_refs),
        "selected_evidence_ref_ratio": selected_ref_ratio,
        "selected_ref_ratio": selected_ref_ratio,
        "selected_evidence_refs_valid": len(selected) == len(selected_refs),
        "selected_evidence_refs_unique": len(unique_selected) == len(selected_refs),
        "selected_observations_have_content": all(
            item.content.strip() for item in selected
        ) if selected else True,
        "selected_observations_provenance_resolvable": provenance_resolvable,
        "selected_observations_belong_to_known_steps": known_steps,
        "selected_search_title_only_count": search_title_only_ref_count,
        "search_title_only_ref_count": search_title_only_ref_count,
        "selected_evidence_all_title_only": bool(selected) and search_title_only_ref_count == len(selected),
        "evidence_selection_digest": selection_digest,
        "evidence_snapshot_digest": snapshot_digest,
        "evidence_snapshot_observation_count": len(snapshot_manifest),
        "evidence_snapshot_omitted_observation_ids": [],
        "candidate_evidence_binding_required": candidate_binding_required,
        "candidate_evidence_binding_present": candidate_binding_present,
        "candidate_evidence_snapshot_matches": candidate_snapshot_matches,
        "candidate_evidence_selection_matches": candidate_selection_matches,
        "candidate_external_operation_digest_matches": candidate_operation_matches,
        "factual_completion_attempt": factual_completion,
        "external_operation_candidate": bool(task_state.external_operation_proposal and task_state.external_operation_proposal.execution_candidate),
    }
    return {
        "raw_facts": raw_facts,
        "diagnostic_score": None,
        "diagnostic_level": "unscored",
        "score_components": {},
        "reason_facts": [],
        "summary": "Concrete evidence observations collected; OPA owns the directive.",
        "score_formula_version": "evidence_integrity_provenance_v1",
        "thresholds_version": thresholds_version,
}


__all__ = ["build_evidence_signal"]
