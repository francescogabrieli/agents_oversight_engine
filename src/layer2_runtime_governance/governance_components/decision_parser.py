from typing import Any

from ..behavioral import BehavioralAssessment
from ..judge import JudgeAssessment
from ..oversight.contracts import (
    DynamicPolicyAssessment,
    DynamicPolicyRecommendedDirective,
    EvidenceQualityAssessment,
    OperationalRiskAssessment,
    OversightReasonCode,
    OversightSignalLevel,
    TrajectoryHealthAssessment,
)

# PolicyDecision is the deterministic outcome returned by the policy engine.
from .contracts import (
    GovernanceDecisionTrace,
    GovernanceDirective,
    GovernanceReasonCode,
    PolicyDecision,
    PolicyEngineExecutionError,
    PolicyEnginePort,
    PolicyEvaluationInput,
    StaticPolicyAssessment,
)
from .directive_mapping import (
    map_behavioral_directive,
    map_judge_directive,
)

# Parse and validate one bounded part of the untrusted OPA response.
def _build_governance_trace_from_opa_output(
    *,
    raw_output: dict[str, Any],
    policy_input: PolicyEvaluationInput,
    policy_engine_service: PolicyEnginePort,
    dynamic_policy_assessment: DynamicPolicyAssessment,
    behavioral_assessment: BehavioralAssessment,
    judge_assessment: JudgeAssessment,
    decision_input: dict[str, Any] | None = None,
) -> GovernanceDecisionTrace:
    _validate_governance_decision_output(raw_output)
    directive = _parse_governance_directive(raw_output.get("directive"))
    policy_decision = _parse_policy_decision(raw_output.get("policy_decision"))
    reason_codes = _parse_reason_codes(raw_output.get("reason_codes"))
    selected_reason = _parse_non_empty_text(
        raw_output.get("selected_reason"),
        field_name="selected_reason",
    )
    pillar_results = raw_output["pillar_results"]
    policy_directive = _static_policy_directive_from_pillar(
        pillar_results.get("static_policy", {}), fallback_directive=directive
    )
    return GovernanceDecisionTrace(
        directive=directive,
        policy_decision=policy_decision,
        reason_codes=reason_codes,
        summary=selected_reason,
        constrains_capability_use=directive != GovernanceDirective.CONTINUE,
        evaluated_actor_result_type=policy_input.actor_result_type,
        evaluated_tool_name=policy_input.tool_name,
        policy_engine_name=policy_engine_service.policy_engine_name,
        policy_bundle_version=str(
            raw_output.get("policy_bundle_version")
            or policy_engine_service.policy_bundle_version
        ),
        policy_directive=policy_directive,
        oversight_recommended_directive=_map_dynamic_recommendation_for_trace(
            dynamic_policy_assessment.recommended_directive
        ),
        requires_human_review=raw_output["requires_human_review"],
        evaluated_plan_step_id=policy_input.current_plan_step_id,
        behavioral_recommended_directive=map_behavioral_directive(
            behavioral_assessment.behavioral_recommended_directive
        ),
        judge_recommended_directive=map_judge_directive(judge_assessment.recommendation),
        judge_invoked=judge_assessment.invoked,
        static_policy_summary=_static_policy_summary_from_pillar(
            pillar_results.get("static_policy", {})
        ),
        dynamic_policy_summary=dynamic_policy_assessment.summary,
        behavioral_summary=behavioral_assessment.summary,
        judge_summary=judge_assessment.summary,
        behavioral_similarity_score=behavioral_assessment.historical_similarity_score,
        behavioral_deviation_score=behavioral_assessment.behavior_deviation_score,
        behavioral_novelty_score=behavioral_assessment.novelty_score,
        oversight_summary=dynamic_policy_assessment.summary,
        combiner_dominant_dimension=None,
        combiner_support_by_directive=None,
        combiner_selected_reason=selected_reason,
        combiner_rejected_alternatives={
            str(key): str(value)
            for key, value in raw_output["rejected_alternatives"].items()
        },
        combiner_safety_invariants_checked=tuple(
            str(item) for item in raw_output["safety_invariants_checked"]
        ),
        combiner_signals=tuple(_pillar_result_signals(pillar_results=pillar_results)),
        combiner_normalized_directive=None,
        combiner_normalization_reason=None,
        governance_input_schema_version=(
            str(decision_input.get("schema_version"))
            if isinstance(decision_input, dict)
            else None
        ),
        pillar_requirements=(
            {
                str(key): bool(value)
                for key, value in decision_input.get(
                    "pillar_requirements", {}
                ).items()
            }
            if isinstance(decision_input, dict)
            else None
        ),
        pillar_results=pillar_results,
        pillar_errors=(
            {
                str(key): str(value)
                for key, value in decision_input.get("pillar_errors", {}).items()
            }
            if isinstance(decision_input, dict)
            else None
        ),
        policy_config_version=str(raw_output.get("policy_config_version")),
    )

# Parse and validate one bounded part of the untrusted OPA response.
def _validate_governance_decision_output(raw_output: dict[str, Any]) -> None:
    from .schema_validation import validate_json_schema

    validate_json_schema(schema_name="governance_decision_output", payload=raw_output)
    required_fields = (
        "schema_version",
        "directive",
        "policy_decision",
        "reason_codes",
        "pillar_results",
        "selected_reason",
        "rejected_alternatives",
        "safety_invariants_checked",
        "requires_human_review",
        "policy_bundle_version",
        "policy_config_version",
    )
    for field_name in required_fields:
        if field_name not in raw_output:
            raise PolicyEngineExecutionError(f"OPA decision output missing '{field_name}'.")
    if not isinstance(raw_output["reason_codes"], list) or not raw_output["reason_codes"]:
        raise PolicyEngineExecutionError("OPA decision output reason_codes must be a non-empty list.")
    if not isinstance(raw_output["pillar_results"], dict):
        raise PolicyEngineExecutionError("OPA decision output pillar_results must be an object.")
    if not isinstance(raw_output["rejected_alternatives"], dict):
        raise PolicyEngineExecutionError("OPA decision output rejected_alternatives must be an object.")
    if not isinstance(raw_output["safety_invariants_checked"], list):
        raise PolicyEngineExecutionError("OPA decision output safety_invariants_checked must be a list.")
    if not isinstance(raw_output["requires_human_review"], bool):
        raise PolicyEngineExecutionError("OPA decision output requires_human_review must be a boolean.")

# Parse and validate one bounded part of the untrusted OPA response.
def _build_static_assessment_from_decision(
    *,
    raw_output: dict[str, Any],
    policy_input: PolicyEvaluationInput,
    policy_engine_service: PolicyEnginePort,
) -> StaticPolicyAssessment:
    pillar = raw_output.get("pillar_results", {}).get("static_policy", {})
    if not isinstance(pillar, dict):
        raise PolicyEngineExecutionError("OPA static_policy pillar result must be an object.")
    hard_deny = pillar.get("hard_deny") is True
    clarification = pillar.get("clarification_signaled") is True
    read_only_required = pillar.get("read_only_required") is True
    policy_decision = (
        PolicyDecision.DENY
        if hard_deny
        else PolicyDecision.ASK_CLARIFICATION
        if clarification
        else PolicyDecision.ALLOW_READ_ONLY
        if read_only_required
        else PolicyDecision.ALLOW
    )
    reason_codes = _parse_optional_reason_codes(
        pillar.get("reason_codes"),
        fallback=(
            GovernanceReasonCode.STATIC_POLICY_HARD_DENY
            if hard_deny
            else GovernanceReasonCode.ASK_CLARIFICATION_REQUIRED
            if clarification
            else GovernanceReasonCode.READ_ONLY_ALLOWED
            if read_only_required
            else GovernanceReasonCode.ALLOWED_BY_POLICY
        ),
    )
    return StaticPolicyAssessment(
        policy_decision=policy_decision,
        reason_codes=reason_codes,
        summary=_static_policy_summary_from_pillar(pillar) or str(raw_output.get("selected_reason")),
        policy_engine_name=policy_engine_service.policy_engine_name,
        policy_bundle_version=str(
            raw_output.get("policy_bundle_version")
            or policy_engine_service.policy_bundle_version
        ),
        policy_directive=_static_policy_directive_from_pillar(
            pillar,
            fallback_directive=GovernanceDirective.CONTINUE,
        ),
        hard_deny=hard_deny,
        constraints=tuple(str(item) for item in pillar.get("constraints", [])),
        violations=tuple(str(item) for item in pillar.get("violations", [])),
    )

# Parse and validate one bounded part of the untrusted OPA response.
def _build_dynamic_assessment_from_signals(
    *,
    pillars: dict[str, Any],
) -> DynamicPolicyAssessment:
    evidence = pillars.get("evidence", {})
    risk = pillars.get("risk", {})
    trajectory = pillars.get("trajectory", {})
    evidence_assessment = EvidenceQualityAssessment(
        score=_optional_float(evidence.get("score", evidence.get("diagnostic_score"))),
        level=_parse_oversight_level(
            evidence.get("level", evidence.get("diagnostic_level")),
            OversightSignalLevel.UNAVAILABLE,
        ),
        reason_codes=_parse_oversight_reason_codes(evidence.get("reason_facts", [])),
    )
    risk_assessment = OperationalRiskAssessment(
        score=_optional_float(risk.get("score", risk.get("diagnostic_score"))),
        level=_parse_oversight_level(
            risk.get("level", risk.get("diagnostic_level")),
            OversightSignalLevel.NOT_APPLICABLE,
        ),
        reason_codes=_parse_oversight_reason_codes(risk.get("reason_facts", [])),
        mode=str(risk.get("mode", risk.get("raw_facts", {}).get("mode", "shadow"))),
        decision_influence=bool(
            risk.get(
                "decision_influence",
                risk.get("raw_facts", {}).get("decision_influence", False),
            )
        ),
        applicability=str(
            risk.get("raw_facts", {}).get("applicability", "not_applicable")
        ),
        recoverability=str(risk.get("raw_facts", {}).get("recoverability", "unknown")),
        blast_radius=str(risk.get("raw_facts", {}).get("blast_radius", "unknown")),
        sensitivity=str(risk.get("raw_facts", {}).get("sensitivity", "unknown")),
        exposure=str(risk.get("raw_facts", {}).get("exposure", "unknown")),
        target_criticality=str(
            risk.get("raw_facts", {}).get("target_criticality", "unknown")
        ),
        impact_level=str(risk.get("raw_facts", {}).get("impact_level", "unknown")),
        dominant_factors=tuple(
            str(item) for item in risk.get("raw_facts", {}).get("dominant_factors", [])
        ),
        unknown_facts=tuple(
            str(item) for item in risk.get("raw_facts", {}).get("unknown_facts", [])
        ),
        fact_provenance=tuple(
            str(item) for item in risk.get("raw_facts", {}).get("fact_provenance", [])
        ),
        profile_version=(
            str(risk.get("raw_facts", {}).get("profile_version"))
            if risk.get("raw_facts", {}).get("profile_version") is not None
            else None
        ),
        error=(
            str(risk.get("summary"))
            if risk.get("raw_facts", {}).get("pillar_unavailable") is True
            else None
        ),
    )
    trajectory_assessment = TrajectoryHealthAssessment(
        score=_optional_float(trajectory.get("score", trajectory.get("diagnostic_score"))),
        level=_parse_oversight_level(
            trajectory.get("level", trajectory.get("diagnostic_level")),
            OversightSignalLevel.UNAVAILABLE,
        ),
        reason_codes=_parse_oversight_reason_codes(trajectory.get("reason_facts", [])),
    )
    recommended = _dynamic_recommendation_from_active_pillars(
        evidence=evidence,
        trajectory=trajectory,
    )
    return DynamicPolicyAssessment(
        evidence_quality=evidence_assessment,
        operational_risk=risk_assessment,
        trajectory_health=trajectory_assessment,
        recommended_directive=recommended,
        summary=(
            f"OPA-first diagnostics: evidence {evidence_assessment.level.value} "
            f"({_format_optional_score(evidence_assessment.score)}), "
            f"risk-shadow {risk_assessment.level.value} "
            f"({_format_optional_score(risk_assessment.score)}), "
            f"trajectory {trajectory_assessment.level.value} "
            f"({_format_optional_score(trajectory_assessment.score)})."
        ),
        reason_codes=_merge_oversight_reason_codes(
            evidence_assessment.reason_codes,
            risk_assessment.reason_codes,
            trajectory_assessment.reason_codes,
        ),
    )

# Parse and validate one bounded part of the untrusted OPA response.
def _parse_governance_directive(value: Any) -> GovernanceDirective:
    if value == "deny":
        return GovernanceDirective.STOP
    if not isinstance(value, str):
        raise PolicyEngineExecutionError("OPA directive must be a string.")
    try:
        return GovernanceDirective(value)
    except ValueError as exc:
        raise PolicyEngineExecutionError(f"OPA returned an unknown directive: {value}") from exc

# Parse and validate one bounded part of the untrusted OPA response.
def _static_policy_directive_from_pillar(
    pillar: Any,
    *,
    fallback_directive: GovernanceDirective,
) -> GovernanceDirective:
    if not isinstance(pillar, dict):
        return fallback_directive
    if pillar.get("hard_deny") is True or pillar.get("write_blocked") is True:
        return GovernanceDirective.STOP
    if pillar.get("hitl_mandatory") is True:
        return GovernanceDirective.WAIT_HUMAN_REVIEW
    if pillar.get("clarification_signaled") is True:
        return GovernanceDirective.ASK_USER_CLARIFICATION
    if pillar.get("read_only_required") is True:
        return GovernanceDirective.CONTINUE_READ_ONLY
    return GovernanceDirective.CONTINUE

# Parse and validate one bounded part of the untrusted OPA response.
def _static_policy_summary_from_pillar(pillar: Any) -> str:
    if not isinstance(pillar, dict):
        return "Static policy pillar result was unavailable."
    if pillar.get("summary"):
        return str(pillar["summary"])
    if pillar.get("hard_deny") is True or pillar.get("write_blocked") is True:
        return str(pillar.get("deny_reason") or "Static policy hard-denied the operation.")
    if pillar.get("clarification_signaled") is True:
        return "Static policy preserved the actor clarification request."
    if pillar.get("read_only_required") is True:
        return "Static policy preserved the read-only envelope."
    return "Static policy allowed the actor outcome."

# Parse and validate one bounded part of the untrusted OPA response.
def _pillar_result_signals(*, pillar_results: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for name, payload in pillar_results.items():
        if not isinstance(payload, dict):
            continue
        signals.append(
            {
                "source": name,
                "dimension": name,
                "diagnostic_score": payload.get("score", payload.get("diagnostic_score")),
                "level": payload.get("level", payload.get("diagnostic_level")),
                "summary": payload.get("summary"),
            }
        )
    return signals

# Parse and validate one bounded part of the untrusted OPA response.
def _map_opa_directive_for_trace(directive: GovernanceDirective) -> GovernanceDirective:
    if directive in {
        GovernanceDirective.REQUEST_MORE_EVIDENCE,
        GovernanceDirective.REPLAN,
        GovernanceDirective.WAIT_HUMAN_REVIEW,
        GovernanceDirective.CONTINUE_READ_ONLY,
    }:
        return directive
    return GovernanceDirective.CONTINUE


# Map only the active dynamic assessment, never the final static/judge-owned directive.
def _map_dynamic_recommendation_for_trace(
    directive: DynamicPolicyRecommendedDirective,
) -> GovernanceDirective:
    return {
        DynamicPolicyRecommendedDirective.CONTINUE: GovernanceDirective.CONTINUE,
        DynamicPolicyRecommendedDirective.CONTINUE_READ_ONLY: GovernanceDirective.CONTINUE_READ_ONLY,
        DynamicPolicyRecommendedDirective.REQUEST_MORE_EVIDENCE: GovernanceDirective.REQUEST_MORE_EVIDENCE,
        DynamicPolicyRecommendedDirective.REPLAN: GovernanceDirective.REPLAN,
        DynamicPolicyRecommendedDirective.WAIT_HUMAN_REVIEW: GovernanceDirective.WAIT_HUMAN_REVIEW,
    }[directive]

# Parse and validate one bounded part of the untrusted OPA response.
def _map_governance_to_dynamic_directive(
    directive: GovernanceDirective,
) -> DynamicPolicyRecommendedDirective:
    if directive == GovernanceDirective.WAIT_HUMAN_REVIEW:
        return DynamicPolicyRecommendedDirective.WAIT_HUMAN_REVIEW
    if directive == GovernanceDirective.REPLAN:
        return DynamicPolicyRecommendedDirective.REPLAN
    if directive == GovernanceDirective.REQUEST_MORE_EVIDENCE:
        return DynamicPolicyRecommendedDirective.REQUEST_MORE_EVIDENCE
    if directive == GovernanceDirective.CONTINUE_READ_ONLY:
        return DynamicPolicyRecommendedDirective.CONTINUE_READ_ONLY
    return DynamicPolicyRecommendedDirective.CONTINUE


# Derive the dynamic diagnostic recommendation from active evidence/trajectory only.
def _dynamic_recommendation_from_active_pillars(
    *,
    evidence: dict[str, Any],
    trajectory: dict[str, Any],
) -> DynamicPolicyRecommendedDirective:
    if (
        evidence.get("raw_facts", {}).get("pillar_unavailable") is True
        or trajectory.get("raw_facts", {}).get("pillar_unavailable") is True
    ):
        return DynamicPolicyRecommendedDirective.WAIT_HUMAN_REVIEW
    if trajectory.get("degraded_or_replan") is True:
        return DynamicPolicyRecommendedDirective.REPLAN
    if evidence.get("evidence_gap") is True or evidence.get("precision_mismatch") is True:
        return DynamicPolicyRecommendedDirective.REQUEST_MORE_EVIDENCE
    return DynamicPolicyRecommendedDirective.CONTINUE

# Parse and validate one bounded part of the untrusted OPA response.
def _parse_oversight_level(value: Any, default: OversightSignalLevel) -> OversightSignalLevel:
    if not isinstance(value, str):
        return default
    try:
        return OversightSignalLevel(value)
    except ValueError:
        return default

# Parse and validate one bounded part of the untrusted OPA response.
def _parse_oversight_reason_codes(value: Any) -> tuple[OversightReasonCode, ...]:
    if not isinstance(value, list):
        return ()
    parsed: list[OversightReasonCode] = []
    for item in value:
        if not isinstance(item, str):
            continue
        try:
            parsed.append(OversightReasonCode(item))
        except ValueError:
            continue
    return tuple(parsed)

# Parse and validate one bounded part of the untrusted OPA response.
def _merge_oversight_reason_codes(
    *groups: tuple[OversightReasonCode, ...],
) -> tuple[OversightReasonCode, ...]:
    merged: list[OversightReasonCode] = []
    for group in groups:
        for code in group:
            if code not in merged:
                merged.append(code)
    return tuple(merged)

# Parse and validate one bounded part of the untrusted OPA response.
def _parse_policy_decision(value: Any) -> PolicyDecision:
    if not isinstance(value, str):
        raise PolicyEngineExecutionError("OPA policy decision must be a string.")
    try:
        return PolicyDecision(value)
    except ValueError as exc:
        raise PolicyEngineExecutionError(f"OPA returned an unknown policy decision: {value}") from exc

# Parse and validate one bounded part of the untrusted OPA response.
def _parse_reason_codes(value: Any) -> tuple[GovernanceReasonCode, ...]:
    if not isinstance(value, list) or not value:
        raise PolicyEngineExecutionError("OPA reason_codes must be a non-empty list.")
    parsed_codes: list[GovernanceReasonCode] = []
    for item in value:
        if not isinstance(item, str):
            raise PolicyEngineExecutionError("OPA reason_codes entries must be strings.")
        try:
            parsed_codes.append(GovernanceReasonCode(item))
        except ValueError as exc:
            raise PolicyEngineExecutionError(f"OPA returned an unknown reason code: {item}") from exc
    return tuple(parsed_codes)


# Parse static-only reason codes without borrowing final OPA reasons.
def _parse_optional_reason_codes(
    value: Any,
    *,
    fallback: GovernanceReasonCode,
) -> tuple[GovernanceReasonCode, ...]:
    if not isinstance(value, list) or not value:
        return (fallback,)
    parsed: list[GovernanceReasonCode] = []
    for item in value:
        if not isinstance(item, str):
            continue
        try:
            parsed.append(GovernanceReasonCode(item))
        except ValueError as exc:
            raise PolicyEngineExecutionError(
                f"OPA returned an unknown static reason code: {item}"
            ) from exc
    return tuple(parsed or (fallback,))


# Preserve unavailable pillar scores instead of inventing healthy/low defaults.
def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


# Format nullable diagnostics in stable summaries.
def _format_optional_score(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"

# Parse and validate one bounded part of the untrusted OPA response.
def _parse_non_empty_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyEngineExecutionError(f"OPA response field '{field_name}' must be a non-empty string.")
    return " ".join(value.strip().split())

__all__ = [
    "_build_governance_trace_from_opa_output",
    "_validate_governance_decision_output",
    "_build_static_assessment_from_decision",
    "_build_dynamic_assessment_from_signals",
    "_parse_governance_directive",
    "_static_policy_directive_from_pillar",
    "_static_policy_summary_from_pillar",
    "_pillar_result_signals",
    "_map_opa_directive_for_trace",
    "_map_governance_to_dynamic_directive",
    "_parse_oversight_level",
    "_parse_oversight_reason_codes",
    "_merge_oversight_reason_codes",
    "_parse_policy_decision",
    "_parse_reason_codes",
    "_parse_non_empty_text"
]
