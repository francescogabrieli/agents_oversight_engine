from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from layer1_intake import (
    ExecutionIntent,
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskSpec,
)

from ..actor_contract import ActorResult, ActorResultType, CapabilityAccessMode
from ..behavioral import (
    BehavioralAssessment,
    BehavioralRecommendedDirective,
    BehavioralStoreSettings,
    SQLiteBehavioralStore,
)
from ..governance_components import (
    GovernanceDecision,
    GovernanceDecisionTrace,
    GovernanceDirective,
    GovernanceReasonCode,
    OPAHttpPolicyEngineSettings,
    PolicyDecision,
    PolicyEvaluationInput,
    PolicyEvaluationResult,
    StaticPolicyAssessment,
)
from ..judge import (
    JudgeAssessment,
    JudgeAssessmentStatus,
    JudgeFinding,
    JudgeFindingCategory,
    JudgeModelMetadata,
    JudgeRecommendedDirective,
    JudgeSeverity,
    JudgeVerdict,
)
from ..external_operations import (
    CommunicationOperationPayload,
    DatabaseOperationPayload,
    DatabaseOperationParameter,
    DatabaseStatementDraft,
    DestinationClass,
    ExternalOperationEffect,
    ExternalExecutionAuthorizationSource,
    ExternalExecutionErrorCode,
    ExternalExecutionReceipt,
    ExternalExecutionRequest,
    ExternalExecutionStatus,
    ExternalOperationFamily,
    ExternalOperationPayloadEdit,
    ExternalOperationProposal,
    ExternalOperationTarget,
    GenericExternalOperationPayload,
    GenericPayloadField,
    GovernanceActionFeedback,
    JudgeFindingSnapshot,
    OperationBoundary,
    OperationFactsProvenance,
    SQLStatementClass,
)
from ..oversight.contracts import (
    DynamicPolicyAssessment,
    DynamicPolicyRecommendedDirective,
    EvidenceQualityAssessment,
    OperationalRiskAssessment,
    OversightAssessment,
    OversightReasonCode,
    OversightRecommendedDirective,
    OversightSignalLevel,
    TrajectoryHealthAssessment,
)
from .runtime_artifacts import build_sqlite_checkpointer
from ..planning.replanning import ReplanDecision
from ..domain import (
    ExecutedPlanStep,
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    ExternalCommunicationState,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    HumanReviewRequest,
    PlanStep,
    PlanStepStatus,
    PlannerDecision,
    ReplanCheckpoint,
    RuntimeFlag,
    RuntimeState,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepRecord,
    StepValidation,
    StepCompletionCandidate,
    AgentWorkspace,
    StepArtifact,
    TaskState,
)
from ..tool_contract import (
    ToolErrorCode,
    ToolExecutionResult,
    ToolName,
    ToolObservation,
    ToolRequest,
)
from .environment import get_env_value


class CheckpointBackend(str, Enum):
    # Keep supported LangGraph persistence implementations explicit.
    SQLITE = "sqlite"
    POSTGRES = "postgres"


@dataclass(frozen=True)
class CheckpointSettings:
    # Select an official LangGraph checkpointer without exposing credentials to graph state.
    backend: CheckpointBackend = CheckpointBackend.SQLITE
    postgres_uri: str | None = None
    postgres_pool_min_size: int = 1
    postgres_pool_max_size: int = 4

    # Resolve deployment settings once at the composition boundary.
    @classmethod
    def from_env(cls) -> "CheckpointSettings":
        backend_value = get_env_value("LAYER2_CHECKPOINT_BACKEND", "sqlite") or "sqlite"
        return cls(
            backend=CheckpointBackend(backend_value.strip().lower()),
            postgres_uri=get_env_value("LAYER2_CHECKPOINT_POSTGRES_URI"),
            postgres_pool_min_size=int(
                get_env_value("LAYER2_CHECKPOINT_POSTGRES_POOL_MIN_SIZE", "1") or "1"
            ),
            postgres_pool_max_size=int(
                get_env_value("LAYER2_CHECKPOINT_POSTGRES_POOL_MAX_SIZE", "4") or "4"
            ),
        )


# Build the serializer shared by durable and in-memory checkpoint adapters.
def build_runtime_serializer() -> JsonPlusSerializer:
    runtime_types = _persisted_runtime_types()
    allowed_type_paths = {
        (runtime_type.__module__, runtime_type.__name__)
        for runtime_type in runtime_types
    }
    # Preserve deserialization of checkpoints written before domain extraction.
    allowed_type_paths.update(_legacy_persisted_type_paths())
    return JsonPlusSerializer(
        allowed_msgpack_modules=sorted(allowed_type_paths)
    )


# Build the default checkpointer with every persisted runtime type allowlisted.
def build_default_checkpointer(
    *,
    storage_path: Any | None = None,
    settings: CheckpointSettings | None = None,
) -> Any:
    serializer = build_runtime_serializer()
    resolved_settings = settings or CheckpointSettings.from_env()
    if storage_path is not None or resolved_settings.backend == CheckpointBackend.SQLITE:
        return build_sqlite_checkpointer(serde=serializer, storage_path=storage_path)
    return _build_postgres_checkpointer(
        settings=resolved_settings,
        serializer=serializer,
    )


# Release the connection or pool owned by a short-lived checkpointer.
def close_checkpointer(checkpointer: Any) -> None:
    connection = getattr(checkpointer, "conn", None)
    close = getattr(connection, "close", None)
    if callable(close):
        close()


# Build the production-grade LangGraph saver recommended for deployed workflows.
def _build_postgres_checkpointer(
    *,
    settings: CheckpointSettings,
    serializer: JsonPlusSerializer,
) -> Any:
    if not settings.postgres_uri:
        raise RuntimeError(
            "LAYER2_CHECKPOINT_POSTGRES_URI is required when the checkpoint backend is postgres."
        )
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL checkpointing requires langgraph-checkpoint-postgres and psycopg[pool]."
        ) from exc

    pool = ConnectionPool(
        conninfo=settings.postgres_uri,
        min_size=settings.postgres_pool_min_size,
        max_size=settings.postgres_pool_max_size,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        open=True,
    )
    saver = PostgresSaver(pool, serde=serializer)
    saver.setup()
    return saver


# Keep the serializer registry explicit and isolated from graph wiring.
def _persisted_runtime_types() -> list[type]:
    return [
        ActorResult,
        ActorResultType,
        CapabilityAccessMode,
        ExecutedPlanStep,
        ExecutionPolicy,
        ExecutionIntent,
        IntakeMetadata,
        IntakeResult,
        ExternalCommunicationDraft,
        ExternalCommunicationPhase,
        ExternalCommunicationState,
        HumanReviewDecision,
        HumanReviewDecisionType,
        HumanReviewExternalEdit,
        HumanReviewRequest,
        GovernanceDecision,
        GovernanceDecisionTrace,
        GovernanceDirective,
        GovernanceReasonCode,
        BehavioralAssessment,
        BehavioralRecommendedDirective,
        BehavioralStoreSettings,
        SQLiteBehavioralStore,
        StaticPolicyAssessment,
        JudgeAssessment,
        JudgeAssessmentStatus,
        JudgeFinding,
        JudgeFindingCategory,
        JudgeModelMetadata,
        JudgeRecommendedDirective,
        JudgeSeverity,
        JudgeVerdict,
        CommunicationOperationPayload,
        DatabaseOperationPayload,
        DatabaseOperationParameter,
        DatabaseStatementDraft,
        DestinationClass,
        ExternalOperationEffect,
        ExternalExecutionAuthorizationSource,
        ExternalExecutionErrorCode,
        ExternalExecutionReceipt,
        ExternalExecutionRequest,
        ExternalExecutionStatus,
        ExternalOperationFamily,
        ExternalOperationPayloadEdit,
        ExternalOperationProposal,
        ExternalOperationTarget,
        GenericExternalOperationPayload,
        GenericPayloadField,
        GovernanceActionFeedback,
        JudgeFindingSnapshot,
        OperationBoundary,
        OperationFactsProvenance,
        SQLStatementClass,
        DynamicPolicyAssessment,
        DynamicPolicyRecommendedDirective,
        EvidenceQualityAssessment,
        OperationalRiskAssessment,
        ParseStatus,
        OversightAssessment,
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
        ReplanCheckpoint,
        RuntimeFlag,
        RuntimeState,
        ScratchpadEntry,
        ScratchpadEntryType,
        SensitivityLevel,
        StepRecord,
        StepValidation,
        StepCompletionCandidate,
        AgentWorkspace,
        StepArtifact,
        StructuredTask,
        TaskSpec,
        TaskState,
        TrajectoryHealthAssessment,
        ToolExecutionResult,
        ToolErrorCode,
        ToolName,
        ToolObservation,
        ToolRequest,
    ]


# Keep legacy module paths allowlisted while facade-based checkpoint migration is supported.
def _legacy_persisted_type_paths() -> set[tuple[str, str]]:
    state_type_names = {
        runtime_type.__name__
        for runtime_type in _persisted_runtime_types()
        if runtime_type.__module__.startswith("layer2_runtime_governance.domain.")
    }
    governance_type_names = {
        runtime_type.__name__
        for runtime_type in _persisted_runtime_types()
        if runtime_type.__module__.startswith(
            "layer2_runtime_governance.governance_components."
        )
    }
    return {
        *(("layer2_runtime_governance.state", name) for name in state_type_names),
        *(("layer2_runtime_governance.governance", name) for name in governance_type_names),
        # ReplanDecision moved into the planning capability after older checkpoints existed.
        ("layer2_runtime_governance.replanning", "ReplanDecision"),
    }


__all__ = [
    "CheckpointBackend",
    "CheckpointSettings",
    "build_default_checkpointer",
    "build_runtime_serializer",
    "close_checkpointer",
]
