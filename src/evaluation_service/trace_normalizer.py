from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


# Normalize exported trace JSON into the runtime-independent EvaluationInput contract.
def normalize_trace_json(trace_payload: dict[str, Any]) -> dict[str, Any]:
    normalized_trace = deepcopy(trace_payload)
    governance_decisions = _normalize_governance_passes(normalized_trace)
    runtime = normalized_trace.get("runtime", {})
    trace_version, completeness = _trace_version_and_completeness(
        trace_payload=normalized_trace,
        governance_decisions=governance_decisions,
    )
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
            "pillar_results_available": trace_version in {"v2", "v3"},
            "safety_invariants_available": trace_version in {"v2", "v3"},
        },
    }


# Normalize v1/v2 decisions into the v3 read model without mutating the source file.
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
    if trace_payload.get("trace_version") == "layer2_trace.v3" and all(
        isinstance(item.get("pillar_results"), dict)
        and isinstance(item.get("safety_invariants"), list)
        for item in decisions
    ):
        return "v3", "complete"
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
