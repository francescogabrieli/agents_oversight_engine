from __future__ import annotations

from copy import deepcopy

from layer1_intake import IntakeResult

from .actor_contract import ActorPort, ActorResult, ActorResultType, build_actor_task
from .planning import PlannerPort
from .state import ExecutedPlanStep, PlanStepStatus, RuntimeState, StepRecord, TaskState


# Nodes.py contains the executable Layer 2 business logic.
# At this stage the runtime performs bootstrap, planning, and one actor execution step.
def bootstrap_from_intake(intake_result: IntakeResult) -> TaskState:
    # Build the first TaskState and record that the bootstrap node executed.
    state = TaskState.from_intake(intake_result)
    state.step_history.append(
        StepRecord(
            step_index=0,
            runtime_state=state.current_state,
            node_name="bootstrap_from_intake",
            summary="Runtime state initialized from layer 1 intake payload.",
        )
    )
    return state


# Build the initial semantic full plan unless the task already requires human clarification.
def planner(state: TaskState, *, planner_service: PlannerPort) -> TaskState:
    if state.current_state != RuntimeState.PLANNING:
        _append_step_record(
            state,
            node_name="planner",
            summary="Planning skipped because the runtime is not currently in a planning state.",
        )
        return state

    decision = planner_service.plan(task_state=state)
    state.plan_steps = decision.plan_steps
    state.current_plan_step_index = 0 if decision.plan_steps else None
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


# Ask the actor to execute the current semantic plan step.
def actor(state: TaskState, *, actor_service: ActorPort) -> TaskState:
    if state.current_state != RuntimeState.PLANNING or state.current_plan_step is None:
        _append_step_record(
            state,
            node_name="actor",
            summary="Actor skipped because there is no executable plan step in a planning state.",
        )
        return state

    actor_task = build_actor_task(task_state=state)
    actor_result = actor_service.act(actor_task=actor_task)

    state.actor_result = actor_result
    state.actor_name = actor_service.actor_name
    state.actor_prompt_version = actor_service.prompt_version
    _record_actor_execution(state, actor_result=actor_result)
    state.current_state = _map_actor_result_to_runtime_state(state, actor_result=actor_result)

    _append_step_record(
        state,
        node_name="actor",
        summary="Actor executed the current semantic step and returned one typed execution outcome.",
    )
    return state


def finalize_bootstrap(state: TaskState) -> TaskState:
    # Close the current Layer 2 slice after the first actor execution step.
    next_state = deepcopy(state)
    next_state.bootstrap_completed = True
    next_state.step_index += 1
    next_state.step_history.append(
        StepRecord(
            step_index=next_state.step_index,
            runtime_state=next_state.current_state,
            node_name="finalize_bootstrap",
            summary=_build_finalize_summary(next_state),
        )
    )
    return next_state


# Keep step history mutation consistent across nodes after bootstrap.
def _append_step_record(state: TaskState, *, node_name: str, summary: str) -> None:
    state.step_index += 1
    state.step_history.append(
        StepRecord(
            step_index=state.step_index,
            runtime_state=state.current_state,
            node_name=node_name,
            summary=summary,
        )
    )


# Persist the local execution trace for the current step before the graph stops.
def _record_actor_execution(state: TaskState, *, actor_result: ActorResult) -> None:
    current_step = state.current_plan_step
    if current_step is None:
        raise ValueError("Cannot record actor execution without a current plan step.")

    if actor_result.plan_step_id != current_step.step_id:
        raise ValueError("actor_result.plan_step_id does not match the current plan step.")

    if actor_result.result_type in {
        ActorResultType.ASK_CLARIFICATION,
        ActorResultType.BLOCKED,
        ActorResultType.NEEDS_REPLAN,
    }:
        current_step.status = PlanStepStatus.BLOCKED
        return

    if actor_result.result_type == ActorResultType.PROPOSE_ACTION:
        current_step.status = PlanStepStatus.IN_PROGRESS
        return

    current_step.status = PlanStepStatus.COMPLETED
    state.executed_steps.append(
        ExecutedPlanStep(
            step_id=current_step.step_id,
            objective=current_step.objective,
            result_type=actor_result.result_type,
            summary=actor_result.summary,
            output_text=actor_result.output_text,
        )
    )

    if actor_result.result_type == ActorResultType.FINAL_OUTPUT:
        state.final_output_text = actor_result.output_text
        state.current_plan_step_index = None
        return

    _advance_to_next_plan_step(state)


# Advance the runtime cursor to the next semantic step after a completed local execution.
def _advance_to_next_plan_step(state: TaskState) -> None:
    current_index = state.current_plan_step_index
    if current_index is None:
        return

    for next_index in range(current_index + 1, len(state.plan_steps)):
        next_step = state.plan_steps[next_index]
        if next_step.status == PlanStepStatus.PENDING:
            next_step.status = PlanStepStatus.IN_PROGRESS
            state.current_plan_step_index = next_index
            return

    state.current_plan_step_index = None


# Convert actor result categories into explicit runtime outcomes for the current baseline.
def _map_actor_result_to_runtime_state(
    state: TaskState,
    *,
    actor_result: ActorResult,
) -> RuntimeState:
    if actor_result.result_type == ActorResultType.NEEDS_REPLAN:
        return RuntimeState.REPLANNING

    if actor_result.result_type in {
        ActorResultType.ASK_CLARIFICATION,
        ActorResultType.BLOCKED,
    }:
        return RuntimeState.WAITING_HUMAN_REVIEW

    if actor_result.result_type == ActorResultType.PROPOSE_ACTION:
        return RuntimeState.READY_TOOL_EXECUTION

    if actor_result.result_type == ActorResultType.FINAL_OUTPUT:
        return RuntimeState.COMPLETED

    if state.current_plan_step_index is None:
        return RuntimeState.COMPLETED

    return RuntimeState.READY_NEXT_STEP


# Keep the closing trace message aligned with the actual endpoint reached by the actor.
def _build_finalize_summary(state: TaskState) -> str:
    if state.actor_result is None:
        return "Layer 2 stopped before actor execution because no operational proposal was available."

    return (
        "Layer 2 stopped after the first actor execution step "
        f"with actor result '{state.actor_result.result_type.value}' "
        f"and runtime state '{state.current_state.value}'."
    )


__all__ = [
    "actor",
    "bootstrap_from_intake",
    "finalize_bootstrap",
    "planner",
]
