from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from .store import EvaluationStore


@dataclass(frozen=True)
class EvaluationSummary:
    total_runs: int
    judged_runs: int
    runtime_statuses: dict[str, int]
    task_results: dict[str, int]
    review_decisions: dict[str, int]
    external_statuses: dict[str, int]
    technical_completion_rate: float
    correct_rate: float | None
    incorrect_completion_rate: float | None
    human_intervention_rate: float
    failed_tool_rate: float
    escalation_confusion: dict[str, int]
    escalation_precision: float | None
    escalation_recall: float | None

    # Expose a JSON-ready report contract.
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Aggregate the core thesis metrics from atomic evaluation facts.
def build_evaluation_summary(
    store: EvaluationStore, *, experiment_id: str | None = None
) -> EvaluationSummary:
    where = " WHERE experiment_id = ?" if experiment_id else ""
    parameters = (experiment_id,) if experiment_id else ()
    runs = store.query(f"SELECT * FROM runs{where}", parameters)
    run_ids = [str(run["run_id"]) for run in runs]
    governance = _child_rows(store, "governance_events", run_ids)
    reviews = _child_rows(store, "human_reviews", run_ids)
    operations = _child_rows(store, "external_operations", run_ids)

    runtime_statuses = Counter(str(run["runtime_status"]) for run in runs)
    task_results = Counter(str(run["task_result"]) for run in runs if run["task_result"] is not None)
    review_decisions = Counter(str(review["decision"]) for review in reviews)
    external_statuses = Counter(str(operation["execution_status"]) for operation in operations)
    completed_runs = [run for run in runs if run["runtime_status"] == "completed"]
    judged_runs = [run for run in runs if run["task_result"] is not None]
    incorrect_completed = [run for run in completed_runs if run["task_result"] == "incorrect"]
    tool_call_total = sum(int(run["tool_call_count"]) for run in runs)
    failed_tool_total = sum(int(run["failed_tool_count"]) for run in runs)
    confusion = _escalation_confusion(governance)

    return EvaluationSummary(
        total_runs=len(runs),
        judged_runs=len(judged_runs),
        runtime_statuses=dict(sorted(runtime_statuses.items())),
        task_results=dict(sorted(task_results.items())),
        review_decisions=dict(sorted(review_decisions.items())),
        external_statuses=dict(sorted(external_statuses.items())),
        technical_completion_rate=_ratio(len(completed_runs), len(runs)) or 0.0,
        correct_rate=_ratio(task_results["correct"], len(judged_runs)),
        incorrect_completion_rate=_ratio(len(incorrect_completed), len(completed_runs)),
        human_intervention_rate=_ratio(len({review["run_id"] for review in reviews}), len(runs)) or 0.0,
        failed_tool_rate=_ratio(failed_tool_total, tool_call_total) or 0.0,
        escalation_confusion=confusion,
        escalation_precision=_ratio(confusion["true_positive"], confusion["true_positive"] + confusion["false_positive"]),
        escalation_recall=_ratio(confusion["true_positive"], confusion["true_positive"] + confusion["false_negative"]),
    )


# Load child facts for a selected run set without leaking SQL into plotting.
def _child_rows(store: EvaluationStore, table: str, run_ids: list[str]) -> list[dict[str, Any]]:
    if not run_ids:
        return []
    placeholders = ", ".join("?" for _ in run_ids)
    return store.query(f"SELECT * FROM {table} WHERE run_id IN ({placeholders})", run_ids)


# Derive the oversight confusion matrix only from reviewed governance events.
def _escalation_confusion(events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"true_positive": 0, "false_positive": 0, "false_negative": 0, "true_negative": 0}
    for event in events:
        needed = event["human_review_was_needed"]
        if needed is None:
            continue
        actual = bool(event["requires_human_review"])
        expected = bool(needed)
        if actual and expected:
            counts["true_positive"] += 1
        elif actual and not expected:
            counts["false_positive"] += 1
        elif not actual and expected:
            counts["false_negative"] += 1
        else:
            counts["true_negative"] += 1
    return counts


# Return a safe ratio or None when the metric has no denominator.
def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None
