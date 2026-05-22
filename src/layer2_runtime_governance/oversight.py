from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from layer1_intake import SensitivityLevel

from .actor_contract import ActorResultType, CapabilityAccessMode
from .state import StepValidation, StepValidationDecision, TaskState
from .tool_contract import EvidenceSupportLevel
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


class DynamicPolicyRecommendedDirective(str, Enum):
    # Dynamic recommendations mirror governance directives without importing governance.py.
    CONTINUE = "continue"
    CONTINUE_READ_ONLY = "continue_read_only"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REPLAN = "replan"
    WAIT_HUMAN_REVIEW = "wait_human_review"


# Keep the legacy public name stable while the dynamic policy layer is renamed explicitly.
OversightRecommendedDirective = DynamicPolicyRecommendedDirective


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
    EVIDENCE_SUPPORT_MATCHES_STEP_NEED = "evidence_support_matches_step_need"
    EVIDENCE_SUPPORT_BELOW_STEP_NEED = "evidence_support_below_step_need"
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
    completion_attempt_turn_overage_scale: float
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
class DynamicPolicyAssessment:
    # DynamicPolicyAssessment is the deterministic adaptive governance record stored in TaskState.
    evidence_quality: EvidenceQualityAssessment
    operational_risk: OperationalRiskAssessment
    trajectory_health: TrajectoryHealthAssessment
    recommended_directive: DynamicPolicyRecommendedDirective
    summary: str
    reason_codes: tuple[OversightReasonCode, ...]


# Keep the legacy public name stable while the dynamic policy layer is renamed explicitly.
OversightAssessment = DynamicPolicyAssessment


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
            max_actor_turns_per_step=int(trajectory_penalties.get("max_actor_turns_per_step", 5)),
            max_replans=int(trajectory_penalties.get("max_replans", 2)),
            actor_turn_overage_penalty=float(
                trajectory_penalties.get("actor_turn_overage_penalty", 0.10)
            ),
            completion_attempt_turn_overage_scale=float(
                trajectory_penalties.get(
                    "completion_attempt_turn_overage_scale",
                    0.50,
                )
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
            max_actor_turns_per_step=5,
            max_replans=2,
            actor_turn_overage_penalty=0.10,
            completion_attempt_turn_overage_scale=0.50,
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
) -> DynamicPolicyAssessment:
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

    return DynamicPolicyAssessment(
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


# Keep the new naming explicit while preserving the old evaluation entrypoint.
def evaluate_dynamic_policy(
    *,
    task_state: TaskState,
    policy_input: "PolicyEvaluationInput",
    config: OversightPolicyConfig | None = None,
    tool_gateway_config: ToolGatewayConfig | None = None,
) -> DynamicPolicyAssessment:
    return evaluate_confidence_risk_trajectory(
        task_state=task_state,
        policy_input=policy_input,
        config=config,
        tool_gateway_config=tool_gateway_config,
    )


# Score the current evidence base against actor validation and provenance signals.
def evaluate_evidence_quality(
    *,
    task_state: TaskState,
    config: OversightPolicyConfig,
) -> EvidenceQualityAssessment:
    validation = _latest_relevant_validation(task_state=task_state)
    relevant_tool_result = _latest_tool_result_for_evaluated_step(task_state=task_state)
    required_support_level = _required_evidence_support_level(task_state=task_state)
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

    if relevant_tool_result is not None and relevant_tool_result.success:
        score += weights.last_tool_success
        reason_codes.append(OversightReasonCode.LAST_TOOL_SUCCESS)
    elif relevant_tool_result is not None:
        reason_codes.append(OversightReasonCode.LAST_TOOL_FAILED)

    if _evidence_support_matches_step_need(task_state=task_state):
        reason_codes.append(OversightReasonCode.EVIDENCE_SUPPORT_MATCHES_STEP_NEED)
    else:
        score = min(score, config.min_evidence_quality * 0.49)
        reason_codes.append(OversightReasonCode.EVIDENCE_SUPPORT_BELOW_STEP_NEED)

    if (
        required_support_level == EvidenceSupportLevel.LOCAL_ONLY
        and validation is not None
        and validation.next_action == StepValidationDecision.COMPLETE_STEP
        and validation.objective_satisfied
        and validation.evidence_sufficient
        and validation.confidence >= config.min_actor_confidence
    ):
        score = max(score, config.min_evidence_quality)

    score = _clamp(score)
    return EvidenceQualityAssessment(
        score=score,
        level=_evidence_level(score=score, config=config),
        reason_codes=tuple(reason_codes),
    )


# Keep status-only or content-free observations from inflating evidence quality.
def _meaningful_observed_evidence(*, task_state: TaskState):
    return [
        item
        for item in task_state.observed_evidence
        if _is_meaningful_observed_evidence_item(item=item)
    ]


# Evaluate evidence substance using support level and excerpt shape, not only raw length.
def _is_meaningful_observed_evidence_item(*, item) -> bool:
    normalized_excerpt = _normalize_evidence_excerpt(item.excerpt)
    if not normalized_excerpt:
        return False

    lowered_excerpt = normalized_excerpt.lower()
    if lowered_excerpt in {
        "url text fetched successfully.",
        "web search executed successfully.",
        "tool succeeded.",
    }:
        return False

    word_count = len(normalized_excerpt.split())
    if item.support_level == EvidenceSupportLevel.SOURCE_DISCOVERY:
        if _looks_like_source_shortlist(normalized_excerpt):
            return True
        return bool(item.locator.strip()) and word_count >= 5

    if item.support_level == EvidenceSupportLevel.HIGH_LEVEL_SUMMARY:
        if word_count >= 8:
            return True
        return len(normalized_excerpt) >= 80 and _contains_substantive_content_markers(lowered_excerpt)

    if item.support_level in {
        EvidenceSupportLevel.DETAILED_EXTRACTED,
        EvidenceSupportLevel.PRIMARY_LEGAL,
    }:
        if word_count >= 5:
            return True
        return _contains_substantive_content_markers(lowered_excerpt) and len(normalized_excerpt) >= 30

    return word_count >= 8


# Normalize whitespace before lightweight evidence heuristics run.
def _normalize_evidence_excerpt(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.split())


# Detect source-discovery evidence that is mostly a compact shortlist of URLs and source names.
def _looks_like_source_shortlist(text: str) -> bool:
    lowered_text = text.lower()
    if "http://" in lowered_text or "https://" in lowered_text:
        return True
    return any(
        marker in lowered_text
        for marker in (
            "official source",
            "official sources",
            "source shortlist",
            "eur-lex",
            "european commission",
            "portal",
            "legislation page",
        )
    )


# Detect content markers that suggest the excerpt carries substantive claims.
def _contains_substantive_content_markers(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "purpose",
            "scope",
            "overview",
            "summary",
            "applies to",
            "aims to",
            "objective",
            "obligation",
            "obligations",
            "prohibition",
            "criteria",
            "threshold",
            "deadline",
            "entered into force",
            "applies from",
            "article ",
            "must ",
            "shall ",
            "required to",
            "interoperability",
            "data portability",
            "gatekeeper",
            "gatekeepers",
        )
    )


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
    current_step_turn_count = _actor_turn_count_for_evaluated_step(task_state=task_state)
    relevant_tool_result = _latest_tool_result_for_evaluated_step(task_state=task_state)
    score = 1.0
    reason_codes: list[OversightReasonCode] = []

    actor_turn_overage = max(
        current_step_turn_count - penalties.max_actor_turns_per_step,
        0,
    )
    if actor_turn_overage:
        turn_overage_penalty = penalties.actor_turn_overage_penalty
        if (
            validation is not None
            and validation.next_action == StepValidationDecision.COMPLETE_STEP
            and validation.objective_satisfied
            and validation.evidence_sufficient
            and validation.confidence >= config.min_actor_confidence
        ):
            turn_overage_penalty *= penalties.completion_attempt_turn_overage_scale
            reason_codes.append(
                OversightReasonCode.COMPLETION_ATTEMPT_TURN_OVERAGE_SOFTENED
            )
        score -= actor_turn_overage * turn_overage_penalty
        reason_codes.append(OversightReasonCode.ACTOR_TURNS_EXCEEDED)
    else:
        reason_codes.append(OversightReasonCode.ACTOR_TURNS_WITHIN_LIMIT)

    failed_tool_results = (
        1 if relevant_tool_result is not None and not relevant_tool_result.success else 0
    )
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


# Resolve the currently evaluated plan step id used by adaptive oversight.
def _evaluated_plan_step_id(*, task_state: TaskState) -> str | None:
    if task_state.actor_result is not None:
        return task_state.actor_result.plan_step_id
    if task_state.current_plan_step is not None:
        return task_state.current_plan_step.step_id
    return None


# Count only actor turns that belong to the evaluated semantic step.
def _actor_turn_count_for_evaluated_step(*, task_state: TaskState) -> int:
    plan_step_id = _evaluated_plan_step_id(task_state=task_state)
    if plan_step_id is None:
        return 0

    # Scratchpad turn indexes are the most reliable per-turn markers in the runtime trace.
    scratchpad_turns = {
        entry.turn_index
        for entry in task_state.scratchpad
        if entry.plan_step_id == plan_step_id
    }
    if scratchpad_turns:
        return len(scratchpad_turns)

    validation_count = sum(
        1 for validation in task_state.step_validations if validation.plan_step_id == plan_step_id
    )
    if validation_count:
        return validation_count

    if task_state.actor_result is not None and task_state.actor_result.plan_step_id == plan_step_id:
        return 1

    return 0


# Read only the tool result that belongs to the currently evaluated semantic step.
def _latest_tool_result_for_evaluated_step(*, task_state: TaskState):
    plan_step_id = _evaluated_plan_step_id(task_state=task_state)
    if plan_step_id is None:
        return None

    if (
        task_state.last_tool_result is not None
        and task_state.last_tool_result.request.plan_step_id == plan_step_id
    ):
        return task_state.last_tool_result

    for tool_result in reversed(task_state.tool_results_history):
        if tool_result.request.plan_step_id == plan_step_id:
            return tool_result

    return None


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
) -> DynamicPolicyRecommendedDirective:
    if operational_risk.score > config.max_operational_risk:
        return DynamicPolicyRecommendedDirective.WAIT_HUMAN_REVIEW

    if OversightReasonCode.REPLAN_LIMIT_EXCEEDED in trajectory_health.reason_codes:
        return DynamicPolicyRecommendedDirective.WAIT_HUMAN_REVIEW

    if trajectory_health.score < config.min_trajectory_health:
        return DynamicPolicyRecommendedDirective.REPLAN

    if (
        _is_completion_attempt(policy_input=policy_input)
        and evidence_quality.score < config.min_evidence_quality
        and _validation_signals_weak_evidence(task_state=task_state, config=config)
    ):
        return DynamicPolicyRecommendedDirective.REQUEST_MORE_EVIDENCE

    if policy_input.actor_result_type == ActorResultType.PROPOSE_ACTION:
        return DynamicPolicyRecommendedDirective.CONTINUE_READ_ONLY

    return DynamicPolicyRecommendedDirective.CONTINUE


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
        or not _evidence_support_matches_step_need(task_state=task_state)
    )


# Compare available evidence strength against the semantic/detail needs of the evaluated step.
def _evidence_support_matches_step_need(*, task_state: TaskState) -> bool:
    available_level = _available_evidence_support_level(task_state=task_state)
    required_level = _required_evidence_support_level(task_state=task_state)
    return _evidence_support_level_rank(available_level) >= _evidence_support_level_rank(required_level)


# Return the strongest evidence support level currently available to the actor.
def _available_evidence_support_level(*, task_state: TaskState) -> EvidenceSupportLevel:
    meaningful_evidence = _meaningful_observed_evidence(task_state=task_state)
    if not meaningful_evidence:
        return EvidenceSupportLevel.LOCAL_ONLY
    return max((item.support_level for item in meaningful_evidence), key=_evidence_support_level_rank)


# Infer the minimum support level needed by the evaluated step and its current draft output.
def _required_evidence_support_level(*, task_state: TaskState) -> EvidenceSupportLevel:
    step = _evaluated_plan_step(task_state=task_state)
    output_text = task_state.actor_result.output_text if task_state.actor_result is not None else None
    required_level = _required_evidence_support_level_from_step(step_text=_step_scope_text(step))
    if output_text and output_text.strip():
        output_level = _required_evidence_support_level_from_output_text(text=output_text)
        if _evidence_support_level_rank(output_level) > _evidence_support_level_rank(required_level):
            return output_level
    return required_level


# Resolve the evaluated semantic plan step for deterministic oversight checks.
def _evaluated_plan_step(*, task_state: TaskState):
    plan_step_id = _evaluated_plan_step_id(task_state=task_state)
    if plan_step_id is None:
        return task_state.current_plan_step
    for step in task_state.plan_steps:
        if step.step_id == plan_step_id:
            return step
    return task_state.current_plan_step


# Flatten the evaluated step scope into one lowercase text block for heuristic classification.
def _step_scope_text(step) -> str:
    if step is None:
        return ""
    return " ".join(
        part.strip()
        for part in (step.objective, step.expected_outcome, step.rationale)
        if part and part.strip()
    ).lower()


# Determine the minimum evidence strength implied by the step wording.
def _required_evidence_support_level_from_step(*, step_text: str) -> EvidenceSupportLevel:
    if _step_is_source_discovery_only(step_text):
        return EvidenceSupportLevel.SOURCE_DISCOVERY

    if _step_allows_conservative_high_level_summary(step_text):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    if any(
        marker in step_text
        for marker in (
            "deadline",
            "deadlines",
            "threshold",
            "thresholds",
            "turnover",
            "number of users",
            "article ",
            "full legal text",
        )
    ):
        return EvidenceSupportLevel.PRIMARY_LEGAL

    if any(
        marker in step_text
        for marker in (
            "official source",
            "official sources",
            "official pages",
            "official website",
            "official portal",
            "legislation page",
            "portal",
            "short summary",
            "summary",
            "overview",
            "high-level",
            "concise",
            "gatekeeper",
            "gatekeepers",
            "core platform services",
            "do's and don'ts",
            "what the dma is",
            "objectives",
            "legal instrument",
            "citation of the legal instrument",
        )
    ):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    if any(
        marker in step_text
        for marker in (
            "specific obligation",
            "specific obligations",
            "obligations-by-number",
            "obligation-by-obligation",
            "specific prohibition",
            "specific prohibitions",
            "specific requirements",
            "clause text",
            "citation-ready mapping",
            "claim mapping",
            "criteria",
            "enforcement mechanism",
            "what it requires",
            "who it applies to",
            "regulatory mechanisms",
            "main obligations",
            "entry into force",
            "timeline",
            "applicability details",
        )
    ):
        return EvidenceSupportLevel.DETAILED_EXTRACTED

    if any(
        marker in step_text
        for marker in ("purpose", "scope", "brief summary")
    ):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    return EvidenceSupportLevel.LOCAL_ONLY


# Determine the minimum evidence strength implied by the actual actor draft.
def _required_evidence_support_level_from_output_text(*, text: str) -> EvidenceSupportLevel:
    lowered_text = _normalize_output_text_for_support_checks(text).lower()

    if _output_text_is_source_shortlist_only(text.lower()):
        return EvidenceSupportLevel.SOURCE_DISCOVERY

    if _output_text_is_conservative_high_level_summary(lowered_text):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    if _contains_primary_precision_markers(lowered_text):
        if any(
            marker in lowered_text
            for marker in (
                "threshold",
                "thresholds",
                "turnover",
                "number of users",
                "specific size",
                "article ",
                "deadline",
                "deadlines",
            )
        ):
            return EvidenceSupportLevel.PRIMARY_LEGAL

        if any(
            marker in lowered_text
            for marker in (
                "entered into force",
                "comes into force",
                "coming into force",
                "effective date",
                "effective from",
                "applies from",
                "applies on",
                "entry into force",
                "adopted on",
                "published on",
                "timeline",
                "implementation date",
                "applicability details",
            )
        ):
            return EvidenceSupportLevel.DETAILED_EXTRACTED

        if any(
            marker in lowered_text
            for marker in (
                "percentages",
                "large numeric thresholds",
                "monetary thresholds",
            )
        ):
            return EvidenceSupportLevel.PRIMARY_LEGAL

        return EvidenceSupportLevel.DETAILED_EXTRACTED

    if any(
        marker in lowered_text
        for marker in (
            "specific obligation",
            "specific obligations",
            "prohibit",
            "prohibiting",
            "must",
            "shall",
            "cannot",
            "criteria",
            "interoperability",
            "data portability",
            "ranking",
            "self-preferencing",
            "non-discriminatory access",
        )
    ):
        return EvidenceSupportLevel.DETAILED_EXTRACTED

    if any(
        marker in lowered_text
        for marker in (
            "summary",
            "overview",
            "aims to",
            "applies to",
            "official sources",
        )
    ):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    return EvidenceSupportLevel.LOCAL_ONLY


# Treat explicitly conservative summary steps as high-level even when they mention broad themes.
def _step_allows_conservative_high_level_summary(step_text: str) -> bool:
    summary_markers = (
        "short summary",
        "brief summary",
        "few sentences",
        "high level",
        "high-level",
        "purpose/scope-level",
        "conservative",
        "broad categories",
        "obligation themes",
        "does not require",
        "do not require",
    )
    return any(marker in step_text for marker in summary_markers)


# Keep compact policy summaries from being treated like clause-level extraction tasks.
def _output_text_is_conservative_high_level_summary(text: str) -> bool:
    if _contains_primary_precision_markers(text):
        return False

    high_level_markers = (
        "brief summary",
        "short summary",
        "overview",
        "purpose",
        "aims to",
        "intended to",
        "in general",
        "at a high level",
        "gatekeeper",
        "gatekeepers",
        "core platform services",
        "official sources",
    )
    if not any(marker in text for marker in high_level_markers):
        return False

    granular_markers = (
        "article ",
        "threshold",
        "thresholds",
        "turnover",
        "number of users",
        "specific size",
        "must ",
        "shall ",
        "cannot ",
        "non-discriminatory access",
        "self-preferencing",
        "data portability",
        "interoperability",
        "ranking",
    )
    return not any(marker in text for marker in granular_markers)


# Detect drafts that only name sources and URLs without summarizing their substance.
def _output_text_is_source_shortlist_only(text: str) -> bool:
    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]
    if not lines:
        return False

    if any(
        marker in text
        for marker in (
            "aims to",
            "applies to",
            "purpose",
            "scope",
            "obligation",
            "obligations",
            "requires",
            "requirement",
            "criteria",
            "timeline",
            "entry into force",
        )
    ):
        return False

    source_identity_markers = (
        "official sources",
        "official eu sources",
        "shortlist of official",
        "source shortlist",
        "dma portal",
        "summary page",
        "legislation page",
        "eur-lex",
        "european commission",
    )
    if not any(marker in text for marker in source_identity_markers):
        return False

    return any("http://" in line or "https://" in line for line in lines)


# Keep link/citation formatting from inflating support-level checks.
def _normalize_output_text_for_support_checks(text: str) -> str:
    without_citations = re.sub(r"\[\d+\]", " ", text)
    return " ".join(without_citations.split())


# Detect exact legal/numeric detail that needs primary-source support.
def _contains_primary_precision_markers(text: str) -> bool:
    precision_context_markers = (
        "entered into force",
        "comes into force",
        "coming into force",
        "effective date",
        "effective from",
        "applies from",
        "applies on",
        "entry into force",
        "adopted on",
        "published on",
        "deadline",
        "deadlines",
        "timeline",
        "applicability details",
        "implementation date",
    )
    has_precision_context = any(marker in text for marker in precision_context_markers)

    if has_precision_context and re.search(r"\b(?:19|20)\d{2}\b", text):
        return True
    if has_precision_context and re.search(
        r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    return any(
        marker in text
        for marker in (
            "threshold",
            "thresholds",
            "turnover",
            "number of users",
            "specific size",
            "article ",
            "deadline",
            "deadlines",
        )
    )


# Distinguish source-list steps from semantic extraction/summarization steps.
def _step_is_source_discovery_only(step_text: str) -> bool:
    discovery_markers = ("identify", "find", "source", "sources", "links", "references", "citations")
    summary_markers = (
        "summary",
        "summar",
        "purpose",
        "scope",
        "obligation",
        "requirement",
        "criteria",
        "timeline",
        "what it requires",
    )
    return any(marker in step_text for marker in discovery_markers) and not any(
        marker in step_text for marker in summary_markers
    )


# Order evidence support levels from weakest to strongest.
def _evidence_support_level_rank(level: EvidenceSupportLevel) -> int:
    ordering = {
        EvidenceSupportLevel.LOCAL_ONLY: 0,
        EvidenceSupportLevel.SOURCE_DISCOVERY: 1,
        EvidenceSupportLevel.HIGH_LEVEL_SUMMARY: 2,
        EvidenceSupportLevel.DETAILED_EXTRACTED: 3,
        EvidenceSupportLevel.PRIMARY_LEGAL: 4,
    }
    return ordering[level]


# Map one recommendation to a compact reason code.
def _recommendation_reason_code(
    recommended_directive: DynamicPolicyRecommendedDirective,
) -> OversightReasonCode:
    if recommended_directive == DynamicPolicyRecommendedDirective.WAIT_HUMAN_REVIEW:
        return OversightReasonCode.RECOMMEND_HUMAN_REVIEW
    if recommended_directive == DynamicPolicyRecommendedDirective.REPLAN:
        return OversightReasonCode.RECOMMEND_REPLAN
    if recommended_directive == DynamicPolicyRecommendedDirective.REQUEST_MORE_EVIDENCE:
        return OversightReasonCode.RECOMMEND_MORE_EVIDENCE
    if recommended_directive == DynamicPolicyRecommendedDirective.CONTINUE_READ_ONLY:
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
    "DynamicPolicyAssessment",
    "DynamicPolicyRecommendedDirective",
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
    "evaluate_dynamic_policy",
    "evaluate_evidence_quality",
    "evaluate_operational_risk",
    "evaluate_trajectory_health",
    "get_default_oversight_policy_config_path",
    "load_oversight_policy_config",
]
