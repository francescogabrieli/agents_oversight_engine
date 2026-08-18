from typing import Any

from ..actor_contract import ActorResult
from ..domain import TaskState

# PolicyDecision is the deterministic outcome returned by the policy engine.
from .contracts import PolicyEvaluationInput

# Translate runtime state into the bounded policy transport contract.
def build_policy_evaluation_input(*, task_state: TaskState) -> PolicyEvaluationInput:
    actor_result = task_state.actor_result
    if actor_result is None:
        raise ValueError("Cannot build policy evaluation input without an actor result.")

    evaluated_step = _resolve_evaluated_plan_step(task_state=task_state, actor_result=actor_result)
    current_tool_request = task_state.current_tool_request or actor_result.tool_request
    return PolicyEvaluationInput(
        task_sensitivity=task_state.task_spec.sensitivity,
        policy_profile_name=task_state.execution_policy.policy_profile_name,
        confidence_threshold=task_state.execution_policy.confidence_threshold,
        read_only_mode=task_state.execution_policy.read_only_mode,
        runtime_state=task_state.current_state,
        runtime_flags=tuple(task_state.runtime_flags),
        current_plan_step_id=evaluated_step.step_id if evaluated_step is not None else actor_result.plan_step_id,
        current_plan_step_objective=evaluated_step.objective if evaluated_step is not None else None,
        actor_result_type=actor_result.result_type,
        capability_mode=actor_result.capability_mode,
        external_impact=actor_result.external_impact,
        blocking_reason=actor_result.blocking_reason,
        actor_has_output_text=bool(actor_result.output_text and actor_result.output_text.strip()),
        tool_name=current_tool_request.tool_name if current_tool_request is not None else None,
        tool_has_query=bool(current_tool_request and current_tool_request.query),
        tool_has_url=bool(current_tool_request and current_tool_request.url),
        tool_call_count=task_state.tool_call_count,
        observation_count=len(task_state.observations),
        actor_turn_count=task_state.actor_turn_count,
        external_communication_phase=(
            task_state.external_communication.phase if task_state.external_communication is not None else None
        ),
        external_communication_review_ready=(
            task_state.external_communication.review_ready
            if task_state.external_communication is not None
            else False
        ),
    )

# Translate runtime state into the bounded policy transport contract.
def build_opa_input_document(*, policy_input: PolicyEvaluationInput) -> dict[str, Any]:
    return {
        "task": {
            "sensitivity": policy_input.task_sensitivity.value,
        },
        "policy": {
            "profile_name": policy_input.policy_profile_name,
            "confidence_threshold": policy_input.confidence_threshold,
            "read_only_mode": policy_input.read_only_mode,
        },
        "runtime": {
            "state": policy_input.runtime_state.value,
            "flags": [flag.value for flag in policy_input.runtime_flags],
            "tool_call_count": policy_input.tool_call_count,
            "observation_count": policy_input.observation_count,
            "actor_turn_count": policy_input.actor_turn_count,
        },
        "step": {
            "id": policy_input.current_plan_step_id,
            "objective": policy_input.current_plan_step_objective,
        },
        "actor": {
            "result_type": policy_input.actor_result_type.value,
            "capability_mode": policy_input.capability_mode.value,
            "external_impact": policy_input.external_impact,
            "blocking_reason": policy_input.blocking_reason,
            "has_output_text": policy_input.actor_has_output_text,
        },
        "tool": {
            "name": policy_input.tool_name.value if policy_input.tool_name is not None else None,
            "has_query": policy_input.tool_has_query,
            "has_url": policy_input.tool_has_url,
        },
        "external_communication": {
            "phase": (
                policy_input.external_communication_phase.value
                if policy_input.external_communication_phase is not None
                else None
            ),
            "review_ready": policy_input.external_communication_review_ready,
        },
    }

# Translate runtime state into the bounded policy transport contract.
def _resolve_evaluated_plan_step(
    *,
    task_state: TaskState,
    actor_result: ActorResult,
):
    for step in task_state.plan_steps:
        if step.step_id == actor_result.plan_step_id:
            return step
    return None

__all__ = [
    "build_policy_evaluation_input",
    "build_opa_input_document",
    "_resolve_evaluated_plan_step"
]
