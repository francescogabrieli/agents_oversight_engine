from __future__ import annotations

from copy import deepcopy
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from layer1_intake import (
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskSpec,
)

from .acting import build_default_actor
from .actor_contract import (
    ActorPort,
    ActorResult,
    ActorResultType,
    CapabilityAccessMode,
)
from .nodes import actor, bootstrap_from_intake, finalize_bootstrap, planner
from .planning import PlannerPort, build_default_planner
from .state import (
    ExecutedPlanStep,
    PlanStep,
    PlanStepStatus,
    PlannerDecision,
    RuntimeFlag,
    RuntimeState,
    StepRecord,
    TaskState,
)


class Layer2GraphInput(TypedDict):
    # LangGraph receives the full IntakeResult as the only external input to Layer 2.
    intake_result: IntakeResult


class Layer2GraphState(Layer2GraphInput, total=False):
    # During execution the graph extends the input wrapper with the evolving TaskState.
    task_state: TaskState


class Layer2GraphOutput(TypedDict):
    # The current graph returns the state after bootstrap, planning, and one actor execution step.
    task_state: TaskState


def build_runtime_graph(
    *,
    checkpointer: Any | None = None,
    planner_service: PlannerPort | None = None,
    actor_service: ActorPort | None = None,
):
    # Wire the current compact Layer 2 graph and compile it into an executable LangGraph.
    resolved_planner = planner_service or build_default_planner()
    resolved_actor = actor_service or build_default_actor()

    builder = StateGraph(
        Layer2GraphState,
        input_schema=Layer2GraphInput,
        output_schema=Layer2GraphOutput,
    )

    builder.add_node("bootstrap_from_intake", _bootstrap_node)
    builder.add_node("planner", _build_planner_node(resolved_planner))
    builder.add_node("actor", _build_actor_node(resolved_actor))
    builder.add_node("finalize_bootstrap", _finalize_bootstrap_node)

    builder.add_edge(START, "bootstrap_from_intake")
    builder.add_edge("bootstrap_from_intake", "planner")
    builder.add_edge("planner", "actor")
    builder.add_edge("actor", "finalize_bootstrap")
    builder.add_edge("finalize_bootstrap", END)

    return builder.compile(checkpointer=checkpointer or _build_default_checkpointer())


def _bootstrap_node(state: Layer2GraphInput) -> dict[str, TaskState]:
    # Adapt the graph wrapper state to the domain-level bootstrap function.
    return {"task_state": bootstrap_from_intake(state["intake_result"])}


def _build_planner_node(planner_service: PlannerPort):
    # Close over the resolved planner so the graph can remain deterministic in tests.
    def _planner_node(state: Layer2GraphState) -> dict[str, TaskState]:
        return {
            "task_state": planner(
                _copy_task_state(state["task_state"]),
                planner_service=planner_service,
            )
        }

    return _planner_node


def _build_actor_node(actor_service: ActorPort):
    # Close over the resolved actor so the graph can remain deterministic in tests.
    def _actor_node(state: Layer2GraphState) -> dict[str, TaskState]:
        return {
            "task_state": actor(
                _copy_task_state(state["task_state"]),
                actor_service=actor_service,
            )
        }

    return _actor_node


def _finalize_bootstrap_node(state: Layer2GraphState) -> dict[str, TaskState]:
    # Close the current Layer 2 slice using a copied TaskState so node updates stay explicit.
    return {"task_state": finalize_bootstrap(_copy_task_state(state["task_state"]))}


def _copy_task_state(task_state: TaskState) -> TaskState:
    # Keep graph-side transformations explicit by copying domain state before reuse.
    return deepcopy(task_state)


def _build_default_checkpointer() -> MemorySaver:
    # Register the custom runtime models so in-memory checkpointing can serialize them safely.
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ActorResult,
            ActorResultType,
            CapabilityAccessMode,
            ExecutedPlanStep,
            ExecutionPolicy,
            IntakeMetadata,
            IntakeResult,
            ParseStatus,
            PlanStep,
            PlanStepStatus,
            PlannerDecision,
            RuntimeFlag,
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
