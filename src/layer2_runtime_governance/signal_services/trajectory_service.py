from collections import Counter
from typing import Any
from urllib.parse import urlparse

from layer2_runtime_governance.actor_contract import ActorResultType
from layer2_runtime_governance.governance_components import PolicyEvaluationInput
from layer2_runtime_governance.domain import TaskState


# Export progress-sensitive facts only; OPA interprets all recovery and escalation thresholds.
def build_trajectory_signal(*, task_state: TaskState, policy_input: PolicyEvaluationInput, thresholds_version: str) -> dict[str, Any]:
    result = task_state.actor_result
    step_id = result.plan_step_id if result is not None else None
    current_refs = set(result.step_validation.evidence_refs if result and result.step_validation else ())
    prior_refs = _previous_selected_refs(task_state=task_state, step_id=step_id)
    new_evidence = bool(current_refs - prior_refs)
    tool_call = bool(result and result.tool_request is not None)
    advanced = bool(result and any(step.step_id == result.plan_step_id for step in task_state.executed_steps))
    draft_changed = _draft_digest_changed(task_state=task_state)
    progress = new_evidence or tool_call or advanced or draft_changed
    raw_facts = {
        "consecutive_no_progress_turns": state_consecutive_no_progress(task_state=task_state, progress=progress),
        "new_evidence_since_last_turn": new_evidence,
        "tool_call_since_last_turn": tool_call,
        "plan_step_advanced": advanced,
        "draft_digest_changed": draft_changed,
        "equivalent_tool_failures_for_host": _equivalent_fetch_failures(task_state=task_state),
        "consecutive_provider_protocol_failures": task_state.consecutive_provider_protocol_failures,
        "replan_budget_remaining": _replan_budget_remaining(task_state=task_state),
        # OPA owns the threshold and uses this raw counter to bound revisions.
        "external_action_revision_count": task_state.external_operation_revision_count,
        "recovery_exhausted": _replan_budget_remaining(task_state=task_state) == 0 and not progress,
        "needs_replan_signal": bool(result and result.result_type == ActorResultType.NEEDS_REPLAN),
        "actor_self_confidence": result.step_validation.actor_self_confidence if result and result.step_validation else None,
        "configured_confidence_threshold": task_state.execution_policy.confidence_threshold,
    }
    return {"raw_facts": raw_facts, "diagnostic_score": None, "diagnostic_level": "unscored", "score_components": {}, "reason_facts": [], "summary": "Progress-sensitive trajectory facts collected; OPA owns recovery.", "score_formula_version": "trajectory_facts_v3", "thresholds_version": thresholds_version}


# Use prior selected refs rather than a run-global counter so useful new evidence resets recovery pressure.
def _previous_selected_refs(*, task_state: TaskState, step_id: str | None) -> set[str]:
    validations = [item for item in task_state.step_validations if item.plan_step_id == step_id]
    if len(validations) < 2:
        return set()
    return set(validations[-2].evidence_refs)


def state_consecutive_no_progress(*, task_state: TaskState, progress: bool) -> int:
    if progress:
        return 0
    return task_state.consecutive_no_progress_turns


def _draft_digest_changed(*, task_state: TaskState) -> bool:
    proposal = task_state.external_operation_proposal
    if proposal is None:
        return False
    return not task_state.external_operation_history or task_state.external_operation_history[-1].payload_digest != proposal.payload_digest


def _equivalent_fetch_failures(*, task_state: TaskState) -> int:
    hosts = [urlparse(item.request.url or "").netloc.lower() for item in task_state.tool_results_history if not item.success and item.request.url]
    return max(Counter(hosts).values(), default=0)


def _replan_budget_remaining(*, task_state: TaskState) -> int | None:
    maximum = task_state.execution_policy.budget.max_steps
    if maximum is None:
        return None
    replans = sum(1 for item in task_state.step_history if item.node_name == "replanner")
    return max(maximum - replans, 0)
