from typing import Any

from ..actor_contract import ActorResultType
from ..behavioral import BehavioralAssessment
from ..judge import JudgeAssessment
from ..oversight import (
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
    PolicyEvaluationResult,
    StaticPolicyAssessment,
)
from .directive_mapping import (
    derive_governance_directive,
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
        oversight_recommended_directive=_map_opa_directive_for_trace(directive),
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
    from ..schema_validation import validate_json_schema

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
    policy_decision = _parse_policy_decision(raw_output.get("policy_decision"))
    reason_codes = _parse_reason_codes(raw_output.get("reason_codes"))
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
            fallback_directive=derive_governance_directive(
                policy_input=policy_input,
                policy_result=PolicyEvaluationResult(
                    policy_decision=policy_decision,
                    reason_codes=reason_codes,
                    summary=str(raw_output.get("selected_reason")),
                    policy_engine_name=policy_engine_service.policy_engine_name,
                    policy_bundle_version=policy_engine_service.policy_bundle_version,
                ),
            ),
        ),
    )

# Parse and validate one bounded part of the untrusted OPA response.
def _build_static_assessment_from_base(
    *,
    policy_input: PolicyEvaluationInput,
    base_facts: dict[str, Any],
    policy_engine_service: PolicyEnginePort,
) -> StaticPolicyAssessment:
    actor = base_facts["actor"]
    external = base_facts["external_communication"]
    policy = base_facts["policy"]
    if actor["capability_mode"] == "write" or actor["external_impact"]:
        decision = PolicyDecision.DENY
        reason_codes = (GovernanceReasonCode.WRITE_CAPABILITY_BLOCKED,)
        directive = GovernanceDirective.STOP
        summary = "Static policy blocks write capability or external impact."
    elif external["review_ready"]:
        decision = PolicyDecision.REQUIRE_HUMAN_REVIEW
        reason_codes = (GovernanceReasonCode.EXTERNAL_COMMUNICATION_REVIEW_REQUIRED,)
        directive = GovernanceDirective.WAIT_HUMAN_REVIEW
        summary = "Static policy requires human review for the external communication draft."
    elif actor["result_type"] == ActorResultType.ASK_CLARIFICATION.value:
        decision = PolicyDecision.ASK_CLARIFICATION
        reason_codes = (GovernanceReasonCode.ASK_CLARIFICATION_REQUIRED,)
        directive = GovernanceDirective.ASK_USER_CLARIFICATION
        summary = "Static policy preserves the actor clarification request."
    elif policy["read_only_mode"] or actor["capability_mode"] == "read_only":
        decision = PolicyDecision.ALLOW_READ_ONLY
        reason_codes = (GovernanceReasonCode.READ_ONLY_ALLOWED,)
        directive = GovernanceDirective.CONTINUE_READ_ONLY
        summary = "Static policy preserves the read-only envelope."
    else:
        decision = PolicyDecision.ALLOW
        reason_codes = (GovernanceReasonCode.ALLOWED_BY_POLICY,)
        directive = derive_governance_directive(
            policy_input=policy_input,
            policy_result=PolicyEvaluationResult(
                policy_decision=PolicyDecision.ALLOW,
                reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
                summary="Static policy allowed the actor outcome.",
                policy_engine_name=policy_engine_service.policy_engine_name,
                policy_bundle_version=policy_engine_service.policy_bundle_version,
            ),
        )
        summary = "Static policy allowed the actor outcome."
    return StaticPolicyAssessment(
        policy_decision=decision,
        reason_codes=reason_codes,
        summary=summary,
        policy_engine_name=policy_engine_service.policy_engine_name,
        policy_bundle_version=policy_engine_service.policy_bundle_version,
        policy_directive=directive,
    )

# Parse and validate one bounded part of the untrusted OPA response.
def _build_judge_static_context(*, policy_engine_service: PolicyEnginePort) -> StaticPolicyAssessment:
    return StaticPolicyAssessment(
        policy_decision=PolicyDecision.ALLOW,
        reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
        summary="Static-policy facts are supplied to OPA; Python does not interpret them before judging.",
        policy_engine_name=policy_engine_service.policy_engine_name,
        policy_bundle_version=policy_engine_service.policy_bundle_version,
        policy_directive=GovernanceDirective.CONTINUE,
    )

# Parse and validate one bounded part of the untrusted OPA response.
def _build_dynamic_assessment_from_signals(
    *,
    pillars: dict[str, Any],
    directive: GovernanceDirective,
) -> DynamicPolicyAssessment:
    evidence = pillars.get("evidence", {})
    risk = pillars.get("risk", {})
    trajectory = pillars.get("trajectory", {})
    evidence_assessment = EvidenceQualityAssessment(
        score=float(evidence.get("score", evidence.get("diagnostic_score")) or 0.0),
        level=_parse_oversight_level(evidence.get("diagnostic_level"), OversightSignalLevel.WEAK),
        reason_codes=_parse_oversight_reason_codes(evidence.get("reason_facts", [])),
    )
    risk_assessment = OperationalRiskAssessment(
        score=float(risk.get("score", risk.get("diagnostic_score")) or 0.0),
        level=_parse_oversight_level(risk.get("diagnostic_level"), OversightSignalLevel.LOW),
        reason_codes=_parse_oversight_reason_codes(risk.get("reason_facts", [])),
    )
    trajectory_assessment = TrajectoryHealthAssessment(
        score=float(trajectory.get("score", trajectory.get("diagnostic_score")) or 0.0),
        level=_parse_oversight_level(trajectory.get("diagnostic_level"), OversightSignalLevel.HEALTHY),
        reason_codes=_parse_oversight_reason_codes(trajectory.get("reason_facts", [])),
    )
    recommended = _map_governance_to_dynamic_directive(directive)
    return DynamicPolicyAssessment(
        evidence_quality=evidence_assessment,
        operational_risk=risk_assessment,
        trajectory_health=trajectory_assessment,
        recommended_directive=recommended,
        summary=(
            f"OPA-first diagnostics: evidence {evidence_assessment.level.value} "
            f"({evidence_assessment.score:.2f}), risk {risk_assessment.level.value} "
            f"({risk_assessment.score:.2f}), trajectory {trajectory_assessment.level.value} "
            f"({trajectory_assessment.score:.2f})."
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
    if pillar.get("write_blocked") is True:
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
    if pillar.get("write_blocked") is True:
        return str(pillar.get("deny_reason") or "Static policy blocked write-like execution.")
    if pillar.get("hitl_mandatory") is True:
        return "Static policy requires human review."
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

# Parse and validate one bounded part of the untrusted OPA response.
def _parse_non_empty_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyEngineExecutionError(f"OPA response field '{field_name}' must be a non-empty string.")
    return " ".join(value.strip().split())

__all__ = [
    "_build_governance_trace_from_opa_output",
    "_validate_governance_decision_output",
    "_build_static_assessment_from_decision",
    "_build_static_assessment_from_base",
    "_build_judge_static_context",
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
