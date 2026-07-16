from __future__ import annotations

import json


# Build the runtime actions bound to actor providers for one execution turn.
def build_actor_runtime_tools() -> list[dict[str, object]]:
    return [
        _action(
            "request_web_search",
            "Request a read-only web search for external source discovery.",
            {
                "summary": _string(),
                "reason": _string(),
                "query": _string(),
                "max_results": _integer(1, 10),
                "extractive_goal": _string(),
            },
            ("summary", "reason", "query"),
        ),
        _action(
            "request_fetch_url_text",
            "Request a read-only fetch of a concrete URL for source extraction.",
            {
                "summary": _string(),
                "reason": _string(),
                "url": _string(),
                "extractive_goal": _string(),
            },
            ("summary", "reason", "url"),
        ),
        _action(
            "request_mcp_echo",
            "Request the configured read-only MCP echo tool.",
            {"summary": _string(), "reason": _string(), "text": _string()},
            ("summary", "reason", "text"),
        ),
        _action(
            "request_mcp_read_fixture",
            "Request a read-only configured MCP fixture.",
            {"summary": _string(), "reason": _string(), "path": _string()},
            ("summary", "reason", "path"),
        ),
        _action(
            "request_mcp_text_stats",
            "Request read-only text statistics through MCP.",
            {
                "summary": _string(),
                "reason": _string(),
                "text": _string(),
                "limit": _integer(1, 20),
            },
            ("summary", "reason", "text"),
        ),
        _action(
            "request_mcp_calculate_expression",
            "Request safe arithmetic calculation through MCP.",
            {"summary": _string(), "reason": _string(), "expression": _string()},
            ("summary", "reason", "expression"),
        ),
        _action(
            "emit_intermediate_output",
            "Emit grounded progress without completing the current step.",
            {
                "summary": _string(),
                "output_text": _string(),
                "progress_note": _string(),
                "residual_uncertainty": _string(),
                "confidence": _confidence(),
            },
            ("summary", "output_text", "progress_note", "confidence"),
        ),
        _completion_action(
            "emit_step_completed",
            "Complete the current step after typed self-validation.",
        ),
        _completion_action(
            "emit_final_output",
            "Emit the final answer after typed self-validation.",
        ),
        _blocking_action(
            "emit_ask_clarification",
            "Ask for clarification when missing information blocks safe progress.",
        ),
        _blocking_action(
            "emit_blocked",
            "Signal that the current step cannot proceed safely.",
        ),
        _blocking_action(
            "emit_needs_replan",
            "Signal that the current semantic trajectory requires replanning.",
        ),
    ]


# Build the JSON envelope described to providers without native tool calling.
def build_json_actor_output_shape() -> str:
    return json.dumps(
        {
            "tool_name": "one of the allowed runtime action names",
            "arguments": {
                "summary": "short audit-safe summary",
                "reason": "required for request_* tool actions",
                "output_text": "required for emit_* output actions",
                "confidence": "number from 0.0 to 1.0 when validating output",
            },
            "allowed_tool_names": [str(tool["name"]) for tool in build_actor_runtime_tools()],
        },
        indent=2,
        ensure_ascii=False,
    )


# Build one provider-neutral function action schema.
def _action(
    name: str,
    description: str,
    properties: dict[str, object],
    required: tuple[str, ...],
) -> dict[str, object]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
    }


# Build one validated completion action with the shared completion contract.
def _completion_action(name: str, description: str) -> dict[str, object]:
    return _action(
        name,
        description,
        {
            "summary": _string(),
            "output_text": _string(),
            "completion_rationale": _string(),
            "objective_satisfied": {"type": "boolean"},
            "evidence_sufficient": {"type": "boolean"},
            "confidence": _confidence(),
            "residual_uncertainty": _string(),
        },
        (
            "summary",
            "output_text",
            "completion_rationale",
            "objective_satisfied",
            "evidence_sufficient",
            "confidence",
        ),
    )


# Build one blocking/recovery action with a required blocking reason.
def _blocking_action(name: str, description: str) -> dict[str, object]:
    return _action(
        name,
        description,
        {"summary": _string(), "blocking_reason": _string()},
        ("summary", "blocking_reason"),
    )


# Return the common JSON string property shape.
def _string() -> dict[str, object]:
    return {"type": "string"}


# Return a bounded integer property shape.
def _integer(minimum: int, maximum: int) -> dict[str, object]:
    return {"type": "integer", "minimum": minimum, "maximum": maximum}


# Return the bounded confidence property shape.
def _confidence() -> dict[str, object]:
    return {"type": "number", "minimum": 0, "maximum": 1}


__all__ = ["build_actor_runtime_tools", "build_json_actor_output_shape"]
