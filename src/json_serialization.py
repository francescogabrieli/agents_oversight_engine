"""One JSON-safe projection for CLI, runtime artifacts, HITL, and evaluation."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel


# Convert typed contracts recursively into JSON-safe primitive values.
def to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


# Serialize one typed value with stable human-readable formatting.
def to_json(value: Any) -> str:
    return json.dumps(to_jsonable(value), indent=2, ensure_ascii=False)


__all__ = ["to_json", "to_jsonable"]
