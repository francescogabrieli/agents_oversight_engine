"""Evaluate one normalized Layer 2 trace."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .contracts import (
    Layer2EvaluationRun,
    NormalizedHumanReview,
    NormalizedObservedEvidence,
    NormalizedOversightAssessment,
    NormalizedPlanStep,
    NormalizedToolCall,
    NormalizedTracePayload,
)


# Compute the run-level evaluation from a normalized trace payload.
def evaluate_normalized_trace(
    normalized: NormalizedTracePayload, *, source_path: Path
) -> Layer2EvaluationRun:
    governance_directive_counts = _count_values(
        decision.directive for decision in normalized.governance_decisions
    )
    human_review_decision_counts = _count_values(
        _iter_human_review_decision_types(normalized.human_review)
    )
    evidence_support_counts = _count_values(
        evidence.support_level for evidence in normalized.observed_evidence
    )
    typed_mismatches, heuristic_mismatches = _count_requirement_mismatches(
        normalized=normalized
    )

    tool_call_count = len(normalized.tool_calls)
    failed_tool_count = sum(1 for call in normalized.tool_calls if call.success is False)
    duplicate_block_count = sum(1 for call in normalized.tool_calls if _is_duplicate_block(call))
    low_score_count = sum(
        1 for assessment in normalized.oversight_assessments if _has_low_score(assessment)
    )
    degraded_count = sum(
        1
        for assessment in normalized.oversight_assessments
        if _is_degraded_trajectory(assessment)
    )

    return Layer2EvaluationRun(
        source_path=str(source_path),
        status="ok",
        runtime_state=normalized.runtime.current_state,
        thread_id=normalized.runtime.thread_id,
        task_outcome=normalized.runtime.current_state,
        governance_directive_counts=governance_directive_counts,
        human_review_decision_counts=human_review_decision_counts,
        evidence_support_counts=evidence_support_counts,
        typed_requirement_mismatch_count=typed_mismatches,
        heuristic_requirement_mismatch_count=heuristic_mismatches,
        tool_call_count=tool_call_count,
        failed_tool_count=failed_tool_count,
        duplicate_block_count=duplicate_block_count,
        oversight_assessment_count=len(normalized.oversight_assessments),
        low_score_assessment_count=low_score_count,
        degraded_trajectory_assessment_count=degraded_count,
        average_evidence_quality=_average(
            item.evidence_quality.score for item in normalized.oversight_assessments
        ),
        average_operational_risk=_average(
            item.operational_risk.score for item in normalized.oversight_assessments
        ),
        average_trajectory_health=_average(
            item.trajectory_health.score for item in normalized.oversight_assessments
        ),
        unresolved_human_review=normalized.human_review.pending_request is not None,
        human_review_activity=normalized.human_review.has_activity,
    )


# Build the stable zero-metric representation for a failed trace.
def failed_run(*, source_path: Path, status: str, error: str) -> Layer2EvaluationRun:
    return Layer2EvaluationRun(
        source_path=str(source_path),
        status=status,
        runtime_state=None,
        thread_id=None,
        task_outcome=None,
        governance_directive_counts={},
        human_review_decision_counts={},
        evidence_support_counts={},
        typed_requirement_mismatch_count=0,
        heuristic_requirement_mismatch_count=0,
        tool_call_count=0,
        failed_tool_count=0,
        duplicate_block_count=0,
        oversight_assessment_count=0,
        low_score_assessment_count=0,
        degraded_trajectory_assessment_count=0,
        average_evidence_quality=None,
        average_operational_risk=None,
        average_trajectory_health=None,
        unresolved_human_review=False,
        human_review_activity=False,
        error=error,
    )


# Count evidence requirement mismatches separately for typed metadata and heuristic fallback.
def _count_requirement_mismatches(*, normalized: NormalizedTracePayload) -> tuple[int, int]:
    plan_step = _resolve_evaluated_plan_step(normalized=normalized)
    if plan_step is None:
        return 0, 0

    available_level = _max_observed_evidence_support_level(normalized.observed_evidence)
    required_level, source = _resolve_required_support_level(plan_step=plan_step)
    if _evidence_support_level_rank(available_level) >= _evidence_support_level_rank(required_level):
        return 0, 0
    if source in {
        "plan_step_evidence_requirement",
        "plan_step_claim_precision_ceiling",
        "plan_step_kind",
        "step_validation",
    }:
        return 1, 0
    return 0, 1


# Resolve the plan step used for the evaluation mismatch check.
def _resolve_evaluated_plan_step(
    *, normalized: NormalizedTracePayload
) -> NormalizedPlanStep | None:
    if not normalized.plan_steps:
        return None
    if normalized.current_plan_step_id is not None:
        for step in normalized.plan_steps:
            if step.step_id == normalized.current_plan_step_id:
                return step
    return normalized.plan_steps[-1]


# Resolve the effective requirement and its provenance from normalized trace data.
def _resolve_required_support_level(*, plan_step: NormalizedPlanStep) -> tuple[str, str]:
    if plan_step.resolved_required_evidence_support_level:
        return plan_step.resolved_required_evidence_support_level, (
            plan_step.resolved_required_evidence_support_source or "heuristic_step"
        )
    if plan_step.evidence_requirement:
        return plan_step.evidence_requirement, "plan_step_evidence_requirement"
    if plan_step.claim_precision_ceiling:
        return plan_step.claim_precision_ceiling, "plan_step_claim_precision_ceiling"
    return "local_only", "heuristic_step"


# Return the strongest observed evidence level from the normalized trace.
def _max_observed_evidence_support_level(
    observed_evidence: tuple[NormalizedObservedEvidence, ...],
) -> str:
    strongest = "local_only"
    for evidence in observed_evidence:
        if _evidence_support_level_rank(evidence.support_level) > _evidence_support_level_rank(
            strongest
        ):
            strongest = evidence.support_level
    return strongest


# Order evidence support levels from weakest to strongest for evaluation comparisons.
def _evidence_support_level_rank(level: str) -> int:
    ordering = {
        "local_only": 0,
        "source_discovery": 1,
        "high_level_summary": 2,
        "detailed_extracted": 3,
        "primary_legal": 4,
    }
    return ordering.get(level, 0)


# Count string values while skipping missing entries.
def _count_values(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


# Extract human-review decision types from the normalized trace payload.
def _iter_human_review_decision_types(human_review: NormalizedHumanReview) -> Iterable[str]:
    if human_review.decision_history:
        for decision in human_review.decision_history:
            decision_type = decision.get("decision_type")
            if isinstance(decision_type, str):
                yield decision_type
        return
    if human_review.last_decision is not None:
        decision_type = human_review.last_decision.get("decision_type")
        if isinstance(decision_type, str):
            yield decision_type


# Compute an average while skipping empty inputs.
def _average(values: Iterable[float]) -> float | None:
    items = list(values)
    if not items:
        return None
    return sum(items) / len(items)


# Recognize duplicate-blocked tool outcomes from the retained tool trace.
def _is_duplicate_block(call: NormalizedToolCall) -> bool:
    if call.success is not False:
        return False
    haystack = " ".join(
        part for part in (call.summary, call.error_message) if part is not None
    ).lower()
    return "duplicate" in haystack or "already executed" in haystack


# Treat weak or degraded assessments as low-score evidence for evaluation purposes.
def _has_low_score(assessment: NormalizedOversightAssessment) -> bool:
    return any(
        signal.score < 0.6 or signal.level in {"weak", "degraded"}
        for signal in (
            assessment.evidence_quality,
            assessment.operational_risk,
            assessment.trajectory_health,
        )
    )


# Detect whether the trajectory health signal degraded in this assessment.
def _is_degraded_trajectory(assessment: NormalizedOversightAssessment) -> bool:
    return (
        assessment.trajectory_health.level == "degraded"
        or assessment.trajectory_health.score < 0.3
    )


__all__ = ["evaluate_normalized_trace", "failed_run"]
