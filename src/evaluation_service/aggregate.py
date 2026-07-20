"""Compatibility facade and thin orchestration for offline trace evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .contracts import (
    EVALUATION_REPORT_VERSION,
    EvaluationIssue,
    Layer2EvaluationAggregate,
    Layer2EvaluationReport,
    Layer2EvaluationRun,
    TraceEvaluationError,
)
from .discovery import collect_layer2_trace_files
from .loader import load_layer2_trace_file
from .metrics import build_aggregate
from .report_builder import (
    layer2_evaluation_report_to_dict,
    layer2_evaluation_report_to_json,
    layer2_evaluation_report_to_markdown,
    write_layer2_evaluation_report,
)
from .run_evaluator import evaluate_normalized_trace, failed_run
from .trace_projection import normalize_trace_payload


# Evaluate one file or directory of Layer 2 trace exports.
def evaluate_layer2_trace_inputs(input_paths: Sequence[Path]) -> Layer2EvaluationReport:
    discovered_files = collect_layer2_trace_files(input_paths)
    runs: list[Layer2EvaluationRun] = []
    issues: list[EvaluationIssue] = []

    for source_path in discovered_files:
        try:
            payload = load_layer2_trace_file(source_path)
            normalized = normalize_trace_payload(payload, source_path=source_path)
            runs.append(evaluate_normalized_trace(normalized, source_path=source_path))
        except TraceEvaluationError as error:
            issues.append(
                EvaluationIssue(
                    source_path=str(source_path),
                    issue_type=error.issue_type,
                    message=error.message,
                )
            )
            runs.append(
                failed_run(
                    source_path=source_path,
                    status=error.issue_type,
                    error=error.message,
                )
            )

    aggregate = build_aggregate(runs=runs, discovered_file_count=len(discovered_files))
    return Layer2EvaluationReport(
        report_version=EVALUATION_REPORT_VERSION,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        input_paths=tuple(str(path) for path in input_paths),
        discovered_files=tuple(str(path) for path in discovered_files),
        runs=tuple(runs),
        aggregate=aggregate,
        issues=tuple(issues),
    )


__all__ = [
    "EVALUATION_REPORT_VERSION",
    "EvaluationIssue",
    "Layer2EvaluationAggregate",
    "Layer2EvaluationReport",
    "Layer2EvaluationRun",
    "TraceEvaluationError",
    "collect_layer2_trace_files",
    "evaluate_layer2_trace_inputs",
    "layer2_evaluation_report_to_dict",
    "layer2_evaluation_report_to_json",
    "layer2_evaluation_report_to_markdown",
    "load_layer2_trace_file",
    "write_layer2_evaluation_report",
]
