from ..planning import PlannerPort
from ..domain import (
    PlanStepStatus,
    RuntimeState,
    TaskState,
    validate_external_effect_plan_contract,
    validate_plan_steps_evidence_contract,
)
from .lifecycle_service import _append_step_record


# Materialize only semantic planner output; runtime never drops trailing steps by slicing.
def planner(state: TaskState, *, planner_service: PlannerPort) -> TaskState:
    if state.current_state != RuntimeState.PLANNING:
        _append_step_record(state, node_name="planner", summary="Planning skipped outside planning state.")
        return state

    decision = planner_service.plan(task_state=state)
    validate_plan_steps_evidence_contract(decision.plan_steps)
    validate_external_effect_plan_contract(
        steps=decision.plan_steps,
        execution_intent=state.execution_intent,
    )
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
