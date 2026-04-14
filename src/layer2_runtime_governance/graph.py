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

from .nodes import bootstrap_from_intake, finalize_bootstrap
from .state import RuntimeFlag, RuntimeState, StepRecord, TaskState


class Layer2GraphInput(TypedDict):
    intake_result: IntakeResult


class Layer2GraphState(Layer2GraphInput, total=False):
    task_state: TaskState


class Layer2GraphOutput(TypedDict):
    task_state: TaskState


def build_runtime_graph(*, checkpointer: Any | None = None):
    # Build the current compact Layer 2 graph.
    # At this stage Layer 2 only performs the bootstrap handoff from IntakeResult to TaskState.
    builder = StateGraph(
        Layer2GraphState,
        input_schema=Layer2GraphInput,
        output_schema=Layer2GraphOutput,
    )

    builder.add_node("bootstrap_from_intake", _bootstrap_node)
    builder.add_node("finalize_bootstrap", _finalize_bootstrap_node)

    builder.add_edge(START, "bootstrap_from_intake")
    builder.add_edge("bootstrap_from_intake", "finalize_bootstrap")
    builder.add_edge("finalize_bootstrap", END)

    return builder.compile(checkpointer=checkpointer or _build_default_checkpointer())


def _bootstrap_node(state: Layer2GraphInput) -> dict[str, TaskState]:
    return {"task_state": bootstrap_from_intake(state["intake_result"])}


def _finalize_bootstrap_node(state: Layer2GraphState) -> dict[str, TaskState]:
    return {"task_state": finalize_bootstrap(_copy_task_state(state["task_state"]))}


def _copy_task_state(task_state: TaskState) -> TaskState:
    return deepcopy(task_state)


def _build_default_checkpointer() -> MemorySaver:
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ExecutionPolicy,
            IntakeMetadata,
            IntakeResult,
            ParseStatus,
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
