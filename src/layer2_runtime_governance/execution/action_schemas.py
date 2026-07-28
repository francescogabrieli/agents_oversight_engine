from __future__ import annotations

import json

from layer1_intake import TaskSpec
from ..actor_contract import ActorTask
from ..tool_contract import ToolName
from ..tools.models import ToolBackend, ToolGatewayConfig
from ..tools.registry import load_tool_gateway_config


_CAPABILITY_ACTIONS: dict[ToolName, tuple[str, str, dict[str, object], tuple[str, ...]]] = {
    ToolName.WEB_SEARCH: (
        "request_web_search",
        "Request a read-only web search for external source discovery.",
        {"summary": {"type": "string"}, "reason": {"type": "string"}, "query": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 10}, "extractive_goal": {"type": "string"}},
        ("summary", "reason", "query"),
    ),
    ToolName.FETCH_URL_TEXT: (
        "request_fetch_url_text",
        "Request a read-only fetch of a concrete URL for source extraction.",
        {"summary": {"type": "string"}, "reason": {"type": "string"}, "url": {"type": "string"}, "extractive_goal": {"type": "string"}},
        ("summary", "reason", "url"),
    ),
    ToolName.MCP_LOCAL_TEST_ECHO: (
        "request_mcp_echo",
        "Request the configured read-only MCP echo tool.",
        {"summary": {"type": "string"}, "reason": {"type": "string"}, "text": {"type": "string"}},
        ("summary", "reason", "text"),
    ),
    ToolName.MCP_LOCAL_TEST_READ_FIXTURE: (
        "request_mcp_read_fixture",
        "Request a read-only configured MCP fixture.",
        {"summary": {"type": "string"}, "reason": {"type": "string"}, "path": {"type": "string"}},
        ("summary", "reason", "path"),
    ),
    ToolName.MCP_LOCAL_TEST_TEXT_STATS: (
        "request_mcp_text_stats",
        "Request read-only text statistics through MCP.",
        {"summary": {"type": "string"}, "reason": {"type": "string"}, "text": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
        ("summary", "reason", "text"),
    ),
    ToolName.MCP_LOCAL_TEST_CALCULATE_EXPRESSION: (
        "request_mcp_calculate_expression",
        "Request safe arithmetic calculation through MCP.",
        {"summary": {"type": "string"}, "reason": {"type": "string"}, "expression": {"type": "string"}},
        ("summary", "reason", "expression"),
    ),
}


# Build the runtime actions bound to actor providers for one execution turn.
def build_actor_runtime_tools(
    tool_gateway_config: ToolGatewayConfig | None = None,
) -> list[dict[str, object]]:
    config = tool_gateway_config or load_tool_gateway_config()
    capability_actions = [
        _action(name, description, properties, required)
        for tool_name, (name, description, properties, required) in _CAPABILITY_ACTIONS.items()
        if _tool_is_actor_available(config=config, tool_name=tool_name)
    ]
    return capability_actions + [
        _completion_action(
            "emit_step_completed",
            "Complete the current step after typed self-validation.",
        ),
        _external_communication_draft_action(),
        _database_statement_action(),
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


# Restrict completion actions to the typed contract appropriate for the current step.
def select_actor_runtime_tools_for_turn(
    runtime_tools: list[dict[str, object]],
    *,
    task_spec: TaskSpec,
    actor_task: ActorTask | None = None,
) -> list[dict[str, object]]:
    # Keep the provider surface bounded to capabilities still actionable in this turn.
    del task_spec
    if actor_task is None or actor_task.last_tool_result is None:
        return list(runtime_tools)
    last_name = actor_task.last_tool_result.request.tool_name
    action_name = _CAPABILITY_ACTIONS.get(last_name, (None,))[0]
    if action_name is None:
        return list(runtime_tools)
    # The result and its observations are already in context; repeating the exact
    # capability family is not the default next action. The actor can still complete,
    # replan, clarify, or use another capability.
    return [tool for tool in runtime_tools if tool.get("name") != action_name]


# Build the JSON envelope described to providers without native tool calling.
def build_json_actor_output_shape(
    tool_gateway_config: ToolGatewayConfig | None = None,
) -> str:
    runtime_tools = build_actor_runtime_tools(tool_gateway_config)
    return json.dumps(
        {
            "tool_name": "one of the allowed runtime action names",
            "arguments": {
                "summary": "short audit-safe summary",
                "reason": "required for request_* tool actions",
                "output_text": "required for emit_* output actions",
                "confidence": "number from 0.0 to 1.0 when validating output",
            },
            "allowed_tool_names": [str(tool["name"]) for tool in runtime_tools],
        },
        indent=2,
        ensure_ascii=False,
    )


# Expose a capability only when its configured backend is executable by this registry.
def _tool_is_actor_available(*, config: ToolGatewayConfig, tool_name: ToolName) -> bool:
    definition = config.tools.get(tool_name)
    if definition is None or not definition.enabled:
        return False
    if definition.backend != ToolBackend.MCP:
        return True
    if not definition.mcp_server_id or not definition.mcp_tool_name:
        return False
    server = config.mcp_servers.get(definition.mcp_server_id)
    return bool(
        server
        and server.enabled
        and definition.mcp_tool_name in server.allowed_tools
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
            "actor_self_confidence": _confidence(),
            "evidence_refs": {"type": "array", "items": _string(), "maxItems": 50},
            "residual_uncertainty": _string(),
        },
        (
            "summary",
            "output_text",
            "completion_rationale",
            "actor_self_confidence",
            "evidence_refs",
        ),
    )


# Expose communication content fields without granting provider authority over policy facts.
def _external_communication_draft_action() -> dict[str, object]:
    return _action(
        "emit_external_communication_draft",
        (
            "Emit one complete external communication draft. The runtime, not the actor, "
            "derives its boundary, destination class, effect, and digest."
        ),
        {
            "summary": _string(),
            "channel": _string(),
            "recipient": _string(),
            "subject": _string(),
            "body": _string(),
            "cc": {"type": "array", "items": _string(), "maxItems": 20},
            "bcc": {"type": "array", "items": _string(), "maxItems": 20},
            "completion_rationale": _string(),
            "actor_self_confidence": _confidence(),
            "evidence_refs": {"type": "array", "items": _string(), "maxItems": 50},
            "residual_uncertainty": _string(),
        },
        (
            "summary",
            "channel",
            "recipient",
            "subject",
            "body",
            "completion_rationale",
            "actor_self_confidence",
            "evidence_refs",
        ),
    )


# Expose a profile-bound SQL proposal without granting connector or DSN authority.
def _database_statement_action() -> dict[str, object]:
    return _action(
        "emit_database_statement",
        (
            "Emit one SQL statement proposal against a registered database profile. "
            "Keep values in the separate parameters object; never concatenate them into SQL."
        ),
        {
            "summary": _string(),
            "database_profile_id": _string(),
            "statement": _string(),
            "parameters": {
                "type": "object",
                "additionalProperties": {
                    "type": ["string", "number", "integer", "boolean", "null"]
                },
            },
            "completion_rationale": _string(),
            "actor_self_confidence": _confidence(),
            "evidence_refs": {"type": "array", "items": _string(), "maxItems": 50},
            "residual_uncertainty": _string(),
        },
        (
            "summary",
            "database_profile_id",
            "statement",
            "parameters",
            "completion_rationale",
            "actor_self_confidence",
            "evidence_refs",
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


__all__ = [
    "build_actor_runtime_tools",
    "build_json_actor_output_shape",
    "select_actor_runtime_tools_for_turn",
]
