"""Aggregate per-run offline evaluation metrics."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .contracts import Layer2EvaluationAggregate, Layer2EvaluationRun


# Aggregate all parsed runs into the final report summary.
def build_aggregate(
    *, runs: Sequence[Layer2EvaluationRun], discovered_file_count: int
) -> Layer2EvaluationAggregate:
    parsed_runs = [run for run in runs if run.status == "ok"]
    task_outcome_distribution = _count_values(
        run.task_outcome for run in parsed_runs if run.task_outcome is not None
    )
    governance_distribution = _merge_nested_counts(
        run.governance_directive_counts for run in parsed_runs
    )
    human_review_distribution = _merge_nested_counts(
        run.human_review_decision_counts for run in parsed_runs
    )
    evidence_distribution = _merge_nested_counts(
        run.evidence_support_counts for run in parsed_runs
    )
    total_governance_decisions = sum(governance_distribution.values())
    escalation_count = sum(
        count
        for directive, count in governance_distribution.items()
        if directive not in {"continue", "continue_read_only"}
    )
    tool_call_total = sum(run.tool_call_count for run in parsed_runs)
    failed_tool_total = sum(run.failed_tool_count for run in parsed_runs)
    duplicate_block_total = sum(run.duplicate_block_count for run in parsed_runs)

    return Layer2EvaluationAggregate(
        total_inputs=len(runs),
        discovered_files=discovered_file_count,
        parsed_runs=len(parsed_runs),
        failed_runs=len(runs) - len(parsed_runs),
        task_outcome_distribution=task_outcome_distribution,
        governance_directive_distribution=governance_distribution,
        governance_escalation_rate=(
            escalation_count / total_governance_decisions if total_governance_decisions else 0.0
        ),
        human_review_frequency=(
            sum(1 for run in parsed_runs if run.human_review_activity) / len(parsed_runs)
            if parsed_runs
            else 0.0
        ),
        human_review_decision_distribution=human_review_distribution,
        unresolved_human_review_count=sum(
            1 for run in parsed_runs if run.unresolved_human_review
        ),
        evidence_support_distribution=evidence_distribution,
        typed_requirement_mismatch_total=sum(
            run.typed_requirement_mismatch_count for run in parsed_runs
        ),
        heuristic_requirement_mismatch_total=sum(
            run.heuristic_requirement_mismatch_count for run in parsed_runs
        ),
        tool_call_total=tool_call_total,
        failed_tool_total=failed_tool_total,
        duplicate_block_total=duplicate_block_total,
        failed_tool_rate=(failed_tool_total / tool_call_total if tool_call_total else 0.0),
        duplicate_block_rate=(duplicate_block_total / tool_call_total if tool_call_total else 0.0),
        oversight_assessment_total=sum(run.oversight_assessment_count for run in parsed_runs),
        low_score_assessment_total=sum(run.low_score_assessment_count for run in parsed_runs),
        degraded_trajectory_assessment_total=sum(
            run.degraded_trajectory_assessment_count for run in parsed_runs
        ),
        average_evidence_quality=_average(
            run.average_evidence_quality
            for run in parsed_runs
            if run.average_evidence_quality is not None
        ),
        average_operational_risk=_average(
            run.average_operational_risk
            for run in parsed_runs
            if run.average_operational_risk is not None
        ),
        average_trajectory_health=_average(
            run.average_trajectory_health
            for run in parsed_runs
            if run.average_trajectory_health is not None
        ),
    )


# Count string values while preserving first-seen insertion order.
def _count_values(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


# Merge multiple per-run count dictionaries.
def _merge_nested_counts(dicts: Iterable[dict[str, int]]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for counts in dicts:
        for key, value in counts.items():
            merged[key] = merged.get(key, 0) + value
    return merged


# Compute an average while skipping empty inputs.
def _average(values: Iterable[float]) -> float | None:
    items = list(values)
    if not items:
        return None
    return sum(items) / len(items)


__all__ = ["build_aggregate"]
