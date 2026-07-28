"""Normalize current and legacy trace shapes into evaluation-only contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import (
    NormalizedActor,
    NormalizedExecution,
    NormalizedGovernanceDecision,
    NormalizedHumanReview,
    NormalizedIntake,
    NormalizedObservation,
    NormalizedOversightAssessment,
    NormalizedOversightSignal,
    NormalizedPlanStep,
    NormalizedRuntime,
    NormalizedToolCall,
    NormalizedTracePayload,
    TraceEvaluationError,
)


# Validate one JSON payload and normalize it into a compact trace analysis shape.
def normalize_trace_payload(payload: Any, *, source_path: Path) -> NormalizedTracePayload:
    if not isinstance(payload, dict):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file is not a JSON object: {source_path}",
        )

    runtime = _require_dict(payload, "runtime", source_path=source_path)
    intake = _require_dict(payload, "intake", source_path=source_path)
    actor = _require_dict(payload, "actor", source_path=source_path)
    execution = _require_dict(payload, "execution", source_path=source_path)

    return NormalizedTracePayload(
        source_path=str(source_path),
        runtime=_normalize_runtime(runtime, source_path=source_path),
        intake=_normalize_intake(intake, source_path=source_path),
        actor=_normalize_actor(actor, source_path=source_path),
        execution=_normalize_execution(execution, source_path=source_path),
        plan_steps=_normalize_plan_steps(payload.get("plan_steps"), source_path=source_path),
        current_plan_step_id=_optional_text(payload.get("current_plan_step_id")),
        tool_calls=_normalize_tool_calls(payload.get("tool_calls"), source_path=source_path),
        observations=_normalize_observations(
            payload.get("observations", payload.get("observed_evidence")),
            source_path=source_path,
        ),
        governance_decisions=_normalize_governance_decisions(
            payload.get("governance_passes", payload.get("governance_decisions")),
            source_path=source_path,
        ),
        oversight_assessments=_normalize_oversight_assessments(
            _oversight_payload(payload), source_path=source_path
        ),
        human_review=_normalize_human_review(payload.get("human_review"), source_path=source_path),
    )


# Validate a required dict section.
def _require_dict(payload: dict[str, Any], key: str, *, source_path: Path) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file is missing a valid '{key}' object: {source_path}",
        )
    return value


# Validate a required string field.
def _require_str(payload: dict[str, Any], key: str, *, source_path: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file is missing a valid '{key}' string: {source_path}",
        )
    return value


# Normalize the runtime section.
def _normalize_runtime(payload: dict[str, Any], *, source_path: Path) -> NormalizedRuntime:
    return NormalizedRuntime(
        thread_id=_require_str(payload, "thread_id", source_path=source_path),
        current_state=_require_str(payload, "current_state", source_path=source_path),
        bootstrap_completed=bool(payload.get("bootstrap_completed", False)),
        runtime_flags=_string_tuple(payload.get("runtime_flags")),
        actor_turn_count=int(payload.get("actor_turn_count", 0)),
        tool_call_count=int(payload.get("tool_call_count", 0)),
    )


# Normalize the intake section.
def _normalize_intake(payload: dict[str, Any], *, source_path: Path) -> NormalizedIntake:
    return NormalizedIntake(
        goal=_require_str(payload, "goal", source_path=source_path),
        context=str(payload.get("context", "")),
        sensitivity=_require_str(payload, "sensitivity", source_path=source_path),
        success_criteria=_string_tuple(payload.get("success_criteria")),
        constraints=_string_tuple(payload.get("constraints")),
        parse_status=_require_str(payload, "parse_status", source_path=source_path),
        parser_name=_require_str(payload, "parser_name", source_path=source_path),
        prompt_version=_require_str(payload, "prompt_version", source_path=source_path),
        schema_version=_require_str(payload, "schema_version", source_path=source_path),
        policy_profile_name=_require_str(payload, "policy_profile_name", source_path=source_path),
        confidence_threshold=float(payload.get("confidence_threshold", 0.0)),
        max_steps=_optional_int(payload.get("max_steps")),
        max_tool_calls=_optional_int(payload.get("max_tool_calls")),
        read_only_mode=bool(payload.get("read_only_mode", False)),
    )


# Normalize the actor section.
def _normalize_actor(payload: dict[str, Any], *, source_path: Path) -> NormalizedActor:
    result_type = payload.get("result_type")
    if result_type is not None and not isinstance(result_type, str):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid actor.result_type: {source_path}",
        )
    return NormalizedActor(result_type=result_type)


# Normalize the execution section.
def _normalize_execution(payload: dict[str, Any], *, source_path: Path) -> NormalizedExecution:
    final_output_text = payload.get("final_output_text")
    if final_output_text is not None and not isinstance(final_output_text, str):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid execution.final_output_text: {source_path}",
        )
    return NormalizedExecution(final_output_text=final_output_text)


# Normalize retained tool calls.
def _normalize_tool_calls(payload: Any, *, source_path: Path) -> tuple[NormalizedToolCall, ...]:
    items = _normalize_optional_list(payload, source_path=source_path, field_name="tool_calls")
    return tuple(
        NormalizedToolCall(
            tool_name=_require_str(item, "tool_name", source_path=source_path),
            success=_optional_bool(item.get("success")),
            summary=_optional_text(item.get("summary")),
            error_message=_optional_text(item.get("error_message")),
        )
        for item in items
    )


# Normalize current observations and legacy evidence at the offline boundary.
def _normalize_observations(
    payload: Any, *, source_path: Path
) -> tuple[NormalizedObservation, ...]:
    items = _normalize_optional_list(
        payload, source_path=source_path, field_name="observations"
    )
    return tuple(
        NormalizedObservation(
            observation_id=_observation_id(item, index=index),
            content=_observation_content(item),
            locator=_optional_text(item.get("locator")),
            legacy_content_state=_optional_text(item.get("content_state")),
        )
        for index, item in enumerate(items, start=1)
    )


# Recover a stable identifier from current or legacy trace entries.
def _observation_id(item: dict[str, Any], *, index: int) -> str:
    return str(
        item.get("observation_id")
        or item.get("evidence_id")
        or f"legacy_observation_{index}"
    )


# Recover concrete content from current observations or legacy excerpts.
def _observation_content(item: dict[str, Any]) -> str:
    return str(item.get("content") or item.get("excerpt") or item.get("snippet") or "")


# Normalize retained plan-step metadata used for evidence requirement analysis.
def _normalize_plan_steps(payload: Any, *, source_path: Path) -> tuple[NormalizedPlanStep, ...]:
    items = _normalize_optional_list(payload, source_path=source_path, field_name="plan_steps")
    return tuple(
        NormalizedPlanStep(
            step_id=_require_str(item, "step_id", source_path=source_path),
            legacy_requirement_source=_optional_text(
                item.get("resolved_required_evidence_support_source")
            ),
            legacy_required_support=_optional_text(
                item.get("resolved_required_evidence_support_level")
            ),
        )
        for item in items
    )


# Normalize governance decisions from either the v3 or legacy root name.
def _normalize_governance_decisions(
    payload: Any, *, source_path: Path
) -> tuple[NormalizedGovernanceDecision, ...]:
    items = _normalize_optional_list(
        payload, source_path=source_path, field_name="governance_decisions"
    )
    return tuple(
        NormalizedGovernanceDecision(
            directive=_require_str(item, "directive", source_path=source_path)
        )
        for item in items
    )


# Project v3 OPA pillar results into the aggregate score view used by thesis metrics.
def _oversight_payload(payload: dict[str, Any]) -> Any:
    governance_passes = payload.get("governance_passes")
    if isinstance(governance_passes, list):
        projected: list[dict[str, Any]] = []
        for item in governance_passes:
            if not isinstance(item, dict):
                continue
            pillars = item.get("pillar_results")
            if not isinstance(pillars, dict):
                continue
            projected.append(
                {
                    "evidence_quality": pillars.get("evidence", {}),
                    "operational_risk": pillars.get("risk", {}),
                    "trajectory_health": pillars.get("trajectory", {}),
                }
            )
        return projected
    return payload.get("dynamic_policy_assessments", payload.get("oversight_assessments"))


# Normalize oversight assessments.
def _normalize_oversight_assessments(
    payload: Any, *, source_path: Path
) -> tuple[NormalizedOversightAssessment, ...]:
    items = _normalize_optional_list(
        payload, source_path=source_path, field_name="oversight_assessments"
    )
    return tuple(
        NormalizedOversightAssessment(
            evidence_quality=_normalize_signal(
                _require_dict(item, "evidence_quality", source_path=source_path),
                source_path=source_path,
            ),
            operational_risk=_normalize_signal(
                _require_dict(item, "operational_risk", source_path=source_path),
                source_path=source_path,
            ),
            trajectory_health=_normalize_signal(
                _require_dict(item, "trajectory_health", source_path=source_path),
                source_path=source_path,
            ),
        )
        for item in items
    )


# Normalize the human review section.
def _normalize_human_review(payload: Any, *, source_path: Path) -> NormalizedHumanReview:
    if payload is None:
        return NormalizedHumanReview(
            pending_request=None, decision_history=(), last_decision=None, has_activity=False
        )
    if not isinstance(payload, dict):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid human_review section: {source_path}",
        )

    pending_request = payload.get("pending_request")
    if pending_request is not None and not isinstance(pending_request, dict):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid human_review.pending_request: {source_path}",
        )
    last_decision = payload.get("last_decision")
    if last_decision is not None and not isinstance(last_decision, dict):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid human_review.last_decision: {source_path}",
        )

    decision_history_items = tuple(
        _normalize_optional_list(
            payload.get("decision_history"),
            source_path=source_path,
            field_name="human_review.decision_history",
        )
    )
    return NormalizedHumanReview(
        pending_request=pending_request,
        decision_history=decision_history_items,
        last_decision=last_decision,
        has_activity=bool(pending_request or last_decision or decision_history_items),
    )


# Normalize an oversight score signal.
def _normalize_signal(
    payload: dict[str, Any], *, source_path: Path
) -> NormalizedOversightSignal:
    score = payload.get("score")
    if not isinstance(score, (int, float)):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid oversight score field: {source_path}",
        )
    return NormalizedOversightSignal(
        score=float(score), level=_require_str(payload, "level", source_path=source_path)
    )


# Return a tuple of strings from a JSON list.
def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


# Return an optional integer without raising on null.
def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


# Return an optional boolean without raising on null.
def _optional_bool(value: Any) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    return None


# Return an optional string without raising on null.
def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return None


# Normalize an optional list, treating missing sections as empty histories.
def _normalize_optional_list(
    payload: Any, *, source_path: Path, field_name: str
) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise TraceEvaluationError(
            issue_type="schema_invalid",
            message=f"Trace file has invalid '{field_name}' list: {source_path}",
        )
    normalized: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise TraceEvaluationError(
                issue_type="schema_invalid",
                message=f"Trace file has invalid item in '{field_name}': {source_path}",
            )
        normalized.append(item)
    return normalized


__all__ = ["normalize_trace_payload"]
