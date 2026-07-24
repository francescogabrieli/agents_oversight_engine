from typing import Callable

from langchain_core.runnables import RunnableConfig

from ..actor_contract import ActorPort
from ..behavioral import BehavioralStorePort
from ..governance_components import PolicyEnginePort
from ..judge import JudgePort
from ..hitl import human_review
from .actor_service import actor
from .external_execution_service import external_execution
from .governance_service import governance
from .lifecycle_service import bootstrap_from_intake, finalize_bootstrap
from .planning_service import planner
from .replanning_service import replanner
from .tool_service import observe_tool_result, tool_gateway
from ..planning import PlannerPort
from ..planning.replanning import ReplannerPort
from ..domain import TaskState
from ..external_operations import (
    ExternalCapabilityRegistry,
    ExternalOperationExecutorPort,
)
from ..tool_contract import ToolGatewayPort
from ..tools import ToolGatewayConfig
from .context import copy_task_state, extract_bootstrap_thread_id, extract_thread_id
from .contracts import Layer2GraphInput, Layer2GraphState


# Adapt external graph input into canonical TaskState.
def bootstrap_node(
    state: Layer2GraphInput,
    config: RunnableConfig | None = None,
) -> dict[str, TaskState]:
    task_state = bootstrap_from_intake(state["intake_result"])
    task_state.runtime_thread_id = extract_bootstrap_thread_id(state, config=config)
    return {"task_state": task_state}


# Build a thin planner node around an injected planner port.
def build_planner_node(planner_service: PlannerPort):
    # Adapt graph state without adding planner business logic.
    def planner_node(state: Layer2GraphState) -> dict[str, TaskState]:
        return {
            "task_state": planner(
                copy_task_state(state["task_state"]),
                planner_service=planner_service,
            )
        }

    return planner_node


# Build a thin actor node around an injected actor port.
def build_actor_node(actor_service: ActorPort):
    # Adapt graph state without adding execution business logic.
    def actor_node(state: Layer2GraphState) -> dict[str, TaskState]:
        return {
            "task_state": actor(
                copy_task_state(state["task_state"]),
                actor_service=actor_service,
            )
        }

    return actor_node


# Build the governance node around explicit policy, judge, and behavioral ports.
def build_governance_node(
    policy_engine_service: PolicyEnginePort,
    *,
    tool_gateway_config: ToolGatewayConfig | None = None,
    judge_service: JudgePort | None = None,
    behavioral_store_service: BehavioralStorePort | None = None,
    hitl_event_callback: Callable[[TaskState], None] | None = None,
):
    # Resolve thread context and delegate governance evaluation to the capability service.
    def governance_node(
        state: Layer2GraphState,
        config: RunnableConfig | None = None,
    ) -> dict[str, TaskState]:
        return {
            "task_state": governance(
                copy_task_state(state["task_state"]),
                policy_engine_service=policy_engine_service,
                judge_service=judge_service,
                behavioral_store_service=behavioral_store_service,
                tool_gateway_config=tool_gateway_config,
                thread_id=extract_thread_id(state, config=config),
                hitl_event_callback=hitl_event_callback,
            )
        }

    return governance_node


# Build the LangGraph interrupt/resume adapter for typed human review.
def build_human_review_node():
    # Resolve thread context and delegate the review transition.
    def human_review_node(
        state: Layer2GraphState,
        config: RunnableConfig | None = None,
    ) -> dict[str, TaskState]:
        return {
            "task_state": human_review(
                state["task_state"],
                thread_id=extract_thread_id(state, config=config),
            )
        }

    return human_review_node


# Build a thin external execution node around injected registry and executor ports.
def build_external_execution_node(
    executor_service: ExternalOperationExecutorPort,
    *,
    capability_registry: ExternalCapabilityRegistry,
    hitl_event_callback: Callable[[TaskState], None] | None = None,
):
    # Resolve thread context and delegate all preflight and outcome rules.
    def external_execution_node(
        state: Layer2GraphState,
        config: RunnableConfig | None = None,
    ) -> dict[str, TaskState]:
        return {
            "task_state": external_execution(
                copy_task_state(state["task_state"]),
                executor_service=executor_service,
                capability_registry=capability_registry,
                thread_id=extract_thread_id(state, config=config),
                hitl_event_callback=hitl_event_callback,
            )
        }

    return external_execution_node


# Build a thin Tool Gateway node around an injected gateway port.
def build_tool_gateway_node(tool_gateway_service: ToolGatewayPort):
    # Delegate governed execution without embedding adapter logic in LangGraph.
    def tool_gateway_node(state: Layer2GraphState) -> dict[str, TaskState]:
        return {
            "task_state": tool_gateway(
                copy_task_state(state["task_state"]),
                tool_gateway_service=tool_gateway_service,
            )
        }

    return tool_gateway_node


# Build a thin replanner node around an injected replanner port.
def build_replanner_node(replanner_service: ReplannerPort):
    # Delegate trajectory replacement without embedding replan rules in LangGraph.
    def replanner_node(state: Layer2GraphState) -> dict[str, TaskState]:
        return {
            "task_state": replanner(
                copy_task_state(state["task_state"]),
                replanner_service=replanner_service,
            )
        }

    return replanner_node


# Observe the latest tool result before returning control to the actor.
def observe_tool_result_node(state: Layer2GraphState) -> dict[str, TaskState]:
    return {"task_state": observe_tool_result(copy_task_state(state["task_state"]))}


# Close the runtime slice through the canonical finalization transition.
def finalize_node(state: Layer2GraphState) -> dict[str, TaskState]:
    return {"task_state": finalize_bootstrap(state["task_state"])}


__all__ = [
    "bootstrap_node",
    "build_actor_node",
    "build_governance_node",
    "build_external_execution_node",
    "build_human_review_node",
    "build_planner_node",
    "build_replanner_node",
    "build_tool_gateway_node",
    "finalize_node",
    "observe_tool_result_node",
]
