"""Schema validation used exclusively by OPA governance contracts."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


# Validate repository contracts with jsonschema when installed, otherwise a strict local Draft subset.
def validate_json_schema(*, schema_name: str, payload: dict[str, Any]) -> None:
    schema = _load_schema(schema_name)
    try:
        import jsonschema
    except ImportError:
        _validate_subset(schema=schema, value=payload, path="$")
        return
    jsonschema.Draft202012Validator(schema).validate(payload)


# Cache repository schemas because their contents are immutable during one runtime process.
@lru_cache(maxsize=8)
def _load_schema(schema_name: str) -> dict[str, Any]:
    schema_path = Path(__file__).resolve().parents[3] / "schemas" / f"{schema_name}.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


# Keep local development fail-closed when the optional validator dependency is absent.
def _validate_subset(*, schema: dict[str, Any], value: Any, path: str) -> None:
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} must equal {schema['const']!r}.")
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object.")
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path} missing required property '{key}'.")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise ValueError(f"{path} has unexpected properties: {sorted(unknown)}.")
        for key, child in properties.items():
            if key in value:
                _validate_subset(schema=child, value=value[key], path=f"{path}.{key}")
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            for key, child_value in value.items():
                if key not in properties:
                    _validate_subset(schema=additional, value=child_value, path=f"{path}.{key}")
    elif expected_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array.")
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{path} has too few items.")
        if isinstance(schema.get("items"), dict):
            for index, child in enumerate(value):
                _validate_subset(schema=schema["items"], value=child, path=f"{path}[{index}]")
    elif expected_type == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} must be a string.")
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"{path} must not be empty.")
    elif expected_type == "boolean" and not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean.")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']!r}.")
