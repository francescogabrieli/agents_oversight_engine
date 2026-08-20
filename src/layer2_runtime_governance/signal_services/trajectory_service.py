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
    # Human interventions do not count as a new observable basis on the world.
    observable_change_since_last_replan = bool(
        last_replan is None
        or len(task_state.tool_results_history) > getattr(last_replan, "tool_result_count", 0)
        or len(task_state.observations) > getattr(last_replan, "observation_count", 0)
        or len(task_state.executed_steps) > getattr(last_replan, "completed_step_count", 0)
    )
    consecutive_tool_failures = 0
    for item in reversed(task_state.tool_results_history):
        if not item.success:
            consecutive_tool_failures += 1
        else:
            break

    seen_digests: list[str] = []
    action_churn_count = 0
    for decision in task_state.governance_history:
        d = decision.evaluated_operation_digest
        if d:
            if d in seen_digests and seen_digests[-1] != d:
                action_churn_count += 1
            seen_digests.append(d)
    if task_state.external_operation_proposal is not None:
        curr_d = task_state.external_operation_proposal.payload_digest
        if curr_d and curr_d in seen_digests and (not seen_digests or seen_digests[-1] != curr_d):
            action_churn_count += 1

    raw_facts = {
        "actor_turn_count": task_state.actor_turn_count,
        "progress_epoch": task_state.progress_epoch,
        "completed_step_count": len(task_state.executed_steps),
        "tool_call_count": task_state.tool_call_count,
        "successful_tool_call_count": successful_tool_calls,
        "failed_tool_call_count": failed_tool_calls,
        "consecutive_tool_failures": consecutive_tool_failures,
        "action_churn_count": action_churn_count,
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
        # Count revisions per operation; unrelated proposals never consume this cycle's allowance.
        "external_action_revision_count": sum(
            1
            for feedback in task_state.governance_action_feedback
            if task_state.external_operation_proposal is not None
            and feedback.operation_id == task_state.external_operation_proposal.operation_id
        ),
        "repeated_rejected_payload_digest": bool(
            task_state.external_operation_proposal
            and any(
                feedback.invalidated_payload_digest == task_state.external_operation_proposal.payload_digest
                and (
                    feedback.invalidated_evidence_selection_digest is None
                    or (
                        task_state.pending_completion_candidate is not None
                        and feedback.invalidated_evidence_selection_digest
                        == task_state.pending_completion_candidate.evidence_selection_digest
                    )
                )
                for feedback in task_state.governance_action_feedback
            )
        ),
        "unchanged_resubmission": bool(
            task_state.external_operation_proposal
            and task_state.pending_completion_candidate is not None
            and _candidate_binding_seen_in_governance(
                task_state=task_state,
                payload_digest=task_state.external_operation_proposal.payload_digest,
                evidence_selection_digest=(
                    task_state.pending_completion_candidate.evidence_selection_digest
                ),
            )
        ),
        "provider_recovery_active": bool(task_state.consecutive_provider_protocol_failures),
        "provider_protocol_retry_exhausted": bool(
            task_state.consecutive_provider_protocol_failures
            and task_state.actor_protocol_retry_count >= 1
        ),
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
        "score_formula_version": "trajectory_health_v1",
        "thresholds_version": thresholds_version,
    }


# Match a resubmission on both payload and Evidence reliance, not payload alone.
def _candidate_binding_seen_in_governance(
    *,
    task_state: TaskState,
    payload_digest: str,
    evidence_selection_digest: str | None,
) -> bool:
    if evidence_selection_digest is None:
        return False
    for decision in task_state.governance_history:
        if decision.evaluated_operation_digest != payload_digest:
            continue
        evidence = (decision.pillar_results or {}).get("evidence", {})
        if not isinstance(evidence, dict):
            continue
        raw_facts = evidence.get("raw_facts", evidence)
        if (
            isinstance(raw_facts, dict)
            and raw_facts.get("evidence_selection_digest")
            == evidence_selection_digest
        ):
            return True
    return False
