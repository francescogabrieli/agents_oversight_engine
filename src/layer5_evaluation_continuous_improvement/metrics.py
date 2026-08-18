from __future__ import annotations

import json
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
    reason_codes: dict[str, int]
    pillar_breakdown: dict[str, int]
    avg_actor_turns: float | None
    avg_tool_calls: float | None
    avg_replanning_count: float | None
    avg_governance_passes: float | None
    avg_review_time_seconds: float | None
    avg_run_duration_seconds: float | None

    # Expose a JSON-ready report contract.
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Classify a raw governance reason code into its architectural pillar.
def classify_reason_code_pillar(code: str) -> str:
    code_lower = code.lower().strip()
    if code_lower.startswith("judge_") or "confidence" in code_lower:
        return "Semantic Judge"
    if code_lower.startswith("evidence_") or "evidence" in code_lower:
        return "Evidence Pillar"
    if code_lower.startswith("trajectory_") or any(
        k in code_lower for k in ("replan", "resubmi", "revision_limit", "recovery", "repeated")
    ):
        return "Trajectory Pillar"
    if code_lower.startswith("behavioral_") or "operational_risk" in code_lower:
        return "Behavioral / Risk Pillar"
    if any(
        k in code_lower
        for k in (
            "static_",
            "read_only",
            "prohibited",
            "database_profile",
            "sql_",
            "capability_",
            "policy_version",
            "opa_",
        )
    ):
        return "Static Policy & Safety"
    return "General Policy"


# Aggregate the core thesis metrics from atomic evaluation facts.
def build_evaluation_summary(
    store: EvaluationStore, *, experiment_id: str | None = None
) -> EvaluationSummary:
    where = " WHERE experiment_id = ?" if experiment_id else ""
    parameters = (experiment_id,) if experiment_id else ()
    runs = store.query(f"SELECT * FROM runs{where}", parameters)
    run_ids = [str(run["run_id"]) for run in runs]
    governance = store.child_rows("governance_events", run_ids)
    reviews = store.child_rows("human_reviews", run_ids)
    operations = store.child_rows("external_operations", run_ids)

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

    # Reason codes and pillar breakdown
    reason_counter: Counter[str] = Counter()
    pillar_counter: Counter[str] = Counter()
    for event in governance:
        raw_codes = event.get("reason_codes_json")
        if raw_codes:
            try:
                parsed_codes = json.loads(raw_codes)
                if isinstance(parsed_codes, list):
                    for code in parsed_codes:
                        if isinstance(code, str) and code:
                            reason_counter[code] += 1
                            pillar_counter[classify_reason_code_pillar(code)] += 1
            except (json.JSONDecodeError, TypeError):
                continue

    # Overhead and efficiency metrics
    actor_turns = [int(run["actor_turn_count"]) for run in runs]
    tool_calls = [int(run["tool_call_count"]) for run in runs]
    replannings = [int(run["replanning_count"]) for run in runs]
    governance_passes = [int(run["governance_pass_count"]) for run in runs]
    review_times = [
        float(r["review_time_seconds"])
        for r in reviews
        if r.get("review_time_seconds") is not None
    ]
    durations = [
        float(run["duration_seconds"])
        for run in runs
        if run.get("duration_seconds") is not None
    ]

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
        reason_codes=dict(sorted(reason_counter.items())),
        pillar_breakdown=dict(sorted(pillar_counter.items())),
        avg_actor_turns=_mean(actor_turns),
        avg_tool_calls=_mean(tool_calls),
        avg_replanning_count=_mean(replannings),
        avg_governance_passes=_mean(governance_passes),
        avg_review_time_seconds=_mean(review_times),
        avg_run_duration_seconds=_mean(durations),
    )


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


# Return arithmetic mean of numeric sequence or None if empty.
def _mean(values: list[int] | list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None
