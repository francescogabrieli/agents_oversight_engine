
from dataclasses import asdict
from typing import Any


from .serialization import to_json, to_jsonable


from .models import Layer2RuntimeTrace, TraceToolCall


# Read-only observability component extracted from the trace facade.
def layer2_trace_to_dict(trace: Layer2RuntimeTrace) -> dict[str, Any]:
    return to_jsonable(trace)


# Read-only observability component extracted from the trace facade.
def layer2_trace_to_json(trace: Layer2RuntimeTrace) -> str:
    return to_json(trace)


# Read-only observability component extracted from the trace facade.
def build_compact_trace_from_runtime_trace(trace: Layer2RuntimeTrace) -> dict[str, Any]:
    return {
        "runtime": {
            "thread_id": trace.runtime.thread_id,
            "demo_intake_used": trace.intake.parser_name == "demo_intake_builder",
        },
        "intake_summary": {
            "goal": trace.intake.goal,
            "domain_type": trace.intake.domain_type,
            "sensitivity": trace.intake.sensitivity,
            "parse_status": trace.intake.parse_status,
            "read_only_mode": trace.intake.read_only_mode,
            "execution_intent": trace.intake.execution_intent,
        },
        "planning_snapshot": {
            "plan_step_count": len(trace.plan_steps),
            "current_plan_step_index": trace.current_plan_step_index,
            "current_plan_step": _current_step_objective(trace),
            "planner_name": trace.planner_name,
            "planning_source": trace.planning_source,
            "planner_prompt_version": trace.planner_prompt_version,
            "plan_steps": [asdict(step) for step in trace.plan_steps],
        },
        "actor_snapshot": asdict(trace.actor),
        "execution_snapshot": asdict(trace.execution),
        "governance_snapshot": {
            "governance_pass_count": len(trace.governance_passes),
            "last_governance_pass": (
                asdict(trace.governance_passes[-1]) if trace.governance_passes else None
            ),
        },
        "human_review_snapshot": asdict(trace.human_review),
        "external_communication_snapshot": (
            asdict(trace.external_communication)
            if trace.external_communication is not None
            else None
        ),
        "external_execution_snapshot": asdict(trace.external_execution),
        "bootstrap_state": {
            "current_state": trace.runtime.current_state,
            "step_count": len(trace.runtime.step_history),
            "bootstrap_completed": trace.runtime.bootstrap_completed,
            "runtime_flags": list(trace.runtime.runtime_flags),
        },
        "tool_snapshot": {
            "actor_turn_count": trace.runtime.actor_turn_count,
            "tool_call_count": trace.runtime.tool_call_count,
            "current_tool_request": _compact_current_tool_request(trace.tool_calls),
            "last_tool_result": _compact_last_tool_result(trace.tool_calls),
            "observed_evidence_count": len(trace.observed_evidence),
        },
    }


# Read-only observability component extracted from the trace facade.
def _current_step_objective(trace: Layer2RuntimeTrace) -> str | None:
    if trace.current_plan_step_id is None:
        return None
    for step in trace.plan_steps:
        if step.step_id == trace.current_plan_step_id:
            return step.objective
    return None


# Read-only observability component extracted from the trace facade.
def _compact_current_tool_request(tool_calls: tuple[TraceToolCall, ...]) -> dict[str, Any] | None:
    if not tool_calls:
        return None

    # Only a trace entry with success=None represents a still-pending request.
    if tool_calls[-1].success is not None:
        return None

    call = tool_calls[-1]
    return {
        "tool_name": call.tool_name,
        "reason": call.reason,
        "query": call.query,
        "url": call.url,
        "text": call.text,
        "path": call.path,
        "expression": call.expression,
        "timezone": call.timezone,
        "limit": call.limit,
        "max_results": call.max_results,
        "extractive_goal": call.extractive_goal,
    }


# Read-only observability component extracted from the trace facade.
def _compact_last_tool_result(tool_calls: tuple[TraceToolCall, ...]) -> dict[str, Any] | None:
    completed_calls = [call for call in tool_calls if call.success is not None]
    if not completed_calls:
        return None
    call = completed_calls[-1]
    return {
        "tool_name": call.tool_name,
        "success": call.success,
        "summary": call.summary,
        "error_message": call.error_message,
        "source_count": len(call.sources),
    }


__all__ = [
    "layer2_trace_to_dict",
    "layer2_trace_to_json",
    "build_compact_trace_from_runtime_trace",
    "_current_step_objective",
    "_compact_current_tool_request",
    "_compact_last_tool_result"
]
