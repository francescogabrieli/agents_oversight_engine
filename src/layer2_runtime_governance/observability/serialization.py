
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel


# Convert typed runtime objects recursively into JSON-safe values.
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
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


# Convert one typed value into stable formatted JSON.
def to_json(value: Any) -> str:
    return json.dumps(to_jsonable(value), indent=2, ensure_ascii=False)


# Convert enum-like values to strings while accepting materialized values.
def enum_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        return value
    return str(value)


__all__ = ["enum_value", "to_json", "to_jsonable"]
