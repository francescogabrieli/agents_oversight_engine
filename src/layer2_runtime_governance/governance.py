from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from layer1_intake import DomainType, SensitivityLevel

from .actor_contract import ActorResult, ActorResultType, CapabilityAccessMode
from .behavioral import BehavioralAssessment, BehavioralRecommendedDirective
from .judge import JudgeAssessment, JudgeRecommendedDirective
from .oversight import (
    DynamicPolicyAssessment,
    DynamicPolicyRecommendedDirective,
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


# Keep the legacy public name stable while the internal model becomes more explicit.
GovernanceDecision = GovernanceDecisionTrace


# PolicyEnginePort keeps the runtime decoupled from the policy engine backend.
class PolicyEnginePort(Protocol):
    policy_engine_name: str
    policy_bundle_version: str

    def evaluate(self, *, policy_input: PolicyEvaluationInput) -> PolicyEvaluationResult: ...


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
    if policy_input.actor_result_type == ActorResultType.PROPOSE_ACTION:
        if resolved_policy_decision == PolicyDecision.ALLOW_READ_ONLY:
            return GovernanceDirective.CONTINUE_READ_ONLY
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
    final_directive = combine_governance_directives(
        static_policy_assessment=static_policy_assessment,
        dynamic_policy_assessment=dynamic_policy_assessment,
        behavioral_assessment=behavioral_assessment,
        judge_assessment=judge_assessment,
    )
    repeated_recovery = _repeated_recovery_attempt_detected(task_state=task_state)
    repeated_provider_failure = _repeated_provider_runtime_failure_detected(task_state=task_state)
    if repeated_recovery and final_directive == GovernanceDirective.REQUEST_MORE_EVIDENCE:
        final_directive = GovernanceDirective.REPLAN
    if _provider_runtime_action_missing(policy_input=policy_input):
        final_directive = (
            GovernanceDirective.WAIT_HUMAN_REVIEW
            if repeated_provider_failure
            else GovernanceDirective.REPLAN
        )
    final_directive = _normalize_intermediate_output_directive(
        task_state=task_state,
        policy_input=policy_input,
        static_policy_assessment=static_policy_assessment,
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

    if policy_input.actor_result_type not in {
        ActorResultType.INTERMEDIATE_OUTPUT,
        ActorResultType.FINAL_OUTPUT,
    }:
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

    final_directive = combine_governance_directives(
        static_policy_assessment=static_policy_assessment,
        dynamic_policy_assessment=oversight_assessment,
        behavioral_assessment=behavioral_assessment,
        judge_assessment=judge_assessment,
    )
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
    if resolved_static_policy.policy_decision == PolicyDecision.DENY:
        return GovernanceDirective.STOP
    if resolved_static_policy.policy_decision == PolicyDecision.REQUIRE_HUMAN_REVIEW:
        return GovernanceDirective.WAIT_HUMAN_REVIEW
    if resolved_static_policy.policy_decision == PolicyDecision.ASK_CLARIFICATION:
        return GovernanceDirective.ASK_USER_CLARIFICATION
    candidate_directives = [resolved_static_policy.policy_directive]
    if resolved_dynamic_policy is not None:
        candidate_directives.append(
            _map_dynamic_directive(resolved_dynamic_policy.recommended_directive)
        )
    if behavioral_assessment is not None:
        candidate_directives.append(
            _map_behavioral_directive(behavioral_assessment.behavioral_recommended_directive)
        )
    if judge_assessment is not None:
        candidate_directives.append(_map_judge_directive(judge_assessment.recommendation))
    most_conservative = max(candidate_directives, key=_conservativeness_rank)
    if most_conservative in {
        GovernanceDirective.WAIT_HUMAN_REVIEW,
        GovernanceDirective.REPLAN,
        GovernanceDirective.REQUEST_MORE_EVIDENCE,
    }:
        return most_conservative
    return resolved_static_policy.policy_directive


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
) -> str:
    summary_parts = [static_policy_assessment.summary]
    if dynamic_policy_assessment is not None:
        if final_directive == static_policy_assessment.policy_directive:
            summary_parts.append(
                "Dynamic policy preserved the static directive "
                f"'{static_policy_assessment.policy_directive.value}'."
            )
        else:
            summary_parts.append(
                "Dynamic policy made the decision more conservative: "
                f"'{static_policy_assessment.policy_directive.value}' became '{final_directive.value}'."
            )
        summary_parts.append(dynamic_policy_assessment.summary)
    if behavioral_assessment is not None:
        summary_parts.append(behavioral_assessment.summary)
    if judge_assessment is not None and judge_assessment.summary:
        summary_parts.append(judge_assessment.summary)
    return " ".join(summary_parts)


# Execute the policy evaluation against OPA over HTTP.
class OPAHttpPolicyEngine(PolicyEnginePort):
    policy_engine_name = "opa_http_policy_engine"
    policy_bundle_version = "layer2_governance_v1"

    def __init__(self, settings: OPAHttpPolicyEngineSettings | None = None) -> None:
        # Resolve settings once so the runtime stays deterministic.
        self._settings = settings or OPAHttpPolicyEngineSettings.from_env()

    def evaluate(self, *, policy_input: PolicyEvaluationInput) -> PolicyEvaluationResult:
        payload = json.dumps(
            {"input": build_opa_input_document(policy_input=policy_input)},
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self._resolve_url(),
            data=payload,
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

        result = parsed_body.get("result")
        if not isinstance(result, dict):
            raise PolicyEngineExecutionError("OPA response did not contain a structured result.")
        return PolicyEvaluationResult(
            policy_decision=_parse_policy_decision(result.get("policy_decision")),
            reason_codes=_parse_reason_codes(result.get("reason_codes")),
            summary=_parse_non_empty_text(result.get("summary"), field_name="summary"),
            policy_engine_name=self.policy_engine_name,
            policy_bundle_version=self.policy_bundle_version,
        )

    # Resolve the OPA decision endpoint from the configured base URL and path.
    def _resolve_url(self) -> str:
        return self._settings.base_url.rstrip("/") + "/" + self._settings.decision_path.lstrip("/")


# Build the production policy engine used by the runtime.
def build_default_policy_engine() -> PolicyEnginePort:
    return OPAHttpPolicyEngine()


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
    "evaluate_static_policy",
]
