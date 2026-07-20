"""Read-only JSON loading for exported Layer 2 traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import TraceEvaluationError


# Load one JSON file while retaining the legacy encoding compatibility order.
def load_layer2_trace_file(source_path: Path) -> dict[str, Any]:
    payload = _load_json_file(source_path)
    return payload


# Decode a JSON file without mutating the source artifact.
def _load_json_file(source_path: Path) -> Any:
    try:
        raw_bytes = source_path.read_bytes()
    except FileNotFoundError as error:
        raise TraceEvaluationError(
            issue_type="discovery_error",
            message=f"Trace file does not exist: {source_path}",
        ) from error

    decode_attempts = ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-8")
    last_error: Exception | None = None
    for encoding in decode_attempts:
        try:
            return json.loads(raw_bytes.decode(encoding))
        except UnicodeDecodeError as error:
            last_error = error
        except json.JSONDecodeError as error:
            raise TraceEvaluationError(
                issue_type="parse_failed",
                message=f"Trace file is not valid JSON: {source_path} ({error.msg})",
            ) from error

    raise TraceEvaluationError(
        issue_type="parse_failed",
        message=f"Trace file could not be decoded as JSON text: {source_path}",
    ) from last_error


__all__ = ["load_layer2_trace_file"]
