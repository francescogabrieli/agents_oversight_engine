from __future__ import annotations

import json
import re

from ..actor_contract import ActorResult, ActorResultType, ActorTask, CapabilityAccessMode
from ..domain import ScratchpadEntry, ScratchpadEntryType, StepValidation, StepValidationDecision
from ..tool_contract import ToolName, ToolRequest
from .errors import ActorExecutionError
from .evidence_guard import (
    guard_completion_against_unsupported_precision as _guard_completion_against_unsupported_precision,
)

# Convert empty provider responses into a typed blocked actor result instead of crashing the graph.
def build_provider_blocked_result(*, actor_task: ActorTask, detail: str) -> ActorResult:
    blocking_reason = f"Actor provider returned no usable runtime action: {detail}"
    return ActorResult(
        plan_step_id=actor_task.current_plan_step.step_id,
        result_type=ActorResultType.BLOCKED,
        summary="Actor provider returned no usable runtime action.",
        capability_mode=CapabilityAccessMode.NONE,
        blocking_reason=blocking_reason,
        scratchpad_entry=_build_scratchpad_entry(
            actor_task=actor_task,
            entry_type=ScratchpadEntryType.UNCERTAINTY,
            summary=blocking_reason,
        ),
    )


# Route malformed provider action envelopes into the existing recovery branch, not a graph crash.
def build_provider_replan_result(*, actor_task: ActorTask, detail: str) -> ActorResult:
    blocking_reason = f"Actor provider emitted an unusable runtime action: {detail}"
    return ActorResult(
        plan_step_id=actor_task.current_plan_step.step_id,
        result_type=ActorResultType.NEEDS_REPLAN,
        summary="Actor provider emitted an incomplete runtime action.",
        capability_mode=CapabilityAccessMode.NONE,
        blocking_reason=blocking_reason,
        scratchpad_entry=_build_scratchpad_entry(
            actor_task=actor_task,
            entry_type=ScratchpadEntryType.UNCERTAINTY,
            summary=blocking_reason,
        ),
    )


# Convert one function tool call into the stable ActorResult domain model.
def convert_tool_call_to_actor_result(*, actor_task: ActorTask, tool_call) -> ActorResult:
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
        _require_arguments(arguments, "output_text")
        completion_arguments = _ensure_completion_arguments(arguments=arguments)
        completion_arguments["summary"] = _sanitize_completion_text(
            str(completion_arguments.get("summary", ""))
        )
        completion_arguments["output_text"] = _sanitize_completion_text(
            str(completion_arguments.get("output_text", ""))
        )
        completion_arguments["completion_rationale"] = _sanitize_completion_text(
            str(completion_arguments.get("completion_rationale", ""))
        )
        completion_arguments = _guard_completion_against_unsupported_precision(
            actor_task=actor_task,
            arguments=completion_arguments,
        )
        next_action = _resolve_completion_next_action(arguments=completion_arguments)
        # Step completion is represented as local output plus positive validation.
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary=completion_arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            output_text=completion_arguments["output_text"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                summary=completion_arguments["completion_rationale"],
            ),
            step_validation=_build_step_validation(
                actor_task=actor_task,
                arguments=completion_arguments,
                next_action=next_action,
                rationale_key="completion_rationale",
            ),
        )

    if tool_name == "emit_final_output":
        _require_arguments(arguments, "output_text")
        completion_arguments = _ensure_completion_arguments(arguments=arguments)
        completion_arguments["summary"] = _sanitize_completion_text(
            str(completion_arguments.get("summary", ""))
        )
        completion_arguments["output_text"] = _sanitize_completion_text(
            str(completion_arguments.get("output_text", ""))
        )
        completion_arguments["completion_rationale"] = _sanitize_completion_text(
            str(completion_arguments.get("completion_rationale", ""))
        )
        completion_arguments = _guard_completion_against_unsupported_precision(
            actor_task=actor_task,
            arguments=completion_arguments,
        )
        next_action = _resolve_completion_next_action(arguments=completion_arguments)
        # Final output is still subject to the same validation contract as normal steps.
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.FINAL_OUTPUT,
            summary=completion_arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            output_text=completion_arguments["output_text"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                summary=completion_arguments["completion_rationale"],
            ),
            step_validation=_build_step_validation(
                actor_task=actor_task,
                arguments=completion_arguments,
                next_action=next_action,
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
        recovered_result = _recover_false_blocked_lookup_as_tool_request(
            actor_task=actor_task,
            arguments=arguments,
            plan_step_id=plan_step_id,
        )
        if recovered_result is not None:
            return recovered_result
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


# Recover model confusion between an empty allowlist and unavailable read-only tools.
def _recover_false_blocked_lookup_as_tool_request(
    *,
    actor_task: ActorTask,
    arguments: dict[str, object],
    plan_step_id: str,
) -> ActorResult | None:
    blocking_reason = str(arguments["blocking_reason"])
    if not _blocked_lookup_is_recoverable(actor_task=actor_task, blocking_reason=blocking_reason):
        return None

    query = _resolve_search_query(actor_task=actor_task, arguments={})
    summary = "Recover blocked source lookup as a read-only web search."
    return ActorResult(
        plan_step_id=plan_step_id,
        result_type=ActorResultType.PROPOSE_ACTION,
        summary=summary,
        capability_mode=CapabilityAccessMode.READ_ONLY,
        external_impact=False,
        tool_request=ToolRequest(
            plan_step_id=plan_step_id,
            tool_name=ToolName.WEB_SEARCH,
            reason=(
                "The actor incorrectly treated runtime-bound read-only tools as unavailable; "
                "the current step requires discovering official sources."
            ),
            query=query,
            max_results=5,
            extractive_goal="Identify official source pages and high-level source evidence for the current step.",
        ),
        scratchpad_entry=_build_scratchpad_entry(
            actor_task=actor_task,
            entry_type=ScratchpadEntryType.TOOL_DECISION,
            summary=summary,
        ),
    )


# Identify false blocked outcomes where a source lookup should have been requested.
def _blocked_lookup_is_recoverable(*, actor_task: ActorTask, blocking_reason: str) -> bool:
    reason_text = blocking_reason.lower()
    blocked_tool_markers = (
        "no external tool",
        "external tool usage is allowed",
        "external lookup",
        "no prior evidence",
        "without external",
        "cannot identify official",
    )
    if not any(marker in reason_text for marker in blocked_tool_markers):
        return False

    step_text = " ".join(
        part
        for part in (
            actor_task.current_plan_step.objective,
            actor_task.current_plan_step.expected_outcome,
            actor_task.current_plan_step.rationale,
        )
        if part
    ).lower()
    source_need_markers = ("source", "sources", "official", "lookup", "evidence", "references")
    return any(marker in step_text for marker in source_need_markers)


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


# Let runtime guards downgrade optimistic completions into explicit evidence-seeking actions.
def _resolve_completion_next_action(*, arguments: dict[str, object]) -> StepValidationDecision:
    raw_next_action = arguments.get("_guarded_next_action")
    if isinstance(raw_next_action, str):
        try:
            return StepValidationDecision(raw_next_action)
        except ValueError:
            pass
    return StepValidationDecision.COMPLETE_STEP


# Validate that required tool arguments are present and non-empty.
def _require_arguments(arguments: dict[str, object], *required_keys: str) -> None:
    for key in required_keys:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ActorExecutionError(f"Actor tool call is missing required string argument '{key}'.")


# Preserve the internal completion contract when provider tool arguments are incomplete.
def _ensure_completion_arguments(*, arguments: dict[str, object]) -> dict[str, object]:
    normalized_arguments = dict(arguments)
    summary = normalized_arguments.get("summary")
    output_text = normalized_arguments.get("output_text")
    rationale = normalized_arguments.get("completion_rationale")

    # Completion summary is audit-facing, so derive it from rationale before user output.
    if not isinstance(summary, str) or not summary.strip():
        if isinstance(rationale, str) and rationale.strip():
            normalized_arguments["summary"] = rationale
        elif isinstance(output_text, str) and output_text.strip():
            normalized_arguments["summary"] = output_text

    summary = normalized_arguments.get("summary")
    if isinstance(rationale, str) and rationale.strip():
        return normalized_arguments

    # Prefer the normalized summary because it is already intended as an audit-safe explanation.
    if isinstance(summary, str) and summary.strip():
        normalized_arguments["completion_rationale"] = summary
        return normalized_arguments

    if isinstance(output_text, str) and output_text.strip():
        normalized_arguments["completion_rationale"] = output_text
        return normalized_arguments

    return normalized_arguments


# Strip internal trace markers so user-facing completions stay clean.
def _sanitize_completion_text(text: str) -> str:
    cleaned = re.sub(r"【[^】]+】", "", text)
    return " ".join(cleaned.split())


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



__all__ = [
    "build_provider_blocked_result",
    "build_provider_replan_result",
    "convert_tool_call_to_actor_result",
]
