from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from .ledger import ServerIdempotencyLedger


@dataclass(frozen=True)
class DatabaseProfile:
    # Profiles select a configured target without accepting arbitrary actor DSNs.
    profile_id: str
    driver: str
    dsn_env: str
    read_only: bool
    timeout_seconds: float


# Build the database MCP server with one profile-bound statement tool.
def build_database_server(
    *,
    ledger: ServerIdempotencyLedger | None = None,
) -> FastMCP:
    resolved_ledger = ledger or ServerIdempotencyLedger()
    server = FastMCP("governed-database")

    # Expose only the profile-bound typed statement operation.
    @server.tool(name="execute_statement")
    def execute_statement(
        database_profile_id: str,
        statement: str,
        parameters: dict[str, str | int | float | bool | None],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Execute one parameterized statement against an allowlisted database profile."""
        return _execute_statement(
            database_profile_id=database_profile_id,
            statement=statement,
            parameters=parameters,
            idempotency_key=idempotency_key,
            ledger=resolved_ledger,
        )

    return server


# Validate and execute one explicit transaction against the selected profile.
def _execute_statement(
    *,
    database_profile_id: str,
    statement: str,
    parameters: dict[str, str | int | float | bool | None],
    idempotency_key: str,
    ledger: ServerIdempotencyLedger,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        profile = _load_profiles()[database_profile_id]
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return _response(
            status="rejected",
            summary="The requested database profile is unavailable or not registered.",
            error_code="database_profile_unavailable",
            retryable=False,
            timestamp=started,
        )
    try:
        expression = _single_statement(statement)
    except ValueError as exc:
        return _response(
            status="rejected",
            summary=str(exc),
            error_code="statement_invalid",
            retryable=False,
            timestamp=started,
        )
    if profile.read_only and not isinstance(expression, exp.Select):
        return _response(
            status="rejected",
            summary="The selected database profile permits read-only statements only.",
            error_code="database_profile_read_only",
            retryable=False,
            timestamp=started,
        )
    if profile.driver != "sqlite":
        return _response(
            status="rejected",
            summary="The configured database driver is not supported by this server build.",
            error_code="database_driver_unsupported",
            retryable=False,
            timestamp=started,
        )
    dsn = os.getenv(profile.dsn_env)
    if not dsn:
        return _response(
            status="rejected",
            summary="The selected database profile has no injected connection target.",
            error_code="database_dsn_missing",
            retryable=False,
            timestamp=started,
        )

    reservation = ledger.reserve(
        idempotency_key=idempotency_key,
        capability_id="database.statement.execute",
    )
    if reservation.prior_response is not None:
        return reservation.prior_response
    if reservation.prior_in_progress:
        return _response(
            status="indeterminate",
            summary="A prior database attempt is unresolved and was not repeated.",
            error_code="prior_attempt_unresolved",
            retryable=False,
            timestamp=started,
        )

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(dsn, timeout=profile.timeout_seconds)
        connection.execute(f"PRAGMA busy_timeout = {int(profile.timeout_seconds * 1000)}")
        connection.execute("BEGIN")
        cursor = connection.execute(statement, parameters)
        metadata: dict[str, Any] = {"driver": "sqlite"}
        if isinstance(expression, exp.Select):
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            raw_rows = cursor.fetchall()
            rows = [dict(zip(columns, row)) for row in raw_rows[:100]]
            metadata.update({
                "row_count": len(raw_rows),
                "columns": columns,
                "rows": rows,
            })
        else:
            metadata.update({"row_count": cursor.rowcount})
        connection.commit()
        transaction_reference = f"sqlite:{idempotency_key[:16]}"
        response = _response(
            status="accepted",
            summary="The database committed the registered single statement.",
            error_code=None,
            retryable=False,
            timestamp=started,
            external_reference=transaction_reference,
            metadata=metadata,
        )
    except sqlite3.OperationalError as exc:
        if connection is not None:
            connection.rollback()
        response = _response(
            status="failed",
            summary=f"The database rejected the transaction ({type(exc).__name__}).",
            error_code="database_operational_error",
            retryable="locked" in str(exc).lower() or "busy" in str(exc).lower(),
            timestamp=started,
        )
    except sqlite3.DatabaseError as exc:
        if connection is not None:
            connection.rollback()
        response = _response(
            status="failed",
            summary=f"The database transaction failed ({type(exc).__name__}).",
            error_code="database_transaction_failed",
            retryable=False,
            timestamp=started,
        )
    finally:
        if connection is not None:
            connection.close()
    ledger.complete(idempotency_key=idempotency_key, response=response)
    return response


# Parse exactly one statement and reject locking reads or hidden mutations.
def _single_statement(statement: str) -> exp.Expression:
    try:
        expressions = list(parse(statement.strip()))
    except (ParseError, ValueError) as exc:
        raise ValueError("The SQL statement could not be parsed.") from exc
    if len(expressions) != 1:
        raise ValueError("Exactly one SQL statement is required.")
    expression = expressions[0]
    if any(isinstance(node, exp.Lock) for node in expression.walk()):
        raise ValueError("Locking statements are not supported.")
    return expression


# Load allowlisted database profiles and resolve only environment variable names.
def _load_profiles() -> dict[str, DatabaseProfile]:
    config_value = os.getenv("LAYER2_DATABASE_PROFILES_CONFIG_PATH")
    if not config_value:
        raise ValueError("Database profiles configuration is missing.")
    payload = json.loads(Path(config_value).read_text(encoding="utf-8"))
    return {
        str(profile_id): DatabaseProfile(
            profile_id=str(profile_id),
            driver=str(item.get("driver", "")),
            dsn_env=str(item["dsn_env"]),
            read_only=bool(item.get("read_only", True)),
            timeout_seconds=float(item.get("timeout_seconds", 10)),
        )
        for profile_id, item in payload.get("profiles", {}).items()
    }


# Build the closed audit-safe response without returning database rows.
def _response(
    *,
    status: str,
    summary: str,
    error_code: str | None,
    retryable: bool,
    timestamp: datetime,
    external_reference: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "external_execution.tool_response.v1",
        "status": status,
        "external_reference": external_reference,
        "provider": "sqlite",
        "timestamp": timestamp.isoformat(),
        "summary": summary,
        "error_code": error_code,
        "retryable": retryable,
        "metadata": metadata or {},
    }


__all__ = ["DatabaseProfile", "build_database_server"]
