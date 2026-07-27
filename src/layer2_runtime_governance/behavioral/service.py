from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from layer1_intake import IntakeResult

from ..domain import TaskState

if TYPE_CHECKING:
    from ..judge import JudgeAssessment


class BehavioralRecommendedDirective(str, Enum):
    # Behavioral recommendations stay conservative and never hard-stop execution.
    CONTINUE = "continue"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REPLAN = "replan"
    WAIT_HUMAN_REVIEW = "wait_human_review"


@dataclass(frozen=True)
class BehavioralAssessment:
    # BehavioralAssessment captures history, similarity, deviation, and one conservative recommendation.
    history_available: bool
    historical_run_count: int
    feature_snapshot_version: str
    summary: str
    notes: tuple[str, ...]
    matched_run_count: int = 0
    historical_similarity_score: float | None = None
    behavior_deviation_score: float | None = None
    novelty_score: float | None = None
    behavioral_recommended_directive: BehavioralRecommendedDirective = (
        BehavioralRecommendedDirective.CONTINUE
    )
    behavioral_flags: tuple[str, ...] = ()
    matched_run_ids: tuple[str, ...] = ()
    mode: str = "shadow"
    decision_influence: bool = False
    status: str = "shadow"


@dataclass(frozen=True)
class BehavioralStoreSettings:
    # BehavioralStoreSettings keeps the local SQLite path explicit and testable.
    database_path: Path


class BehavioralStorePort(Protocol):
    # BehavioralStorePort decouples governance from one persistence backend.
    def build_behavioral_assessment(
        self,
        *,
        thread_id: str,
        task_state: TaskState,
    ) -> BehavioralAssessment: ...

    def persist_behavioral_snapshot(
        self,
        *,
        thread_id: str,
        intake_result: IntakeResult,
        task_state: TaskState,
    ) -> None: ...

    def persist_judge_snapshot(
        self,
        *,
        thread_id: str,
        task_state: TaskState,
    ) -> None: ...

    def persist_runtime_snapshot(
        self,
        *,
        thread_id: str,
        intake_result: IntakeResult,
        task_state: TaskState,
    ) -> None: ...


@dataclass
class SQLiteBehavioralStore(BehavioralStorePort):
    # SQLiteBehavioralStore keeps behavioural history local, durable, and queryable.
    settings: BehavioralStoreSettings
    _schema_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _schema_initialized: bool = field(default=False, init=False, repr=False)

    # Build the behavioural assessment from comparable historical runs and current runtime state.
    def build_behavioral_assessment(
        self,
        *,
        thread_id: str,
        task_state: TaskState,
    ) -> BehavioralAssessment:
        with closing(self._connect()) as connection:
            self._ensure_schema_once(connection)
            historical_run_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM runs WHERE thread_id <> ?",
                    (thread_id,),
                ).fetchone()[0]
            )
            current_profile = _build_current_profile(task_state=task_state)
            candidate_runs = self._load_candidate_runs(
                connection=connection,
                thread_id=thread_id,
                current_profile=current_profile,
            )

            if historical_run_count < 20 or not candidate_runs:
                return _build_foundation_only_assessment(
                    task_state=task_state,
                    historical_run_count=historical_run_count,
                )

            candidate_thread_ids = [candidate["thread_id"] for candidate in candidate_runs]
            feature_map = self._load_feature_map(
                connection=connection,
                thread_ids=candidate_thread_ids,
            )
            tool_usage_map = self._load_tool_usage_map(
                connection=connection,
                thread_ids=candidate_thread_ids,
            )
            governance_sequence_map = self._load_governance_distribution_map(
                connection=connection,
                thread_ids=candidate_thread_ids,
            )
            validation_sequence_map = self._load_validation_distribution_map(
                connection=connection,
                thread_ids=candidate_thread_ids,
            )
            scored_candidates = [
                _score_candidate_similarity(
                    current_profile=current_profile,
                    candidate_run=candidate,
                    candidate_features=feature_map.get(candidate["thread_id"], {}),
                    candidate_tool_usage=tool_usage_map.get(candidate["thread_id"], set()),
                    candidate_governance_distribution=governance_sequence_map.get(
                        candidate["thread_id"],
                        {},
                    ),
                    candidate_validation_distribution=validation_sequence_map.get(
                        candidate["thread_id"],
                        {},
                    ),
                )
                for candidate in candidate_runs
            ]
            scored_candidates.sort(key=lambda item: item["score"], reverse=True)

        top_summary = scored_candidates[:5]
        top_baseline = scored_candidates[:10]
        similarity_scores = [float(item["score"]) for item in top_summary]
        historical_similarity_score = similarity_scores[0] if similarity_scores else None
        novelty_score = _compute_novelty_score(similarity_scores=similarity_scores[:3])
        behavior_deviation_score, deviation_driver = _compute_behavior_deviation(
            current_profile=current_profile,
            baseline_candidates=top_baseline,
        )
        review_rate = _compute_review_rate(top_baseline)
        failure_rate = _compute_failure_rate(top_baseline)
        behavioral_directive, behavioral_flags = _recommend_behavioral_directive(
            task_state=task_state,
            historical_similarity_score=historical_similarity_score,
            behavior_deviation_score=behavior_deviation_score,
            novelty_score=novelty_score,
            review_rate=review_rate,
            deviation_driver=deviation_driver,
        )

        matched_run_ids = tuple(item["thread_id"] for item in top_summary)
        notes = (
            f"matched_run_count={len(top_summary)}",
            f"review_rate={review_rate:.2f}",
            f"failure_rate={failure_rate:.2f}",
            f"deviation_driver={deviation_driver or 'none'}",
            *behavioral_flags,
        )
        summary = _build_behavioral_summary(
            matched_run_count=len(top_summary),
            historical_similarity_score=historical_similarity_score,
            behavior_deviation_score=behavior_deviation_score,
            novelty_score=novelty_score,
            behavioral_directive=behavioral_directive,
        )
        return BehavioralAssessment(
            history_available=historical_run_count > 0,
            historical_run_count=historical_run_count,
            feature_snapshot_version="behavioral_features_v2",
            summary=summary,
            notes=tuple(dict.fromkeys(notes)),
            matched_run_count=len(top_summary),
            historical_similarity_score=historical_similarity_score,
            behavior_deviation_score=behavior_deviation_score,
            novelty_score=novelty_score,
            behavioral_recommended_directive=behavioral_directive,
            behavioral_flags=tuple(dict.fromkeys(behavioral_flags)),
            matched_run_ids=matched_run_ids,
        )

    # Persist the current run snapshot as a replaceable read model for future behavioural checks.
    def persist_behavioral_snapshot(
        self,
        *,
        thread_id: str,
        intake_result: IntakeResult,
        task_state: TaskState,
    ) -> None:
        with closing(self._connect()) as connection:
            self._ensure_schema_once(connection)
            self._replace_run_snapshot(
                connection=connection,
                thread_id=thread_id,
                intake_result=intake_result,
                task_state=task_state,
            )
            connection.commit()

    # Persist judge history separately so runtime can store judge signals even if behavioural fails closed.
    def persist_judge_snapshot(
        self,
        *,
        thread_id: str,
        task_state: TaskState,
    ) -> None:
        with closing(self._connect()) as connection:
            self._ensure_schema_once(connection)
            if task_state.judge_history:
                self._upsert_judge_assessment(
                    connection=connection,
                    thread_id=thread_id,
                    assessment_index=len(task_state.judge_history),
                    assessment=task_state.judge_history[-1],
                )
            connection.commit()

    # Persist behavioural and judge projections in one SQLite transaction.
    def persist_runtime_snapshot(
        self,
        *,
        thread_id: str,
        intake_result: IntakeResult,
        task_state: TaskState,
    ) -> None:
        with closing(self._connect()) as connection:
            self._ensure_schema_once(connection)
            self._replace_run_snapshot(
                connection=connection,
                thread_id=thread_id,
                intake_result=intake_result,
                task_state=task_state,
            )
            self._replace_judge_snapshot(
                connection=connection,
                thread_id=thread_id,
                task_state=task_state,
            )
            connection.commit()

    # Open one SQLite connection and ensure the parent directory exists first.
    def _connect(self) -> sqlite3.Connection:
        self.settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.settings.database_path)

    # Initialize or migrate the schema once per store instance.
    def _ensure_schema_once(self, connection: sqlite3.Connection) -> None:
        if self._schema_initialized:
            return
        with self._schema_lock:
            if self._schema_initialized:
                return
            self._ensure_schema(connection)
            self._schema_initialized = True

    # Create the behavioural schema needed for similarity, deviation, and judge persistence.
    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                thread_id TEXT PRIMARY KEY,
                final_state TEXT NOT NULL,
                domain_type TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                policy_profile_name TEXT NOT NULL,
                budget_max_steps INTEGER,
                budget_max_tool_calls INTEGER,
                actor_turn_count INTEGER NOT NULL,
                tool_call_count INTEGER NOT NULL,
                replan_count INTEGER NOT NULL,
                review_count INTEGER NOT NULL,
                abort_count INTEGER NOT NULL,
                has_external_communication INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS steps (
                thread_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                status TEXT NOT NULL,
                actor_result_type TEXT,
                turn_count INTEGER NOT NULL,
                PRIMARY KEY (thread_id, step_id)
            );
            CREATE TABLE IF NOT EXISTS tool_calls (
                thread_id TEXT NOT NULL,
                call_index INTEGER NOT NULL,
                plan_step_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                success INTEGER,
                PRIMARY KEY (thread_id, call_index)
            );
            CREATE TABLE IF NOT EXISTS governance_decisions (
                thread_id TEXT NOT NULL,
                decision_index INTEGER NOT NULL,
                static_policy_decision TEXT NOT NULL,
                dynamic_recommended_directive TEXT NOT NULL,
                final_directive TEXT NOT NULL,
                reason_codes_json TEXT NOT NULL,
                PRIMARY KEY (thread_id, decision_index)
            );
            CREATE TABLE IF NOT EXISTS dynamic_signals (
                thread_id TEXT NOT NULL,
                assessment_index INTEGER NOT NULL,
                evidence_quality_score REAL,
                evidence_quality_level TEXT NOT NULL,
                operational_risk_score REAL,
                operational_risk_level TEXT NOT NULL,
                trajectory_health_score REAL,
                trajectory_health_level TEXT NOT NULL,
                recommended_directive TEXT NOT NULL,
                PRIMARY KEY (thread_id, assessment_index)
            );
            CREATE TABLE IF NOT EXISTS behavioral_features (
                thread_id TEXT NOT NULL,
                feature_name TEXT NOT NULL,
                feature_value TEXT NOT NULL,
                PRIMARY KEY (thread_id, feature_name)
            );
            CREATE TABLE IF NOT EXISTS judge_assessments (
                thread_id TEXT NOT NULL,
                assessment_index INTEGER NOT NULL,
                invoked INTEGER NOT NULL,
                recommendation TEXT NOT NULL,
                confidence REAL NOT NULL,
                evaluated_target TEXT NOT NULL,
                flags_json TEXT NOT NULL,
                PRIMARY KEY (thread_id, assessment_index)
            );
            """
        )
        self._migrate_behavioral_table_shapes(connection)
        self._migrate_dynamic_signal_scores_to_nullable(connection)

    # Rebuild old local read-model tables onto the current concrete, rank-free schema.
    def _migrate_behavioral_table_shapes(self, connection: sqlite3.Connection) -> None:
        self._migrate_runs_table(connection)
        self._migrate_steps_table(connection)
        self._migrate_tool_calls_table(connection)

    # Preserve run history while removing retired columns and nullable-budget drift.
    def _migrate_runs_table(self, connection: sqlite3.Connection) -> None:
        columns = connection.execute("PRAGMA table_info(runs)").fetchall()
        if not columns:
            return
        expected_columns = (
            "thread_id",
            "final_state",
            "domain_type",
            "sensitivity",
            "policy_profile_name",
            "budget_max_steps",
            "budget_max_tool_calls",
            "actor_turn_count",
            "tool_call_count",
            "replan_count",
            "review_count",
            "abort_count",
            "has_external_communication",
        )
        actual_columns = tuple(str(column[1]) for column in columns)
        budget_columns_are_nullable = all(
            int(column[3]) == 0
            for column in columns
            if column[1] in {"budget_max_steps", "budget_max_tool_calls"}
        )
        if actual_columns == expected_columns and budget_columns_are_nullable:
            return

        connection.executescript(
            """
            ALTER TABLE runs RENAME TO runs_schema_migration;
            CREATE TABLE runs (
                thread_id TEXT PRIMARY KEY,
                final_state TEXT NOT NULL,
                domain_type TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                policy_profile_name TEXT NOT NULL,
                budget_max_steps INTEGER,
                budget_max_tool_calls INTEGER,
                actor_turn_count INTEGER NOT NULL,
                tool_call_count INTEGER NOT NULL,
                replan_count INTEGER NOT NULL,
                review_count INTEGER NOT NULL,
                abort_count INTEGER NOT NULL,
                has_external_communication INTEGER NOT NULL
            );
            INSERT INTO runs (
                thread_id, final_state, domain_type, sensitivity, policy_profile_name,
                budget_max_steps, budget_max_tool_calls, actor_turn_count, tool_call_count,
                replan_count, review_count, abort_count, has_external_communication
            )
            SELECT
                thread_id, final_state, domain_type, sensitivity, policy_profile_name,
                budget_max_steps, budget_max_tool_calls, actor_turn_count, tool_call_count,
                replan_count, review_count, abort_count, has_external_communication
            FROM runs_schema_migration;
            DROP TABLE runs_schema_migration;
            """
        )

    # Preserve step history while retaining only explicit actor result facts.
    def _migrate_steps_table(self, connection: sqlite3.Connection) -> None:
        columns = connection.execute("PRAGMA table_info(steps)").fetchall()
        expected_columns = (
            "thread_id",
            "step_id",
            "status",
            "actor_result_type",
            "turn_count",
        )
        if tuple(str(column[1]) for column in columns) == expected_columns:
            return

        connection.executescript(
            """
            ALTER TABLE steps RENAME TO steps_schema_migration;
            CREATE TABLE steps (
                thread_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                status TEXT NOT NULL,
                actor_result_type TEXT,
                turn_count INTEGER NOT NULL,
                PRIMARY KEY (thread_id, step_id)
            );
            INSERT INTO steps (
                thread_id, step_id, status, actor_result_type, turn_count
            )
            SELECT
                thread_id, step_id, status, actor_result_type, turn_count
            FROM steps_schema_migration;
            DROP TABLE steps_schema_migration;
            """
        )

    # Preserve tool-call history without retaining a universal evidence rank.
    def _migrate_tool_calls_table(self, connection: sqlite3.Connection) -> None:
        columns = connection.execute("PRAGMA table_info(tool_calls)").fetchall()
        expected_columns = (
            "thread_id",
            "call_index",
            "plan_step_id",
            "tool_name",
            "success",
        )
        if tuple(str(column[1]) for column in columns) == expected_columns:
            return

        connection.executescript(
            """
            ALTER TABLE tool_calls RENAME TO tool_calls_schema_migration;
            CREATE TABLE tool_calls (
                thread_id TEXT NOT NULL,
                call_index INTEGER NOT NULL,
                plan_step_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                success INTEGER,
                PRIMARY KEY (thread_id, call_index)
            );
            INSERT INTO tool_calls (
                thread_id, call_index, plan_step_id, tool_name, success
            )
            SELECT
                thread_id, call_index, plan_step_id, tool_name, success
            FROM tool_calls_schema_migration;
            DROP TABLE tool_calls_schema_migration;
            """
        )

    # Preserve legacy SQLite history while allowing explicit unavailable/shadow scores.
    def _migrate_dynamic_signal_scores_to_nullable(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = connection.execute("PRAGMA table_info(dynamic_signals)").fetchall()
        score_columns = {
            column[1]: column
            for column in columns
            if column[1] in {
                "evidence_quality_score",
                "operational_risk_score",
                "trajectory_health_score",
            }
        }
        if not score_columns or all(int(column[3]) == 0 for column in score_columns.values()):
            return

        connection.executescript(
            """
            ALTER TABLE dynamic_signals RENAME TO dynamic_signals_legacy_score_migration;
            CREATE TABLE dynamic_signals (
                thread_id TEXT NOT NULL,
                assessment_index INTEGER NOT NULL,
                evidence_quality_score REAL,
                evidence_quality_level TEXT NOT NULL,
                operational_risk_score REAL,
                operational_risk_level TEXT NOT NULL,
                trajectory_health_score REAL,
                trajectory_health_level TEXT NOT NULL,
                recommended_directive TEXT NOT NULL,
                PRIMARY KEY (thread_id, assessment_index)
            );
            INSERT INTO dynamic_signals (
                thread_id, assessment_index, evidence_quality_score,
                evidence_quality_level, operational_risk_score,
                operational_risk_level, trajectory_health_score,
                trajectory_health_level, recommended_directive
            )
            SELECT
                thread_id, assessment_index, evidence_quality_score,
                evidence_quality_level, operational_risk_score,
                operational_risk_level, trajectory_health_score,
                trajectory_health_level, recommended_directive
            FROM dynamic_signals_legacy_score_migration;
            DROP TABLE dynamic_signals_legacy_score_migration;
            """
        )

    # Load the strict candidate pool first and broaden it only when history is still sparse.
    def _load_candidate_runs(
        self,
        *,
        connection: sqlite3.Connection,
        thread_id: str,
        current_profile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        strict_rows = connection.execute(
            """
            SELECT
                thread_id, final_state, domain_type, sensitivity, policy_profile_name,
                actor_turn_count, tool_call_count, replan_count, review_count, abort_count,
                has_external_communication
            FROM runs
            WHERE thread_id <> ?
              AND domain_type = ?
              AND sensitivity = ?
              AND policy_profile_name = ?
            ORDER BY has_external_communication = ? DESC, thread_id DESC
            LIMIT 200
            """,
            (
                thread_id,
                current_profile["domain_type"],
                current_profile["sensitivity"],
                current_profile["policy_profile_name"],
                current_profile["has_external_communication"],
            ),
        ).fetchall()
        if len(strict_rows) >= 20:
            return [_row_to_candidate(row) for row in strict_rows]

        fallback_rows = connection.execute(
            """
            SELECT
                thread_id, final_state, domain_type, sensitivity, policy_profile_name,
                actor_turn_count, tool_call_count, replan_count, review_count, abort_count,
                has_external_communication
            FROM runs
            WHERE thread_id <> ?
              AND domain_type = ?
              AND has_external_communication = ?
            ORDER BY thread_id DESC
            LIMIT 300
            """,
            (
                thread_id,
                current_profile["domain_type"],
                current_profile["has_external_communication"],
            ),
        ).fetchall()
        return [_row_to_candidate(row) for row in fallback_rows]

    # Replace the materialized judge projection inside the caller transaction.
    def _replace_judge_snapshot(
        self,
        *,
        connection: sqlite3.Connection,
        thread_id: str,
        task_state: TaskState,
    ) -> None:
        connection.execute("DELETE FROM judge_assessments WHERE thread_id = ?", (thread_id,))
        for assessment_index, assessment in enumerate(task_state.judge_history, start=1):
            self._upsert_judge_assessment(
                connection=connection,
                thread_id=thread_id,
                assessment_index=assessment_index,
                assessment=assessment,
            )

    # Upsert one judge record so intermediate governance passes stay incremental.
    def _upsert_judge_assessment(
        self,
        *,
        connection: sqlite3.Connection,
        thread_id: str,
        assessment_index: int,
        assessment: JudgeAssessment,
    ) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO judge_assessments (
                thread_id, assessment_index, invoked, recommendation,
                confidence, evaluated_target, flags_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                thread_id,
                assessment_index,
                int(bool(assessment.invoked)),
                assessment.recommendation.value,
                assessment.confidence,
                assessment.evaluated_target,
                json.dumps(list(assessment.flags), ensure_ascii=False),
            ),
        )

    # Load feature snapshots into one per-thread dictionary.
    def _load_feature_map(
        self,
        *,
        connection: sqlite3.Connection,
        thread_ids: list[str],
    ) -> dict[str, dict[str, str]]:
        if not thread_ids:
            return {}
        placeholders = ", ".join("?" for _ in thread_ids)
        rows = connection.execute(
            f"""
            SELECT thread_id, feature_name, feature_value
            FROM behavioral_features
            WHERE thread_id IN ({placeholders})
            """,
            thread_ids,
        ).fetchall()
        feature_map: dict[str, dict[str, str]] = {}
        for thread_id, feature_name, feature_value in rows:
            feature_map.setdefault(str(thread_id), {})[str(feature_name)] = str(feature_value)
        return feature_map

    # Load tool names as one set per historical run.
    def _load_tool_usage_map(
        self,
        *,
        connection: sqlite3.Connection,
        thread_ids: list[str],
    ) -> dict[str, set[str]]:
        if not thread_ids:
            return {}
        placeholders = ", ".join("?" for _ in thread_ids)
        rows = connection.execute(
            f"""
            SELECT thread_id, tool_name
            FROM tool_calls
            WHERE thread_id IN ({placeholders})
            """,
            thread_ids,
        ).fetchall()
        tool_usage_map: dict[str, set[str]] = {}
        for run_id, tool_name in rows:
            tool_usage_map.setdefault(str(run_id), set()).add(str(tool_name))
        return tool_usage_map

    # Load governance directive frequencies from persisted feature snapshots.
    def _load_governance_distribution_map(
        self,
        *,
        connection: sqlite3.Connection,
        thread_ids: list[str],
    ) -> dict[str, dict[str, int]]:
        return self._load_distribution_feature_map(
            connection=connection,
            thread_ids=thread_ids,
            feature_name="governance_distribution",
        )

    # Load validation decision frequencies from persisted feature snapshots.
    def _load_validation_distribution_map(
        self,
        *,
        connection: sqlite3.Connection,
        thread_ids: list[str],
    ) -> dict[str, dict[str, int]]:
        return self._load_distribution_feature_map(
            connection=connection,
            thread_ids=thread_ids,
            feature_name="validation_distribution",
        )

    # Rehydrate one JSON distribution feature per thread.
    def _load_distribution_feature_map(
        self,
        *,
        connection: sqlite3.Connection,
        thread_ids: list[str],
        feature_name: str,
    ) -> dict[str, dict[str, int]]:
        if not thread_ids:
            return {}
        placeholders = ", ".join("?" for _ in thread_ids)
        rows = connection.execute(
            f"""
            SELECT thread_id, feature_value
            FROM behavioral_features
            WHERE feature_name = ?
              AND thread_id IN ({placeholders})
            """,
            [feature_name, *thread_ids],
        ).fetchall()
        distribution_map: dict[str, dict[str, int]] = {}
        for run_id, raw_value in rows:
            parsed_value = _parse_json_dict(raw_value)
            distribution_map[str(run_id)] = {
                str(key): int(value)
                for key, value in parsed_value.items()
                if isinstance(key, str) and isinstance(value, (int, float))
            }
        return distribution_map

    # Replace the materialized snapshot for one thread so repeated persists stay idempotent.
    def _replace_run_snapshot(
        self,
        *,
        connection: sqlite3.Connection,
        thread_id: str,
        intake_result: IntakeResult,
        task_state: TaskState,
    ) -> None:
        for table_name in (
            "runs",
            "steps",
            "tool_calls",
            "governance_decisions",
            "dynamic_signals",
            "behavioral_features",
        ):
            connection.execute(f"DELETE FROM {table_name} WHERE thread_id = ?", (thread_id,))

        structured_task = intake_result.structured_task
        connection.execute(
            """
            INSERT INTO runs (
                thread_id, final_state, domain_type, sensitivity, policy_profile_name,
                budget_max_steps, budget_max_tool_calls, actor_turn_count, tool_call_count,
                replan_count, review_count, abort_count, has_external_communication
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                thread_id,
                task_state.current_state.value,
                structured_task.task_spec.domain_type.value,
                structured_task.task_spec.sensitivity.value,
                structured_task.execution_policy.policy_profile_name,
                structured_task.execution_policy.budget.max_steps,
                structured_task.execution_policy.budget.max_tool_calls,
                task_state.actor_turn_count,
                task_state.tool_call_count,
                sum(1 for record in task_state.step_history if record.node_name == "replanner"),
                sum(1 for decision in task_state.governance_history if decision.directive.value == "wait_human_review"),
                1 if task_state.current_state.value == "aborted" else 0,
                1 if task_state.external_communication is not None else 0,
            ),
        )

        for step in task_state.plan_steps:
            actor_result_type = None
            turn_count = 0
            for executed_step in task_state.executed_steps:
                if executed_step.step_id == step.step_id:
                    actor_result_type = executed_step.result_type.value
                    break
            for scratchpad_entry in task_state.scratchpad:
                if scratchpad_entry.plan_step_id == step.step_id:
                    turn_count = max(turn_count, scratchpad_entry.turn_index)
            connection.execute(
                """
                INSERT INTO steps (
                    thread_id, step_id, status, actor_result_type, turn_count
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    step.step_id,
                    step.status.value,
                    actor_result_type,
                    turn_count,
                ),
            )

        for call_index, tool_result in enumerate(task_state.tool_results_history, start=1):
            connection.execute(
                """
                INSERT INTO tool_calls (
                    thread_id, call_index, plan_step_id, tool_name, success
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    call_index,
                    tool_result.request.plan_step_id,
                    tool_result.request.tool_name.value,
                    None if tool_result.success is None else int(bool(tool_result.success)),
                ),
            )

        for decision_index, decision in enumerate(task_state.governance_history, start=1):
            connection.execute(
                """
                INSERT INTO governance_decisions (
                    thread_id, decision_index, static_policy_decision,
                    dynamic_recommended_directive, final_directive, reason_codes_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    decision_index,
                    decision.policy_decision.value,
                    decision.oversight_recommended_directive.value,
                    decision.directive.value,
                    json.dumps([code.value for code in decision.reason_codes], ensure_ascii=False),
                ),
            )

        for assessment_index, assessment in enumerate(task_state.dynamic_policy_history, start=1):
            connection.execute(
                """
                INSERT INTO dynamic_signals (
                    thread_id, assessment_index, evidence_quality_score, evidence_quality_level,
                    operational_risk_score, operational_risk_level, trajectory_health_score,
                    trajectory_health_level, recommended_directive
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    assessment_index,
                    assessment.evidence_quality.score,
                    assessment.evidence_quality.level.value,
                    assessment.operational_risk.score,
                    assessment.operational_risk.level.value,
                    assessment.trajectory_health.score,
                    assessment.trajectory_health.level.value,
                    assessment.recommended_directive.value,
                ),
            )

        for feature_name, feature_value in _build_feature_snapshot(task_state=task_state).items():
            connection.execute(
                """
                INSERT INTO behavioral_features (thread_id, feature_name, feature_value)
                VALUES (?, ?, ?)
                """,
                (thread_id, feature_name, feature_value),
            )


# Return the repository-local SQLite path used by behavioural persistence.
def get_default_behavioral_store_path() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "artifacts" / "runtime" / "behavioral" / "behavioral.sqlite3"


# Build the default local behavioural store used by runtime governance.
def build_default_behavioral_store() -> BehavioralStorePort:
    return SQLiteBehavioralStore(
        settings=BehavioralStoreSettings(database_path=get_default_behavioral_store_path())
    )


# Build the behavioural assessment from the configured store.
def build_behavioral_assessment(
    *,
    thread_id: str,
    task_state: TaskState,
    behavioral_store: BehavioralStorePort | None = None,
) -> BehavioralAssessment:
    resolved_store = behavioral_store or build_default_behavioral_store()
    return resolved_store.build_behavioral_assessment(
        thread_id=thread_id,
        task_state=task_state,
    )


# Keep the new public behavioural-policy entrypoint explicit.
def evaluate_behavioral_policy(
    *,
    thread_id: str,
    task_state: TaskState,
    behavioral_store: BehavioralStorePort | None = None,
) -> BehavioralAssessment:
    return build_behavioral_assessment(
        thread_id=thread_id,
        task_state=task_state,
        behavioral_store=behavioral_store,
    )


# Persist the current runtime snapshot into the behavioural SQLite read model.
def persist_behavioral_snapshot(
    *,
    thread_id: str,
    intake_result: IntakeResult,
    task_state: TaskState,
    behavioral_store: BehavioralStorePort | None = None,
) -> None:
    resolved_store = behavioral_store or build_default_behavioral_store()
    resolved_store.persist_behavioral_snapshot(
        thread_id=thread_id,
        intake_result=intake_result,
        task_state=task_state,
    )


# Persist the current judge history into the behavioural SQLite read model.
def persist_judge_snapshot(
    *,
    thread_id: str,
    task_state: TaskState,
    behavioral_store: BehavioralStorePort | None = None,
) -> None:
    resolved_store = behavioral_store or build_default_behavioral_store()
    resolved_store.persist_judge_snapshot(
        thread_id=thread_id,
        task_state=task_state,
    )


# Persist both behavioural projections through one transaction when supported.
def persist_behavioral_runtime_snapshot(
    *,
    thread_id: str,
    intake_result: IntakeResult,
    task_state: TaskState,
    behavioral_store: BehavioralStorePort | None = None,
) -> None:
    resolved_store = behavioral_store or build_default_behavioral_store()
    combined_persist = getattr(resolved_store, "persist_runtime_snapshot", None)
    if callable(combined_persist):
        combined_persist(
            thread_id=thread_id,
            intake_result=intake_result,
            task_state=task_state,
        )
        return
    resolved_store.persist_behavioral_snapshot(
        thread_id=thread_id,
        intake_result=intake_result,
        task_state=task_state,
    )
    resolved_store.persist_judge_snapshot(
        thread_id=thread_id,
        task_state=task_state,
    )


# Materialize the feature vector used by the behavioural similarity and deviation logic.
def _build_feature_snapshot(*, task_state: TaskState) -> dict[str, str]:
    validation_distribution = _distribution_from_validation(task_state=task_state)

    governance_distribution: dict[str, int] = {}
    for decision in task_state.governance_history:
        governance_distribution[decision.directive.value] = (
            governance_distribution.get(decision.directive.value, 0) + 1
        )

    behavioral_assessment = task_state.last_behavioral_assessment
    return {
        "feature_snapshot_version": "behavioral_features_v3",
        "step_count": str(len(task_state.plan_steps)),
        "actor_turn_count": str(task_state.actor_turn_count),
        "tool_call_count": str(task_state.tool_call_count),
        "replan_count": str(sum(1 for record in task_state.step_history if record.node_name == "replanner")),
        "review_count": str(
            sum(1 for decision in task_state.governance_history if decision.directive.value == "wait_human_review")
        ),
        "abort_count": str(1 if task_state.current_state.value == "aborted" else 0),
        "has_external_communication": "1" if task_state.external_communication is not None else "0",
        "evidence_count": str(len(task_state.observed_evidence)),
        "official_source_count": str(sum(item.source_authority.value == "official_primary" for item in task_state.observed_evidence)),
        "cited_summary_count": str(sum(item.content_state.value == "provider_cited_summary" for item in task_state.observed_evidence)),
        "extracted_partial_count": str(sum(item.content_state.value == "extracted_partial" for item in task_state.observed_evidence)),
        "extracted_full_count": str(sum(item.content_state.value == "extracted_full" for item in task_state.observed_evidence)),
        "extractability_failure_count": str(sum(not result.success and result.extractability_limited for result in task_state.tool_results_history)),
        "governance_distribution": json.dumps(governance_distribution, ensure_ascii=False, sort_keys=True),
        "validation_distribution": json.dumps(validation_distribution, ensure_ascii=False, sort_keys=True),
        "historical_similarity_score": (
            "" if behavioral_assessment is None or behavioral_assessment.historical_similarity_score is None
            else f"{behavioral_assessment.historical_similarity_score:.6f}"
        ),
        "behavior_deviation_score": (
            "" if behavioral_assessment is None or behavioral_assessment.behavior_deviation_score is None
            else f"{behavioral_assessment.behavior_deviation_score:.6f}"
        ),
        "novelty_score": (
            "" if behavioral_assessment is None or behavioral_assessment.novelty_score is None
            else f"{behavioral_assessment.novelty_score:.6f}"
        ),
        "matched_run_count": (
            "0" if behavioral_assessment is None else str(behavioral_assessment.matched_run_count)
        ),
        "behavioral_recommended_directive": (
            BehavioralRecommendedDirective.CONTINUE.value
            if behavioral_assessment is None
            else behavioral_assessment.behavioral_recommended_directive.value
        ),
    }


# Build the current behavioural profile directly from the live runtime state.
def _build_current_profile(*, task_state: TaskState) -> dict[str, Any]:
    return {
        "domain_type": task_state.task_spec.domain_type.value,
        "sensitivity": task_state.task_spec.sensitivity.value,
        "policy_profile_name": task_state.execution_policy.policy_profile_name,
        "has_external_communication": 1 if task_state.external_communication is not None else 0,
        "step_count": len(task_state.plan_steps),
        "actor_turn_count": task_state.actor_turn_count,
        "tool_call_count": task_state.tool_call_count,
        "replan_count": sum(1 for record in task_state.step_history if record.node_name == "replanner"),
        "review_count": sum(
            1 for decision in task_state.governance_history if decision.directive.value == "wait_human_review"
        ),
        "abort_count": 1 if task_state.current_state.value == "aborted" else 0,
        "evidence_count": len(task_state.observed_evidence),
        "tool_usage": {result.request.tool_name.value for result in task_state.tool_results_history},
        "governance_distribution": _distribution_from_governance(task_state=task_state),
        "validation_distribution": _distribution_from_validation(task_state=task_state),
    }


# Build the descriptive v1-like assessment when history is still below the activation threshold.
def _build_foundation_only_assessment(
    *,
    task_state: TaskState,
    historical_run_count: int,
) -> BehavioralAssessment:
    history_available = historical_run_count > 0
    notes = (
        "behavioral_history_below_threshold",
        "sqlite_history_available" if history_available else "no_historical_runs_yet",
        f"current_governance_passes={len(task_state.governance_history)}",
    )
    summary = (
        f"Behavioral foundation found {historical_run_count} historical runs, below the scoring threshold."
        if history_available
        else "Behavioral foundation found no historical runs in SQLite yet."
    )
    return BehavioralAssessment(
        history_available=history_available,
        historical_run_count=historical_run_count,
        feature_snapshot_version="behavioral_features_v2",
        summary=summary,
        notes=notes,
        behavioral_recommended_directive=BehavioralRecommendedDirective.CONTINUE,
    )


# Convert one SQLite row into the candidate dictionary used by similarity scoring.
def _row_to_candidate(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "thread_id": str(row[0]),
        "final_state": str(row[1]),
        "domain_type": str(row[2]),
        "sensitivity": str(row[3]),
        "policy_profile_name": str(row[4]),
        "actor_turn_count": int(row[5]),
        "tool_call_count": int(row[6]),
        "replan_count": int(row[7]),
        "review_count": int(row[8]),
        "abort_count": int(row[9]),
        "has_external_communication": int(row[10]),
    }


# Score one historical candidate against the current runtime profile.
def _score_candidate_similarity(
    *,
    current_profile: dict[str, Any],
    candidate_run: dict[str, Any],
    candidate_features: dict[str, str],
    candidate_tool_usage: set[str],
    candidate_governance_distribution: dict[str, int],
    candidate_validation_distribution: dict[str, int],
) -> dict[str, Any]:
    candidate_step_count = _int_feature(candidate_features, "step_count", 0)
    candidate_actor_turn_count = _int_feature(
        candidate_features,
        "actor_turn_count",
        int(candidate_run["actor_turn_count"]),
    )
    candidate_tool_call_count = _int_feature(
        candidate_features,
        "tool_call_count",
        int(candidate_run["tool_call_count"]),
    )
    candidate_replan_count = _int_feature(
        candidate_features,
        "replan_count",
        int(candidate_run["replan_count"]),
    )
    candidate_review_count = _int_feature(
        candidate_features,
        "review_count",
        int(candidate_run["review_count"]),
    )
    candidate_abort_count = _int_feature(
        candidate_features,
        "abort_count",
        int(candidate_run["abort_count"]),
    )

    score = (
        0.10 * _binary_match(current_profile["domain_type"], candidate_run["domain_type"])
        + 0.10 * _binary_match(current_profile["sensitivity"], candidate_run["sensitivity"])
        + 0.10 * _binary_match(
            current_profile["policy_profile_name"],
            candidate_run["policy_profile_name"],
        )
        + 0.05 * _binary_match(
            current_profile["has_external_communication"],
            candidate_run["has_external_communication"],
        )
        + 0.09 * _ratio_similarity(current_profile["step_count"], candidate_step_count)
        + 0.08 * _ratio_similarity(current_profile["actor_turn_count"], candidate_actor_turn_count)
        + 0.08 * _ratio_similarity(current_profile["tool_call_count"], candidate_tool_call_count)
        + 0.05 * _ratio_similarity(current_profile["replan_count"], candidate_replan_count)
        + 0.05 * _ratio_similarity(current_profile["review_count"], candidate_review_count)
        + 0.05 * _ratio_similarity(current_profile["abort_count"], candidate_abort_count)
        + 0.10 * _ratio_similarity(
            current_profile["evidence_count"],
            _int_feature(candidate_features, "evidence_count", 0),
        )
        + 0.10 * _jaccard_similarity(current_profile["tool_usage"], candidate_tool_usage)
        + 0.03 * _distribution_similarity(
            current_profile["governance_distribution"],
            candidate_governance_distribution,
        )
        + 0.02 * _distribution_similarity(
            current_profile["validation_distribution"],
            candidate_validation_distribution,
        )
    )
    return {
        **candidate_run,
        "score": max(0.0, min(score, 1.0)),
        "step_count": candidate_step_count,
        "tool_usage": candidate_tool_usage,
        "governance_distribution": candidate_governance_distribution,
        "validation_distribution": candidate_validation_distribution,
    }


# Compute the novelty score from the strongest historical similarity matches.
def _compute_novelty_score(*, similarity_scores: list[float]) -> float | None:
    if not similarity_scores:
        return None
    average_similarity = sum(similarity_scores) / len(similarity_scores)
    return max(0.0, min(1.0, 1.0 - average_similarity))


# Compute one deviation score and record the strongest contributing feature.
def _compute_behavior_deviation(
    *,
    current_profile: dict[str, Any],
    baseline_candidates: list[dict[str, Any]],
) -> tuple[float | None, str | None]:
    if not baseline_candidates:
        return None, None

    tracked_features = (
        "step_count",
        "actor_turn_count",
        "tool_call_count",
        "replan_count",
        "review_count",
        "abort_count",
    )
    feature_deviations: dict[str, float] = {}
    for feature_name in tracked_features:
        baseline_values = [float(candidate.get(feature_name, 0.0)) for candidate in baseline_candidates]
        if not baseline_values:
            continue
        baseline_mean = sum(baseline_values) / len(baseline_values)
        feature_deviations[feature_name] = _normalized_deviation(
            current=float(current_profile[feature_name]),
            baseline_mean=baseline_mean,
        )

    if not feature_deviations:
        return None, None
    deviation_driver = max(feature_deviations, key=feature_deviations.get)
    overall_deviation = sum(feature_deviations.values()) / len(feature_deviations)
    return max(0.0, min(overall_deviation, 1.0)), deviation_driver


# Compute the review rate among the top comparable historical runs.
def _compute_review_rate(scored_candidates: list[dict[str, Any]]) -> float:
    if not scored_candidates:
        return 0.0
    review_runs = sum(1 for item in scored_candidates if int(item.get("review_count", 0)) > 0)
    return review_runs / len(scored_candidates)


# Compute historical terminal-failure frequency as a non-authoritative behavioural fact.
def _compute_failure_rate(scored_candidates: list[dict[str, Any]]) -> float:
    if not scored_candidates:
        return 0.0
    failed_runs = sum(1 for item in scored_candidates if int(item.get("abort_count", 0)) > 0)
    return failed_runs / len(scored_candidates)


# Recommend a conservative behavioural directive from the computed scores.
def _recommend_behavioral_directive(
    *,
    task_state: TaskState,
    historical_similarity_score: float | None,
    behavior_deviation_score: float | None,
    novelty_score: float | None,
    review_rate: float,
    deviation_driver: str | None,
) -> tuple[BehavioralRecommendedDirective, tuple[str, ...]]:
    flags: list[str] = []
    if (
        novelty_score is not None
        and novelty_score >= 0.70
    ):
        flags.append("high_novelty")
        return BehavioralRecommendedDirective.REQUEST_MORE_EVIDENCE, tuple(flags)

    if historical_similarity_score is not None and historical_similarity_score < 0.35:
        flags.append("low_historical_similarity")
        return BehavioralRecommendedDirective.REQUEST_MORE_EVIDENCE, tuple(flags)

    if behavior_deviation_score is not None and behavior_deviation_score >= 0.80:
        flags.append("high_behavior_deviation")
        return BehavioralRecommendedDirective.WAIT_HUMAN_REVIEW, tuple(flags)

    if review_rate >= 0.50:
        flags.append("historical_review_rate_high")
        return BehavioralRecommendedDirective.WAIT_HUMAN_REVIEW, tuple(flags)

    if (
        behavior_deviation_score is not None
        and behavior_deviation_score >= 0.60
        and deviation_driver in {"actor_turn_count", "replan_count", "tool_call_count"}
    ):
        flags.append("trajectory_deviation")
        flags.append(f"deviation_driver_{deviation_driver}")
        return BehavioralRecommendedDirective.REPLAN, tuple(flags)

    return BehavioralRecommendedDirective.CONTINUE, tuple(flags)


# Summarize the behavioural result in one audit-friendly sentence.
def _build_behavioral_summary(
    *,
    matched_run_count: int,
    historical_similarity_score: float | None,
    behavior_deviation_score: float | None,
    novelty_score: float | None,
    behavioral_directive: BehavioralRecommendedDirective,
) -> str:
    return (
        "Behavioral shadow telemetry matched "
        f"{matched_run_count} comparable runs, best similarity="
        f"{_format_score(historical_similarity_score)}, deviation={_format_score(behavior_deviation_score)}, "
        f"novelty={_format_score(novelty_score)}. Counterfactual recommendation="
        f"'{behavioral_directive.value}' (shadow only; no live decision influence)."
    )


# Count governance directives so historical comparisons stay compact and deterministic.
def _distribution_from_governance(*, task_state: TaskState) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for decision in task_state.governance_history:
        distribution[decision.directive.value] = distribution.get(decision.directive.value, 0) + 1
    return distribution


# Count completion attestations without turning validation into a routing authority.
def _distribution_from_validation(*, task_state: TaskState) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for validation in task_state.step_validations:
        key = "completion_attestation"
        distribution[key] = distribution.get(key, 0) + 1
    return distribution


# Compute a stable ratio-style similarity for quantitative features.
def _ratio_similarity(current: float, historical: float) -> float:
    if current == historical == 0:
        return 1.0
    return max(0.0, 1.0 - min(abs(current - historical) / max(current, historical, 1.0), 1.0))


# Compute one normalized deviation against the baseline mean.
def _normalized_deviation(*, current: float, baseline_mean: float) -> float:
    return max(0.0, min(abs(current - baseline_mean) / max(baseline_mean, 1.0), 1.0))


# Return one exact-match similarity for stable categorical fields.
def _binary_match(left: Any, right: Any) -> float:
    return 1.0 if left == right else 0.0


# Compute a Jaccard similarity over the tool sets used in comparable runs.
def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


# Compare two sparse distributions by overlap on normalized frequencies.
def _distribution_similarity(left: dict[str, int], right: dict[str, int]) -> float:
    if not left and not right:
        return 1.0
    keys = set(left) | set(right)
    if not keys:
        return 1.0
    left_total = max(sum(left.values()), 1)
    right_total = max(sum(right.values()), 1)
    distance = 0.0
    for key in keys:
        distance += abs((left.get(key, 0) / left_total) - (right.get(key, 0) / right_total))
    return max(0.0, 1.0 - min(distance / len(keys), 1.0))


# Parse one persisted integer-like feature defensively.
def _int_feature(feature_map: dict[str, str], feature_name: str, default: int) -> int:
    raw_value = feature_map.get(feature_name)
    if raw_value is None or raw_value == "":
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


# Parse one JSON object feature while failing closed on malformed data.
def _parse_json_dict(raw_value: Any) -> dict[str, Any]:
    if not isinstance(raw_value, str) or not raw_value.strip():
        return {}
    try:
        parsed_value = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    return parsed_value if isinstance(parsed_value, dict) else {}


# Format optional scores consistently in summaries.
def _format_score(score: float | None) -> str:
    return "n/a" if score is None else f"{score:.2f}"


__all__ = [
    "BehavioralAssessment",
    "BehavioralRecommendedDirective",
    "BehavioralStorePort",
    "BehavioralStoreSettings",
    "SQLiteBehavioralStore",
    "build_behavioral_assessment",
    "build_default_behavioral_store",
    "evaluate_behavioral_policy",
    "get_default_behavioral_store_path",
    "persist_behavioral_snapshot",
    "persist_behavioral_runtime_snapshot",
    "persist_judge_snapshot",
]
