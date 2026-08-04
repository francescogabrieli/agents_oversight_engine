from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DynamicPolicyRecommendedDirective(str, Enum):
    CONTINUE = "continue"
    CONTINUE_READ_ONLY = "continue_read_only"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REPLAN = "replan"
    WAIT_HUMAN_REVIEW = "wait_human_review"


# Preserve checkpoint vocabulary while OPA remains the only final directive authority.
OversightRecommendedDirective = DynamicPolicyRecommendedDirective


class OversightSignalLevel(str, Enum):
    UNSCORED = "unscored"
    OBSERVED = "observed"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    HEALTHY = "healthy"
    WATCH = "watch"
    DEGRADED = "degraded"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class OversightReasonCode(str, Enum):
    EVIDENCE_MARKED_SUFFICIENT = "evidence_marked_sufficient"
    OBJECTIVE_MARKED_SATISFIED = "objective_marked_satisfied"
    ACTOR_CONFIDENCE_CONTRIBUTED = "actor_confidence_contributed"
    ACTOR_CONFIDENCE_BELOW_THRESHOLD = "actor_confidence_below_threshold"
    OBSERVATIONS_PRESENT = "observations_present"
    OBSERVATIONS_MISSING = "observations_missing"
    SOURCE_LOCATOR_PRESENT = "source_locator_present"
    SOURCE_LOCATOR_MISSING = "source_locator_missing"
    LAST_TOOL_SUCCESS = "last_tool_success"
    LAST_TOOL_FAILED = "last_tool_failed"
    EVIDENCE_SUPPORT_MATCHES_STEP_NEED = "evidence_support_matches_step_need"
    EVIDENCE_SUPPORT_BELOW_STEP_NEED = "evidence_support_below_step_need"
    TOOL_RISK_LOW = "tool_risk_low"
    TOOL_RISK_MEDIUM = "tool_risk_medium"
    TOOL_RISK_HIGH = "tool_risk_high"
    TOOL_RISK_NONE = "tool_risk_none"
    TOOL_RISK_UNKNOWN = "tool_risk_unknown"
    TASK_SENSITIVITY_LOW = "task_sensitivity_low"
    TASK_SENSITIVITY_MEDIUM = "task_sensitivity_medium"
    TASK_SENSITIVITY_HIGH = "task_sensitivity_high"
    TASK_SENSITIVITY_CRITICAL = "task_sensitivity_critical"
    NO_CAPABILITY = "no_capability"
    NO_TOOL_REQUEST = "no_tool_request"
    READ_ONLY_CAPABILITY = "read_only_capability"
    WRITE_CAPABILITY = "write_capability"
    EXTERNAL_IMPACT = "external_impact"
    TOOL_REQUIRES_HUMAN_REVIEW = "tool_requires_human_review"
    ACTOR_TURNS_WITHIN_LIMIT = "actor_turns_within_limit"
    ACTOR_TURNS_EXCEEDED = "actor_turns_exceeded"
    COMPLETION_ATTEMPT_TURN_OVERAGE_SOFTENED = "completion_attempt_turn_overage_softened"
    NO_FAILED_TOOL_RESULTS = "no_failed_tool_results"
    FAILED_TOOL_RESULT_PRESENT = "failed_tool_result_present"
    NO_REPLAN_NEEDED = "no_replan_needed"
    NEEDS_REPLAN_SIGNAL = "needs_replan_signal"
    REPLAN_LIMIT_EXCEEDED = "replan_limit_exceeded"
    LOW_CONFIDENCE_UNCERTAINTY = "low_confidence_uncertainty"
    RECOMMEND_CONTINUE = "recommend_continue"
    RECOMMEND_MORE_EVIDENCE = "recommend_more_evidence"
    RECOMMEND_REPLAN = "recommend_replan"
    RECOMMEND_HUMAN_REVIEW = "recommend_human_review"


@dataclass(frozen=True)
class EvidenceQualityAssessment:
    score: float | None
    level: OversightSignalLevel
    reason_codes: tuple[OversightReasonCode, ...]


@dataclass(frozen=True)
class OperationalRiskAssessment:
    score: float | None
    level: OversightSignalLevel
    reason_codes: tuple[OversightReasonCode, ...]
    mode: str = "shadow"
    decision_influence: bool = False
    applicability: str = "not_applicable"
    recoverability: str = "unknown"
    blast_radius: str = "unknown"
    sensitivity: str = "unknown"
    exposure: str = "unknown"
    target_criticality: str = "unknown"
    impact_level: str = "unknown"
    dominant_factors: tuple[str, ...] = ()
    unknown_facts: tuple[str, ...] = ()
    fact_provenance: tuple[str, ...] = ()
    profile_version: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class TrajectoryHealthAssessment:
    score: float | None
    level: OversightSignalLevel
    reason_codes: tuple[OversightReasonCode, ...]


@dataclass(frozen=True)
class DynamicPolicyAssessment:
    evidence_quality: EvidenceQualityAssessment
    operational_risk: OperationalRiskAssessment
    trajectory_health: TrajectoryHealthAssessment
    recommended_directive: DynamicPolicyRecommendedDirective
    summary: str
    reason_codes: tuple[OversightReasonCode, ...]


OversightAssessment = DynamicPolicyAssessment


__all__ = [
    "DynamicPolicyAssessment",
    "DynamicPolicyRecommendedDirective",
    "EvidenceQualityAssessment",
    "OperationalRiskAssessment",
    "OversightAssessment",
    "OversightReasonCode",
    "OversightRecommendedDirective",
    "OversightSignalLevel",
    "TrajectoryHealthAssessment",
]
