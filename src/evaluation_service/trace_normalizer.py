from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# Normalize exported trace JSON into the runtime-independent EvaluationInput contract.
def normalize_trace_json(trace_payload: dict[str, Any]) -> dict[str, Any]:
    governance_decisions = trace_payload.get("governance_decisions", [])
    runtime = trace_payload.get("runtime", {})
    trace_version, completeness = _trace_version_and_completeness(
        trace_payload=trace_payload,
        governance_decisions=governance_decisions,
    )
    return {
        "schema_version": "evaluation_input.v1",
        "trace": trace_payload,
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
            "pillar_results_available": trace_version == "v2",
            "safety_invariants_available": trace_version == "v2",
        },
    }


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
        invariants = decision.get("combiner_safety_invariants_checked", [])
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
    is_v2 = all(
        isinstance(item.get("pillar_results"), dict)
        and isinstance(item.get("combiner_safety_invariants_checked"), list)
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
