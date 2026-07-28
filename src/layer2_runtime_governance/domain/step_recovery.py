from __future__ import annotations

from .plan import PlanStepStatus
from .task_state import TaskState


# Restore one actor step after governance or HITL requests additional evidence.
def restore_actor_step_for_more_evidence(state: TaskState) -> None:
    actor_result = state.actor_result
    if actor_result is None:
        return
    # Rejection discards only the uncommitted candidate; completed history is immutable.
    if (
        state.pending_completion_candidate is not None
        and state.pending_completion_candidate.step_id == actor_result.plan_step_id
    ):
        state.pending_completion_candidate = None
    for index, step in enumerate(state.plan_steps):
        if step.step_id == actor_result.plan_step_id:
            step.status = PlanStepStatus.IN_PROGRESS
            state.current_plan_step_index = index
        elif step.status == PlanStepStatus.IN_PROGRESS:
            step.status = PlanStepStatus.PENDING


__all__ = ["restore_actor_step_for_more_evidence"]
