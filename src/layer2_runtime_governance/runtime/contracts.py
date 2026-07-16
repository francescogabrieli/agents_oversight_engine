from __future__ import annotations

from typing_extensions import TypedDict

from layer1_intake import IntakeResult

from ..domain import TaskState


class Layer2GraphInput(TypedDict):
    # LangGraph receives the canonical intake and an optional explicit thread id.
    intake_result: IntakeResult
    thread_id: str | None


class Layer2GraphState(Layer2GraphInput, total=False):
    # Runtime execution extends the graph wrapper with canonical TaskState.
    task_state: TaskState


class Layer2GraphOutput(TypedDict):
    # The graph exposes canonical TaskState at its stable stop point.
    task_state: TaskState


__all__ = ["Layer2GraphInput", "Layer2GraphOutput", "Layer2GraphState"]
