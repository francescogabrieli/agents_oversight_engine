from __future__ import annotations

from datetime import datetime, timezone

from layer1_intake import ExecutionIntent

from .contracts import (
    ExternalExecutionAuthorizationSource,
    ExternalExecutionErrorCode,
    ExternalExecutionReceipt,
    ExternalExecutionRequest,
    ExternalExecutionStatus,
    ExternalOperationExecutorPort,
    ExternalOperationProposal,
)


class DeferredExternalOperationExecutor:
    # Fail closed when no configured real external backend is available.
    def execute(
        self,
        request: ExternalExecutionRequest | None = None,
        *,
        proposal: ExternalOperationProposal | None = None,
        approved_payload_digest: str | None = None,
    ) -> ExternalExecutionReceipt:
        resolved_request = request or _legacy_request(
            proposal=proposal,
            approved_payload_digest=approved_payload_digest,
        )
        if (
            resolved_request.current_payload_digest
            != resolved_request.authorized_payload_digest
        ):
            raise ValueError("External execution approval digest does not match the proposal.")
        now = datetime.now(timezone.utc)
        return ExternalExecutionReceipt(
            operation_id=resolved_request.operation_id,
            capability_id=resolved_request.capability_id,
            payload_digest=resolved_request.current_payload_digest,
            status=ExternalExecutionStatus.REJECTED_BEFORE_EXECUTION,
            idempotency_key=resolved_request.idempotency_key,
            started_at=now,
            completed_at=now,
            summary=(
                "External execution was rejected because no production side-effect "
                "backend is configured."
            ),
            error_code=ExternalExecutionErrorCode.CONNECTOR_UNSUPPORTED,
            retryable=False,
        )


# Adapt the retired keyword call only for import/backward compatibility.
def _legacy_request(
    *,
    proposal: ExternalOperationProposal | None,
    approved_payload_digest: str | None,
) -> ExternalExecutionRequest:
    if proposal is None or approved_payload_digest is None:
        raise ValueError("Deferred execution requires a typed request.")
    return ExternalExecutionRequest(
        operation_id=proposal.operation_id,
        capability_id=proposal.capability_id,
        proposal=proposal,
        current_payload_digest=proposal.payload_digest,
        authorized_payload_digest=approved_payload_digest,
        execution_intent=ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
        idempotency_key=f"legacy:{proposal.operation_id}:{proposal.payload_digest}",
        authorization_source=ExternalExecutionAuthorizationSource.HUMAN_REVIEW,
    )


__all__ = ["DeferredExternalOperationExecutor", "ExternalOperationExecutorPort"]
