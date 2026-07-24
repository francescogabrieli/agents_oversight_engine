from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mcp.types import CallToolResult, TextContent

from ..contracts import (
    CommunicationOperationPayload,
    DatabaseOperationPayload,
    ExternalExecutionErrorCode,
    ExternalExecutionReceipt,
    ExternalExecutionRequest,
    ExternalExecutionStatus,
    ExternalOperationFamily,
    GenericPayloadField,
)
from ..idempotency import ExternalExecutionLedger
from ..registry import (
    ExternalCapabilityDefinition,
    ExternalCapabilityRegistry,
    ExternalConnectorKind,
)
from .pool import MCPCallError, MCPCallTimeout, MCPConnectionPool


class MCPExternalOperationExecutor:
    # This adapter maps authorized typed requests to exact registered MCP tools.
    def __init__(
        self,
        *,
        registry: ExternalCapabilityRegistry,
        pool: MCPConnectionPool | None = None,
        ledger: ExternalExecutionLedger | None = None,
    ) -> None:
        self._registry = registry
        self._pool = pool or MCPConnectionPool(registry=registry)
        self._owns_pool = pool is None
        self._ledger = ledger or ExternalExecutionLedger(
            path=_default_execution_ledger_path()
        )
        self._argument_builders: dict[
            ExternalOperationFamily,
            Callable[[ExternalExecutionRequest], dict[str, object]],
        ] = {
            ExternalOperationFamily.COMMUNICATION: _communication_arguments,
            ExternalOperationFamily.DATABASE: _database_arguments,
        }

    # Execute one request without changing its proposal, digest, or authorization.
    def execute(self, request: ExternalExecutionRequest) -> ExternalExecutionReceipt:
        started = datetime.now(timezone.utc)
        definition = self._registry.resolve(request.capability_id)
        if definition is None:
            return _receipt(
                request=request,
                status=(
                    ExternalExecutionStatus.REJECTED_BEFORE_EXECUTION
                ),
                started=started,
                error_code=(
                    ExternalExecutionErrorCode.CAPABILITY_DISABLED
                    if request.capability_id in self._registry.capabilities
                    else ExternalExecutionErrorCode.CAPABILITY_UNKNOWN
                ),
                summary="The requested external capability is not enabled in the registry.",
            )
        server = self._registry.mcp_servers.get(definition.mcp_server_id)
        if server is None or not server.enabled:
            return _receipt(
                request=request,
                status=ExternalExecutionStatus.REJECTED_BEFORE_EXECUTION,
                started=started,
                error_code=ExternalExecutionErrorCode.CONNECTOR_UNSUPPORTED,
                summary="The registered MCP server is unavailable.",
                definition=definition,
            )
        if definition.mcp_tool_name not in server.allowed_tools:
            return _receipt(
                request=request,
                status=ExternalExecutionStatus.REJECTED_BEFORE_EXECUTION,
                started=started,
                error_code=ExternalExecutionErrorCode.TOOL_NOT_ALLOWLISTED,
                summary="The registered MCP tool is not allowlisted.",
                definition=definition,
            )
        validation_error = _validate_mapping(request=request, definition=definition)
        if validation_error is not None:
            return _receipt(
                request=request,
                status=ExternalExecutionStatus.REJECTED_BEFORE_EXECUTION,
                started=started,
                error_code=validation_error,
                summary="The external capability mapping rejected the typed request.",
                definition=definition,
            )
        try:
            arguments = self._argument_builders[definition.operation_family](request)
        except (KeyError, TypeError, ValueError):
            return _receipt(
                request=request,
                status=ExternalExecutionStatus.REJECTED_BEFORE_EXECUTION,
                started=started,
                error_code=ExternalExecutionErrorCode.INPUT_SCHEMA_INVALID,
                summary="The typed proposal is incompatible with the registered input contract.",
                definition=definition,
            )
        if not _arguments_match_schema(arguments=arguments, definition=definition):
            return _receipt(
                request=request,
                status=ExternalExecutionStatus.REJECTED_BEFORE_EXECUTION,
                started=started,
                error_code=ExternalExecutionErrorCode.INPUT_SCHEMA_INVALID,
                summary="The projected MCP arguments do not satisfy the registered schema.",
                definition=definition,
            )

        prior = self._ledger.reserve(request=request)
        if isinstance(prior, ExternalExecutionReceipt):
            return prior
        if isinstance(prior, str):
            receipt = _receipt(
                request=request,
                status=ExternalExecutionStatus.INDETERMINATE,
                started=started,
                error_code=ExternalExecutionErrorCode.OUTCOME_INDETERMINATE,
                summary=(
                    "The idempotency key is already reserved or conflicts with "
                    "another request and was not repeated."
                ),
                definition=definition,
            )
            self._ledger.store(receipt)
            return receipt
        try:
            result = self._pool.call_tool(
                server_id=definition.mcp_server_id,
                tool_name=definition.mcp_tool_name,
                arguments=arguments,
                timeout_seconds=min(
                    definition.timeout_seconds,
                    self._registry.mcp_servers[
                        definition.mcp_server_id
                    ].timeout_seconds,
                ),
            )
            receipt = _receipt_from_mcp_result(
                request=request,
                definition=definition,
                result=result,
                started=started,
            )
        except MCPCallTimeout:
            receipt = _receipt(
                request=request,
                status=ExternalExecutionStatus.INDETERMINATE,
                started=started,
                error_code=ExternalExecutionErrorCode.CONNECTOR_TIMEOUT,
                summary="The MCP call timed out and its external effect cannot be established.",
                definition=definition,
            )
        except MCPCallError as exc:
            receipt = _receipt(
                request=request,
                status=(
                    ExternalExecutionStatus.INDETERMINATE
                    if exc.outcome_may_be_indeterminate
                    else ExternalExecutionStatus.FAILED
                ),
                started=started,
                error_code=(
                    ExternalExecutionErrorCode.OUTCOME_INDETERMINATE
                    if exc.outcome_may_be_indeterminate
                    else ExternalExecutionErrorCode.CONNECTOR_FAILURE
                ),
                summary=str(exc),
                retryable=not exc.outcome_may_be_indeterminate,
                definition=definition,
            )
        self._ledger.store(receipt)
        return receipt

    # Release owned MCP sessions and child processes.
    def close(self) -> None:
        if self._owns_pool:
            self._pool.close()


# Verify registry mapping facts without reinterpreting governance.
def _validate_mapping(
    *,
    request: ExternalExecutionRequest,
    definition: ExternalCapabilityDefinition,
) -> ExternalExecutionErrorCode | None:
    proposal = request.proposal
    if definition.connector_kind != ExternalConnectorKind.MCP:
        return ExternalExecutionErrorCode.CONNECTOR_UNSUPPORTED
    if not definition.mcp_server_id or not definition.mcp_tool_name:
        return ExternalExecutionErrorCode.CONNECTOR_UNSUPPORTED
    if proposal.operation_family != definition.operation_family:
        return ExternalExecutionErrorCode.INPUT_SCHEMA_INVALID
    if (
        proposal.operation_family == ExternalOperationFamily.COMMUNICATION
        and proposal.normalized_effect != definition.normalized_effect
    ):
        return ExternalExecutionErrorCode.INPUT_SCHEMA_INVALID
    return None


# Project a typed communication payload into the exact MCP input shape.
def _communication_arguments(request: ExternalExecutionRequest) -> dict[str, object]:
    payload = request.proposal.payload
    if not isinstance(payload, CommunicationOperationPayload):
        raise TypeError("Communication capability requires communication payload.")
    if not payload.recipient or not payload.subject or not payload.body:
        raise ValueError("Email recipient, subject, and body are required.")
    return {
        "recipient": payload.recipient,
        "subject": payload.subject,
        "body": payload.body,
        "cc": list(payload.cc),
        "bcc": list(payload.bcc),
        "idempotency_key": request.idempotency_key,
    }


# Project a typed database payload without concatenating parameters or accepting a DSN.
def _database_arguments(request: ExternalExecutionRequest) -> dict[str, object]:
    payload = request.proposal.payload
    if not isinstance(payload, DatabaseOperationPayload):
        raise TypeError("Database capability requires database payload.")
    if not payload.parse_valid or payload.ambiguous or payload.statement_count != 1:
        raise ValueError("Database capability requires one unambiguous parsed statement.")
    return {
        "database_profile_id": payload.database_profile_id,
        "statement": payload.normalized_sql or payload.sql,
        "parameters": {item.name: item.value for item in payload.parameters},
        "idempotency_key": request.idempotency_key,
    }


# Validate the compact registered required/optional field contract.
def _arguments_match_schema(
    *,
    arguments: dict[str, object],
    definition: ExternalCapabilityDefinition,
) -> bool:
    required = {
        str(item) for item in definition.input_schema.get("required", [])
    }
    optional = {
        str(item) for item in definition.input_schema.get("optional", [])
    }
    keys = set(arguments)
    if not (required.issubset(keys) and keys.issubset(required | optional)):
        return False
    properties = definition.input_schema.get("properties", {})
    if not isinstance(properties, dict):
        return False
    return all(
        _argument_matches_constraint(
            value=value,
            constraint=properties.get(name, {}),
        )
        for name, value in arguments.items()
    )


# Apply the compact configuration-driven input constraints before MCP invocation.
def _argument_matches_constraint(
    *,
    value: object,
    constraint: object,
) -> bool:
    if not isinstance(constraint, dict):
        return False
    expected_type = constraint.get("type")
    if expected_type == "string":
        return bool(
            isinstance(value, str)
            and len(value) >= int(constraint.get("min_length", 0))
            and len(value) <= int(constraint.get("max_length", 1_000_000))
        )
    if expected_type == "array":
        return bool(
            isinstance(value, list)
            and len(value) <= int(constraint.get("max_items", 1_000_000))
            and (
                constraint.get("item_type") != "string"
                or all(isinstance(item, str) for item in value)
            )
        )
    if expected_type == "object":
        return bool(
            isinstance(value, dict)
            and len(value) <= int(constraint.get("max_properties", 1_000_000))
        )
    return False


# Parse a closed server response from structured content or the SDK text fallback.
def _receipt_from_mcp_result(
    *,
    request: ExternalExecutionRequest,
    definition: ExternalCapabilityDefinition,
    result: CallToolResult,
    started: datetime,
) -> ExternalExecutionReceipt:
    payload = result.structuredContent
    if payload is None:
        text_items = [
            item.text
            for item in result.content
            if isinstance(item, TextContent)
        ]
        if len(text_items) == 1:
            try:
                candidate = json.loads(text_items[0])
            except json.JSONDecodeError:
                candidate = None
            payload = candidate if isinstance(candidate, dict) else None
    if result.isError or not isinstance(payload, dict):
        return _receipt(
            request=request,
            status=ExternalExecutionStatus.FAILED,
            started=started,
            error_code=ExternalExecutionErrorCode.CONNECTOR_RESPONSE_INVALID,
            summary="The MCP server returned an invalid execution response.",
            definition=definition,
        )
    required = {
        "schema_version",
        "status",
        "external_reference",
        "provider",
        "timestamp",
        "summary",
        "error_code",
        "retryable",
        "metadata",
    }
    if (
        set(payload) != required
        or payload.get("schema_version") != definition.output_schema_version
        or not isinstance(payload.get("metadata"), dict)
        or not isinstance(payload.get("retryable"), bool)
    ):
        return _receipt(
            request=request,
            status=ExternalExecutionStatus.FAILED,
            started=started,
            error_code=ExternalExecutionErrorCode.CONNECTOR_RESPONSE_INVALID,
            summary="The MCP server response does not match the registered output contract.",
            definition=definition,
        )
    status_map = {
        "accepted": ExternalExecutionStatus.EXECUTED,
        "rejected": ExternalExecutionStatus.REJECTED_BEFORE_EXECUTION,
        "failed": ExternalExecutionStatus.FAILED,
        "indeterminate": ExternalExecutionStatus.INDETERMINATE,
    }
    status = status_map.get(str(payload.get("status")))
    if status is None:
        return _receipt(
            request=request,
            status=ExternalExecutionStatus.FAILED,
            started=started,
            error_code=ExternalExecutionErrorCode.CONNECTOR_RESPONSE_INVALID,
            summary="The MCP server returned an unknown execution status.",
            definition=definition,
        )
    return _receipt(
        request=request,
        status=status,
        started=started,
        error_code=(
            ExternalExecutionErrorCode.NONE
            if status == ExternalExecutionStatus.EXECUTED
            else ExternalExecutionErrorCode.OUTCOME_INDETERMINATE
            if status == ExternalExecutionStatus.INDETERMINATE
            else ExternalExecutionErrorCode.CONNECTOR_FAILURE
        ),
        summary=str(payload.get("summary", ""))[:1000],
        retryable=bool(payload["retryable"]) and status == ExternalExecutionStatus.FAILED,
        external_reference=(
            str(payload["external_reference"])
            if payload.get("external_reference")
            else None
        ),
        definition=definition,
        metadata={
            "provider": str(payload.get("provider", "")),
            "server_error_code": str(payload.get("error_code") or ""),
            **{
                str(key): str(value)
                for key, value in payload["metadata"].items()
            },
        },
    )


# Construct one audit-safe receipt using only bounded connector metadata.
def _receipt(
    *,
    request: ExternalExecutionRequest,
    status: ExternalExecutionStatus,
    started: datetime,
    error_code: ExternalExecutionErrorCode,
    summary: str,
    retryable: bool = False,
    external_reference: str | None = None,
    definition: ExternalCapabilityDefinition | None = None,
    metadata: dict[str, str] | None = None,
) -> ExternalExecutionReceipt:
    return ExternalExecutionReceipt(
        operation_id=request.operation_id,
        capability_id=request.capability_id,
        payload_digest=request.current_payload_digest,
        status=status,
        idempotency_key=request.idempotency_key,
        started_at=started,
        completed_at=datetime.now(timezone.utc),
        external_reference=external_reference,
        summary=summary[:1000],
        error_code=error_code,
        retryable=retryable,
        connector_kind="mcp" if definition is not None else None,
        connector_id=(
            f"mcp://{definition.mcp_server_id}/{definition.mcp_tool_name}"
            if definition is not None
            else None
        ),
        server_id=definition.mcp_server_id if definition is not None else None,
        tool_name=definition.mcp_tool_name if definition is not None else None,
        connector_metadata=tuple(
            GenericPayloadField(name=key[:80], value=value[:500])
            for key, value in sorted((metadata or {}).items())
        ),
        attempt_number=_attempt_number(request.attempt_scope),
    )


# Derive the bounded attempt number from the stable attempt scope.
def _attempt_number(attempt_scope: str) -> int:
    if attempt_scope.startswith("retry-"):
        try:
            return max(1, int(attempt_scope.split("-", 1)[1]) + 1)
        except ValueError:
            return 1
    return 1


# Keep the local ledger under runtime artifacts unless explicitly injected.
def _default_execution_ledger_path() -> Path:
    return (
        Path(__file__).resolve().parents[4]
        / "artifacts"
        / "runtime"
        / "external"
        / "execution_ledger.sqlite3"
    )


__all__ = ["MCPExternalOperationExecutor"]
