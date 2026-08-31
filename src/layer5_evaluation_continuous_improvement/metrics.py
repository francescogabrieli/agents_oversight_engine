from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from .reason_code_pillars import classify_reason_code_pillar
from .store import EvaluationStore


@dataclass(frozen=True)
class EvaluationSummary:
    total_runs: int
    judged_runs: int
    runtime_statuses: dict[str, int]
    termination_reasons: dict[str, int]
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
    # The realistic pool spans ten task families on purpose, so the per-family view is
    # the comparison the corpus was designed to support; without it every run collapses
    # into one undifferentiated average.
    family_breakdown: dict[str, dict[str, Any]]
    labeled_governance_events: int
    total_governance_events: int
    # No archived run ever reached `completed` after replanning at least once - every
    # one ended in `waiting_human_review` or `aborted` instead. This measures how often
    # a replan is a genuine recovery rather than asserting it as a guaranteed capability.
    runs_with_replan: int
    replan_recovery_rate: float | None

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
    governance = store.child_rows("governance_events", run_ids)
    reviews = store.child_rows("human_reviews", run_ids)
    operations = store.child_rows("external_operations", run_ids)

    runtime_statuses = Counter(str(run["runtime_status"]) for run in runs)
    termination_reasons = Counter(
        str(run["termination_reason"])
        for run in runs
        if run.get("termination_reason")
    )
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

    runs_with_replan = [run for run in runs if int(run["replanning_count"]) > 0]
    recovered_after_replan = [
        run for run in runs_with_replan if run["runtime_status"] == "completed"
    ]

    return EvaluationSummary(
        family_breakdown=_family_breakdown(runs, reviews),
        runs_with_replan=len(runs_with_replan),
        replan_recovery_rate=_ratio(len(recovered_after_replan), len(runs_with_replan)),
        labeled_governance_events=sum(
            1 for event in governance if event["human_review_was_needed"] is not None
        ),
        total_governance_events=len(governance),
        total_runs=len(runs),
        judged_runs=len(judged_runs),
        runtime_statuses=dict(sorted(runtime_statuses.items())),
        termination_reasons=dict(sorted(termination_reasons.items())),
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


# Aggregate the headline outcomes per task family declared by the evaluation manifest.
def _family_breakdown(
    runs: list[dict[str, Any]], reviews: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    reviewed_run_ids = {review["run_id"] for review in reviews}
    families: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        # Runs imported outside a campaign carry no family; they are grouped explicitly
        # rather than silently folded into a real family.
        family = str(run.get("scenario_family") or "non dichiarata")
        families.setdefault(family, []).append(run)

    breakdown: dict[str, dict[str, Any]] = {}
    for family, family_runs in sorted(families.items()):
        judged = [run for run in family_runs if run["task_result"] is not None]
        completed = [run for run in family_runs if run["runtime_status"] == "completed"]
        intervened = [run for run in family_runs if run["run_id"] in reviewed_run_ids]
        breakdown[family] = {
            "runs": len(family_runs),
            "technical_completion_rate": _ratio(len(completed), len(family_runs)),
            "correct_rate": _ratio(
                sum(1 for run in judged if run["task_result"] == "correct"), len(judged)
            ),
            "human_intervention_rate": _ratio(len(intervened), len(family_runs)),
            "avg_tool_calls": _mean([int(run["tool_call_count"]) for run in family_runs]),
            "avg_run_duration_seconds": _mean(
                [
                    float(run["duration_seconds"])
                    for run in family_runs
                    if run.get("duration_seconds") is not None
                ]
            ),
        }
    return breakdown


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
