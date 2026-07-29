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
    unique_selected = tuple(dict.fromkeys(selected_refs))
    available_obs_ids = {item.observation_id for item in task_state.observations}
    selected_ref_ratio = (
        len(unique_selected) / len(available_obs_ids)
        if available_obs_ids
        else 0.0
    )

    obs_tool_map = {}
    for tr in task_state.tool_results_history:
        tool_name = tr.request.tool_name.value if hasattr(tr.request.tool_name, "value") else str(tr.request.tool_name)
        for obs in tr.observations:
            obs_tool_map[obs.observation_id] = tool_name

    search_title_only_ref_count = 0
    for item in selected:
        tool_name = obs_tool_map.get(item.observation_id)
        if (
            tool_name == "web_search"
            or item.content.startswith("Search result title:")
            or (item.locator and item.locator.startswith("search:"))
        ):
            search_title_only_ref_count += 1

    raw_facts = {
        "observation_count": len(task_state.observations),
        "selected_evidence_ref_count": len(selected_refs),
        "selected_evidence_ref_ratio": selected_ref_ratio,
        "selected_ref_ratio": selected_ref_ratio,
        "selected_evidence_refs_valid": len(selected) == len(selected_refs),
        "selected_observations_have_content": all(
            item.content.strip() for item in selected
        ) if selected else True,
        "selected_search_title_only_count": search_title_only_ref_count,
        "search_title_only_ref_count": search_title_only_ref_count,
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
        "score_formula_version": "evidence_facts_v4",
        "thresholds_version": thresholds_version,
    }
