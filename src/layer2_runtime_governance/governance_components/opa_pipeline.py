from typing import Any

from ..behavioral import BehavioralAssessment, BehavioralStorePort
from ..judge import JudgeAssessment, JudgePort
from ..oversight import DynamicPolicyAssessment
from ..domain import TaskState

# PolicyDecision is the deterministic outcome returned by the policy engine.
from .contracts import (
    GovernanceDirective,
    GovernanceEvaluationResult,
    PolicyEngineExecutionError,
    PolicyEnginePort,
)
from .decision_parser import (
    _build_dynamic_assessment_from_signals,
    _build_governance_trace_from_opa_output,
    _build_judge_static_context,
    _build_static_assessment_from_decision,
    _parse_governance_directive,
)
from .fail_closed import (
    _build_fail_closed_governance_result,
    _not_required_judge_assessment,
    _unavailable_behavioral_assessment,
    _unavailable_judge_assessment,
    _unavailable_pillar,
)
from .policy_input import build_policy_evaluation_input

# Keep OPA orchestration explicit and independently testable.
def evaluate_opa_first_governance(
    *,
    task_state: TaskState,
    policy_engine_service: PolicyEnginePort,
    judge_service: JudgePort | None = None,
    behavioral_store_service: BehavioralStorePort | None = None,
    thread_id: str | None = None,
) -> GovernanceEvaluationResult:
    from ..behavioral import build_behavioral_assessment
    from ..governance_input_builder import (
        build_governance_base_facts,
        build_governance_decision_input,
        validate_governance_decision_input,
    )
    from ..judge import build_default_judge
    from ..signal_services import (
        build_behavioral_signal,
        build_evidence_signal,
        build_judge_signal,
        build_risk_signal,
        build_trajectory_signal,
    )

    policy_input = build_policy_evaluation_input(task_state=task_state)
    # OPA owns configuration; the PEP only declares the compatible bundle versions.
    policy_bundle_version = str(policy_engine_service.policy_bundle_version)
    policy_config_version = str(
        getattr(
            policy_engine_service,
            "policy_config_version",
            "layer2_governance_thresholds_v1",
        )
    )
    thresholds_version = policy_config_version
    base_facts = build_governance_base_facts(
        task_state=task_state,
        policy_input=policy_input,
    )

    try:
        pillar_requirements = _evaluate_required_pillars(
            policy_engine_service=policy_engine_service,
            base_facts=base_facts,
        )
    except Exception as exc:
        return _build_fail_closed_governance_result(
            task_state=task_state,
            policy_input=policy_input,
            policy_engine_service=policy_engine_service,
            reason=f"OPA required_pillars failed: {exc}",
            base_facts=base_facts,
            policy_bundle_version=policy_bundle_version,
            policy_config_version=policy_config_version,
        )

    pillar_errors: dict[str, str] = {}
    pillars: dict[str, Any] = {}
    dynamic_assessment: DynamicPolicyAssessment | None = None
    behavioral_assessment: BehavioralAssessment | None = None
    judge_assessment: JudgeAssessment | None = None

    try:
        pillars["evidence"] = build_evidence_signal(
            task_state=task_state,
            thresholds_version=thresholds_version,
        )
        pillars["risk"] = build_risk_signal(
            policy_input=policy_input,
            thresholds_version=thresholds_version,
        )
        pillars["trajectory"] = build_trajectory_signal(
            task_state=task_state,
            policy_input=policy_input,
            thresholds_version=thresholds_version,
        )
        dynamic_assessment = _build_dynamic_assessment_from_signals(
            pillars=pillars,
            directive=GovernanceDirective.CONTINUE,
        )
    except Exception as exc:
        pillar_errors["deterministic_pillars"] = str(exc)
        pillars.setdefault("evidence", _unavailable_pillar("evidence", thresholds_version))
        pillars.setdefault("risk", _unavailable_pillar("risk", thresholds_version))
        pillars.setdefault("trajectory", _unavailable_pillar("trajectory", thresholds_version))
        dynamic_assessment = _build_dynamic_assessment_from_signals(
            pillars=pillars,
            directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        )

    resolved_thread_id = thread_id or task_state.runtime_thread_id or task_state.intake_metadata.request_id
    try:
        behavioral_assessment = build_behavioral_assessment(
            thread_id=resolved_thread_id,
            task_state=task_state,
            dynamic_policy_assessment=dynamic_assessment,
            behavioral_store=behavioral_store_service,
        )
        pillars["behavioral"] = build_behavioral_signal(
            behavioral_assessment=behavioral_assessment,
            thresholds_version=thresholds_version,
        )
    except Exception as exc:
        pillar_errors["behavioral"] = str(exc)
        behavioral_assessment = _unavailable_behavioral_assessment(str(exc))
        pillars["behavioral"] = build_behavioral_signal(
            behavioral_assessment=behavioral_assessment,
            thresholds_version=thresholds_version,
        )

    # The judge receives opaque static context; only OPA interprets policy semantics.
    static_for_judge = _build_judge_static_context(
        policy_engine_service=policy_engine_service,
    )
    if pillar_requirements.get("require_judge", False):
        try:
            resolved_judge = judge_service or build_default_judge()
            judge_assessment = resolved_judge.evaluate(
                task_state=task_state,
                policy_input=policy_input,
                static_policy_assessment=static_for_judge,
                dynamic_policy_assessment=dynamic_assessment,
                behavioral_assessment=behavioral_assessment,
                evaluated_target="opa_required",
                invocation_flags=("opa_required_judge",),
            )
        except Exception as exc:
            pillar_errors["judge"] = str(exc)
            judge_assessment = _unavailable_judge_assessment(str(exc))
    else:
        judge_assessment = _not_required_judge_assessment()
    pillars["judge"] = build_judge_signal(
        judge_assessment=judge_assessment,
        required=pillar_requirements.get("require_judge", False),
        thresholds_version=thresholds_version,
    )

    decision_input = build_governance_decision_input(
        base_facts=base_facts,
        policy_bundle_version=policy_bundle_version,
        policy_config_version=policy_config_version,
        pillar_requirements=pillar_requirements,
        pillars=pillars,
        pillar_errors=pillar_errors,
    )
    try:
        validate_governance_decision_input(decision_input)
    except Exception as exc:
        return _build_fail_closed_governance_result(
            task_state=task_state,
            policy_input=policy_input,
            policy_engine_service=policy_engine_service,
            reason=f"GovernanceDecisionInput validation failed: {exc}",
            base_facts=base_facts,
            policy_bundle_version=policy_bundle_version,
            policy_config_version=policy_config_version,
            pillar_requirements=pillar_requirements,
            pillars=pillars,
            pillar_errors=pillar_errors,
            dynamic_assessment=dynamic_assessment,
            behavioral_assessment=behavioral_assessment,
            judge_assessment=judge_assessment,
        )

    try:
        raw_output = _evaluate_governance_decision_output(
            policy_engine_service=policy_engine_service,
            decision_input=decision_input,
        )
        # Build every exported diagnostic from the scored final OPA pillars.
        dynamic_assessment = _build_dynamic_assessment_from_signals(
            pillars=raw_output.get("pillar_results", pillars),
            directive=_parse_governance_directive(raw_output.get("directive")),
        )
        governance_decision = _build_governance_trace_from_opa_output(
            raw_output=raw_output,
            policy_input=policy_input,
            policy_engine_service=policy_engine_service,
            dynamic_policy_assessment=dynamic_assessment,
            behavioral_assessment=behavioral_assessment,
            judge_assessment=judge_assessment,
            decision_input=decision_input,
        )
    except Exception as exc:
        return _build_fail_closed_governance_result(
            task_state=task_state,
            policy_input=policy_input,
            policy_engine_service=policy_engine_service,
            reason=f"OPA decision failed: {exc}",
            base_facts=base_facts,
            policy_bundle_version=policy_bundle_version,
            policy_config_version=policy_config_version,
            pillar_requirements=pillar_requirements,
            pillars=pillars,
            pillar_errors=pillar_errors,
            dynamic_assessment=dynamic_assessment,
            behavioral_assessment=behavioral_assessment,
            judge_assessment=judge_assessment,
        )

    static_policy_assessment = _build_static_assessment_from_decision(
        raw_output=raw_output,
        policy_input=policy_input,
        policy_engine_service=policy_engine_service,
    )
    return GovernanceEvaluationResult(
        policy_input=policy_input,
        static_policy_assessment=static_policy_assessment,
        dynamic_policy_assessment=dynamic_assessment,
        behavioral_assessment=behavioral_assessment,
        judge_assessment=judge_assessment,
        governance_decision=governance_decision,
        decision_input=decision_input,
        pillar_requirements=pillar_requirements,
    )

# Keep OPA orchestration explicit and independently testable.
def _evaluate_required_pillars(
    *,
    policy_engine_service: PolicyEnginePort,
    base_facts: dict[str, Any],
) -> dict[str, bool]:
    result = policy_engine_service.evaluate_required_pillars(base_facts=base_facts)
    if not isinstance(result, dict):
        raise PolicyEngineExecutionError("OPA required_pillars response must be an object.")
    return {
        "require_evidence": bool(result.get("require_evidence", True)),
        "require_risk": bool(result.get("require_risk", True)),
        "require_trajectory": bool(result.get("require_trajectory", True)),
        "require_behavioral": bool(result.get("require_behavioral", True)),
        "require_judge": bool(result.get("require_judge", False)),
    }

# Keep OPA orchestration explicit and independently testable.
def _evaluate_governance_decision_output(
    *,
    policy_engine_service: PolicyEnginePort,
    decision_input: dict[str, Any],
) -> dict[str, Any]:
    result = policy_engine_service.evaluate_decision(decision_input=decision_input)
    if not isinstance(result, dict):
        raise PolicyEngineExecutionError("OPA decision response must be an object.")
    return result

__all__ = [
    "evaluate_opa_first_governance",
    "_evaluate_required_pillars",
    "_evaluate_governance_decision_output"
]
