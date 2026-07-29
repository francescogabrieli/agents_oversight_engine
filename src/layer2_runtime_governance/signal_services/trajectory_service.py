from typing import Any

from layer2_runtime_governance.actor_contract import ActorResultType
from layer2_runtime_governance.governance_components import PolicyEvaluationInput
from layer2_runtime_governance.domain import TaskState


# Export whole-run observable facts only at a semantic or operational boundary.
def build_trajectory_signal(*, task_state: TaskState, policy_input: PolicyEvaluationInput, thresholds_version: str) -> dict[str, Any]:
    result = task_state.actor_result
    successful_tool_calls = sum(
        1 for item in task_state.tool_results_history if item.success
    )
    failed_tool_calls = len(task_state.tool_results_history) - successful_tool_calls
    replan_count = sum(
        1 for item in task_state.step_history if item.node_name == "replanner"
    )
    last_replan = (
        task_state.replan_checkpoints[-1]
        if task_state.replan_checkpoints
        else None
    )
    # A new observable basis means new execution events since the checkpoint.
    # progress_epoch and plan_signature are bumped by the replanner itself, so
    # comparing them would let every replan self-admit the next one.
    observable_change_since_last_replan = bool(
        last_replan is None
        or len(task_state.tool_results_history) > getattr(last_replan, "tool_result_count", 0)
        or len(task_state.observations) > getattr(last_replan, "observation_count", 0)
        or len(task_state.executed_steps) > getattr(last_replan, "completed_step_count", 0)
        or len(task_state.human_review_history) > getattr(last_replan, "human_review_count", 0)
    )
    raw_facts = {
        "actor_turn_count": task_state.actor_turn_count,
        "progress_epoch": task_state.progress_epoch,
        "completed_step_count": len(task_state.executed_steps),
        "tool_call_count": task_state.tool_call_count,
        "successful_tool_call_count": successful_tool_calls,
        "failed_tool_call_count": failed_tool_calls,
        "observation_count": len(task_state.observations),
        "replan_count": replan_count,
        "consecutive_provider_protocol_failures": task_state.consecutive_provider_protocol_failures,
        "needs_replan_signal": bool(
            result and result.result_type == ActorResultType.NEEDS_REPLAN
        ),
        "replan_reason_present": bool(
            result
            and result.blocking_reason
            and result.blocking_reason.strip()
        ),
        "first_replan_request": last_replan is None,
        "observable_change_since_last_replan": (
            observable_change_since_last_replan
        ),
        "completion_signal": bool(
            result and result.result_type == ActorResultType.STEP_COMPLETED
        ),
        "external_candidate": bool(
            task_state.external_operation_proposal
            and task_state.external_operation_proposal.execution_candidate
        ),
        # Count revisions per operation; unrelated proposals never consume this cycle's budget.
        "external_action_revision_count": sum(
            1
            for feedback in task_state.governance_action_feedback
            if task_state.external_operation_proposal is not None
            and feedback.operation_id == task_state.external_operation_proposal.operation_id
        ),
        "actor_self_confidence": result.step_validation.actor_self_confidence if result and result.step_validation else None,
        "configured_confidence_threshold": task_state.execution_policy.confidence_threshold,
    }
    return {
        "raw_facts": raw_facts,
        "diagnostic_score": None,
        "diagnostic_level": "observed",
        "score_components": {},
        "reason_facts": [],
        "summary": (
            "Whole-run trajectory facts collected at a semantic or operational boundary."
        ),
        "score_formula_version": "trajectory_facts_v5",
        "thresholds_version": thresholds_version,
    }
