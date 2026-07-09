from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# Normalize exported trace JSON into the runtime-independent EvaluationInput contract.
def normalize_trace_json(trace_payload: dict[str, Any]) -> dict[str, Any]:
    governance_decisions = trace_payload.get("governance_decisions", [])
    runtime = trace_payload.get("runtime", {})
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
        },
    }


# Load one trace file and normalize it without importing runtime dataclasses.
def normalize_trace_file(trace_path: Path) -> dict[str, Any]:
    return normalize_trace_json(json.loads(trace_path.read_text(encoding="utf-8")))


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
