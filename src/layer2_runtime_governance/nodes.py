from __future__ import annotations

from layer1_intake import IntakeResult

from .planning import PlannerPort
from .state import PlanStepStatus, RuntimeState, StepRecord, TaskState


# Nodes.py contains the executable Layer 2 business logic.
# At this stage the runtime performs bootstrap plus the initial full-plan snapshot.
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
        state.step_index += 1
        state.step_history.append(
            StepRecord(
                step_index=state.step_index,
                runtime_state=state.current_state,
                node_name="planner",
                summary="Planning skipped because the runtime is not currently in a planning state.",
            )
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

    state.step_index += 1
    state.step_history.append(
        StepRecord(
            step_index=state.step_index,
            runtime_state=state.current_state,
            node_name="planner",
            summary="Initial semantic full plan created from the current task state.",
        )
    )
    return state


def finalize_bootstrap(state: TaskState) -> TaskState:
    # Mark the current Layer 2 slice as complete without introducing actor or governance logic yet.
    next_state = TaskState(
        intake_payload=state.intake_payload,
        task_spec=state.task_spec,
        execution_policy=state.execution_policy,
        intake_metadata=state.intake_metadata,
        current_state=state.current_state,
        step_index=state.step_index + 1,
        step_history=list(state.step_history),
        runtime_flags=list(state.runtime_flags),
        bootstrap_completed=True,
        plan_steps=list(state.plan_steps),
        current_plan_step_index=state.current_plan_step_index,
        planner_rationale=state.planner_rationale,
        planner_name=state.planner_name,
        planner_prompt_version=state.planner_prompt_version,
    )
    next_state.step_history.append(
        StepRecord(
            step_index=next_state.step_index,
            runtime_state=RuntimeState.BOOTSTRAPPED,
            node_name="finalize_bootstrap",
            summary="Layer 2 stopped after bootstrap and planner snapshot as configured.",
        )
    )
    return next_state


__all__ = [
    "bootstrap_from_intake",
    "finalize_bootstrap",
    "planner",
]
