from __future__ import annotations

from ..domain import RuntimeState
from .contracts import Layer2GraphState


# Route a governed actor outcome to the next internal capability.
def route_after_governance(state: Layer2GraphState) -> str:
    task_state = state["task_state"]
    if task_state.current_state == RuntimeState.READY_EXTERNAL_EXECUTION:
        return "external_execution"
    if task_state.current_state == RuntimeState.READY_TOOL_EXECUTION:
        return "tool_gateway"
    if task_state.current_state == RuntimeState.REPLANNING:
        return "replanner"
    if (
        task_state.current_state == RuntimeState.WAITING_HUMAN_REVIEW
        and task_state.pending_human_review_request is not None
    ):
        return "human_review"
    if task_state.current_state in {RuntimeState.READY_NEXT_STEP, RuntimeState.ACTING}:
        return "actor"
    return "finalize_bootstrap"


# Route a typed review decision back into the appropriate runtime capability.
def route_after_human_review(state: Layer2GraphState) -> str:
    task_state = state["task_state"]
    route_by_state = {
        RuntimeState.PLANNING: "planner",
        RuntimeState.READY_TOOL_EXECUTION: "tool_gateway",
        RuntimeState.REPLANNING: "replanner",
        RuntimeState.READY_NEXT_STEP: "actor",
        RuntimeState.ACTING: "actor",
        RuntimeState.READY_GOVERNANCE: "governance",
        RuntimeState.READY_EXTERNAL_EXECUTION: "external_execution",
    }
    return route_by_state.get(task_state.current_state, "finalize_bootstrap")


# Route connector outcomes to a bounded retry, reconciliation review, or finalization.
def route_after_external_execution(state: Layer2GraphState) -> str:
    task_state = state["task_state"]
    if task_state.current_state == RuntimeState.READY_EXTERNAL_EXECUTION:
        return "external_execution"
    if (
        task_state.current_state == RuntimeState.WAITING_HUMAN_REVIEW
        and task_state.pending_human_review_request is not None
    ):
        return "human_review"
    return "finalize_bootstrap"


__all__ = [
    "route_after_external_execution",
    "route_after_governance",
    "route_after_human_review",
]
