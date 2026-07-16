from __future__ import annotations

from typing import Any

from layer2_runtime_governance.actor_contract import ActorResultType
from layer2_runtime_governance.governance_components import PolicyEvaluationInput
from layer2_runtime_governance.domain import ScratchpadEntryType, TaskState


# Build trajectory counters and diagnostic components for OPA interpretation.
def build_trajectory_signal(
    *,
    task_state: TaskState,
    policy_input: PolicyEvaluationInput,
    thresholds_version: str,
) -> dict[str, Any]:
    raw_facts = {
        "actor_turn_count_for_step": task_state.actor_turn_count,
        "replan_count": _replan_count(task_state=task_state),
        "failed_tool_count": sum(1 for result in task_state.tool_results_history if not result.success),
        "needs_replan_signal": policy_input.actor_result_type == ActorResultType.NEEDS_REPLAN,
        "low_confidence_uncertainty": _low_confidence_uncertainty(task_state=task_state),
        "completion_attempt": policy_input.actor_result_type in {
            ActorResultType.INTERMEDIATE_OUTPUT,
            ActorResultType.FINAL_OUTPUT,
        },
        "provider_runtime_action_missing": (
            policy_input.actor_result_type == ActorResultType.BLOCKED
            and bool(policy_input.blocking_reason)
        ),
        "consecutive_provider_failures": _provider_failure_count(task_state=task_state),
        "repeated_recovery": _repeated_recovery_count(task_state=task_state) >= 2,
    }
    score_components = {
        "actor_turn_count_for_step": raw_facts["actor_turn_count_for_step"],
        "failed_tool_count": raw_facts["failed_tool_count"],
        "needs_replan_signal": raw_facts["needs_replan_signal"],
        "low_confidence_uncertainty": raw_facts["low_confidence_uncertainty"],
    }
    return {
        "raw_facts": raw_facts,
        "diagnostic_score": None,
        "diagnostic_level": "unscored",
        "score_components": score_components,
        "reason_facts": [],
        "summary": "Trajectory facts collected; OPA owns score and threshold interpretation.",
        "score_formula_version": "trajectory_health_v2_raw_facts",
        "thresholds_version": thresholds_version,
    }


# Count replanner node executions from the audit history.
def _replan_count(*, task_state: TaskState) -> int:
    return sum(1 for record in task_state.step_history if record.node_name == "replanner")


# Detect low-confidence validation states without converting them into a directive.
def _low_confidence_uncertainty(*, task_state: TaskState) -> bool:
    if task_state.actor_result is None or task_state.actor_result.step_validation is None:
        return False
    return task_state.actor_result.step_validation.confidence < task_state.execution_policy.confidence_threshold


# Count provider protocol failures on the current semantic step.
def _provider_failure_count(*, task_state: TaskState) -> int:
    current_step = task_state.current_plan_step
    if current_step is None:
        return 0
    return sum(
        1
        for entry in task_state.scratchpad
        if entry.plan_step_id == current_step.step_id
        and entry.entry_type == ScratchpadEntryType.UNCERTAINTY
        and entry.summary.startswith("Actor provider returned no usable runtime action")
    )


# Count repeated request-more-evidence validations on the current semantic step.
def _repeated_recovery_count(*, task_state: TaskState) -> int:
    current_step = task_state.current_plan_step
    if current_step is None:
        return 0
    return sum(
        1
        for validation in task_state.step_validations
        if validation.plan_step_id == current_step.step_id
        and validation.next_action.value == "request_more_evidence"
    )
