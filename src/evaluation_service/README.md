# Evaluation Service

This package evaluates exported Layer 2 traces without importing the live runtime.

- `aggregate.py` is the public compatibility facade and orchestration entry point.
- `contracts.py` owns report, run, issue, and normalized trace contracts.
- `discovery.py` finds trace JSON files in stable order.
- `loader.py` performs read-only JSON loading and error normalization.
- `trace_projection.py` projects current and legacy trace layouts into evaluation-only contracts.
- `run_evaluator.py` computes metrics for one normalized trace.
- `metrics.py` aggregates per-run measurements.
- `report_builder.py` owns stable JSON and Markdown serialization.
- `trace_normalizer.py` retains v1/v2 compatibility for the OPA evaluation input.
- `main.py` is the standalone CLI; `main_layer2_evaluation.py` remains the deprecated entrypoint.

New metrics should be added first to the typed contracts, then to the per-run evaluator,
aggregate calculator, report serialization, and regression tests. Runtime models must never
be imported into this package.
