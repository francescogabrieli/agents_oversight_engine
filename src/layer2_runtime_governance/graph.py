from __future__ import annotations

from typing import Any, Callable

from .runtime_warnings import suppress_known_runtime_warnings

# Install targeted filters before importing LangGraph dependencies.
suppress_known_runtime_warnings()

from langgraph.graph import END, START, StateGraph

from .actor_contract import ActorPort
from layer4_state_evidence_observability.behavioral import BehavioralStorePort
from .governance_components import PolicyEnginePort
from .judge import JudgePort
from .planning import PlannerPort
from .planning.replanning import ReplannerPort
from .runtime.contracts import Layer2GraphInput, Layer2GraphOutput, Layer2GraphState
from .runtime.node_adapters import (
    bootstrap_node,
    build_actor_node,
    build_governance_node,
    build_external_execution_node,
    build_human_review_node,
    build_planner_node,
    build_replanner_node,
    build_tool_gateway_node,
    finalize_node,
    observe_tool_result_node,
)
from .runtime.routing import (
    route_after_actor,
    route_after_external_execution,
    route_after_governance,
    route_after_human_review,
    route_after_observe_tool_result,
)
from .domain import TaskState
from layer3_tool_bus_external_capabilities.external_operations import (
    ExternalCapabilityRegistry,
    ExternalOperationExecutorPort,
)
from layer3_tool_bus_external_capabilities.tool_contract import ToolGatewayPort
from layer3_tool_bus_external_capabilities.tools import ToolGatewayConfig


# Compose Layer 2 capabilities into the executable LangGraph runtime.
def build_runtime_graph(
    *,
    checkpointer: Any | None,
    planner_service: PlannerPort,
    actor_service: ActorPort,
    tool_gateway_service: ToolGatewayPort,
    tool_gateway_config: ToolGatewayConfig,
    replanner_service: ReplannerPort,
    policy_engine_service: PolicyEnginePort,
    judge_service: JudgePort,
    behavioral_store_service: BehavioralStorePort,
    external_operation_executor_service: ExternalOperationExecutorPort,
    external_capability_registry: ExternalCapabilityRegistry,
    hitl_event_callback: Callable[[TaskState], None] | None = None,
):
    builder = StateGraph(
        Layer2GraphState,
        input_schema=Layer2GraphInput,
        output_schema=Layer2GraphOutput,
    )
    builder.add_node("bootstrap_from_intake", bootstrap_node)
    builder.add_node("planner", build_planner_node(planner_service))
    builder.add_node("actor", build_actor_node(actor_service))
    builder.add_node(
        "governance",
        build_governance_node(
            policy_engine_service,
            tool_gateway_config=tool_gateway_config,
            judge_service=judge_service,
            behavioral_store_service=behavioral_store_service,
            hitl_event_callback=hitl_event_callback,
        ),
    )
    builder.add_node("human_review", build_human_review_node())
    builder.add_node(
        "external_execution",
        build_external_execution_node(
            external_operation_executor_service,
            capability_registry=external_capability_registry,
            hitl_event_callback=hitl_event_callback,
        ),
    )
    builder.add_node("tool_gateway", build_tool_gateway_node(tool_gateway_service))
    builder.add_node("observe_tool_result", observe_tool_result_node)
    builder.add_node("replanner", build_replanner_node(replanner_service))
    builder.add_node("finalize_bootstrap", finalize_node)

    builder.add_edge(START, "bootstrap_from_intake")
    builder.add_edge("bootstrap_from_intake", "planner")
    builder.add_edge("planner", "actor")
    builder.add_conditional_edges(
        "actor",
        route_after_actor,
        {
            "governance": "governance",
        },
    )
    builder.add_conditional_edges(
        "governance",
        route_after_governance,
        {
            "actor": "actor",
            "tool_gateway": "tool_gateway",
            "replanner": "replanner",
            "human_review": "human_review",
            "external_execution": "external_execution",
            "finalize_bootstrap": "finalize_bootstrap",
        },
    )
    builder.add_conditional_edges(
        "human_review",
        route_after_human_review,
        {
            "planner": "planner",
            "actor": "actor",
            "tool_gateway": "tool_gateway",
            "replanner": "replanner",
            "governance": "governance",
            "external_execution": "external_execution",
            "finalize_bootstrap": "finalize_bootstrap",
        },
    )
    builder.add_conditional_edges(
        "external_execution",
        route_after_external_execution,
        {
            "external_execution": "external_execution",
            "human_review": "human_review",
            "finalize_bootstrap": "finalize_bootstrap",
        },
    )
    builder.add_edge("tool_gateway", "observe_tool_result")
    builder.add_conditional_edges("observe_tool_result", route_after_observe_tool_result, {"governance": "governance", "replanner": "replanner", "actor": "actor"})
    builder.add_edge("replanner", "actor")
    builder.add_edge("finalize_bootstrap", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = [
    "Layer2GraphInput",
    "Layer2GraphOutput",
    "Layer2GraphState",
    "build_runtime_graph",
]
