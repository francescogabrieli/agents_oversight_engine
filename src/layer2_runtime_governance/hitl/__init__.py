from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from langgraph.types import interrupt

from json_serialization import to_jsonable as _to_jsonable
from layer1_intake import ExecutionIntent
from layer3_tool_bus_external_capabilities.external_operations import (
    CommunicationOperationPayload,
    DatabaseOperationPayload,
    ExternalExecutionAuthorizationSource,
    ExternalExecutionRequest,
    ExternalExecutionStatus,
    ExternalOperationEffect,
    ExternalOperationPayloadEdit,
    OperationBoundary,
    build_external_execution_request,
    normalize_communication_operation,
    normalize_sql_operation,
    replace_external_operation_payload,
)
from layer4_state_evidence_observability.oversight.contracts import (
    DynamicPolicyAssessment,
    OversightAssessment,
)

from ..actor_contract import ActorResultType
from ..domain import (
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    HumanReviewRequest,
    RuntimeFlag,
    RuntimeState,
    StepRecord,
    TaskState,
    TerminationReason,
    build_evidence_selection_digest,
    build_evidence_snapshot_manifest,
    coerce_human_review_decision_payload,
    digest_json,
    normalize_task_state_serialization,
    set_external_communication_phase,
)
from ..domain.step_recovery import restore_actor_step_for_more_evidence
from ..governance_components import GovernanceReasonCode, governance_reason_code_values


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

    # Reconciliation asks a different question from the approval gate that preceded it:
    # not "may this be sent" but "did the send actually happen". Inheriting the prior
    # governance reason would show the operator the wrong question for the same digest.
    reconciliation = _is_indeterminate_reconciliation(state)
    reason_codes: list[str] = []
    if reconciliation:
        reason_codes.append(
            GovernanceReasonCode.EXTERNAL_EXECUTION_RECONCILIATION_REQUIRED.value
        )
    else:
        if governance_decision is not None:
            reason_codes.extend(governance_reason_code_values(governance_decision))
        if dynamic_policy_assessment is not None:
            reason_codes.extend(
                code.value for code in dynamic_policy_assessment.reason_codes
            )
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
        actor_summary=state.actor_result.summary if state.actor_result is not None else None,
        actor_blocking_reason=(
            state.actor_result.blocking_reason if state.actor_result is not None else None
        ),
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
            judge_assessment=judge_assessment,
            resume_state=resume_state,
            external_communication_draft=external_draft,
            reconciliation_receipt=(
                state.last_external_execution_receipt if reconciliation else None
            ),
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
    human_review_decision = coerce_human_review_decision_payload(
        decision_payload,
        pending_request=pending_request,
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

    # Enforce the immutable request allowlist inside the domain transition as well as the UI adapter.
    if human_review_decision.decision_type not in pending_request.allowed_decisions:
        raise ValueError(
            "This review request cannot authorize the supplied human decision."
        )

    if human_review_decision.decision_type == HumanReviewDecisionType.APPROVE_CONTINUE:
        if "evidence_request_limit_reached" in _review_reason_codes(
            state=state,
            request=pending_request,
        ):
            raise ValueError(
                "An Evidence ceiling cannot be bypassed by an unbound continue approval."
            )
        _record_anchoring_adjudication(state=state, pending_request=pending_request)
        if _is_non_authorizing_boundary_release(
            decision=human_review_decision,
            pending_request=pending_request,
        ):
            # This approval releases only the reviewed boundary (a trajectory ceiling,
            # or a settled anchoring concern). It never authorizes a coincident or
            # stale external-operation proposal - that still needs its own
            # capability_approval_required gate, unaffected by this decision.
            state.current_external_execution_request = None
            state.approved_external_operation_digest = None
            state.current_state = pending_request.resume_state
            return
        _validate_approval_scope(
            state=state,
            pending_request=pending_request,
        )
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
            set_external_communication_phase(
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
        _authorize_governed_external_execution(
            state=state,
            decision=human_review_decision,
            transition_summary=(
                "Human review authorized the exact proposal digest for external execution."
            ),
        )
        return

    if human_review_decision.decision_type == HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY:
        if "evidence_request_limit_reached" in _review_reason_codes(
            state=state,
            request=pending_request,
        ):
            raise ValueError(
                "An Evidence ceiling cannot be bypassed by a read-only continue approval."
            )
        _record_anchoring_adjudication(state=state, pending_request=pending_request)
        _validate_approval_scope(
            state=state,
            pending_request=pending_request,
        )
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
            set_external_communication_phase(
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
        _authorize_governed_external_execution(
            state=state,
            decision=human_review_decision,
            transition_summary=(
                "Human review authorized the exact read-only proposal digest."
            ),
        )
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
        _start_fresh_evidence_request_episode(state)
        set_external_communication_phase(
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
        _start_fresh_evidence_request_episode(state)
        state.adjudicated_evidence_anchoring_digest = None
        set_external_communication_phase(
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
        set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED,
            summary="Human review requested clarification before the external communication branch can continue.",
            review_ready=True,
        )
        state.current_tool_request = None
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        return

    if human_review_decision.decision_type == HumanReviewDecisionType.ABORT:
        set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.BLOCKED,
            summary="Human review aborted the external communication branch.",
            review_ready=False,
        )
        state.current_tool_request = None
        state.last_tool_result = None
        state.termination_reason = TerminationReason.HUMAN_ABORT
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


# Apply only the identical post-validation authorization mutation shared by approvals.
def _authorize_governed_external_execution(
    *,
    state: TaskState,
    decision: HumanReviewDecision,
    transition_summary: str,
) -> None:
    set_external_communication_phase(
        state,
        phase=ExternalCommunicationPhase.APPROVED_FOR_EXECUTION,
        summary=transition_summary,
        review_ready=True,
    )
    state.approved_external_operation_digest = decision.approved_operation_digest
    state.current_external_execution_request = _build_human_authorized_request(
        state=state,
        decision=decision,
    )
    state.current_state = RuntimeState.READY_EXTERNAL_EXECUTION


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
# State the unresolved delivery outcome in the operator's own terms, with the concrete
# connector evidence needed to check the external system before deciding.
def _reconciliation_review_text(receipt: Any) -> str:
    parts = [
        "The external operation was attempted but its outcome could not be confirmed, "
        "so the runtime stopped instead of retrying and risking a duplicate effect.",
    ]
    summary = getattr(receipt, "summary", None)
    if summary:
        parts.append(str(summary))
    reference = getattr(receipt, "external_reference", None)
    if reference:
        parts.append(f"Connector reference: {reference}.")
    error_code = getattr(receipt, "error_code", None)
    if error_code:
        parts.append(f"Connector error code: {error_code}.")
    parts.append(
        "Verify the external system, then confirm the operation as terminal or abort. "
        "This decision records the outcome and never authorizes a new execution."
    )
    return " ".join(parts)


def _build_review_summary(
    *,
    governance_decision: Any,
    dynamic_policy_assessment: DynamicPolicyAssessment | OversightAssessment | None,
    judge_assessment: Any,
    resume_state: RuntimeState,
    external_communication_draft: ExternalCommunicationDraft | None = None,
    reconciliation_receipt: Any = None,
) -> str | None:
    parts: list[str] = []
    normalized_parts: list[str] = []
    # A reconciliation summary must describe the unresolved outcome, not the approval
    # that authorized the attempt; reusing the governance text asks the wrong question.
    if reconciliation_receipt is not None:
        _append_unique_review_part(
            parts,
            normalized_parts,
            _reconciliation_review_text(reconciliation_receipt),
        )
        _append_unique_review_part(
            parts,
            normalized_parts,
            f"A permitted review decision would resume to '{resume_state.value}'.",
        )
        return " ".join(parts)
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
    if judge_assessment is not None and getattr(judge_assessment, "summary", None):
        _append_unique_review_part(parts, normalized_parts, judge_assessment.summary)
    if external_communication_draft is not None:
        _append_unique_review_part(
            parts,
            normalized_parts,
            "The external communication draft can be modified and submitted in this review step.",
        )
    _append_unique_review_part(
        parts,
        normalized_parts,
        f"A permitted review decision would resume to '{resume_state.value}'.",
    )
    return " ".join(parts) if parts else None


# Resolve the canonical operator choices for the current review context.
def _resolve_allowed_human_review_decisions(
    *,
    state: TaskState,
) -> tuple[HumanReviewDecisionType, ...]:
    # Choices are intentionally limited to the pause boundary, not a universal control panel.
    request = state.pending_human_review_request
    reason_codes = _review_reason_codes(state=state, request=request)
    if (
        "ask_clarification_required" in reason_codes
        or RuntimeFlag.CLARIFICATION_REQUIRED in state.runtime_flags
    ):
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
    if "evidence_request_limit_reached" in reason_codes:
        return (
            HumanReviewDecisionType.REQUEST_MORE_EVIDENCE,
            HumanReviewDecisionType.REPLAN,
            HumanReviewDecisionType.ABORT,
        )
    if "evidence_anchoring_requires_review" in reason_codes:
        # Approving here settles the flagged values as fine, releasing only the
        # anchoring boundary; it is not an authorization to execute the pending
        # external operation, which keeps its own separate approval gate.
        return (
            HumanReviewDecisionType.APPROVE_CONTINUE,
            HumanReviewDecisionType.MODIFY_AND_CONTINUE,
            HumanReviewDecisionType.REPLAN,
            HumanReviewDecisionType.ABORT,
        )
    if {
        "judge_unsafe",
        "judge_uncertain",
        "action_revision_limit_reached",
        "unchanged_candidate_resubmission",
        "rejected_candidate_resubmitted",
        "unresolvable_revision",
        "external_low_actor_self_confidence",
    } & reason_codes:
        return (
            HumanReviewDecisionType.MODIFY_AND_CONTINUE,
            HumanReviewDecisionType.REPLAN,
            HumanReviewDecisionType.ABORT,
        )
    if human_review_reasons_authorize_external_execution(reason_codes):
        return (
            HumanReviewDecisionType.APPROVE_CONTINUE,
            HumanReviewDecisionType.MODIFY_AND_CONTINUE,
            HumanReviewDecisionType.ABORT,
        )
    if "replan_request_stalled" in reason_codes:
        return (
            HumanReviewDecisionType.REPLAN,
            HumanReviewDecisionType.ABORT,
        )
    if "replan_ceiling_reached" in reason_codes:
        return (
            HumanReviewDecisionType.APPROVE_CONTINUE,
            HumanReviewDecisionType.REPLAN,
            HumanReviewDecisionType.ABORT,
        )
    if "consecutive_tool_failures" in reason_codes:
        return (
            HumanReviewDecisionType.APPROVE_CONTINUE,
            HumanReviewDecisionType.REPLAN,
            HumanReviewDecisionType.ABORT,
        )
    if {
        "policy_version_mismatch",
        "required_pillar_unavailable",
        "judge_required_unavailable",
        "opa_failure_fail_closed",
        "provider_failures_repeated",
        "repeated_recovery",
    } & reason_codes:
        return (
            HumanReviewDecisionType.REPLAN,
            HumanReviewDecisionType.ABORT,
        )
    # Unknown and legacy external pauses remain non-authorizing by default.
    if _is_governed_external_proposal(state):
        return (
            HumanReviewDecisionType.MODIFY_AND_CONTINUE,
            HumanReviewDecisionType.REPLAN,
            HumanReviewDecisionType.ABORT,
        )
    return (
        HumanReviewDecisionType.APPROVE_CONTINUE,
        HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY,
        HumanReviewDecisionType.REPLAN,
        HumanReviewDecisionType.ABORT,
    )


# Reject forged or legacy approval choices that do not carry explicit approval authority.
def _validate_approval_scope(
    *,
    state: TaskState,
    pending_request: HumanReviewRequest,
) -> None:
    if _is_indeterminate_reconciliation(state):
        return
    if not _is_governed_external_proposal(state):
        return
    if state.execution_intent == ExecutionIntent.PROPOSAL_ONLY:
        return
    reason_codes = _review_reason_codes(state=state, request=pending_request)
    if not human_review_reasons_authorize_external_execution(reason_codes):
        raise ValueError(
            "This review reason cannot authorize an external operation. "
            "Only capability_approval_required may approve the exact digest."
        )


# Resolve reason codes from both canonical governance state and the immutable review request.
def _review_reason_codes(
    *,
    state: TaskState,
    request: HumanReviewRequest | None,
) -> set[str]:
    reason_codes = set(request.reason_codes if request is not None else ())
    if state.last_governance_decision is not None:
        reason_codes.update(
            governance_reason_code_values(state.last_governance_decision)
        )
    return reason_codes


# Grant execution authority only to an uncontaminated capability-approval gate.
def human_review_reasons_authorize_external_execution(
    reason_codes: set[str] | tuple[str, ...],
) -> bool:
    reasons = set(reason_codes)
    blocking_reasons = {
        "judge_unsafe",
        "judge_uncertain",
        "action_revision_limit_reached",
        "unchanged_candidate_resubmission",
        "rejected_candidate_resubmitted",
        "unresolvable_revision",
        "external_low_actor_self_confidence",
        "replan_ceiling_reached",
        "replan_request_stalled",
        "consecutive_tool_failures",
        "action_churn",
        "evidence_request_limit_reached",
        "invalid_or_missing_selected_evidence",
        "policy_version_mismatch",
        "required_pillar_unavailable",
        "judge_required_unavailable",
        "opa_failure_fail_closed",
        "provider_failures_repeated",
        "repeated_recovery",
        # Reconciliation settles an outcome that already happened; it is never
        # authorization to invoke the connector again.
        "external_execution_reconciliation_required",
    }
    return (
        "capability_approval_required" in reasons
        and not reasons.intersection(blocking_reasons)
    )


# Start a new bounded Evidence remediation episode without erasing whole-run audit volume.
def _start_fresh_evidence_request_episode(state: TaskState) -> None:
    state.evidence_request_episode_count = 0
    state.evidence_request_episode_id = None


# A continue approval on an anchoring review settles exactly the selection reviewed.
# Binding the ruling to the selection digest stops it from carrying over to a later,
# unreviewed completion, while letting the approved one proceed instead of escalating
# again on the very facts a human already saw.
def _record_anchoring_adjudication(
    *,
    state: TaskState,
    pending_request: HumanReviewRequest,
) -> None:
    if "evidence_anchoring_requires_review" not in _review_reason_codes(
        state=state,
        request=pending_request,
    ):
        return
    candidate = state.pending_completion_candidate
    if candidate is not None and candidate.evidence_selection_digest:
        state.adjudicated_evidence_anchoring_digest = candidate.evidence_selection_digest


# Recognize an exact approval that closes only its reviewed trajectory episode.
def _is_non_authorizing_boundary_release(
    *,
    decision: HumanReviewDecision,
    pending_request: HumanReviewRequest,
) -> bool:
    return (
        decision.decision_type == HumanReviewDecisionType.APPROVE_CONTINUE
        and decision.request_id == pending_request.request_id
        and bool(
            {
                "replan_ceiling_reached",
                "consecutive_tool_failures",
                "evidence_anchoring_requires_review",
            }
            & set(pending_request.reason_codes)
        )
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
    normalized_part = normalized_part.rstrip(" ,;:")
    if not normalized_part:
        return
    if normalized_part[-1] not in ".!?":
        normalized_part += "."

    joined_parts = " ".join(normalized_parts)
    if normalized_part in joined_parts:
        return

    parts.append(normalized_part)
    normalized_parts.append(normalized_part)


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
    set_external_communication_phase(
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


# Replace the active immutable proposal; the next governance projection records its occurrence.
def _replace_current_external_proposal(
    state: TaskState,
    *,
    proposal,
) -> None:
    communication_draft = None
    materialized_output = proposal.payload_preview
    if isinstance(proposal.payload, CommunicationOperationPayload):
        existing_metadata = (
            state.external_communication.draft.metadata
            if state.external_communication is not None
            and state.external_communication.draft is not None
            else {}
        )
        communication_draft = ExternalCommunicationDraft(
            channel=proposal.payload.channel,
            recipient=proposal.payload.recipient,
            subject=proposal.payload.subject,
            body=proposal.payload.body,
            cc=proposal.payload.cc,
            bcc=proposal.payload.bcc,
            metadata=existing_metadata,
        )
        materialized_output = proposal.payload.body
        if state.external_communication is not None:
            state.external_communication.draft = communication_draft

    state.external_operation_proposal = proposal
    candidate = state.pending_completion_candidate
    if candidate is not None and candidate.step_id == proposal.plan_step_id:
        evidence_snapshot_digest = digest_json(
            build_evidence_snapshot_manifest(task_state=state)
        )
        evidence_selection_digest = build_evidence_selection_digest(
            selected_refs=candidate.evidence_refs,
            evidence_snapshot_digest=evidence_snapshot_digest,
            external_operation_digest=proposal.payload_digest,
        )
        # A human edit creates a new immutable candidate bound to the new payload,
        # while preserving the original candidate identity and evidence attribution.
        state.pending_completion_candidate = replace(
            candidate,
            output_text=materialized_output,
            external_operation_id=proposal.operation_id,
            external_operation_digest=proposal.payload_digest,
            evidence_snapshot_digest=evidence_snapshot_digest,
            evidence_selection_digest=evidence_selection_digest,
        )
    if state.actor_result is not None:
        state.actor_result = replace(
            state.actor_result,
            output_text=materialized_output,
            external_communication_draft=(
                communication_draft
                if communication_draft is not None
                else state.actor_result.external_communication_draft
            ),
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
