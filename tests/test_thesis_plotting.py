"""Render regressions with optional evaluation dependencies, isolated from real runs."""
from dataclasses import replace

import pytest

matplotlib = pytest.importorskip("matplotlib")
pytest.importorskip("seaborn")
matplotlib.use("Agg")

from notebooks import thesis_plotting as plotting  # noqa: E402
from layer5_evaluation_continuous_improvement import EvaluationStore, build_evaluation_summary  # noqa: E402


def test_confusion_cells_match_axes_and_export(tmp_path):
    with EvaluationStore(tmp_path / "test.sqlite3") as store:
        summary = build_evaluation_summary(store)
    summary = replace(
        summary,
        escalation_confusion={"true_positive": 1, "false_negative": 2, "false_positive": 3, "true_negative": 4},
        labeled_governance_events=10, total_governance_events=10,
    )
    fig = plotting.plot_escalation_confusion_matrix(summary)
    try:
        axis = fig.axes[0]
        assert axis.collections[0].get_array().reshape(2, 2).tolist() == [[1, 2], [3, 4]]
        text_by_position = {text.get_position(): text.get_text() for text in axis.texts}
        assert "False Negative" in text_by_position[(1.5, 0.5)]
        assert "False Positive" in text_by_position[(0.5, 1.5)]
        assert "Precision: N/D" in axis.get_title()
        assert "Recall: N/D" in axis.get_title()
        for extension in ("pdf", "png"):
            target = tmp_path / f"confusion.{extension}"
            fig.savefig(target)
            assert target.stat().st_size > 0
    finally:
        plotting.plt.close(fig)


def test_unavailable_is_distinct_from_measured_zero(tmp_path):
    with EvaluationStore(tmp_path / "test.sqlite3") as store:
        summary = build_evaluation_summary(store)
    for candidate, expected in (
        (summary, "N/D"),
        (replace(summary, technical_completion_rate=0.0, correct_rate=0.0), "0.0%"),
    ):
        fig = plotting.plot_technical_vs_semantic_gap(candidate)
        try:
            assert all(expected in text.get_text() for text in fig.axes[0].texts)
            fig.canvas.draw()
        finally:
            plotting.plt.close(fig)


def test_comparison_marks_unjudged_experiment_as_unavailable(tmp_path, monkeypatch):
    with EvaluationStore(tmp_path / "test.sqlite3") as store:
        summary = build_evaluation_summary(store)
    monkeypatch.setattr(plotting, "list_experiment_ids", lambda _path: ["first", "second"])
    monkeypatch.setattr(plotting, "load_evaluation_summary", lambda _path, **_kwargs: summary)
    fig = plotting.plot_experiment_comparison(tmp_path / "test.sqlite3")
    try:
        assert len([text for text in fig.axes[0].texts if text.get_text() == "N/D"]) == 6
        fig.canvas.draw()
    finally:
        plotting.plt.close(fig)
