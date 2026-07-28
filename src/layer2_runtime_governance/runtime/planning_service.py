from ..planning import PlannerPort
from ..domain import PlanStepStatus, RuntimeState, TaskState
from .lifecycle_service import _append_step_record


# Materialize only semantic planner output; runtime never drops trailing steps by slicing.
def planner(state: TaskState, *, planner_service: PlannerPort) -> TaskState:
    if state.current_state != RuntimeState.PLANNING:
        _append_step_record(state, node_name="planner", summary="Planning skipped outside planning state.")
        return state

    decision = planner_service.plan(task_state=state)
    state.plan_steps = list(decision.plan_steps)
    state.current_plan_step_index = 0 if state.plan_steps else None
    state.planner_rationale = decision.rationale
    state.planner_name = decision.planner_name
    state.planner_prompt_version = decision.prompt_version
    if state.current_plan_step is not None:
        state.current_plan_step.status = PlanStepStatus.IN_PROGRESS

    _append_step_record(
        state,
        node_name="planner",
        summary="Initial semantic full plan created from the current task state.",
    )
    return state


__all__ = ["planner"]
