import json
from typing import Any

from json_serialization import to_jsonable


# Print one titled JSON block with a stable terminal format.
def print_json_section(title: str, payload: Any) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(to_jsonable(payload), indent=2, ensure_ascii=False))


__all__ = ["print_json_section"]
