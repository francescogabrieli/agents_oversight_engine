from __future__ import annotations

import json
from typing import Any


# Build the JSON report emitted by the decoupled evaluation service.
def build_report(*, evaluation_input: dict[str, Any], compliance_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "evaluation_report.v1",
        "evaluation_input_summary": evaluation_input.get("derived", {}),
        "compliance": compliance_result,
    }


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
