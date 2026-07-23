from typing import Any

from ..behavioral import BehavioralAssessment, BehavioralStorePort
from ..judge import JudgeAssessment, JudgePort
from ..oversight.contracts import DynamicPolicyAssessment
from ..domain import TaskState
from ..tools import ToolGatewayConfig

# PolicyDecision is the deterministic outcome returned by the policy engine.
from .contracts import (
    GovernanceEvaluationResult,
    PolicyEngineExecutionError,
    PolicyEnginePort,
)
from .decision_parser import (
    _build_dynamic_assessment_from_signals,
    _build_governance_trace_from_opa_output,
    _build_static_assessment_from_decision,
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
    tool_gateway_config: ToolGatewayConfig | None = None,
    thread_id: str | None = None,
) -> GovernanceEvaluationResult:
    from ..behavioral import build_behavioral_assessment
    from .input_document import (
        build_governance_base_facts,
        build_governance_decision_input,
        validate_governance_decision_input,
    )
    from ..judge import build_default_judge
    from ..signal_services import (
        build_behavioral_signal,
        build_evidence_signal,
        build_judge_context,
        build_judge_signal,
        build_risk_signal,
        build_trajectory_signal,
        validate_required_judge_assessment,
    )
    from ..external_operations.runtime_projection import (
        prepare_external_operation_proposal,
    )

    external_operation = prepare_external_operation_proposal(
        task_state=task_state,
        tool_gateway_config=tool_gateway_config,
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
    except Exception as exc:
        pillar_errors["evidence"] = str(exc)
        pillars["evidence"] = _unavailable_pillar("evidence", thresholds_version)

    try:
        pillars["risk"] = build_risk_signal(
            policy_input=policy_input,
            external_operation=external_operation,
            thresholds_version=thresholds_version,
            tool_gateway_config=tool_gateway_config,
        )
    except Exception as exc:
        # Risk is shadow-only: its failure is visible but never activates fail-closed.
        pillar_errors["risk"] = str(exc)
        pillars["risk"] = _unavailable_pillar(
            "risk",
            thresholds_version,
            mode="shadow",
            decision_influence=False,
        )

    try:
        pillars["trajectory"] = build_trajectory_signal(
            task_state=task_state,
            policy_input=policy_input,
            thresholds_version=thresholds_version,
        )
    except Exception as exc:
        pillar_errors["trajectory"] = str(exc)
        pillars["trajectory"] = _unavailable_pillar("trajectory", thresholds_version)

    dynamic_assessment = _build_dynamic_assessment_from_signals(pillars=pillars)

    resolved_thread_id = thread_id or task_state.runtime_thread_id or task_state.intake_metadata.request_id
    try:
        # Behavioral is shadow-only, so one cached read-model assessment per run is sufficient.
        behavioral_assessment = (
            task_state.last_behavioral_assessment
            if task_state.last_behavioral_assessment is not None
            else build_behavioral_assessment(
                thread_id=resolved_thread_id,
                task_state=task_state,
                behavioral_store=behavioral_store_service,
            )
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

    if pillar_requirements.get("require_judge", False):
        try:
            if external_operation is None:
                raise ValueError("OPA required judge without a canonical external operation.")
            resolved_judge = judge_service or build_default_judge()
            judge_context = build_judge_context(
                task_state=task_state,
                external_operation=external_operation,
                invocation_reason="opa_required_governed_external_execution_candidate",
            )
            judge_assessment = resolved_judge.evaluate(
                context=judge_context,
            )
            judge_assessment = validate_required_judge_assessment(
                judge_assessment=judge_assessment,
                expected_operation_digest=external_operation.payload_digest,
            )
        except Exception as exc:
            pillar_errors["judge"] = str(exc)
            judge_assessment = _unavailable_judge_assessment(str(exc))
    else:
        judge_assessment = _not_required_judge_assessment()
    pillars["judge"] = build_judge_signal(
        judge_assessment=judge_assessment,
        required=pillar_requirements.get("require_judge", False),
        expected_operation_digest=(
            external_operation.payload_digest if external_operation is not None else None
        ),
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
    required_fields = (
        "require_evidence",
        "require_risk",
        "require_trajectory",
        "require_behavioral",
        "require_judge",
    )
    if any(type(result.get(field_name)) is not bool for field_name in required_fields):
        raise PolicyEngineExecutionError(
            "OPA required_pillars response is missing one or more boolean fields."
        )
    return {field_name: result[field_name] for field_name in required_fields}

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
