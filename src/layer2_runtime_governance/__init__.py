"""Lazy public API for the governed Layer 2 runtime.

Capability packages remain the preferred import surface.  These lazy exports
preserve existing callers without importing LangGraph, providers, persistence,
and dashboard code for every domain-only import.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORT_GROUPS: dict[str, tuple[str, ...]] = {
    ".acting": (
        "ActorExecutionError", "BedrockNovaActor", "BedrockNovaActorSettings",
        "OpenAIActor", "OpenAIActorSettings", "build_default_actor",
    ),
    ".actor_contract": (
        "ActorPastStep", "ActorPlanStep", "ActorPort", "ActorResult",
        "ActorResultType", "ActorTask", "CapabilityAccessMode", "build_actor_task",
    ),
    ".behavioral": (
        "BehavioralAssessment", "BehavioralRecommendedDirective", "BehavioralStorePort",
        "BehavioralStoreSettings", "SQLiteBehavioralStore", "build_behavioral_assessment",
        "build_default_behavioral_store", "evaluate_behavioral_policy",
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
        "build_default_checkpointer", "build_runtime_graph",
    ),
    ".composition": ("Layer2RuntimeDependencies", "compose_layer2_runtime"),
    ".judge": (
        "DisabledJudge", "JudgeAssessment", "JudgeExecutionError", "JudgePort",
        "JudgeRecommendedDirective", "JudgeSettings", "OpenAIJudge", "build_default_judge",
        "build_judge_system_prompt", "build_judge_user_prompt", "evaluate_judge",
        "should_invoke_judge",
    ),
    ".hitl_dashboard": (
        "DashboardBehavioralPanel", "DashboardExternalCommunicationPanel", "DashboardFocus",
        "DashboardGovernancePanel", "DashboardHumanReviewPanel", "DashboardOversightPanel",
        "DashboardPlanStep", "GovernanceHitlDashboardSnapshot",
        "build_governance_hitl_dashboard_snapshot", "governance_hitl_dashboard_snapshot_to_dict",
        "governance_hitl_dashboard_snapshot_to_json",
    ),
    ".runtime": (
        "actor", "bootstrap_from_intake", "finalize_bootstrap", "human_review",
        "observe_tool_result", "planner", "replanner", "tool_gateway",
    ),
    ".hitl": ("build_human_review_request",),
    ".oversight": (
        "DynamicPolicyAssessment", "DynamicPolicyRecommendedDirective",
        "EvidenceQualityAssessment", "EvidenceRequirementResolution",
        "EvidenceRequirementSource", "EvidenceQualityWeights", "OperationalRiskAssessment",
        "OversightAssessment", "OversightPolicyConfig", "OversightReasonCode",
        "OversightRecommendedDirective", "OversightSignalLevel", "RiskScores",
        "TrajectoryHealthAssessment", "TrajectoryPenalties",
        "build_default_oversight_policy_config", "evaluate_confidence_risk_trajectory",
        "evaluate_dynamic_policy", "evaluate_evidence_quality", "evaluate_operational_risk",
        "evaluate_trajectory_health", "get_default_oversight_policy_config_path",
        "load_oversight_policy_config", "resolve_required_evidence_support_context",
    ),
    ".persistence": (
        "build_sqlite_checkpointer", "RuntimeArtifactPaths", "RuntimeArtifactStore",
        "get_default_checkpointer_path", "get_default_runtime_artifacts_root",
        "persist_runtime_artifacts",
    ),
    ".planning": (
        "OpenAIPlanner", "OpenAIPlannerSettings", "PlannerExecutionError", "PlannerPort",
        "build_default_planner",
    ),
    ".replanning": (
        "LocalConservativeReplanner", "OpenAIReplanner", "OpenAIReplannerSettings",
        "ReplanDecision", "ReplannerExecutionError", "ReplannerPort",
        "build_default_replanner", "build_replanner_system_prompt", "build_replanner_user_prompt",
    ),
    ".domain": (
        "ExecutedPlanStep", "ExternalCommunicationPhase", "ExternalCommunicationDraft",
        "ExternalCommunicationState", "HumanReviewDecision", "HumanReviewDecisionType",
        "HumanReviewExternalEdit", "HumanReviewRequest", "PlanStep", "PlanStepKind",
        "PlanStepStatus", "PlannerDecision", "RuntimeFlag", "RuntimeState",
        "ScratchpadEntry", "ScratchpadEntryType", "StepRecord", "StepValidation",
        "StepValidationDecision", "TaskState",
    ),
    ".tool_contract": (
        "EvidenceSourceKind", "EvidenceSupportLevel", "ObservedEvidence",
        "ToolExecutionResult", "ToolGatewayPort", "ToolName", "ToolRequest", "ToolSource",
    ),
    ".tool_gateway": (
        "McpServerDefinition", "OpenAIToolGateway", "OpenAIToolGatewaySettings", "ToolBackend",
        "ToolCapability", "ToolDefinition", "ToolExecutionError", "ToolGatewayConfig",
        "ToolRiskLevel", "build_default_tool_gateway", "build_default_tool_gateway_config",
        "get_default_tool_gateway_config_path", "load_tool_gateway_config",
    ),
    ".observability": (
        "Layer2RuntimeTrace", "TraceActorTurn", "TraceBehavioralAssessment",
        "TraceExecutionSummary", "TraceExternalCommunicationDraft",
        "TraceExternalCommunicationSummary", "TraceGovernanceDecision",
        "TraceHumanReviewDecision", "TraceHumanReviewExternalEdit", "TraceHumanReviewRequest",
        "TraceHumanReviewSummary", "TraceIntakeSummary", "TraceJudgeAssessment",
        "TraceObservedEvidence", "TraceOversightAssessment", "TracePlanStep",
        "TraceRuntimeSummary", "TraceStaticPolicyAssessment", "TraceToolCall",
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
