from __future__ import annotations

from ..domain import RuntimeState
from .contracts import Layer2GraphState


# Route a governed actor outcome to the next internal capability.
def route_after_governance(state: Layer2GraphState) -> str:
    task_state = state["task_state"]
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
    }
    return route_by_state.get(task_state.current_state, "finalize_bootstrap")


__all__ = ["route_after_governance", "route_after_human_review"]
