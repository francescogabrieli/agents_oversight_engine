from typing import Any

from layer2_runtime_governance.actor_contract import ActorResultType
from layer2_runtime_governance.domain import TaskState
from layer2_runtime_governance.tool_contract import EvidenceContentState, Extractability


# Build concrete observed facts; OPA decides whether the event needs evidence and how to react.
def build_evidence_signal(*, task_state: TaskState, thresholds_version: str) -> dict[str, Any]:
    result = task_state.actor_result
    validation = result.step_validation if result is not None else None
    selected_refs = validation.evidence_refs if validation is not None else ()
    evidence_by_id = {item.evidence_id: item for item in task_state.observed_evidence}
    selected = [evidence_by_id[item] for item in selected_refs if item in evidence_by_id]
    factual_completion = bool(result and result.result_type in {ActorResultType.STEP_COMPLETED, ActorResultType.FINAL_OUTPUT})
    raw_facts = {
        "evidence_count": len(task_state.observed_evidence),
        "selected_evidence_ref_count": len(selected_refs),
        "selected_evidence_refs_valid": len(selected) == len(selected_refs),
        "source_locator_present": any(item.locator.strip() for item in selected),
        "cited_context_present": any(item.citation_alignment.value != "none" for item in selected),
        "extracted_content_present": any(item.content_state in {EvidenceContentState.EXTRACTED_PARTIAL, EvidenceContentState.EXTRACTED_FULL} for item in selected),
        "official_source_present": any(item.source_authority.value == "official_primary" for item in selected),
        "extraction_truncated": any(item.extractability == Extractability.TRUNCATED for item in selected),
        "extractability_limited": any(item.extractability in {Extractability.FORBIDDEN, Extractability.EMPTY, Extractability.UNAVAILABLE} for item in task_state.observed_evidence),
        "factual_completion_attempt": factual_completion,
        "external_operation_candidate": bool(task_state.external_operation_proposal and task_state.external_operation_proposal.execution_candidate),
        "actor_self_confidence": validation.actor_self_confidence if validation is not None else None,
    }
    return {
        "raw_facts": raw_facts,
        "diagnostic_score": None,
        "diagnostic_level": "unscored",
        "score_components": {},
        "reason_facts": [],
        "summary": "Concrete evidence observations collected; OPA owns the directive.",
        "score_formula_version": "evidence_facts_v3",
        "thresholds_version": thresholds_version,
    }
