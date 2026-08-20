"""Lazy public API for the governed Layer 2 runtime.

Capability packages remain the preferred import surface.  These lazy exports
preserve existing callers without importing LangGraph, providers, persistence,
and dashboard code for every domain-only import.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORT_GROUPS: dict[str, tuple[str, ...]] = {
    ".execution": (
        "ActorExecutionError", "BedrockNovaActor", "BedrockNovaActorSettings",
        "OpenAIActor", "OpenAIActorSettings", "build_default_actor",
    ),
    ".actor_contract": (
        "ActorPastStep", "ActorPlanStep", "ActorPort", "ActorResult",
        "ActorResultType", "ActorTask", "CapabilityAccessMode", "build_actor_task",
    ),
    "layer4_state_evidence_observability.behavioral": (
        "BehavioralAssessment", "BehavioralRecommendedDirective", "BehavioralStorePort",
        "BehavioralStoreSettings", "SQLiteBehavioralStore", "build_behavioral_assessment",
        "build_default_behavioral_store",
        "get_default_behavioral_store_path", "persist_behavioral_runtime_snapshot",
        "persist_behavioral_snapshot", "persist_judge_snapshot",
    ),
    ".governance_components": (
        "GovernanceDecision", "GovernanceDecisionTrace", "GovernanceEvaluationResult",
        "GovernanceDirective", "GovernanceReasonCode", "OPAHttpPolicyEngine",
        "OPAHttpPolicyEngineSettings", "PolicyDecision", "PolicyEngineExecutionError",
        "PolicyEnginePort", "PolicyEvaluationInput", "PolicyEvaluationResult",
        "StaticPolicyAssessment", "build_default_policy_engine", "build_opa_input_document",
        "build_policy_evaluation_input", "evaluate_opa_first_governance",
    ),
    ".graph": (
        "Layer2GraphInput", "Layer2GraphOutput", "Layer2GraphState",
        "build_runtime_graph",
    ),
    ".composition": ("Layer2RuntimeDependencies", "compose_layer2_runtime"),
    ".judge": (
        "DisabledJudge", "JudgeAssessment", "JudgeExecutionError", "JudgePort",
        "JudgeAssessmentStatus", "JudgeContext", "JudgeFinding", "JudgeFindingCategory",
        "JudgeModelMetadata", "JudgePayloadField", "JudgePlanStepContext",
        "JudgeRecommendedDirective", "JudgeSettings", "JudgeSeverity", "JudgeStageContract",
        "JudgeTaskRequirement", "JudgeTaskRequirementKind", "JudgeVerdict", "OpenAIJudge",
        "build_default_judge", "build_judge_system_prompt", "build_judge_user_prompt",
    ),
    "layer3_tool_bus_external_capabilities.external_operations": (
        "CommunicationOperationPayload", "DatabaseOperationPayload",
        "DatabaseOperationParameter", "DatabaseStatementDraft", "DestinationClass",
        "ExternalCapabilityDefinition", "ExternalCapabilityRegistry",
        "ExternalExecutionAuthorizationSource", "ExternalExecutionErrorCode",
        "ExternalExecutionReceipt", "ExternalExecutionRequest", "ExternalExecutionStatus",
        "ExternalOperationEffect", "ExternalOperationExecutionResult",
        "ExternalOperationExecutorPort",
        "ExternalOperationFamily", "ExternalOperationPayloadEdit",
        "ExternalOperationProposal", "ExternalOperationTarget",
        "GenericExternalOperationPayload", "GenericPayloadField",
        "GovernanceActionFeedback", "OperationBoundary", "SQLStatementClass",
        "OperationFactsProvenance",
        "DeferredExternalOperationExecutor", "normalize_communication_operation",
        "normalize_generic_external_operation", "normalize_sql_operation",
        "replace_external_operation_payload", "load_external_capability_registry",
        "build_external_execution_request", "build_default_external_operation_executor",
    ),
    ".hitl.dashboard": (
        "DashboardBehavioralPanel", "DashboardExternalCommunicationPanel",
        "DashboardExternalExecutionPanel", "DashboardFocus",
        "DashboardGovernancePanel", "DashboardHumanReviewPanel", "DashboardOversightPanel",
        "DashboardPlanStep", "GovernanceHitlDashboardSnapshot",
        "build_governance_hitl_dashboard_snapshot", "governance_hitl_dashboard_snapshot_to_dict",
        "governance_hitl_dashboard_snapshot_to_json",
    ),
    ".runtime": (
        "actor", "bootstrap_from_intake", "external_execution", "finalize_bootstrap", "human_review",
        "observe_tool_result", "planner", "replanner", "tool_gateway",
    ),
    ".hitl": ("build_human_review_request", "coerce_human_review_decision_payload"),
    ".hitl.decision_inbox": (
        "ReviewDecisionInboxPort", "ReviewDecisionStatus",
        "ReviewDecisionSubmission", "RuntimeOwnerLease",
    ),
    "layer4_state_evidence_observability.oversight": (
        "DynamicPolicyAssessment", "DynamicPolicyRecommendedDirective",
        "EvidenceQualityAssessment", "OperationalRiskAssessment",
        "OversightAssessment", "OversightReasonCode",
        "OversightRecommendedDirective", "OversightSignalLevel",
        "TrajectoryHealthAssessment",
    ),
    "layer4_state_evidence_observability.infrastructure.runtime_artifacts": (
        "build_sqlite_checkpointer", "RuntimeArtifactPaths", "RuntimeArtifactStore",
        "get_default_checkpointer_path", "get_default_runtime_artifacts_root",
        "persist_runtime_artifacts",
    ),
    "layer4_state_evidence_observability.infrastructure.checkpointing": (
        "build_default_checkpointer",
    ),
    "layer4_state_evidence_observability.infrastructure.review_decision_inbox": (
        "SQLiteReviewDecisionInbox", "get_default_review_decision_inbox_path",
    ),
    ".planning": (
        "OpenAIPlanner", "OpenAIPlannerSettings", "PlannerExecutionError", "PlannerPort",
        "build_default_planner",
    ),
    ".planning.replanning": (
        "LocalConservativeReplanner", "OpenAIReplanner", "OpenAIReplannerSettings",
        "ReplanDecision", "ReplannerExecutionError", "ReplannerPort",
        "build_default_replanner", "build_replanner_system_prompt", "build_replanner_user_prompt",
    ),
    ".domain": (
        "ExecutedPlanStep", "ExternalCommunicationPhase", "ExternalCommunicationDraft",
        "ExternalCommunicationState", "HumanReviewDecision", "HumanReviewDecisionType",
        "HumanReviewExternalEdit", "HumanReviewRequest", "EvidenceBurden", "PlanStep",
        "PlanStepStatus", "PlannerDecision", "RuntimeFlag", "RuntimeState", "TerminationReason",
        "ReplanCheckpoint", "ScratchpadEntry", "ScratchpadEntryType", "StepRecord", "StepValidation",
        "TaskState",
    ),
    "layer3_tool_bus_external_capabilities.tool_contract": (
        "ToolExecutionResult", "ToolGatewayPort", "ToolName", "ToolObservation", "ToolRequest",
    ),
    "layer3_tool_bus_external_capabilities.tools": (
        "McpServerDefinition", "OpenAIToolGateway", "OpenAIToolGatewaySettings", "ToolBackend",
        "ToolCapability", "ToolDefinition", "ToolExecutionError", "ToolGatewayConfig",
        "ToolRiskLevel", "build_default_tool_gateway", "build_default_tool_gateway_config",
        "get_default_tool_gateway_config_path", "load_tool_gateway_config",
    ),
    "layer4_state_evidence_observability.observability": (
        "Layer2RuntimeTrace", "TraceActorTurn",
        "TraceExecutionSummary", "TraceExternalCommunicationDraft",
        "TraceExternalCommunicationSummary", "TraceExternalExecutionReceipt",
        "TraceExternalExecutionSummary", "TraceGovernancePass",
        "TraceHumanReviewDecision", "TraceHumanReviewExternalEdit", "TraceHumanReviewRequest",
        "TraceHumanReviewSummary", "TraceIntakeSummary", "TraceToolObservation",
        "TracePlanStep", "TraceRuntimeSummary", "TraceToolCall",
        "build_compact_trace_from_runtime_trace", "build_layer2_runtime_trace",
        "layer2_trace_to_dict", "layer2_trace_to_json",
    ),
}

_EXPORT_MODULES = {
    name: module_name
    for module_name, names in _EXPORT_GROUPS.items()
    for name in names
}
__all__ = sorted(_EXPORT_MODULES)


# Resolve compatibility exports only when a caller actually requests them.
def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


# Make interactive discovery work without importing every capability.
def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
