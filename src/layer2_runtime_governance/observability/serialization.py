
import json
from enum import Enum
from typing import Any

from json_serialization import to_jsonable


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
