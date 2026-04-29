from __future__ import annotations

import json
import os
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path
from typing import Any

from .actor_contract import (
    ActorPort,
    ActorResult,
    ActorResultType,
    ActorTask,
    CapabilityAccessMode,
)
from .openrouter_client import (
    OpenRouterChatClient,
    OpenRouterRuntimeError,
    OpenRouterRuntimeSettings,
)
from .state import ScratchpadEntry, ScratchpadEntryType, StepValidation, StepValidationDecision
from .tool_contract import ToolName, ToolRequest

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime if dependency is missing
    OpenAI = None


@dataclass(frozen=True)
class OpenAIActorSettings:
    model: str = "gpt-5.4-mini"
    api_key: str | None = None
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "OpenAIActorSettings":
        # Merge process env and local .env files into one actor settings object.
        env_file_values = _load_env_file_values()

        return cls(
            model=os.getenv(
                "LAYER2_ACTOR_OPENAI_MODEL",
                env_file_values.get("LAYER2_ACTOR_OPENAI_MODEL", "gpt-5.4-mini"),
            ),
            api_key=os.getenv("OPENAI_API_KEY", env_file_values.get("OPENAI_API_KEY")),
            timeout_seconds=float(
                os.getenv(
                    "LAYER2_ACTOR_OPENAI_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_ACTOR_OPENAI_TIMEOUT_SECONDS", "30"),
                )
            ),
        )


@dataclass(frozen=True)
class OpenRouterActorSettings:
    # OpenRouterActorSettings selects the temporary cloud actor test model.
    model: str = "openai/gpt-oss-120b:free"
    api_key: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 120.0
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "OpenRouterActorSettings":
        # Reuse the shared OpenRouter settings while allowing actor-specific model override.
        settings = OpenRouterRuntimeSettings.from_env(
            model_env_key="LAYER2_ACTOR_OPENROUTER_MODEL"
        )
        return cls(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout_seconds=settings.timeout_seconds,
            temperature=settings.temperature,
        )


class ActorExecutionError(RuntimeError):
    """Raised when the actor cannot produce a valid execution outcome."""


# Build the constrained system prompt for Layer 2 step execution.
def build_actor_system_prompt() -> str:
    return """
You are the execution agent for layer 2 of a governed plan-and-execute runtime.
The planner has already produced the semantic plan.
Your job is to execute only the current plan step, using prior executed steps, observed evidence, and the last tool result as context.

Rules:
- Operate only on the current plan step.
- Use the full plan as context, not as a second planning task.
- Do not rewrite the plan and do not invent new plan steps.
- Work in a local ReAct-like way for the current step.
- Use the runtime-bound tools when external information is required.
- If you do not need an external tool, finish the turn by emitting exactly one structured outcome tool.
- Use emit_intermediate_output only for progress that should continue the current step.
- Use emit_step_completed or emit_final_output only after validating that the current step objective is satisfied.
- Keep the turn compact, explicit, and directly actionable by the runtime.
- Respect read_only_mode when it is true.
- Do not emit chain-of-thought.
- Do not emit concrete shell commands or raw implementation details.
- Prefer grounded local outputs when the available evidence is already sufficient.
""".strip()


# Build the user prompt from the canonical actor handoff.
def build_actor_user_prompt(*, actor_task: ActorTask) -> str:
    payload = {
        "objective": actor_task.task_spec.goal,
        "task_spec": {
            "goal": actor_task.task_spec.goal,
            "context": actor_task.task_spec.context,
            "constraints": actor_task.task_spec.constraints,
            "domain_type": actor_task.task_spec.domain_type.value,
            "domain_label": actor_task.task_spec.domain_label,
            "sensitivity": actor_task.task_spec.sensitivity.value,
            "success_criteria": actor_task.task_spec.success_criteria,
        },
        "execution_policy": {
            "policy_profile_name": actor_task.execution_policy.policy_profile_name,
            "confidence_threshold": actor_task.execution_policy.confidence_threshold,
            "read_only_mode": actor_task.execution_policy.read_only_mode,
            "allowed_tools": actor_task.execution_policy.allowed_tools,
            "disallowed_tools": actor_task.execution_policy.disallowed_tools,
            "budget": {
                "max_steps": actor_task.execution_policy.budget.max_steps,
                "max_tool_calls": actor_task.execution_policy.budget.max_tool_calls,
            },
            "tool_call_count": actor_task.tool_call_count,
            "remaining_tool_calls": actor_task.remaining_tool_calls,
        },
        "intake_metadata": {
            "parse_status": actor_task.intake_metadata.parse_status.value,
            "warnings": actor_task.intake_metadata.warnings,
            "missing_information": actor_task.intake_metadata.missing_information,
        },
        "runtime": {
            "current_state": actor_task.current_state.value,
            "runtime_flags": [flag.value for flag in actor_task.runtime_flags],
            "actor_turn_count": actor_task.actor_turn_count,
        },
        "planner": {
            "planner_name": actor_task.planner_name,
            "planner_prompt_version": actor_task.planner_prompt_version,
            "planner_rationale": actor_task.planner_rationale,
        },
        "plan": {
            "current_step_number": actor_task.current_plan_step_index + 1,
            "current_plan_step_index": actor_task.current_plan_step_index,
            "current_plan_step": {
                "step_id": actor_task.current_plan_step.step_id,
                "objective": actor_task.current_plan_step.objective,
                "rationale": actor_task.current_plan_step.rationale,
                "expected_outcome": actor_task.current_plan_step.expected_outcome,
                "status": actor_task.current_plan_step.status.value,
            },
            "plan_steps": [
                {
                    "step_id": step.step_id,
                    "objective": step.objective,
                    "rationale": step.rationale,
                    "expected_outcome": step.expected_outcome,
                    "status": step.status.value,
                }
                for step in actor_task.plan_steps
            ],
        },
        "past_steps": [
            {
                "step_id": step.step_id,
                "objective": step.objective,
                "result_type": step.result_type.value,
                "summary": step.summary,
                "output_text": step.output_text,
            }
            for step in actor_task.past_steps
        ],
        "observed_evidence": [
            {
                "evidence_id": evidence.evidence_id,
                "title": evidence.title,
                "locator": evidence.locator,
                "excerpt": evidence.excerpt,
                "source_tool": evidence.source_tool.value,
            }
            for evidence in actor_task.observed_evidence
        ],
        # Scratchpad and validations are summarized state, not chain-of-thought.
        "scratchpad": [
            {
                "turn_index": entry.turn_index,
                "plan_step_id": entry.plan_step_id,
                "entry_type": entry.entry_type.value,
                "summary": entry.summary,
                "evidence_refs": entry.evidence_refs,
            }
            for entry in actor_task.scratchpad
        ],
        "step_validations": [
            {
                "plan_step_id": validation.plan_step_id,
                "objective_satisfied": validation.objective_satisfied,
                "evidence_sufficient": validation.evidence_sufficient,
                "confidence": validation.confidence,
                "next_action": validation.next_action.value,
                "rationale_summary": validation.rationale_summary,
                "residual_uncertainty": validation.residual_uncertainty,
            }
            for validation in actor_task.step_validations
        ],
        "last_tool_result": (
            {
                "tool_name": actor_task.last_tool_result.request.tool_name.value,
                "success": actor_task.last_tool_result.success,
                "summary": actor_task.last_tool_result.summary,
                "content": actor_task.last_tool_result.content,
                "error_message": actor_task.last_tool_result.error_message,
                "sources": [
                    {
                        "title": source.title,
                        "locator": source.locator,
                        "snippet": source.snippet,
                    }
                    for source in actor_task.last_tool_result.sources
                ],
            }
            if actor_task.last_tool_result is not None
            else None
        ),
    }

    return (
        "Execute the current plan step in the governed plan-and-execute runtime.\n\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}"
    )


class OpenAIActor(ActorPort):
    actor_name = "openai_layer2_actor"
    prompt_version = "actor_v4_runtime_tool_binding"

    def __init__(self, settings: OpenAIActorSettings | None = None) -> None:
        # Validate actor prerequisites once at construction time.
        self._settings = settings or OpenAIActorSettings.from_env()

        if OpenAI is None:
            raise RuntimeError(
                "The 'openai' package is not installed. Install it before using the Layer 2 actor."
            )
        if not self._settings.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing. Set it in the process environment or in src/.env."
            )

        self._client = OpenAI(
            api_key=self._settings.api_key,
            timeout=self._settings.timeout_seconds,
        )

    # Ask OpenAI for one tool-bound actor turn and convert it into the domain result.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        # Keep the actor to one tool call so the runtime can validate a single typed outcome per turn.
        try:
            response = self._client.responses.create(
                model=self._settings.model,
                input=[
                    {
                        "role": "system",
                        "content": build_actor_system_prompt(),
                    },
                    {
                        "role": "user",
                        "content": build_actor_user_prompt(actor_task=actor_task),
                    },
                ],
                tools=_build_actor_runtime_tools(),
                tool_choice="required",
                parallel_tool_calls=False,
                max_tool_calls=1,
            )
        except Exception as exc:  # pragma: no cover - network/client errors
            raise ActorExecutionError(f"Actor request failed: {exc}") from exc

        tool_call = _extract_single_function_call(response)
        return _convert_tool_call_to_actor_result(
            actor_task=actor_task,
            tool_call=tool_call,
        )


class OpenRouterActor(ActorPort):
    # OpenRouterActor runs only the actor role on the temporary cloud test backend.
    actor_name = "openrouter_gpt_oss_120b_actor"
    prompt_version = "actor_v4_openrouter_json_runtime_tool_binding"

    def __init__(self, settings: OpenRouterActorSettings | None = None) -> None:
        # Validate OpenRouter actor settings without affecting planner/replanner providers.
        self._settings = settings or OpenRouterActorSettings.from_env()
        self._client = OpenRouterChatClient(
            OpenRouterRuntimeSettings(
                model=self._settings.model,
                api_key=self._settings.api_key,
                base_url=self._settings.base_url,
                timeout_seconds=self._settings.timeout_seconds,
                temperature=self._settings.temperature,
            )
        )

    # Ask OpenRouter for one JSON runtime action and convert it into ActorResult.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        try:
            payload = self._client.complete_json(
                system_prompt=build_actor_system_prompt(),
                user_prompt=build_actor_user_prompt(actor_task=actor_task),
                expected_shape=_build_json_actor_output_shape(),
            )
        except OpenRouterRuntimeError as exc:
            raise ActorExecutionError(f"OpenRouter actor request failed: {exc}") from exc

        tool_call = _convert_json_payload_to_tool_call(payload)
        return _convert_tool_call_to_actor_result(actor_task=actor_task, tool_call=tool_call)


# Build the bound runtime tools exposed to the actor model.
def _build_actor_runtime_tools() -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "name": "request_web_search",
            "description": (
                "Request a read-only web search when the current plan step requires discovering external sources."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                    "extractive_goal": {"type": "string"},
                },
                "required": ["summary", "reason", "query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "request_fetch_url_text",
            "description": (
                "Request a read-only fetch of a concrete URL when the current plan step needs extraction from a known source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "url": {"type": "string"},
                    "extractive_goal": {"type": "string"},
                },
                "required": ["summary", "reason", "url"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "request_mcp_echo",
            "description": "Request the configured read-only MCP echo tool for gateway connectivity checks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["summary", "reason", "text"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "request_mcp_read_fixture",
            "description": "Request a read-only MCP fixture read from the configured test fixture directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["summary", "reason", "path"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "request_mcp_text_stats",
            "description": "Request read-only text statistics through the configured MCP test server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "text": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["summary", "reason", "text"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "request_mcp_calculate_expression",
            "description": "Request safe arithmetic calculation through the configured MCP test server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "expression": {"type": "string"},
                },
                "required": ["summary", "reason", "expression"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "emit_intermediate_output",
            "description": (
                "Emit a grounded progress note when the current plan step should continue without completing yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "output_text": {"type": "string"},
                    "progress_note": {"type": "string"},
                    "residual_uncertainty": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["summary", "output_text", "progress_note", "confidence"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "emit_step_completed",
            "description": (
                "Complete the current plan step only after self-validating that the objective is satisfied."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "output_text": {"type": "string"},
                    "completion_rationale": {"type": "string"},
                    "objective_satisfied": {"type": "boolean"},
                    "evidence_sufficient": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "residual_uncertainty": {"type": "string"},
                },
                "required": [
                    "summary",
                    "output_text",
                    "completion_rationale",
                    "objective_satisfied",
                    "evidence_sufficient",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "emit_final_output",
            "description": "Emit the final answer only after self-validating that the final plan step is satisfied.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "output_text": {"type": "string"},
                    "completion_rationale": {"type": "string"},
                    "objective_satisfied": {"type": "boolean"},
                    "evidence_sufficient": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "residual_uncertainty": {"type": "string"},
                },
                "required": [
                    "summary",
                    "output_text",
                    "completion_rationale",
                    "objective_satisfied",
                    "evidence_sufficient",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "emit_ask_clarification",
            "description": "Ask for clarification when missing information blocks safe progress.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "blocking_reason": {"type": "string"},
                },
                "required": ["summary", "blocking_reason"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "emit_blocked",
            "description": "Signal that the current step cannot proceed safely from the current state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "blocking_reason": {"type": "string"},
                },
                "required": ["summary", "blocking_reason"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "emit_needs_replan",
            "description": "Signal that the current semantic step is insufficient or misaligned and requires replanning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "blocking_reason": {"type": "string"},
                },
                "required": ["summary", "blocking_reason"],
                "additionalProperties": False,
            },
        },
    ]


# Build the JSON action schema described to non-tool-calling actor models.
def _build_json_actor_output_shape() -> str:
    tool_names = [str(tool["name"]) for tool in _build_actor_runtime_tools()]
    return json.dumps(
        {
            "tool_name": "one of the allowed runtime action names",
            "arguments": {
                "summary": "short audit-safe summary",
                "reason": "required for request_* tool actions",
                "output_text": "required for emit_* output actions",
                "confidence": "number from 0.0 to 1.0 when validating output",
            },
            "allowed_tool_names": tool_names,
        },
        indent=2,
        ensure_ascii=False,
    )


# Convert JSON actor output into the same shape used by OpenAI function tool calls.
def _convert_json_payload_to_tool_call(payload: dict[str, Any]) -> SimpleNamespace:
    tool_name = payload.get("tool_name")
    arguments = payload.get("arguments")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ActorExecutionError("Actor JSON response must include string 'tool_name'.")
    if not isinstance(arguments, dict):
        raise ActorExecutionError("Actor JSON response must include object 'arguments'.")

    allowed_names = {str(tool["name"]) for tool in _build_actor_runtime_tools()}
    if tool_name not in allowed_names:
        raise ActorExecutionError(f"Actor emitted an unknown runtime action: {tool_name}")

    return SimpleNamespace(name=tool_name, arguments=json.dumps(arguments, ensure_ascii=False))


# Extract exactly one function tool call from the model response.
def _extract_single_function_call(response):
    function_calls = [
        item
        for item in getattr(response, "output", []) or []
        if getattr(item, "type", None) == "function_call"
    ]

    if len(function_calls) != 1:
        raise ActorExecutionError(
            f"Actor must emit exactly one function tool call, received {len(function_calls)}."
        )

    return function_calls[0]


# Convert one function tool call into the stable ActorResult domain model.
def _convert_tool_call_to_actor_result(*, actor_task: ActorTask, tool_call) -> ActorResult:
    tool_name = getattr(tool_call, "name", None)
    raw_arguments = getattr(tool_call, "arguments", None)

    if tool_name is None or raw_arguments is None:
        raise ActorExecutionError("Actor returned a malformed function tool call.")

    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ActorExecutionError(f"Actor returned invalid tool arguments: {exc}") from exc

    plan_step_id = actor_task.current_plan_step.step_id

    if tool_name == "request_web_search":
        _require_arguments(arguments, "summary", "reason")
        query = _resolve_search_query(actor_task=actor_task, arguments=arguments)
        # Tool-selection outcomes are converted into ToolRequest for the gateway node.
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.PROPOSE_ACTION,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.READ_ONLY,
            external_impact=False,
            tool_request=ToolRequest(
                plan_step_id=plan_step_id,
                tool_name=ToolName.WEB_SEARCH,
                reason=arguments["reason"],
                query=query,
                max_results=int(arguments.get("max_results", 5)),
                extractive_goal=arguments.get("extractive_goal"),
            ),
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.TOOL_DECISION,
                summary=arguments["summary"],
            ),
        )

    if tool_name == "request_fetch_url_text":
        _require_arguments(arguments, "summary", "reason", "url")
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.PROPOSE_ACTION,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.READ_ONLY,
            external_impact=False,
            tool_request=ToolRequest(
                plan_step_id=plan_step_id,
                tool_name=ToolName.FETCH_URL_TEXT,
                reason=arguments["reason"],
                url=arguments["url"],
                extractive_goal=arguments.get("extractive_goal"),
            ),
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.TOOL_DECISION,
                summary=arguments["summary"],
            ),
        )

    if tool_name == "request_mcp_echo":
        _require_arguments(arguments, "summary", "reason", "text")
        return _build_mcp_actor_result(
            actor_task=actor_task,
            plan_step_id=plan_step_id,
            arguments=arguments,
            tool_name=ToolName.MCP_LOCAL_TEST_ECHO,
            text=arguments["text"],
        )

    if tool_name == "request_mcp_read_fixture":
        _require_arguments(arguments, "summary", "reason", "path")
        return _build_mcp_actor_result(
            actor_task=actor_task,
            plan_step_id=plan_step_id,
            arguments=arguments,
            tool_name=ToolName.MCP_LOCAL_TEST_READ_FIXTURE,
            path=arguments["path"],
        )

    if tool_name == "request_mcp_text_stats":
        _require_arguments(arguments, "summary", "reason", "text")
        return _build_mcp_actor_result(
            actor_task=actor_task,
            plan_step_id=plan_step_id,
            arguments=arguments,
            tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
            text=arguments["text"],
            limit=int(arguments.get("limit", 5)),
        )

    if tool_name == "request_mcp_calculate_expression":
        _require_arguments(arguments, "summary", "reason", "expression")
        return _build_mcp_actor_result(
            actor_task=actor_task,
            plan_step_id=plan_step_id,
            arguments=arguments,
            tool_name=ToolName.MCP_LOCAL_TEST_CALCULATE_EXPRESSION,
            expression=arguments["expression"],
        )

    if tool_name == "emit_intermediate_output":
        _require_arguments(arguments, "summary", "output_text", "progress_note")
        # Intermediate output deliberately keeps the current step active.
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            output_text=arguments["output_text"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.PROGRESS_NOTE,
                summary=arguments["progress_note"],
            ),
            step_validation=_build_step_validation(
                actor_task=actor_task,
                arguments=arguments,
                next_action=StepValidationDecision.CONTINUE_ACTING,
                rationale_key="progress_note",
                objective_satisfied=False,
                evidence_sufficient=False,
            ),
        )

    if tool_name == "emit_step_completed":
        _require_arguments(arguments, "summary", "output_text", "completion_rationale")
        # Step completion is represented as local output plus positive validation.
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            output_text=arguments["output_text"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                summary=arguments["completion_rationale"],
            ),
            step_validation=_build_step_validation(
                actor_task=actor_task,
                arguments=arguments,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_key="completion_rationale",
            ),
        )

    if tool_name == "emit_final_output":
        _require_arguments(arguments, "summary", "output_text", "completion_rationale")
        # Final output is still subject to the same validation contract as normal steps.
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.FINAL_OUTPUT,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            output_text=arguments["output_text"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                summary=arguments["completion_rationale"],
            ),
            step_validation=_build_step_validation(
                actor_task=actor_task,
                arguments=arguments,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_key="completion_rationale",
            ),
        )

    if tool_name == "emit_ask_clarification":
        _require_arguments(arguments, "summary", "blocking_reason")
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.ASK_CLARIFICATION,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            blocking_reason=arguments["blocking_reason"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.UNCERTAINTY,
                summary=arguments["blocking_reason"],
            ),
        )

    if tool_name == "emit_blocked":
        _require_arguments(arguments, "summary", "blocking_reason")
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.BLOCKED,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            blocking_reason=arguments["blocking_reason"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.UNCERTAINTY,
                summary=arguments["blocking_reason"],
            ),
        )

    if tool_name == "emit_needs_replan":
        _require_arguments(arguments, "summary", "blocking_reason")
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.NEEDS_REPLAN,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            blocking_reason=arguments["blocking_reason"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.UNCERTAINTY,
                summary=arguments["blocking_reason"],
            ),
        )

    raise ActorExecutionError(f"Actor emitted an unknown runtime tool: {tool_name}")


# Build one read-only ActorResult for configured MCP tool requests.
def _build_mcp_actor_result(
    *,
    actor_task: ActorTask,
    plan_step_id: str,
    arguments: dict[str, object],
    tool_name: ToolName,
    text: object | None = None,
    path: object | None = None,
    expression: object | None = None,
    limit: int | None = None,
) -> ActorResult:
    return ActorResult(
        plan_step_id=plan_step_id,
        result_type=ActorResultType.PROPOSE_ACTION,
        summary=str(arguments["summary"]),
        capability_mode=CapabilityAccessMode.READ_ONLY,
        external_impact=False,
        tool_request=ToolRequest(
            plan_step_id=plan_step_id,
            tool_name=tool_name,
            reason=str(arguments["reason"]),
            text=str(text) if text is not None else None,
            path=str(path) if path is not None else None,
            expression=str(expression) if expression is not None else None,
            limit=limit,
        ),
        scratchpad_entry=_build_scratchpad_entry(
            actor_task=actor_task,
            entry_type=ScratchpadEntryType.TOOL_DECISION,
            summary=str(arguments["summary"]),
        ),
    )


# Create one audit-safe scratchpad entry for the current actor turn.
def _build_scratchpad_entry(
    *,
    actor_task: ActorTask,
    entry_type: ScratchpadEntryType,
    summary: str,
) -> ScratchpadEntry:
    return ScratchpadEntry(
        turn_index=actor_task.actor_turn_count,
        plan_step_id=actor_task.current_plan_step.step_id,
        entry_type=entry_type,
        summary=summary,
        evidence_refs=[item.evidence_id for item in actor_task.observed_evidence],
    )


# Build the compact self-validation contract used before step completion.
def _build_step_validation(
    *,
    actor_task: ActorTask,
    arguments: dict[str, object],
    next_action: StepValidationDecision,
    rationale_key: str,
    objective_satisfied: bool | None = None,
    evidence_sufficient: bool | None = None,
) -> StepValidation:
    confidence = arguments.get("confidence", 0.0)
    if not isinstance(confidence, int | float):
        raise ActorExecutionError("Actor validation confidence must be numeric.")

    # Some runtime tools fix these booleans, while completion tools read them from the model.
    resolved_objective_satisfied = (
        bool(arguments.get("objective_satisfied"))
        if objective_satisfied is None
        else objective_satisfied
    )
    resolved_evidence_sufficient = (
        bool(arguments.get("evidence_sufficient"))
        if evidence_sufficient is None
        else evidence_sufficient
    )

    rationale = arguments.get(rationale_key)
    if not isinstance(rationale, str) or not rationale.strip():
        raise ActorExecutionError(f"Actor validation is missing '{rationale_key}'.")

    residual_uncertainty = arguments.get("residual_uncertainty")
    if residual_uncertainty is not None and not isinstance(residual_uncertainty, str):
        raise ActorExecutionError("Actor validation residual_uncertainty must be a string.")

    return StepValidation(
        plan_step_id=actor_task.current_plan_step.step_id,
        objective_satisfied=resolved_objective_satisfied,
        evidence_sufficient=resolved_evidence_sufficient,
        confidence=max(0.0, min(float(confidence), 1.0)),
        next_action=next_action,
        rationale_summary=rationale,
        residual_uncertainty=residual_uncertainty,
    )


# Validate that required tool arguments are present and non-empty.
def _require_arguments(arguments: dict[str, object], *required_keys: str) -> None:
    for key in required_keys:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ActorExecutionError(f"Actor tool call is missing required string argument '{key}'.")


# Build a conservative search query if a JSON actor omits the explicit query field.
def _resolve_search_query(*, actor_task: ActorTask, arguments: dict[str, object]) -> str:
    query = arguments.get("query")
    if isinstance(query, str) and query.strip():
        return query

    # Prefer the current step objective because the actor is scoped to that semantic step.
    fallback_parts = [
        actor_task.current_plan_step.objective,
        actor_task.task_spec.context,
        "official sources",
    ]
    return " ".join(part.strip() for part in fallback_parts if part and part.strip())


# Build the production actor used by the live Layer 2 runtime.
def build_default_actor() -> ActorPort:
    provider = _resolve_actor_provider()
    if provider == "openai":
        return OpenAIActor()
    if provider == "openrouter":
        return OpenRouterActor()
    raise RuntimeError(
        "Unsupported LAYER2_ACTOR_PROVIDER. Use 'openrouter' for temporary actor tests or 'openai'."
    )


# Resolve only the actor provider so planner and replanner can stay on OpenAI.
def _resolve_actor_provider() -> str:
    env_file_values = _load_env_file_values()
    return os.getenv(
        "LAYER2_ACTOR_PROVIDER",
        env_file_values.get("LAYER2_ACTOR_PROVIDER", "openrouter"),
    ).strip().lower()


def _load_env_file_values() -> dict[str, str]:
    # Load simple KEY=VALUE pairs from the supported .env file locations.
    env_values: dict[str, str] = {}

    for candidate in _candidate_env_paths():
        if not candidate.exists():
            continue

        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key:
                env_values[key] = value

    return env_values


def _candidate_env_paths() -> list[Path]:
    # Search both repository root and src-level env files for local development.
    repo_root = Path(__file__).resolve().parents[2]
    return [
        repo_root / ".env",
        repo_root / "src" / ".env",
    ]


__all__ = [
    "ActorExecutionError",
    "OpenAIActor",
    "OpenAIActorSettings",
    "OpenRouterActor",
    "OpenRouterActorSettings",
    "build_actor_system_prompt",
    "build_actor_user_prompt",
    "build_default_actor",
]
