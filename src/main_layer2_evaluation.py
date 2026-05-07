from __future__ import annotations

import argparse
from pathlib import Path

from layer2_runtime_governance.evaluation import (
    evaluate_layer2_trace_inputs,
    layer2_evaluation_report_to_markdown,
    write_layer2_evaluation_report,
)


# Keep offline evaluation outputs in a dedicated artifacts folder by default.
DEFAULT_EVALUATION_JSON_PATH = Path("artifacts/evaluations/layer2_evaluation_report.json")
DEFAULT_EVALUATION_MARKDOWN_PATH = Path("artifacts/evaluations/layer2_evaluation_report.md")


# Parse CLI arguments for the offline evaluation pipeline.
def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run offline evaluation over Layer 2 traces.")
    parser.add_argument(
        "--input",
        required=True,
        nargs="+",
        type=Path,
        help="One trace JSON file or one or more directories containing trace JSON files.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_EVALUATION_JSON_PATH,
        help="Where to write the JSON evaluation report.",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_EVALUATION_MARKDOWN_PATH,
        help="Where to write the Markdown evaluation summary.",
    )
    return parser


# Run the offline evaluation pipeline from the command line.
def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()

    report = evaluate_layer2_trace_inputs(args.input)
    write_layer2_evaluation_report(
        report,
        output_json_path=args.output_json,
        output_markdown_path=args.output_md,
    )

    print(f"Wrote JSON report to {args.output_json}")
    print(f"Wrote Markdown report to {args.output_md}")
    print(layer2_evaluation_report_to_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
