from datetime import datetime
from typing import Any

from layer1_intake import IntakeResult

from layer2_runtime_governance.actor_contract import ActorResult
from layer2_runtime_governance.governance_components import (
    GovernanceDecision,
    governance_reason_code_values,
)
from .serialization import enum_value, to_jsonable
from layer2_runtime_governance.domain import (
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
from layer2_runtime_governance.domain import HumanReviewDecision, HumanReviewRequest
from layer3_tool_bus_external_capabilities.tool_contract import ToolExecutionResult, ToolObservation, ToolRequest
from layer3_tool_bus_external_capabilities.external_operations import ExternalExecutionReceipt


from .models import (
    Layer2RuntimeTrace,
    TraceActorTurn,
    TraceExecutionSummary,
    TraceEvidenceBurden,
    TraceExternalCommunicationDraft,
    TraceExternalCommunicationSummary,
    TraceExternalExecutionReceipt,
    TraceExternalExecutionSummary,
    TraceGovernancePass,
    TraceHumanReviewDecision,
    TraceHumanReviewExternalEdit,
    TraceHumanReviewRequest,
    TraceHumanReviewSummary,
    TraceIntakeSummary,
    TraceToolObservation,
    TracePlanStep,
    TraceRuntimeSummary,
    TraceToolCall,
)


# Read-only observability component extracted from the trace facade.
def build_layer2_runtime_trace(
    thread_id: str,
    intake_result: IntakeResult,
    task_state: TaskState,
) -> Layer2RuntimeTrace:
    task_state = normalize_task_state_serialization(task_state)
    return Layer2RuntimeTrace(
        trace_version="layer2_trace.v6",
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
        observations=tuple(
            _build_tool_observation(observation) for observation in task_state.observations
        ),
        governance_passes=tuple(
            _build_governance_pass(pass_index=index, decision=decision)
            for index, decision in enumerate(task_state.governance_history, start=1)
        ),
        human_review=_build_human_review_summary(task_state=task_state),
        external_communication=_build_external_communication_summary(
            task_state.external_communication
        ),
        external_execution=_build_external_execution_summary(task_state=task_state),
    )


# Read-only observability component extracted from the trace facade.
def _build_runtime_summary(*, thread_id: str, task_state: TaskState) -> TraceRuntimeSummary:
    return TraceRuntimeSummary(
        thread_id=thread_id,
        current_state=task_state.current_state.value,
        termination_reason=enum_value(task_state.termination_reason),
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
        sensitivity=task_spec.sensitivity.value,
        success_criteria=tuple(task_spec.success_criteria),
        constraints=tuple(task_spec.constraints),
        parse_status=intake_result.metadata.parse_status.value,
        parser_name=intake_result.metadata.parser_name,
        prompt_version=intake_result.metadata.prompt_version,
        schema_version=intake_result.metadata.schema_version,
        policy_profile_name=execution_policy.policy_profile_name,
        confidence_threshold=execution_policy.confidence_threshold,
        read_only_mode=execution_policy.read_only_mode,
        execution_intent=task_spec.execution_intent.value,
    )


# Read-only observability component extracted from the trace facade.
def _build_plan_step(*, step: Any, task_state: TaskState) -> TracePlanStep:
    evidence_burden = getattr(step, "evidence_burden", None)
    return TracePlanStep(
        step_id=step.step_id,
        objective=step.objective,
        rationale=step.rationale,
        expected_outcome=step.expected_outcome,
        status=enum_value(step.status),
        produces_factual_claims=bool(
            getattr(step, "produces_factual_claims", False)
        ),
        evidence_burden=(
            TraceEvidenceBurden(
                min_independent_sources=evidence_burden.min_independent_sources,
                require_retrieved_content=evidence_burden.require_retrieved_content,
                freshness_window_days=evidence_burden.freshness_window_days,
            )
            if evidence_burden is not None
            else None
        ),
    )


# Read-only observability component extracted from the trace facade.
def _build_actor_turn(*, actor_result: ActorResult | None, task_state: TaskState) -> TraceActorTurn:
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
        actor_self_confidence=(actor_result.step_validation.actor_self_confidence if actor_result and actor_result.step_validation else None),
        selected_evidence_refs=(actor_result.step_validation.evidence_refs if actor_result and actor_result.step_validation else ()),
        external_operation=(
            to_jsonable(
                actor_result.external_operation_proposal
                or task_state.external_operation_proposal
            )
            if actor_result is not None
            and (
                actor_result.external_operation_proposal is not None
                or task_state.external_operation_proposal is not None
            )
            else None
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
    calls.extend(_build_tool_call_from_request(request) for request in task_state.pending_tool_requests)
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
        error_code=_enum_or_string_value(result.error_code),
        truncated=result.truncated,
        extractability_limited=result.extractability_limited,
        observations=tuple(
            _build_tool_observation_dict(observation)
            for observation in result.observations
        ),
    )


# Preserve compatibility with checkpoints written before an enum was serializer-allowlisted.
def _enum_or_string_value(value: Any | None) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", value)
    return str(enum_value)


# Read-only observability component extracted from the trace facade.
def _build_tool_call_from_request(request: ToolRequest) -> TraceToolCall:
    return TraceToolCall(
        **_tool_request_fields(request),
        success=None,
        summary=None,
        content=None,
        error_message=None,
        error_code=None,
        truncated=False,
        extractability_limited=False,
        observations=(),
    )


# Read-only observability component extracted from the trace facade.
def _tool_request_fields(request: ToolRequest) -> dict[str, Any]:
    return {
        "request_id": request.request_id,
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
        "document_id": request.document_id,
        "dataset_id": request.dataset_id,
    }


# Read-only observability component extracted from the trace facade.
def _build_tool_observation_dict(observation: ToolObservation) -> dict[str, Any]:
    return {
        "observation_id": observation.observation_id,
        "content": observation.content,
        "locator": observation.locator,
        "source_timestamp": _source_timestamp_iso(observation),
    }


# Read-only observability component extracted from the trace facade.
def _build_tool_observation(observation: ToolObservation) -> TraceToolObservation:
    return TraceToolObservation(
        observation_id=observation.observation_id,
        content=observation.content,
        locator=observation.locator,
        source_timestamp=_source_timestamp_iso(observation),
    )


# Serialize source metadata defensively so pre-existing checkpoint observations remain readable.
def _source_timestamp_iso(observation: ToolObservation) -> str | None:
    source_timestamp = getattr(observation, "source_timestamp", None)
    return source_timestamp.isoformat() if isinstance(source_timestamp, datetime) else None


# Collapse the former parallel governance histories into one audit record per OPA pass.
def _build_governance_pass(
    *,
    pass_index: int,
    decision: GovernanceDecision,
) -> TraceGovernancePass:
    return TraceGovernancePass(
        pass_index=pass_index,
        evaluated_plan_step_id=decision.evaluated_plan_step_id,
        evaluated_actor_result_type=decision.evaluated_actor_result_type.value,
        evaluated_tool_name=(
            decision.evaluated_tool_name.value
            if decision.evaluated_tool_name is not None
            else None
        ),
        evaluated_operation_digest=decision.evaluated_operation_digest,
        governance_input_schema_version=decision.governance_input_schema_version,
        policy_bundle_version=decision.policy_bundle_version,
        policy_config_version=decision.policy_config_version,
        pillar_requirements=dict(decision.pillar_requirements or {}),
        pillar_results=dict(decision.pillar_results or {}),
        pillar_errors=dict(decision.pillar_errors or {}),
        directive=decision.directive.value,
        policy_decision=decision.policy_decision.value,
        requires_human_review=decision.requires_human_review,
        reason_codes=governance_reason_code_values(decision),
        selected_reason=decision.combiner_selected_reason or decision.summary,
        rejected_alternatives=dict(decision.combiner_rejected_alternatives or {}),
        safety_invariants=tuple(decision.combiner_safety_invariants_checked),
        judge_admission=(
            to_jsonable(decision.judge_admission)
            if decision.judge_admission is not None
            else None
        ),
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
        operation_id=request.operation_id,
        operation_payload_digest=request.operation_payload_digest,
        operation_proposal_summary=request.operation_proposal_summary,
        operation_target=request.operation_target,
        external_operation_proposal=(
            to_jsonable(request.external_operation_proposal)
            if request.external_operation_proposal is not None
            else None
        ),
        judge_findings=request.judge_findings,
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
        approved_operation_digest=decision.approved_operation_digest,
        external_operation_edit=(
            to_jsonable(decision.external_operation_edit)
            if decision.external_operation_edit is not None
            else None
        ),
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
        cc=tuple(draft.cc),
        bcc=tuple(draft.bcc),
        metadata=dict(draft.metadata),
    )


# Build the typed external execution projection without exposing connector secrets.
def _build_external_execution_summary(
    *,
    task_state: TaskState,
) -> TraceExternalExecutionSummary:
    proposal = task_state.external_operation_proposal
    return TraceExternalExecutionSummary(
        execution_intent=task_state.execution_intent.value,
        capability_id=proposal.capability_id if proposal is not None else None,
        current_payload_digest=proposal.payload_digest if proposal is not None else None,
        current_request=(
            to_jsonable(task_state.current_external_execution_request)
            if task_state.current_external_execution_request is not None
            else None
        ),
        last_receipt=(
            _build_external_execution_receipt(
                task_state.last_external_execution_receipt
            )
            if task_state.last_external_execution_receipt is not None
            else None
        ),
        receipt_history=tuple(
            _build_external_execution_receipt(receipt)
            for receipt in task_state.external_execution_history
        ),
        attempt_count=task_state.external_execution_attempt_count,
    )


# Convert one canonical receipt into its closed trace model.
def _build_external_execution_receipt(
    receipt: ExternalExecutionReceipt,
) -> TraceExternalExecutionReceipt:
    return TraceExternalExecutionReceipt(
        operation_id=receipt.operation_id,
        capability_id=receipt.capability_id,
        payload_digest=receipt.payload_digest,
        status=receipt.status.value,
        idempotency_key=receipt.idempotency_key,
        started_at=receipt.started_at.isoformat(),
        completed_at=receipt.completed_at.isoformat(),
        external_reference=receipt.external_reference,
        summary=receipt.summary,
        error_code=receipt.error_code.value,
        retryable=receipt.retryable,
        connector_kind=receipt.connector_kind,
        connector_id=receipt.connector_id,
        server_id=receipt.server_id,
        tool_name=receipt.tool_name,
        connector_metadata=tuple(
            {"name": item.name, "value": item.value}
            for item in receipt.connector_metadata
        ),
        attempt_number=receipt.attempt_number,
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
        "actor_self_confidence": validation.actor_self_confidence,
        "evidence_refs": list(validation.evidence_refs),
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
    "_build_tool_observation",
    "_build_governance_pass",
    "_build_human_review_summary",
    "_build_human_review_request",
    "_build_human_review_decision",
    "_build_external_communication_summary",
    "_build_external_communication_draft",
    "_build_external_execution_summary",
    "_build_external_execution_receipt",
    "_build_human_review_external_edit",
    "_build_step_record",
    "_build_executed_step",
    "_build_scratchpad_entry",
    "_build_step_validation",
    "_resolve_planning_source"
]
