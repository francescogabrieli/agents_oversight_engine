from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .behavioral import BehavioralAssessment, BehavioralRecommendedDirective
from .judge import JudgeAssessment, JudgeRecommendedDirective
from .oversight import (
    DynamicPolicyAssessment,
    DynamicPolicyRecommendedDirective,
    OversightReasonCode,
    OversightSignalLevel,
)

if TYPE_CHECKING:
    from .governance import GovernanceDirective, StaticPolicyAssessment


class CombinerSignalSource(str, Enum):
    STATIC_POLICY = "static_policy"
    DYNAMIC_POLICY = "dynamic_policy"
    BEHAVIORAL = "behavioral"
    JUDGE = "judge"
    RUNTIME = "runtime"


class CombinerSignalDimension(str, Enum):
    STATIC_POLICY_VIOLATION = "static_policy_violation"
    EVIDENCE_GAP = "evidence_gap"
    OPERATIONAL_RISK = "operational_risk"
    TRAJECTORY_DEGRADATION = "trajectory_degradation"
    SEMANTIC_MISMATCH = "semantic_mismatch"
    HISTORICAL_ANOMALY = "historical_anomaly"
    EXTERNAL_IMPACT = "external_impact"
    CAPABILITY_RESTRICTION = "capability_restriction"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CombinerSignal:
    source: str
    dimension: str
    recommended_directive: "GovernanceDirective"
    severity: float
    confidence: float
    recoverable_by: tuple["GovernanceDirective", ...]
    rationale: str


@dataclass(frozen=True)
class DecisionCombinerResult:
    final_directive: "GovernanceDirective"
    static_envelope: "GovernanceDirective"
    signals: tuple[CombinerSignal, ...]
    dominant_dimension: str | None
    support_by_directive: dict["GovernanceDirective", float]
    selected_reason: str
    rejected_alternatives: dict["GovernanceDirective", str]
    safety_invariants_checked: tuple[str, ...]


# Keep the static policy envelope explicit before adaptive fusion begins.
def apply_static_policy_envelope(
    *,
    static_policy_assessment: "StaticPolicyAssessment",
) -> tuple["GovernanceDirective", "GovernanceDirective" | None]:
    from .governance import GovernanceDirective, PolicyDecision

    if static_policy_assessment.policy_decision == PolicyDecision.DENY:
        return GovernanceDirective.STOP, GovernanceDirective.STOP
    if static_policy_assessment.policy_decision == PolicyDecision.REQUIRE_HUMAN_REVIEW:
        return GovernanceDirective.WAIT_HUMAN_REVIEW, GovernanceDirective.WAIT_HUMAN_REVIEW
    if static_policy_assessment.policy_decision == PolicyDecision.ASK_CLARIFICATION:
        return GovernanceDirective.ASK_USER_CLARIFICATION, GovernanceDirective.ASK_USER_CLARIFICATION
    return static_policy_assessment.policy_directive, None


# Combine dynamic, behavioral, judge, and runtime signals into one minimal-safe decision.
def combine_decision_signals(
    *,
    static_policy_assessment: "StaticPolicyAssessment",
    dynamic_policy_assessment: DynamicPolicyAssessment | None = None,
    behavioral_assessment: BehavioralAssessment | None = None,
    judge_assessment: JudgeAssessment | None = None,
    external_impact: bool = False,
    validation_allows_completion: bool = False,
    repeated_recovery: bool = False,
    repeated_provider_failure: bool = False,
    provider_runtime_action_missing: bool = False,
) -> DecisionCombinerResult:
    from .governance import GovernanceDirective

    static_envelope, hard_directive = apply_static_policy_envelope(
        static_policy_assessment=static_policy_assessment
    )
    invariants = (
        "DENY is absorbing.",
        "REQUIRE_HUMAN_REVIEW cannot be downgraded.",
        "ASK_CLARIFICATION cannot be downgraded.",
        "Advisory layers cannot make static policy more permissive.",
        "Read-only restrictions must be preserved.",
        "External-impact actions require stricter handling.",
        "Low evidence should not produce high-precision final claims.",
        "Repeated recovery failures should escalate.",
        "Judge and behavioral signals cannot override hard policy.",
    )
    if hard_directive is not None:
        return DecisionCombinerResult(
            final_directive=hard_directive,
            static_envelope=static_envelope,
            signals=(),
            dominant_dimension=CombinerSignalDimension.STATIC_POLICY_VIOLATION.value,
            support_by_directive={hard_directive: 1.0},
            selected_reason=(
                "Static policy produced a hard directive, so advisory layers were not allowed to downgrade it."
            ),
            rejected_alternatives={},
            safety_invariants_checked=invariants,
        )

    signals = tuple(
        extract_static_signals(static_policy_assessment=static_policy_assessment)
        + extract_dynamic_signals(
            dynamic_policy_assessment=dynamic_policy_assessment,
            external_impact=external_impact,
        )
        + extract_behavioral_signals(behavioral_assessment=behavioral_assessment)
        + extract_judge_signals(
            judge_assessment=judge_assessment,
            external_impact=external_impact,
        )
        + extract_runtime_signals(
            repeated_recovery=repeated_recovery,
            repeated_provider_failure=repeated_provider_failure,
            provider_runtime_action_missing=provider_runtime_action_missing,
        )
    )
    support_by_directive = _support_by_directive(signals=signals)
    dominant_dimension = _dominant_dimension(signals=signals)
    final_directive, selected_reason, rejected_alternatives = select_minimal_safe_intervention(
        static_envelope=static_envelope,
        signals=signals,
        support_by_directive=support_by_directive,
        dominant_dimension=dominant_dimension,
        external_impact=external_impact,
        validation_allows_completion=validation_allows_completion,
    )
    return DecisionCombinerResult(
        final_directive=final_directive,
        static_envelope=static_envelope,
        signals=signals,
        dominant_dimension=dominant_dimension,
        support_by_directive=support_by_directive,
        selected_reason=selected_reason,
        rejected_alternatives=rejected_alternatives,
        safety_invariants_checked=invariants,
    )


# Preserve static read-only restrictions as an explicit decision signal.
def extract_static_signals(
    *,
    static_policy_assessment: "StaticPolicyAssessment",
) -> list[CombinerSignal]:
    from .governance import GovernanceDirective

    if static_policy_assessment.policy_directive != GovernanceDirective.CONTINUE_READ_ONLY:
        return []
    return [
        CombinerSignal(
            source=CombinerSignalSource.STATIC_POLICY.value,
            dimension=CombinerSignalDimension.CAPABILITY_RESTRICTION.value,
            recommended_directive=GovernanceDirective.CONTINUE_READ_ONLY,
            severity=1.0,
            confidence=1.0,
            recoverable_by=(
                GovernanceDirective.CONTINUE_READ_ONLY,
                GovernanceDirective.REQUEST_MORE_EVIDENCE,
                GovernanceDirective.REPLAN,
                GovernanceDirective.WAIT_HUMAN_REVIEW,
            ),
            rationale="Static policy allowed execution only in read-only mode.",
        )
    ]


# Translate deterministic dynamic-policy scores into structured risk signals.
def extract_dynamic_signals(
    *,
    dynamic_policy_assessment: DynamicPolicyAssessment | None,
    external_impact: bool,
) -> list[CombinerSignal]:
    from .governance import GovernanceDirective

    if dynamic_policy_assessment is None:
        return []

    signals: list[CombinerSignal] = []
    evidence = dynamic_policy_assessment.evidence_quality
    if (
        dynamic_policy_assessment.recommended_directive
        == DynamicPolicyRecommendedDirective.REQUEST_MORE_EVIDENCE
        or OversightReasonCode.EVIDENCE_SUPPORT_BELOW_STEP_NEED in evidence.reason_codes
        or OversightReasonCode.OBSERVED_EVIDENCE_MISSING in evidence.reason_codes
    ):
        severity = max(0.35, 1.0 - evidence.score)
        signals.append(
            CombinerSignal(
                source=CombinerSignalSource.DYNAMIC_POLICY.value,
                dimension=CombinerSignalDimension.EVIDENCE_GAP.value,
                recommended_directive=GovernanceDirective.REQUEST_MORE_EVIDENCE,
                severity=severity,
                confidence=0.90,
                recoverable_by=(
                    GovernanceDirective.REQUEST_MORE_EVIDENCE,
                    GovernanceDirective.REPLAN,
                ),
                rationale=(
                    "Dynamic policy detected that evidence support is below the current step need."
                ),
            )
        )

    risk = dynamic_policy_assessment.operational_risk
    if risk.level in {OversightSignalLevel.MEDIUM, OversightSignalLevel.HIGH}:
        signals.append(
            CombinerSignal(
                source=CombinerSignalSource.DYNAMIC_POLICY.value,
                dimension=CombinerSignalDimension.OPERATIONAL_RISK.value,
                recommended_directive=(
                    GovernanceDirective.WAIT_HUMAN_REVIEW
                    if external_impact or risk.level == OversightSignalLevel.HIGH
                    else GovernanceDirective.REQUEST_MORE_EVIDENCE
                ),
                severity=max(0.40, risk.score),
                confidence=0.85,
                recoverable_by=(
                    GovernanceDirective.REQUEST_MORE_EVIDENCE,
                    GovernanceDirective.REPLAN,
                    GovernanceDirective.WAIT_HUMAN_REVIEW,
                ),
                rationale=(
                    "Dynamic policy detected operational risk that may require a safer control point."
                ),
            )
        )

    trajectory = dynamic_policy_assessment.trajectory_health
    if (
        trajectory.level != OversightSignalLevel.HEALTHY
        or OversightReasonCode.NEEDS_REPLAN_SIGNAL in trajectory.reason_codes
        or OversightReasonCode.REPLAN_LIMIT_EXCEEDED in trajectory.reason_codes
    ):
        signals.append(
            CombinerSignal(
                source=CombinerSignalSource.DYNAMIC_POLICY.value,
                dimension=CombinerSignalDimension.TRAJECTORY_DEGRADATION.value,
                recommended_directive=(
                    GovernanceDirective.WAIT_HUMAN_REVIEW
                    if OversightReasonCode.REPLAN_LIMIT_EXCEEDED in trajectory.reason_codes
                    else GovernanceDirective.REPLAN
                ),
                severity=max(0.45, 1.0 - trajectory.score),
                confidence=0.90,
                recoverable_by=(
                    GovernanceDirective.REPLAN,
                    GovernanceDirective.WAIT_HUMAN_REVIEW,
                ),
                rationale=(
                    "Dynamic policy detected a degraded trajectory or an explicit replanning signal."
                ),
            )
        )

    if external_impact:
        signals.append(
            CombinerSignal(
                source=CombinerSignalSource.DYNAMIC_POLICY.value,
                dimension=CombinerSignalDimension.EXTERNAL_IMPACT.value,
                recommended_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                severity=max(0.50, risk.score),
                confidence=0.80,
                recoverable_by=(GovernanceDirective.WAIT_HUMAN_REVIEW,),
                rationale=(
                    "External impact raises the minimum safe intervention threshold for risky states."
                ),
            )
        )
    return signals


# Translate historical anomaly signals without letting them dominate alone.
def extract_behavioral_signals(
    *,
    behavioral_assessment: BehavioralAssessment | None,
) -> list[CombinerSignal]:
    from .governance import GovernanceDirective

    if behavioral_assessment is None:
        return []

    directive = behavioral_assessment.behavioral_recommended_directive
    if directive == BehavioralRecommendedDirective.CONTINUE:
        return []

    severity = max(
        0.35,
        behavioral_assessment.behavior_deviation_score or 0.0,
        behavioral_assessment.novelty_score or 0.0,
        0.75 if directive == BehavioralRecommendedDirective.WAIT_HUMAN_REVIEW else 0.0,
    )
    mapped_directive = _map_behavioral_directive(directive)
    return [
        CombinerSignal(
            source=CombinerSignalSource.BEHAVIORAL.value,
            dimension=CombinerSignalDimension.HISTORICAL_ANOMALY.value,
            recommended_directive=mapped_directive,
            severity=severity,
            confidence=0.55,
            recoverable_by=(
                GovernanceDirective.REQUEST_MORE_EVIDENCE,
                GovernanceDirective.REPLAN,
                GovernanceDirective.WAIT_HUMAN_REVIEW,
            ),
            rationale=behavioral_assessment.summary,
        )
    ]


# Translate judge outputs into semantic mismatch signals with stronger confidence.
def extract_judge_signals(
    *,
    judge_assessment: JudgeAssessment | None,
    external_impact: bool,
) -> list[CombinerSignal]:
    from .governance import GovernanceDirective

    if judge_assessment is None or judge_assessment.recommendation == JudgeRecommendedDirective.CONTINUE:
        return []

    mapped_directive = _map_judge_directive(judge_assessment.recommendation)
    dimension = CombinerSignalDimension.SEMANTIC_MISMATCH.value
    if external_impact or judge_assessment.evaluated_target == "external_draft":
        dimension = CombinerSignalDimension.EXTERNAL_IMPACT.value
        if mapped_directive == GovernanceDirective.REQUEST_MORE_EVIDENCE:
            mapped_directive = GovernanceDirective.WAIT_HUMAN_REVIEW
    return [
        CombinerSignal(
            source=CombinerSignalSource.JUDGE.value,
            dimension=dimension,
            recommended_directive=mapped_directive,
            severity=max(0.45, judge_assessment.confidence),
            confidence=max(0.55, judge_assessment.confidence),
            recoverable_by=(
                GovernanceDirective.REQUEST_MORE_EVIDENCE,
                GovernanceDirective.REPLAN,
                GovernanceDirective.WAIT_HUMAN_REVIEW,
            ),
            rationale=judge_assessment.summary,
        )
    ]


# Promote repeated recovery loops and provider failures into explicit runtime signals.
def extract_runtime_signals(
    *,
    repeated_recovery: bool,
    repeated_provider_failure: bool,
    provider_runtime_action_missing: bool,
) -> list[CombinerSignal]:
    from .governance import GovernanceDirective

    signals: list[CombinerSignal] = []
    if repeated_recovery:
        signals.append(
            CombinerSignal(
                source=CombinerSignalSource.RUNTIME.value,
                dimension=CombinerSignalDimension.TRAJECTORY_DEGRADATION.value,
                recommended_directive=GovernanceDirective.REPLAN,
                severity=0.90,
                confidence=0.95,
                recoverable_by=(GovernanceDirective.REPLAN, GovernanceDirective.WAIT_HUMAN_REVIEW),
                rationale="The same step already required recovery multiple times.",
            )
        )
    if provider_runtime_action_missing:
        signals.append(
            CombinerSignal(
                source=CombinerSignalSource.RUNTIME.value,
                dimension=CombinerSignalDimension.CAPABILITY_RESTRICTION.value,
                recommended_directive=(
                    GovernanceDirective.WAIT_HUMAN_REVIEW
                    if repeated_provider_failure
                    else GovernanceDirective.REPLAN
                ),
                severity=0.88 if repeated_provider_failure else 0.72,
                confidence=0.95,
                recoverable_by=(GovernanceDirective.REPLAN, GovernanceDirective.WAIT_HUMAN_REVIEW),
                rationale=(
                    "The actor provider returned no usable runtime action, so the runtime must recover conservatively."
                ),
            )
        )
    return signals


# Select the least invasive safe directive rather than averaging advisory weights.
def select_minimal_safe_intervention(
    *,
    static_envelope: "GovernanceDirective",
    signals: tuple[CombinerSignal, ...],
    support_by_directive: dict["GovernanceDirective", float],
    dominant_dimension: str | None,
    external_impact: bool,
    validation_allows_completion: bool,
) -> tuple["GovernanceDirective", str, dict["GovernanceDirective", str]]:
    from .governance import GovernanceDirective

    if not signals:
        return (
            static_envelope,
            "No material adaptive-risk signals were present, so the combiner preserved the static policy envelope.",
            {},
        )

    evidence_support = _support_for_dimension(
        signals=signals, dimension=CombinerSignalDimension.EVIDENCE_GAP.value
    )
    trajectory_support = _support_for_dimension(
        signals=signals, dimension=CombinerSignalDimension.TRAJECTORY_DEGRADATION.value
    )
    semantic_support = _support_for_dimension(
        signals=signals, dimension=CombinerSignalDimension.SEMANTIC_MISMATCH.value
    )
    operational_support = _support_for_dimension(
        signals=signals, dimension=CombinerSignalDimension.OPERATIONAL_RISK.value
    )
    external_support = _support_for_dimension(
        signals=signals, dimension=CombinerSignalDimension.EXTERNAL_IMPACT.value
    )
    corroborated_review = (
        support_by_directive.get(GovernanceDirective.WAIT_HUMAN_REVIEW, 0.0) >= 0.95
        and len([signal for signal in signals if signal.recommended_directive == GovernanceDirective.WAIT_HUMAN_REVIEW]) >= 2
    )
    strong_advisory_review = any(
        signal.source
        in {
            CombinerSignalSource.DYNAMIC_POLICY.value,
            CombinerSignalSource.JUDGE.value,
            CombinerSignalSource.RUNTIME.value,
        }
        and signal.recommended_directive == GovernanceDirective.WAIT_HUMAN_REVIEW
        and signal.confidence >= 0.80
        and signal.severity >= 0.55
        for signal in signals
    )
    strong_judge_review = any(
        signal.source == CombinerSignalSource.JUDGE.value
        and signal.recommended_directive == GovernanceDirective.WAIT_HUMAN_REVIEW
        and signal.confidence >= 0.80
        for signal in signals
    )
    severe_operational_review = (
        external_impact
        and (
            operational_support >= 0.60
            or external_support >= 0.60
            or strong_judge_review
        )
    )
    provider_recovery_replan = any(
        signal.source == CombinerSignalSource.RUNTIME.value
        and signal.dimension == CombinerSignalDimension.CAPABILITY_RESTRICTION.value
        and signal.recommended_directive == GovernanceDirective.REPLAN
        and signal.confidence >= 0.90
        for signal in signals
    )
    repeated_or_degraded_trajectory = trajectory_support >= 0.70
    strong_evidence_gap = evidence_support >= 0.55
    strong_semantic_mismatch = semantic_support >= 0.55 or strong_judge_review
    pure_evidence_gap = (
        strong_evidence_gap
        and trajectory_support < 0.40
        and operational_support < 0.40
        and external_support < 0.40
        and support_by_directive.get(GovernanceDirective.WAIT_HUMAN_REVIEW, 0.0) == 0.0
        and not strong_semantic_mismatch
    )
    behavioral_only_review = (
        support_by_directive.get(GovernanceDirective.WAIT_HUMAN_REVIEW, 0.0) > 0.0
        and all(signal.source == CombinerSignalSource.BEHAVIORAL.value for signal in signals)
    )

    rejected_alternatives: dict[GovernanceDirective, str] = {}
    if provider_recovery_replan:
        rejected_alternatives[GovernanceDirective.WAIT_HUMAN_REVIEW] = (
            "Human review was rejected because the provider failure is still recoverable through automatic replanning."
        )
        return (
            GovernanceDirective.REPLAN,
            _build_selected_reason(
                static_envelope=static_envelope,
                dominant_dimension=dominant_dimension,
                detail=(
                    "The runtime detected a recoverable provider protocol failure, so replanning is safer than opening human review."
                ),
            ),
            rejected_alternatives,
        )

    if severe_operational_review or strong_advisory_review or (
        corroborated_review and not behavioral_only_review
    ):
        rejected_alternatives[GovernanceDirective.REPLAN] = (
            "Replanning was rejected because the risk includes external impact or corroborated review signals."
        )
        rejected_alternatives[GovernanceDirective.REQUEST_MORE_EVIDENCE] = (
            "Requesting more evidence was rejected because automatic recovery is not sufficient for the current risk."
        )
        return (
            GovernanceDirective.WAIT_HUMAN_REVIEW,
            _build_selected_reason(
                static_envelope=static_envelope,
                dominant_dimension=dominant_dimension,
                detail=(
                    "The issue is not safely recoverable through another autonomous turn because risk affects an external or semantically fragile outcome."
                ),
            ),
            rejected_alternatives,
        )

    if repeated_or_degraded_trajectory:
        rejected_alternatives[GovernanceDirective.WAIT_HUMAN_REVIEW] = (
            "Human review was rejected because the dominant failure mode is still recoverable through replanning."
        )
        rejected_alternatives[GovernanceDirective.REQUEST_MORE_EVIDENCE] = (
            "Requesting more evidence was rejected because the current trajectory itself is degraded."
        )
        return (
            GovernanceDirective.REPLAN,
            _build_selected_reason(
                static_envelope=static_envelope,
                dominant_dimension=dominant_dimension,
                detail=(
                    "The dominant issue is trajectory degradation, so replanning is the minimum safe automatic intervention."
                ),
            ),
            rejected_alternatives,
        )

    if strong_semantic_mismatch:
        rejected_alternatives[GovernanceDirective.WAIT_HUMAN_REVIEW] = (
            "Human review was rejected because the mismatch appears recoverable without external impact."
        )
        rejected_alternatives[GovernanceDirective.REPLAN] = (
            "Replanning was rejected because the safer minimal intervention is to improve evidence or reduce claim precision first."
        )
        return (
            GovernanceDirective.REQUEST_MORE_EVIDENCE,
            _build_selected_reason(
                static_envelope=static_envelope,
                dominant_dimension=dominant_dimension,
                detail=(
                    "A semantic mismatch was detected, but it is still recoverable automatically by gathering stronger support or tightening the claim."
                ),
            ),
            rejected_alternatives,
        )

    if validation_allows_completion and pure_evidence_gap:
        rejected_alternatives[GovernanceDirective.REQUEST_MORE_EVIDENCE] = (
            "Requesting more evidence was rejected because typed step validation already marked the step complete with sufficient evidence."
        )
        return (
            static_envelope,
            _build_selected_reason(
                static_envelope=static_envelope,
                dominant_dimension=dominant_dimension,
                detail=(
                    "Typed step validation indicates the step can complete safely, so the combiner preserved the static envelope instead of reopening recovery."
                ),
            ),
            rejected_alternatives,
        )

    if strong_evidence_gap:
        rejected_alternatives[GovernanceDirective.WAIT_HUMAN_REVIEW] = (
            "Human review was rejected because the problem is missing support, not a hard approval requirement."
        )
        rejected_alternatives[GovernanceDirective.REPLAN] = (
            "Replanning was rejected because the current plan can still succeed if the runtime gathers stronger evidence."
        )
        return (
            GovernanceDirective.REQUEST_MORE_EVIDENCE,
            _build_selected_reason(
                static_envelope=static_envelope,
                dominant_dimension=dominant_dimension,
                detail=(
                    "The dominant issue is an evidence gap, and requesting more evidence is the minimum safe intervention."
                ),
            ),
            rejected_alternatives,
        )

    if behavioral_only_review:
        rejected_alternatives[GovernanceDirective.WAIT_HUMAN_REVIEW] = (
            "Behavioral anomaly alone does not justify human review without corroborating risk signals."
        )

    return (
        static_envelope,
        _build_selected_reason(
            static_envelope=static_envelope,
            dominant_dimension=dominant_dimension,
            detail=(
                "Adaptive signals were not strong enough to justify a more conservative directive than the static envelope."
            ),
        ),
        rejected_alternatives,
    )


# Aggregate signal strength by recommended directive.
def _support_by_directive(
    *,
    signals: tuple[CombinerSignal, ...],
) -> dict["GovernanceDirective", float]:
    support: dict["GovernanceDirective", float] = {}
    for signal in signals:
        support[signal.recommended_directive] = support.get(signal.recommended_directive, 0.0) + (
            signal.severity * signal.confidence
        )
    return support


# Resolve the strongest risk dimension after signal extraction.
def _dominant_dimension(*, signals: tuple[CombinerSignal, ...]) -> str | None:
    if not signals:
        return None
    dimension_support: dict[str, float] = {}
    for signal in signals:
        dimension_support[signal.dimension] = dimension_support.get(signal.dimension, 0.0) + (
            signal.severity * signal.confidence
        )
    return max(dimension_support, key=dimension_support.get)


# Measure support for one specific risk dimension.
def _support_for_dimension(*, signals: tuple[CombinerSignal, ...], dimension: str) -> float:
    return sum(
        signal.severity * signal.confidence for signal in signals if signal.dimension == dimension
    )


# Build one stable textual reason for the selected directive.
def _build_selected_reason(
    *,
    static_envelope: "GovernanceDirective",
    dominant_dimension: str | None,
    detail: str,
) -> str:
    envelope_text = (
        "Static policy preserved read-only execution."
        if static_envelope.value == "continue_read_only"
        else "Static policy allowed the action."
    )
    if dominant_dimension is None:
        return f"{envelope_text} {detail}"
    return f"{envelope_text} Dominant risk dimension: {dominant_dimension}. {detail}"


# Map behavioural recommendations into the shared governance directive enum.
def _map_behavioral_directive(
    recommended_directive: BehavioralRecommendedDirective,
):
    from .governance import GovernanceDirective

    if recommended_directive == BehavioralRecommendedDirective.WAIT_HUMAN_REVIEW:
        return GovernanceDirective.WAIT_HUMAN_REVIEW
    if recommended_directive == BehavioralRecommendedDirective.REPLAN:
        return GovernanceDirective.REPLAN
    if recommended_directive == BehavioralRecommendedDirective.REQUEST_MORE_EVIDENCE:
        return GovernanceDirective.REQUEST_MORE_EVIDENCE
    return GovernanceDirective.CONTINUE


# Map judge recommendations into the shared governance directive enum.
def _map_judge_directive(
    recommended_directive: JudgeRecommendedDirective,
):
    from .governance import GovernanceDirective

    if recommended_directive == JudgeRecommendedDirective.WAIT_HUMAN_REVIEW:
        return GovernanceDirective.WAIT_HUMAN_REVIEW
    if recommended_directive == JudgeRecommendedDirective.REPLAN:
        return GovernanceDirective.REPLAN
    if recommended_directive == JudgeRecommendedDirective.REQUEST_MORE_EVIDENCE:
        return GovernanceDirective.REQUEST_MORE_EVIDENCE
    return GovernanceDirective.CONTINUE


__all__ = [
    "CombinerSignal",
    "CombinerSignalDimension",
    "CombinerSignalSource",
    "DecisionCombinerResult",
    "apply_static_policy_envelope",
    "combine_decision_signals",
    "extract_behavioral_signals",
    "extract_dynamic_signals",
    "extract_judge_signals",
    "extract_runtime_signals",
    "extract_static_signals",
    "select_minimal_safe_intervention",
]
