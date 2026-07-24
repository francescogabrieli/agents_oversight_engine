from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable

from layer1_intake import ExecutionIntent

from ..domain import (
    ExternalCommunicationPhase,
    HumanReviewDecisionType,
    RuntimeFlag,
    RuntimeState,
    TaskState,
)
from ..external_operations import (
    ExternalCapabilityDefinition,
    ExternalCapabilityRegistry,
    ExternalConnectorKind,
    ExternalExecutionAuthorizationSource,
    ExternalExecutionErrorCode,
    ExternalExecutionReceipt,
    ExternalExecutionRequest,
    ExternalExecutionStatus,
    ExternalOperationEffect,
    ExternalOperationExecutorPort,
    OperationBoundary,
    build_external_execution_request,
)
from ..governance_components import GovernanceDirective
from ..hitl import build_human_review_request
from .actor_service import _set_external_communication_phase
from .lifecycle_service import _append_step_record


# Execute one exact, already-authorized external proposal through an injected port.
def external_execution(
    state: TaskState,
    *,
    executor_service: ExternalOperationExecutorPort,
    capability_registry: ExternalCapabilityRegistry,
    thread_id: str,
    hitl_event_callback: Callable[[TaskState], None] | None = None,
) -> TaskState:
    request = state.current_external_execution_request
    duplicate = _find_executed_receipt(
        state=state,
        idempotency_key=request.idempotency_key if request is not None else None,
    )
    if duplicate is not None:
        state.last_external_execution_receipt = duplicate
        state.current_external_execution_request = None
        state.current_state = RuntimeState.COMPLETED
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EXECUTED,
            summary="A persisted executed receipt prevented duplicate connector invocation.",
            review_ready=False,
        )
        _append_step_record(
            state,
            node_name="external_execution",
            summary="External execution replay reused its terminal idempotent receipt.",
        )
        return state
    rejection, capability = _preflight_external_execution(
        state=state,
        request=request,
        capability_registry=capability_registry,
    )
    if rejection is not None:
        _record_external_execution_receipt(state, receipt=rejection)
        state.current_state = RuntimeState.ABORTED
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EXECUTION_FAILED,
            summary=rejection.summary,
            review_ready=False,
        )
        _append_step_record(
            state,
            node_name="external_execution",
            summary=(
                "External execution was rejected before connector invocation "
                f"with code '{rejection.error_code.value}'."
            ),
        )
        return state

    if request is None or capability is None:
        raise RuntimeError("External execution preflight returned an incomplete result.")

    state.external_execution_attempt_count += 1
    receipt = executor_service.execute(request)
    _record_external_execution_receipt(state, receipt=receipt)
    _apply_external_execution_outcome(
        state=state,
        receipt=receipt,
        capability=capability,
        thread_id=thread_id,
        hitl_event_callback=hitl_event_callback,
    )
    _append_step_record(
        state,
        node_name="external_execution",
        summary=(
            "The capability-bound external executor returned "
            f"status '{receipt.status.value}'."
        ),
    )
    return state


# Validate every non-policy runtime invariant before any connector can be called.
def _preflight_external_execution(
    *,
    state: TaskState,
    request: ExternalExecutionRequest | None,
    capability_registry: ExternalCapabilityRegistry,
) -> tuple[ExternalExecutionReceipt | None, ExternalCapabilityDefinition | None]:
    proposal = state.external_operation_proposal
    if proposal is None:
        return _rejection_receipt(
            request=request,
            state=state,
            code=ExternalExecutionErrorCode.MISSING_PROPOSAL,
            summary="No current external operation proposal exists.",
        ), None
    if state.execution_intent != ExecutionIntent.EXECUTE_AFTER_GOVERNANCE:
        return _rejection_receipt(
            request=request,
            state=state,
            code=ExternalExecutionErrorCode.INVALID_EXECUTION_INTENT,
            summary="The task intent permits a proposal only.",
        ), None
    if not proposal.execution_candidate:
        return _rejection_receipt(
            request=request,
            state=state,
            code=ExternalExecutionErrorCode.NOT_EXECUTION_CANDIDATE,
            summary="The current proposal is not an execution candidate.",
        ), None
    if request is None or not _has_exact_authorization(state=state, request=request):
        return _rejection_receipt(
            request=request,
            state=state,
            code=ExternalExecutionErrorCode.AUTHORIZATION_MISSING,
            summary="No valid digest-bound authorization exists for this request.",
        ), None
    if (
        request.proposal != proposal
        or request.current_payload_digest != proposal.payload_digest
        or request.authorized_payload_digest != proposal.payload_digest
    ):
        return _rejection_receipt(
            request=request,
            state=state,
            code=ExternalExecutionErrorCode.DIGEST_MISMATCH,
            summary="The authorized digest does not match the current payload.",
        ), None

    configured = capability_registry.capabilities.get(request.capability_id)
    if configured is None:
        return _rejection_receipt(
            request=request,
            state=state,
            code=ExternalExecutionErrorCode.CAPABILITY_UNKNOWN,
            summary="The requested external capability is not registered.",
        ), None
    if not configured.enabled:
        return _rejection_receipt(
            request=request,
            state=state,
            code=ExternalExecutionErrorCode.CAPABILITY_DISABLED,
            summary="The requested external capability is disabled.",
        ), None
    server = capability_registry.mcp_servers.get(configured.mcp_server_id)
    if (
        configured.connector_kind != ExternalConnectorKind.MCP
        or server is None
        or not server.enabled
    ):
        return _rejection_receipt(
            request=request,
            state=state,
            code=ExternalExecutionErrorCode.CONNECTOR_UNSUPPORTED,
            summary="The registered MCP connector is unavailable.",
        ), None
    if configured.mcp_tool_name not in server.allowed_tools:
        return _rejection_receipt(
            request=request,
            state=state,
            code=ExternalExecutionErrorCode.TOOL_NOT_ALLOWLISTED,
            summary="The configured MCP tool is not allowlisted for this server.",
        ), None
    if (
        proposal.boundary != OperationBoundary.GOVERNED_EXTERNAL
        or configured.operation_boundary != proposal.boundary
        or configured.operation_family != proposal.operation_family
        or proposal.normalized_effect not in configured.allowed_normalized_effects
    ):
        return _rejection_receipt(
            request=request,
            state=state,
            code=ExternalExecutionErrorCode.INPUT_SCHEMA_INVALID,
            summary="The proposal facts do not match the registered capability boundary.",
        ), None
    if (
        state.execution_policy.read_only_mode
        or RuntimeFlag.READ_ONLY_EXECUTION in state.runtime_flags
    ) and proposal.normalized_effect != ExternalOperationEffect.READ:
        return _rejection_receipt(
            request=request,
            state=state,
            code=ExternalExecutionErrorCode.READ_ONLY_VIOLATION,
            summary="The proposal would violate the active read-only envelope.",
        ), None
    if not request.idempotency_key.strip():
        return _rejection_receipt(
            request=request,
            state=state,
            code=ExternalExecutionErrorCode.IDEMPOTENCY_KEY_MISSING,
            summary="The external execution request has no idempotency key.",
        ), None
    return None, configured


# Find an already-completed idempotency key without creating a second receipt.
def _find_executed_receipt(
    *,
    state: TaskState,
    idempotency_key: str | None,
) -> ExternalExecutionReceipt | None:
    if not idempotency_key:
        return None
    return next(
        (
            receipt
            for receipt in reversed(state.external_execution_history)
            if receipt.idempotency_key == idempotency_key
            and receipt.status == ExternalExecutionStatus.EXECUTED
        ),
        None,
    )


# Check authorization facts without recomputing or relaxing the OPA directive.
def _has_exact_authorization(
    *,
    state: TaskState,
    request: ExternalExecutionRequest,
) -> bool:
    if request.execution_intent != ExecutionIntent.EXECUTE_AFTER_GOVERNANCE:
        return False
    if request.authorization_source == ExternalExecutionAuthorizationSource.OPA:
        decision = state.last_governance_decision
        return bool(
            decision is not None
            and decision.directive
            in {GovernanceDirective.CONTINUE, GovernanceDirective.CONTINUE_READ_ONLY}
            and decision.evaluated_operation_digest == request.authorized_payload_digest
        )
    if request.authorization_source == ExternalExecutionAuthorizationSource.HUMAN_REVIEW:
        decision = state.last_human_review_decision
        return bool(
            decision is not None
            and decision.decision_type
            in {
                HumanReviewDecisionType.APPROVE_CONTINUE,
                HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY,
            }
            and decision.approved_operation_digest == request.authorized_payload_digest
        )
    return False


# Apply one connector receipt without treating adapter output as policy authority.
def _apply_external_execution_outcome(
    *,
    state: TaskState,
    receipt: ExternalExecutionReceipt,
    capability: ExternalCapabilityDefinition,
    thread_id: str,
    hitl_event_callback: Callable[[TaskState], None] | None,
) -> None:
    if receipt.status == ExternalExecutionStatus.EXECUTED:
        state.current_external_execution_request = None
        state.current_state = RuntimeState.COMPLETED
        _set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.EXECUTED,
            summary="The authorized external operation completed and returned a typed receipt.",
            review_ready=False,
        )
        return
    if receipt.status == ExternalExecutionStatus.INDETERMINATE:
        _route_indeterminate_to_human_reconciliation(
            state=state,
            thread_id=thread_id,
            hitl_event_callback=hitl_event_callback,
        )
        return
    if (
        receipt.status == ExternalExecutionStatus.FAILED
        and receipt.retryable
        and state.external_execution_attempt_count < capability.max_attempts
    ):
        current = state.current_external_execution_request
        proposal = state.external_operation_proposal
        if current is None or proposal is None:
            state.current_state = RuntimeState.ABORTED
            return
        retry_scope = f"retry-{state.external_execution_attempt_count}"
        state.current_external_execution_request = build_external_execution_request(
            proposal=proposal,
            authorized_payload_digest=current.authorized_payload_digest,
            execution_intent=state.execution_intent,
            authorization_source=current.authorization_source,
            attempt_scope=retry_scope,
            reviewer_id=current.reviewer_id,
            governance_decision_reference=current.governance_decision_reference,
        )
        state.current_state = RuntimeState.READY_EXTERNAL_EXECUTION
        return

    state.current_external_execution_request = None
    state.current_state = RuntimeState.ABORTED
    _set_external_communication_phase(
        state,
        phase=ExternalCommunicationPhase.EXECUTION_FAILED,
        summary=receipt.summary or "The external connector failed deterministically.",
        review_ready=False,
    )


# Pause an unknown-outcome operation for audit-safe human reconciliation without retry.
def _route_indeterminate_to_human_reconciliation(
    *,
    state: TaskState,
    thread_id: str,
    hitl_event_callback: Callable[[TaskState], None] | None,
) -> None:
    if RuntimeFlag.EXTERNAL_EXECUTION_RECONCILIATION_REQUIRED not in state.runtime_flags:
        state.runtime_flags.append(
            RuntimeFlag.EXTERNAL_EXECUTION_RECONCILIATION_REQUIRED
        )
    state.current_external_execution_request = None
    state.human_review_resume_state = RuntimeState.COMPLETED
    state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
    _set_external_communication_phase(
        state,
        phase=ExternalCommunicationPhase.INDETERMINATE,
        summary=(
            "The connector outcome is indeterminate; automatic retry is prohibited "
            "until a human reconciles the external system."
        ),
        review_ready=False,
    )
    state.pending_human_review_request = build_human_review_request(
        state=state,
        thread_id=thread_id,
    )
    if hitl_event_callback is not None:
        hitl_event_callback(deepcopy(state))


# Persist the receipt in canonical state for checkpoints, traces, and duplicate guards.
def _record_external_execution_receipt(
    state: TaskState,
    *,
    receipt: ExternalExecutionReceipt,
) -> None:
    state.last_external_execution_receipt = receipt
    state.external_execution_history.append(receipt)


# Build an audit-safe preflight receipt even when request construction was incomplete.
def _rejection_receipt(
    *,
    request: ExternalExecutionRequest | None,
    state: TaskState,
    code: ExternalExecutionErrorCode,
    summary: str,
) -> ExternalExecutionReceipt:
    now = datetime.now(timezone.utc)
    proposal = state.external_operation_proposal
    return ExternalExecutionReceipt(
        operation_id=(
            request.operation_id
            if request is not None
            else proposal.operation_id
            if proposal is not None
            else "missing"
        ),
        capability_id=(
            request.capability_id
            if request is not None
            else proposal.capability_id
            if proposal is not None
            else "missing"
        ),
        payload_digest=(
            request.current_payload_digest
            if request is not None
            else proposal.payload_digest
            if proposal is not None
            else ""
        ),
        status=ExternalExecutionStatus.REJECTED_BEFORE_EXECUTION,
        idempotency_key=request.idempotency_key if request is not None else "",
        started_at=now,
        completed_at=now,
        summary=summary,
        error_code=code,
        retryable=False,
        attempt_number=max(1, state.external_execution_attempt_count + 1),
    )


__all__ = ["external_execution"]
