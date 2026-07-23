from __future__ import annotations

from typing import Protocol

from .contracts import ExternalOperationExecutionResult, ExternalOperationProposal


class ExternalOperationExecutorPort(Protocol):
    # Implementations must execute only an already-governed, digest-bound proposal.
    def execute(
        self,
        *,
        proposal: ExternalOperationProposal,
        approved_payload_digest: str,
    ) -> ExternalOperationExecutionResult: ...


class DeferredExternalOperationExecutor:
    # Production deliberately exposes no email, database, or generic side-effect backend.
    def execute(
        self,
        *,
        proposal: ExternalOperationProposal,
        approved_payload_digest: str,
    ) -> ExternalOperationExecutionResult:
        if proposal.payload_digest != approved_payload_digest:
            raise ValueError("External execution approval digest does not match the proposal.")
        return ExternalOperationExecutionResult(
            operation_id=proposal.operation_id,
            payload_digest=proposal.payload_digest,
            executed=False,
            summary=(
                "External execution is intentionally deferred; no production side effect "
                "backend is configured."
            ),
        )


__all__ = ["DeferredExternalOperationExecutor", "ExternalOperationExecutorPort"]
