from __future__ import annotations

from copy import deepcopy
from typing import Callable
from typing import Any

# Install targeted warning filters before importing langgraph and langchain_core dependencies.
from .runtime_warnings import suppress_known_runtime_warnings

suppress_known_runtime_warnings()

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langchain_core.runnables import RunnableConfig
from typing_extensions import TypedDict

from layer1_intake import (
    ExecutionPolicy,
    DomainType,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskSpec,
)

from .behavioral import (
    BehavioralAssessment,
    BehavioralStorePort,
    BehavioralStoreSettings,
    SQLiteBehavioralStore,
)
from .acting import build_default_actor
from .actor_contract import (
    ActorPort,
    ActorResult,
    ActorResultType,
    CapabilityAccessMode,
)
from .governance import (
    GovernanceDecision,
    GovernanceDecisionTrace,
    GovernanceDirective,
    GovernanceReasonCode,
    OPAHttpPolicyEngineSettings,
    PolicyDecision,
    PolicyEnginePort,
    PolicyEvaluationInput,
    PolicyEvaluationResult,
    StaticPolicyAssessment,
    build_default_policy_engine,
)
from .oversight import (
    DynamicPolicyAssessment,
    DynamicPolicyRecommendedDirective,
    EvidenceQualityAssessment,
    EvidenceQualityWeights,
    OperationalRiskAssessment,
    OversightAssessment,
    OversightPolicyConfig,
    OversightReasonCode,
    OversightRecommendedDirective,
    OversightSignalLevel,
    RiskScores,
    TrajectoryHealthAssessment,
    TrajectoryPenalties,
)
from .nodes import (
    actor,
    bootstrap_from_intake,
    finalize_bootstrap,
    governance,
    observe_tool_result,
    planner,
    replanner,
    human_review,
    tool_gateway,
)
from .persistence import DiskBackedMemorySaver
from .planning import PlannerPort, build_default_planner
from .replanning import ReplanDecision, ReplannerPort, build_default_replanner
from .state import (
    ExecutedPlanStep,
    ExternalCommunicationPhase,
    ExternalCommunicationState,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewRequest,
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
    EvidenceSupportLevel,
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
    thread_id: str | None


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
    policy_engine_service: PolicyEnginePort | None = None,
    behavioral_store_service: BehavioralStorePort | None = None,
    hitl_event_callback: Callable[[TaskState], None] | None = None,
):
    # Wire the current Layer 2 graph and compile it into an executable LangGraph.
    resolved_planner = planner_service or build_default_planner()
    resolved_actor = actor_service or build_default_actor()
    resolved_tool_gateway = tool_gateway_service or build_default_tool_gateway()
    resolved_replanner = replanner_service or build_default_replanner()
    resolved_policy_engine = policy_engine_service or build_default_policy_engine()

    builder = StateGraph(
        Layer2GraphState,
        input_schema=Layer2GraphInput,
        output_schema=Layer2GraphOutput,
    )

    builder.add_node("bootstrap_from_intake", _bootstrap_node)
    builder.add_node("planner", _build_planner_node(resolved_planner))
    builder.add_node("actor", _build_actor_node(resolved_actor))
    builder.add_node(
        "governance",
        _build_governance_node(
            resolved_policy_engine,
            behavioral_store_service=behavioral_store_service,
            hitl_event_callback=hitl_event_callback,
        ),
    )
    builder.add_node("human_review", _build_human_review_node())
    builder.add_node("tool_gateway", _build_tool_gateway_node(resolved_tool_gateway))
    builder.add_node("observe_tool_result", _observe_tool_result_node)
    builder.add_node("replanner", _build_replanner_node(resolved_replanner))
    builder.add_node("finalize_bootstrap", _finalize_bootstrap_node)

    builder.add_edge(START, "bootstrap_from_intake")
    builder.add_edge("bootstrap_from_intake", "planner")
    builder.add_edge("planner", "actor")

    builder.add_edge("actor", "governance")
    # Keep the internal execution loop inside the graph so the outer surface still feels agentic.
    # The governance node is the decision point; routing only follows its typed runtime outcome.
    builder.add_conditional_edges(
        "governance",
        _route_after_governance,
        {
            "actor": "actor",
            "tool_gateway": "tool_gateway",
            "replanner": "replanner",
            "human_review": "human_review",
            "finalize_bootstrap": "finalize_bootstrap",
        },
    )

    builder.add_conditional_edges(
        "human_review",
        _route_after_human_review,
        {
            "planner": "planner",
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


def _bootstrap_node(state: Layer2GraphInput, config: RunnableConfig | None = None) -> dict[str, TaskState]:
    # Adapt the graph wrapper state to the domain-level bootstrap function.
    task_state = bootstrap_from_intake(state["intake_result"])
    task_state.runtime_thread_id = _extract_bootstrap_thread_id(state, config=config)
    return {"task_state": task_state}


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


# Close over the resolved policy engine so governance stays deterministic in tests.
def _build_governance_node(
    policy_engine_service: PolicyEnginePort,
    *,
    behavioral_store_service: BehavioralStorePort | None = None,
    hitl_event_callback: Callable[[TaskState], None] | None = None,
):
    def _governance_node(
        state: Layer2GraphState,
        config: RunnableConfig | None = None,
    ) -> dict[str, TaskState]:
        thread_id = _extract_thread_id(state, config=config)
        return {
            "task_state": governance(
                _copy_task_state(state["task_state"]),
                policy_engine_service=policy_engine_service,
                behavioral_store_service=behavioral_store_service,
                thread_id=thread_id,
                hitl_event_callback=hitl_event_callback,
            )
        }

    return _governance_node


def _build_human_review_node():
    # Human review is the LangGraph interrupt/resume node for typed HITL pauses.
    def _human_review_node(
        state: Layer2GraphState,
        config: RunnableConfig | None = None,
    ) -> dict[str, TaskState]:
        thread_id = _extract_thread_id(state, config=config)
        return {
            "task_state": human_review(
                _copy_task_state(state["task_state"]),
                thread_id=thread_id,
            )
        }

    return _human_review_node


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


# Route governance outcomes to the next internal runtime node.
def _route_after_governance(state: Layer2GraphState) -> str:
    task_state = state["task_state"]

    if task_state.current_state == RuntimeState.READY_TOOL_EXECUTION:
        # Tool execution stays outside the actor so requests remain observable and typed.
        return "tool_gateway"

    if task_state.current_state == RuntimeState.REPLANNING:
        # Replanning is a real branch now, not a terminal stop condition.
        return "replanner"

    if (
        task_state.current_state == RuntimeState.WAITING_HUMAN_REVIEW
        and task_state.pending_human_review_request is not None
    ):
        # Human review now interrupts the graph instead of ending the runtime slice.
        return "human_review"

    if task_state.current_state == RuntimeState.READY_NEXT_STEP:
        # A completed non-final step advances the cursor and immediately re-enters acting.
        return "actor"

    if task_state.current_state == RuntimeState.ACTING:
        # Validation can intentionally keep the actor on the same step for another turn.
        return "actor"

    return "finalize_bootstrap"


# Route the post-review runtime state back into the appropriate execution branch.
def _route_after_human_review(state: Layer2GraphState) -> str:
    task_state = state["task_state"]

    if task_state.current_state == RuntimeState.PLANNING:
        return "planner"

    if task_state.current_state == RuntimeState.READY_TOOL_EXECUTION:
        return "tool_gateway"

    if task_state.current_state == RuntimeState.REPLANNING:
        return "replanner"

    if task_state.current_state == RuntimeState.READY_NEXT_STEP:
        return "actor"

    if task_state.current_state == RuntimeState.ACTING:
        return "actor"

    return "finalize_bootstrap"


def _copy_task_state(task_state: TaskState) -> TaskState:
    # Keep graph-side transformations explicit by copying domain state before reuse.
    return deepcopy(task_state)


# Extract the real LangGraph thread id from the runtime config.
def _extract_thread_id(state: Layer2GraphState, config: Any | None = None) -> str:
    task_state = state["task_state"]
    if task_state.runtime_thread_id:
        return task_state.runtime_thread_id

    if config is not None:
        candidate_containers: list[Any] = []
        if isinstance(config, dict):
            candidate_containers.extend([config.get("metadata"), config.get("configurable")])
        else:
            candidate_containers.extend(
                [
                    getattr(config, "metadata", None),
                    getattr(config, "configurable", None),
                ]
            )

        for container in candidate_containers:
            if not isinstance(container, dict):
                continue
            thread_id = container.get("thread_id")
            if isinstance(thread_id, str) and thread_id.strip():
                return thread_id

    return task_state.intake_metadata.request_id


# Resolve the thread id at bootstrap time without forcing callers to embed it in the input.
def _extract_bootstrap_thread_id(
    state: Layer2GraphInput,
    config: RunnableConfig | None = None,
) -> str:
    thread_id = state.get("thread_id")
    if isinstance(thread_id, str) and thread_id.strip():
        return thread_id

    if config is not None:
        if isinstance(config, dict):
            for container_name in ("metadata", "configurable"):
                container = config.get(container_name, {})
                if not isinstance(container, dict):
                    continue
                thread_id = container.get("thread_id")
                if isinstance(thread_id, str) and thread_id.strip():
                    return thread_id
        else:
            for container in (
                getattr(config, "metadata", None),
                getattr(config, "configurable", None),
            ):
                if not isinstance(container, dict):
                    continue
                thread_id = container.get("thread_id")
                if isinstance(thread_id, str) and thread_id.strip():
                    return thread_id

    return state["intake_result"].metadata.request_id


def _build_default_checkpointer(*, storage_path: Any | None = None) -> DiskBackedMemorySaver:
    # Register the custom runtime models so checkpoint persistence can serialize them safely.
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ActorResult,
            ActorResultType,
            CapabilityAccessMode,
            ExecutedPlanStep,
            ExecutionPolicy,
            DomainType,
            IntakeMetadata,
            IntakeResult,
            ExternalCommunicationPhase,
            ExternalCommunicationState,
            HumanReviewDecision,
            HumanReviewDecisionType,
            HumanReviewRequest,
            GovernanceDecision,
            GovernanceDecisionTrace,
            GovernanceDirective,
            GovernanceReasonCode,
            BehavioralAssessment,
            BehavioralStoreSettings,
            SQLiteBehavioralStore,
            StaticPolicyAssessment,
            DynamicPolicyAssessment,
            DynamicPolicyRecommendedDirective,
            EvidenceQualityAssessment,
            EvidenceQualityWeights,
            EvidenceSupportLevel,
            ObservedEvidence,
            OperationalRiskAssessment,
            ParseStatus,
            OversightAssessment,
            OversightPolicyConfig,
            OversightReasonCode,
            OversightRecommendedDirective,
            OversightSignalLevel,
            PlanStep,
            PlanStepStatus,
            PlannerDecision,
            PolicyDecision,
            PolicyEvaluationInput,
            PolicyEvaluationResult,
            OPAHttpPolicyEngineSettings,
            ReplanDecision,
            RiskScores,
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
            TrajectoryHealthAssessment,
            TrajectoryPenalties,
            ToolExecutionResult,
            ToolName,
            ToolRequest,
            ToolSource,
        ]
    )
    return DiskBackedMemorySaver(serde=serializer, storage_path=storage_path)


# Expose the default checkpointer so local tools can share the same resume-safe serializer.
def build_default_checkpointer(*, storage_path: Any | None = None) -> DiskBackedMemorySaver:
    return _build_default_checkpointer(storage_path=storage_path)


__all__ = [
    "Layer2GraphInput",
    "Layer2GraphOutput",
    "Layer2GraphState",
    "build_runtime_graph",
    "build_default_checkpointer",
]
