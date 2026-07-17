
from typing import Any

from layer1_intake import IntakeResult

from ..actor_contract import ActorResult
from ..behavioral import BehavioralAssessment
from ..governance_components import GovernanceDecision, StaticPolicyAssessment
from ..judge import JudgeAssessment
from ..oversight import (
    EvidenceQualityAssessment,
    OperationalRiskAssessment,
    OversightAssessment,
    _output_claim_support_level,
    _precision_mismatch_detected,
    resolve_required_evidence_support_context,
    TrajectoryHealthAssessment,
)
from .serialization import enum_value, to_jsonable
from ..domain import (
    ExecutedPlanStep,
    ExternalCommunicationDraft,
    ExternalCommunicationState,
    HumanReviewExternalEdit,
    normalize_task_state_serialization,
    ScratchpadEntry,
    StepRecord,
    StepValidation,
    TaskState,
)
from ..domain import HumanReviewDecision, HumanReviewRequest
from ..tool_contract import ObservedEvidence, ToolExecutionResult, ToolRequest, ToolSource


from .models import *


# Read-only observability component extracted from the trace facade.
def build_layer2_runtime_trace(
    thread_id: str,
    intake_result: IntakeResult,
    task_state: TaskState,
) -> Layer2RuntimeTrace:
    normalize_task_state_serialization(task_state)
    return Layer2RuntimeTrace(
        runtime=_build_runtime_summary(thread_id=thread_id, task_state=task_state),
        intake=_build_intake_summary(intake_result=intake_result),
        planner_name=task_state.planner_name,
        planner_prompt_version=task_state.planner_prompt_version,
        planner_rationale=task_state.planner_rationale,
        planning_source=_resolve_planning_source(task_state.planner_name),
        plan_steps=tuple(
            _build_plan_step(step=step, task_state=task_state) for step in task_state.plan_steps
        ),
        current_plan_step_index=task_state.current_plan_step_index,
        current_plan_step_id=(
            task_state.current_plan_step.step_id if task_state.current_plan_step is not None else None
        ),
        actor=_build_actor_turn(actor_result=task_state.actor_result, task_state=task_state),
        execution=_build_execution_summary(task_state=task_state),
        tool_calls=_build_tool_calls(task_state=task_state),
        observed_evidence=tuple(
            _build_observed_evidence(evidence) for evidence in task_state.observed_evidence
        ),
        static_policy_assessments=tuple(
            _build_static_policy_assessment(assessment) for assessment in task_state.static_policy_history
        ),
        dynamic_policy_assessments=tuple(
            _build_oversight_assessment(assessment) for assessment in task_state.dynamic_policy_history
        ),
        behavioral_assessments=tuple(
            _build_behavioral_assessment(assessment) for assessment in task_state.behavioral_history
        ),
        judge_assessments=tuple(
            _build_judge_assessment(assessment) for assessment in task_state.judge_history
        ),
        governance_decisions=tuple(
            _build_governance_decision(decision) for decision in task_state.governance_history
        ),
        human_review=_build_human_review_summary(task_state=task_state),
        external_communication=_build_external_communication_summary(
            task_state.external_communication
        ),
    )


# Read-only observability component extracted from the trace facade.
def _build_runtime_summary(*, thread_id: str, task_state: TaskState) -> TraceRuntimeSummary:
    return TraceRuntimeSummary(
        thread_id=thread_id,
        current_state=task_state.current_state.value,
        bootstrap_completed=task_state.bootstrap_completed,
        runtime_flags=tuple(flag.value for flag in task_state.runtime_flags),
        step_history=tuple(_build_step_record(record) for record in task_state.step_history),
        actor_turn_count=task_state.actor_turn_count,
        tool_call_count=task_state.tool_call_count,
    )


# Read-only observability component extracted from the trace facade.
def _build_intake_summary(*, intake_result: IntakeResult) -> TraceIntakeSummary:
    task_spec = intake_result.structured_task.task_spec
    execution_policy = intake_result.structured_task.execution_policy
    return TraceIntakeSummary(
        goal=task_spec.goal,
        context=task_spec.context,
        domain_type=task_spec.domain_type.value,
        domain_label=task_spec.domain_label,
        sensitivity=task_spec.sensitivity.value,
        success_criteria=tuple(task_spec.success_criteria),
        constraints=tuple(task_spec.constraints),
        parse_status=intake_result.metadata.parse_status.value,
        parser_name=intake_result.metadata.parser_name,
        prompt_version=intake_result.metadata.prompt_version,
        schema_version=intake_result.metadata.schema_version,
        policy_profile_name=execution_policy.policy_profile_name,
        confidence_threshold=execution_policy.confidence_threshold,
        max_steps=execution_policy.budget.max_steps,
        max_tool_calls=execution_policy.budget.max_tool_calls,
        read_only_mode=execution_policy.read_only_mode,
    )


# Read-only observability component extracted from the trace facade.
def _build_plan_step(*, step: Any, task_state: TaskState) -> TracePlanStep:
    resolved_requirement = resolve_required_evidence_support_context(
        task_state=task_state,
        current_step=step,
    )
    return TracePlanStep(
        step_id=step.step_id,
        objective=step.objective,
        rationale=step.rationale,
        expected_outcome=step.expected_outcome,
        step_kind=enum_value(step.step_kind),
        evidence_requirement=enum_value(step.evidence_requirement),
        claim_precision_ceiling=enum_value(step.claim_precision_ceiling),
        resolved_required_evidence_support_level=enum_value(
            resolved_requirement.required_level
        ),
        resolved_required_evidence_support_source=enum_value(resolved_requirement.source),
        status=enum_value(step.status),
    )


# Read-only observability component extracted from the trace facade.
def _build_actor_turn(*, actor_result: ActorResult | None, task_state: TaskState) -> TraceActorTurn:
    output_claim_level = (
        _output_claim_support_level(task_state=task_state) if actor_result is not None else None
    )
    return TraceActorTurn(
        actor_name=task_state.actor_name,
        actor_prompt_version=task_state.actor_prompt_version,
        result_type=actor_result.result_type.value if actor_result is not None else None,
        plan_step_id=actor_result.plan_step_id if actor_result is not None else None,
        summary=actor_result.summary if actor_result is not None else None,
        capability_mode=actor_result.capability_mode.value if actor_result is not None else None,
        external_impact=actor_result.external_impact if actor_result is not None else None,
        output_text=actor_result.output_text if actor_result is not None else None,
        blocking_reason=actor_result.blocking_reason if actor_result is not None else None,
        proposed_tool_name=(
            actor_result.tool_request.tool_name.value
            if actor_result is not None and actor_result.tool_request is not None
            else None
        ),
        output_claim_support_level=output_claim_level.value if output_claim_level is not None else None,
        precision_mismatch_detected=bool(
            actor_result is not None
            and actor_result.output_text
            and _precision_mismatch_detected(task_state=task_state)
        ),
    )


# Read-only observability component extracted from the trace facade.
def _build_execution_summary(*, task_state: TaskState) -> TraceExecutionSummary:
    return TraceExecutionSummary(
        final_output_text=task_state.final_output_text,
        executed_steps=tuple(_build_executed_step(step) for step in task_state.executed_steps),
        scratchpad=tuple(_build_scratchpad_entry(entry) for entry in task_state.scratchpad),
        step_validations=tuple(
            _build_step_validation(validation) for validation in task_state.step_validations
        ),
    )


# Read-only observability component extracted from the trace facade.
def _build_tool_calls(*, task_state: TaskState) -> tuple[TraceToolCall, ...]:
    calls: list[TraceToolCall] = []
    for result in task_state.tool_results_history:
        calls.append(_build_tool_call_from_result(result))
    if not calls and task_state.last_tool_result is not None:
        calls.append(_build_tool_call_from_result(task_state.last_tool_result))
    if task_state.current_tool_request is not None:
        calls.append(_build_tool_call_from_request(task_state.current_tool_request))
    return tuple(calls)


# Read-only observability component extracted from the trace facade.
def _build_tool_call_from_result(result: ToolExecutionResult) -> TraceToolCall:
    request = result.request
    return TraceToolCall(
        **_tool_request_fields(request),
        success=result.success,
        summary=result.summary,
        content=result.content,
        error_message=result.error_message,
        sources=tuple(_build_tool_source(source) for source in result.sources),
    )


# Read-only observability component extracted from the trace facade.
def _build_tool_call_from_request(request: ToolRequest) -> TraceToolCall:
    return TraceToolCall(
        **_tool_request_fields(request),
        success=None,
        summary=None,
        content=None,
        error_message=None,
        sources=(),
    )


# Read-only observability component extracted from the trace facade.
def _tool_request_fields(request: ToolRequest) -> dict[str, Any]:
    return {
        "plan_step_id": request.plan_step_id,
        "tool_name": request.tool_name.value,
        "reason": request.reason,
        "query": request.query,
        "url": request.url,
        "text": request.text,
        "path": request.path,
        "expression": request.expression,
        "timezone": request.timezone,
        "limit": request.limit,
        "max_results": request.max_results,
        "extractive_goal": request.extractive_goal,
    }


# Read-only observability component extracted from the trace facade.
def _build_tool_source(source: ToolSource) -> dict[str, Any]:
    return {
        "title": source.title,
        "locator": source.locator,
        "snippet": source.snippet,
    }


# Read-only observability component extracted from the trace facade.
def _build_observed_evidence(evidence: ObservedEvidence) -> TraceObservedEvidence:
    return TraceObservedEvidence(
        evidence_id=evidence.evidence_id,
        plan_step_id=evidence.plan_step_id,
        title=evidence.title,
        locator=evidence.locator,
        excerpt=evidence.excerpt,
        source_kind=evidence.source_kind.value,
        source_tool=evidence.source_tool.value if evidence.source_tool is not None else None,
        support_level=evidence.support_level.value,
    )


# Read-only observability component extracted from the trace facade.
def _build_governance_decision(decision: GovernanceDecision) -> TraceGovernanceDecision:
    return TraceGovernanceDecision(
        directive=decision.directive.value,
        policy_decision=decision.policy_decision.value,
        reason_codes=tuple(code.value for code in decision.reason_codes),
        summary=decision.summary,
        constrains_capability_use=decision.constrains_capability_use,
        evaluated_actor_result_type=decision.evaluated_actor_result_type.value,
        evaluated_tool_name=(
            decision.evaluated_tool_name.value if decision.evaluated_tool_name is not None else None
        ),
        policy_engine_name=decision.policy_engine_name,
        policy_bundle_version=decision.policy_bundle_version,
        policy_config_version=decision.policy_config_version,
        policy_directive=decision.policy_directive.value,
        oversight_recommended_directive=decision.oversight_recommended_directive.value,
        behavioral_recommended_directive=decision.behavioral_recommended_directive.value,
        judge_recommended_directive=decision.judge_recommended_directive.value,
        judge_invoked=decision.judge_invoked,
        static_policy_summary=decision.static_policy_summary,
        dynamic_policy_summary=decision.dynamic_policy_summary,
        behavioral_summary=decision.behavioral_summary,
        judge_summary=decision.judge_summary,
        behavioral_similarity_score=decision.behavioral_similarity_score,
        behavioral_deviation_score=decision.behavioral_deviation_score,
        behavioral_novelty_score=decision.behavioral_novelty_score,
        oversight_summary=decision.oversight_summary,
        combiner_dominant_dimension=decision.combiner_dominant_dimension,
        combiner_support_by_directive=decision.combiner_support_by_directive,
        combiner_selected_reason=decision.combiner_selected_reason,
        combiner_rejected_alternatives=decision.combiner_rejected_alternatives,
        combiner_safety_invariants_checked=decision.combiner_safety_invariants_checked,
        combiner_signals=decision.combiner_signals,
        combiner_normalized_directive=decision.combiner_normalized_directive,
        combiner_normalization_reason=decision.combiner_normalization_reason,
        governance_input_schema_version=decision.governance_input_schema_version,
        pillar_requirements=decision.pillar_requirements,
        pillar_results=decision.pillar_results,
        pillar_errors=decision.pillar_errors,
    )


# Read-only observability component extracted from the trace facade.
def _build_static_policy_assessment(
    assessment: StaticPolicyAssessment,
) -> TraceStaticPolicyAssessment:
    return TraceStaticPolicyAssessment(
        policy_decision=assessment.policy_decision.value,
        reason_codes=tuple(code.value for code in assessment.reason_codes),
        summary=assessment.summary,
        policy_engine_name=assessment.policy_engine_name,
        policy_bundle_version=assessment.policy_bundle_version,
        policy_directive=assessment.policy_directive.value,
    )


# Read-only observability component extracted from the trace facade.
def _build_oversight_assessment(assessment: OversightAssessment) -> TraceOversightAssessment:
    return TraceOversightAssessment(
        evidence_quality=_build_oversight_signal(assessment.evidence_quality),
        operational_risk=_build_oversight_signal(assessment.operational_risk),
        trajectory_health=_build_oversight_signal(assessment.trajectory_health),
        recommended_directive=assessment.recommended_directive.value,
        summary=assessment.summary,
        reason_codes=tuple(code.value for code in assessment.reason_codes),
    )


# Read-only observability component extracted from the trace facade.
def _build_behavioral_assessment(
    assessment: BehavioralAssessment,
) -> TraceBehavioralAssessment:
    return TraceBehavioralAssessment(
        history_available=assessment.history_available,
        historical_run_count=assessment.historical_run_count,
        feature_snapshot_version=assessment.feature_snapshot_version,
        summary=assessment.summary,
        notes=assessment.notes,
        matched_run_count=assessment.matched_run_count,
        historical_similarity_score=assessment.historical_similarity_score,
        behavior_deviation_score=assessment.behavior_deviation_score,
        novelty_score=assessment.novelty_score,
        behavioral_recommended_directive=assessment.behavioral_recommended_directive.value,
        behavioral_flags=assessment.behavioral_flags,
        matched_run_ids=assessment.matched_run_ids,
    )


# Read-only observability component extracted from the trace facade.
def _build_judge_assessment(
    assessment: JudgeAssessment,
) -> TraceJudgeAssessment:
    return TraceJudgeAssessment(
        invoked=assessment.invoked,
        recommendation=assessment.recommendation.value,
        confidence=assessment.confidence,
        summary=assessment.summary,
        rationale=assessment.rationale,
        flags=assessment.flags,
        evaluated_target=assessment.evaluated_target,
    )


# Read-only observability component extracted from the trace facade.
def _build_human_review_summary(*, task_state: TaskState) -> TraceHumanReviewSummary:
    return TraceHumanReviewSummary(
        pending_request=(
            _build_human_review_request(task_state.pending_human_review_request)
            if task_state.pending_human_review_request is not None
            else None
        ),
        last_request=(
            _build_human_review_request(task_state.last_human_review_request)
            if task_state.last_human_review_request is not None
            else None
        ),
        last_decision=(
            _build_human_review_decision(task_state.last_human_review_decision)
            if task_state.last_human_review_decision is not None
            else None
        ),
        decision_history=tuple(
            _build_human_review_decision(decision) for decision in task_state.human_review_history
        ),
    )


# Read-only observability component extracted from the trace facade.
def _build_human_review_request(
    request: HumanReviewRequest,
) -> TraceHumanReviewRequest:
    return TraceHumanReviewRequest(
        request_id=request.request_id,
        thread_id=request.thread_id,
        current_state=request.current_state.value,
        resume_state=request.resume_state.value,
        current_plan_step_id=request.current_plan_step_id,
        current_plan_step_objective=request.current_plan_step_objective,
        actor_result_type=(
            request.actor_result_type.value if request.actor_result_type is not None else None
        ),
        current_tool_request=(
            to_jsonable(request.current_tool_request)
            if request.current_tool_request is not None
            else None
        ),
        governance_directive=(
            request.governance_directive.value if request.governance_directive is not None else None
        ),
        governance_summary=request.governance_summary,
        static_policy_summary=request.static_policy_summary,
        dynamic_policy_summary=request.dynamic_policy_summary,
        behavioral_summary=request.behavioral_summary,
        judge_summary=request.judge_summary,
        oversight_summary=request.oversight_summary,
        external_communication_target_type=request.external_communication_target_type,
        external_communication_draft=(
            _build_external_communication_draft(request.external_communication_draft)
            if request.external_communication_draft is not None
            else None
        ),
        reason_codes=tuple(request.reason_codes),
        allowed_decisions=tuple(decision.value for decision in request.allowed_decisions),
        review_summary=request.review_summary,
    )


# Read-only observability component extracted from the trace facade.
def _build_human_review_decision(
    decision: HumanReviewDecision,
) -> TraceHumanReviewDecision:
    return TraceHumanReviewDecision(
        request_id=decision.request_id,
        decision_type=decision.decision_type.value,
        note=decision.note,
        clarification_text=decision.clarification_text,
        external_communication_edit=(
            _build_human_review_external_edit(decision.external_communication_edit)
            if decision.external_communication_edit is not None
            else None
        ),
        reviewer_id=decision.reviewer_id,
    )


# Read-only observability component extracted from the trace facade.
def _build_external_communication_summary(
    external_communication: ExternalCommunicationState | None,
) -> TraceExternalCommunicationSummary | None:
    if external_communication is None:
        return None
    return TraceExternalCommunicationSummary(
        phase=external_communication.phase.value,
        draft=(
            _build_external_communication_draft(external_communication.draft)
            if external_communication.draft is not None
            else None
        ),
        last_applied_edit=(
            _build_human_review_external_edit(external_communication.last_applied_edit)
            if external_communication.last_applied_edit is not None
            else None
        ),
        edit_history=tuple(
            _build_human_review_external_edit(edit) for edit in external_communication.edit_history
        ),
        last_transition_summary=external_communication.last_transition_summary,
    )


# Read-only observability component extracted from the trace facade.
def _build_external_communication_draft(
    draft: ExternalCommunicationDraft,
) -> TraceExternalCommunicationDraft:
    return TraceExternalCommunicationDraft(
        channel=draft.channel,
        recipient=draft.recipient,
        subject=draft.subject,
        body=draft.body,
        metadata=dict(draft.metadata),
    )


# Read-only observability component extracted from the trace facade.
def _build_human_review_external_edit(
    edit: HumanReviewExternalEdit,
) -> TraceHumanReviewExternalEdit:
    return TraceHumanReviewExternalEdit(
        draft=_build_external_communication_draft(edit.draft),
        note=edit.note,
    )


# Read-only observability component extracted from the trace facade.
def _build_oversight_signal(
    signal: EvidenceQualityAssessment | OperationalRiskAssessment | TrajectoryHealthAssessment,
) -> TraceOversightSignal:
    return TraceOversightSignal(
        score=signal.score,
        level=signal.level.value,
        reason_codes=tuple(code.value for code in signal.reason_codes),
    )


# Read-only observability component extracted from the trace facade.
def _build_step_record(record: StepRecord) -> dict[str, Any]:
    return {
        "step_index": record.step_index,
        "runtime_state": record.runtime_state.value,
        "node_name": record.node_name,
        "summary": record.summary,
    }


# Read-only observability component extracted from the trace facade.
def _build_executed_step(step: ExecutedPlanStep) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "objective": step.objective,
        "result_type": step.result_type.value,
        "summary": step.summary,
        "output_text": step.output_text,
    }


# Read-only observability component extracted from the trace facade.
def _build_scratchpad_entry(entry: ScratchpadEntry) -> dict[str, Any]:
    return {
        "turn_index": entry.turn_index,
        "plan_step_id": entry.plan_step_id,
        "entry_type": entry.entry_type.value,
        "summary": entry.summary,
        "evidence_refs": list(entry.evidence_refs),
    }


# Read-only observability component extracted from the trace facade.
def _build_step_validation(validation: StepValidation) -> dict[str, Any]:
    return {
        "plan_step_id": validation.plan_step_id,
        "objective_satisfied": validation.objective_satisfied,
        "evidence_sufficient": validation.evidence_sufficient,
        "confidence": validation.confidence,
        "next_action": validation.next_action.value,
        "rationale_summary": validation.rationale_summary,
        "residual_uncertainty": validation.residual_uncertainty,
    }


# Read-only observability component extracted from the trace facade.
def _resolve_planning_source(planner_name: str | None) -> str | None:
    if planner_name is None:
        return None
    if "replanner" in planner_name:
        return "replanner"
    return "planner"


__all__ = [
    "build_layer2_runtime_trace",
    "_build_runtime_summary",
    "_build_intake_summary",
    "_build_plan_step",
    "_build_actor_turn",
    "_build_execution_summary",
    "_build_tool_calls",
    "_build_tool_call_from_result",
    "_build_tool_call_from_request",
    "_tool_request_fields",
    "_build_tool_source",
    "_build_observed_evidence",
    "_build_governance_decision",
    "_build_static_policy_assessment",
    "_build_oversight_assessment",
    "_build_behavioral_assessment",
    "_build_judge_assessment",
    "_build_human_review_summary",
    "_build_human_review_request",
    "_build_human_review_decision",
    "_build_external_communication_summary",
    "_build_external_communication_draft",
    "_build_human_review_external_edit",
    "_build_oversight_signal",
    "_build_step_record",
    "_build_executed_step",
    "_build_scratchpad_entry",
    "_build_step_validation",
    "_resolve_planning_source"
]
