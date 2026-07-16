from __future__ import annotations

from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from layer1_intake import (
    DomainType,
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
from ..judge import JudgeAssessment, JudgeRecommendedDirective
from ..oversight import (
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
from ..persistence import build_sqlite_checkpointer
from ..replanning import ReplanDecision
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
    PlanStepKind,
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
from ..tool_contract import (
    EvidenceSourceKind,
    EvidenceSupportLevel,
    ObservedEvidence,
    ToolExecutionResult,
    ToolName,
    ToolRequest,
    ToolSource,
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
def build_default_checkpointer(*, storage_path: Any | None = None) -> Any:
    serializer = build_runtime_serializer()
    return build_sqlite_checkpointer(serde=serializer, storage_path=storage_path)


# Keep the serializer registry explicit and isolated from graph wiring.
def _persisted_runtime_types() -> list[type]:
    return [
        ActorResult,
        ActorResultType,
        CapabilityAccessMode,
        ExecutedPlanStep,
        ExecutionPolicy,
        DomainType,
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
        JudgeRecommendedDirective,
        DynamicPolicyAssessment,
        DynamicPolicyRecommendedDirective,
        EvidenceSourceKind,
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
        PlanStepKind,
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
    }


__all__ = ["build_default_checkpointer", "build_runtime_serializer"]
