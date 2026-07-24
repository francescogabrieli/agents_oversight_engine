from __future__ import annotations

from typing import Any, Callable

from .infrastructure.runtime_warnings import suppress_known_runtime_warnings

# Install targeted filters before importing LangGraph dependencies.
suppress_known_runtime_warnings()

from langgraph.graph import END, START, StateGraph

from .execution import build_default_actor
from .actor_contract import ActorPort
from .behavioral import BehavioralStorePort, build_default_behavioral_store
from .governance_components import PolicyEnginePort, build_default_policy_engine
from .infrastructure.checkpointing import build_default_checkpointer
from .judge import JudgePort, build_default_judge
from .planning import PlannerPort, build_default_planner
from .planning.replanning import ReplannerPort, build_default_replanner
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
    route_after_external_execution,
    route_after_governance,
    route_after_human_review,
)
from .domain import TaskState
from .external_operations import (
    ExternalCapabilityRegistry,
    ExternalOperationExecutorPort,
    build_default_external_operation_executor,
    load_external_capability_registry,
)
from .tool_contract import ToolGatewayPort
from .tools import ToolGatewayConfig, build_default_tool_gateway, load_tool_gateway_config


# Compose Layer 2 capabilities into the executable LangGraph runtime.
def build_runtime_graph(
    *,
    checkpointer: Any | None = None,
    planner_service: PlannerPort | None = None,
    actor_service: ActorPort | None = None,
    tool_gateway_service: ToolGatewayPort | None = None,
    tool_gateway_config: ToolGatewayConfig | None = None,
    replanner_service: ReplannerPort | None = None,
    policy_engine_service: PolicyEnginePort | None = None,
    judge_service: JudgePort | None = None,
    behavioral_store_service: BehavioralStorePort | None = None,
    external_operation_executor_service: ExternalOperationExecutorPort | None = None,
    external_capability_registry: ExternalCapabilityRegistry | None = None,
    hitl_event_callback: Callable[[TaskState], None] | None = None,
):
    resolved_planner = planner_service or build_default_planner()
    # Direct graph construction remains supported while sharing one selected registry.
    resolved_tool_config = tool_gateway_config or load_tool_gateway_config()
    resolved_actor = actor_service or build_default_actor(
        tool_gateway_config=resolved_tool_config
    )
    resolved_tool_gateway = tool_gateway_service or build_default_tool_gateway(
        config=resolved_tool_config
    )
    resolved_replanner = replanner_service or build_default_replanner()
    resolved_policy_engine = policy_engine_service or build_default_policy_engine()
    resolved_behavioral_store = behavioral_store_service or build_default_behavioral_store()
    resolved_judge = judge_service or build_default_judge()
    resolved_external_registry = (
        external_capability_registry or load_external_capability_registry()
    )
    resolved_external_executor = (
        external_operation_executor_service
        or build_default_external_operation_executor(
            registry=resolved_external_registry
        )
    )

    builder = StateGraph(
        Layer2GraphState,
        input_schema=Layer2GraphInput,
        output_schema=Layer2GraphOutput,
    )
    builder.add_node("bootstrap_from_intake", bootstrap_node)
    builder.add_node("planner", build_planner_node(resolved_planner))
    builder.add_node("actor", build_actor_node(resolved_actor))
    builder.add_node(
        "governance",
        build_governance_node(
            resolved_policy_engine,
            tool_gateway_config=resolved_tool_config,
            judge_service=resolved_judge,
            behavioral_store_service=resolved_behavioral_store,
            hitl_event_callback=hitl_event_callback,
        ),
    )
    builder.add_node("human_review", build_human_review_node())
    builder.add_node(
        "external_execution",
        build_external_execution_node(
            resolved_external_executor,
            capability_registry=resolved_external_registry,
            hitl_event_callback=hitl_event_callback,
        ),
    )
    builder.add_node("tool_gateway", build_tool_gateway_node(resolved_tool_gateway))
    builder.add_node("observe_tool_result", observe_tool_result_node)
    builder.add_node("replanner", build_replanner_node(resolved_replanner))
    builder.add_node("finalize_bootstrap", finalize_node)

    builder.add_edge(START, "bootstrap_from_intake")
    builder.add_edge("bootstrap_from_intake", "planner")
    builder.add_edge("planner", "actor")
    builder.add_edge("actor", "governance")
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
    builder.add_edge("observe_tool_result", "actor")
    builder.add_edge("replanner", "actor")
    builder.add_edge("finalize_bootstrap", END)
    return builder.compile(checkpointer=checkpointer or build_default_checkpointer())


__all__ = [
    "Layer2GraphInput",
    "Layer2GraphOutput",
    "Layer2GraphState",
    "build_default_checkpointer",
    "build_runtime_graph",
]
