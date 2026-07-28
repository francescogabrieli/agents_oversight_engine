from typing import Any

from layer2_runtime_governance.actor_contract import ActorResultType
from layer2_runtime_governance.domain import TaskState


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
    raw_facts = {
        "observation_count": len(task_state.observations),
        "selected_evidence_ref_count": len(selected_refs),
        "selected_evidence_refs_valid": len(selected) == len(selected_refs),
        "selected_observations_have_content": all(
            item.content.strip() for item in selected
        ),
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
