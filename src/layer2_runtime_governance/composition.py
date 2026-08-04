from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .actor_contract import ActorPort
from layer4_state_evidence_observability.behavioral import BehavioralStorePort
from .governance_components import PolicyEnginePort
from .graph import build_runtime_graph
from .judge import JudgePort
from .planning import PlannerPort
from .planning.replanning import ReplannerPort
from .domain import TaskState
from layer3_tool_bus_external_capabilities.external_operations import (
    ExternalCapabilityRegistry,
    ExternalOperationExecutorPort,
    build_default_external_operation_executor,
    load_external_capability_registry,
)
from layer3_tool_bus_external_capabilities.tool_contract import ToolGatewayPort
from .execution import build_default_actor
from layer3_tool_bus_external_capabilities.tools import (
    ToolGatewayConfig,
    build_default_tool_gateway,
    load_tool_gateway_config,
)


@dataclass(frozen=True)
class Layer2RuntimeDependencies:
    # Optional ports let production and tests share one explicit composition surface.
    planner: PlannerPort | None = None
    actor: ActorPort | None = None
    tool_gateway: ToolGatewayPort | None = None
    tool_gateway_config: ToolGatewayConfig | None = None
    replanner: ReplannerPort | None = None
    policy_engine: PolicyEnginePort | None = None
    judge: JudgePort | None = None
    behavioral_store: BehavioralStorePort | None = None
    external_operation_executor: ExternalOperationExecutorPort | None = None
    external_capability_registry: ExternalCapabilityRegistry | None = None
    checkpointer: Any | None = None
    hitl_event_callback: Callable[[TaskState], None] | None = None


# Build the runtime from explicit ports while preserving existing default adapters.
def compose_layer2_runtime(
    dependencies: Layer2RuntimeDependencies | None = None,
):
    resolved = dependencies or Layer2RuntimeDependencies()
    # Load one immutable registry and share it across actor schema and gateway construction.
    tool_gateway_config = resolved.tool_gateway_config or load_tool_gateway_config()
    actor = resolved.actor or build_default_actor(tool_gateway_config=tool_gateway_config)
    tool_gateway = resolved.tool_gateway or build_default_tool_gateway(
        config=tool_gateway_config
    )
    external_registry = (
        resolved.external_capability_registry
        or load_external_capability_registry()
    )
    external_executor = (
        resolved.external_operation_executor
        or build_default_external_operation_executor(registry=external_registry)
    )
    return build_runtime_graph(
        checkpointer=resolved.checkpointer,
        planner_service=resolved.planner,
        actor_service=actor,
        tool_gateway_service=tool_gateway,
        tool_gateway_config=tool_gateway_config,
        replanner_service=resolved.replanner,
        policy_engine_service=resolved.policy_engine,
        judge_service=resolved.judge,
        behavioral_store_service=resolved.behavioral_store,
        external_operation_executor_service=external_executor,
        external_capability_registry=external_registry,
        hitl_event_callback=resolved.hitl_event_callback,
    )


__all__ = ["Layer2RuntimeDependencies", "compose_layer2_runtime"]
