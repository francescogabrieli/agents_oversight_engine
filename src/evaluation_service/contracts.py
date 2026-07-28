"""Typed contracts for offline Layer 2 trace evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Version the offline evaluation contract so report consumers can track changes over time.
EVALUATION_REPORT_VERSION = "layer2_offline_evaluation_v2"


@dataclass(frozen=True)
class EvaluationIssue:
    source_path: str
    issue_type: str
    message: str


@dataclass(frozen=True)
class Layer2EvaluationRun:
    source_path: str
    status: str
    runtime_state: str | None
    thread_id: str | None
    task_outcome: str | None
    governance_directive_counts: dict[str, int]
    human_review_decision_counts: dict[str, int]
    evidence_support_counts: dict[str, int]
    typed_requirement_mismatch_count: int
    heuristic_requirement_mismatch_count: int
    tool_call_count: int
    failed_tool_count: int
    duplicate_block_count: int
    oversight_assessment_count: int
    low_score_assessment_count: int
    degraded_trajectory_assessment_count: int
    average_evidence_quality: float | None
    average_operational_risk: float | None
    average_trajectory_health: float | None
    unresolved_human_review: bool
    human_review_activity: bool
    error: str | None = None


@dataclass(frozen=True)
class Layer2EvaluationAggregate:
    total_inputs: int
    discovered_files: int
    parsed_runs: int
    failed_runs: int
    task_outcome_distribution: dict[str, int]
    governance_directive_distribution: dict[str, int]
    governance_escalation_rate: float
    human_review_frequency: float
    human_review_decision_distribution: dict[str, int]
    unresolved_human_review_count: int
    evidence_support_distribution: dict[str, int]
    typed_requirement_mismatch_total: int
    heuristic_requirement_mismatch_total: int
    tool_call_total: int
    failed_tool_total: int
    duplicate_block_total: int
    failed_tool_rate: float
    duplicate_block_rate: float
    oversight_assessment_total: int
    low_score_assessment_total: int
    degraded_trajectory_assessment_total: int
    average_evidence_quality: float | None
    average_operational_risk: float | None
    average_trajectory_health: float | None


@dataclass(frozen=True)
class Layer2EvaluationReport:
    report_version: str
    generated_at_utc: str
    input_paths: tuple[str, ...]
    discovered_files: tuple[str, ...]
    runs: tuple[Layer2EvaluationRun, ...]
    aggregate: Layer2EvaluationAggregate
    issues: tuple[EvaluationIssue, ...] = field(default_factory=tuple)


# Carry structured failures across discovery, loading, and trace normalization.
class TraceEvaluationError(RuntimeError):
    # Preserve one stable error type across all evaluation infrastructure boundaries.
    def __init__(self, *, issue_type: str, message: str) -> None:
        super().__init__(message)
        self.issue_type = issue_type
        self.message = message


@dataclass(frozen=True)
class NormalizedRuntime:
    thread_id: str
    current_state: str
    bootstrap_completed: bool
    runtime_flags: tuple[str, ...]
    actor_turn_count: int
    tool_call_count: int


@dataclass(frozen=True)
class NormalizedIntake:
    goal: str
    context: str
    sensitivity: str
    success_criteria: tuple[str, ...]
    constraints: tuple[str, ...]
    parse_status: str
    parser_name: str
    prompt_version: str
    schema_version: str
    policy_profile_name: str
    confidence_threshold: float
    max_steps: int | None
    max_tool_calls: int | None
    read_only_mode: bool


@dataclass(frozen=True)
class NormalizedActor:
    result_type: str | None


@dataclass(frozen=True)
class NormalizedExecution:
    final_output_text: str | None


@dataclass(frozen=True)
class NormalizedToolCall:
    tool_name: str
    success: bool | None
    summary: str | None
    error_message: str | None


@dataclass(frozen=True)
class NormalizedObservation:
    observation_id: str
    content: str
    locator: str | None
    # Removed evidence taxonomy is retained only for legacy metric compatibility.
    legacy_content_state: str | None = None


@dataclass(frozen=True)
class NormalizedPlanStep:
    step_id: str
    # Legacy requirement metadata is retained only inside evaluation normalization.
    legacy_requirement_source: str | None = None
    legacy_required_support: str | None = None


@dataclass(frozen=True)
class NormalizedGovernanceDecision:
    directive: str


@dataclass(frozen=True)
class NormalizedOversightSignal:
    score: float
    level: str


@dataclass(frozen=True)
class NormalizedOversightAssessment:
    evidence_quality: NormalizedOversightSignal
    operational_risk: NormalizedOversightSignal
    trajectory_health: NormalizedOversightSignal


@dataclass(frozen=True)
class NormalizedHumanReview:
    pending_request: dict[str, Any] | None
    decision_history: tuple[dict[str, Any], ...]
    last_decision: dict[str, Any] | None
    has_activity: bool


@dataclass(frozen=True)
class NormalizedTracePayload:
    source_path: str
    runtime: NormalizedRuntime
    intake: NormalizedIntake
    actor: NormalizedActor
    execution: NormalizedExecution
    plan_steps: tuple[NormalizedPlanStep, ...]
    current_plan_step_id: str | None
    tool_calls: tuple[NormalizedToolCall, ...]
    observations: tuple[NormalizedObservation, ...]
    governance_decisions: tuple[NormalizedGovernanceDecision, ...]
    oversight_assessments: tuple[NormalizedOversightAssessment, ...]
    human_review: NormalizedHumanReview


__all__ = [
    "EVALUATION_REPORT_VERSION",
    "EvaluationIssue",
    "Layer2EvaluationAggregate",
    "Layer2EvaluationReport",
    "Layer2EvaluationRun",
    "NormalizedActor",
    "NormalizedExecution",
    "NormalizedGovernanceDecision",
    "NormalizedHumanReview",
    "NormalizedIntake",
    "NormalizedObservation",
    "NormalizedOversightAssessment",
    "NormalizedOversightSignal",
    "NormalizedPlanStep",
    "NormalizedRuntime",
    "NormalizedToolCall",
    "NormalizedTracePayload",
    "TraceEvaluationError",
]
