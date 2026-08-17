from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .actor_contract import ActorPort
from layer4_state_evidence_observability.behavioral import (
    BehavioralStorePort,
    build_default_behavioral_store,
)
from .governance_components import PolicyEnginePort, build_default_policy_engine
from .graph import build_runtime_graph
from .judge import JudgePort, build_default_judge
from .planning import PlannerPort, build_default_planner
from .planning.replanning import ReplannerPort, build_default_replanner
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
from layer4_state_evidence_observability.infrastructure.checkpointing import (
    build_default_checkpointer,
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
    planner = resolved.planner or build_default_planner()
    actor = resolved.actor or build_default_actor(tool_gateway_config=tool_gateway_config)
    tool_gateway = resolved.tool_gateway or build_default_tool_gateway(
        config=tool_gateway_config
    )
    replanner = resolved.replanner or build_default_replanner()
    policy_engine = resolved.policy_engine or build_default_policy_engine()
    behavioral_store = (
        resolved.behavioral_store or build_default_behavioral_store()
    )
    judge = resolved.judge or build_default_judge()
    external_registry = (
        resolved.external_capability_registry
        or load_external_capability_registry()
    )
    external_executor = (
        resolved.external_operation_executor
        or build_default_external_operation_executor(registry=external_registry)
    )
    checkpointer = resolved.checkpointer or build_default_checkpointer()
    return build_runtime_graph(
        checkpointer=checkpointer,
        planner_service=planner,
        actor_service=actor,
        tool_gateway_service=tool_gateway,
        tool_gateway_config=tool_gateway_config,
        replanner_service=replanner,
        policy_engine_service=policy_engine,
        judge_service=judge,
        behavioral_store_service=behavioral_store,
        external_operation_executor_service=external_executor,
        external_capability_registry=external_registry,
        hitl_event_callback=resolved.hitl_event_callback,
    )


__all__ = ["Layer2RuntimeDependencies", "compose_layer2_runtime"]
