from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import Lock

from .contracts import (
    ExternalExecutionErrorCode,
    ExternalExecutionReceipt,
    ExternalExecutionRequest,
    ExternalExecutionStatus,
    GenericPayloadField,
)


class ExternalExecutionLedger:
    # A durable pre-call marker provides at-most-once behavior across process restarts.
    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._initialize()

    # Reserve a key or recover a prior terminal receipt.
    def reserve(
        self,
        *,
        request: ExternalExecutionRequest,
    ) -> ExternalExecutionReceipt | str | None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT operation_id, payload_digest, state, receipt_json
                FROM external_executions WHERE idempotency_key = ?
                """,
                (request.idempotency_key,),
            ).fetchone()
            if row is not None:
                connection.commit()
                if row[0] != request.operation_id or row[1] != request.current_payload_digest:
                    return "conflict"
                if row[3]:
                    return _receipt_from_json(row[3])
                return str(row[2])
            connection.execute(
                """
                INSERT INTO external_executions(
                    idempotency_key, operation_id, payload_digest, state, receipt_json
                ) VALUES (?, ?, ?, 'in_progress', NULL)
                """,
                (
                    request.idempotency_key,
                    request.operation_id,
                    request.current_payload_digest,
                ),
            )
            connection.commit()
        return None

    # Persist one final receipt for subsequent replay-safe recovery.
    def store(self, receipt: ExternalExecutionReceipt) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE external_executions
                SET state = ?, receipt_json = ?
                WHERE idempotency_key = ?
                """,
                (
                    receipt.status.value,
                    _receipt_to_json(receipt),
                    receipt.idempotency_key,
                ),
            )
            connection.commit()

    # Create the durable ledger schema once.
    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS external_executions (
                    idempotency_key TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    receipt_json TEXT
                )
                """
            )
            connection.commit()

    # Open a short transaction-scoped SQLite connection.
    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=30)


# Serialize one typed receipt without custom pickle or secret-bearing payloads.
def _receipt_to_json(receipt: ExternalExecutionReceipt) -> str:
    payload = asdict(receipt)
    for key, value in tuple(payload.items()):
        if isinstance(value, Enum):
            payload[key] = value.value
        elif isinstance(value, datetime):
            payload[key] = value.isoformat()
    payload["connector_metadata"] = [
        {"name": item.name, "value": item.value}
        for item in receipt.connector_metadata
    ]
    return json.dumps(payload, sort_keys=True)


# Recover one typed receipt from the durable ledger.
def _receipt_from_json(payload: str) -> ExternalExecutionReceipt:
    raw = json.loads(payload)
    return ExternalExecutionReceipt(
        operation_id=str(raw["operation_id"]),
        capability_id=str(raw["capability_id"]),
        payload_digest=str(raw["payload_digest"]),
        status=ExternalExecutionStatus(raw["status"]),
        idempotency_key=str(raw["idempotency_key"]),
        started_at=datetime.fromisoformat(raw["started_at"]),
        completed_at=datetime.fromisoformat(raw["completed_at"]),
        external_reference=raw.get("external_reference"),
        summary=str(raw.get("summary", "")),
        error_code=ExternalExecutionErrorCode(raw.get("error_code", "none")),
        retryable=bool(raw.get("retryable", False)),
        connector_kind=raw.get("connector_kind"),
        connector_id=raw.get("connector_id"),
        server_id=raw.get("server_id"),
        tool_name=raw.get("tool_name"),
        connector_metadata=tuple(
            GenericPayloadField(name=str(item["name"]), value=str(item["value"]))
            for item in raw.get("connector_metadata", [])
        ),
        attempt_number=int(raw.get("attempt_number", 1)),
    )


__all__ = ["ExternalExecutionLedger"]
