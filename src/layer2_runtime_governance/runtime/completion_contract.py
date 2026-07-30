from __future__ import annotations

from layer1_intake import ExecutionIntent

from ..actor_contract import ActorResult
from ..domain import (
    HumanReviewDecisionType,
    PlanStepStatus,
    RuntimeFlag,
    TaskState,
)
from ..external_operations import ExternalExecutionStatus


# Require an execution receipt only when Layer 1 recorded an explicit execution request.
def terminal_execution_contract_satisfied(state: TaskState) -> bool:
    if not terminal_plan_contract_satisfied(state):
        return False
    if state.execution_intent != ExecutionIntent.EXECUTE_AFTER_GOVERNANCE:
        return bool(state.pending_completion_candidate is None and state.executed_steps)
    proposal = state.external_operation_proposal
    if proposal is None:
        return False
    if any(
        receipt.status == ExternalExecutionStatus.EXECUTED
        and receipt.operation_id == proposal.operation_id
        and receipt.capability_id == proposal.capability_id
        and receipt.payload_digest == proposal.payload_digest
        for receipt in state.external_execution_history
    ):
        return True
    return _indeterminate_execution_was_human_reconciled(state)


# Require every completed runtime to exhaust its cursor and close every semantic step.
def terminal_plan_contract_satisfied(state: TaskState) -> bool:
    return bool(
        state.current_plan_step_index is None
        and state.pending_completion_candidate is None
        and state.plan_steps
        and all(
            step.status in {PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED}
            for step in state.plan_steps
        )
    )


# Accept unknown connector outcome only after explicit digest-bound human reconciliation.
def _indeterminate_execution_was_human_reconciled(state: TaskState) -> bool:
    receipt = state.last_external_execution_receipt
    decision = state.last_human_review_decision
    return bool(
        receipt is not None
        and receipt.status == ExternalExecutionStatus.INDETERMINATE
        and RuntimeFlag.EXTERNAL_EXECUTION_RECONCILIATION_REQUIRED
        in state.runtime_flags
        and decision is not None
        and decision.decision_type == HumanReviewDecisionType.APPROVE_CONTINUE
        and decision.approved_operation_digest == receipt.payload_digest
    )


# Detect a terminal local completion that omitted the external candidate required by the task.
def terminal_actor_completion_omits_external_candidate(
    state: TaskState,
    *,
    actor_result: ActorResult,
) -> bool:
    if state.execution_intent != ExecutionIntent.EXECUTE_AFTER_GOVERNANCE:
        return False
    if state.current_plan_step_index != len(state.plan_steps) - 1:
        return False
    if terminal_execution_contract_satisfied(state):
        return False
    return (
        actor_result.external_operation_proposal is None
        and actor_result.external_communication_draft is None
        and actor_result.database_statement_draft is None
    )


__all__ = [
    "terminal_actor_completion_omits_external_candidate",
    "terminal_execution_contract_satisfied",
    "terminal_plan_contract_satisfied",
]
