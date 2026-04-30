from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from layer1_intake import SensitivityLevel

from .actor_contract import ActorResultType, CapabilityAccessMode
from .state import StepValidation, StepValidationDecision, TaskState
from .tool_gateway import (
    ToolCapability,
    ToolGatewayConfig,
    ToolRiskLevel,
    build_default_tool_gateway_config,
    get_default_tool_gateway_config_path,
    load_tool_gateway_config,
)

if TYPE_CHECKING:
    from .governance import PolicyEvaluationInput


class OversightRecommendedDirective(str, Enum):
    # Oversight recommendations mirror governance directives without importing governance.py.
    CONTINUE = "continue"
    CONTINUE_READ_ONLY = "continue_read_only"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REPLAN = "replan"
    WAIT_HUMAN_REVIEW = "wait_human_review"


# OversightSignalLevel keeps score interpretation explicit and compact.
class OversightSignalLevel(str, Enum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    HEALTHY = "healthy"
    WATCH = "watch"
    DEGRADED = "degraded"


# OversightReasonCode records why a deterministic oversight score moved.
class OversightReasonCode(str, Enum):
    EVIDENCE_MARKED_SUFFICIENT = "evidence_marked_sufficient"
    OBJECTIVE_MARKED_SATISFIED = "objective_marked_satisfied"
    ACTOR_CONFIDENCE_CONTRIBUTED = "actor_confidence_contributed"
    ACTOR_CONFIDENCE_BELOW_THRESHOLD = "actor_confidence_below_threshold"
    OBSERVED_EVIDENCE_PRESENT = "observed_evidence_present"
    OBSERVED_EVIDENCE_MISSING = "observed_evidence_missing"
    SOURCE_LOCATOR_PRESENT = "source_locator_present"
    SOURCE_LOCATOR_MISSING = "source_locator_missing"
    LAST_TOOL_SUCCESS = "last_tool_success"
    LAST_TOOL_FAILED = "last_tool_failed"
    TOOL_RISK_LOW = "tool_risk_low"
    TOOL_RISK_MEDIUM = "tool_risk_medium"
    TOOL_RISK_HIGH = "tool_risk_high"
    TOOL_RISK_UNKNOWN = "tool_risk_unknown"
    TASK_SENSITIVITY_LOW = "task_sensitivity_low"
    TASK_SENSITIVITY_MEDIUM = "task_sensitivity_medium"
    TASK_SENSITIVITY_HIGH = "task_sensitivity_high"
    TASK_SENSITIVITY_CRITICAL = "task_sensitivity_critical"
    READ_ONLY_CAPABILITY = "read_only_capability"
    WRITE_CAPABILITY = "write_capability"
    EXTERNAL_IMPACT = "external_impact"
    TOOL_REQUIRES_HUMAN_REVIEW = "tool_requires_human_review"
    ACTOR_TURNS_WITHIN_LIMIT = "actor_turns_within_limit"
    ACTOR_TURNS_EXCEEDED = "actor_turns_exceeded"
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
class EvidenceQualityWeights:
    # Weights keep evidence scoring explainable and configurable.
    evidence_sufficient: float
    objective_satisfied: float
    actor_confidence: float
    observed_evidence_present: float
    source_locator_present: float
    last_tool_success: float


@dataclass(frozen=True)
class RiskScores:
    # RiskScores maps configured labels into deterministic numeric risk.
    tool_risk: dict[str, float]
    sensitivity_multiplier: dict[str, float]
    external_impact_additive: float
    write_capability_additive: float
    requires_human_review_additive: float


@dataclass(frozen=True)
class TrajectoryPenalties:
    # TrajectoryPenalties defines how runtime degradation reduces health.
    max_actor_turns_per_step: int
    max_replans: int
    actor_turn_overage_penalty: float
    failed_tool_result_penalty: float
    needs_replan_penalty: float
    low_confidence_uncertainty_penalty: float


@dataclass(frozen=True)
class OversightPolicyConfig:
    # OversightPolicyConfig centralizes all deterministic scoring knobs.
    min_actor_confidence: float
    min_evidence_quality: float
    max_operational_risk: float
    min_trajectory_health: float
    evidence_quality_weights: EvidenceQualityWeights
    risk_scores: RiskScores
    trajectory_penalties: TrajectoryPenalties


@dataclass(frozen=True)
class EvidenceQualityAssessment:
    # EvidenceQualityAssessment estimates whether evidence supports the current outcome.
    score: float
    level: OversightSignalLevel
    reason_codes: tuple[OversightReasonCode, ...]


@dataclass(frozen=True)
class OperationalRiskAssessment:
    # OperationalRiskAssessment estimates the risk of the current actor intent.
    score: float
    level: OversightSignalLevel
    reason_codes: tuple[OversightReasonCode, ...]


@dataclass(frozen=True)
class TrajectoryHealthAssessment:
    # TrajectoryHealthAssessment estimates whether the runtime trajectory is healthy.
    score: float
    level: OversightSignalLevel
    reason_codes: tuple[OversightReasonCode, ...]


@dataclass(frozen=True)
class OversightAssessment:
    # OversightAssessment is the adaptive governance record stored in TaskState.
    evidence_quality: EvidenceQualityAssessment
    operational_risk: OperationalRiskAssessment
    trajectory_health: TrajectoryHealthAssessment
    recommended_directive: OversightRecommendedDirective
    summary: str
    reason_codes: tuple[OversightReasonCode, ...]


# Load deterministic oversight scoring settings from repository configuration.
def load_oversight_policy_config(*, config_path: Path | None = None) -> OversightPolicyConfig:
    resolved_path = config_path or get_default_oversight_policy_config_path()
    if not resolved_path.exists():
        return build_default_oversight_policy_config()

    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    thresholds = payload.get("thresholds", {})
    weights = payload.get("evidence_quality_weights", {})
    risk_scores = payload.get("risk_scores", {})
    trajectory_penalties = payload.get("trajectory_penalties", {})

    return OversightPolicyConfig(
        min_actor_confidence=float(thresholds.get("min_actor_confidence", 0.65)),
        min_evidence_quality=float(thresholds.get("min_evidence_quality", 0.70)),
        max_operational_risk=float(thresholds.get("max_operational_risk", 0.60)),
        min_trajectory_health=float(thresholds.get("min_trajectory_health", 0.60)),
        evidence_quality_weights=EvidenceQualityWeights(
            evidence_sufficient=float(weights.get("evidence_sufficient", 0.30)),
            objective_satisfied=float(weights.get("objective_satisfied", 0.20)),
            actor_confidence=float(weights.get("actor_confidence", 0.20)),
            observed_evidence_present=float(weights.get("observed_evidence_present", 0.15)),
            source_locator_present=float(weights.get("source_locator_present", 0.10)),
            last_tool_success=float(weights.get("last_tool_success", 0.05)),
        ),
        risk_scores=RiskScores(
            tool_risk={
                str(key): float(value)
                for key, value in risk_scores.get(
                    "tool_risk",
                    {"low": 0.15, "medium": 0.45, "high": 0.75, "critical": 0.95},
                ).items()
            },
            sensitivity_multiplier={
                str(key): float(value)
                for key, value in risk_scores.get(
                    "sensitivity_multiplier",
                    {"low": 1.0, "medium": 1.15, "high": 1.35, "critical": 1.6},
                ).items()
            },
            external_impact_additive=float(risk_scores.get("external_impact_additive", 0.25)),
            write_capability_additive=float(risk_scores.get("write_capability_additive", 0.30)),
            requires_human_review_additive=float(
                risk_scores.get("requires_human_review_additive", 0.40)
            ),
        ),
        trajectory_penalties=TrajectoryPenalties(
            max_actor_turns_per_step=int(trajectory_penalties.get("max_actor_turns_per_step", 3)),
            max_replans=int(trajectory_penalties.get("max_replans", 2)),
            actor_turn_overage_penalty=float(
                trajectory_penalties.get("actor_turn_overage_penalty", 0.15)
            ),
            failed_tool_result_penalty=float(
                trajectory_penalties.get("failed_tool_result_penalty", 0.20)
            ),
            needs_replan_penalty=float(trajectory_penalties.get("needs_replan_penalty", 0.25)),
            low_confidence_uncertainty_penalty=float(
                trajectory_penalties.get("low_confidence_uncertainty_penalty", 0.20)
            ),
        ),
    )


# Return the repository-local oversight policy configuration path.
def get_default_oversight_policy_config_path() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "configs" / "layer2_oversight_policy.json"


# Build a conservative fallback if the JSON policy file is unavailable.
def build_default_oversight_policy_config() -> OversightPolicyConfig:
    return OversightPolicyConfig(
        min_actor_confidence=0.65,
        min_evidence_quality=0.70,
        max_operational_risk=0.60,
        min_trajectory_health=0.60,
        evidence_quality_weights=EvidenceQualityWeights(
            evidence_sufficient=0.30,
            objective_satisfied=0.20,
            actor_confidence=0.20,
            observed_evidence_present=0.15,
            source_locator_present=0.10,
            last_tool_success=0.05,
        ),
        risk_scores=RiskScores(
            tool_risk={"low": 0.15, "medium": 0.45, "high": 0.75, "critical": 0.95},
            sensitivity_multiplier={"low": 1.0, "medium": 1.15, "high": 1.35, "critical": 1.6},
            external_impact_additive=0.25,
            write_capability_additive=0.30,
            requires_human_review_additive=0.40,
        ),
        trajectory_penalties=TrajectoryPenalties(
            max_actor_turns_per_step=3,
            max_replans=2,
            actor_turn_overage_penalty=0.15,
            failed_tool_result_penalty=0.20,
            needs_replan_penalty=0.25,
            low_confidence_uncertainty_penalty=0.20,
        ),
    )


# Evaluate the three deterministic adaptive oversight signals for one actor outcome.
def evaluate_confidence_risk_trajectory(
    *,
    task_state: TaskState,
    policy_input: "PolicyEvaluationInput",
    config: OversightPolicyConfig | None = None,
    tool_gateway_config: ToolGatewayConfig | None = None,
) -> OversightAssessment:
    resolved_config = config or load_oversight_policy_config()
    resolved_tool_config = tool_gateway_config or _load_tool_gateway_config_safely()

    evidence_quality = evaluate_evidence_quality(
        task_state=task_state,
        config=resolved_config,
    )
    operational_risk = evaluate_operational_risk(
        policy_input=policy_input,
        config=resolved_config,
        tool_gateway_config=resolved_tool_config,
    )
    trajectory_health = evaluate_trajectory_health(
        task_state=task_state,
        policy_input=policy_input,
        config=resolved_config,
    )
    recommended_directive = _recommend_directive(
        task_state=task_state,
        policy_input=policy_input,
        config=resolved_config,
        evidence_quality=evidence_quality,
        operational_risk=operational_risk,
        trajectory_health=trajectory_health,
    )
    reason_codes = _merge_reason_codes(
        evidence_quality.reason_codes,
        operational_risk.reason_codes,
        trajectory_health.reason_codes,
        (_recommendation_reason_code(recommended_directive),),
    )

    return OversightAssessment(
        evidence_quality=evidence_quality,
        operational_risk=operational_risk,
        trajectory_health=trajectory_health,
        recommended_directive=recommended_directive,
        summary=(
            f"Evidence quality is {evidence_quality.level.value} ({evidence_quality.score:.2f}), "
            f"operational risk is {operational_risk.level.value} ({operational_risk.score:.2f}), "
            f"and trajectory health is {trajectory_health.level.value} ({trajectory_health.score:.2f})."
        ),
        reason_codes=reason_codes,
    )


# Score the current evidence base against actor validation and provenance signals.
def evaluate_evidence_quality(
    *,
    task_state: TaskState,
    config: OversightPolicyConfig,
) -> EvidenceQualityAssessment:
    validation = _latest_relevant_validation(task_state=task_state)
    weights = config.evidence_quality_weights
    score = 0.0
    reason_codes: list[OversightReasonCode] = []

    if validation is not None and validation.evidence_sufficient:
        score += weights.evidence_sufficient
        reason_codes.append(OversightReasonCode.EVIDENCE_MARKED_SUFFICIENT)

    if validation is not None and validation.objective_satisfied:
        score += weights.objective_satisfied
        reason_codes.append(OversightReasonCode.OBJECTIVE_MARKED_SATISFIED)

    if validation is not None:
        score += weights.actor_confidence * _clamp(validation.confidence)
        reason_codes.append(OversightReasonCode.ACTOR_CONFIDENCE_CONTRIBUTED)
        if validation.confidence < config.min_actor_confidence:
            reason_codes.append(OversightReasonCode.ACTOR_CONFIDENCE_BELOW_THRESHOLD)

    meaningful_evidence = _meaningful_observed_evidence(task_state=task_state)
    if meaningful_evidence:
        score += weights.observed_evidence_present
        reason_codes.append(OversightReasonCode.OBSERVED_EVIDENCE_PRESENT)
    else:
        reason_codes.append(OversightReasonCode.OBSERVED_EVIDENCE_MISSING)

    if any(item.locator.strip() for item in meaningful_evidence):
        score += weights.source_locator_present
        reason_codes.append(OversightReasonCode.SOURCE_LOCATOR_PRESENT)
    else:
        reason_codes.append(OversightReasonCode.SOURCE_LOCATOR_MISSING)

    if task_state.last_tool_result is not None and task_state.last_tool_result.success:
        score += weights.last_tool_success
        reason_codes.append(OversightReasonCode.LAST_TOOL_SUCCESS)
    elif task_state.last_tool_result is not None:
        reason_codes.append(OversightReasonCode.LAST_TOOL_FAILED)

    score = _clamp(score)
    return EvidenceQualityAssessment(
        score=score,
        level=_evidence_level(score=score, config=config),
        reason_codes=tuple(reason_codes),
    )


# Keep status-only observations from inflating evidence quality.
def _meaningful_observed_evidence(*, task_state: TaskState):
    return [
        item
        for item in task_state.observed_evidence
        if item.excerpt and len(" ".join(item.excerpt.split())) >= 40
    ]


# Score operational risk from actor intent, task sensitivity, and tool registry metadata.
def evaluate_operational_risk(
    *,
    policy_input: "PolicyEvaluationInput",
    config: OversightPolicyConfig,
    tool_gateway_config: ToolGatewayConfig,
) -> OperationalRiskAssessment:
    reason_codes: list[OversightReasonCode] = []
    tool_definition = (
        tool_gateway_config.tools.get(policy_input.tool_name)
        if policy_input.tool_name is not None
        else None
    )
    tool_risk_level = (
        tool_definition.risk_level.value
        if tool_definition is not None
        else ToolRiskLevel.MEDIUM.value
    )
    if tool_definition is None and policy_input.tool_name is not None:
        reason_codes.append(OversightReasonCode.TOOL_RISK_UNKNOWN)
    else:
        reason_codes.append(_tool_risk_reason_code(tool_risk_level))

    sensitivity = policy_input.task_sensitivity.value
    reason_codes.append(_sensitivity_reason_code(policy_input.task_sensitivity))

    base_risk = config.risk_scores.tool_risk.get(tool_risk_level, 0.45)
    multiplier = config.risk_scores.sensitivity_multiplier.get(sensitivity, 1.15)
    score = base_risk * multiplier

    if policy_input.external_impact:
        score += config.risk_scores.external_impact_additive
        reason_codes.append(OversightReasonCode.EXTERNAL_IMPACT)

    if policy_input.capability_mode == CapabilityAccessMode.WRITE:
        score += config.risk_scores.write_capability_additive
        reason_codes.append(OversightReasonCode.WRITE_CAPABILITY)
    else:
        reason_codes.append(OversightReasonCode.READ_ONLY_CAPABILITY)

    if tool_definition is not None and tool_definition.requires_human_review:
        score += config.risk_scores.requires_human_review_additive
        reason_codes.append(OversightReasonCode.TOOL_REQUIRES_HUMAN_REVIEW)

    score = _clamp(score)
    return OperationalRiskAssessment(
        score=score,
        level=_risk_level(score=score, config=config),
        reason_codes=tuple(reason_codes),
    )


# Score trajectory health from loop pressure, failed tools, replan, and uncertainty signals.
def evaluate_trajectory_health(
    *,
    task_state: TaskState,
    policy_input: "PolicyEvaluationInput",
    config: OversightPolicyConfig,
) -> TrajectoryHealthAssessment:
    penalties = config.trajectory_penalties
    validation = _latest_relevant_validation(task_state=task_state)
    score = 1.0
    reason_codes: list[OversightReasonCode] = []

    actor_turn_overage = max(
        policy_input.actor_turn_count - penalties.max_actor_turns_per_step,
        0,
    )
    if actor_turn_overage:
        score -= actor_turn_overage * penalties.actor_turn_overage_penalty
        reason_codes.append(OversightReasonCode.ACTOR_TURNS_EXCEEDED)
    else:
        reason_codes.append(OversightReasonCode.ACTOR_TURNS_WITHIN_LIMIT)

    failed_tool_results = 1 if task_state.last_tool_result is not None and not task_state.last_tool_result.success else 0
    if failed_tool_results:
        score -= failed_tool_results * penalties.failed_tool_result_penalty
        reason_codes.append(OversightReasonCode.FAILED_TOOL_RESULT_PRESENT)
    else:
        reason_codes.append(OversightReasonCode.NO_FAILED_TOOL_RESULTS)

    if validation is not None and validation.next_action == StepValidationDecision.NEEDS_REPLAN:
        score -= penalties.needs_replan_penalty
        reason_codes.append(OversightReasonCode.NEEDS_REPLAN_SIGNAL)
    elif policy_input.actor_result_type == ActorResultType.NEEDS_REPLAN:
        score -= penalties.needs_replan_penalty
        reason_codes.append(OversightReasonCode.NEEDS_REPLAN_SIGNAL)
    else:
        reason_codes.append(OversightReasonCode.NO_REPLAN_NEEDED)

    if (
        validation is not None
        and validation.residual_uncertainty
        and validation.confidence < config.min_actor_confidence
    ):
        score -= penalties.low_confidence_uncertainty_penalty
        reason_codes.append(OversightReasonCode.LOW_CONFIDENCE_UNCERTAINTY)

    if _replan_count(task_state=task_state) >= penalties.max_replans:
        score = 0.0
        reason_codes.append(OversightReasonCode.REPLAN_LIMIT_EXCEEDED)

    score = _clamp(score)
    return TrajectoryHealthAssessment(
        score=score,
        level=_trajectory_level(score=score, config=config),
        reason_codes=tuple(reason_codes),
    )


# Find the latest validation for the actor's current evaluated step.
def _latest_relevant_validation(*, task_state: TaskState) -> StepValidation | None:
    if task_state.actor_result is None:
        return None

    for validation in reversed(task_state.step_validations):
        if validation.plan_step_id == task_state.actor_result.plan_step_id:
            return validation
    return task_state.actor_result.step_validation


# Count completed replanner passes from the canonical runtime step history.
def _replan_count(*, task_state: TaskState) -> int:
    return sum(1 for record in task_state.step_history if record.node_name == "replanner")


# Choose the adaptive recommendation without overriding hard OPA decisions.
def _recommend_directive(
    *,
    task_state: TaskState,
    policy_input: "PolicyEvaluationInput",
    config: OversightPolicyConfig,
    evidence_quality: EvidenceQualityAssessment,
    operational_risk: OperationalRiskAssessment,
    trajectory_health: TrajectoryHealthAssessment,
) -> OversightRecommendedDirective:
    if operational_risk.score > config.max_operational_risk:
        return OversightRecommendedDirective.WAIT_HUMAN_REVIEW

    if OversightReasonCode.REPLAN_LIMIT_EXCEEDED in trajectory_health.reason_codes:
        return OversightRecommendedDirective.WAIT_HUMAN_REVIEW

    if trajectory_health.score < config.min_trajectory_health:
        return OversightRecommendedDirective.REPLAN

    if (
        _is_completion_attempt(policy_input=policy_input)
        and evidence_quality.score < config.min_evidence_quality
        and _validation_signals_weak_evidence(task_state=task_state, config=config)
    ):
        return OversightRecommendedDirective.REQUEST_MORE_EVIDENCE

    if policy_input.actor_result_type == ActorResultType.PROPOSE_ACTION:
        return OversightRecommendedDirective.CONTINUE_READ_ONLY

    return OversightRecommendedDirective.CONTINUE


# Identify actor outcomes that attempt to complete or finalize a semantic step.
def _is_completion_attempt(*, policy_input: "PolicyEvaluationInput") -> bool:
    if policy_input.actor_result_type == ActorResultType.FINAL_OUTPUT:
        return True
    return policy_input.actor_result_type == ActorResultType.INTERMEDIATE_OUTPUT


# Require stronger evidence only when validation itself exposes a weak completion attempt.
def _validation_signals_weak_evidence(
    *,
    task_state: TaskState,
    config: OversightPolicyConfig,
) -> bool:
    validation = _latest_relevant_validation(task_state=task_state)
    if validation is None:
        return False
    if validation.next_action != StepValidationDecision.COMPLETE_STEP:
        return False
    return (
        not validation.evidence_sufficient
        or validation.confidence < config.min_actor_confidence
    )


# Map one recommendation to a compact reason code.
def _recommendation_reason_code(
    recommended_directive: OversightRecommendedDirective,
) -> OversightReasonCode:
    if recommended_directive == OversightRecommendedDirective.WAIT_HUMAN_REVIEW:
        return OversightReasonCode.RECOMMEND_HUMAN_REVIEW
    if recommended_directive == OversightRecommendedDirective.REPLAN:
        return OversightReasonCode.RECOMMEND_REPLAN
    if recommended_directive == OversightRecommendedDirective.REQUEST_MORE_EVIDENCE:
        return OversightReasonCode.RECOMMEND_MORE_EVIDENCE
    if recommended_directive == OversightRecommendedDirective.CONTINUE_READ_ONLY:
        return OversightReasonCode.RECOMMEND_CONTINUE
    return OversightReasonCode.RECOMMEND_CONTINUE


# Convert evidence score into a human-readable level.
def _evidence_level(*, score: float, config: OversightPolicyConfig) -> OversightSignalLevel:
    if score >= config.min_evidence_quality:
        return OversightSignalLevel.STRONG
    if score >= config.min_evidence_quality * 0.5:
        return OversightSignalLevel.MODERATE
    return OversightSignalLevel.WEAK


# Convert risk score into a human-readable level.
def _risk_level(*, score: float, config: OversightPolicyConfig) -> OversightSignalLevel:
    if score > config.max_operational_risk:
        return OversightSignalLevel.HIGH
    if score >= config.max_operational_risk * 0.5:
        return OversightSignalLevel.MEDIUM
    return OversightSignalLevel.LOW


# Convert trajectory score into a human-readable level.
def _trajectory_level(*, score: float, config: OversightPolicyConfig) -> OversightSignalLevel:
    if score >= config.min_trajectory_health:
        return OversightSignalLevel.HEALTHY
    if score >= config.min_trajectory_health * 0.5:
        return OversightSignalLevel.WATCH
    return OversightSignalLevel.DEGRADED


# Return the reason code matching the configured tool risk level.
def _tool_risk_reason_code(tool_risk_level: str) -> OversightReasonCode:
    if tool_risk_level == ToolRiskLevel.LOW.value:
        return OversightReasonCode.TOOL_RISK_LOW
    if tool_risk_level == ToolRiskLevel.HIGH.value:
        return OversightReasonCode.TOOL_RISK_HIGH
    return OversightReasonCode.TOOL_RISK_MEDIUM


# Return the reason code matching the task sensitivity level.
def _sensitivity_reason_code(sensitivity: SensitivityLevel) -> OversightReasonCode:
    if sensitivity == SensitivityLevel.LOW:
        return OversightReasonCode.TASK_SENSITIVITY_LOW
    if sensitivity == SensitivityLevel.HIGH:
        return OversightReasonCode.TASK_SENSITIVITY_HIGH
    if sensitivity == SensitivityLevel.CRITICAL:
        return OversightReasonCode.TASK_SENSITIVITY_CRITICAL
    return OversightReasonCode.TASK_SENSITIVITY_MEDIUM


# Load the Tool Gateway registry without making oversight construction fragile.
def _load_tool_gateway_config_safely() -> ToolGatewayConfig:
    try:
        return load_tool_gateway_config(config_path=get_default_tool_gateway_config_path())
    except Exception:
        return build_default_tool_gateway_config()


# Merge reason code groups while preserving first-seen order.
def _merge_reason_codes(*groups: tuple[OversightReasonCode, ...]) -> tuple[OversightReasonCode, ...]:
    merged: list[OversightReasonCode] = []
    for group in groups:
        for code in group:
            if code not in merged:
                merged.append(code)
    return tuple(merged)


# Keep score arithmetic bounded and predictable.
def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


__all__ = [
    "EvidenceQualityAssessment",
    "EvidenceQualityWeights",
    "OperationalRiskAssessment",
    "OversightAssessment",
    "OversightPolicyConfig",
    "OversightReasonCode",
    "OversightRecommendedDirective",
    "OversightSignalLevel",
    "RiskScores",
    "TrajectoryHealthAssessment",
    "TrajectoryPenalties",
    "build_default_oversight_policy_config",
    "evaluate_confidence_risk_trajectory",
    "evaluate_evidence_quality",
    "evaluate_operational_risk",
    "evaluate_trajectory_health",
    "get_default_oversight_policy_config_path",
    "load_oversight_policy_config",
]
