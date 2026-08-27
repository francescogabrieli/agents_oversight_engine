from typing import Any

from layer2_runtime_governance.actor_contract import ActorResultType
from layer2_runtime_governance.governance_components import PolicyEvaluationInput
from layer2_runtime_governance.domain import TaskState, ToolDisposition
from layer2_runtime_governance.judge import JudgeAssessmentStatus, JudgeVerdict
from layer3_tool_bus_external_capabilities.external_operations import OperationBoundary


# Export whole-run observable facts only at a semantic or operational boundary.
def build_trajectory_signal(*, task_state: TaskState, policy_input: PolicyEvaluationInput, thresholds_version: str) -> dict[str, Any]:
    result = task_state.actor_result
    # Reuse the canonical backend/local split so this pillar and the tool guards can
    # never disagree about what counted as a real capability call.
    executed_attempts = [
        attempt
        for attempt in task_state.tool_attempts
        if attempt.disposition.reached_backend
    ]
    if task_state.tool_attempts:
        successful_tool_calls = sum(
            1
            for attempt in executed_attempts
            if attempt.disposition == ToolDisposition.EXECUTED_SUCCESS
        )
        failed_tool_calls = len(executed_attempts) - successful_tool_calls
        synthetic_tool_results = len(task_state.tool_attempts) - len(executed_attempts)
    else:
        # Legacy checkpoints predate typed attempts; their persisted results all
        # represented backend calls, so retain that replay interpretation.
        successful_tool_calls = sum(
            1 for item in task_state.tool_results_history if item.success
        )
        failed_tool_calls = len(task_state.tool_results_history) - successful_tool_calls
        synthetic_tool_results = 0
    replan_count = sum(
        1 for item in task_state.step_history if item.node_name == "replanner"
    )
    current_review_count = len(task_state.human_review_history)
    # A completed human intervention closes the prior replanning episode. Each
    # checkpoint records the review count at installation, so the active episode
    # can be reconstructed without resetting or hiding the whole-run counter.
    episode_replans = [
        checkpoint
        for checkpoint in task_state.replan_checkpoints
        if checkpoint.human_review_count == current_review_count
    ]
    replan_episode_count = len(episode_replans)
    last_replan = episode_replans[-1] if episode_replans else None
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
    if task_state.tool_attempts:
        current_epoch_attempts = [
            attempt
            for attempt in executed_attempts
            if attempt.progress_epoch == task_state.progress_epoch
        ]
        for attempt in reversed(current_epoch_attempts):
            if attempt.disposition != ToolDisposition.EXECUTED_SUCCESS:
                consecutive_tool_failures += 1
            else:
                break
    elif not task_state.human_review_history:
        for item in reversed(task_state.tool_results_history):
            if not item.success:
                consecutive_tool_failures += 1
            else:
                break

    action_churn_count = _current_operation_churn_count(task_state=task_state)
    # Read-only exploration had no ceiling at all: searches that succeed feed no failure
    # counter, and turn counters are observational by design, so a step could be worked
    # indefinitely without any rule seeing it. Every attempt on the current step counts,
    # including locally rejected ones, because they consume the same budget.
    current_step = task_state.current_plan_step
    current_step_tool_attempts = (
        sum(
            1
            for attempt in task_state.tool_attempts
            if attempt.plan_step_id == current_step.step_id
        )
        if current_step is not None
        else 0
    )
    unresolvable_revision = _judge_revision_has_no_new_observational_basis(
        task_state=task_state,
    )

    raw_facts = {
        "actor_turn_count": task_state.actor_turn_count,
        "progress_epoch": task_state.progress_epoch,
        "completed_step_count": len(task_state.executed_steps),
        "tool_call_count": task_state.tool_call_count,
        "successful_tool_call_count": successful_tool_calls,
        "failed_tool_call_count": failed_tool_calls,
        "synthetic_tool_result_count": synthetic_tool_results,
        "consecutive_tool_failures": consecutive_tool_failures,
        "action_churn_count": action_churn_count,
        "current_step_tool_attempt_count": current_step_tool_attempts,
        "observation_count": len(task_state.observations),
        "replan_count": replan_count,
        "replan_episode_count": replan_episode_count,
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
            and task_state.external_operation_proposal.boundary
            == OperationBoundary.GOVERNED_EXTERNAL
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
        "unresolvable_revision": unresolvable_revision,
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


# Detect a repeated needs_revision verdict without turning the fact into a Python directive.
def _judge_revision_has_no_new_observational_basis(*, task_state: TaskState) -> bool:
    proposal = task_state.external_operation_proposal
    latest_judge = task_state.last_judge_assessment
    if (
        proposal is None
        or latest_judge is None
        or latest_judge.status != JudgeAssessmentStatus.ACTIVE
        or latest_judge.verdict != JudgeVerdict.NEEDS_REVISION
        or latest_judge.evaluated_operation_digest != proposal.payload_digest
    ):
        return False
    current_basis = (
        task_state.tool_call_count,
        len(task_state.observations),
        len(task_state.executed_steps),
    )
    for decision in reversed(task_state.governance_history):
        if decision.evaluated_operation_digest != proposal.payload_digest:
            continue
        judge_result = (decision.pillar_results or {}).get("judge", {})
        trajectory_result = (decision.pillar_results or {}).get("trajectory", {})
        judge_facts = judge_result.get("raw_facts", judge_result)
        trajectory_facts = trajectory_result.get("raw_facts", trajectory_result)
        if not isinstance(judge_facts, dict) or not isinstance(trajectory_facts, dict):
            continue
        if judge_facts.get("verdict") != JudgeVerdict.NEEDS_REVISION.value:
            continue
        prior_basis = (
            int(trajectory_facts.get("tool_call_count", 0)),
            int(trajectory_facts.get("observation_count", 0)),
            int(trajectory_facts.get("completed_step_count", 0)),
        )
        return current_basis == prior_basis
    return False


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


# Count oscillation only inside the current operation and post-review edit episode.
def _current_operation_churn_count(*, task_state: TaskState) -> int:
    current_proposal = task_state.external_operation_proposal
    if (
        current_proposal is None
        or not current_proposal.execution_candidate
        or current_proposal.boundary != OperationBoundary.GOVERNED_EXTERNAL
    ):
        return 0

    operation_history = [
        proposal
        for proposal in task_state.external_operation_history
        if proposal.operation_id == current_proposal.operation_id
        and proposal.execution_candidate
        and proposal.boundary == OperationBoundary.GOVERNED_EXTERNAL
    ]
    if (
        not operation_history
        or operation_history[-1].payload_digest != current_proposal.payload_digest
    ):
        operation_history.append(current_proposal)

    # A normalized human edit is an explicit correction boundary and starts a
    # fresh churn episode for the same logical operation.
    episode_start = 0
    for index, proposal in enumerate(operation_history):
        if proposal.facts_provenance.source_kind == "human_review_edit":
            episode_start = index

    seen_digests: list[str] = []
    churn_count = 0
    for proposal in operation_history[episode_start:]:
        digest = proposal.payload_digest
        if digest in seen_digests and seen_digests[-1] != digest:
            churn_count += 1
        seen_digests.append(digest)
    return churn_count
