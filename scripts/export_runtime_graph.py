"""Export the LangGraph runtime topology in canonical and thesis-ready forms.

The canonical Mermaid source is produced directly by LangGraph. The thesis-ready
source is a compact vertical projection of that same drawable graph: node ids and
labels remain unchanged, while only the layout and selected secondary edges differ.
No runtime node is executed by this script.

Usage:
    .venv/bin/python scripts/export_runtime_graph.py
    .venv/bin/python scripts/export_runtime_graph.py --render

The optional ``--render`` flag uses Mermaid CLI locally to create PNG files. It does
not use the remote mermaid.ink renderer.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "figures"

# Keep the thesis view vertical and close to LangGraph's native ``graph TD`` output.
# These are real edges from the compiled graph, with secondary conditional routes
# omitted only to reduce visual crossings. The canonical export keeps every edge.
_THESIS_EDGES = (
    ("__start__", "bootstrap_from_intake"),
    ("bootstrap_from_intake", "planner"),
    ("planner", "actor"),
    ("actor", "governance"),
    ("governance", "actor"),
    ("governance", "tool_gateway"),
    ("tool_gateway", "observe_tool_result"),
    ("observe_tool_result", "actor"),
    ("governance", "replanner"),
    ("replanner", "actor"),
    ("governance", "human_review"),
    ("human_review", "governance"),
    ("governance", "external_execution"),
    ("external_execution", "human_review"),
    ("governance", "finalize_bootstrap"),
    ("human_review", "finalize_bootstrap"),
    ("external_execution", "finalize_bootstrap"),
    ("finalize_bootstrap", "__end__"),
)

# Make the repository source packages importable when the script is run directly.
def _prepare_import_path() -> None:
    for import_root in (REPO_ROOT, REPO_ROOT / "src"):
        if str(import_root) not in sys.path:
            sys.path.insert(0, str(import_root))


# Build only the compiled graph wiring; no injected service is invoked during export.
def _build_shape_only_graph() -> Any:
    _prepare_import_path()
    from layer2_runtime_governance.graph import build_runtime_graph

    return build_runtime_graph(
        checkpointer=None,
        planner_service=object(),
        actor_service=object(),
        tool_gateway_service=object(),
        tool_gateway_config=None,
        replanner_service=object(),
        policy_engine_service=object(),
        judge_service=None,
        behavioral_store_service=None,
        external_operation_executor_service=object(),
        external_capability_registry=None,
    )


# Build the compact vertical Mermaid projection from LangGraph's drawable topology.
def _build_thesis_mermaid(drawable_graph: Any) -> str:
    lines = [
        "---",
        "config:",
        "  theme: base",
        "  flowchart:",
        "    curve: linear",
        "    nodeSpacing: 45",
        "    rankSpacing: 65",
        "    padding: 12",
        "---",
        "%% Compact presentation projection generated from the LangGraph drawable graph.",
        "%% Node ids and labels remain identical to the native LangGraph export.",
        "graph TD;",
    ]

    for node_id in drawable_graph.nodes:
        if node_id == "__start__" or node_id == "__end__":
            node_class = "first" if node_id == "__start__" else "last"
            lines.append(f"    {node_id}([<p>{node_id}</p>]):::{node_class}")
        else:
            lines.append(f"    {node_id}({node_id})")

    canonical_edges = {
        (edge.source, edge.target): edge for edge in drawable_graph.edges
    }
    for edge_key in _THESIS_EDGES:
        edge = canonical_edges.get(edge_key)
        if edge is None:
            raise ValueError(
                "Thesis projection references an edge that is not present in the "
                f"compiled graph: {edge_key[0]} -> {edge_key[1]}"
            )
        edge_syntax = "-.->" if edge.conditional else "-->"
        lines.append(f"    {edge.source} {edge_syntax} {edge.target}")

    lines.extend(
        [
            "    classDef default fill:#f2f0ff,line-height:1.2",
            "    classDef first fill-opacity:0",
            "    classDef last fill:#bfb6fc",
        ]
    )
    return "\n".join(lines) + "\n"


# Export canonical and presentation Mermaid sources from one compiled graph shape.
def export_runtime_graph(output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    drawable_graph = _build_shape_only_graph().get_graph(xray=True)
    canonical_path = output_dir / "layer2_runtime_graph.mmd"
    thesis_path = output_dir / "layer2_runtime_graph_thesis.mmd"
    canonical_path.write_text(drawable_graph.draw_mermaid(), encoding="utf-8")
    thesis_path.write_text(_build_thesis_mermaid(drawable_graph), encoding="utf-8")
    return canonical_path, thesis_path


# Render Mermaid sources locally through the Mermaid CLI when requested explicitly.
def _render_with_mermaid_cli(source_path: Path, output_path: Path) -> None:
    mmdc = shutil.which("mmdc")
    command = [mmdc, "-i", str(source_path), "-o", str(output_path), "--scale", "2"] if mmdc else [
        "npx",
        "--yes",
        "@mermaid-js/mermaid-cli",
        "-i",
        str(source_path),
        "-o",
        str(output_path),
        "--scale",
        "2",
    ]
    subprocess.run(command, check=True, cwd=REPO_ROOT)


# Parse CLI options and export the requested runtime graph artifacts.
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory receiving Mermaid sources and optional PNGs.",
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="Render both Mermaid sources locally with Mermaid CLI.",
    )
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else REPO_ROOT / args.output_dir
    canonical_path, thesis_path = export_runtime_graph(output_dir)
    print(f"Wrote {canonical_path}")
    print(f"Wrote {thesis_path}")
    if args.render:
        for source_path in (canonical_path, thesis_path):
            output_path = source_path.with_suffix(".png")
            _render_with_mermaid_cli(source_path, output_path)
            print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
