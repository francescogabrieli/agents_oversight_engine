from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "layer5_evaluation_db.v1"


class EvaluationStore:
    # Open the dedicated Layer 5 SQLite projection and initialize its schema.
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self._setup()

    # Release the SQLite connection owned by this store.
    def close(self) -> None:
        self.connection.close()

    # Support deterministic cleanup in callers and tests.
    def __enter__(self) -> EvaluationStore:
        return self

    # Close the connection when leaving a managed context.
    def __exit__(self, *_: object) -> None:
        self.close()

    # Create the intentionally small evaluation schema.
    def _setup(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS experiments (
                experiment_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
                task_case_id TEXT NOT NULL,
                trace_path TEXT NOT NULL,
                trace_digest TEXT NOT NULL UNIQUE,
                trace_version TEXT NOT NULL,
                configuration_fingerprint TEXT NOT NULL,
                runtime_status TEXT NOT NULL,
                task_result TEXT CHECK (task_result IN ('correct', 'partial', 'incorrect')),
                evaluation_method TEXT CHECK (evaluation_method IN ('automatic', 'human')),
                goal TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                execution_intent TEXT NOT NULL,
                duration_seconds REAL,
                actor_turn_count INTEGER NOT NULL,
                tool_call_count INTEGER NOT NULL,
                failed_tool_count INTEGER NOT NULL,
                replanning_count INTEGER NOT NULL,
                governance_pass_count INTEGER NOT NULL,
                human_review_count INTEGER NOT NULL,
                external_operation_count INTEGER NOT NULL,
                imported_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS governance_events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                pass_index INTEGER NOT NULL,
                directive TEXT NOT NULL,
                policy_decision TEXT NOT NULL,
                reason_codes_json TEXT NOT NULL,
                requires_human_review INTEGER NOT NULL CHECK (requires_human_review IN (0, 1)),
                human_review_was_needed INTEGER CHECK (human_review_was_needed IN (0, 1)),
                UNIQUE (run_id, pass_index)
            );

            CREATE TABLE IF NOT EXISTS human_reviews (
                review_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                governance_event_id TEXT REFERENCES governance_events(event_id),
                request_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                reviewer_id TEXT,
                feedback TEXT,
                human_modified INTEGER NOT NULL CHECK (human_modified IN (0, 1)),
                review_time_seconds REAL
            );

            CREATE TABLE IF NOT EXISTS external_operations (
                row_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                operation_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                payload_digest TEXT,
                human_modified INTEGER NOT NULL CHECK (human_modified IN (0, 1)),
                execution_status TEXT NOT NULL,
                operation_correct INTEGER CHECK (operation_correct IN (0, 1)),
                started_at TEXT,
                completed_at TEXT,
                duration_seconds REAL
            );

            CREATE INDEX IF NOT EXISTS idx_runs_experiment ON runs(experiment_id);
            CREATE INDEX IF NOT EXISTS idx_governance_run ON governance_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_reviews_run ON human_reviews(run_id);
            CREATE INDEX IF NOT EXISTS idx_operations_run ON external_operations(run_id);
            """
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        self.connection.commit()

    # Ensure an experiment exists before importing its runs.
    def ensure_experiment(
        self, *, experiment_id: str, name: str, description: str | None, created_at: str
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO experiments(experiment_id, name, description, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(experiment_id) DO UPDATE SET
                name = excluded.name,
                description = COALESCE(excluded.description, experiments.description)
            """,
            (experiment_id, name, description, created_at),
        )
        self.connection.commit()

    # Replace one trace projection while preserving manual run assessment fields.
    def replace_run_projection(
        self,
        *,
        run: Mapping[str, Any],
        governance_events: Sequence[Mapping[str, Any]],
        human_reviews: Sequence[Mapping[str, Any]],
        external_operations: Sequence[Mapping[str, Any]],
    ) -> None:
        with self.connection:
            existing = self.connection.execute(
                "SELECT task_result, evaluation_method FROM runs WHERE run_id = ?",
                (run["run_id"],),
            ).fetchone()
            retained_governance = {
                row["event_id"]: row["human_review_was_needed"]
                for row in self.connection.execute(
                    "SELECT event_id, human_review_was_needed FROM governance_events WHERE run_id = ?",
                    (run["run_id"],),
                ).fetchall()
            }
            retained_operations = {
                row["row_id"]: row["operation_correct"]
                for row in self.connection.execute(
                    "SELECT row_id, operation_correct FROM external_operations WHERE run_id = ?",
                    (run["run_id"],),
                ).fetchall()
            }
            retained_result = existing["task_result"] if existing else None
            retained_method = existing["evaluation_method"] if existing else None
            self.connection.execute(
                "DELETE FROM runs WHERE run_id = ?",
                (run["run_id"],),
            )
            columns = tuple(run.keys()) + ("task_result", "evaluation_method")
            values = tuple(run.values()) + (retained_result, retained_method)
            placeholders = ", ".join("?" for _ in columns)
            self.connection.execute(
                f"INSERT INTO runs ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
            governance_rows = [dict(row) for row in governance_events]
            for row in governance_rows:
                retained = retained_governance.get(str(row["event_id"]))
                if retained is not None:
                    row["human_review_was_needed"] = retained
            operation_rows = [dict(row) for row in external_operations]
            for row in operation_rows:
                retained = retained_operations.get(str(row["row_id"]))
                if retained is not None:
                    row["operation_correct"] = retained
            self._insert_rows("governance_events", governance_rows)
            self._insert_rows("human_reviews", human_reviews)
            self._insert_rows("external_operations", operation_rows)

    # Insert homogeneous typed projections into one child table.
    def _insert_rows(self, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        columns = tuple(rows[0].keys())
        placeholders = ", ".join("?" for _ in columns)
        statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        self.connection.executemany(statement, [tuple(row[column] for column in columns) for row in rows])

    # Check whether the exact trace revision is already present.
    def has_trace_digest(self, trace_digest: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM runs WHERE trace_digest = ?", (trace_digest,)
        ).fetchone()
        return row is not None

    # Record the adjudicated task result without mutating the source trace.
    def set_task_result(self, *, run_id: str, result: str, method: str) -> None:
        if result not in {"correct", "partial", "incorrect"}:
            raise ValueError(f"Unsupported task result: {result}")
        if method not in {"automatic", "human"}:
            raise ValueError(f"Unsupported evaluation method: {method}")
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE runs SET task_result = ?, evaluation_method = ? WHERE run_id = ?",
                (result, method, run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown run: {run_id}")

    # Record whether one concrete escalation was actually necessary.
    def set_human_review_needed(self, *, event_id: str, needed: bool) -> None:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE governance_events SET human_review_was_needed = ? WHERE event_id = ?",
                (int(needed), event_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown governance event: {event_id}")

    # Record the semantic correctness of one external operation.
    def set_external_operation_correct(self, *, row_id: str, correct: bool) -> None:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE external_operations SET operation_correct = ? WHERE row_id = ?",
                (int(correct), row_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown external operation: {row_id}")

    # Return rows as plain dictionaries for metrics, CSV export, and tests.
    def query(self, statement: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        rows = self.connection.execute(statement, tuple(parameters)).fetchall()
        return [dict(row) for row in rows]

    # Load ordered child facts for a bounded set of evaluation runs.
    def child_rows(self, table: str, run_ids: Sequence[str]) -> list[dict[str, Any]]:
        allowed_tables = {"governance_events", "human_reviews", "external_operations"}
        if table not in allowed_tables:
            raise ValueError(f"Unsupported evaluation child table: {table}")
        if not run_ids:
            return []
        placeholders = ", ".join("?" for _ in run_ids)
        return self.query(
            f"SELECT * FROM {table} WHERE run_id IN ({placeholders}) ORDER BY run_id",
            run_ids,
        )
