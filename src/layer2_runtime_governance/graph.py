from __future__ import annotations

from copy import deepcopy
from typing import Any

# Install targeted warning filters before importing langgraph and langchain_core dependencies.
from .runtime_warnings import suppress_known_runtime_warnings

suppress_known_runtime_warnings()

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
from .nodes import (
    actor,
    bootstrap_from_intake,
    finalize_bootstrap,
    observe_tool_result,
    planner,
    replanner,
    tool_gateway,
)
from .planning import PlannerPort, build_default_planner
from .replanning import ReplanDecision, ReplannerPort, build_default_replanner
from .state import (
    ExecutedPlanStep,
    PlanStep,
    PlanStepStatus,
    PlannerDecision,
    RuntimeFlag,
    RuntimeState,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepRecord,
    StepValidation,
    StepValidationDecision,
    TaskState,
)
from .tool_contract import (
    ObservedEvidence,
    ToolExecutionResult,
    ToolGatewayPort,
    ToolName,
    ToolRequest,
    ToolSource,
)
from .tool_gateway import build_default_tool_gateway


class Layer2GraphInput(TypedDict):
    # LangGraph receives the full IntakeResult as the only external input to Layer 2.
    intake_result: IntakeResult


class Layer2GraphState(Layer2GraphInput, total=False):
    # During execution the graph extends the input wrapper with the evolving TaskState.
    task_state: TaskState


class Layer2GraphOutput(TypedDict):
    # The current graph returns the state after the planner/actor/tool loop reaches a stable stop point.
    task_state: TaskState


def build_runtime_graph(
    *,
    checkpointer: Any | None = None,
    planner_service: PlannerPort | None = None,
    actor_service: ActorPort | None = None,
    tool_gateway_service: ToolGatewayPort | None = None,
    replanner_service: ReplannerPort | None = None,
):
    # Wire the current Layer 2 graph and compile it into an executable LangGraph.
    resolved_planner = planner_service or build_default_planner()
    resolved_actor = actor_service or build_default_actor()
    resolved_tool_gateway = tool_gateway_service or build_default_tool_gateway()
    resolved_replanner = replanner_service or build_default_replanner()

    builder = StateGraph(
        Layer2GraphState,
        input_schema=Layer2GraphInput,
        output_schema=Layer2GraphOutput,
    )

    builder.add_node("bootstrap_from_intake", _bootstrap_node)
    builder.add_node("planner", _build_planner_node(resolved_planner))
    builder.add_node("actor", _build_actor_node(resolved_actor))
    builder.add_node("tool_gateway", _build_tool_gateway_node(resolved_tool_gateway))
    builder.add_node("observe_tool_result", _observe_tool_result_node)
    builder.add_node("replanner", _build_replanner_node(resolved_replanner))
    builder.add_node("finalize_bootstrap", _finalize_bootstrap_node)

    builder.add_edge(START, "bootstrap_from_intake")
    builder.add_edge("bootstrap_from_intake", "planner")
    builder.add_edge("planner", "actor")

    # Keep the internal execution loop inside the graph so the outer surface still feels agentic.
    # The actor is the decision point; routing only follows its typed runtime outcome.
    builder.add_conditional_edges(
        "actor",
        _route_after_actor,
        {
            "actor": "actor",
            "tool_gateway": "tool_gateway",
            "replanner": "replanner",
            "finalize_bootstrap": "finalize_bootstrap",
        },
    )

    builder.add_edge("tool_gateway", "observe_tool_result")
    builder.add_edge("observe_tool_result", "actor")
    builder.add_edge("replanner", "actor")
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


def _build_tool_gateway_node(tool_gateway_service: ToolGatewayPort):
    # Close over the resolved tool gateway so the graph can remain deterministic in tests.
    def _tool_gateway_node(state: Layer2GraphState) -> dict[str, TaskState]:
        return {
            "task_state": tool_gateway(
                _copy_task_state(state["task_state"]),
                tool_gateway_service=tool_gateway_service,
            )
        }

    return _tool_gateway_node


def _build_replanner_node(replanner_service: ReplannerPort):
    # Close over the resolved replanner so tests can inject deterministic behavior.
    def _replanner_node(state: Layer2GraphState) -> dict[str, TaskState]:
        return {
            "task_state": replanner(
                _copy_task_state(state["task_state"]),
                replanner_service=replanner_service,
            )
        }

    return _replanner_node


def _observe_tool_result_node(state: Layer2GraphState) -> dict[str, TaskState]:
    # Observe the latest tool result before returning control to the actor.
    return {"task_state": observe_tool_result(_copy_task_state(state["task_state"]))}


def _finalize_bootstrap_node(state: Layer2GraphState) -> dict[str, TaskState]:
    # Close the current Layer 2 slice using a copied TaskState so node updates stay explicit.
    return {"task_state": finalize_bootstrap(_copy_task_state(state["task_state"]))}


# Route actor outcomes to the next internal runtime node.
def _route_after_actor(state: Layer2GraphState) -> str:
    task_state = state["task_state"]

    if task_state.current_state == RuntimeState.READY_TOOL_EXECUTION:
        # Tool execution stays outside the actor so requests remain observable and typed.
        return "tool_gateway"

    if task_state.current_state == RuntimeState.REPLANNING:
        # Replanning is a real branch now, not a terminal stop condition.
        return "replanner"

    if task_state.current_state == RuntimeState.READY_NEXT_STEP:
        # A completed non-final step advances the cursor and immediately re-enters acting.
        return "actor"

    if task_state.current_state == RuntimeState.ACTING:
        # Validation can intentionally keep the actor on the same step for another turn.
        return "actor"

    return "finalize_bootstrap"


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
            ObservedEvidence,
            ParseStatus,
            PlanStep,
            PlanStepStatus,
            PlannerDecision,
            ReplanDecision,
            RuntimeFlag,
            RuntimeState,
            ScratchpadEntry,
            ScratchpadEntryType,
            SensitivityLevel,
            StepRecord,
            StepValidation,
            StepValidationDecision,
            StructuredTask,
            TaskSpec,
            TaskState,
            ToolExecutionResult,
            ToolName,
            ToolRequest,
            ToolSource,
        ]
    )
    return MemorySaver(serde=serializer)


__all__ = [
    "Layer2GraphInput",
    "Layer2GraphOutput",
    "Layer2GraphState",
    "build_runtime_graph",
]
