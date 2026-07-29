from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from .action_schemas import build_actor_runtime_tools
from .errors import ActorExecutionError


# Convert provider JSON into the same shape used by native function calls.
def convert_json_payload_to_tool_call(
    payload: dict[str, Any],
    *,
    allowed_action_names: set[str] | None = None,
) -> SimpleNamespace:
    normalized_payload = normalize_actor_payload(payload=payload)
    tool_name = normalized_payload.get("tool_name")
    arguments = normalized_payload.get("arguments")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ActorExecutionError("Actor JSON response must include string 'tool_name'.")
    if not isinstance(arguments, dict):
        raise ActorExecutionError("Actor JSON response must include object 'arguments'.")
    allowed_names = allowed_action_names or {
        str(tool["name"]) for tool in build_actor_runtime_tools()
    }
    if tool_name not in allowed_names:
        raise ActorExecutionError(f"Actor emitted an unknown runtime action: {tool_name}")
    return SimpleNamespace(name=tool_name, arguments=json.dumps(arguments, ensure_ascii=False))


# Reject native provider calls that are outside the registry selected for this actor.
def validate_tool_call_action(*, tool_call: object, allowed_action_names: set[str]) -> object:
    tool_name = getattr(tool_call, "name", None)
    if tool_name not in allowed_action_names:
        raise ActorExecutionError(f"Actor emitted an unknown runtime action: {tool_name}")
    return tool_call


# Normalize completion-shaped provider JSON without inventing capability intent.
def normalize_actor_payload(*, payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("tool_name"), str):
        return payload
    output_value = _first_string_value(
        payload,
        "output_text",
        "answer",
        "final_answer",
        "summary",
        "content",
    )
    if output_value and _looks_like_completion_payload(payload):
        return {
            "tool_name": "emit_step_completed",
            "arguments": {
                "summary": _first_string_value(
                    payload,
                    "summary",
                    "completion_rationale",
                    "output_text",
                    "answer",
                    "final_answer",
                    "content",
                )
                or "Actor provider returned JSON without the runtime action envelope.",
                "output_text": output_value,
                "completion_rationale": _first_string_value(
                    payload,
                    "completion_rationale",
                    "summary",
                    "output_text",
                    "answer",
                    "final_answer",
                    "content",
                )
                or "Provider JSON was recovered as a completion payload.",
                # This is a provider-boundary recovery, not a canonical legacy contract.
                "actor_self_confidence": coerce_provider_confidence(
                    payload.get("actor_self_confidence", payload.get("confidence")), default=0.55
                ),
                "evidence_refs": payload.get("evidence_refs", []),
            },
        }
    if output_value:
        return {
            "tool_name": "emit_blocked",
            "arguments": {
                "summary": "Actor provider returned malformed JSON without a usable runtime action.",
                "blocking_reason": (
                    "The actor provider returned answer-like JSON without the required "
                    f"runtime action envelope. Preserved excerpt: {_truncate_excerpt(output_value)}"
                ),
            },
        }
    raise ActorExecutionError("Actor JSON response must include string 'tool_name'.")


# Extract all native function calls in provider order for the shared actor batch contract.
def extract_function_calls(response: object) -> tuple[object, ...]:
    function_calls = [
        item
        for item in getattr(response, "output", []) or []
        if getattr(item, "type", None) == "function_call"
    ]
    if not function_calls:
        raise ActorExecutionError(
            "Actor returned no function tool calls."
        )
    return tuple(function_calls)


# Preserve the legacy public helper while callers migrate to the batch contract.
def extract_single_function_call(response: object) -> object:
    calls = extract_function_calls(response)
    if len(calls) != 1:
        raise ActorExecutionError(f"Actor returned {len(calls)} function tool calls; use batch normalization.")
    return calls[0]


# Return the first non-empty string value for known provider fallback keys.
def _first_string_value(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# Recover only clearly completion-shaped payloads.
def _looks_like_completion_payload(payload: dict[str, Any]) -> bool:
    rationale = payload.get("completion_rationale")
    if isinstance(rationale, str) and rationale.strip():
        return True
    return any(key in payload for key in ("completion_rationale", "actor_self_confidence", "confidence", "evidence_refs"))


# Clamp provider confidence before it enters typed validation.
def coerce_provider_confidence(value: object, *, default: float) -> float:
    if isinstance(value, int | float):
        return max(0.0, min(float(value), 1.0))
    return default


# Keep malformed provider excerpts compact inside blocked outcomes.
def _truncate_excerpt(value: str, *, limit: int = 240) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."


__all__ = [
    "coerce_provider_confidence",
    "convert_json_payload_to_tool_call",
    "extract_single_function_call",
    "extract_function_calls",
    "normalize_actor_payload",
    "validate_tool_call_action",
]
