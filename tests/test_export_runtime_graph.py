"""Regression tests for the canonical and thesis-ready runtime graph exports."""

from pathlib import Path

from scripts.export_runtime_graph import export_runtime_graph


# Ensure both exports are generated from the same runtime topology source.
def test_export_runtime_graph_writes_canonical_and_thesis_sources(tmp_path: Path) -> None:
    canonical_path, thesis_path = export_runtime_graph(tmp_path)

    canonical = canonical_path.read_text(encoding="utf-8")
    thesis = thesis_path.read_text(encoding="utf-8")

    assert canonical_path == tmp_path / "layer2_runtime_graph.mmd"
    assert thesis_path == tmp_path / "layer2_runtime_graph_thesis.mmd"
    assert "graph TD;" in canonical
    assert "graph TD;" in thesis
    assert "governance(governance)" in thesis
    assert "finalize_bootstrap(finalize_bootstrap)" in thesis
    assert "|resume|" not in thesis
    assert "|approve|" not in thesis
    assert "layer2_runtime_graph_thesis" not in canonical
