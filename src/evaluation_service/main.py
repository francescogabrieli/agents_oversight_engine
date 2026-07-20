from __future__ import annotations

import argparse
from pathlib import Path

from .evaluation_client import OPAEvaluationClient
from .report_builder import build_markdown_report, build_report, report_to_json
from .trace_normalizer import normalize_trace_file
from .aggregate import evaluate_layer2_trace_inputs, layer2_evaluation_report_to_dict


# Parse CLI arguments for the standalone evaluation service.
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate exported Layer 2 trace JSON through OPA.")
    parser.add_argument("trace_path", type=Path, nargs="+")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--opa-url", default="http://127.0.0.1:8181")
    return parser.parse_args()


# Run offline evaluation without importing Layer 2 runtime models.
def main() -> None:
    args = _parse_args()
    evaluation_input = normalize_trace_file(args.trace_path[0])
    compliance = OPAEvaluationClient(base_url=args.opa_url).evaluate(
        evaluation_input=evaluation_input,
    )
    aggregate = evaluate_layer2_trace_inputs(args.trace_path)
    report = build_report(
        evaluation_input=evaluation_input,
        compliance_result=compliance,
        aggregate_result=layer2_evaluation_report_to_dict(aggregate),
    )
    if args.json_out is not None:
        args.json_out.write_text(report_to_json(report), encoding="utf-8")
    if args.md_out is not None:
        args.md_out.write_text(build_markdown_report(report), encoding="utf-8")
    if args.json_out is None and args.md_out is None:
        print(report_to_json(report))


if __name__ == "__main__":
    main()
