from __future__ import annotations

from layer1_intake import IntakeResult

from .state import RuntimeState, StepRecord, TaskState


# Nodes.py contains only the current executable Layer 2 business logic.
# At this stage the runtime is intentionally limited to the intake handoff
# and bootstrap into TaskState.
def bootstrap_from_intake(intake_result: IntakeResult) -> TaskState:
    # Build the first TaskState and record that the bootstrap node executed.
    # Convert the canonical Layer 1 payload into the first Layer 2 state.
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


def finalize_bootstrap(state: TaskState) -> TaskState:
    # Mark the bootstrap handoff as complete without introducing any new runtime logic.
    # The current graph stops immediately after bootstrap, so this helper
    # simply marks the state as having completed the bootstrap handoff.
    next_state = TaskState(
        intake_payload=state.intake_payload,
        task_spec=state.task_spec,
        execution_policy=state.execution_policy,
        intake_metadata=state.intake_metadata,
        current_state=state.current_state,
        step_index=state.step_index,
        step_history=list(state.step_history),
        runtime_flags=list(state.runtime_flags),
        bootstrap_completed=True,
    )
    next_state.step_history.append(
        StepRecord(
            step_index=next_state.step_index,
            runtime_state=RuntimeState.BOOTSTRAPPED,
            node_name="finalize_bootstrap",
            summary="Layer 2 stopped after bootstrap handoff as configured.",
        )
    )
    return next_state


__all__ = [
    "bootstrap_from_intake",
    "finalize_bootstrap",
]
