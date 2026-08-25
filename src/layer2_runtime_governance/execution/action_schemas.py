from __future__ import annotations

import json
from urllib.parse import urlparse

from layer1_intake import TaskSpec
from ..actor_contract import ActorTask
from layer3_tool_bus_external_capabilities.tool_contract import ToolName
from layer3_tool_bus_external_capabilities.tools.models import ToolBackend, ToolGatewayConfig
from layer3_tool_bus_external_capabilities.tools.registry import load_tool_gateway_config


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
    ToolName.CONTROLLED_DOCUMENT_READ: (
        "request_controlled_document",
        (
            "Read one configured controlled document by its documented ID. "
            "Use only when the task explicitly names a controlled corpus document."
        ),
        {
            "summary": {"type": "string"},
            "reason": {"type": "string"},
            "document_id": {"type": "string"},
        },
        ("summary", "reason", "document_id"),
    ),
    ToolName.CONTROLLED_DATASET_READ: (
        "request_controlled_dataset",
        (
            "Inspect one configured controlled CSV dataset by its documented ID. "
            "The result contains a bounded raw snapshot, not arbitrary database access."
        ),
        {
            "summary": {"type": "string"},
            "reason": {"type": "string"},
            "dataset_id": {"type": "string"},
            "max_rows": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        ("summary", "reason", "dataset_id"),
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
    filtered = list(runtime_tools)

    # Anti-spin filter: suppress the capability family just executed when its observations
    # are already in context, nudging the actor to process results before repeating.
    if actor_task is not None and actor_task.last_tool_result is not None:
        last_name = actor_task.last_tool_result.request.tool_name
        action_name = _CAPABILITY_ACTIONS.get(last_name, (None,))[0]
        if action_name is not None:
            filtered = [tool for tool in filtered if tool.get("name") != action_name]

    # Evidence burden gate: when the current step declares an evidence burden that is not
    # yet satisfied by the available observations, remove completion actions so the actor
    # cannot converge prematurely. The safety valve re-enables completions when the
    # governance system has already issued multiple evidence requests without progress
    # (evidence_request_episode_count >= _EVIDENCE_GATE_SAFETY_VALVE_THRESHOLD),
    # indicating that additional tool calls are unlikely to close the gap.
    if actor_task is not None and _evidence_burden_unsatisfied(actor_task):
        completion_actions = frozenset({
            "emit_step_completed",
            "emit_external_communication_draft",
            "emit_database_statement",
        })
        filtered = [tool for tool in filtered if tool.get("name") not in completion_actions]

    return filtered


# Threshold after which the evidence burden gate re-enables completions so the actor
# can declare best-effort output instead of spinning on unreachable evidence.
_EVIDENCE_GATE_SAFETY_VALVE_THRESHOLD = 3


def _evidence_burden_unsatisfied(actor_task: ActorTask) -> bool:
    """True when the current step's evidence burden is not yet met by available observations.

    The gate counts distinct source origins across *all* observations that carry
    retrieved page content (not search-result titles), matching the evidence service's
    independent-source counting logic.

    When the actor has already been sent back for more evidence at least
    _EVIDENCE_GATE_SAFETY_VALVE_THRESHOLD times in the current episode without
    satisfying the burden, the gate opens so the actor can declare best-effort
    completion rather than spinning indefinitely.
    """
    step = actor_task.current_plan_step
    if not step.produces_factual_claims or step.evidence_burden is None:
        return False
    required = step.evidence_burden.min_independent_sources
    if required <= 0:
        return False

    # Safety valve: after repeated governance-directed evidence requests without
    # progress, allow the actor to converge with an explicit best-effort declaration.
    if actor_task.evidence_request_episode_count >= _EVIDENCE_GATE_SAFETY_VALVE_THRESHOLD:
        return False

    # Count distinct source origins from observations that carry retrieved document
    # content, excluding search-result title stubs that lack factual support.
    origins: set[str] = set()
    for obs in actor_task.observations:
        if obs.locator is None:
            continue
        # Search-result title stubs are not retrieved content.
        if obs.content.startswith("Search result title:"):
            continue
        origin = _extract_observation_origin(obs.locator)
        if origin and origin != "unknown_origin":
            origins.add(origin)

    return len(origins) < required


def _extract_observation_origin(locator: str) -> str:
    """Extract a normalized source origin from an observation locator.

    Mirrors the logic in ``evidence_service._extract_source_origin`` so the gate
    uses the same origin equivalence as governance evaluation.
    """
    if not locator:
        return "unknown_origin"
    parsed = urlparse(locator)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc.lower()}"
    parts = locator.split(":", 1)
    if len(parts) == 2:
        return f"{parts[0].lower()}:{parts[1].split('/')[0].lower()}"
    return locator.lower().strip()


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


# Evidence selection stays bounded so completions cite direct support, not bulk dumps.
def _evidence_refs() -> dict[str, object]:
    return {
        "type": "array",
        "items": _string(),
        "maxItems": 15,
        "description": (
            "The smallest set of observation IDs (at most 15) that directly supports the claims "
            "in this output. Never select observations in bulk. Search-result titles or short snippets "
            "do not support detailed factual claims unless explicitly contained in the title/snippet. "
            "Prefer fetched documents and authoritative source content."
        ),
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
            "evidence_refs": _evidence_refs(),
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
            "evidence_refs": _evidence_refs(),
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
            "evidence_refs": _evidence_refs(),
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


# Return the bounded confidence property shape.
def _confidence() -> dict[str, object]:
    return {"type": "number", "minimum": 0, "maximum": 1}


__all__ = [
    "build_actor_runtime_tools",
    "build_json_actor_output_shape",
    "select_actor_runtime_tools_for_turn",
]
