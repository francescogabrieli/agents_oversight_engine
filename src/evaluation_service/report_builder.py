from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from json_serialization import to_jsonable

from .contracts import Layer2EvaluationReport


# Build the JSON report emitted by the decoupled evaluation service.
def build_report(
    *,
    evaluation_input: dict[str, Any],
    compliance_result: dict[str, Any],
    aggregate_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = {
        "schema_version": "evaluation_report.v1",
        "evaluation_input_summary": evaluation_input.get("derived", {}),
        "compliance": compliance_result,
    }
    if aggregate_result is not None:
        report["aggregate_report"] = aggregate_result
    return report


# Build a compact Markdown report from the JSON report.
def build_markdown_report(report: dict[str, Any]) -> str:
    compliance = report.get("compliance", {})
    findings = compliance.get("findings", [])
    lines = [
        "# Layer 2 Offline Evaluation",
        "",
        f"- Status: {compliance.get('status', 'unknown')}",
        f"- Policy bundle: {compliance.get('policy_bundle_version', 'unknown')}",
        f"- Governance decisions: {report.get('evaluation_input_summary', {}).get('governance_decision_count', 0)}",
        "",
        "## Findings",
    ]
    if findings:
        lines.extend(f"- {finding}" for finding in findings)
    else:
        lines.append("- No findings.")
    return "\n".join(lines) + "\n"


# Convert the JSON report to stable formatted text for CLI output.
def report_to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)


# Write the aggregate JSON and Markdown report files for offline analysis.
def write_layer2_evaluation_report(
    report: Layer2EvaluationReport,
    *,
    output_json_path: Path,
    output_markdown_path: Path,
) -> tuple[Path, Path]:
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(layer2_evaluation_report_to_json(report), encoding="utf-8")
    output_markdown_path.write_text(
        layer2_evaluation_report_to_markdown(report), encoding="utf-8"
    )
    return output_json_path, output_markdown_path


# Convert the aggregate report into JSON-safe structures.
def layer2_evaluation_report_to_dict(report: Layer2EvaluationReport) -> dict[str, Any]:
    return to_jsonable(report)


# Convert the aggregate report into stable formatted JSON.
def layer2_evaluation_report_to_json(report: Layer2EvaluationReport) -> str:
    return json.dumps(layer2_evaluation_report_to_dict(report), indent=2, ensure_ascii=False)


# Convert the aggregate report into the existing compact Markdown summary.
def layer2_evaluation_report_to_markdown(report: Layer2EvaluationReport) -> str:
    aggregate = report.aggregate
    lines = [
        "# Layer 2 Offline Evaluation Report",
        "",
        f"- Report version: `{report.report_version}`",
        f"- Generated at: `{report.generated_at_utc}`",
        f"- Input paths: `{len(report.input_paths)}`",
        f"- Discovered trace files: `{aggregate.discovered_files}`",
        f"- Parsed runs: `{aggregate.parsed_runs}`",
        f"- Failed runs: `{aggregate.failed_runs}`",
        "",
        "## Aggregate Metrics",
        _markdown_table_row(
            "Task outcome distribution", _format_counts(aggregate.task_outcome_distribution)
        ),
        _markdown_table_row(
            "Governance directive distribution",
            _format_counts(aggregate.governance_directive_distribution),
        ),
        _markdown_table_row(
            "Governance escalation rate", _format_ratio(aggregate.governance_escalation_rate)
        ),
        _markdown_table_row(
            "Human review frequency", _format_ratio(aggregate.human_review_frequency)
        ),
        _markdown_table_row(
            "Human review decision distribution",
            _format_counts(aggregate.human_review_decision_distribution),
        ),
        _markdown_table_row(
            "Unresolved human review cases", str(aggregate.unresolved_human_review_count)
        ),
        _markdown_table_row(
            "Evidence support distribution", _format_counts(aggregate.evidence_support_distribution)
        ),
        _markdown_table_row(
            "Typed requirement mismatch total", str(aggregate.typed_requirement_mismatch_total)
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
            "Oversight assessments total", str(aggregate.oversight_assessment_total)
        ),
        _markdown_table_row(
            "Low-score assessment total", str(aggregate.low_score_assessment_total)
        ),
        _markdown_table_row(
            "Degraded trajectory assessment total",
            str(aggregate.degraded_trajectory_assessment_total),
        ),
        "",
        "## Per-Run Summary",
        "| Trace | Status | Runtime | Tool Calls | Failed Tools | Duplicate Blocks | Human Review |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for run in report.runs:
        lines.append(
            "| {trace} | {status} | {runtime} | {tool_calls} | {failed_tools} | {duplicate_blocks} | {human_review} |".format(
                trace=_escape_markdown(run.source_path),
                status=_escape_markdown(run.status),
                runtime=_escape_markdown(run.runtime_state or "-"),
                tool_calls=run.tool_call_count,
                failed_tools=run.failed_tool_count,
                duplicate_blocks=run.duplicate_block_count,
                human_review="yes" if run.human_review_activity else "no",
            )
        )

    if report.issues:
        lines.extend(["", "## Load Issues"])
        for issue in report.issues:
            lines.append(
                f"- `{issue.source_path}` [{issue.issue_type}]: {_escape_markdown(issue.message)}"
            )
    return "\n".join(lines) + "\n"


# Render a markdown summary metric.
def _markdown_table_row(label: str, value: str) -> str:
    return f"- **{label}**: {value}"


# Escape pipe characters used by the per-run Markdown table.
def _escape_markdown(value: str) -> str:
    return value.replace("|", "\\|")


# Render counts deterministically for Markdown output.
def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={counts[key]}" for key in sorted(counts))


# Render ratios in the established report format.
def _format_ratio(value: float) -> str:
    return f"{value:.3f}"


__all__ = [
    "build_markdown_report",
    "build_report",
    "layer2_evaluation_report_to_dict",
    "layer2_evaluation_report_to_json",
    "layer2_evaluation_report_to_markdown",
    "report_to_json",
    "write_layer2_evaluation_report",
]
