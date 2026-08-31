from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

from .metrics import build_evaluation_summary
from .reason_code_pillars import classify_reason_code_pillar
from .store import EvaluationStore


# Generate a reproducible JSON, CSV, and SVG evaluation bundle.
def generate_evaluation_report(
    store: EvaluationStore,
    *,
    output_dir: Path,
    experiment_id: str | None = None,
) -> tuple[Path, ...]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = build_evaluation_summary(store, experiment_id=experiment_id)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    files: list[Path] = [summary_path]
    run_filter = " WHERE experiment_id = ?" if experiment_id else ""
    parameters = (experiment_id,) if experiment_id else ()
    runs = store.query(f"SELECT * FROM runs{run_filter} ORDER BY run_id", parameters)
    run_ids = [str(run["run_id"]) for run in runs]
    datasets = {
        "runs": runs,
        "governance_events": store.child_rows("governance_events", run_ids),
        "human_reviews": store.child_rows("human_reviews", run_ids),
        "external_operations": store.child_rows("external_operations", run_ids),
    }
    for name, rows in datasets.items():
        csv_path = output_dir / f"{name}.csv"
        _write_csv(csv_path, rows)
        files.append(csv_path)

    family_rows = [
        {"family": family, **values} for family, values in summary.family_breakdown.items()
    ]
    family_csv = output_dir / "family_breakdown.csv"
    _write_csv(family_csv, family_rows)
    files.append(family_csv)

    # Export reason codes tabular dataset
    reason_code_rows = [
        {"reason_code": code, "pillar": classify_reason_code_pillar(code), "count": count}
        for code, count in summary.reason_codes.items()
    ]
    reason_csv = output_dir / "reason_codes.csv"
    _write_csv(reason_csv, reason_code_rows)
    files.append(reason_csv)

    efficiency_data: dict[str, float | int] = {
        "Turni Actor medi": summary.avg_actor_turns if summary.avg_actor_turns is not None else 0.0,
        "Tool call medie": summary.avg_tool_calls if summary.avg_tool_calls is not None else 0.0,
        "Replanning medi": summary.avg_replanning_count if summary.avg_replanning_count is not None else 0.0,
        "Pass di governance medi": summary.avg_governance_passes if summary.avg_governance_passes is not None else 0.0,
    }
    if summary.avg_review_time_seconds is not None:
        efficiency_data["Tempo review medio (s)"] = summary.avg_review_time_seconds
    if summary.avg_run_duration_seconds is not None:
        efficiency_data["Durata run media (s)"] = summary.avg_run_duration_seconds

    plot_specs: dict[str, tuple[str, dict[str, int] | dict[str, float | int]]] = {
        "runtime_statuses.svg": ("Stato tecnico delle run", summary.runtime_statuses),
        "task_results.svg": (
            "Correttezza delle run giudicate",
            summary.task_results,
        ),
        "human_review_decisions.svg": (
            "Decisioni umane",
            summary.review_decisions,
        ),
        "external_operation_statuses.svg": (
            "Stato delle operazioni esterne",
            summary.external_statuses,
        ),
        "governance_pillars.svg": (
            "Distribuzione per pilastro di governance",
            summary.pillar_breakdown,
        ),
        "reason_codes.svg": (
            "Frequenza reason code di governance",
            summary.reason_codes,
        ),
        "efficiency_averages.svg": (
            "Metriche medie di efficienza e overhead per run",
            efficiency_data,
        ),
        "family_completion.svg": (
            "Completamento tecnico per famiglia di task",
            {
                family: values["technical_completion_rate"] or 0.0
                for family, values in summary.family_breakdown.items()
            },
        ),
        "family_intervention.svg": (
            "Tasso di intervento umano per famiglia di task",
            {
                family: values["human_intervention_rate"] or 0.0
                for family, values in summary.family_breakdown.items()
            },
        ),
        "replan_recovery.svg": (
            "Esito delle run che hanno ripianificato almeno una volta",
            _replan_recovery_breakdown(summary),
        ),
    }
    for filename, (title, values) in plot_specs.items():
        plot_path = output_dir / filename
        plot_path.write_text(_bar_chart_svg(title, values), encoding="utf-8")
        files.append(plot_path)
    confusion_path = output_dir / "escalation_confusion_matrix.svg"
    confusion_path.write_text(
        _confusion_matrix_svg(
            summary.escalation_confusion,
            labeled=summary.labeled_governance_events,
            total=summary.total_governance_events,
        ),
        encoding="utf-8",
    )
    files.append(confusion_path)
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "experiment_id": experiment_id,
                "run_count": summary.total_runs,
                "files": [path.name for path in files],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    files.append(manifest_path)
    return tuple(files)


# Export the exact tabular input used by downstream figures.
def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        if not rows:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# A replan is a recovery attempt, not a guaranteed one; this counts how often it
# actually reaches a technical completion versus stalling into review or aborting.
def _replan_recovery_breakdown(summary) -> dict[str, int]:
    recovered = round(summary.replan_recovery_rate * summary.runs_with_replan) if summary.replan_recovery_rate is not None else 0
    return {
        "Completate dopo replan": recovered,
        "Non completate dopo replan": summary.runs_with_replan - recovered,
    }


# Render a dependency-free SVG bar chart suitable for thesis export.
def _bar_chart_svg(title: str, values: dict[str, int] | dict[str, float | int]) -> str:
    items = list(values.items()) or [("nessun dato", 0)]
    width = 900
    left = 270
    top = 90
    row_height = 58
    chart_width = 540
    maximum = max((float(value) for _, value in items), default=0.0) or 1.0
    height = top + len(items) * row_height + 50
    elements = [
        f'<text x="40" y="45" font-size="26" font-family="sans-serif">{html.escape(title)}</text>'
    ]
    for index, (label, val) in enumerate(items):
        y = top + index * row_height
        val_float = float(val)
        bar_width = int(chart_width * val_float / maximum) if maximum > 0 else 0
        display_val = f"{val:.2f}" if isinstance(val, float) else str(val)
        elements.extend(
            [
                f'<text x="{left - 15}" y="{y + 25}" text-anchor="end" font-size="18" font-family="sans-serif">{html.escape(label)}</text>',
                f'<rect x="{left}" y="{y}" width="{bar_width}" height="34" fill="#2563eb" rx="4"/>',
                f'<text x="{left + bar_width + 10}" y="{y + 25}" font-size="18" font-family="sans-serif">{display_val}</text>',
            ]
        )
    return _svg_document(width, height, elements)


# Render the annotated escalation confusion matrix without plotting dependencies.
#
# The labeled/total caption is not decoration: only manually labeled events enter this
# matrix, so labeling escalations alone forces false_negative to zero and makes recall
# read as a perfect 1.0. Printing the covered fraction next to the cells keeps that
# limitation attached to the figure wherever it is reproduced.
def _confusion_matrix_svg(values: dict[str, int], *, labeled: int, total: int) -> str:
    cells = [
        ("Review richiesta / necessaria", values["true_positive"], 260, 155, "#86efac"),
        ("Review richiesta / evitabile", values["false_positive"], 520, 155, "#fde68a"),
        ("Review mancata", values["false_negative"], 260, 305, "#fca5a5"),
        ("Continuazione corretta", values["true_negative"], 520, 305, "#bfdbfe"),
    ]
    coverage = (
        f"Eventi etichettati: {labeled} su {total}"
        + (f" ({labeled / total:.0%})" if total else "")
        + (
            "  —  precision e recall valgono solo su questo sottoinsieme"
            if labeled
            else "  —  nessun evento etichettato: precision e recall non calcolabili"
        )
    )
    elements = [
        '<text x="40" y="45" font-size="26" font-family="sans-serif">Confusion matrix delle escalation</text>',
        f'<text x="40" y="72" font-size="15" fill="#475569" font-family="sans-serif">{html.escape(coverage)}</text>',
        '<text x="520" y="112" text-anchor="middle" font-size="18" font-family="sans-serif">Decisione del sistema</text>',
    ]
    for label, value, x, y, color in cells:
        elements.extend(
            [
                f'<rect x="{x}" y="{y}" width="240" height="125" fill="{color}" stroke="#334155"/>',
                f'<text x="{x + 120}" y="{y + 48}" text-anchor="middle" font-size="16" font-family="sans-serif">{html.escape(label)}</text>',
                f'<text x="{x + 120}" y="{y + 92}" text-anchor="middle" font-size="30" font-family="sans-serif">{value}</text>',
            ]
        )
    return _svg_document(820, 480, elements)


# Wrap chart elements in one portable SVG document.
def _svg_document(width: int, height: int, elements: list[str]) -> str:
    body = "\n  ".join(elements)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n'
        f'  <rect width="100%" height="100%" fill="white"/>\n  {body}\n</svg>\n'
    )
