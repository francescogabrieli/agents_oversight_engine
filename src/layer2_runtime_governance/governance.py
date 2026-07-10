from __future__ import annotations

import json
import os
import warnings
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from layer1_intake import DomainType, SensitivityLevel

from .actor_contract import ActorResult, ActorResultType, CapabilityAccessMode
from .behavioral import BehavioralAssessment, BehavioralRecommendedDirective, BehavioralStorePort
from .judge import JudgeAssessment, JudgePort, JudgeRecommendedDirective
from .oversight import (
    DynamicPolicyAssessment,
    DynamicPolicyRecommendedDirective,
    EvidenceQualityAssessment,
    OperationalRiskAssessment,
    OversightReasonCode,
    OversightSignalLevel,
    TrajectoryHealthAssessment,
)
from .state import (
    ExternalCommunicationPhase,
    RuntimeFlag,
    RuntimeState,
    ScratchpadEntryType,
    StepValidationDecision,
    TaskState,
)
from .tool_contract import ToolName


# PolicyDecision is the deterministic outcome returned by the policy engine.
class PolicyDecision(str, Enum):
    ALLOW = "allow"
    ALLOW_READ_ONLY = "allow_read_only"
    ASK_CLARIFICATION = "ask_clarification"
    REQUIRE_HUMAN_REVIEW = "require_human_review"
    DENY = "deny"


# GovernanceDirective is the runtime action that follows the policy decision.
class GovernanceDirective(str, Enum):
    CONTINUE = "continue"
    CONTINUE_READ_ONLY = "continue_read_only"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REPLAN = "replan"
    ASK_USER_CLARIFICATION = "ask_user_clarification"
    WAIT_HUMAN_REVIEW = "wait_human_review"
    STOP = "stop"


# Reason codes remain compact and deterministic so tests and traces stay stable.
class GovernanceReasonCode(str, Enum):
    ALLOWED_BY_POLICY = "allowed_by_policy"
    READ_ONLY_ALLOWED = "read_only_allowed"
    WRITE_CAPABILITY_BLOCKED = "write_capability_blocked"
    READ_ONLY_MODE_ENFORCED = "read_only_mode_enforced"
    LOCAL_OUTPUT_ALLOWED = "local_output_allowed"
    REPLAN_ALLOWED = "replan_allowed"
    ASK_CLARIFICATION_REQUIRED = "ask_clarification_required"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    DENIED_BY_POLICY = "denied_by_policy"
    NO_TOOL_REQUEST = "no_tool_request"
    EXTERNAL_CAPABILITY_REQUESTED = "external_capability_requested"
    EXTERNAL_COMMUNICATION_REVIEW_REQUIRED = "external_communication_review_required"
    EVIDENCE_SUPPORT_BELOW_STEP_NEED = "evidence_support_below_step_need"
    PRECISION_MISMATCH = "precision_mismatch"
    TRAJECTORY_DEGRADED = "trajectory_degraded"
    EVIDENCE_GAP_AND_TRAJECTORY_DEGRADED = "evidence_gap_and_trajectory_degraded"
    HIGH_OPERATIONAL_RISK_EXTERNAL = "high_operational_risk_external"
    REPLAN_LIMIT_EXCEEDED = "replan_limit_exceeded"
    PROVIDER_FAILURES_REPEATED = "provider_failures_repeated"
    REPEATED_RECOVERY = "repeated_recovery"
    BEHAVIORAL_ANOMALY_NEEDS_CORROBORATION = "behavioral_anomaly_needs_corroboration"
    BEHAVIORAL_CORROBORATED_RISK = "behavioral_corroborated_risk"
    JUDGE_REJECT_OUTPUT = "judge_reject_output"
    JUDGE_REJECT_EXTERNAL_OR_HIGH_RISK = "judge_reject_external_or_high_risk"
    JUDGE_FLAG_EXTERNAL_DRAFT = "judge_flag_external_draft"
    POLICY_VERSION_MISMATCH = "policy_version_mismatch"
    REQUIRED_PILLAR_UNAVAILABLE = "required_pillar_unavailable"
    OPA_FAILURE_FAIL_CLOSED = "opa_failure_fail_closed"


@dataclass(frozen=True)
class PolicyEvaluationInput:
    # PolicyEvaluationInput is the compact runtime snapshot sent to governance layers.
    task_domain_type: DomainType
    task_domain_label: str
    task_sensitivity: SensitivityLevel
    policy_profile_name: str
    confidence_threshold: float
    read_only_mode: bool
    budget_max_steps: int | None
    budget_max_tool_calls: int | None
    runtime_state: RuntimeState
    runtime_flags: tuple[RuntimeFlag, ...]
    current_plan_step_id: str | None
    current_plan_step_objective: str | None
    actor_result_type: ActorResultType
    capability_mode: CapabilityAccessMode
    external_impact: bool
    blocking_reason: str | None
    actor_has_output_text: bool
    tool_name: ToolName | None
    tool_has_query: bool
    tool_has_url: bool
    tool_call_count: int
    observed_evidence_count: int
    actor_turn_count: int
    external_communication_phase: ExternalCommunicationPhase | None
    external_communication_review_ready: bool


@dataclass(frozen=True)
class PolicyEvaluationResult:
    # PolicyEvaluationResult mirrors the raw typed output returned by OPA.
    policy_decision: PolicyDecision
    reason_codes: tuple[GovernanceReasonCode, ...]
    summary: str
    policy_engine_name: str
    policy_bundle_version: str


@dataclass(frozen=True)
class StaticPolicyAssessment:
    # StaticPolicyAssessment is the hard deterministic result produced by OPA.
    policy_decision: PolicyDecision
    reason_codes: tuple[GovernanceReasonCode, ...]
    summary: str
    policy_engine_name: str
    policy_bundle_version: str
    policy_directive: GovernanceDirective


@dataclass(frozen=True)
class GovernanceDecisionTrace:
    # GovernanceDecisionTrace is the canonical combined governance record stored in TaskState.
    directive: GovernanceDirective
    policy_decision: PolicyDecision
    reason_codes: tuple[GovernanceReasonCode, ...]
    summary: str
    constrains_capability_use: bool
    evaluated_actor_result_type: ActorResultType
    evaluated_tool_name: ToolName | None
    policy_engine_name: str
    policy_bundle_version: str
    policy_directive: GovernanceDirective
    oversight_recommended_directive: GovernanceDirective
    behavioral_recommended_directive: GovernanceDirective = GovernanceDirective.CONTINUE
    judge_recommended_directive: GovernanceDirective = GovernanceDirective.CONTINUE
    judge_invoked: bool = False
    static_policy_summary: str | None = None
    dynamic_policy_summary: str | None = None
    behavioral_summary: str | None = None
    judge_summary: str | None = None
    behavioral_similarity_score: float | None = None
    behavioral_deviation_score: float | None = None
    behavioral_novelty_score: float | None = None
    oversight_summary: str | None = None
    combiner_dominant_dimension: str | None = None
    combiner_support_by_directive: dict[str, float] | None = None
    combiner_selected_reason: str | None = None
    combiner_rejected_alternatives: dict[str, str] | None = None
    combiner_safety_invariants_checked: tuple[str, ...] = ()
    combiner_signals: tuple[dict[str, Any], ...] = ()
    combiner_normalized_directive: str | None = None
    combiner_normalization_reason: str | None = None
    governance_input_schema_version: str | None = None
    pillar_requirements: dict[str, bool] | None = None
    pillar_results: dict[str, Any] | None = None
    pillar_errors: dict[str, str] | None = None
    policy_config_version: str | None = None


@dataclass(frozen=True)
class GovernanceEvaluationResult:
    # GovernanceEvaluationResult carries all runtime records produced by the OPA-first PEP flow.
    policy_input: PolicyEvaluationInput
    static_policy_assessment: StaticPolicyAssessment
    dynamic_policy_assessment: DynamicPolicyAssessment
    behavioral_assessment: BehavioralAssessment
    judge_assessment: JudgeAssessment
    governance_decision: GovernanceDecisionTrace
    decision_input: dict[str, Any]
    pillar_requirements: dict[str, bool]


# Keep the legacy public name stable while the internal model becomes more explicit.
GovernanceDecision = GovernanceDecisionTrace


# PolicyEnginePort keeps the runtime decoupled from the policy engine backend.
class PolicyEnginePort(Protocol):
    policy_engine_name: str
    policy_bundle_version: str
    policy_config_version: str

    def evaluate_required_pillars(self, *, base_facts: dict[str, Any]) -> dict[str, bool]: ...

    def evaluate_decision(self, *, decision_input: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class OPAHttpPolicyEngineSettings:
    # Settings for the HTTP OPA adapter.
    base_url: str = "http://127.0.0.1:8181"
    decision_path: str = "/v1/data/layer2/governance/decision"
    timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "OPAHttpPolicyEngineSettings":
        # Merge process env and local .env files into one policy engine settings object.
        env_file_values = _load_env_file_values()
        return cls(
            base_url=os.getenv(
                "LAYER2_GOVERNANCE_OPA_BASE_URL",
                env_file_values.get("LAYER2_GOVERNANCE_OPA_BASE_URL", "http://127.0.0.1:8181"),
            ),
            decision_path=os.getenv(
                "LAYER2_GOVERNANCE_OPA_DECISION_PATH",
                env_file_values.get(
                    "LAYER2_GOVERNANCE_OPA_DECISION_PATH",
                    "/v1/data/layer2/governance/decision",
                ),
            ),
            timeout_seconds=float(
                os.getenv(
                    "LAYER2_GOVERNANCE_OPA_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_GOVERNANCE_OPA_TIMEOUT_SECONDS", "5"),
                )
            ),
        )


# Raised when the policy engine cannot return a valid deterministic decision.
class PolicyEngineExecutionError(RuntimeError):
    pass


# Build the policy input snapshot from TaskState and the actor outcome.
def build_policy_evaluation_input(*, task_state: TaskState) -> PolicyEvaluationInput:
    actor_result = task_state.actor_result
    if actor_result is None:
        raise ValueError("Cannot build policy evaluation input without an actor result.")

    evaluated_step = _resolve_evaluated_plan_step(task_state=task_state, actor_result=actor_result)
    current_tool_request = task_state.current_tool_request or actor_result.tool_request
    return PolicyEvaluationInput(
        task_domain_type=task_state.task_spec.domain_type,
        task_domain_label=task_state.task_spec.domain_label,
        task_sensitivity=task_state.task_spec.sensitivity,
        policy_profile_name=task_state.execution_policy.policy_profile_name,
        confidence_threshold=task_state.execution_policy.confidence_threshold,
        read_only_mode=task_state.execution_policy.read_only_mode,
        budget_max_steps=task_state.execution_policy.budget.max_steps,
        budget_max_tool_calls=task_state.execution_policy.budget.max_tool_calls,
        runtime_state=task_state.current_state,
        runtime_flags=tuple(task_state.runtime_flags),
        current_plan_step_id=evaluated_step.step_id if evaluated_step is not None else actor_result.plan_step_id,
        current_plan_step_objective=evaluated_step.objective if evaluated_step is not None else None,
        actor_result_type=actor_result.result_type,
        capability_mode=actor_result.capability_mode,
        external_impact=actor_result.external_impact,
        blocking_reason=actor_result.blocking_reason,
        actor_has_output_text=bool(actor_result.output_text and actor_result.output_text.strip()),
        tool_name=current_tool_request.tool_name if current_tool_request is not None else None,
        tool_has_query=bool(current_tool_request and current_tool_request.query),
        tool_has_url=bool(current_tool_request and current_tool_request.url),
        tool_call_count=task_state.tool_call_count,
        observed_evidence_count=len(task_state.observed_evidence),
        actor_turn_count=task_state.actor_turn_count,
        external_communication_phase=(
            task_state.external_communication.phase if task_state.external_communication is not None else None
        ),
        external_communication_review_ready=(
            task_state.external_communication.review_ready
            if task_state.external_communication is not None
            else False
        ),
    )


# Turn the typed governance input into the JSON document posted to OPA.
def build_opa_input_document(*, policy_input: PolicyEvaluationInput) -> dict[str, Any]:
    return {
        "task": {
            "domain_type": policy_input.task_domain_type.value,
            "domain_label": policy_input.task_domain_label,
            "sensitivity": policy_input.task_sensitivity.value,
        },
        "policy": {
            "profile_name": policy_input.policy_profile_name,
            "confidence_threshold": policy_input.confidence_threshold,
            "read_only_mode": policy_input.read_only_mode,
            "budget": {
                "max_steps": policy_input.budget_max_steps,
                "max_tool_calls": policy_input.budget_max_tool_calls,
            },
        },
        "runtime": {
            "state": policy_input.runtime_state.value,
            "flags": [flag.value for flag in policy_input.runtime_flags],
            "tool_call_count": policy_input.tool_call_count,
            "observed_evidence_count": policy_input.observed_evidence_count,
            "actor_turn_count": policy_input.actor_turn_count,
        },
        "step": {
            "id": policy_input.current_plan_step_id,
            "objective": policy_input.current_plan_step_objective,
        },
        "actor": {
            "result_type": policy_input.actor_result_type.value,
            "capability_mode": policy_input.capability_mode.value,
            "external_impact": policy_input.external_impact,
            "blocking_reason": policy_input.blocking_reason,
            "has_output_text": policy_input.actor_has_output_text,
        },
        "tool": {
            "name": policy_input.tool_name.value if policy_input.tool_name is not None else None,
            "has_query": policy_input.tool_has_query,
            "has_url": policy_input.tool_has_url,
        },
        "external_communication": {
            "phase": (
                policy_input.external_communication_phase.value
                if policy_input.external_communication_phase is not None
                else None
            ),
            "review_ready": policy_input.external_communication_review_ready,
        },
    }


# Evaluate the hard static policy layer through the configured OPA-backed engine.
def evaluate_static_policy(
    *,
    policy_engine_service: PolicyEnginePort,
    policy_input: PolicyEvaluationInput,
) -> StaticPolicyAssessment:
    warnings.warn("evaluate_static_policy is deprecated; OPA owns policy interpretation.", DeprecationWarning, stacklevel=2)
    policy_result = policy_engine_service.evaluate(policy_input=policy_input)
    return StaticPolicyAssessment(
        policy_decision=policy_result.policy_decision,
        reason_codes=policy_result.reason_codes,
        summary=policy_result.summary,
        policy_engine_name=policy_result.policy_engine_name,
        policy_bundle_version=policy_result.policy_bundle_version,
        policy_directive=derive_governance_directive(
            policy_input=policy_input,
            policy_result=policy_result,
        ),
    )


# Map the policy outcome and actor intent into a runtime directive.
def derive_governance_directive(
    *,
    policy_input: PolicyEvaluationInput,
    policy_result: PolicyEvaluationResult | None = None,
    static_policy_assessment: StaticPolicyAssessment | None = None,
) -> GovernanceDirective:
    resolved_policy_decision = (
        static_policy_assessment.policy_decision
        if static_policy_assessment is not None
        else policy_result.policy_decision
        if policy_result is not None
        else None
    )
    if resolved_policy_decision is None:
        raise ValueError("derive_governance_directive requires a policy result or static policy assessment.")

    if resolved_policy_decision == PolicyDecision.DENY:
        return GovernanceDirective.STOP
    if resolved_policy_decision == PolicyDecision.REQUIRE_HUMAN_REVIEW:
        return GovernanceDirective.WAIT_HUMAN_REVIEW
    if resolved_policy_decision == PolicyDecision.ASK_CLARIFICATION:
        return GovernanceDirective.ASK_USER_CLARIFICATION
    if policy_input.actor_result_type == ActorResultType.NEEDS_REPLAN:
        return GovernanceDirective.REPLAN
    if resolved_policy_decision == PolicyDecision.ALLOW_READ_ONLY:
        # Preserve the static read-only envelope even when the current actor turn is not a tool proposal.
        return GovernanceDirective.CONTINUE_READ_ONLY
    if policy_input.actor_result_type == ActorResultType.PROPOSE_ACTION:
        return GovernanceDirective.CONTINUE
    return GovernanceDirective.CONTINUE


# Decide whether the policy constrained capability use in a meaningful way.
def constrains_capability_use(
    *,
    policy_input: PolicyEvaluationInput,
    static_policy_assessment: StaticPolicyAssessment,
) -> bool:
    if static_policy_assessment.policy_decision in {
        PolicyDecision.ALLOW_READ_ONLY,
        PolicyDecision.REQUIRE_HUMAN_REVIEW,
        PolicyDecision.DENY,
    }:
        return True
    if (
        policy_input.actor_result_type == ActorResultType.PROPOSE_ACTION
        and policy_input.capability_mode == CapabilityAccessMode.WRITE
    ):
        return True
    return False


# Combine all governance assessments into the one canonical runtime decision record.
def combine_governance_assessments(
    *,
    task_state: TaskState,
    policy_input: PolicyEvaluationInput,
    static_policy_assessment: StaticPolicyAssessment,
    dynamic_policy_assessment: DynamicPolicyAssessment | None = None,
    behavioral_assessment: BehavioralAssessment | None = None,
    judge_assessment: JudgeAssessment | None = None,
) -> GovernanceDecisionTrace:
    warnings.warn("combine_governance_assessments is deprecated; OPA owns final governance decisions.", DeprecationWarning, stacklevel=2)
    from .decision_combiner import combine_decision_signals

    repeated_recovery = _repeated_recovery_attempt_detected(task_state=task_state)
    repeated_provider_failure = _repeated_provider_runtime_failure_detected(task_state=task_state)
    combiner_result = combine_decision_signals(
        static_policy_assessment=static_policy_assessment,
        dynamic_policy_assessment=dynamic_policy_assessment,
        behavioral_assessment=behavioral_assessment,
        judge_assessment=judge_assessment,
        external_impact=policy_input.external_impact,
        validation_allows_completion=_validation_allows_completion(task_state=task_state),
        repeated_recovery=repeated_recovery,
        repeated_provider_failure=repeated_provider_failure,
        provider_runtime_action_missing=_provider_runtime_action_missing(policy_input=policy_input),
    )
    combiner_directive = combiner_result.final_directive
    final_directive = _normalize_intermediate_output_directive(
        task_state=task_state,
        policy_input=policy_input,
        static_policy_assessment=static_policy_assessment,
        final_directive=combiner_directive,
    )
    normalization_reason = _build_combiner_normalization_reason(
        task_state=task_state,
        policy_input=policy_input,
        combiner_directive=combiner_directive,
        final_directive=final_directive,
    )
    dynamic_directive = _map_dynamic_directive(
        dynamic_policy_assessment.recommended_directive
        if dynamic_policy_assessment is not None
        else DynamicPolicyRecommendedDirective.CONTINUE
    )
    behavioral_directive = _map_behavioral_directive(
        behavioral_assessment.behavioral_recommended_directive
        if behavioral_assessment is not None
        else BehavioralRecommendedDirective.CONTINUE
    )
    judge_directive = _map_judge_directive(
        judge_assessment.recommendation
        if judge_assessment is not None
        else JudgeRecommendedDirective.CONTINUE
    )
    combined_summary = _build_combined_summary(
        static_policy_assessment=static_policy_assessment,
        final_directive=final_directive,
        dynamic_policy_assessment=dynamic_policy_assessment,
        behavioral_assessment=behavioral_assessment,
        judge_assessment=judge_assessment,
        combiner_selected_reason=combiner_result.selected_reason,
        combiner_directive=combiner_directive,
        combiner_normalization_reason=normalization_reason,
    )
    summary_suffixes: list[str] = []
    if repeated_recovery and final_directive == GovernanceDirective.REPLAN:
        summary_suffixes.append(
            "Repeated recovery on the same step triggered replanning to avoid a loop."
        )
    if repeated_provider_failure and final_directive == GovernanceDirective.WAIT_HUMAN_REVIEW:
        summary_suffixes.append(
            "Repeated provider runtime failures on the same step escalated to human review to avoid a replan loop."
        )
    if _provider_runtime_action_missing(policy_input=policy_input) and not repeated_provider_failure:
        summary_suffixes.append(
            "The actor provider returned no usable runtime action, so the runtime treated the event as recovery and replanned."
        )
    if summary_suffixes:
        combined_summary = f"{combined_summary} {' '.join(summary_suffixes)}"
    return GovernanceDecisionTrace(
        directive=final_directive,
        policy_decision=static_policy_assessment.policy_decision,
        reason_codes=static_policy_assessment.reason_codes,
        summary=combined_summary,
        constrains_capability_use=constrains_capability_use(
            policy_input=policy_input,
            static_policy_assessment=static_policy_assessment,
        )
        or final_directive != static_policy_assessment.policy_directive,
        evaluated_actor_result_type=policy_input.actor_result_type,
        evaluated_tool_name=policy_input.tool_name,
        policy_engine_name=static_policy_assessment.policy_engine_name,
        policy_bundle_version=static_policy_assessment.policy_bundle_version,
        policy_directive=static_policy_assessment.policy_directive,
        oversight_recommended_directive=dynamic_directive,
        behavioral_recommended_directive=behavioral_directive,
        judge_recommended_directive=judge_directive,
        judge_invoked=judge_assessment.invoked if judge_assessment is not None else False,
        static_policy_summary=static_policy_assessment.summary,
        dynamic_policy_summary=(
            dynamic_policy_assessment.summary if dynamic_policy_assessment is not None else None
        ),
        behavioral_summary=behavioral_assessment.summary if behavioral_assessment is not None else None,
        judge_summary=judge_assessment.summary if judge_assessment is not None else None,
        behavioral_similarity_score=(
            behavioral_assessment.historical_similarity_score
            if behavioral_assessment is not None
            else None
        ),
        behavioral_deviation_score=(
            behavioral_assessment.behavior_deviation_score
            if behavioral_assessment is not None
            else None
        ),
        behavioral_novelty_score=(
            behavioral_assessment.novelty_score if behavioral_assessment is not None else None
        ),
        oversight_summary=(
            dynamic_policy_assessment.summary if dynamic_policy_assessment is not None else None
        ),
        combiner_dominant_dimension=combiner_result.dominant_dimension,
        combiner_support_by_directive={
            directive.value: round(score, 4)
            for directive, score in combiner_result.support_by_directive.items()
        },
        combiner_selected_reason=combiner_result.selected_reason,
        combiner_rejected_alternatives={
            directive.value: reason
            for directive, reason in combiner_result.rejected_alternatives.items()
        },
        combiner_safety_invariants_checked=combiner_result.safety_invariants_checked,
        combiner_signals=tuple(
            {
                "source": signal.source,
                "dimension": signal.dimension,
                "recommended_directive": signal.recommended_directive.value,
                "severity": round(signal.severity, 4),
                "confidence": round(signal.confidence, 4),
                "recoverable_by": tuple(directive.value for directive in signal.recoverable_by),
                "rationale": signal.rationale,
            }
            for signal in combiner_result.signals
        ),
        combiner_normalized_directive=(
            combiner_directive.value if final_directive != combiner_directive else None
        ),
        combiner_normalization_reason=normalization_reason,
    )


# Keep governance observable on every turn without opening HITL for recoverable intermediate work.
def _normalize_intermediate_output_directive(
    *,
    task_state: TaskState,
    policy_input: PolicyEvaluationInput,
    static_policy_assessment: StaticPolicyAssessment,
    final_directive: GovernanceDirective,
) -> GovernanceDirective:
    if static_policy_assessment.policy_decision in {
        PolicyDecision.DENY,
        PolicyDecision.REQUIRE_HUMAN_REVIEW,
        PolicyDecision.ASK_CLARIFICATION,
    }:
        return final_directive

    if policy_input.actor_result_type != ActorResultType.INTERMEDIATE_OUTPUT:
        return final_directive

    if policy_input.external_impact or policy_input.capability_mode == CapabilityAccessMode.WRITE:
        return final_directive

    if final_directive != GovernanceDirective.WAIT_HUMAN_REVIEW:
        return final_directive

    validation_action = _resolve_validation_action_for_directive_normalization(task_state=task_state)
    if validation_action == StepValidationDecision.CONTINUE_ACTING:
        return GovernanceDirective.CONTINUE
    if validation_action == StepValidationDecision.REQUEST_MORE_EVIDENCE:
        return GovernanceDirective.REQUEST_MORE_EVIDENCE
    if validation_action == StepValidationDecision.NEEDS_REPLAN:
        return GovernanceDirective.REPLAN
    if (
        validation_action == StepValidationDecision.COMPLETE_STEP
        and task_state.current_state in {RuntimeState.READY_NEXT_STEP, RuntimeState.COMPLETED}
    ):
        return GovernanceDirective.CONTINUE
    return final_directive


# Keep post-combiner normalization explicit in summaries and traces.
def _build_combiner_normalization_reason(
    *,
    task_state: TaskState,
    policy_input: PolicyEvaluationInput,
    combiner_directive: GovernanceDirective,
    final_directive: GovernanceDirective,
) -> str | None:
    if final_directive == combiner_directive:
        return None

    validation_action = _resolve_validation_action_for_directive_normalization(task_state=task_state)
    validation_text = (
        f"Typed step validation selected '{validation_action.value}'."
        if validation_action is not None
        else "Typed step validation exposed an automatic recovery path."
    )
    return (
        "Post-combiner normalization changed the directive from "
        f"'{combiner_directive.value}' to '{final_directive.value}' because the actor output "
        "was intermediate, non-external, non-write, and "
        f"{validation_text}"
    )


def _resolve_validation_action_for_directive_normalization(
    *,
    task_state: TaskState,
) -> StepValidationDecision | None:
    actor_result = task_state.actor_result
    if actor_result is None:
        return None
    if actor_result.result_type not in {
        ActorResultType.INTERMEDIATE_OUTPUT,
        ActorResultType.FINAL_OUTPUT,
    }:
        return None
    if actor_result.step_validation is None:
        return None
    return actor_result.step_validation.next_action


# Treat positive typed completion validation as explicit evidence that recovery can stop.
def _validation_allows_completion(*, task_state: TaskState) -> bool:
    actor_result = task_state.actor_result
    if actor_result is None or actor_result.step_validation is None:
        return False
    validation = actor_result.step_validation
    return (
        validation.next_action == StepValidationDecision.COMPLETE_STEP
        and validation.objective_satisfied
        and validation.evidence_sufficient
    )


# Treat provider protocol failures as runtime recovery, not operator review.
def _provider_runtime_action_missing(*, policy_input: PolicyEvaluationInput) -> bool:
    if policy_input.actor_result_type != ActorResultType.BLOCKED:
        return False
    if policy_input.actor_has_output_text or policy_input.tool_name is not None:
        return False
    return (
        policy_input.blocking_reason is not None
        and "Actor provider returned no usable runtime action" in policy_input.blocking_reason
    )


# Escalate repeated same-step recovery attempts so the runtime does not spin on evidence requests.
def _repeated_recovery_attempt_detected(*, task_state: TaskState) -> bool:
    current_step = task_state.current_plan_step
    actor_result = task_state.actor_result
    if current_step is None or actor_result is None:
        return False
    if actor_result.result_type not in {
        ActorResultType.INTERMEDIATE_OUTPUT,
        ActorResultType.FINAL_OUTPUT,
    }:
        return False

    repeated_request_count = sum(
        1
        for validation in task_state.step_validations
        if validation.plan_step_id == current_step.step_id
        and validation.next_action == StepValidationDecision.REQUEST_MORE_EVIDENCE
    )
    return repeated_request_count >= 2


# Detect repeated provider failures on the same step so the runtime can stop a replan loop.
def _repeated_provider_runtime_failure_detected(*, task_state: TaskState) -> bool:
    current_step = task_state.current_plan_step
    if current_step is None:
        return False

    repeated_failure_count = sum(
        1
        for entry in task_state.scratchpad
        if entry.plan_step_id == current_step.step_id
        and entry.entry_type == ScratchpadEntryType.UNCERTAINTY
        and entry.summary.startswith("Actor provider returned no usable runtime action")
    )
    return repeated_failure_count >= 2


# Keep the old public builder alive while internally delegating to the new layered combiner.
def build_governance_decision(
    *,
    task_state: TaskState | None = None,
    policy_input: PolicyEvaluationInput,
    policy_result: PolicyEvaluationResult,
    oversight_assessment: DynamicPolicyAssessment | None = None,
    behavioral_assessment: BehavioralAssessment | None = None,
    judge_assessment: JudgeAssessment | None = None,
) -> GovernanceDecisionTrace:
    from .decision_combiner import combine_decision_signals

    static_policy_assessment = StaticPolicyAssessment(
        policy_decision=policy_result.policy_decision,
        reason_codes=policy_result.reason_codes,
        summary=policy_result.summary,
        policy_engine_name=policy_result.policy_engine_name,
        policy_bundle_version=policy_result.policy_bundle_version,
        policy_directive=derive_governance_directive(
            policy_input=policy_input,
            policy_result=policy_result,
        ),
    )
    if task_state is not None:
        return combine_governance_assessments(
            task_state=task_state,
            policy_input=policy_input,
            static_policy_assessment=static_policy_assessment,
            dynamic_policy_assessment=oversight_assessment,
            behavioral_assessment=behavioral_assessment,
            judge_assessment=judge_assessment,
        )

    combiner_result = combine_decision_signals(
        static_policy_assessment=static_policy_assessment,
        dynamic_policy_assessment=oversight_assessment,
        behavioral_assessment=behavioral_assessment,
        judge_assessment=judge_assessment,
        external_impact=policy_input.external_impact,
        validation_allows_completion=False,
    )
    final_directive = combiner_result.final_directive
    return GovernanceDecisionTrace(
        directive=final_directive,
        policy_decision=static_policy_assessment.policy_decision,
        reason_codes=static_policy_assessment.reason_codes,
        summary=_build_combined_summary(
            static_policy_assessment=static_policy_assessment,
            final_directive=final_directive,
            dynamic_policy_assessment=oversight_assessment,
            behavioral_assessment=behavioral_assessment,
            judge_assessment=judge_assessment,
            combiner_selected_reason=combiner_result.selected_reason,
            combiner_directive=combiner_result.final_directive,
            combiner_normalization_reason=None,
        ),
        constrains_capability_use=constrains_capability_use(
            policy_input=policy_input,
            static_policy_assessment=static_policy_assessment,
        )
        or final_directive != static_policy_assessment.policy_directive,
        evaluated_actor_result_type=policy_input.actor_result_type,
        evaluated_tool_name=policy_input.tool_name,
        policy_engine_name=static_policy_assessment.policy_engine_name,
        policy_bundle_version=static_policy_assessment.policy_bundle_version,
        policy_directive=static_policy_assessment.policy_directive,
        oversight_recommended_directive=_map_dynamic_directive(
            oversight_assessment.recommended_directive
            if oversight_assessment is not None
            else DynamicPolicyRecommendedDirective.CONTINUE
        ),
        behavioral_recommended_directive=_map_behavioral_directive(
            behavioral_assessment.behavioral_recommended_directive
            if behavioral_assessment is not None
            else BehavioralRecommendedDirective.CONTINUE
        ),
        judge_recommended_directive=_map_judge_directive(
            judge_assessment.recommendation
            if judge_assessment is not None
            else JudgeRecommendedDirective.CONTINUE
        ),
        judge_invoked=judge_assessment.invoked if judge_assessment is not None else False,
        static_policy_summary=static_policy_assessment.summary,
        dynamic_policy_summary=oversight_assessment.summary if oversight_assessment is not None else None,
        behavioral_summary=behavioral_assessment.summary if behavioral_assessment is not None else None,
        judge_summary=judge_assessment.summary if judge_assessment is not None else None,
        behavioral_similarity_score=(
            behavioral_assessment.historical_similarity_score
            if behavioral_assessment is not None
            else None
        ),
        behavioral_deviation_score=(
            behavioral_assessment.behavior_deviation_score
            if behavioral_assessment is not None
            else None
        ),
        behavioral_novelty_score=(
            behavioral_assessment.novelty_score if behavioral_assessment is not None else None
        ),
        oversight_summary=oversight_assessment.summary if oversight_assessment is not None else None,
        combiner_dominant_dimension=combiner_result.dominant_dimension,
        combiner_support_by_directive={
            directive.value: round(score, 4)
            for directive, score in combiner_result.support_by_directive.items()
        },
        combiner_selected_reason=combiner_result.selected_reason,
        combiner_rejected_alternatives={
            directive.value: reason
            for directive, reason in combiner_result.rejected_alternatives.items()
        },
        combiner_safety_invariants_checked=combiner_result.safety_invariants_checked,
        combiner_signals=tuple(
            {
                "source": signal.source,
                "dimension": signal.dimension,
                "recommended_directive": signal.recommended_directive.value,
                "severity": round(signal.severity, 4),
                "confidence": round(signal.confidence, 4),
                "recoverable_by": tuple(directive.value for directive in signal.recoverable_by),
                "rationale": signal.rationale,
            }
            for signal in combiner_result.signals
        ),
        combiner_normalized_directive=None,
        combiner_normalization_reason=None,
    )


# Combine hard policy with adaptive dynamic policy without making policy more permissive.
def combine_governance_directives(
    *,
    static_policy_assessment: StaticPolicyAssessment | None = None,
    dynamic_policy_assessment: DynamicPolicyAssessment | None = None,
    behavioral_assessment: BehavioralAssessment | None = None,
    judge_assessment: JudgeAssessment | None = None,
    policy_result: PolicyEvaluationResult | None = None,
    policy_directive: GovernanceDirective | None = None,
    oversight_assessment: DynamicPolicyAssessment | None = None,
) -> GovernanceDirective:
    from .decision_combiner import combine_decision_signals

    # Keep backward compatibility for older callers while routing through the new model.
    resolved_static_policy = static_policy_assessment
    if resolved_static_policy is None and policy_result is not None and policy_directive is not None:
        resolved_static_policy = StaticPolicyAssessment(
            policy_decision=policy_result.policy_decision,
            reason_codes=policy_result.reason_codes,
            summary=policy_result.summary,
            policy_engine_name=policy_result.policy_engine_name,
            policy_bundle_version=policy_result.policy_bundle_version,
            policy_directive=policy_directive,
        )
    if resolved_static_policy is None:
        raise ValueError("combine_governance_directives requires static policy information.")

    resolved_dynamic_policy = dynamic_policy_assessment or oversight_assessment
    return combine_decision_signals(
        static_policy_assessment=resolved_static_policy,
        dynamic_policy_assessment=resolved_dynamic_policy,
        behavioral_assessment=behavioral_assessment,
        judge_assessment=judge_assessment,
    ).final_directive


# Convert the dynamic recommendation into the governance directive enum.
def _map_dynamic_directive(
    recommended_directive: DynamicPolicyRecommendedDirective,
) -> GovernanceDirective:
    if recommended_directive == DynamicPolicyRecommendedDirective.WAIT_HUMAN_REVIEW:
        return GovernanceDirective.WAIT_HUMAN_REVIEW
    if recommended_directive == DynamicPolicyRecommendedDirective.REPLAN:
        return GovernanceDirective.REPLAN
    if recommended_directive == DynamicPolicyRecommendedDirective.REQUEST_MORE_EVIDENCE:
        return GovernanceDirective.REQUEST_MORE_EVIDENCE
    if recommended_directive == DynamicPolicyRecommendedDirective.CONTINUE_READ_ONLY:
        return GovernanceDirective.CONTINUE_READ_ONLY
    return GovernanceDirective.CONTINUE


# Convert the behavioural recommendation into the governance directive enum.
def _map_behavioral_directive(
    recommended_directive: BehavioralRecommendedDirective,
) -> GovernanceDirective:
    if recommended_directive == BehavioralRecommendedDirective.WAIT_HUMAN_REVIEW:
        return GovernanceDirective.WAIT_HUMAN_REVIEW
    if recommended_directive == BehavioralRecommendedDirective.REPLAN:
        return GovernanceDirective.REPLAN
    if recommended_directive == BehavioralRecommendedDirective.REQUEST_MORE_EVIDENCE:
        return GovernanceDirective.REQUEST_MORE_EVIDENCE
    return GovernanceDirective.CONTINUE


# Convert the judge recommendation into the governance directive enum.
def _map_judge_directive(
    recommended_directive: JudgeRecommendedDirective,
) -> GovernanceDirective:
    if recommended_directive == JudgeRecommendedDirective.WAIT_HUMAN_REVIEW:
        return GovernanceDirective.WAIT_HUMAN_REVIEW
    if recommended_directive == JudgeRecommendedDirective.REPLAN:
        return GovernanceDirective.REPLAN
    if recommended_directive == JudgeRecommendedDirective.REQUEST_MORE_EVIDENCE:
        return GovernanceDirective.REQUEST_MORE_EVIDENCE
    return GovernanceDirective.CONTINUE


# Order runtime directives by conservativeness while preserving read-only static policy as non-escalating.
def _conservativeness_rank(directive: GovernanceDirective) -> int:
    ordering = {
        GovernanceDirective.CONTINUE: 0,
        GovernanceDirective.CONTINUE_READ_ONLY: 0,
        GovernanceDirective.REQUEST_MORE_EVIDENCE: 1,
        GovernanceDirective.REPLAN: 2,
        GovernanceDirective.WAIT_HUMAN_REVIEW: 3,
        GovernanceDirective.ASK_USER_CLARIFICATION: 4,
        GovernanceDirective.STOP: 5,
    }
    return ordering[directive]


# Summarize how the layered governance stack produced the final directive.
def _build_combined_summary(
    *,
    static_policy_assessment: StaticPolicyAssessment,
    final_directive: GovernanceDirective,
    dynamic_policy_assessment: DynamicPolicyAssessment | None,
    behavioral_assessment: BehavioralAssessment | None,
    judge_assessment: JudgeAssessment | None = None,
    combiner_selected_reason: str | None = None,
    combiner_directive: GovernanceDirective | None = None,
    combiner_normalization_reason: str | None = None,
) -> str:
    summary_parts = [static_policy_assessment.summary]
    if dynamic_policy_assessment is not None:
        effective_combiner_directive = combiner_directive or final_directive
        if effective_combiner_directive == static_policy_assessment.policy_directive:
            summary_parts.append(
                "Dynamic policy preserved the static directive "
                f"'{static_policy_assessment.policy_directive.value}'."
            )
        else:
            summary_parts.append(
                "Dynamic policy made the decision more conservative: "
                f"'{static_policy_assessment.policy_directive.value}' became "
                f"'{effective_combiner_directive.value}'."
            )
        summary_parts.append(dynamic_policy_assessment.summary)
    if behavioral_assessment is not None:
        summary_parts.append(behavioral_assessment.summary)
    if judge_assessment is not None and judge_assessment.summary:
        summary_parts.append(judge_assessment.summary)
    if combiner_selected_reason:
        summary_parts.append(f"Combiner rationale: {combiner_selected_reason}")
    if combiner_normalization_reason:
        summary_parts.append(combiner_normalization_reason)
    return " ".join(summary_parts)


# Execute the policy evaluation against OPA over HTTP.
class OPAHttpPolicyEngine(PolicyEnginePort):
    policy_engine_name = "opa_http_policy_engine"
    policy_bundle_version = "layer2_governance_v2"
    policy_config_version = "layer2_governance_thresholds_v1"

    def __init__(self, settings: OPAHttpPolicyEngineSettings | None = None) -> None:
        # Resolve settings once so the runtime stays deterministic.
        self._settings = settings or OPAHttpPolicyEngineSettings.from_env()

    # Preserve the v1 API for callers during this release; the runtime never invokes it.
    def evaluate(self, *, policy_input: PolicyEvaluationInput) -> PolicyEvaluationResult:
        warnings.warn("OPAHttpPolicyEngine.evaluate is deprecated; use evaluate_decision.", DeprecationWarning, stacklevel=2)
        result = self._post_json(
            path=self._settings.decision_path,
            payload={"input": build_opa_input_document(policy_input=policy_input)},
        )
        if not isinstance(result, dict):
            raise PolicyEngineExecutionError("OPA response must be an object.")
        return PolicyEvaluationResult(
            policy_decision=_parse_policy_decision(result.get("policy_decision")),
            reason_codes=_parse_reason_codes(result.get("reason_codes")),
            summary=_parse_non_empty_text(result.get("summary"), field_name="summary"),
            policy_engine_name=self.policy_engine_name,
            policy_bundle_version=self.policy_bundle_version,
        )

    # Ask OPA which optional signal pillars are required before invoking the judge.
    def evaluate_required_pillars(self, *, base_facts: dict[str, Any]) -> dict[str, bool]:
        result = self._post_json(
            path="/v1/data/layer2/governance/required_pillars",
            payload={"input": {
                "policy_bundle_version": self.policy_bundle_version,
                "policy_config_version": self.policy_config_version,
                "base_facts": base_facts,
            }},
        )
        if not isinstance(result, dict):
            raise PolicyEngineExecutionError("OPA required_pillars response must be an object.")
        return {
            "require_evidence": bool(result.get("require_evidence", True)),
            "require_risk": bool(result.get("require_risk", True)),
            "require_trajectory": bool(result.get("require_trajectory", True)),
            "require_behavioral": bool(result.get("require_behavioral", True)),
            "require_judge": bool(result.get("require_judge", False)),
        }

    # Ask OPA for the final governance decision from versioned base facts and pillar signals.
    def evaluate_decision(self, *, decision_input: dict[str, Any]) -> dict[str, Any]:
        result = self._post_json(
            path="/v1/data/layer2/governance/decision",
            payload={"input": decision_input},
        )
        if not isinstance(result, dict):
            raise PolicyEngineExecutionError("OPA decision response must be an object.")
        return result

    # Execute one JSON POST request against OPA and return the `result` envelope.
    def _post_json(self, *, path: str, payload: dict[str, Any]) -> Any:
        request = Request(
            self._settings.base_url.rstrip("/") + "/" + path.lstrip("/"),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._settings.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:  # pragma: no cover
            raise PolicyEngineExecutionError(
                f"OPA policy request failed with HTTP {exc.code}: {exc.reason}"
            ) from exc
        except URLError as exc:  # pragma: no cover
            raise PolicyEngineExecutionError(f"OPA policy request failed: {exc.reason}") from exc
        except Exception as exc:  # pragma: no cover
            raise PolicyEngineExecutionError(f"OPA policy request failed: {exc}") from exc

        try:
            parsed_body = json.loads(raw_body)
        except json.JSONDecodeError as exc:  # pragma: no cover
            raise PolicyEngineExecutionError(f"OPA returned invalid JSON: {exc}") from exc
        if "result" not in parsed_body:
            raise PolicyEngineExecutionError("OPA response did not contain a result field.")
        return parsed_body["result"]

    # Resolve the OPA decision endpoint from the configured base URL and path.
    def _resolve_url(self) -> str:
        return self._settings.base_url.rstrip("/") + "/" + self._settings.decision_path.lstrip("/")


# Build the production policy engine used by the runtime.
def build_default_policy_engine() -> PolicyEnginePort:
    return OPAHttpPolicyEngine()


# Run the OPA-first governance flow and return all records needed by TaskState.
def evaluate_opa_first_governance(
    *,
    task_state: TaskState,
    policy_engine_service: PolicyEnginePort,
    judge_service: JudgePort | None = None,
    behavioral_store_service: BehavioralStorePort | None = None,
    thread_id: str | None = None,
) -> GovernanceEvaluationResult:
    from .behavioral import build_behavioral_assessment
    from .governance_input_builder import (
        build_governance_base_facts,
        build_governance_decision_input,
        validate_governance_decision_input,
    )
    from .judge import build_default_judge
    from .signal_services import (
        build_behavioral_signal,
        build_evidence_signal,
        build_judge_signal,
        build_risk_signal,
        build_trajectory_signal,
    )

    policy_input = build_policy_evaluation_input(task_state=task_state)
    # OPA owns configuration; the PEP only declares the compatible bundle versions.
    policy_bundle_version = str(policy_engine_service.policy_bundle_version)
    policy_config_version = str(getattr(policy_engine_service, "policy_config_version", "layer2_governance_thresholds_v1"))
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
    dynamic_assessment = _build_dynamic_assessment_from_signals(
        pillars=raw_output.get("pillar_results", pillars),
        directive=governance_decision.oversight_recommended_directive,
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


# Use OPA's required-pillar endpoint. Missing support is a fail-closed engine failure.
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


# Request the final decision only from the OPA PDP; no Python policy adapter is reachable here.
def _evaluate_governance_decision_output(
    *,
    policy_engine_service: PolicyEnginePort,
    decision_input: dict[str, Any],
) -> dict[str, Any]:
    result = policy_engine_service.evaluate_decision(decision_input=decision_input)
    if not isinstance(result, dict):
        raise PolicyEngineExecutionError("OPA decision response must be an object.")
    return result


# Validate the OPA output shape and convert it into the canonical trace dataclass.
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
        directive=directive, policy_decision=policy_decision, reason_codes=reason_codes,
        summary=selected_reason, constrains_capability_use=directive != GovernanceDirective.CONTINUE,
        evaluated_actor_result_type=policy_input.actor_result_type, evaluated_tool_name=policy_input.tool_name,
        policy_engine_name=policy_engine_service.policy_engine_name,
        policy_bundle_version=str(raw_output.get("policy_bundle_version") or policy_engine_service.policy_bundle_version),
        policy_directive=policy_directive, oversight_recommended_directive=_map_opa_directive_for_trace(directive),
        behavioral_recommended_directive=_map_behavioral_directive(behavioral_assessment.behavioral_recommended_directive),
        judge_recommended_directive=_map_judge_directive(judge_assessment.recommendation), judge_invoked=judge_assessment.invoked,
        static_policy_summary=_static_policy_summary_from_pillar(pillar_results.get("static_policy", {})),
        dynamic_policy_summary=dynamic_policy_assessment.summary, behavioral_summary=behavioral_assessment.summary,
        judge_summary=judge_assessment.summary, behavioral_similarity_score=behavioral_assessment.historical_similarity_score,
        behavioral_deviation_score=behavioral_assessment.behavior_deviation_score, behavioral_novelty_score=behavioral_assessment.novelty_score,
        oversight_summary=dynamic_policy_assessment.summary, combiner_dominant_dimension=None, combiner_support_by_directive=None,
        combiner_selected_reason=selected_reason,
        combiner_rejected_alternatives={str(key): str(value) for key, value in raw_output["rejected_alternatives"].items()},
        combiner_safety_invariants_checked=tuple(str(item) for item in raw_output["safety_invariants_checked"]),
        combiner_signals=tuple(_pillar_result_signals(pillar_results=pillar_results)),
        combiner_normalized_directive=None, combiner_normalization_reason=None,
        governance_input_schema_version=(str(decision_input.get("schema_version")) if isinstance(decision_input, dict) else None),
        pillar_requirements=({str(key): bool(value) for key, value in decision_input.get("pillar_requirements", {}).items()} if isinstance(decision_input, dict) else None),
        pillar_results=pillar_results,
        pillar_errors=({str(key): str(value) for key, value in decision_input.get("pillar_errors", {}).items()} if isinstance(decision_input, dict) else None),
        policy_config_version=str(raw_output.get("policy_config_version")),
    )


# Validate the minimal GovernanceDecisionOutput contract before applying a directive.
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


# Convert OPA/static-pillar output into the legacy static assessment stored in TaskState.
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
        policy_bundle_version=str(raw_output.get("policy_bundle_version") or policy_engine_service.policy_bundle_version),
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


# Build a static assessment from base facts so judge prompts can still receive the static envelope.
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


# Supply a compatibility-shaped judge context without evaluating static policy in Python.
def _build_judge_static_context(*, policy_engine_service: PolicyEnginePort) -> StaticPolicyAssessment:
    return StaticPolicyAssessment(
        policy_decision=PolicyDecision.ALLOW,
        reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
        summary="Static-policy facts are supplied to OPA; Python does not interpret them before judging.",
        policy_engine_name=policy_engine_service.policy_engine_name,
        policy_bundle_version=policy_engine_service.policy_bundle_version,
        policy_directive=GovernanceDirective.CONTINUE,
    )


# Build legacy dynamic assessment records from pillar facts for trace/dashboard compatibility.
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


# Build an audit-safe fail-closed result when OPA cannot produce a valid decision.
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
    from .governance_input_builder import build_governance_decision_input

    resolved_requirements = pillar_requirements or {
        "require_evidence": True,
        "require_risk": True,
        "require_trajectory": True,
        "require_behavioral": False,
        "require_judge": False,
    }
    resolved_pillars = pillars or {}
    thresholds_version = policy_config_version
    for name in ("evidence", "risk", "trajectory", "behavioral", "judge"):
        resolved_pillars.setdefault(name, _unavailable_pillar(name, thresholds_version))
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
        directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
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
        behavioral_recommended_directive=_map_behavioral_directive(behavioral.behavioral_recommended_directive),
        judge_recommended_directive=_map_judge_directive(judge.recommendation),
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


# Find the plan step that the actor result was evaluated against.
def _resolve_evaluated_plan_step(
    *,
    task_state: TaskState,
    actor_result: ActorResult,
):
    for step in task_state.plan_steps:
        if step.step_id == actor_result.plan_step_id:
            return step
    return None


# Convert legacy PolicyEnginePort outputs into the OPA v2 output envelope for test adapters.
def _legacy_policy_result_to_decision_output(
    *,
    policy_result: PolicyEvaluationResult,
    policy_input: PolicyEvaluationInput,
    decision_input: dict[str, Any],
) -> dict[str, Any]:
    directive = derive_governance_directive(
        policy_input=policy_input,
        policy_result=policy_result,
    )
    selected_reason = policy_result.summary
    reason_codes = [code.value for code in policy_result.reason_codes] or [
        GovernanceReasonCode.ALLOWED_BY_POLICY.value
    ]
    pillars = decision_input["pillars"]
    trajectory_facts = pillars.get("trajectory", {}).get("raw_facts", {})
    evidence_facts = pillars.get("evidence", {}).get("raw_facts", {})
    judge_facts = pillars.get("judge", {}).get("raw_facts", {})
    if policy_result.policy_decision not in {
        PolicyDecision.DENY,
        PolicyDecision.REQUIRE_HUMAN_REVIEW,
        PolicyDecision.ASK_CLARIFICATION,
    }:
        if trajectory_facts.get("provider_runtime_action_missing"):
            directive = GovernanceDirective.REPLAN
            reason_codes = [GovernanceReasonCode.REPEATED_RECOVERY.value]
            selected_reason = "Provider returned no usable runtime action; replanning is the minimal recovery."
        elif trajectory_facts.get("needs_replan_signal"):
            directive = GovernanceDirective.REPLAN
            reason_codes = [GovernanceReasonCode.TRAJECTORY_DEGRADED.value]
            selected_reason = "Actor signaled that replanning is required."
        elif (
            judge_facts.get("verdict") in {"reject_output", "flag"}
            and float(judge_facts.get("confidence") or 0.0) >= 0.7
        ):
            directive = GovernanceDirective.REQUEST_MORE_EVIDENCE
            reason_codes = [GovernanceReasonCode.JUDGE_REJECT_OUTPUT.value]
            selected_reason = "Judge found the output fragile enough to require more evidence."
        elif evidence_facts.get("support_rank_available", 0) < evidence_facts.get("support_rank_required", 0):
            directive = GovernanceDirective.REQUEST_MORE_EVIDENCE
            reason_codes = [GovernanceReasonCode.EVIDENCE_SUPPORT_BELOW_STEP_NEED.value]
            selected_reason = "Evidence support is below the current step requirement."
    pillar_results = {
        "static_policy": {
            "write_blocked": policy_result.policy_decision == PolicyDecision.DENY,
            "read_only_required": policy_result.policy_decision == PolicyDecision.ALLOW_READ_ONLY,
            "hitl_mandatory": policy_result.policy_decision == PolicyDecision.REQUIRE_HUMAN_REVIEW,
            "clarification_signaled": policy_result.policy_decision == PolicyDecision.ASK_CLARIFICATION,
            "reason_codes": [code.value for code in policy_result.reason_codes],
        },
        "evidence": decision_input["pillars"].get("evidence", {}),
        "risk": decision_input["pillars"].get("risk", {}),
        "trajectory": decision_input["pillars"].get("trajectory", {}),
        "behavioral": decision_input["pillars"].get("behavioral", {}),
        "judge": decision_input["pillars"].get("judge", {}),
    }
    return {
        "directive": directive.value,
        "policy_decision": policy_result.policy_decision.value,
        "reason_codes": reason_codes,
        "pillar_results": pillar_results,
        "selected_reason": selected_reason,
        "rejected_alternatives": {},
        "safety_invariants_checked": [
            "deny_is_absorbing",
            "hitl_mandatory_not_downgraded",
            "read_only_envelope_preserved",
            "behavioral_cannot_escalate_alone",
            "judge_cannot_hard_deny",
        ],
        "requires_human_review": directive == GovernanceDirective.WAIT_HUMAN_REVIEW,
        "policy_bundle_version": policy_result.policy_bundle_version,
    }


# Parse OPA directives while mapping external `deny` to the runtime stop directive.
def _parse_governance_directive(value: Any) -> GovernanceDirective:
    if value == "deny":
        return GovernanceDirective.STOP
    if not isinstance(value, str):
        raise PolicyEngineExecutionError("OPA directive must be a string.")
    try:
        return GovernanceDirective(value)
    except ValueError as exc:
        raise PolicyEngineExecutionError(f"OPA returned an unknown directive: {value}") from exc


# Resolve static-policy envelope from OPA's static pillar result.
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


# Summarize the static pillar without inventing policy semantics.
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


# Convert pillar results into compact trace signals for dashboard/debug visibility.
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


# Convert final OPA directives into the trace-facing dynamic directive enum.
def _map_opa_directive_for_trace(directive: GovernanceDirective) -> GovernanceDirective:
    if directive in {
        GovernanceDirective.REQUEST_MORE_EVIDENCE,
        GovernanceDirective.REPLAN,
        GovernanceDirective.WAIT_HUMAN_REVIEW,
        GovernanceDirective.CONTINUE_READ_ONLY,
    }:
        return directive
    return GovernanceDirective.CONTINUE


# Map governance directives into the legacy dynamic-policy recommendation enum.
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


# Parse oversight signal levels from JSON while keeping a conservative default.
def _parse_oversight_level(value: Any, default: OversightSignalLevel) -> OversightSignalLevel:
    if not isinstance(value, str):
        return default
    try:
        return OversightSignalLevel(value)
    except ValueError:
        return default


# Parse oversight reason codes and ignore unknown future policy facts.
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


# Merge oversight reason-code groups in first-seen order.
def _merge_oversight_reason_codes(
    *groups: tuple[OversightReasonCode, ...],
) -> tuple[OversightReasonCode, ...]:
    merged: list[OversightReasonCode] = []
    for group in groups:
        for code in group:
            if code not in merged:
                merged.append(code)
    return tuple(merged)


# Build a pillar payload when a local signal service is unavailable.
def _unavailable_pillar(name: str, thresholds_version: str) -> dict[str, Any]:
    return {
        "raw_facts": {"pillar_unavailable": True, "pillar_name": name},
        "diagnostic_score": 0.0,
        "diagnostic_level": "weak" if name == "evidence" else "low",
        "score_components": {},
        "reason_facts": ["pillar_unavailable"],
        "summary": f"{name} pillar unavailable.",
        "score_formula_version": f"{name}_unavailable",
        "thresholds_version": thresholds_version,
    }


# Build a typed behavioral assessment for failure facts without affecting policy.
def _unavailable_behavioral_assessment(reason: str) -> BehavioralAssessment:
    return BehavioralAssessment(
        history_available=False,
        historical_run_count=0,
        feature_snapshot_version="behavioral_unavailable",
        summary=f"Behavioral pillar unavailable: {reason}",
        notes=("behavioral_unavailable",),
    )


# Build a typed judge assessment when OPA did not require judge invocation.
def _not_required_judge_assessment() -> JudgeAssessment:
    return JudgeAssessment(
        invoked=False,
        recommendation=JudgeRecommendedDirective.CONTINUE,
        confidence=0.0,
        summary="Judge not required by OPA required_pillars.",
        rationale="The first OPA pass did not request a semantic judge assessment.",
        flags=(),
        evaluated_target="not_required",
    )


# Build a typed judge assessment when the judge backend fails.
def _unavailable_judge_assessment(reason: str) -> JudgeAssessment:
    return JudgeAssessment(
        invoked=False,
        recommendation=JudgeRecommendedDirective.CONTINUE,
        confidence=0.0,
        summary=f"Judge pillar unavailable: {reason}",
        rationale="The judge backend failed; the failure is passed to OPA as a fact.",
        flags=("judge_unavailable",),
        evaluated_target="unavailable",
    )


# Parse a policy decision from the OPA response body.
def _parse_policy_decision(value: Any) -> PolicyDecision:
    if not isinstance(value, str):
        raise PolicyEngineExecutionError("OPA policy decision must be a string.")
    try:
        return PolicyDecision(value)
    except ValueError as exc:
        raise PolicyEngineExecutionError(f"OPA returned an unknown policy decision: {value}") from exc


# Parse the reason codes while failing closed on unknown values.
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


# Parse a required non-empty text field from the OPA response.
def _parse_non_empty_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyEngineExecutionError(f"OPA response field '{field_name}' must be a non-empty string.")
    return " ".join(value.strip().split())


# Load simple KEY=VALUE pairs from the supported .env file locations.
def _load_env_file_values() -> dict[str, str]:
    env_values: dict[str, str] = {}
    for candidate in _candidate_env_paths():
        if not candidate.exists():
            continue
        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key:
                env_values[key] = value
    return env_values


# Search both repository root and src-level env files for local development.
def _candidate_env_paths() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[2]
    return [repo_root / ".env", repo_root / "src" / ".env"]


__all__ = [
    "GovernanceDecision",
    "GovernanceDecisionTrace",
    "GovernanceEvaluationResult",
    "GovernanceDirective",
    "GovernanceReasonCode",
    "OPAHttpPolicyEngine",
    "OPAHttpPolicyEngineSettings",
    "PolicyDecision",
    "PolicyEngineExecutionError",
    "PolicyEnginePort",
    "PolicyEvaluationInput",
    "PolicyEvaluationResult",
    "StaticPolicyAssessment",
    "build_default_policy_engine",
    "build_governance_decision",
    "build_opa_input_document",
    "build_policy_evaluation_input",
    "combine_governance_assessments",
    "combine_governance_directives",
    "constrains_capability_use",
    "derive_governance_directive",
    "evaluate_opa_first_governance",
    "evaluate_static_policy",
]
