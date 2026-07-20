"""Stable discovery of exported Layer 2 trace files."""

from pathlib import Path
from typing import Sequence

from .contracts import TraceEvaluationError


# Collect trace files from files and directories, recursively, in stable order.
def collect_layer2_trace_files(input_paths: Sequence[Path]) -> list[Path]:
    discovered: list[Path] = []
    for input_path in input_paths:
        resolved = input_path.expanduser()
        if resolved.is_file():
            discovered.append(resolved)
            continue
        if resolved.is_dir():
            discovered.extend(sorted(resolved.rglob("*.json")))
            continue
        raise TraceEvaluationError(
            issue_type="discovery_error",
            message=f"Input path does not exist or is not accessible: {resolved}",
        )
    return discovered


__all__ = ["collect_layer2_trace_files"]
