from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from layer1_intake import IntakeResult

from .state import TaskState
from .tool_contract import EvidenceSupportLevel


@dataclass(frozen=True)
class BehavioralAssessment:
    # BehavioralAssessment is the non-blocking historical context surfaced to governance.
    history_available: bool
    historical_run_count: int
    feature_snapshot_version: str
    summary: str
    notes: tuple[str, ...]


@dataclass(frozen=True)
class BehavioralStoreSettings:
    # BehavioralStoreSettings keeps the local SQLite path explicit and testable.
    database_path: Path


class BehavioralStorePort(Protocol):
    # BehavioralStorePort decouples governance from one persistence backend.
    def build_behavioral_assessment(self, *, thread_id: str, task_state: TaskState) -> BehavioralAssessment: ...

    def persist_behavioral_snapshot(
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

    # Return one descriptive assessment without making behavioural logic decision-making yet.
    def build_behavioral_assessment(self, *, thread_id: str, task_state: TaskState) -> BehavioralAssessment:
        with closing(self._connect()) as connection:
            self._ensure_schema(connection)
            historical_run_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM runs WHERE thread_id <> ?",
                    (thread_id,),
                ).fetchone()[0]
            )

        history_available = historical_run_count > 0
        notes = (
            "behavioral_foundation_only",
            "sqlite_history_available" if history_available else "no_historical_runs_yet",
            f"current_governance_passes={len(task_state.governance_history)}",
        )
        summary = (
            f"Behavioral foundation found {historical_run_count} historical runs in SQLite."
            if history_available
            else "Behavioral foundation found no historical runs in SQLite yet."
        )
        return BehavioralAssessment(
            history_available=history_available,
            historical_run_count=historical_run_count,
            feature_snapshot_version="behavioral_features_v1",
            summary=summary,
            notes=notes,
        )

    # Persist the latest run snapshot as a replaceable read model for future behavioural logic.
    def persist_behavioral_snapshot(
        self,
        *,
        thread_id: str,
        intake_result: IntakeResult,
        task_state: TaskState,
    ) -> None:
        with closing(self._connect()) as connection:
            self._ensure_schema(connection)
            self._replace_run_snapshot(
                connection=connection,
                thread_id=thread_id,
                intake_result=intake_result,
                task_state=task_state,
            )
            connection.commit()

    # Open one SQLite connection and ensure the parent directory exists first.
    def _connect(self) -> sqlite3.Connection:
        self.settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.settings.database_path)

    # Create the minimal behavioural schema needed for v1 historical memory.
    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                thread_id TEXT PRIMARY KEY,
                final_state TEXT NOT NULL,
                domain_type TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                policy_profile_name TEXT NOT NULL,
                budget_max_steps INTEGER NOT NULL,
                budget_max_tool_calls INTEGER,
                actor_turn_count INTEGER NOT NULL,
                tool_call_count INTEGER NOT NULL,
                replan_count INTEGER NOT NULL,
                review_count INTEGER NOT NULL,
                abort_count INTEGER NOT NULL,
                has_external_communication INTEGER NOT NULL,
                max_evidence_support_level TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS steps (
                thread_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                status TEXT NOT NULL,
                actor_result_type TEXT,
                validation_next_action TEXT,
                turn_count INTEGER NOT NULL,
                PRIMARY KEY (thread_id, step_id)
            );
            CREATE TABLE IF NOT EXISTS tool_calls (
                thread_id TEXT NOT NULL,
                call_index INTEGER NOT NULL,
                plan_step_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                success INTEGER,
                support_level TEXT,
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
                evidence_quality_score REAL NOT NULL,
                evidence_quality_level TEXT NOT NULL,
                operational_risk_score REAL NOT NULL,
                operational_risk_level TEXT NOT NULL,
                trajectory_health_score REAL NOT NULL,
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
            """
        )

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
                replan_count, review_count, abort_count, has_external_communication,
                max_evidence_support_level
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                _max_support_level(task_state=task_state).value,
            ),
        )

        for step in task_state.plan_steps:
            actor_result_type = None
            validation_next_action = None
            turn_count = 0
            for executed_step in task_state.executed_steps:
                if executed_step.step_id == step.step_id:
                    actor_result_type = executed_step.result_type.value
                    break
            for validation in reversed(task_state.step_validations):
                if validation.plan_step_id == step.step_id:
                    validation_next_action = validation.next_action.value
                    break
            for scratchpad_entry in task_state.scratchpad:
                if scratchpad_entry.plan_step_id == step.step_id:
                    turn_count = max(turn_count, scratchpad_entry.turn_index)
            connection.execute(
                """
                INSERT INTO steps (
                    thread_id, step_id, status, actor_result_type, validation_next_action, turn_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    step.step_id,
                    step.status.value,
                    actor_result_type,
                    validation_next_action,
                    turn_count,
                ),
            )

        for call_index, tool_result in enumerate(task_state.tool_results_history, start=1):
            support_level = None
            for evidence in task_state.observed_evidence:
                if evidence.plan_step_id == tool_result.request.plan_step_id:
                    support_level = evidence.support_level.value
                    break
            connection.execute(
                """
                INSERT INTO tool_calls (
                    thread_id, call_index, plan_step_id, tool_name, success, support_level
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    call_index,
                    tool_result.request.plan_step_id,
                    tool_result.request.tool_name.value,
                    None if tool_result.success is None else int(bool(tool_result.success)),
                    support_level,
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


# Build the non-blocking behavioural assessment from the configured store.
def build_behavioral_assessment(
    *,
    thread_id: str,
    task_state: TaskState,
    behavioral_store: BehavioralStorePort | None = None,
) -> BehavioralAssessment:
    resolved_store = behavioral_store or build_default_behavioral_store()
    return resolved_store.build_behavioral_assessment(thread_id=thread_id, task_state=task_state)


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


# Materialize the minimum feature vector needed for the v1 behavioural foundation.
def _build_feature_snapshot(*, task_state: TaskState) -> dict[str, str]:
    validation_distribution: dict[str, int] = {}
    for validation in task_state.step_validations:
        validation_distribution[validation.next_action.value] = (
            validation_distribution.get(validation.next_action.value, 0) + 1
        )

    governance_distribution: dict[str, int] = {}
    for decision in task_state.governance_history:
        governance_distribution[decision.directive.value] = (
            governance_distribution.get(decision.directive.value, 0) + 1
        )

    return {
        "feature_snapshot_version": "behavioral_features_v1",
        "step_count": str(len(task_state.plan_steps)),
        "actor_turn_count": str(task_state.actor_turn_count),
        "tool_call_count": str(task_state.tool_call_count),
        "replan_count": str(sum(1 for record in task_state.step_history if record.node_name == "replanner")),
        "review_count": str(
            sum(1 for decision in task_state.governance_history if decision.directive.value == "wait_human_review")
        ),
        "abort_count": str(1 if task_state.current_state.value == "aborted" else 0),
        "has_external_communication": "1" if task_state.external_communication is not None else "0",
        "max_evidence_support_level": _max_support_level(task_state=task_state).value,
        "governance_distribution": json.dumps(governance_distribution, ensure_ascii=False, sort_keys=True),
        "validation_distribution": json.dumps(validation_distribution, ensure_ascii=False, sort_keys=True),
    }


# Return the strongest support level observed so the store can retain evidence quality context.
def _max_support_level(*, task_state: TaskState) -> EvidenceSupportLevel:
    if not task_state.observed_evidence:
        return EvidenceSupportLevel.LOCAL_ONLY
    ordering = {
        EvidenceSupportLevel.LOCAL_ONLY: 0,
        EvidenceSupportLevel.SOURCE_DISCOVERY: 1,
        EvidenceSupportLevel.HIGH_LEVEL_SUMMARY: 2,
        EvidenceSupportLevel.DETAILED_EXTRACTED: 3,
        EvidenceSupportLevel.PRIMARY_LEGAL: 4,
    }
    return max(task_state.observed_evidence, key=lambda item: ordering[item.support_level]).support_level


__all__ = [
    "BehavioralAssessment",
    "BehavioralStorePort",
    "BehavioralStoreSettings",
    "SQLiteBehavioralStore",
    "build_behavioral_assessment",
    "build_default_behavioral_store",
    "get_default_behavioral_store_path",
    "persist_behavioral_snapshot",
]
