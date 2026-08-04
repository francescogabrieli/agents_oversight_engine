from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class ServerLedgerReservation:
    # Reservations prevent a repeated MCP call from silently repeating a side effect.
    should_execute: bool
    prior_response: dict[str, Any] | None = None
    prior_in_progress: bool = False


class ServerIdempotencyLedger:
    # Each server process owns an explicit durable ledger rather than a global singleton.
    def __init__(self, path: Path | None = None) -> None:
        configured = os.getenv("LAYER2_EXTERNAL_SERVER_LEDGER_PATH")
        self._path = path or (
            Path(configured)
            if configured
            else Path("artifacts/runtime/external/server_idempotency.sqlite3")
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._initialize()

    # Atomically reserve a key or return its prior durable result.
    def reserve(self, *, idempotency_key: str, capability_id: str) -> ServerLedgerReservation:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT capability_id, state, response_json
                FROM executions WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                connection.commit()
                if row[0] != capability_id:
                    return ServerLedgerReservation(
                        should_execute=False,
                        prior_in_progress=True,
                    )
                response = json.loads(row[2]) if row[2] else None
                return ServerLedgerReservation(
                    should_execute=False,
                    prior_response=response,
                    prior_in_progress=row[1] == "in_progress",
                )
            connection.execute(
                """
                INSERT INTO executions(idempotency_key, capability_id, state, response_json)
                VALUES (?, ?, 'in_progress', NULL)
                """,
                (idempotency_key, capability_id),
            )
            connection.commit()
        return ServerLedgerReservation(should_execute=True)

    # Persist the response returned for a reserved key.
    def complete(self, *, idempotency_key: str, response: dict[str, Any]) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE executions
                SET state = ?, response_json = ?
                WHERE idempotency_key = ?
                """,
                (
                    "executed" if response.get("status") == "accepted" else "terminal",
                    json.dumps(response, sort_keys=True),
                    idempotency_key,
                ),
            )
            connection.commit()

    # Create the small durable idempotency table once.
    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    idempotency_key TEXT PRIMARY KEY,
                    capability_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    response_json TEXT
                )
                """
            )
            connection.commit()

    # Open one short transaction-scoped SQLite connection.
    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=30)


__all__ = ["ServerIdempotencyLedger", "ServerLedgerReservation"]
