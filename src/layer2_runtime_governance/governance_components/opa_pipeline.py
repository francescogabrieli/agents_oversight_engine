from dataclasses import replace
from typing import Any

from layer4_state_evidence_observability.behavioral import BehavioralAssessment, BehavioralStorePort
from ..judge import JudgeAssessment, JudgePort
from layer4_state_evidence_observability.oversight.contracts import DynamicPolicyAssessment
from ..domain import (
    ActorResultType,
    TaskState,
    build_evidence_selection_digest,
    build_evidence_snapshot_manifest,
    digest_json,
)
from layer3_tool_bus_external_capabilities.tools import ToolGatewayConfig

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
    _validate_governance_decision_output,
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
    from layer4_state_evidence_observability.behavioral import build_behavioral_assessment
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
    from layer3_tool_bus_external_capabilities.external_operations.runtime_projection import (
        prepare_external_operation_proposal,
    )

    external_operation = prepare_external_operation_proposal(
        task_state=task_state,
        tool_gateway_config=tool_gateway_config,
    )
    _bind_late_projected_operation_to_completion_candidate(
        task_state=task_state,
        external_operation=external_operation,
    )
    policy_input = build_policy_evaluation_input(task_state=task_state)
    # OPA owns configuration; the PEP only declares the compatible bundle versions.
    policy_bundle_version = str(policy_engine_service.policy_bundle_version)
    policy_config_version = str(
        getattr(
            policy_engine_service,
            "policy_config_version",
            "layer2_governance_thresholds_v2",
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

    if pillar_requirements.get("require_evidence", False):
        try:
            pillars["evidence"] = build_evidence_signal(task_state=task_state, thresholds_version=thresholds_version)
        except Exception as exc:
            pillar_errors["evidence"] = str(exc)
            pillars["evidence"] = _unavailable_pillar("evidence", thresholds_version)
    else:
        pillars["evidence"] = _unavailable_pillar("evidence", thresholds_version, mode="inactive", decision_influence=False)

    if external_operation is not None:
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
    else:
        pillars["risk"] = _unavailable_pillar("risk", thresholds_version, mode="shadow", decision_influence=False)

    if pillar_requirements.get("require_trajectory", False):
        try:
            pillars["trajectory"] = build_trajectory_signal(
                task_state=task_state,
                policy_input=policy_input,
                thresholds_version=thresholds_version,
            )
        except Exception as exc:
            pillar_errors["trajectory"] = str(exc)
            pillars["trajectory"] = _unavailable_pillar("trajectory", thresholds_version)
    else:
        pillars["trajectory"] = _unavailable_pillar(
            "trajectory",
            thresholds_version,
            mode="inactive",
            decision_influence=False,
        )

    dynamic_assessment = _build_dynamic_assessment_from_signals(pillars=pillars)

    resolved_thread_id = thread_id or task_state.runtime_thread_id or task_state.intake_metadata.request_id
    # Behavioural SQLite is a shadow read model, sampled only at stable audit boundaries.
    if task_state.current_state.value in {"waiting_human_review", "completed"} or external_operation is not None:
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
    else:
        behavioral_assessment = _unavailable_behavioral_assessment("shadow sampling deferred")
        pillars["behavioral"] = build_behavioral_signal(behavioral_assessment=behavioral_assessment, thresholds_version=thresholds_version)

    evidence_facts = pillars.get("evidence", {}).get("raw_facts", {})
    expected_evidence_snapshot_digest = evidence_facts.get(
        "evidence_snapshot_digest"
    )
    expected_evidence_selection_digest = evidence_facts.get(
        "evidence_selection_digest"
    )

    # Ask OPA for staged judge admission after deterministic signals exist.  This is a
    # policy query, not a Python decision: its output only controls whether to collect
    # the optional semantic assessment before the final OPA directive.
    preliminary_judge = _not_required_judge_assessment()
    pillars["judge"] = build_judge_signal(
        judge_assessment=preliminary_judge,
        required=False,
        expected_operation_digest=(
            external_operation.payload_digest if external_operation is not None else None
        ),
        expected_evidence_snapshot_digest=expected_evidence_snapshot_digest,
        expected_evidence_selection_digest=expected_evidence_selection_digest,
        thresholds_version=thresholds_version,
    )
    try:
        preliminary_input = build_governance_decision_input(
            base_facts=base_facts,
            policy_bundle_version=policy_bundle_version,
            policy_config_version=policy_config_version,
            pillar_requirements=pillar_requirements,
            pillars=pillars,
            pillar_errors=pillar_errors,
        )
        validate_governance_decision_input(preliminary_input)
        preliminary_output = _evaluate_governance_decision_output(
            policy_engine_service=policy_engine_service,
            decision_input=preliminary_input,
        )
        _validate_governance_decision_output(preliminary_output)
        judge_required = _parse_judge_admission(preliminary_output)["required"]
    except Exception as exc:
        return _build_fail_closed_governance_result(
            task_state=task_state, policy_input=policy_input,
            policy_engine_service=policy_engine_service,
            reason=f"OPA judge admission failed: {exc}", base_facts=base_facts,
            policy_bundle_version=policy_bundle_version,
            policy_config_version=policy_config_version,
            pillar_requirements=pillar_requirements, pillars=pillars,
            pillar_errors={**pillar_errors, "judge_admission": str(exc)},
            dynamic_assessment=dynamic_assessment,
            behavioral_assessment=behavioral_assessment,
        )
    pillar_requirements = {**pillar_requirements, "require_judge": judge_required}

    if judge_required:
        try:
            if external_operation is None:
                raise ValueError("OPA required judge without a canonical external operation.")
            judge_context = build_judge_context(
                task_state=task_state,
                external_operation=external_operation,
                invocation_reason="opa_required_governed_external_execution_candidate",
            )
            if (
                judge_context.evidence_snapshot_digest
                != expected_evidence_snapshot_digest
                or judge_context.evidence_selection_digest
                != expected_evidence_selection_digest
            ):
                raise ValueError(
                    "Judge context does not match the Evidence pillar snapshot binding."
                )
            # Reuse only an assessment bound to the exact payload and evidence universe.
            judge_assessment = next(
                (
                    assessment
                    for assessment in reversed(task_state.judge_history)
                    if assessment.evaluated_operation_digest == external_operation.payload_digest
                    and assessment.evaluated_evidence_snapshot_digest
                    == expected_evidence_snapshot_digest
                    and assessment.evaluated_evidence_selection_digest
                    == expected_evidence_selection_digest
                    and assessment.status.value == "active"
                ),
                None,
            )
            if judge_assessment is None:
                resolved_judge = judge_service or build_default_judge()
                judge_assessment = resolved_judge.evaluate(context=judge_context)
            judge_assessment = validate_required_judge_assessment(
                judge_assessment=judge_assessment,
                expected_operation_digest=external_operation.payload_digest,
                expected_evidence_snapshot_digest=expected_evidence_snapshot_digest,
                expected_evidence_selection_digest=expected_evidence_selection_digest,
            )
        except Exception as exc:
            pillar_errors["judge"] = str(exc)
            judge_assessment = _unavailable_judge_assessment(str(exc))
    else:
        judge_assessment = preliminary_judge
    pillars["judge"] = build_judge_signal(
        judge_assessment=judge_assessment,
        required=judge_required,
        expected_operation_digest=(
            external_operation.payload_digest if external_operation is not None else None
        ),
        expected_evidence_snapshot_digest=expected_evidence_snapshot_digest,
        expected_evidence_selection_digest=expected_evidence_selection_digest,
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


# Validate the policy-owned staged-admission output without truthiness coercion.
def _parse_judge_admission(raw_output: dict[str, Any]) -> dict[str, Any]:
    admission = raw_output.get("judge_admission")
    if (
        not isinstance(admission, dict)
        or type(admission.get("required")) is not bool
        or not isinstance(admission.get("reason"), str)
        or not admission["reason"].strip()
    ):
        raise PolicyEngineExecutionError(
            "OPA judge_admission must contain boolean required and non-empty reason."
        )
    return {
        "required": admission["required"],
        "reason": admission["reason"].strip(),
    }


# Seal a proposal that governance projects after the actor created its completion candidate.
def _bind_late_projected_operation_to_completion_candidate(
    *,
    task_state: TaskState,
    external_operation: Any,
) -> None:
    candidate = task_state.pending_completion_candidate
    actor_result = task_state.actor_result
    if (
        external_operation is None
        or candidate is None
        or actor_result is None
        or actor_result.result_type != ActorResultType.STEP_COMPLETED
        or candidate.step_id != external_operation.plan_step_id
        or actor_result.plan_step_id != external_operation.plan_step_id
    ):
        return
    # Existing non-empty bindings are immutable; Evidence must reject later drift.
    if (
        candidate.external_operation_id is not None
        or candidate.external_operation_digest is not None
    ):
        return
    snapshot_digest = digest_json(
        build_evidence_snapshot_manifest(task_state=task_state)
    )
    selection_digest = build_evidence_selection_digest(
        selected_refs=candidate.evidence_refs,
        evidence_snapshot_digest=snapshot_digest,
        external_operation_digest=external_operation.payload_digest,
    )
    task_state.pending_completion_candidate = replace(
        candidate,
        external_operation_id=external_operation.operation_id,
        external_operation_digest=external_operation.payload_digest,
        evidence_snapshot_digest=snapshot_digest,
        evidence_selection_digest=selection_digest,
    )


__all__ = [
    "evaluate_opa_first_governance",
    "_evaluate_required_pillars",
    "_evaluate_governance_decision_output",
    "_parse_judge_admission",
]
