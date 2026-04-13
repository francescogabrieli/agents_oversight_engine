from __future__ import annotations

from copy import deepcopy
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import Literal, TypedDict

from layer1_intake import (
    DomainType,
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskSpec,
)

from .nodes import (
    apply_human_decision,
    bootstrap_from_intake,
    build_human_review_payload,
    coerce_human_decision,
    finalize,
    governance_router,
    observe_tool_result,
    planner,
    replanner,
    tool_gateway,
)
from .state import (
    GovernanceDecision,
    GovernanceDecisionType,
    HumanDecision,
    HumanDecisionType,
    ProposedAction,
    RuntimeState,
    StepRecord,
    TaskState,
)

# Graph.py is the only file that should know about LangGraph wiring details.
# Nodes stay in nodes.py, the shared state stays in state.py, and this file
# connects them into an executable runtime.

class Layer2GraphInput(TypedDict):
    intake_result: IntakeResult


class Layer2GraphState(Layer2GraphInput, total=False):
    task_state: TaskState


class Layer2GraphOutput(TypedDict):
    task_state: TaskState


def build_runtime_graph(*, checkpointer: Any | None = None):
    # Build the compact Layer 2 graph.
    # The graph uses a small wrapper schema around TaskState so we can keep
    # the runtime domain model clean and separate from LangGraph internals.
    builder = StateGraph(
        Layer2GraphState,
        input_schema=Layer2GraphInput,
        output_schema=Layer2GraphOutput,
    )

    builder.add_node("bootstrap_from_intake", _bootstrap_node)
    builder.add_node("planner", _planner_node)
    builder.add_node("governance_router", _governance_node)
    builder.add_node("tool_gateway", _tool_gateway_node)
    builder.add_node("observe_tool_result", _observe_tool_result_node)
    builder.add_node("replanner", _replanner_node)
    builder.add_node("human_review", _human_review_node)
    builder.add_node("finalize", _finalize_node)

    builder.add_edge(START, "bootstrap_from_intake")
    builder.add_conditional_edges(
        "bootstrap_from_intake",
        _route_after_bootstrap,
        {
            "planner": "planner",
            "human_review": "human_review",
            "end": END,
        },
    )
    builder.add_edge("planner", "governance_router")
    builder.add_conditional_edges(
        "governance_router",
        _route_after_governance,
        {
            "tool_gateway": "tool_gateway",
            "replanner": "replanner",
            "human_review": "human_review",
            "finalize": "finalize",
            "end": END,
        },
    )
    builder.add_edge("tool_gateway", "observe_tool_result")
    builder.add_edge("observe_tool_result", "finalize")
    builder.add_edge("replanner", "governance_router")
    builder.add_conditional_edges(
        "human_review",
        _route_after_human_review,
        {
            "tool_gateway": "tool_gateway",
            "replanner": "replanner",
            "human_review": "human_review",
            "finalize": "finalize",
            "end": END,
        },
    )
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer or _build_default_checkpointer())


def _bootstrap_node(state: Layer2GraphInput) -> dict[str, TaskState]:
    # Convert Layer 1 output into runtime state.
    return {"task_state": bootstrap_from_intake(state["intake_result"])}


def _planner_node(state: Layer2GraphState) -> dict[str, TaskState]:
    # Produce the next action proposal.
    return {"task_state": planner(state["task_state"])}


def _governance_node(state: Layer2GraphState) -> dict[str, TaskState]:
    # Convert the proposal into a governance decision.
    return {"task_state": governance_router(state["task_state"])}


def _tool_gateway_node(state: Layer2GraphState) -> dict[str, TaskState]:
    # Prepare the request that Layer 3 will execute.
    return {"task_state": tool_gateway(state["task_state"])}


def _observe_tool_result_node(state: Layer2GraphState) -> dict[str, TaskState]:
    # Persist the latest tool output as evidence.
    return {"task_state": observe_tool_result(state["task_state"])}


def _replanner_node(state: Layer2GraphState) -> dict[str, TaskState]:
    # Build the next action after evidence or review.
    return {"task_state": replanner(state["task_state"])}


def _human_review_node(state: Layer2GraphState) -> dict[str, TaskState]:
    # LangGraph interrupt pauses the graph and exposes the review payload.
    # On resume, the human decision is normalized and applied to the state.
    task_state = state["task_state"]
    resume_value = interrupt(build_human_review_payload(task_state))
    next_state = _copy_task_state(task_state)
    next_state.human_decision = coerce_human_decision(resume_value)
    return {"task_state": apply_human_decision(next_state)}


def _finalize_node(state: Layer2GraphState) -> dict[str, TaskState]:
    # Finalize unless the run is already aborted.
    task_state = state["task_state"]
    if task_state.current_state == RuntimeState.ABORTED:
        return {"task_state": task_state}
    return {"task_state": finalize(task_state)}


def _route_after_bootstrap(
    state: Layer2GraphState,
) -> Literal["planner", "human_review", "end"]:
    # The first route depends only on the initial runtime state set by bootstrap.
    task_state = state["task_state"]
    if task_state.current_state == RuntimeState.WAITING_HUMAN_REVIEW:
        return "human_review"
    if task_state.current_state in {RuntimeState.ABORTED, RuntimeState.COMPLETED}:
        return "end"
    return "planner"


def _route_after_governance(
    state: Layer2GraphState,
) -> Literal["tool_gateway", "replanner", "human_review", "finalize", "end"]:
    # Governance decides which operational branch should execute next.
    task_state = state["task_state"]

    if task_state.current_state == RuntimeState.ACTING:
        return "tool_gateway"
    if task_state.current_state == RuntimeState.REPLANNING:
        return "replanner"
    if task_state.current_state == RuntimeState.WAITING_HUMAN_REVIEW:
        return "human_review"
    if task_state.current_state == RuntimeState.COMPLETED:
        return "finalize"
    return "end"


def _route_after_human_review(
    state: Layer2GraphState,
) -> Literal["tool_gateway", "replanner", "human_review", "finalize", "end"]:
    # After HITL the runtime resumes using the same routing rules as governance.
    task_state = state["task_state"]

    if task_state.current_state == RuntimeState.ACTING:
        return "tool_gateway"
    if task_state.current_state == RuntimeState.REPLANNING:
        return "replanner"
    if task_state.current_state == RuntimeState.WAITING_HUMAN_REVIEW:
        return "human_review"
    if task_state.current_state == RuntimeState.COMPLETED:
        return "finalize"
    return "end"


def _copy_task_state(task_state: TaskState) -> TaskState:
    # Graph nodes also work on a copy of the domain state.
    return deepcopy(task_state)


def _build_default_checkpointer() -> MemorySaver:
    # The in-memory checkpointer is enough for local development and tests.
    # We explicitly allow our custom models so checkpoint deserialization is stable.
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[
            DomainType,
            ExecutionPolicy,
            GovernanceDecision,
            GovernanceDecisionType,
            HumanDecision,
            HumanDecisionType,
            IntakeMetadata,
            IntakeResult,
            ParseStatus,
            ProposedAction,
            RuntimeState,
            SensitivityLevel,
            StepRecord,
            StructuredTask,
            TaskSpec,
            TaskState,
        ]
    )
    return MemorySaver(serde=serializer)


__all__ = [
    "Layer2GraphInput",
    "Layer2GraphOutput",
    "Layer2GraphState",
    "build_runtime_graph",
]
