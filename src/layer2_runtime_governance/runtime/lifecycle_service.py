from copy import deepcopy

from layer1_intake import IntakeResult

from ..domain import RuntimeState, StepRecord, TaskState

# Nodes.py contains the executable Layer 2 business logic.
# At this stage the runtime performs bootstrap, planning, and the actor/tool loop.

# Maintain explicit lifecycle state and audit records.
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

# Maintain explicit lifecycle state and audit records.
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

# Maintain explicit lifecycle state and audit records.
def finalize_bootstrap(state: TaskState) -> TaskState:
    # Close the current runtime slice after the planner/actor/tool loop reaches a stable stop point.
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

# Maintain explicit lifecycle state and audit records.
def _build_finalize_summary(state: TaskState) -> str:
    if state.actor_result is None:
        if state.actor_turn_count > 0 or state.executed_steps:
            return (
                "Layer 2 reached a stable stop after actor execution; "
                "the latest actor projection was unavailable during recovery."
            )
        return "Layer 2 stopped before actor execution because no operational outcome was available."

    if state.last_external_execution_receipt is not None:
        receipt = state.last_external_execution_receipt
        return (
            "Layer 2 finalized the governed external-operation path with "
            f"execution status '{receipt.status.value}', capability "
            f"'{receipt.capability_id}', and runtime state '{state.current_state.value}'."
        )

    if state.current_state == RuntimeState.ABORTED:
        return (
            "Layer 2 stopped after governance denied the actor outcome "
            f"'{state.actor_result.result_type.value}'."
        )

    return (
        "Layer 2 stopped after the planner/actor/tool loop "
        f"with actor result '{state.actor_result.result_type.value}', "
        f"runtime state '{state.current_state.value}', "
        f"actor turns {state.actor_turn_count}, "
        f"and tool calls {state.tool_call_count}."
    )

__all__ = [
    "bootstrap_from_intake",
    "_append_step_record",
    "finalize_bootstrap",
    "_build_finalize_summary"
]
