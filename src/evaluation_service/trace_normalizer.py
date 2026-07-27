from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


# Normalize exported trace JSON into the runtime-independent EvaluationInput contract.
def normalize_trace_json(trace_payload: dict[str, Any]) -> dict[str, Any]:
    source_trace = deepcopy(trace_payload)
    governance_decisions = _normalize_governance_passes(source_trace)
    runtime = source_trace.get("runtime", {})
    trace_version, completeness = _trace_version_and_completeness(
        trace_payload=source_trace,
        governance_decisions=governance_decisions,
    )
    normalized_trace = normalize_legacy_trace_to_v4(source_trace)
    return {
        "schema_version": "evaluation_input.v1",
        "trace": normalized_trace,
        "derived": {
            "governance_decision_count": len(governance_decisions) if isinstance(governance_decisions, list) else 0,
            "final_runtime_state": runtime.get("current_state") if isinstance(runtime, dict) else None,
            "has_fail_closed": _has_fail_closed(governance_decisions),
            "has_human_review": _has_directive(governance_decisions, "wait_human_review"),
            "has_deny_or_stop": (
                _has_directive(governance_decisions, "deny")
                or _has_directive(governance_decisions, "stop")
            ),
            "trace_version": trace_version,
            "completeness": completeness,
            "pillar_results_available": trace_version in {"v2", "v3", "v4"},
            "safety_invariants_available": trace_version in {"v2", "v3", "v4"},
        },
    }


# Project a legacy trace into the closed v4 read model without mutating its source payload.
def normalize_legacy_trace_to_v4(trace_payload: dict[str, Any]) -> dict[str, Any]:
    source = deepcopy(trace_payload)
    if source.get("trace_version") == "layer2_trace.v4":
        return _add_current_v4_compatibility_defaults(source)

    runtime = _dict_or_empty(source.get("runtime"))
    intake = _dict_or_empty(source.get("intake"))
    actor = _dict_or_empty(source.get("actor"))
    execution = _dict_or_empty(source.get("execution"))
    governance_passes = _normalize_governance_passes(source)

    return {
        "trace_version": "layer2_trace.v4",
        "runtime": {
            "thread_id": _text_or(runtime.get("thread_id"), "legacy_unknown"),
            "current_state": _text_or(runtime.get("current_state"), "aborted"),
            "bootstrap_completed": bool(runtime.get("bootstrap_completed", False)),
            "runtime_flags": _string_list(runtime.get("runtime_flags")),
            "step_history": [
                {
                    "step_index": _integer_or(item.get("step_index"), index),
                    "runtime_state": _text_or(
                        item.get("runtime_state"), _text_or(runtime.get("current_state"), "aborted")
                    ),
                    "node_name": _text_or(item.get("node_name"), "legacy_unknown"),
                    "summary": _text_or(item.get("summary"), ""),
                }
                for index, item in enumerate(_dict_list(runtime.get("step_history")))
            ],
            "actor_turn_count": _integer_or(runtime.get("actor_turn_count"), 0),
            "tool_call_count": _integer_or(runtime.get("tool_call_count"), 0),
        },
        "intake": {
            "goal": _text_or(intake.get("goal"), "legacy_unknown"),
            "context": _text_or(intake.get("context"), ""),
            "domain_type": _text_or(intake.get("domain_type"), "other"),
            "domain_label": _text_or(intake.get("domain_label"), "other"),
            "sensitivity": _text_or(intake.get("sensitivity"), "medium"),
            "success_criteria": _string_list(intake.get("success_criteria")),
            "constraints": _string_list(intake.get("constraints")),
            "parse_status": _text_or(intake.get("parse_status"), "partial"),
            "parser_name": _text_or(intake.get("parser_name"), "legacy_unknown"),
            "prompt_version": _text_or(intake.get("prompt_version"), "legacy_unknown"),
            "schema_version": _text_or(intake.get("schema_version"), "legacy_unknown"),
            "policy_profile_name": _text_or(
                intake.get("policy_profile_name"), "legacy_unknown"
            ),
            "confidence_threshold": _number_or(intake.get("confidence_threshold"), 0.0),
            "max_steps": _optional_integer(intake.get("max_steps")),
            "max_tool_calls": _optional_integer(intake.get("max_tool_calls")),
            "read_only_mode": bool(intake.get("read_only_mode", False)),
            "execution_intent": "proposal_only",
            "requested_operations": [],
        },
        "planner_name": _optional_text(source.get("planner_name")),
        "planner_prompt_version": _optional_text(source.get("planner_prompt_version")),
        "planner_rationale": _optional_text(source.get("planner_rationale")),
        "planning_source": _optional_planning_source(source.get("planning_source")),
        "plan_steps": [
            _normalize_legacy_plan_step(item, index=index)
            for index, item in enumerate(_dict_list(source.get("plan_steps")), start=1)
        ],
        "current_plan_step_index": _optional_integer(source.get("current_plan_step_index")),
        "current_plan_step_id": _optional_text(source.get("current_plan_step_id")),
        "actor": {
            "actor_name": _optional_text(actor.get("actor_name")),
            "actor_prompt_version": _optional_text(actor.get("actor_prompt_version")),
            "result_type": _optional_text(actor.get("result_type")),
            "plan_step_id": _optional_text(actor.get("plan_step_id")),
            "summary": _optional_text(actor.get("summary")),
            "capability_mode": _optional_capability_mode(actor.get("capability_mode")),
            "external_impact": _optional_bool(actor.get("external_impact")),
            "output_text": _optional_text(actor.get("output_text")),
            "blocking_reason": _optional_text(actor.get("blocking_reason")),
            "proposed_tool_name": _optional_text(actor.get("proposed_tool_name")),
            "actor_self_confidence": _number_or(
                actor.get("actor_self_confidence", actor.get("confidence")), 0.0
            ),
            "selected_evidence_refs": _string_list(actor.get("selected_evidence_refs")),
        },
        "execution": {
            "final_output_text": _optional_text(execution.get("final_output_text")),
            "executed_steps": _dict_list(execution.get("executed_steps")),
            "scratchpad": _dict_list(execution.get("scratchpad")),
            "step_validations": _dict_list(execution.get("step_validations")),
        },
        "tool_calls": [
            _normalize_legacy_tool_call(item)
            for item in _dict_list(source.get("tool_calls"))
        ],
        "observed_evidence": [
            _normalize_legacy_evidence(item, index=index)
            for index, item in enumerate(
                _dict_list(source.get("observed_evidence")), start=1
            )
        ],
        "governance_passes": [
            _normalize_legacy_governance_pass(item, index=index, actor=actor)
            for index, item in enumerate(governance_passes, start=1)
        ],
        "human_review": _normalize_legacy_human_review(source.get("human_review")),
        "external_communication": None,
        "external_execution": _empty_external_execution(),
    }


# Normalize one legacy semantic plan step into the v4 contract.
def _normalize_legacy_plan_step(item: dict[str, Any], *, index: int) -> dict[str, Any]:
    return {
        "step_id": _text_or(item.get("step_id"), f"legacy_step_{index}"),
        "objective": _text_or(item.get("objective"), "legacy_unknown"),
        "rationale": _text_or(item.get("rationale"), ""),
        "expected_outcome": _text_or(item.get("expected_outcome"), ""),
        "status": _text_or(item.get("status"), "pending"),
    }


# Normalize one legacy tool record while preserving its observable request/result fields.
def _normalize_legacy_tool_call(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_step_id": _text_or(item.get("plan_step_id"), "legacy_unknown"),
        "tool_name": _text_or(item.get("tool_name"), "legacy_unknown"),
        "reason": _text_or(item.get("reason"), ""),
        "query": _optional_text(item.get("query")),
        "url": _optional_text(item.get("url")),
        "text": _optional_text(item.get("text")),
        "path": _optional_text(item.get("path")),
        "expression": _optional_text(item.get("expression")),
        "timezone": _optional_text(item.get("timezone")),
        "limit": _optional_integer(item.get("limit")),
        "max_results": _integer_or(item.get("max_results"), 5),
        "extractive_goal": _optional_text(item.get("extractive_goal")),
        "success": _optional_bool(item.get("success")),
        "summary": _optional_text(item.get("summary")),
        "content": _optional_text(item.get("content")),
        "error_message": _optional_text(item.get("error_message")),
        "error_code": _optional_text(item.get("error_code")),
        "truncated": bool(item.get("truncated", False)),
        "extractability_limited": bool(item.get("extractability_limited", False)),
        "sources": [
            {
                "title": _text_or(source.get("title"), ""),
                "locator": _text_or(source.get("locator"), ""),
                "snippet": _optional_text(source.get("snippet")),
                "content_state": _text_or(source.get("content_state"), "metadata_only"),
                "source_authority": _text_or(source.get("source_authority"), "unknown"),
                "document_type": _text_or(source.get("document_type"), "unknown"),
                "extractability": _text_or(source.get("extractability"), "unavailable"),
                "citation_alignment": _text_or(source.get("citation_alignment"), "none"),
            }
            for source in _dict_list(item.get("sources"))
        ],
    }


# Normalize one legacy evidence item without strengthening its support level.
def _normalize_legacy_evidence(item: dict[str, Any], *, index: int) -> dict[str, Any]:
    return {
        "evidence_id": _text_or(item.get("evidence_id"), f"legacy:{index}"),
        "plan_step_id": _text_or(item.get("plan_step_id"), "legacy_unknown"),
        "title": _text_or(item.get("title"), ""),
        "locator": _text_or(item.get("locator"), ""),
        "excerpt": _text_or(item.get("excerpt"), ""),
        "source_kind": _text_or(item.get("source_kind"), "tool_observation"),
        "source_tool": _optional_text(item.get("source_tool")),
        "content_state": _text_or(item.get("content_state"), "metadata_only"),
        "source_authority": _text_or(item.get("source_authority"), "unknown"),
        "document_type": _text_or(item.get("document_type"), "unknown"),
        "extractability": _text_or(item.get("extractability"), "unavailable"),
        "citation_alignment": _text_or(item.get("citation_alignment"), "none"),
    }


# Normalize a legacy governance decision into one closed v4 pass.
def _normalize_legacy_governance_pass(
    item: dict[str, Any], *, index: int, actor: dict[str, Any]
) -> dict[str, Any]:
    return {
        "pass_index": _integer_or(item.get("pass_index"), index),
        "evaluated_plan_step_id": _optional_text(item.get("evaluated_plan_step_id")),
        "evaluated_actor_result_type": _text_or(
            item.get("evaluated_actor_result_type"),
            _text_or(actor.get("result_type"), "blocked"),
        ),
        "evaluated_tool_name": _optional_text(item.get("evaluated_tool_name")),
        "evaluated_operation_digest": _optional_text(
            item.get("evaluated_operation_digest")
        ),
        "governance_input_schema_version": _optional_text(
            item.get("governance_input_schema_version")
        ),
        "policy_bundle_version": _text_or(
            item.get("policy_bundle_version"), "legacy_unknown"
        ),
        "policy_config_version": _optional_text(item.get("policy_config_version")),
        "pillar_requirements": _bool_dict(item.get("pillar_requirements")),
        "pillar_results": _object_dict(item.get("pillar_results")),
        "pillar_errors": _string_dict(item.get("pillar_errors")),
        "directive": _text_or(item.get("directive"), "stop"),
        "policy_decision": _text_or(item.get("policy_decision"), "deny"),
        "requires_human_review": bool(item.get("requires_human_review", False)),
        "reason_codes": _string_list(item.get("reason_codes")),
        "selected_reason": _text_or(item.get("selected_reason"), ""),
        "rejected_alternatives": _string_dict(item.get("rejected_alternatives")),
        "safety_invariants": _string_list(item.get("safety_invariants")),
    }


# Add conservative defaults when reading current v4 traces at the evaluation boundary.
def _add_current_v4_compatibility_defaults(
    source: dict[str, Any],
) -> dict[str, Any]:
    intake = _dict_or_empty(source.get("intake"))
    intake.setdefault("execution_intent", "proposal_only")
    source["intake"] = intake
    source.setdefault("external_execution", _empty_external_execution())
    for governance_pass in _dict_list(source.get("governance_passes")):
        governance_pass.setdefault("evaluated_operation_digest", None)
    communication = source.get("external_communication")
    if isinstance(communication, dict):
        _add_draft_recipient_defaults(communication.get("draft"))
        last_edit = communication.get("last_applied_edit")
        if isinstance(last_edit, dict):
            _add_draft_recipient_defaults(last_edit.get("draft"))
        for edit in _dict_list(communication.get("edit_history")):
            _add_draft_recipient_defaults(edit.get("draft"))
    human_review = _dict_or_empty(source.get("human_review"))
    for request_key in ("pending_request", "last_request"):
        request = human_review.get(request_key)
        if isinstance(request, dict):
            _add_draft_recipient_defaults(
                request.get("external_communication_draft")
            )
    return source


# Add empty CC/BCC arrays to legacy communication drafts.
def _add_draft_recipient_defaults(payload: Any) -> None:
    if isinstance(payload, dict):
        payload.setdefault("cc", [])
        payload.setdefault("bcc", [])


# Build the conservative non-executable projection for older traces.
def _empty_external_execution() -> dict[str, Any]:
    return {
        "execution_intent": "proposal_only",
        "capability_id": None,
        "current_payload_digest": None,
        "current_request": None,
        "last_receipt": None,
        "receipt_history": [],
        "attempt_count": 0,
    }


# Preserve the nullable legacy review summary while making its empty shape explicit.
def _normalize_legacy_human_review(payload: Any) -> dict[str, Any]:
    review = _dict_or_empty(payload)
    return {
        "pending_request": review.get("pending_request") if isinstance(review.get("pending_request"), dict) else None,
        "last_request": review.get("last_request") if isinstance(review.get("last_request"), dict) else None,
        "last_decision": review.get("last_decision") if isinstance(review.get("last_decision"), dict) else None,
        "decision_history": _dict_list(review.get("decision_history")),
    }


# Return a dictionary or an empty read model for malformed optional legacy sections.
def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# Retain only object items from a legacy list.
def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


# Retain only string items from a legacy list.
def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


# Normalize a legacy string with an explicit conservative fallback.
def _text_or(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


# Normalize a nullable legacy string.
def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


# Retain only the two producer-owned planning source values.
def _optional_planning_source(value: Any) -> str | None:
    return value if value in {"planner", "replanner"} else None


# Retain only typed capability modes from legacy actor output.
def _optional_capability_mode(value: Any) -> str | None:
    return value if value in {"none", "read_only", "write"} else None


# Normalize an optional boolean without coercing arbitrary legacy values.
def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


# Normalize a bounded counter-compatible integer.
def _integer_or(value: Any, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else fallback


# Normalize a nullable positive integer.
def _optional_integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


# Normalize numeric legacy policy values without accepting booleans.
def _number_or(value: Any, fallback: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else fallback


# Retain boolean-valued extension facts.
def _bool_dict(value: Any) -> dict[str, bool]:
    return {
        key: item
        for key, item in _dict_or_empty(value).items()
        if isinstance(key, str) and isinstance(item, bool)
    }


# Retain string-valued extension facts.
def _string_dict(value: Any) -> dict[str, str]:
    return {
        key: item
        for key, item in _dict_or_empty(value).items()
        if isinstance(key, str) and isinstance(item, str)
    }


# Retain object-valued pillar results without interpreting their policy semantics.
def _object_dict(value: Any) -> dict[str, dict[str, Any]]:
    return {
        key: item
        for key, item in _dict_or_empty(value).items()
        if isinstance(key, str) and isinstance(item, dict)
    }


# Normalize v1/v2/v3 decisions into the v4 read model without mutating the source file.
def _normalize_governance_passes(trace_payload: dict[str, Any]) -> list[dict[str, Any]]:
    current = trace_payload.get("governance_passes")
    if isinstance(current, list):
        return [item for item in current if isinstance(item, dict)]
    legacy = trace_payload.get("governance_decisions")
    if not isinstance(legacy, list):
        trace_payload["governance_passes"] = []
        return []
    normalized: list[dict[str, Any]] = []
    for index, decision in enumerate(legacy, start=1):
        if not isinstance(decision, dict):
            continue
        item = dict(decision)
        item.setdefault("pass_index", index)
        item.setdefault("selected_reason", item.get("combiner_selected_reason") or item.get("summary", ""))
        item.setdefault("rejected_alternatives", item.get("combiner_rejected_alternatives") or {})
        item.setdefault("safety_invariants", item.get("combiner_safety_invariants_checked") or [])
        if "requires_human_review" not in item:
            item["requires_human_review"] = bool(
                item.get("policy_decision") == "require_human_review"
                or item.get("directive") == "wait_human_review"
            )
        normalized.append(item)
    trace_payload["governance_passes"] = normalized
    return normalized


# Load one trace file and normalize it without importing runtime dataclasses.
def normalize_trace_file(trace_path: Path) -> dict[str, Any]:
    normalized = normalize_trace_json(json.loads(trace_path.read_text(encoding="utf-8")))
    _validate_evaluation_input(normalized)
    return normalized


# Detect fail-closed traces from reason codes and invariant text.
def _has_fail_closed(governance_decisions: Any) -> bool:
    if not isinstance(governance_decisions, list):
        return False
    for decision in governance_decisions:
        if not isinstance(decision, dict):
            continue
        reason_codes = decision.get("reason_codes", [])
        invariants = decision.get("safety_invariants", [])
        if "opa_failure_fail_closed" in reason_codes or "fail_closed_on_opa_error" in invariants:
            return True
    return False


# Detect whether any governance decision used a specific directive.
def _has_directive(governance_decisions: Any, directive: str) -> bool:
    if not isinstance(governance_decisions, list):
        return False
    return any(isinstance(decision, dict) and decision.get("directive") == directive for decision in governance_decisions)


# Preserve observable v1 facts but refuse to reconstruct v2-only governance evidence.
def _trace_version_and_completeness(*, trace_payload: dict[str, Any], governance_decisions: Any) -> tuple[str, str]:
    if not isinstance(governance_decisions, list) or not governance_decisions:
        return "unknown", "inconclusive"
    decisions = [item for item in governance_decisions if isinstance(item, dict)]
    if not decisions:
        return "unknown", "inconclusive"
    if trace_payload.get("trace_version") == "layer2_trace.v4" and all(
        isinstance(item.get("pillar_results"), dict)
        and isinstance(item.get("safety_invariants"), list)
        for item in decisions
    ):
        return "v4", "complete"
    is_v2 = all(
        isinstance(item.get("pillar_results"), dict)
        and (
            isinstance(item.get("combiner_safety_invariants_checked"), list)
            or isinstance(item.get("safety_invariants"), list)
        )
        for item in decisions
    )
    if is_v2:
        return "v2", "complete"
    if isinstance(trace_payload.get("runtime"), dict):
        return "v1", "partial"
    return "unknown", "inconclusive"


# Validate the standalone EvaluationInput contract without importing runtime modules.
def _validate_evaluation_input(payload: dict[str, Any]) -> None:
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "evaluation_input.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        import jsonschema
    except ImportError:
        if payload.get("schema_version") != "evaluation_input.v1":
            raise ValueError("EvaluationInput has an unsupported schema_version.")
        if not isinstance(payload.get("trace"), dict) or not isinstance(payload.get("derived"), dict):
            raise ValueError("EvaluationInput.trace and EvaluationInput.derived must be objects.")
        return
    jsonschema.Draft202012Validator(schema).validate(payload)
