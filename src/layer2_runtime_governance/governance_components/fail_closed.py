from typing import Any

from layer4_state_evidence_observability.behavioral import BehavioralAssessment
from ..judge import (
    JudgeAssessment,
    JudgeAssessmentStatus,
    JudgeRecommendedDirective,
    JudgeSeverity,
    JudgeVerdict,
)
from layer4_state_evidence_observability.oversight.contracts import DynamicPolicyAssessment
from ..domain import TaskState

# PolicyDecision is the deterministic outcome returned by the policy engine.
from .contracts import (
    GovernanceDecisionTrace,
    GovernanceDirective,
    GovernanceEvaluationResult,
    GovernanceReasonCode,
    PolicyDecision,
    PolicyEnginePort,
    PolicyEvaluationInput,
    StaticPolicyAssessment,
)
from .decision_parser import _build_dynamic_assessment_from_signals
from .directive_mapping import map_behavioral_directive, map_judge_directive

# Build one deterministic component of the fail-closed governance result.
def _build_fail_closed_governance_result(
    *,
    task_state: TaskState,
    policy_input: PolicyEvaluationInput,
    policy_engine_service: PolicyEnginePort,
    reason: str,
    base_facts: dict[str, Any],
    policy_bundle_version: str,
    policy_config_version: str,
    pillar_requirements: dict[str, bool] | None = None,
    pillars: dict[str, Any] | None = None,
    pillar_errors: dict[str, str] | None = None,
    dynamic_assessment: DynamicPolicyAssessment | None = None,
    behavioral_assessment: BehavioralAssessment | None = None,
    judge_assessment: JudgeAssessment | None = None,
) -> GovernanceEvaluationResult:
    from .input_document import build_governance_decision_input

    resolved_requirements = pillar_requirements or {
        "require_evidence": True,
        "require_risk": True,
        "require_trajectory": True,
        "require_behavioral": False,
        "require_judge": False,
    }
    resolved_pillars = pillars or {}
    thresholds_version = policy_config_version
    for name in ("evidence", "trajectory", "judge"):
        resolved_pillars.setdefault(name, _unavailable_pillar(name, thresholds_version))
    for name in ("risk", "behavioral"):
        resolved_pillars.setdefault(
            name,
            _unavailable_pillar(
                name,
                thresholds_version,
                mode="shadow",
                decision_influence=False,
            ),
        )
    decision_input = build_governance_decision_input(
        base_facts=base_facts,
        policy_bundle_version=policy_bundle_version,
        policy_config_version=policy_config_version,
        pillar_requirements=resolved_requirements,
        pillars=resolved_pillars,
        pillar_errors={**(pillar_errors or {}), "opa": reason},
    )
    static_assessment = StaticPolicyAssessment(
        policy_decision=PolicyDecision.REQUIRE_HUMAN_REVIEW,
        reason_codes=(GovernanceReasonCode.OPA_FAILURE_FAIL_CLOSED,),
        summary=f"OPA governance failed closed: {reason}",
        policy_engine_name=policy_engine_service.policy_engine_name,
        policy_bundle_version=policy_engine_service.policy_bundle_version,
        policy_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
    )
    dynamic = dynamic_assessment or _build_dynamic_assessment_from_signals(
        pillars=resolved_pillars,
    )
    behavioral = behavioral_assessment or _unavailable_behavioral_assessment(reason)
    judge = judge_assessment or _not_required_judge_assessment()
    decision = GovernanceDecisionTrace(
        directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        policy_decision=PolicyDecision.REQUIRE_HUMAN_REVIEW,
        reason_codes=(GovernanceReasonCode.OPA_FAILURE_FAIL_CLOSED,),
        summary=f"OPA governance failed closed: {reason}",
        constrains_capability_use=True,
        evaluated_actor_result_type=policy_input.actor_result_type,
        evaluated_tool_name=policy_input.tool_name,
        policy_engine_name=policy_engine_service.policy_engine_name,
        policy_bundle_version=policy_engine_service.policy_bundle_version,
        policy_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        oversight_recommended_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        requires_human_review=True,
        evaluated_plan_step_id=policy_input.current_plan_step_id,
        behavioral_recommended_directive=map_behavioral_directive(behavioral.behavioral_recommended_directive),
        judge_recommended_directive=map_judge_directive(judge.recommendation),
        judge_invoked=judge.invoked,
        static_policy_summary=static_assessment.summary,
        dynamic_policy_summary=dynamic.summary,
        behavioral_summary=behavioral.summary,
        judge_summary=judge.summary,
        oversight_summary=dynamic.summary,
        combiner_selected_reason="OPA failure triggered fail-closed human review.",
        combiner_safety_invariants_checked=("fail_closed_on_opa_error",),
        combiner_signals=({"source": "opa", "dimension": "policy_engine_failure", "rationale": reason},),
        governance_input_schema_version=decision_input.get("schema_version"),
        pillar_requirements=resolved_requirements,
        pillar_results=resolved_pillars,
        pillar_errors={**(pillar_errors or {}), "opa": reason},
        policy_config_version=policy_config_version,
    )
    return GovernanceEvaluationResult(
        policy_input=policy_input,
        static_policy_assessment=static_assessment,
        dynamic_policy_assessment=dynamic,
        behavioral_assessment=behavioral,
        judge_assessment=judge,
        governance_decision=decision,
        decision_input=decision_input,
        pillar_requirements=resolved_requirements,
    )

# Build one deterministic component of the fail-closed governance result.
def _unavailable_pillar(
    name: str,
    thresholds_version: str,
    *,
    mode: str = "active",
    decision_influence: bool = True,
) -> dict[str, Any]:
    return {
        "raw_facts": {
            "pillar_unavailable": True,
            "pillar_name": name,
            "mode": mode,
            "decision_influence": decision_influence,
        },
        "diagnostic_score": None,
        "diagnostic_level": "unavailable",
        "score_components": {},
        "reason_facts": ["pillar_unavailable"],
        "summary": f"{name} pillar unavailable.",
        "score_formula_version": f"{name}_unavailable",
        "thresholds_version": thresholds_version,
        "mode": mode,
        "decision_influence": decision_influence,
    }

# Build one deterministic component of the fail-closed governance result.
def _unavailable_behavioral_assessment(reason: str) -> BehavioralAssessment:
    return BehavioralAssessment(
        history_available=False,
        historical_run_count=0,
        feature_snapshot_version="behavioral_unavailable",
        summary=f"Behavioral pillar unavailable: {reason}",
        notes=("behavioral_unavailable",),
        mode="shadow",
        decision_influence=False,
        status="unavailable",
    )

# Build one deterministic component of the fail-closed governance result.
def _not_required_judge_assessment() -> JudgeAssessment:
    return JudgeAssessment(
        invoked=False,
        recommendation=JudgeRecommendedDirective.CONTINUE,
        confidence=0.0,
        summary="Judge not required by OPA required_pillars.",
        rationale="The first OPA pass did not request a semantic judge assessment.",
        flags=(),
        evaluated_target="not_required",
        status=JudgeAssessmentStatus.NOT_APPLICABLE,
        verdict=JudgeVerdict.UNCERTAIN,
        severity=JudgeSeverity.LOW,
    )

# Build one deterministic component of the fail-closed governance result.
def _unavailable_judge_assessment(reason: str) -> JudgeAssessment:
    return JudgeAssessment(
        invoked=False,
        recommendation=JudgeRecommendedDirective.CONTINUE,
        confidence=0.0,
        summary=f"Judge pillar unavailable: {reason}",
        rationale="The judge backend failed; the failure is passed to OPA as a fact.",
        flags=("judge_unavailable",),
        evaluated_target="unavailable",
        status=JudgeAssessmentStatus.UNAVAILABLE,
        verdict=JudgeVerdict.UNCERTAIN,
        severity=JudgeSeverity.HIGH,
    )

__all__ = [
    "_build_fail_closed_governance_result",
    "_unavailable_pillar",
    "_unavailable_behavioral_assessment",
    "_not_required_judge_assessment",
    "_unavailable_judge_assessment"
]
