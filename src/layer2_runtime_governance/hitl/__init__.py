from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from langgraph.types import interrupt
from json_serialization import to_jsonable as _to_jsonable
from layer1_intake import ExecutionIntent

from ..actor_contract import ActorResultType
from ..behavioral import BehavioralAssessment
from ..governance_components import governance_reason_code_values
from ..oversight.contracts import DynamicPolicyAssessment, OversightAssessment
from ..domain import (
    ExternalCommunicationPhase,
    ExternalCommunicationDraft,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    HumanReviewRequest,
    RuntimeFlag,
    RuntimeState,
    StepRecord,
    TaskState,
    normalize_task_state_serialization,
)
from ..domain.step_recovery import restore_actor_step_for_more_evidence
from ..external_operations import (
    CommunicationOperationPayload,
    DatabaseOperationPayload,
    DatabaseOperationParameter,
    DestinationClass,
    ExternalOperationEffect,
    ExternalOperationPayloadEdit,
    ExternalExecutionAuthorizationSource,
    ExternalExecutionRequest,
    ExternalExecutionStatus,
    GenericExternalOperationPayload,
    GenericPayloadField,
    OperationBoundary,
    SQLStatementClass,
    normalize_communication_operation,
    normalize_sql_operation,
    replace_external_operation_payload,
    build_external_execution_request,
)


# Build the canonical human review request from the current runtime state.
def build_human_review_request(*, state: TaskState, thread_id: str) -> HumanReviewRequest:
    state = normalize_task_state_serialization(state)
    current_plan_step = _resolve_review_plan_step(state)
    governance_decision = state.last_governance_decision
    dynamic_policy_assessment = state.last_dynamic_policy_assessment or state.last_oversight_assessment
    behavioral_assessment = state.last_behavioral_assessment
    judge_assessment = state.last_judge_assessment
    resume_state = _resolve_resume_state(state)
    external_draft = (
        state.external_communication.draft if state.external_communication is not None else None
    )
    external_proposal = state.external_operation_proposal
    judge_findings = tuple(
        finding.summary
        for finding in (judge_assessment.findings if judge_assessment is not None else ())
    )

    reason_codes: list[str] = []
    if governance_decision is not None:
        reason_codes.extend(governance_reason_code_values(governance_decision))
    if dynamic_policy_assessment is not None:
        reason_codes.extend(code.value for code in dynamic_policy_assessment.reason_codes)
    if not reason_codes:
        reason_codes.append("human_review_required")

    return HumanReviewRequest(
        request_id=_build_request_id(state=state, thread_id=thread_id),
        thread_id=thread_id,
        current_state=state.current_state,
        resume_state=resume_state,
        current_plan_step_id=current_plan_step.step_id if current_plan_step is not None else None,
        current_plan_step_objective=(
            current_plan_step.objective if current_plan_step is not None else None
        ),
        actor_result_type=state.actor_result.result_type if state.actor_result is not None else None,
        current_tool_request=state.current_tool_request,
        governance_directive=(
            governance_decision.directive if governance_decision is not None else None
        ),
        governance_summary=governance_decision.summary if governance_decision is not None else None,
        static_policy_summary=(
            getattr(governance_decision, "static_policy_summary", None)
            if governance_decision is not None
            else None
        ),
        dynamic_policy_summary=(
            getattr(governance_decision, "dynamic_policy_summary", None)
            if governance_decision is not None
            else None
        ),
        behavioral_summary=(
            behavioral_assessment.summary if behavioral_assessment is not None else None
        ),
        judge_summary=judge_assessment.summary if judge_assessment is not None else None,
        oversight_summary=(
            dynamic_policy_assessment.summary if dynamic_policy_assessment is not None else None
        ),
        external_communication_target_type=(
            external_draft.channel if external_draft is not None else None
        ),
        external_communication_draft=external_draft,
        reason_codes=tuple(reason_codes),
        allowed_decisions=_resolve_allowed_human_review_decisions(state=state),
        review_summary=_build_review_summary(
            governance_decision=governance_decision,
            dynamic_policy_assessment=dynamic_policy_assessment,
            behavioral_assessment=behavioral_assessment,
            judge_assessment=judge_assessment,
            resume_state=resume_state,
            external_communication_draft=external_draft,
        ),
        operation_id=external_proposal.operation_id if external_proposal else None,
        operation_payload_digest=(
            external_proposal.payload_digest if external_proposal else None
        ),
        operation_proposal_summary=(
            external_proposal.summary() if external_proposal else None
        ),
        operation_target=(
            external_proposal.target.value if external_proposal else None
        ),
        external_operation_proposal=external_proposal,
        judge_findings=judge_findings,
    )


# Route the interrupt payload through the LangGraph HITL pause/resume mechanism.
def human_review(state: TaskState, *, thread_id: str) -> TaskState:
    runtime_state = deepcopy(state)

    if runtime_state.pending_human_review_request is None:
        runtime_state.pending_human_review_request = build_human_review_request(
            state=runtime_state,
            thread_id=thread_id,
        )

    pending_request = runtime_state.pending_human_review_request
    if pending_request is None:
        raise ValueError("Human review requested without a pending review payload.")

    # The interrupt payload must be JSON-safe so dashboard code can consume it directly.
    decision_payload = interrupt(_to_jsonable(pending_request))
    human_review_decision = _coerce_human_review_decision(
        decision_payload,
        expected_request_id=pending_request.request_id,
        expected_operation_digest=pending_request.operation_payload_digest,
    )
    if human_review_decision.decision_type not in pending_request.allowed_decisions:
        raise ValueError(
            "Human review decision is not permitted for the current review request."
        )
    _apply_human_review_decision(runtime_state, human_review_decision)

    runtime_state.human_review_resume_state = pending_request.resume_state
    runtime_state.last_human_review_request = pending_request
    runtime_state.pending_human_review_request = None
    runtime_state.human_review_history.append(human_review_decision)
    # A typed human decision is a concrete state change and resets local liveness recovery.
    runtime_state.progress_epoch += 1
    runtime_state.state_revision += 1
    runtime_state.no_progress_turns = 0
    runtime_state.step_index += 1
    runtime_state.step_history.append(
        StepRecord(
            step_index=runtime_state.step_index,
            runtime_state=runtime_state.current_state,
            node_name="human_review",
            summary=(
                "Human review resumed the runtime with typed decision "
                f"'{human_review_decision.decision_type.value}'."
            ),
        )
    )
    return runtime_state


# Convert a review decision payload back into the canonical typed decision model.
def _coerce_human_review_decision(
    payload: Any,
    *,
    expected_request_id: str,
    expected_operation_digest: str | None = None,
) -> HumanReviewDecision:
    if isinstance(payload, HumanReviewDecision):
        decision = payload
    elif isinstance(payload, dict):
        decision_type_value = payload.get("decision_type", payload.get("decision"))
        if decision_type_value is None:
            raise ValueError("Human review resume payload is missing decision_type.")

        decision_type = _parse_decision_type(decision_type_value)
        external_edit = _optional_external_communication_edit(payload)
        operation_edit = _optional_external_operation_edit(payload)
        if (
            decision_type == HumanReviewDecisionType.MODIFY_AND_CONTINUE
            and external_edit is None
            and operation_edit is None
        ):
            raise ValueError("modify_and_continue requires a typed external operation edit.")

        decision = HumanReviewDecision(
            request_id=_extract_request_id(payload),
            decision_type=decision_type,
            note=_optional_text(payload.get("note")),
            clarification_text=_optional_text(
                payload.get("clarification_text", payload.get("clarification"))
            ),
            external_communication_edit=external_edit,
            reviewer_id=_optional_text(payload.get("reviewer_id")),
            approved_operation_digest=_optional_text(
                payload.get("approved_operation_digest")
            ),
            external_operation_edit=operation_edit,
        )
    else:
        raise ValueError("Human review resume payload must be a decision dict or dataclass.")

    if decision.request_id != expected_request_id:
        raise ValueError("Human review resume payload request_id does not match the pending review.")

    if (
        decision.decision_type
        in {
            HumanReviewDecisionType.APPROVE_CONTINUE,
            HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY,
        }
        and decision.approved_operation_digest is None
        and expected_operation_digest is not None
    ):
        # The control plane binds the click to the exact pending digest.
        decision = replace(
            decision,
            approved_operation_digest=expected_operation_digest,
        )
    return decision


# Expose one bounded adapter for dashboard/API payloads without duplicating HITL parsing.
def coerce_human_review_decision_payload(
    payload: Any,
    *,
    expected_request_id: str,
    expected_operation_digest: str | None = None,
) -> HumanReviewDecision:
    return _coerce_human_review_decision(
        payload,
        expected_request_id=expected_request_id,
        expected_operation_digest=expected_operation_digest,
    )


# Apply the typed human decision to runtime state without mutating the review payload itself.
def _apply_human_review_decision(
    state: TaskState,
    human_review_decision: HumanReviewDecision,
) -> None:
    pending_request = state.pending_human_review_request
    if pending_request is None:
        raise ValueError("Cannot apply a human review decision without a pending request.")

    if human_review_decision.request_id != pending_request.request_id:
        raise ValueError("Human review decision request_id does not match the pending request.")

    if human_review_decision.decision_type == HumanReviewDecisionType.APPROVE_CONTINUE:
        _validate_digest_bound_approval(
            state=state,
            decision=human_review_decision,
            pending_request=pending_request,
        )
        if _is_indeterminate_reconciliation(state):
            # Import after HITL module initialization to avoid the runtime package cycle.
            from ..runtime.actor_service import commit_terminal_completion_candidate

            receipt = state.last_external_execution_receipt
            if receipt is None:
                raise ValueError(
                    "Indeterminate reconciliation requires a persisted receipt."
                )
            commit_terminal_completion_candidate(
                state,
                expected_operation_digest=receipt.payload_digest,
                skip_reason=(
                    "Human review reconciled the digest-bound indeterminate "
                    "external execution as terminal."
                ),
            )
            state.current_external_execution_request = None
            state.current_state = RuntimeState.COMPLETED
            return
        if _is_governed_external_proposal(state) and (
            state.execution_intent == ExecutionIntent.PROPOSAL_ONLY
        ):
            # Import after HITL module initialization to avoid the runtime package cycle.
            from ..runtime.actor_service import commit_terminal_completion_candidate

            proposal = state.external_operation_proposal
            if proposal is None:
                raise ValueError("Proposal-only approval requires a governed proposal.")
            commit_terminal_completion_candidate(
                state,
                expected_operation_digest=proposal.payload_digest,
                skip_reason=(
                    "Human review accepted the proposal as the terminal local artifact."
                ),
            )
            _set_external_communication_phase(
                state,
                phase=ExternalCommunicationPhase.APPROVED_DRAFT,
                summary=(
                    "Human review approved the draft as a terminal local artifact; "
                    "no external execution was requested."
                ),
                review_ready=False,
            )
            state.approved_external_operation_digest = (
                human_review_decision.approved_operation_digest
            )
            state.current_external_execution_request = None
            state.current_state = RuntimeState.COMPLETED
            return
        if not _is_governed_external_proposal(state):
            state.current_state = pending_request.resume_state
            return
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.APPROVED_FOR_EXECUTION,
            summary="Human review authorized the exact proposal digest for external execution.",
            review_ready=True,
        )
        state.approved_external_operation_digest = (
            human_review_decision.approved_operation_digest
        )
        state.current_external_execution_request = _build_human_authorized_request(
            state=state,
            decision=human_review_decision,
        )
        state.current_state = RuntimeState.READY_EXTERNAL_EXECUTION
        return

    if human_review_decision.decision_type == HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY:
        _validate_digest_bound_approval(
            state=state,
            decision=human_review_decision,
            pending_request=pending_request,
        )
        proposal = state.external_operation_proposal
        if proposal is not None and proposal.normalized_effect != ExternalOperationEffect.READ:
            raise ValueError(
                "approve_continue_read_only cannot authorize an unchanged mutating proposal."
            )
        if not state.execution_policy.read_only_mode:
            state.execution_policy = state.execution_policy.model_copy(
                update={"read_only_mode": True}
            )
        if RuntimeFlag.READ_ONLY_EXECUTION not in state.runtime_flags:
            state.runtime_flags.append(RuntimeFlag.READ_ONLY_EXECUTION)
        if _is_governed_external_proposal(state) and (
            state.execution_intent == ExecutionIntent.PROPOSAL_ONLY
        ):
            _set_external_communication_phase(
                state,
                phase=ExternalCommunicationPhase.APPROVED_DRAFT,
                summary="Human review approved a terminal draft under a read-only envelope.",
                review_ready=False,
            )
            state.approved_external_operation_digest = (
                human_review_decision.approved_operation_digest
            )
            state.current_external_execution_request = None
            state.current_state = RuntimeState.COMPLETED
            return
        if not _is_governed_external_proposal(state):
            state.current_state = pending_request.resume_state
            return
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.APPROVED_FOR_EXECUTION,
            summary="Human review authorized the exact read-only proposal digest.",
            review_ready=True,
        )
        state.approved_external_operation_digest = (
            human_review_decision.approved_operation_digest
        )
        state.current_external_execution_request = _build_human_authorized_request(
            state=state,
            decision=human_review_decision,
        )
        state.current_state = RuntimeState.READY_EXTERNAL_EXECUTION
        return

    if human_review_decision.decision_type == HumanReviewDecisionType.MODIFY_AND_CONTINUE:
        external_edit = human_review_decision.external_communication_edit
        operation_edit = human_review_decision.external_operation_edit
        if external_edit is not None:
            _apply_external_communication_edit(state, external_edit=external_edit)
        elif operation_edit is not None:
            _apply_external_operation_edit(state, operation_edit=operation_edit)
        else:
            raise ValueError("modify_and_continue requires a typed external operation edit.")
        state.approved_external_operation_digest = None
        state.current_external_execution_request = None
        state.current_tool_request = None
        # Every modified digest returns directly to governance before any execution seam.
        state.current_state = RuntimeState.READY_GOVERNANCE
        return

    if human_review_decision.decision_type == HumanReviewDecisionType.REQUEST_MORE_EVIDENCE:
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EVIDENCE_COLLECTION,
            summary="Human review sent the external communication branch back for more evidence.",
            review_ready=False,
        )
        restore_actor_step_for_more_evidence(state)
        state.current_tool_request = None
        state.current_state = RuntimeState.ACTING
        return

    if human_review_decision.decision_type == HumanReviewDecisionType.REPLAN:
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EVIDENCE_COLLECTION,
            summary="Human review invalidated the current external communication trajectory and requested replanning.",
            review_ready=False,
        )
        state.current_tool_request = None
        state.current_state = RuntimeState.REPLANNING
        return

    if human_review_decision.decision_type == HumanReviewDecisionType.ASK_CLARIFICATION:
        if RuntimeFlag.CLARIFICATION_REQUIRED not in state.runtime_flags:
            state.runtime_flags.append(RuntimeFlag.CLARIFICATION_REQUIRED)
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED,
            summary="Human review requested clarification before the external communication branch can continue.",
            review_ready=True,
        )
        state.current_tool_request = None
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        return

    if human_review_decision.decision_type == HumanReviewDecisionType.ABORT:
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.BLOCKED,
            summary="Human review aborted the external communication branch.",
            review_ready=False,
        )
        state.current_tool_request = None
        state.last_tool_result = None
        state.current_state = RuntimeState.ABORTED
        return

    raise ValueError(f"Unsupported human review decision: {human_review_decision.decision_type}")


# Build a request whose authority is the reviewer decision bound to the pending digest.
def _build_human_authorized_request(
    *,
    state: TaskState,
    decision: HumanReviewDecision,
) -> ExternalExecutionRequest:
    proposal = state.external_operation_proposal
    approved_digest = decision.approved_operation_digest
    if proposal is None or approved_digest is None:
        raise ValueError("Human authorization requires a current proposal and exact digest.")
    return build_external_execution_request(
        proposal=proposal,
        authorized_payload_digest=approved_digest,
        execution_intent=state.execution_intent,
        authorization_source=ExternalExecutionAuthorizationSource.HUMAN_REVIEW,
        reviewer_id=decision.reviewer_id,
    )


# Treat approval after an indeterminate receipt as reconciliation, never as retry authority.
def _is_indeterminate_reconciliation(state: TaskState) -> bool:
    return (
        RuntimeFlag.EXTERNAL_EXECUTION_RECONCILIATION_REQUIRED in state.runtime_flags
        and state.last_external_execution_receipt is not None
        and state.last_external_execution_receipt.status
        == ExternalExecutionStatus.INDETERMINATE
    )


# Identify the only proposal boundary that may enter the external execution seam.
def _is_governed_external_proposal(state: TaskState) -> bool:
    return bool(
        state.external_operation_proposal is not None
        and state.external_operation_proposal.boundary
        == OperationBoundary.GOVERNED_EXTERNAL
    )


# Build a stable request id so replay and resume stay aligned across checkpoints.
def _build_request_id(*, state: TaskState, thread_id: str) -> str:
    review_step = _resolve_review_plan_step(state)
    current_step = review_step.step_id if review_step is not None else "no_step"
    digest_suffix = (
        f":{state.external_operation_proposal.payload_digest[:12]}"
        if state.external_operation_proposal is not None
        else ""
    )
    return f"{thread_id}:human_review:{state.step_index}:{current_step}{digest_suffix}"


# Resolve the plan step being reviewed, which may be the just-completed actor step after cursor advance.
def _resolve_review_plan_step(state: TaskState):
    actor_result = state.actor_result
    if (
        actor_result is not None
        and actor_result.plan_step_id
        and state.external_communication is not None
        and state.external_communication.draft is not None
        and state.external_communication.review_ready
    ):
        for step in state.plan_steps:
            if step.step_id == actor_result.plan_step_id:
                return step

    return state.current_plan_step


# Resolve the runtime state the graph should return to after approval.
def _resolve_resume_state(state: TaskState) -> RuntimeState:
    if state.human_review_resume_state is not None:
        return state.human_review_resume_state

    if state.current_tool_request is not None:
        return RuntimeState.READY_TOOL_EXECUTION

    if state.actor_result is not None and state.actor_result.result_type == ActorResultType.NEEDS_REPLAN:
        return RuntimeState.REPLANNING

    if state.current_plan_step_index is None:
        return RuntimeState.COMPLETED if state.executed_steps else RuntimeState.PLANNING

    if state.actor_result is not None and state.actor_result.result_type in {
        ActorResultType.INTERMEDIATE_OUTPUT,
    }:
        if state.current_plan_step_index < len(state.plan_steps) - 1:
            return RuntimeState.READY_NEXT_STEP
        return RuntimeState.COMPLETED

    return RuntimeState.ACTING


# Summarize the review for trace and dashboard display.
def _build_review_summary(
    *,
    governance_decision: Any,
    dynamic_policy_assessment: DynamicPolicyAssessment | OversightAssessment | None,
    behavioral_assessment: BehavioralAssessment | None,
    judge_assessment: Any,
    resume_state: RuntimeState,
    external_communication_draft: ExternalCommunicationDraft | None = None,
) -> str | None:
    parts: list[str] = []
    normalized_parts: list[str] = []
    if governance_decision is not None:
        _append_unique_review_part(
            parts,
            normalized_parts,
            governance_decision.summary,
        )
    if dynamic_policy_assessment is not None and dynamic_policy_assessment.summary:
        _append_unique_review_part(
            parts,
            normalized_parts,
            dynamic_policy_assessment.summary,
        )
    if behavioral_assessment is not None and behavioral_assessment.summary:
        _append_unique_review_part(parts, normalized_parts, behavioral_assessment.summary)
    if judge_assessment is not None and getattr(judge_assessment, "summary", None):
        _append_unique_review_part(parts, normalized_parts, judge_assessment.summary)
    if external_communication_draft is not None:
        _append_unique_review_part(
            parts,
            normalized_parts,
            "The external communication draft can be modified and submitted in this review step.",
        )
    _append_unique_review_part(parts, normalized_parts, f"Approval would resume to '{resume_state.value}'.")
    return " ".join(parts) if parts else None


# Resolve the canonical operator choices for the current review context.
def _resolve_allowed_human_review_decisions(
    *,
    state: TaskState,
) -> tuple[HumanReviewDecisionType, ...]:
    # Choices are intentionally limited to the pause boundary, not a universal control panel.
    request = state.pending_human_review_request
    reason_codes = set()
    if state.last_governance_decision is not None:
        reason_codes.update(governance_reason_code_values(state.last_governance_decision))
    if "ask_clarification_required" in reason_codes:
        return (
            HumanReviewDecisionType.ASK_CLARIFICATION,
            HumanReviewDecisionType.ABORT,
        )
    if state.last_external_execution_receipt is not None and (
        state.last_external_execution_receipt.status == ExternalExecutionStatus.INDETERMINATE
    ):
        return (
            HumanReviewDecisionType.APPROVE_CONTINUE,
            HumanReviewDecisionType.REQUEST_MORE_EVIDENCE,
            HumanReviewDecisionType.ABORT,
        )
    if {
        "judge_unsafe",
        "judge_uncertain",
        "action_revision_limit_reached",
    } & reason_codes:
        return (
            HumanReviewDecisionType.MODIFY_AND_CONTINUE,
            HumanReviewDecisionType.REPLAN,
            HumanReviewDecisionType.ABORT,
        )
    if "capability_approval_required" in reason_codes:
        return (
            HumanReviewDecisionType.APPROVE_CONTINUE,
            HumanReviewDecisionType.MODIFY_AND_CONTINUE,
            HumanReviewDecisionType.ABORT,
        )
    # Compatibility fallback for legacy checkpoint requests that predate reason-scoped actions.
    del request
    return (
        HumanReviewDecisionType.APPROVE_CONTINUE,
        HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY,
        HumanReviewDecisionType.MODIFY_AND_CONTINUE,
        HumanReviewDecisionType.REQUEST_MORE_EVIDENCE,
        HumanReviewDecisionType.REPLAN,
        HumanReviewDecisionType.ASK_CLARIFICATION,
        HumanReviewDecisionType.ABORT,
    )


# Append one review summary fragment only when it does not already appear in the summary body.
def _append_unique_review_part(
    parts: list[str],
    normalized_parts: list[str],
    part: str | None,
) -> None:
    if part is None:
        return

    normalized_part = " ".join(part.split()).strip()
    if not normalized_part:
        return

    joined_parts = " ".join(normalized_parts)
    if normalized_part in joined_parts:
        return

    parts.append(normalized_part)
    normalized_parts.append(normalized_part)


# Parse decision enum values while failing closed on malformed resume payloads.
def _parse_decision_type(value: Any) -> HumanReviewDecisionType:
    if isinstance(value, HumanReviewDecisionType):
        return value
    if not isinstance(value, str):
        raise ValueError("Human review decision_type must be a string.")

    try:
        return HumanReviewDecisionType(value)
    except ValueError as exc:
        raise ValueError(f"Unknown human review decision_type: {value}") from exc


# Recover the request id from a review resume payload.
def _extract_request_id(payload: dict[str, Any]) -> str:
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("Human review resume payload is missing request_id.")
    return " ".join(request_id.strip().split())


# Normalize optional free-text fields before storing them in TaskState.
def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Human review text fields must be strings.")
    cleaned = " ".join(value.strip().split())
    return cleaned or None


# Normalize bounded recipient lists used by typed communication edits.
def _text_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("Communication cc/bcc fields must be arrays of strings.")
    normalized = tuple(
        item
        for raw in value
        if (item := _optional_text(raw)) is not None
    )
    if len(normalized) > 20:
        raise ValueError("Communication cc/bcc fields exceed the recipient bound.")
    return normalized


# Parse a fully typed external draft from a review payload.
def _coerce_external_communication_draft(payload: Any) -> ExternalCommunicationDraft:
    if isinstance(payload, ExternalCommunicationDraft):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("External communication draft must be a dict or dataclass.")

    channel = _optional_text(payload.get("channel"))
    if channel is None:
        raise ValueError("External communication draft is missing channel.")

    body = _optional_text(payload.get("body"))
    if body is None:
        raise ValueError("External communication draft is missing body.")

    metadata = payload.get("metadata")
    if metadata is None:
        metadata_dict: dict[str, Any] = {}
    elif not isinstance(metadata, dict):
        raise ValueError("External communication draft metadata must be a dict.")
    else:
        metadata_dict = {str(key): value for key, value in metadata.items()}

    return ExternalCommunicationDraft(
        channel=channel,
        recipient=_optional_text(payload.get("recipient")),
        subject=_optional_text(payload.get("subject")),
        body=body,
        cc=_text_tuple(payload.get("cc")),
        bcc=_text_tuple(payload.get("bcc")),
        metadata=metadata_dict,
    )


# Parse the human edit wrapper used by the modify-and-continue decision.
def _coerce_external_communication_edit(payload: Any) -> HumanReviewExternalEdit:
    if isinstance(payload, HumanReviewExternalEdit):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("External communication edit must be a dict or dataclass.")

    draft_payload = payload.get("draft", payload.get("external_communication_draft"))
    if draft_payload is None:
        raise ValueError("External communication edit is missing draft.")

    return HumanReviewExternalEdit(
        draft=_coerce_external_communication_draft(draft_payload),
        note=_optional_text(payload.get("note")),
    )


# Recover an optional external edit wrapper from a review payload.
def _optional_external_communication_edit(payload: dict[str, Any]) -> HumanReviewExternalEdit | None:
    edit_payload = payload.get("external_communication_edit", payload.get("external_edit"))
    if edit_payload is None:
        return None
    return _coerce_external_communication_edit(edit_payload)


# Parse a discriminated full-payload replacement for non-communication proposals.
def _optional_external_operation_edit(
    payload: dict[str, Any],
) -> ExternalOperationPayloadEdit | None:
    edit_payload = payload.get("external_operation_edit")
    if edit_payload is None:
        return None
    if isinstance(edit_payload, ExternalOperationPayloadEdit):
        return edit_payload
    if not isinstance(edit_payload, dict):
        raise ValueError("external_operation_edit must be an object.")
    typed_payload = edit_payload.get("payload")
    if not isinstance(typed_payload, dict):
        raise ValueError("external_operation_edit is missing its typed payload.")
    payload_type = typed_payload.get("payload_type")
    if payload_type == "communication":
        operation_payload = CommunicationOperationPayload(
            channel=_optional_text(typed_payload.get("channel")) or "external_communication",
            recipient=_optional_text(typed_payload.get("recipient")),
            subject=_optional_text(typed_payload.get("subject")),
            body=_optional_text(typed_payload.get("body")) or "",
            destination_class=DestinationClass(
                typed_payload.get("destination_class", DestinationClass.UNKNOWN.value)
            ),
            cc=_text_tuple(typed_payload.get("cc")),
            bcc=_text_tuple(typed_payload.get("bcc")),
        )
    elif payload_type == "database":
        operation_payload = DatabaseOperationPayload(
            sql=str(typed_payload.get("sql") or "").strip(),
            normalized_sql=_optional_text(typed_payload.get("normalized_sql")),
            statement_class=SQLStatementClass(
                typed_payload.get("statement_class", SQLStatementClass.UNKNOWN.value)
            ),
            statement_count=int(typed_payload.get("statement_count", 0)),
            parse_valid=bool(typed_payload.get("parse_valid", False)),
            ambiguous=bool(typed_payload.get("ambiguous", True)),
            database_profile_id=str(
                typed_payload.get("database_profile_id") or "enterprise_db.read_only"
            ),
            parameters=tuple(
                DatabaseOperationParameter(
                    name=str(item.get("name") or "").strip(),
                    value=item.get("value"),
                )
                for item in typed_payload.get("parameters", [])
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ),
        )
    elif payload_type == "generic":
        raw_fields = typed_payload.get("fields", [])
        operation_payload = GenericExternalOperationPayload(
            action=str(typed_payload.get("action") or "external_action").strip(),
            target=str(typed_payload.get("target") or "unknown_target").strip(),
            fields=tuple(
                GenericPayloadField(
                    name=str(item.get("name") or "").strip(),
                    value=str(item.get("value") or "").strip(),
                )
                for item in raw_fields
                if isinstance(item, dict)
            ),
        )
    else:
        raise ValueError("Unsupported external_operation_edit payload_type.")
    return ExternalOperationPayloadEdit(
        payload=operation_payload,
        note=_optional_text(edit_payload.get("note")),
    )


# Apply a validated external communication edit and preserve it in the audit trail.
def _apply_external_communication_edit(
    state: TaskState,
    *,
    external_edit: HumanReviewExternalEdit,
) -> None:
    if state.external_communication is None:
        raise ValueError("Cannot edit external communication without a typed branch state.")
    if (
        state.external_operation_proposal is not None
        and not state.external_operation_proposal.modifiable
    ):
        raise ValueError("The current external operation proposal is not modifiable.")

    state.external_communication.draft = external_edit.draft
    state.external_communication.last_applied_edit = external_edit
    state.external_communication.edit_history.append(external_edit)
    proposal = state.external_operation_proposal
    if proposal is not None:
        # Re-run deterministic destination normalization for every changed payload.
        edited_proposal = normalize_communication_operation(
            operation_id=proposal.operation_id,
            plan_step_id=proposal.plan_step_id,
            capability_id=proposal.capability_id,
            draft=external_edit.draft,
            execution_candidate=proposal.execution_candidate,
            capability_profile_id=(
                proposal.capability_profile_id or "communication.default"
            ),
            capability_profile_version=(
                proposal.capability_profile_version or "1"
            ),
        )
        edited_proposal = _mark_human_review_normalization(edited_proposal)
        _replace_current_external_proposal(state, proposal=edited_proposal)
    _set_external_communication_phase(
        state,
        phase=ExternalCommunicationPhase.DRAFT_PROPOSAL,
        summary=(
            "Human review produced a new immutable communication proposal; "
            "the new digest must be governed again."
        ),
        review_ready=True,
    )


# Apply a typed non-communication payload replacement and force re-governance.
def _apply_external_operation_edit(
    state: TaskState,
    *,
    operation_edit: ExternalOperationPayloadEdit,
) -> None:
    proposal = state.external_operation_proposal
    if proposal is None:
        raise ValueError("Cannot modify an absent external operation proposal.")
    if not proposal.modifiable:
        raise ValueError("The current external operation proposal is not modifiable.")
    if isinstance(operation_edit.payload, DatabaseOperationPayload):
        # SQL class and effect are always recomputed from AST, never accepted from the UI.
        edited_proposal = normalize_sql_operation(
            operation_id=proposal.operation_id,
            plan_step_id=proposal.plan_step_id,
            capability_id=proposal.capability_id,
            sql=operation_edit.payload.sql,
            database_target=proposal.target.value,
            database_profile_id=(
                proposal.capability_profile_id or "enterprise_db.read_only"
            ),
            capability_profile_version=(
                proposal.capability_profile_version or "1"
            ),
            parameters=operation_edit.payload.parameters,
            execution_candidate=proposal.execution_candidate,
        )
        edited_proposal = _mark_human_review_normalization(edited_proposal)
    elif isinstance(operation_edit.payload, CommunicationOperationPayload):
        edited_proposal = normalize_communication_operation(
            operation_id=proposal.operation_id,
            plan_step_id=proposal.plan_step_id,
            capability_id=proposal.capability_id,
            draft=ExternalCommunicationDraft(
                channel=operation_edit.payload.channel,
                recipient=operation_edit.payload.recipient,
                subject=operation_edit.payload.subject,
                body=operation_edit.payload.body,
                cc=operation_edit.payload.cc,
                bcc=operation_edit.payload.bcc,
            ),
            execution_candidate=proposal.execution_candidate,
            capability_profile_id=(
                proposal.capability_profile_id or "communication.default"
            ),
            capability_profile_version=(
                proposal.capability_profile_version or "1"
            ),
        )
        edited_proposal = _mark_human_review_normalization(edited_proposal)
    else:
        edited_proposal = replace_external_operation_payload(
            proposal,
            payload=operation_edit.payload,
        )
    _replace_current_external_proposal(state, proposal=edited_proposal)


# Preserve the deterministic normalizer identity while recording the human edit boundary.
def _mark_human_review_normalization(proposal):
    return replace(
        proposal,
        facts_provenance=replace(
            proposal.facts_provenance,
            source_kind="human_review_edit",
            fact_notes=(
                *proposal.facts_provenance.fact_notes,
                "payload_renormalized",
            ),
        ),
    )


# Replace the active immutable proposal while retaining both digests in history.
def _replace_current_external_proposal(
    state: TaskState,
    *,
    proposal,
) -> None:
    state.external_operation_proposal = proposal
    state.external_operation_history.append(proposal)
    if state.actor_result is not None:
        state.actor_result = replace(
            state.actor_result,
            external_operation_proposal=proposal,
            external_impact=proposal.boundary == OperationBoundary.GOVERNED_EXTERNAL,
        )


# Bind approval to request, current proposal, and decision digests simultaneously.
def _validate_digest_bound_approval(
    *,
    state: TaskState,
    decision: HumanReviewDecision,
    pending_request: HumanReviewRequest,
) -> None:
    proposal = state.external_operation_proposal
    if proposal is None:
        return
    expected = pending_request.operation_payload_digest
    approved = decision.approved_operation_digest
    if expected is None or approved is None:
        raise ValueError("External operation approval requires an exact payload digest.")
    if approved != expected or approved != proposal.payload_digest:
        raise ValueError("External operation approval digest does not match the current proposal.")


# Keep external communication phase updates local to the typed HITL resume logic.
def _set_external_communication_phase(
    state: TaskState,
    *,
    phase: ExternalCommunicationPhase,
    summary: str,
    review_ready: bool | None = None,
) -> None:
    if state.external_communication is None:
        return
    state.external_communication.phase = phase
    if review_ready is not None:
        state.external_communication.review_ready = review_ready
    state.external_communication.last_transition_summary = summary
