from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .actor_contract import ActorPort
from .behavioral import BehavioralStorePort
from .governance_components import PolicyEnginePort
from .graph import build_runtime_graph
from .judge import JudgePort
from .planning import PlannerPort
from .planning.replanning import ReplannerPort
from .domain import TaskState
from .tool_contract import ToolGatewayPort


@dataclass(frozen=True)
class Layer2RuntimeDependencies:
    # Optional ports let production and tests share one explicit composition surface.
    planner: PlannerPort | None = None
    actor: ActorPort | None = None
    tool_gateway: ToolGatewayPort | None = None
    replanner: ReplannerPort | None = None
    policy_engine: PolicyEnginePort | None = None
    judge: JudgePort | None = None
    behavioral_store: BehavioralStorePort | None = None
    checkpointer: Any | None = None
    hitl_event_callback: Callable[[TaskState], None] | None = None


# Build the runtime from explicit ports while preserving existing default adapters.
def compose_layer2_runtime(
    dependencies: Layer2RuntimeDependencies | None = None,
):
    resolved = dependencies or Layer2RuntimeDependencies()
    return build_runtime_graph(
        checkpointer=resolved.checkpointer,
        planner_service=resolved.planner,
        actor_service=resolved.actor,
        tool_gateway_service=resolved.tool_gateway,
        replanner_service=resolved.replanner,
        policy_engine_service=resolved.policy_engine,
        judge_service=resolved.judge,
        behavioral_store_service=resolved.behavioral_store,
        hitl_event_callback=resolved.hitl_event_callback,
    )


__all__ = ["Layer2RuntimeDependencies", "compose_layer2_runtime"]
