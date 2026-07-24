from __future__ import annotations

import hashlib

from layer1_intake import ExecutionIntent

from .contracts import (
    ExternalExecutionAuthorizationSource,
    ExternalExecutionRequest,
    ExternalOperationProposal,
)


# Build one immutable request from an exact digest-bound authorization.
def build_external_execution_request(
    *,
    proposal: ExternalOperationProposal,
    authorized_payload_digest: str,
    execution_intent: ExecutionIntent,
    authorization_source: ExternalExecutionAuthorizationSource,
    attempt_scope: str = "initial",
    reviewer_id: str | None = None,
    governance_decision_reference: str | None = None,
) -> ExternalExecutionRequest:
    return ExternalExecutionRequest(
        operation_id=proposal.operation_id,
        capability_id=proposal.capability_id,
        proposal=proposal,
        current_payload_digest=proposal.payload_digest,
        authorized_payload_digest=authorized_payload_digest,
        execution_intent=execution_intent,
        idempotency_key=build_external_execution_idempotency_key(
            operation_id=proposal.operation_id,
            payload_digest=proposal.payload_digest,
            attempt_scope=attempt_scope,
        ),
        authorization_source=authorization_source,
        reviewer_id=reviewer_id,
        governance_decision_reference=governance_decision_reference,
        attempt_scope=attempt_scope,
    )


# Derive a stable, opaque key from operation identity, digest, and bounded attempt scope.
def build_external_execution_idempotency_key(
    *,
    operation_id: str,
    payload_digest: str,
    attempt_scope: str,
) -> str:
    material = f"{operation_id}\n{payload_digest}\n{attempt_scope}"
    return f"layer2:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


__all__ = [
    "build_external_execution_idempotency_key",
    "build_external_execution_request",
]
