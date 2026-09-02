"""
Thesis Plotting and Analysis Helpers for Layer 5 Evaluation.

Provides publication-quality matplotlib / seaborn figures for the Master's Thesis:
'Human Oversight and Human-in-the-Loop Control Engine for Autonomous AI Agents'

All rates and counts are read from `metrics.build_evaluation_summary` - the same
canonical aggregation `reporting.py` uses for the SVG bundle. This module never
recomputes a rate or a confusion matrix on its own; it only queries raw per-run
distributions (turn counts, review latency) that the summary intentionally keeps
as averages, for the boxplots/histograms that need the individual values.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src is in sys.path when running from notebooks or external scripts
_src_dir = str(Path(__file__).resolve().parents[1] / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from layer5_evaluation_continuous_improvement.metrics import (
    EvaluationSummary,
    build_evaluation_summary,
)
from layer5_evaluation_continuous_improvement.store import EvaluationStore


# Set publication-ready style defaults
def setup_thesis_style() -> None:
    sns.set_theme(style="whitegrid", font="sans-serif")
    plt.rcParams.update(
        {
            "font.size": 11,
            "axes.labelsize": 12,
            "axes.titlesize": 14,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 10,
            "figure.titlesize": 16,
            "figure.dpi": 150,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
        }
    )


# Palette tokens matching architectural components
PALETTE = {
    "primary": "#2563eb",  # Blue
    "secondary": "#475569",  # Slate
    "success": "#16a34a",  # Green (True Positives / Correct)
    "warning": "#ca8a04",  # Amber/Yellow (False Positives / Edit)
    "danger": "#dc2626",  # Red (False Negatives / Incorrect)
    "info": "#0284c7",  # Light blue
    "purple": "#7c3aed",  # Violet (OPA / Governance)
}


# Load the canonical evaluation summary - the single source of every rate/count plotted below.
def load_evaluation_summary(
    database_path: str | Path = "artifacts/evaluation/layer5_evaluation.sqlite3",
    *,
    experiment_id: str | None = None,
) -> EvaluationSummary:
    with EvaluationStore(Path(database_path)) as store:
        return build_evaluation_summary(store, experiment_id=experiment_id)


# Raw per-run distributions for the overhead boxplots/histogram - metrics.py deliberately
# exposes only the mean of these, so the individual values live here instead.
def load_run_level_frames(
    database_path: str | Path = "artifacts/evaluation/layer5_evaluation.sqlite3",
) -> dict[str, pd.DataFrame]:
    db_path = Path(database_path)
    if not db_path.exists():
        return {"runs": pd.DataFrame(), "human_reviews": pd.DataFrame()}
    with EvaluationStore(db_path) as store:
        conn = store.connection
        runs = pd.read_sql_query(
            "SELECT run_id, experiment_id, actor_turn_count, tool_call_count, "
            "governance_pass_count, replanning_count FROM runs WHERE trace_available = 1",
            conn,
        )
        reviews = pd.read_sql_query(
            "SELECT run_id, review_time_seconds FROM human_reviews", conn
        )
    return {"runs": runs, "human_reviews": reviews}


# Distinct experiment_ids actually present in the store, for the cross-experiment comparison.
def list_experiment_ids(
    database_path: str | Path = "artifacts/evaluation/layer5_evaluation.sqlite3",
) -> list[str]:
    db_path = Path(database_path)
    if not db_path.exists():
        return []
    with EvaluationStore(db_path) as store:
        rows = store.query(
            "SELECT DISTINCT experiment_id FROM runs WHERE experiment_id IS NOT NULL "
            "ORDER BY experiment_id"
        )
    return [str(row["experiment_id"]) for row in rows]


def plot_technical_vs_semantic_gap(summary: EvaluationSummary) -> plt.Figure:
    """Figure 1: Technical Completion Rate vs. Semantic Correctness Rate."""
    setup_thesis_style()
    fig, ax = plt.subplots(figsize=(8, 4.5))

    tech_comp_rate = summary.technical_completion_rate
    judged_total = summary.judged_runs
    correct_rate = summary.correct_rate or 0.0
    partial_rate = (summary.task_results.get("partial", 0) / judged_total) if judged_total else 0.0
    incorrect_rate = (summary.task_results.get("incorrect", 0) / judged_total) if judged_total else 0.0

    categories = ["Technical Completion\n(Runtime Status)", "Semantic Correctness\n(Task Ground Truth)"]

    ax.barh([0], [tech_comp_rate * 100], color="#64748b", height=0.4, label="Completed (Technical)")
    if (1.0 - tech_comp_rate) > 0:
        ax.barh([0], [(1.0 - tech_comp_rate) * 100], left=[tech_comp_rate * 100], color="#cbd5e1", height=0.4, label="Aborted / Failed")

    ax.barh([1], [correct_rate * 100], color=PALETTE["success"], height=0.4, label="Correct (Semantically Valid)")
    ax.barh([1], [partial_rate * 100], left=[correct_rate * 100], color=PALETTE["warning"], height=0.4, label="Partial")
    ax.barh([1], [incorrect_rate * 100], left=[(correct_rate + partial_rate) * 100], color=PALETTE["danger"], height=0.4, label="Incorrect (Semantic Failure)")

    ax.set_yticks([0, 1])
    ax.set_yticklabels(categories)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Percentage (%)")
    ax.set_title(
        "Technical Completion vs. True Semantic Correctness\n"
        f"({summary.attempted_runs} attempted; {judged_total}/{summary.total_runs} planned runs judged)",
        pad=15,
    )

    for i, rate in enumerate([tech_comp_rate, correct_rate]):
        ax.text(rate * 50, i, f"{rate * 100:.1f}%", va="center", ha="center", color="white", fontweight="bold", fontsize=11)

    ax.legend(bbox_to_anchor=(0.5, -0.2), loc="upper center", ncol=3, frameon=True)
    plt.tight_layout()
    return fig


def plot_escalation_confusion_matrix(summary: EvaluationSummary) -> plt.Figure:
    """Figure 2: Human Oversight Confusion Matrix (Escalations)."""
    setup_thesis_style()
    fig, ax = plt.subplots(figsize=(6.5, 5))

    confusion = summary.escalation_confusion
    tp, fp = confusion["true_positive"], confusion["false_positive"]
    fn, tn = confusion["false_negative"], confusion["true_negative"]
    total = tp + fp + fn + tn
    precision = summary.escalation_precision or 0.0
    recall = summary.escalation_recall or 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    matrix = np.array([[tp, fp], [fn, tn]])

    def cell(count: int) -> str:
        return f"{count} ({count / total * 100:.1f}%)" if total else f"{count} (n/a)"

    labels = [
        [f"True Positive\n(Review Needed & Required)\n\n{cell(tp)}", f"False Positive\n(Unnecessary Escalation)\n\n{cell(fp)}"],
        [f"False Negative\n(Missed Risk)\n\n{cell(fn)}", f"True Negative\n(Correct Autonomy)\n\n{cell(tn)}"],
    ]

    sns.heatmap(matrix, annot=labels, fmt="", cmap="Blues", cbar=False, ax=ax, linewidths=1.5, linecolor="white", annot_kws={"fontsize": 10})

    ax.set_xticklabels(["Escalated (HITL)", "Autonomous (Continue)"])
    ax.set_yticklabels(["Actually Risky\n(Review Needed)", "Safe Execution\n(No Review Needed)"], va="center")
    ax.set_xlabel("System Governance Decision")
    ax.set_ylabel("Ground Truth Requirement")
    ax.set_title(
        f"Human Oversight Escalation Matrix\nPrecision: {precision:.2f} | Recall: {recall:.2f} | F1: {f1:.2f}"
        f"\nLabeled {summary.labeled_governance_events}/{summary.total_governance_events} governance events",
        pad=15,
    )

    plt.tight_layout()
    return fig


def plot_governance_pillars_and_reasons(summary: EvaluationSummary) -> plt.Figure:
    """Figure 3: Governance Pillars and Reason Code breakdown."""
    setup_thesis_style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    pillar_counts = summary.pillar_breakdown
    reason_counts = summary.reason_codes

    if not pillar_counts or not reason_counts:
        for axis in (ax1, ax2):
            axis.text(0.5, 0.5, "No governance events with reason codes yet", ha="center", va="center")
            axis.set_xticks([])
            axis.set_yticks([])
        plt.tight_layout()
        return fig

    p_df = pd.DataFrame(list(pillar_counts.items()), columns=["Pillar", "Count"]).sort_values("Count", ascending=True)
    ax1.barh(p_df["Pillar"], p_df["Count"], color=PALETTE["purple"], height=0.55)
    ax1.set_xlabel("Occurrences")
    ax1.set_title("Oversight Triggers by Governance Pillar", pad=12)
    for index, value in enumerate(p_df["Count"]):
        ax1.text(value + 0.3, index, str(value), va="center", fontweight="bold")

    r_df = pd.DataFrame(list(reason_counts.items()), columns=["Reason Code", "Count"]).sort_values("Count", ascending=True).tail(8)
    ax2.barh(r_df["Reason Code"], r_df["Count"], color=PALETTE["primary"], height=0.55)
    ax2.set_xlabel("Occurrences")
    ax2.set_title("Top Governance Reason Codes", pad=12)
    for index, value in enumerate(r_df["Count"]):
        ax2.text(value + 0.3, index, str(value), va="center", fontweight="bold")

    plt.tight_layout()
    return fig


def plot_overhead_and_efficiency(run_frames: dict[str, pd.DataFrame]) -> plt.Figure:
    """Figure 4: Computational & Interaction Overhead Distribution (raw per-run values)."""
    setup_thesis_style()
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(14, 4.5))

    runs_df = run_frames.get("runs", pd.DataFrame())
    reviews_df = run_frames.get("human_reviews", pd.DataFrame())

    if runs_df.empty:
        for axis in (ax1, ax2, ax3):
            axis.text(0.5, 0.5, "No runs yet", ha="center", va="center")
        plt.tight_layout()
        return fig

    sns.boxplot(y=runs_df["actor_turn_count"], ax=ax1, color="#93c5fd", width=0.4)
    ax1.set_title("Actor Turns per Run")
    ax1.set_ylabel("Turn Count")

    melted = runs_df[["tool_call_count", "governance_pass_count"]].melt()
    melted["variable"] = melted["variable"].map({"tool_call_count": "Tool Calls", "governance_pass_count": "Gov Passes"})
    sns.boxplot(x="variable", y="value", data=melted, ax=ax2, hue="variable", palette=["#86efac", "#c084fc"], legend=False, width=0.5)
    ax2.set_title("Tool Calls & Governance Passes")
    ax2.set_xlabel("")
    ax2.set_ylabel("Count per Run")

    if not reviews_df.empty and reviews_df["review_time_seconds"].notna().any():
        sns.histplot(reviews_df["review_time_seconds"], ax=ax3, kde=True, color=PALETTE["warning"], bins=10)
        ax3.set_title("Human Review Duration")
        ax3.set_xlabel("Review Time (seconds)")
        ax3.set_ylabel("Review Count")
    else:
        ax3.text(0.5, 0.5, "No review timing recorded", ha="center", va="center")

    plt.tight_layout()
    return fig


def plot_experiment_comparison(
    database_path: str | Path = "artifacts/evaluation/layer5_evaluation.sqlite3",
) -> plt.Figure | None:
    """Figure 5: Compare technical completion / correctness / human-intervention rate across
    whatever experiment_ids are actually present in the store. Returns None when fewer than
    two experiments exist - there is nothing to compare yet, and this never fabricates an
    ablation design that has no corresponding real campaign runner."""
    setup_thesis_style()
    experiment_ids = list_experiment_ids(database_path)
    if len(experiment_ids) < 2:
        return None

    rows = []
    for experiment_id in experiment_ids:
        summary = load_evaluation_summary(database_path, experiment_id=experiment_id)
        rows.append(
            {
                "experiment_id": experiment_id,
                "technical_completion_rate": (summary.technical_completion_rate or 0.0) * 100,
                "correct_rate": (summary.correct_rate or 0.0) * 100,
                "human_intervention_rate": (summary.human_intervention_rate or 0.0) * 100,
            }
        )
    grouped = pd.DataFrame(rows)

    fig, ax = plt.subplots(figsize=(9, 5))
    x = np.arange(len(grouped))
    width = 0.27

    ax.bar(x - width, grouped["technical_completion_rate"], width, label="Technical Completion (%)", color=PALETTE["secondary"])
    ax.bar(x, grouped["correct_rate"], width, label="Task Accuracy (%)", color=PALETTE["success"])
    ax.bar(x + width, grouped["human_intervention_rate"], width, label="Human Intervention (%)", color=PALETTE["warning"])

    ax.set_ylabel("Percentage (%)")
    ax.set_title("Comparison Across Experiments", pad=15)
    ax.set_xticks(x)
    ax.set_xticklabels(grouped["experiment_id"], rotation=15)
    ax.set_ylim(0, 105)
    ax.legend(frameon=True)

    plt.tight_layout()
    return fig


def export_all_thesis_figures(
    database_path: str | Path = "artifacts/evaluation/layer5_evaluation.sqlite3",
    output_dir: str | Path = "figures/thesis",
) -> list[Path]:
    """Generate and save all publication figures in PDF (vector for LaTeX) and PNG (high-res).
    Requires a populated evaluation database - ingest a real campaign first
    (see notebooks/README.md); this no longer falls back to synthetic data."""
    summary = load_evaluation_summary(database_path)
    if summary.total_runs == 0:
        raise ValueError(
            f"No runs found in {database_path}. Ingest a real evaluation campaign "
            "before exporting thesis figures (see notebooks/README.md)."
        )
    run_frames = load_run_level_frames(database_path)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    figures = {
        "fig1_technical_vs_semantic_gap": plot_technical_vs_semantic_gap(summary),
        "fig2_escalation_confusion_matrix": plot_escalation_confusion_matrix(summary),
        "fig3_governance_pillars_and_reasons": plot_governance_pillars_and_reasons(summary),
        "fig4_overhead_and_efficiency": plot_overhead_and_efficiency(run_frames),
    }
    comparison = plot_experiment_comparison(database_path)
    if comparison is not None:
        figures["fig5_experiment_comparison"] = comparison

    for name, fig in figures.items():
        pdf_path = out_dir / f"{name}.pdf"
        png_path = out_dir / f"{name}.png"
        fig.savefig(pdf_path, format="pdf", bbox_inches="tight")
        fig.savefig(png_path, format="png", dpi=300, bbox_inches="tight")
        plt.close(fig)
        saved.extend([pdf_path, png_path])

    return saved
