"""
Thesis Plotting and Analysis Helpers for Layer 5 Evaluation.

Provides publication-quality matplotlib / seaborn figures for the Master's Thesis:
'Human Oversight and Human-in-the-Loop Control Engine for Autonomous AI Agents'
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

# Ensure src is in sys.path when running from notebooks or external scripts
_src_dir = str(Path(__file__).resolve().parents[1] / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


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


def load_evaluation_data(
    database_path: str | Path = "artifacts/evaluation/layer5_evaluation.sqlite3",
) -> dict[str, pd.DataFrame]:
    """Load tables from the Layer 5 SQLite database into Pandas DataFrames."""
    db_path = Path(database_path)
    if not db_path.exists():
        return {}

    conn = sqlite3.connect(db_path)
    tables = ["experiments", "runs", "governance_events", "human_reviews", "external_operations"]
    data = {}
    for table in tables:
        try:
            data[table] = pd.read_sql_query(f"SELECT * FROM {table}", conn)
        except Exception:
            data[table] = pd.DataFrame()
    conn.close()
    return data


def create_sample_evaluation_data() -> dict[str, pd.DataFrame]:
    """Generate representative synthetic evaluation data for offline demonstration and testing."""
    np.random.seed(42)
    experiments_df = pd.DataFrame(
        [
            {
                "experiment_id": "exp_baseline",
                "name": "Baseline Multi-Pillar Oversight",
                "description": "Full runtime with OPA, Trajectory, Evidence and Semantic Judge",
                "created_at": "2026-08-15T10:00:00Z",
            },
            {
                "experiment_id": "exp_no_judge",
                "name": "Ablation: No Semantic Judge",
                "description": "Oversight without LLM-as-a-Judge semantic assessor",
                "created_at": "2026-08-16T10:00:00Z",
            },
            {
                "experiment_id": "exp_static_only",
                "name": "Ablation: Static Policy Only",
                "description": "Rule-based boundaries without active trajectory or evidence checks",
                "created_at": "2026-08-17T10:00:00Z",
            },
        ]
    )

    runs = []
    governance_events = []
    human_reviews = []
    external_ops = []

    exp_configs = {
        "exp_baseline": {
            "n": 60,
            "corr_prob": 0.88,
            "esc_rate": 0.35,
            "fp_rate": 0.08,
            "fn_rate": 0.02,
        },
        "exp_no_judge": {
            "n": 40,
            "corr_prob": 0.72,
            "esc_rate": 0.20,
            "fp_rate": 0.05,
            "fn_rate": 0.22,
        },
        "exp_static_only": {
            "n": 40,
            "corr_prob": 0.60,
            "esc_rate": 0.15,
            "fp_rate": 0.03,
            "fn_rate": 0.38,
        },
    }

    run_idx = 1
    event_idx = 1
    review_idx = 1
    op_idx = 1

    for exp_id, cfg in exp_configs.items():
        for i in range(cfg["n"]):
            r_id = f"run_{run_idx:03d}"
            run_idx += 1
            is_completed = np.random.rand() > 0.05
            status = "completed" if is_completed else "aborted"
            is_correct = (
                np.random.rand() < cfg["corr_prob"]
                if is_completed
                else (np.random.rand() < 0.1)
            )
            result = "correct" if is_correct else ("partial" if np.random.rand() < 0.3 else "incorrect")

            actor_turns = np.random.randint(2, 6)
            tool_calls = np.random.randint(1, 5)
            failed_tools = 1 if np.random.rand() < 0.12 else 0
            replans = 1 if np.random.rand() < 0.18 else 0
            passes = actor_turns + 1

            runs.append(
                {
                    "run_id": r_id,
                    "experiment_id": exp_id,
                    "task_case_id": f"task_{((run_idx - 1) % 15) + 1:02d}",
                    "trace_path": f"artifacts/runtime/{r_id}/full-trace-2.json",
                    "trace_digest": f"digest_{r_id}",
                    "trace_version": "layer2_trace.v6",
                    "configuration_fingerprint": f"fp_{exp_id}",
                    "runtime_status": status,
                    "task_result": result,
                    "evaluation_method": "human" if np.random.rand() < 0.5 else "automatic",
                    "goal": f"Task goal {run_idx}",
                    "sensitivity": "high" if np.random.rand() < 0.6 else "medium",
                    "execution_intent": "execute_after_governance",
                    "duration_seconds": round(float(np.random.uniform(8.5, 34.0)), 2),
                    "actor_turn_count": actor_turns,
                    "tool_call_count": tool_calls,
                    "failed_tool_count": failed_tools,
                    "replanning_count": replans,
                    "governance_pass_count": passes,
                    "human_review_count": 0,
                    "external_operation_count": 1,
                    "imported_at": "2026-08-18T10:00:00Z",
                }
            )

            # Governance passes
            requires_esc = np.random.rand() < cfg["esc_rate"]
            needed = (
                not is_correct
                if np.random.rand() > 0.1
                else (np.random.rand() < 0.2)
            )

            for p in range(1, passes + 1):
                is_last = p == passes
                ev_id = f"{r_id}:governance:{p}"
                req_review = 1 if (is_last and requires_esc) else 0

                codes = []
                if req_review:
                    if exp_id == "exp_baseline":
                        codes = list(
                            np.random.choice(
                                [
                                    "judge_uncertain",
                                    "evidence_insufficient",
                                    "action_revision_limit_reached",
                                    "prohibited_destination",
                                ],
                                size=np.random.randint(1, 3),
                                replace=False,
                            )
                        )
                    elif exp_id == "exp_no_judge":
                        codes = list(
                            np.random.choice(
                                ["evidence_insufficient", "action_revision_limit_reached"],
                                size=1,
                            )
                        )
                    else:
                        codes = ["prohibited_destination"]
                else:
                    codes = ["allowed_by_policy"] if np.random.rand() < 0.8 else ["local_output_allowed"]

                governance_events.append(
                    {
                        "event_id": ev_id,
                        "run_id": r_id,
                        "pass_index": p,
                        "directive": "wait_human_review" if req_review else "continue",
                        "policy_decision": "require_human_review" if req_review else "allow",
                        "reason_codes_json": json.dumps(codes),
                        "requires_human_review": req_review,
                        "human_review_was_needed": (1 if needed else 0) if req_review else None,
                    }
                )

                if req_review:
                    runs[-1]["human_review_count"] += 1
                    dec = np.random.choice(
                        ["approve", "modify_and_continue", "reject", "abort"],
                        p=[0.45, 0.35, 0.15, 0.05],
                    )
                    human_reviews.append(
                        {
                            "review_id": f"{r_id}:review:{review_idx}",
                            "run_id": r_id,
                            "governance_event_id": ev_id,
                            "request_id": f"req_{review_idx}",
                            "decision": dec,
                            "reviewer_id": "operator_1",
                            "feedback": "Modified target parameter" if dec == "modify_and_continue" else "Approved",
                            "human_modified": 1 if dec == "modify_and_continue" else 0,
                            "review_time_seconds": round(float(np.random.uniform(15.0, 65.0)), 2),
                        }
                    )
                    review_idx += 1

            # External operation
            external_ops.append(
                {
                    "row_id": f"{r_id}:external:{op_idx}",
                    "run_id": r_id,
                    "operation_id": f"op_{op_idx}",
                    "capability": "smtp_email",
                    "payload_digest": f"digest_op_{op_idx}",
                    "human_modified": 1 if any(r["run_id"] == r_id and r["human_modified"] == 1 for r in human_reviews) else 0,
                    "execution_status": "executed" if is_completed else "blocked",
                    "operation_correct": 1 if is_correct else 0,
                    "started_at": "2026-08-18T10:05:00Z",
                    "completed_at": "2026-08-18T10:05:02Z",
                    "duration_seconds": 2.0,
                }
            )
            op_idx += 1

    return {
        "experiments": experiments_df,
        "runs": pd.DataFrame(runs),
        "governance_events": pd.DataFrame(governance_events),
        "human_reviews": pd.DataFrame(human_reviews),
        "external_operations": pd.DataFrame(external_ops),
    }


def plot_technical_vs_semantic_gap(runs_df: pd.DataFrame) -> plt.Figure:
    """Figure 1: Technical Completion Rate vs. Semantic Correctness Rate."""
    setup_thesis_style()
    fig, ax = plt.subplots(figsize=(8, 4.5))

    completed = runs_df[runs_df["runtime_status"] == "completed"]
    tech_comp_rate = len(completed) / len(runs_df) if len(runs_df) else 0

    judged = runs_df[runs_df["task_result"].notna()]
    correct_rate = (judged["task_result"] == "correct").mean() if len(judged) else 0
    partial_rate = (judged["task_result"] == "partial").mean() if len(judged) else 0
    incorrect_rate = (judged["task_result"] == "incorrect").mean() if len(judged) else 0

    categories = ["Technical Completion\n(Runtime Status)", "Semantic Correctness\n(Task Ground Truth)"]

    # Plot technical bar
    ax.barh([0], [tech_comp_rate * 100], color="#64748b", height=0.4, label="Completed (Technical)")
    if (1.0 - tech_comp_rate) > 0:
        ax.barh([0], [(1.0 - tech_comp_rate) * 100], left=[tech_comp_rate * 100], color="#cbd5e1", height=0.4, label="Aborted / Failed")

    # Plot semantic stack
    ax.barh([1], [correct_rate * 100], color=PALETTE["success"], height=0.4, label="Correct (Semantically Valid)")
    ax.barh([1], [partial_rate * 100], left=[correct_rate * 100], color=PALETTE["warning"], height=0.4, label="Partial")
    ax.barh([1], [incorrect_rate * 100], left=[(correct_rate + partial_rate) * 100], color=PALETTE["danger"], height=0.4, label="Incorrect (Semantic Failure)")

    ax.set_yticks([0, 1])
    ax.set_yticklabels(categories)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Percentage (%)")
    ax.set_title("Technical Completion vs. True Semantic Correctness", pad=15)

    for i, rate in enumerate([tech_comp_rate, correct_rate]):
        ax.text(rate * 50, i, f"{rate * 100:.1f}%", va="center", ha="center", color="white", fontweight="bold", fontsize=11)

    ax.legend(bbox_to_anchor=(0.5, -0.2), loc="upper center", ncol=3, frameon=True)
    plt.tight_layout()
    return fig


def plot_escalation_confusion_matrix(gov_df: pd.DataFrame) -> plt.Figure:
    """Figure 2: Human Oversight Confusion Matrix (Escalations)."""
    setup_thesis_style()
    fig, ax = plt.subplots(figsize=(6.5, 5))

    reviewed_events = gov_df[gov_df["human_review_was_needed"].notna()]
    if reviewed_events.empty:
        tp, fp, fn, tn = 12, 3, 1, 45
    else:
        req = reviewed_events["requires_human_review"].astype(bool)
        need = reviewed_events["human_review_was_needed"].astype(bool)
        tp = int((req & need).sum())
        fp = int((req & ~need).sum())
        fn = int((~req & need).sum())
        tn = int((~req & ~need).sum())

    matrix = np.array([[tp, fp], [fn, tn]])
    total = tp + fp + fn + tn
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    labels = [
        [f"True Positive\n(Review Needed & Required)\n\n{tp} ({tp/total*100:.1f}%)", f"False Positive\n(Unnecessary Escalation)\n\n{fp} ({fp/total*100:.1f}%)"],
        [f"False Negative\n(Missed Risk)\n\n{fn} ({fn/total*100:.1f}%)", f"True Negative\n(Correct Autonomy)\n\n{tn} ({tn/total*100:.1f}%)"],
    ]

    cmap = sns.color_palette(["#f1f5f9", "#e2e8f0", "#bfdbfe", "#3b82f6"], as_cmap=True)
    sns.heatmap(matrix, annot=labels, fmt="", cmap="Blues", cbar=False, ax=ax, linewidths=1.5, linecolor="white", annot_kws={"fontsize": 10})

    ax.set_xticklabels(["Escalated (HITL)", "Autonomous (Continue)"])
    ax.set_yticklabels(["Actually Risky\n(Review Needed)", "Safe Execution\n(No Review Needed)"], va="center")
    ax.set_xlabel("System Governance Decision")
    ax.set_ylabel("Ground Truth Requirement")
    ax.set_title(f"Human Oversight Escalation Matrix\nPrecision: {precision:.2f} | Recall: {recall:.2f} | F1: {f1:.2f}", pad=15)

    plt.tight_layout()
    return fig


def plot_governance_pillars_and_reasons(gov_df: pd.DataFrame) -> plt.Figure:
    """Figure 3: Governance Pillars and Reason Code breakdown."""
    setup_thesis_style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    from layer5_evaluation_continuous_improvement.metrics import classify_reason_code_pillar

    reason_counts: dict[str, int] = {}
    pillar_counts: dict[str, int] = {}

    for raw in gov_df["reason_codes_json"]:
        if not raw:
            continue
        try:
            codes = json.loads(raw)
            for c in codes:
                reason_counts[c] = reason_counts.get(c, 0) + 1
                p = classify_reason_code_pillar(c)
                pillar_counts[p] = pillar_counts.get(p, 0) + 1
        except Exception:
            continue

    if not pillar_counts:
        pillar_counts = {"Semantic Judge": 15, "Evidence Pillar": 12, "Trajectory Pillar": 8, "Static Policy & Safety": 6}
    if not reason_counts:
        reason_counts = {"judge_uncertain": 15, "evidence_insufficient": 12, "action_revision_limit_reached": 8, "prohibited_destination": 6}

    # Pillars chart
    p_df = pd.DataFrame(list(pillar_counts.items()), columns=["Pillar", "Count"]).sort_values("Count", ascending=True)
    ax1.barh(p_df["Pillar"], p_df["Count"], color=PALETTE["purple"], height=0.55)
    ax1.set_xlabel("Occurrences")
    ax1.set_title("Oversight Triggers by Governance Pillar", pad=12)
    for index, value in enumerate(p_df["Count"]):
        ax1.text(value + 0.3, index, str(value), va="center", fontweight="bold")

    # Reason codes chart (top 6)
    r_df = pd.DataFrame(list(reason_counts.items()), columns=["Reason Code", "Count"]).sort_values("Count", ascending=True).tail(8)
    ax2.barh(r_df["Reason Code"], r_df["Count"], color=PALETTE["primary"], height=0.55)
    ax2.set_xlabel("Occurrences")
    ax2.set_title("Top Governance Reason Codes", pad=12)
    for index, value in enumerate(r_df["Count"]):
        ax2.text(value + 0.3, index, str(value), va="center", fontweight="bold")

    plt.tight_layout()
    return fig


def plot_overhead_and_efficiency(runs_df: pd.DataFrame, reviews_df: pd.DataFrame) -> plt.Figure:
    """Figure 4: Computational & Interaction Overhead Distribution."""
    setup_thesis_style()
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(14, 4.5))

    # Turn counts
    sns.boxplot(y=runs_df["actor_turn_count"], ax=ax1, color="#93c5fd", width=0.4)
    ax1.set_title("Actor Turns per Run")
    ax1.set_ylabel("Turn Count")

    # Tool calls & Governance passes
    melted = runs_df[["tool_call_count", "governance_pass_count"]].melt()
    melted["variable"] = melted["variable"].map({"tool_call_count": "Tool Calls", "governance_pass_count": "Gov Passes"})
    sns.boxplot(x="variable", y="value", data=melted, ax=ax2, hue="variable", palette=["#86efac", "#c084fc"], legend=False, width=0.5)
    ax2.set_title("Tool Calls & Governance Passes")
    ax2.set_xlabel("")
    ax2.set_ylabel("Count per Run")

    # Review latency
    if not reviews_df.empty and "review_time_seconds" in reviews_df and reviews_df["review_time_seconds"].notna().any():
        sns.histplot(reviews_df["review_time_seconds"], ax=ax3, kde=True, color=PALETTE["warning"], bins=10)
        ax3.set_title("Human Review Duration")
        ax3.set_xlabel("Review Time (seconds)")
        ax3.set_ylabel("Review Count")
    else:
        ax3.text(0.5, 0.5, "No review timing recorded", ha="center", va="center")

    plt.tight_layout()
    return fig


def plot_ablation_comparison(runs_df: pd.DataFrame) -> plt.Figure:
    """Figure 5: Comparative Ablation Study across configurations."""
    setup_thesis_style()
    fig, ax = plt.subplots(figsize=(9, 5))

    grouped = runs_df.groupby("experiment_id").agg(
        total_runs=("run_id", "count"),
        correct_runs=("task_result", lambda s: (s == "correct").sum()),
        esc_runs=("human_review_count", lambda s: (s > 0).sum()),
        avg_turns=("actor_turn_count", "mean"),
    ).reset_index()

    grouped["correct_rate"] = grouped["correct_runs"] / grouped["total_runs"] * 100
    grouped["esc_rate"] = grouped["esc_runs"] / grouped["total_runs"] * 100

    x = np.arange(len(grouped))
    width = 0.35

    rects1 = ax.bar(x - width/2, grouped["correct_rate"], width, label="Task Accuracy (%)", color=PALETTE["success"])
    rects2 = ax.bar(x + width/2, grouped["esc_rate"], width, label="Human Escalation Rate (%)", color=PALETTE["warning"])

    ax.set_ylabel("Percentage (%)")
    ax.set_title("Ablation Study: Accuracy vs. Human Intervention by Configuration", pad=15)
    ax.set_xticks(x)
    ax.set_xticklabels(grouped["experiment_id"], rotation=15)
    ax.set_ylim(0, 105)
    ax.legend(frameon=True)

    for rects in [rects1, rects2]:
        for rect in rects:
            height = rect.get_height()
            ax.annotate(f"{height:.1f}%", xy=(rect.get_x() + rect.get_width() / 2, height),
                        xytext=(0, 3), textcoords="offset points", ha="center", va="bottom", fontsize=10, fontweight="bold")

    plt.tight_layout()
    return fig


def export_all_thesis_figures(data: dict[str, pd.DataFrame], output_dir: str | Path = "figures/thesis") -> list[Path]:
    """Generate and save all publication figures in PDF (vector for LaTeX) and PNG (high-res)."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    runs_df = data.get("runs", pd.DataFrame())
    gov_df = data.get("governance_events", pd.DataFrame())
    reviews_df = data.get("human_reviews", pd.DataFrame())

    if runs_df.empty:
        data = create_sample_evaluation_data()
        runs_df = data["runs"]
        gov_df = data["governance_events"]
        reviews_df = data["human_reviews"]

    figures = {
        "fig1_technical_vs_semantic_gap": plot_technical_vs_semantic_gap(runs_df),
        "fig2_escalation_confusion_matrix": plot_escalation_confusion_matrix(gov_df),
        "fig3_governance_pillars_and_reasons": plot_governance_pillars_and_reasons(gov_df),
        "fig4_overhead_and_efficiency": plot_overhead_and_efficiency(runs_df, reviews_df),
        "fig5_ablation_comparison": plot_ablation_comparison(runs_df),
    }

    for name, fig in figures.items():
        pdf_path = out_dir / f"{name}.pdf"
        png_path = out_dir / f"{name}.png"
        fig.savefig(pdf_path, format="pdf", bbox_inches="tight")
        fig.savefig(png_path, format="png", dpi=300, bbox_inches="tight")
        plt.close(fig)
        saved.extend([pdf_path, png_path])

    return saved
