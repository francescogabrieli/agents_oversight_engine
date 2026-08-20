from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from ..domain import TaskState, normalize_task_state_serialization
from .contracts import Layer2GraphInput, Layer2GraphState


# Copy and normalize checkpoint state before a graph node applies a domain transition.
def copy_task_state(task_state: TaskState) -> TaskState:
    return normalize_task_state_serialization(task_state)


# Extract a stable thread id from TaskState or LangGraph configuration.
def extract_thread_id(state: Layer2GraphState, config: Any | None = None) -> str:
    task_state = state["task_state"]
    if task_state.runtime_thread_id:
        return task_state.runtime_thread_id
    configured = _thread_id_from_config(config)
    return configured or task_state.intake_metadata.request_id


# Resolve a thread id before TaskState exists during bootstrap.
def extract_bootstrap_thread_id(
    state: Layer2GraphInput,
    config: RunnableConfig | None = None,
) -> str:
    explicit = state.get("thread_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    configured = _thread_id_from_config(config)
    return configured or state["intake_result"].metadata.request_id


# Read a thread id from dict-like or object-like runtime configuration.
def _thread_id_from_config(config: Any | None) -> str | None:
    if config is None:
        return None
    if isinstance(config, dict):
        containers = [config.get("metadata"), config.get("configurable")]
    else:
        containers = [getattr(config, "metadata", None), getattr(config, "configurable", None)]
    for container in containers:
        if not isinstance(container, dict):
            continue
        thread_id = container.get("thread_id")
        if isinstance(thread_id, str) and thread_id.strip():
            return thread_id
    return None


__all__ = ["copy_task_state", "extract_bootstrap_thread_id", "extract_thread_id"]
