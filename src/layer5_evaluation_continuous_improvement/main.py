from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ingestion import TraceImporter
from .reporting import generate_evaluation_report
from .store import EvaluationStore


# Build the standalone Layer 5 command-line interface.
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Layer 5 offline evaluation")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("artifacts/evaluation/layer5_evaluation.sqlite3"),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="Import Layer 4 trace files")
    ingest.add_argument("paths", nargs="+", type=Path)
    ingest.add_argument("--experiment-id", required=True)
    ingest.add_argument("--experiment-name")
    ingest.add_argument("--experiment-description")

    report = commands.add_parser("report", help="Generate metrics, CSV, and SVG files")
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--experiment-id")

    assess = commands.add_parser("assess-run", help="Assign the semantic task result")
    assess.add_argument("run_id")
    assess.add_argument("result", choices=("correct", "partial", "incorrect"))
    assess.add_argument("--method", choices=("automatic", "human"), required=True)

    assess_governance = commands.add_parser(
        "assess-governance", help="Label whether one human review was necessary"
    )
    assess_governance.add_argument("event_id")
    assess_governance.add_argument("needed", choices=("yes", "no"))

    assess_operation = commands.add_parser(
        "assess-operation", help="Label one external operation as correct or incorrect"
    )
    assess_operation.add_argument("row_id")
    assess_operation.add_argument("correct", choices=("yes", "no"))
    return parser


# Execute one CLI command against the dedicated evaluation database.
def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    with EvaluationStore(arguments.database) as store:
        if arguments.command == "ingest":
            result = TraceImporter(store).import_paths(
                arguments.paths,
                experiment_id=arguments.experiment_id,
                experiment_name=arguments.experiment_name,
                experiment_description=arguments.experiment_description,
            )
            print(
                json.dumps(
                    {
                        "imported": [str(path) for path in result.imported],
                        "skipped": [str(path) for path in result.skipped],
                        "failures": [
                            {"path": str(path), "error": error}
                            for path, error in result.failures
                        ],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 1 if result.failures else 0
        if arguments.command == "report":
            files = generate_evaluation_report(
                store,
                output_dir=arguments.output,
                experiment_id=arguments.experiment_id,
            )
            print(json.dumps({"files": [str(path) for path in files]}, indent=2))
            return 0
        if arguments.command == "assess-governance":
            store.set_human_review_needed(
                event_id=arguments.event_id,
                needed=arguments.needed == "yes",
            )
            return 0
        if arguments.command == "assess-operation":
            store.set_external_operation_correct(
                row_id=arguments.row_id,
                correct=arguments.correct == "yes",
            )
            return 0
        store.set_task_result(
            run_id=arguments.run_id,
            result=arguments.result,
            method=arguments.method,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
