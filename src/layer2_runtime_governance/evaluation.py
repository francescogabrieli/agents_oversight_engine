from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


# Version the offline evaluation contract so report consumers can track changes over time.
EVALUATION_REPORT_VERSION = "layer2_offline_evaluation_v2"


@dataclass(frozen=True)
class EvaluationIssue:
    # Keep non-fatal load and validation issues visible in the report.
    source_path: str
    issue_type: str
    message: str


@dataclass(frozen=True)
class Layer2EvaluationRun:
    # Capture the per-trace metrics needed for thesis analysis and later aggregation.
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
    # Aggregate the per-run metrics into a thesis-friendly overview.
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
    # Keep the full offline evaluation artifact JSON-safe and reproducible.
    report_version: str
    generated_at_utc: str
    input_paths: tuple[str, ...]
    discovered_files: tuple[str, ...]
    runs: tuple[Layer2EvaluationRun, ...]
    aggregate: Layer2EvaluationAggregate
    issues: tuple[EvaluationIssue, ...] = field(default_factory=tuple)


# Evaluate one file or directory of Layer 2 trace exports.
def evaluate_layer2_trace_inputs(input_paths: Sequence[Path]) -> Layer2EvaluationReport:
    discovered_files = collect_layer2_trace_files(input_paths)
    runs: list[Layer2EvaluationRun] = []
    issues: list[EvaluationIssue] = []

    for source_path in discovered_files:
        try:
            payload = _load_json_file(source_path)
            normalized = _normalize_trace_payload(payload, source_path=source_path)
            runs.append(_evaluate_normalized_trace(normalized, source_path=source_path))
        except TraceEvaluationError as error:
            issues.append(
                EvaluationIssue(
                    source_path=str(source_path),
                    issue_type=error.issue_type,
                    message=error.message,
                )
            )
            runs.append(
                _failed_run(
                    source_path=source_path,
                    status=error.issue_type,
                    error=error.message,
                )
            )

    aggregate = _build_aggregate(runs=runs, discovered_file_count=len(discovered_files))
    return Layer2EvaluationReport(
        report_version=EVALUATION_REPORT_VERSION,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        input_paths=tuple(str(path) for path in input_paths),
        discovered_files=tuple(str(path) for path in discovered_files),
        runs=tuple(runs),
        aggregate=aggregate,
        issues=tuple(issues),
    )


# Write the JSON and Markdown report files for offline analysis.
def write_layer2_evaluation_report(
    report: Layer2EvaluationReport,
    *,
    output_json_path: Path,
    output_markdown_path: Path,
) -> tuple[Path, Path]:
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(layer2_evaluation_report_to_json(report), encoding="utf-8")
    output_markdown_path.write_text(layer2_evaluation_report_to_markdown(report), encoding="utf-8")
    return output_json_path, output_markdown_path


# Convert the report into JSON-safe structures.
def layer2_evaluation_report_to_dict(report: Layer2EvaluationReport) -> dict[str, Any]:
    return _to_jsonable(report)


# Convert the report into formatted JSON.
def layer2_evaluation_report_to_json(report: Layer2EvaluationReport) -> str:
    return json.dumps(layer2_evaluation_report_to_dict(report), indent=2, ensure_ascii=False)


# Convert the report into a compact Markdown summary.
def layer2_evaluation_report_to_markdown(report: Layer2EvaluationReport) -> str:
    lines: list[str] = []
    aggregate = report.aggregate
    lines.append("# Layer 2 Offline Evaluation Report")
    lines.append("")
    lines.append(f"- Report version: `{report.report_version}`")
    lines.append(f"- Generated at: `{report.generated_at_utc}`")
    lines.append(f"- Input paths: `{len(report.input_paths)}`")
    lines.append(f"- Discovered trace files: `{aggregate.discovered_files}`")
    lines.append(f"- Parsed runs: `{aggregate.parsed_runs}`")
    lines.append(f"- Failed runs: `{aggregate.failed_runs}`")
    lines.append("")

    lines.append("## Aggregate Metrics")
    lines.extend(
        [
            _markdown_table_row("Task outcome distribution", _format_counts(aggregate.task_outcome_distribution)),
            _markdown_table_row(
                "Governance directive distribution",
                _format_counts(aggregate.governance_directive_distribution),
            ),
            _markdown_table_row(
                "Governance escalation rate",
                _format_ratio(aggregate.governance_escalation_rate),
            ),
            _markdown_table_row(
                "Human review frequency",
                _format_ratio(aggregate.human_review_frequency),
            ),
            _markdown_table_row(
                "Human review decision distribution",
                _format_counts(aggregate.human_review_decision_distribution),
            ),
            _markdown_table_row(
                "Unresolved human review cases",
                str(aggregate.unresolved_human_review_count),
            ),
            _markdown_table_row(
                "Evidence support distribution",
                _format_counts(aggregate.evidence_support_distribution),
            ),
            _markdown_table_row(
                "Typed requirement mismatch total",
                str(aggregate.typed_requirement_mismatch_total),
            ),
            _markdown_table_row(
                "Heuristic requirement mismatch total",
                str(aggregate.heuristic_requirement_mismatch_total),
            ),
            _markdown_table_row("Tool calls total", str(aggregate.tool_call_total)),
            _markdown_table_row("Failed tool total", str(aggregate.failed_tool_total)),
            _markdown_table_row("Duplicate block total", str(aggregate.duplicate_block_total)),
            _markdown_table_row("Failed tool rate", _format_ratio(aggregate.failed_tool_rate)),
            _markdown_table_row("Duplicate block rate", _format_ratio(aggregate.duplicate_block_rate)),
            _markdown_table_row(
                "Oversight assessments total",
                str(aggregate.oversight_assessment_total),
            ),
            _markdown_table_row(
                "Low-score assessment total",
                str(aggregate.low_score_assessment_total),
            ),
            _markdown_table_row(
                "Degraded trajectory assessment total",
                str(aggregate.degraded_trajectory_assessment_total),
            ),
        ]
    )
    lines.append("")

    lines.append("## Per-Run Summary")
    lines.append("| Trace | Status | Runtime | Tool Calls | Failed Tools | Duplicate Blocks | Human Review |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | --- |")
    for run in report.runs:
        human_review = "yes" if run.human_review_activity else "no"
        runtime_state = run.runtime_state or "-"
        lines.append(
            "| {trace} | {status} | {runtime} | {tool_calls} | {failed_tools} | {duplicate_blocks} | {human_review} |".format(
                trace=_escape_markdown(run.source_path),
                status=_escape_markdown(run.status),
                runtime=_escape_markdown(runtime_state),
                tool_calls=run.tool_call_count,
                failed_tools=run.failed_tool_count,
                duplicate_blocks=run.duplicate_block_count,
                human_review=human_review,
            )
        )

    if report.issues:
        lines.append("")
        lines.append("## Load Issues")
        for issue in report.issues:
            lines.append(
                f"- `{issue.source_path}` [{issue.issue_type}]: {_escape_markdown(issue.message)}"
            )

    return "\n".join(lines) + "\n"


# Collect trace files from files and directories, recursively, in stable order.
def collect_layer2_trace_files(input_paths: Sequence[Path]) -> list[Path]:
    discovered: list[Path] = []
    for input_path in input_paths:
        resolved = input_path.expanduser()
        if resolved.is_file():
            discovered.append(resolved)
            continue
        if resolved.is_dir():
            discovered.extend(sorted(resolved.rglob("*.json")))
            continue
        raise TraceEvaluationError(
            issue_type="discovery_error",
            message=f"Input path does not exist or is not accessible: {resolved}",
        )
    return discovered


# Load and normalize a single JSON trace file.
def load_layer2_trace_file(source_path: Path) -> dict[str, Any]:
    return _load_json_file(source_path)


# Validate one JSON payload and normalize it into a compact trace analysis shape.
def _normalize_trace_payload(payload: Any, *, source_path: Path) -> _NormalizedTracePayload:
    if not isinstance(payload, dict):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file is not a JSON object: {source_path}",
        )

    runtime = _require_dict(payload, "runtime", source_path=source_path)
    intake = _require_dict(payload, "intake", source_path=source_path)
    actor = _require_dict(payload, "actor", source_path=source_path)
    execution = _require_dict(payload, "execution", source_path=source_path)

    return _NormalizedTracePayload(
        source_path=str(source_path),
        runtime=_normalize_runtime(runtime, source_path=source_path),
        intake=_normalize_intake(intake, source_path=source_path),
        actor=_normalize_actor(actor, source_path=source_path),
        execution=_normalize_execution(execution, source_path=source_path),
        plan_steps=_normalize_plan_steps(payload.get("plan_steps"), source_path=source_path),
        current_plan_step_id=_optional_text(payload.get("current_plan_step_id")),
        tool_calls=_normalize_tool_calls(payload.get("tool_calls"), source_path=source_path),
        observed_evidence=_normalize_observed_evidence(
            payload.get("observed_evidence"),
            source_path=source_path,
        ),
        governance_decisions=_normalize_governance_decisions(
            payload.get("governance_decisions"),
            source_path=source_path,
        ),
        oversight_assessments=_normalize_oversight_assessments(
            payload.get("oversight_assessments"),
            source_path=source_path,
        ),
        human_review=_normalize_human_review(payload.get("human_review"), source_path=source_path),
    )


# Compute the run-level evaluation from a normalized trace payload.
def _evaluate_normalized_trace(
    normalized: _NormalizedTracePayload,
    *,
    source_path: Path,
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
    typed_requirement_mismatch_count, heuristic_requirement_mismatch_count = _count_requirement_mismatches(
        normalized=normalized
    )

    tool_call_count = len(normalized.tool_calls)
    failed_tool_count = sum(1 for call in normalized.tool_calls if call.success is False)
    duplicate_block_count = sum(1 for call in normalized.tool_calls if _is_duplicate_block(call))
    low_score_assessment_count = sum(
        1 for assessment in normalized.oversight_assessments if _has_low_score(assessment)
    )
    degraded_trajectory_assessment_count = sum(
        1 for assessment in normalized.oversight_assessments if _is_degraded_trajectory(assessment)
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
        typed_requirement_mismatch_count=typed_requirement_mismatch_count,
        heuristic_requirement_mismatch_count=heuristic_requirement_mismatch_count,
        tool_call_count=tool_call_count,
        failed_tool_count=failed_tool_count,
        duplicate_block_count=duplicate_block_count,
        oversight_assessment_count=len(normalized.oversight_assessments),
        low_score_assessment_count=low_score_assessment_count,
        degraded_trajectory_assessment_count=degraded_trajectory_assessment_count,
        average_evidence_quality=_average(
            assessment.evidence_quality.score for assessment in normalized.oversight_assessments
        ),
        average_operational_risk=_average(
            assessment.operational_risk.score for assessment in normalized.oversight_assessments
        ),
        average_trajectory_health=_average(
            assessment.trajectory_health.score for assessment in normalized.oversight_assessments
        ),
        unresolved_human_review=normalized.human_review.pending_request is not None,
        human_review_activity=normalized.human_review.has_activity,
    )


# Aggregate all parsed runs into the final report summary.
def _build_aggregate(
    *,
    runs: Sequence[Layer2EvaluationRun],
    discovered_file_count: int,
) -> Layer2EvaluationAggregate:
    parsed_runs = [run for run in runs if run.status == "ok"]
    failed_runs = len(runs) - len(parsed_runs)
    task_outcome_distribution = _count_values(
        run.task_outcome for run in parsed_runs if run.task_outcome is not None
    )

    governance_directive_distribution = _merge_nested_counts(
        run.governance_directive_counts for run in parsed_runs
    )
    human_review_decision_distribution = _merge_nested_counts(
        run.human_review_decision_counts for run in parsed_runs
    )
    evidence_support_distribution = _merge_nested_counts(
        run.evidence_support_counts for run in parsed_runs
    )
    typed_requirement_mismatch_total = sum(
        run.typed_requirement_mismatch_count for run in parsed_runs
    )
    heuristic_requirement_mismatch_total = sum(
        run.heuristic_requirement_mismatch_count for run in parsed_runs
    )

    total_governance_decisions = sum(governance_directive_distribution.values())
    escalation_count = sum(
        count
        for directive, count in governance_directive_distribution.items()
        if directive not in {"continue", "continue_read_only"}
    )

    total_human_reviews = sum(1 for run in parsed_runs if run.human_review_activity)
    unresolved_human_review_count = sum(1 for run in parsed_runs if run.unresolved_human_review)

    tool_call_total = sum(run.tool_call_count for run in parsed_runs)
    failed_tool_total = sum(run.failed_tool_count for run in parsed_runs)
    duplicate_block_total = sum(run.duplicate_block_count for run in parsed_runs)
    oversight_assessment_total = sum(run.oversight_assessment_count for run in parsed_runs)
    low_score_assessment_total = sum(run.low_score_assessment_count for run in parsed_runs)
    degraded_trajectory_assessment_total = sum(
        run.degraded_trajectory_assessment_count for run in parsed_runs
    )

    return Layer2EvaluationAggregate(
        total_inputs=len(runs),
        discovered_files=discovered_file_count,
        parsed_runs=len(parsed_runs),
        failed_runs=failed_runs,
        task_outcome_distribution=task_outcome_distribution,
        governance_directive_distribution=governance_directive_distribution,
        governance_escalation_rate=(
            escalation_count / total_governance_decisions if total_governance_decisions else 0.0
        ),
        human_review_frequency=(total_human_reviews / len(parsed_runs) if parsed_runs else 0.0),
        human_review_decision_distribution=human_review_decision_distribution,
        unresolved_human_review_count=unresolved_human_review_count,
        evidence_support_distribution=evidence_support_distribution,
        typed_requirement_mismatch_total=typed_requirement_mismatch_total,
        heuristic_requirement_mismatch_total=heuristic_requirement_mismatch_total,
        tool_call_total=tool_call_total,
        failed_tool_total=failed_tool_total,
        duplicate_block_total=duplicate_block_total,
        failed_tool_rate=(failed_tool_total / tool_call_total if tool_call_total else 0.0),
        duplicate_block_rate=(duplicate_block_total / tool_call_total if tool_call_total else 0.0),
        oversight_assessment_total=oversight_assessment_total,
        low_score_assessment_total=low_score_assessment_total,
        degraded_trajectory_assessment_total=degraded_trajectory_assessment_total,
        average_evidence_quality=_average(
            run.average_evidence_quality for run in parsed_runs if run.average_evidence_quality is not None
        ),
        average_operational_risk=_average(
            run.average_operational_risk for run in parsed_runs if run.average_operational_risk is not None
        ),
        average_trajectory_health=_average(
            run.average_trajectory_health for run in parsed_runs if run.average_trajectory_health is not None
        ),
    )


# Remove parse failures from the returned run list while keeping them visible in the report.
def _failed_run(*, source_path: Path, status: str, error: str) -> Layer2EvaluationRun:
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


# Load one JSON file from disk.
def _load_json_file(source_path: Path) -> Any:
    try:
        raw_bytes = source_path.read_bytes()
    except FileNotFoundError as error:
        raise TraceEvaluationError(
            issue_type="discovery_error",
            message=f"Trace file does not exist: {source_path}",
        ) from error

    decode_attempts = ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-8")
    last_error: Exception | None = None
    for encoding in decode_attempts:
        try:
            return json.loads(raw_bytes.decode(encoding))
        except UnicodeDecodeError as error:
            last_error = error
        except json.JSONDecodeError as error:
            raise TraceEvaluationError(
                issue_type="parse_failed",
                message=f"Trace file is not valid JSON: {source_path} ({error.msg})",
            ) from error

    raise TraceEvaluationError(
        issue_type="parse_failed",
        message=f"Trace file could not be decoded as JSON text: {source_path}",
    ) from last_error


# Handle malformed trace payloads without crashing the whole evaluation pass.
class TraceEvaluationError(RuntimeError):
    def __init__(self, *, issue_type: str, message: str) -> None:
        super().__init__(message)
        self.issue_type = issue_type
        self.message = message


# Keep the normalized shape small and explicit for the evaluation pass.
@dataclass(frozen=True)
class _NormalizedRuntime:
    thread_id: str
    current_state: str
    bootstrap_completed: bool
    runtime_flags: tuple[str, ...]
    actor_turn_count: int
    tool_call_count: int


@dataclass(frozen=True)
class _NormalizedIntake:
    goal: str
    context: str
    domain_type: str
    domain_label: str
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
class _NormalizedActor:
    result_type: str | None


@dataclass(frozen=True)
class _NormalizedExecution:
    final_output_text: str | None


@dataclass(frozen=True)
class _NormalizedToolCall:
    tool_name: str
    success: bool | None
    summary: str | None
    error_message: str | None


@dataclass(frozen=True)
class _NormalizedObservedEvidence:
    source_kind: str | None
    support_level: str


@dataclass(frozen=True)
class _NormalizedPlanStep:
    step_id: str
    evidence_requirement: str | None
    claim_precision_ceiling: str | None
    resolved_required_evidence_support_level: str | None
    resolved_required_evidence_support_source: str | None


@dataclass(frozen=True)
class _NormalizedGovernanceDecision:
    directive: str


@dataclass(frozen=True)
class _NormalizedOversightSignal:
    score: float
    level: str


@dataclass(frozen=True)
class _NormalizedOversightAssessment:
    evidence_quality: _NormalizedOversightSignal
    operational_risk: _NormalizedOversightSignal
    trajectory_health: _NormalizedOversightSignal


@dataclass(frozen=True)
class _NormalizedHumanReview:
    pending_request: dict[str, Any] | None
    decision_history: tuple[dict[str, Any], ...]
    last_decision: dict[str, Any] | None
    has_activity: bool


@dataclass(frozen=True)
class _NormalizedTracePayload:
    source_path: str
    runtime: _NormalizedRuntime
    intake: _NormalizedIntake
    actor: _NormalizedActor
    execution: _NormalizedExecution
    plan_steps: tuple[_NormalizedPlanStep, ...]
    current_plan_step_id: str | None
    tool_calls: tuple[_NormalizedToolCall, ...]
    observed_evidence: tuple[_NormalizedObservedEvidence, ...]
    governance_decisions: tuple[_NormalizedGovernanceDecision, ...]
    oversight_assessments: tuple[_NormalizedOversightAssessment, ...]
    human_review: _NormalizedHumanReview


# Validate a required dict section.
def _require_dict(payload: dict[str, Any], key: str, *, source_path: Path) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file is missing a valid '{key}' object: {source_path}",
        )
    return value


# Validate a required string field.
def _require_str(payload: dict[str, Any], key: str, *, source_path: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file is missing a valid '{key}' string: {source_path}",
        )
    return value


# Normalize the runtime section.
def _normalize_runtime(payload: dict[str, Any], *, source_path: Path) -> _NormalizedRuntime:
    thread_id = _require_str(payload, "thread_id", source_path=source_path)
    current_state = _require_str(payload, "current_state", source_path=source_path)
    return _NormalizedRuntime(
        thread_id=thread_id,
        current_state=current_state,
        bootstrap_completed=bool(payload.get("bootstrap_completed", False)),
        runtime_flags=_string_tuple(payload.get("runtime_flags")),
        actor_turn_count=int(payload.get("actor_turn_count", 0)),
        tool_call_count=int(payload.get("tool_call_count", 0)),
    )


# Normalize the intake section.
def _normalize_intake(payload: dict[str, Any], *, source_path: Path) -> _NormalizedIntake:
    return _NormalizedIntake(
        goal=_require_str(payload, "goal", source_path=source_path),
        context=str(payload.get("context", "")),
        domain_type=_require_str(payload, "domain_type", source_path=source_path),
        domain_label=_require_str(payload, "domain_label", source_path=source_path),
        sensitivity=_require_str(payload, "sensitivity", source_path=source_path),
        success_criteria=_string_tuple(payload.get("success_criteria")),
        constraints=_string_tuple(payload.get("constraints")),
        parse_status=_require_str(payload, "parse_status", source_path=source_path),
        parser_name=_require_str(payload, "parser_name", source_path=source_path),
        prompt_version=_require_str(payload, "prompt_version", source_path=source_path),
        schema_version=_require_str(payload, "schema_version", source_path=source_path),
        policy_profile_name=_require_str(payload, "policy_profile_name", source_path=source_path),
        confidence_threshold=float(payload.get("confidence_threshold", 0.0)),
        max_steps=_optional_int(payload.get("max_steps")),
        max_tool_calls=_optional_int(payload.get("max_tool_calls")),
        read_only_mode=bool(payload.get("read_only_mode", False)),
    )


# Normalize the actor section.
def _normalize_actor(payload: dict[str, Any], *, source_path: Path) -> _NormalizedActor:
    result_type = payload.get("result_type")
    if result_type is not None and not isinstance(result_type, str):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid actor.result_type: {source_path}",
        )
    return _NormalizedActor(result_type=result_type)


# Normalize the execution section.
def _normalize_execution(payload: dict[str, Any], *, source_path: Path) -> _NormalizedExecution:
    final_output_text = payload.get("final_output_text")
    if final_output_text is not None and not isinstance(final_output_text, str):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid execution.final_output_text: {source_path}",
        )
    return _NormalizedExecution(final_output_text=final_output_text)


# Normalize retained tool calls.
def _normalize_tool_calls(
    payload: Any,
    *,
    source_path: Path,
) -> tuple[_NormalizedToolCall, ...]:
    items = _normalize_optional_list(payload, source_path=source_path, field_name="tool_calls")
    return tuple(
        _NormalizedToolCall(
            tool_name=_require_str(item, "tool_name", source_path=source_path),
            success=_optional_bool(item.get("success")),
            summary=_optional_text(item.get("summary")),
            error_message=_optional_text(item.get("error_message")),
        )
        for item in items
    )


# Normalize observed evidence.
def _normalize_observed_evidence(
    payload: Any,
    *,
    source_path: Path,
) -> tuple[_NormalizedObservedEvidence, ...]:
    items = _normalize_optional_list(
        payload,
        source_path=source_path,
        field_name="observed_evidence",
    )
    return tuple(
        _NormalizedObservedEvidence(
            source_kind=_optional_text(item.get("source_kind")),
            support_level=_require_str(item, "support_level", source_path=source_path),
        )
        for item in items
    )


# Normalize the retained plan-step metadata used for evidence requirement analysis.
def _normalize_plan_steps(
    payload: Any,
    *,
    source_path: Path,
) -> tuple[_NormalizedPlanStep, ...]:
    items = _normalize_optional_list(payload, source_path=source_path, field_name="plan_steps")
    return tuple(
        _NormalizedPlanStep(
            step_id=_require_str(item, "step_id", source_path=source_path),
            evidence_requirement=_optional_text(item.get("evidence_requirement")),
            claim_precision_ceiling=_optional_text(item.get("claim_precision_ceiling")),
            resolved_required_evidence_support_level=_optional_text(
                item.get("resolved_required_evidence_support_level")
            ),
            resolved_required_evidence_support_source=_optional_text(
                item.get("resolved_required_evidence_support_source")
            ),
        )
        for item in items
    )


# Normalize governance decisions.
def _normalize_governance_decisions(
    payload: Any,
    *,
    source_path: Path,
) -> tuple[_NormalizedGovernanceDecision, ...]:
    items = _normalize_optional_list(
        payload,
        source_path=source_path,
        field_name="governance_decisions",
    )
    return tuple(
        _NormalizedGovernanceDecision(
            directive=_require_str(item, "directive", source_path=source_path),
        )
        for item in items
    )


# Normalize oversight assessments.
def _normalize_oversight_assessments(
    payload: Any,
    *,
    source_path: Path,
) -> tuple[_NormalizedOversightAssessment, ...]:
    items = _normalize_optional_list(
        payload,
        source_path=source_path,
        field_name="oversight_assessments",
    )
    return tuple(
        _NormalizedOversightAssessment(
            evidence_quality=_normalize_signal(
                _require_dict(item, "evidence_quality", source_path=source_path),
                source_path=source_path,
            ),
            operational_risk=_normalize_signal(
                _require_dict(item, "operational_risk", source_path=source_path),
                source_path=source_path,
            ),
            trajectory_health=_normalize_signal(
                _require_dict(item, "trajectory_health", source_path=source_path),
                source_path=source_path,
            ),
        )
        for item in items
    )


# Normalize the human review section.
def _normalize_human_review(
    payload: Any,
    *,
    source_path: Path,
) -> _NormalizedHumanReview:
    if payload is None:
        return _NormalizedHumanReview(
            pending_request=None,
            decision_history=(),
            last_decision=None,
            has_activity=False,
        )
    if not isinstance(payload, dict):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid human_review section: {source_path}",
        )

    pending_request = payload.get("pending_request")
    if pending_request is not None and not isinstance(pending_request, dict):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid human_review.pending_request: {source_path}",
        )

    last_decision = payload.get("last_decision")
    if last_decision is not None and not isinstance(last_decision, dict):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid human_review.last_decision: {source_path}",
        )

    decision_history_raw = payload.get("decision_history")
    decision_history_items = tuple(
        item
        for item in _normalize_optional_list(
            decision_history_raw,
            source_path=source_path,
            field_name="human_review.decision_history",
        )
    )
    return _NormalizedHumanReview(
        pending_request=pending_request,
        decision_history=decision_history_items,
        last_decision=last_decision,
        has_activity=bool(pending_request or last_decision or decision_history_items),
    )


# Count evidence requirement mismatches separately for typed metadata and heuristic fallback traces.
def _count_requirement_mismatches(
    *,
    normalized: _NormalizedTracePayload,
) -> tuple[int, int]:
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
    *,
    normalized: _NormalizedTracePayload,
) -> _NormalizedPlanStep | None:
    if not normalized.plan_steps:
        return None

    if normalized.current_plan_step_id is not None:
        for step in normalized.plan_steps:
            if step.step_id == normalized.current_plan_step_id:
                return step

    return normalized.plan_steps[-1]


# Resolve the effective requirement and its provenance from normalized trace data.
def _resolve_required_support_level(
    *,
    plan_step: _NormalizedPlanStep,
) -> tuple[str, str]:
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
    observed_evidence: tuple[_NormalizedObservedEvidence, ...],
) -> str:
    if not observed_evidence:
        return "local_only"

    strongest = "local_only"
    for evidence in observed_evidence:
        if _evidence_support_level_rank(evidence.support_level) > _evidence_support_level_rank(strongest):
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


# Normalize an oversight score signal.
def _normalize_signal(payload: dict[str, Any], *, source_path: Path) -> _NormalizedOversightSignal:
    score = payload.get("score")
    if not isinstance(score, (int, float)):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid oversight score field: {source_path}",
        )
    level = _require_str(payload, "level", source_path=source_path)
    return _NormalizedOversightSignal(score=float(score), level=level)


# Return a tuple of strings from a JSON list.
def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


# Return an optional integer without raising on null.
def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


# Return an optional boolean without raising on null.
def _optional_bool(value: Any) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    return None


# Return an optional string without raising on null.
def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return None


# Normalize an optional list, treating missing sections as empty histories.
def _normalize_optional_list(
    payload: Any,
    *,
    source_path: Path,
    field_name: str,
) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid '{field_name}' list: {source_path}",
        )
    normalized: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise TraceEvaluationError(
                issue_type="schema_invalid",
                message=f"Trace file has invalid item in '{field_name}': {source_path}",
            )
        normalized.append(item)
    return normalized


# Count string values while skipping missing entries.
def _count_values(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


# Extract human-review decision types from the normalized trace payload.
def _iter_human_review_decision_types(
    human_review: _NormalizedHumanReview,
) -> Iterable[str]:
    if human_review.decision_history:
        for decision in human_review.decision_history:
            if isinstance(decision, dict):
                decision_type = decision.get("decision_type")
                if isinstance(decision_type, str):
                    yield decision_type
        return

    if human_review.last_decision is not None:
        decision_type = human_review.last_decision.get("decision_type")
        if isinstance(decision_type, str):
            yield decision_type


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


# Recognize duplicate-blocked tool outcomes from the retained tool trace.
def _is_duplicate_block(call: _NormalizedToolCall) -> bool:
    if call.success is not False:
        return False
    haystack = " ".join(
        part for part in (call.summary, call.error_message) if part is not None
    ).lower()
    return "duplicate" in haystack or "already executed" in haystack


# Treat weak or degraded assessments as low-score evidence for evaluation purposes.
def _has_low_score(assessment: _NormalizedOversightAssessment) -> bool:
    return any(
        signal.score < 0.6 or signal.level in {"weak", "degraded"}
        for signal in (
            assessment.evidence_quality,
            assessment.operational_risk,
            assessment.trajectory_health,
        )
    )


# Detect whether the trajectory health signal degraded in this assessment.
def _is_degraded_trajectory(assessment: _NormalizedOversightAssessment) -> bool:
    return assessment.trajectory_health.level == "degraded" or assessment.trajectory_health.score < 0.3


# Render a markdown table row for a summary metric.
def _markdown_table_row(label: str, value: str) -> str:
    return f"- **{label}**: {value}"


# Escape a small subset of markdown-sensitive content.
def _escape_markdown(value: str) -> str:
    return value.replace("|", "\\|")


# Render counts deterministically for markdown output.
def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={counts[key]}" for key in sorted(counts))


# Render ratios in a human-friendly but stable way.
def _format_ratio(value: float) -> str:
    return f"{value:.3f}"


# Convert the report tree into JSON-safe primitives.
def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


__all__ = [
    "EVALUATION_REPORT_VERSION",
    "EvaluationIssue",
    "Layer2EvaluationAggregate",
    "Layer2EvaluationReport",
    "Layer2EvaluationRun",
    "collect_layer2_trace_files",
    "evaluate_layer2_trace_inputs",
    "layer2_evaluation_report_to_dict",
    "layer2_evaluation_report_to_json",
    "layer2_evaluation_report_to_markdown",
    "load_layer2_trace_file",
    "write_layer2_evaluation_report",
]
