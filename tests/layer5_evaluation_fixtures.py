from __future__ import annotations

import json
from pathlib import Path


# Write the campaign metadata independently of whether a trace exists.
def write_run_record(root: Path, *, run_id: str = "run-1", status: str = "not_started", **fields) -> Path:
    directory = root / run_id
    directory.mkdir(exist_ok=True)
    path = directory / "run_record.json"
    path.write_text(json.dumps({
        "record_version": "evaluation_campaign_run.v1",
        "thread_id": run_id,
        "campaign_id": "campaign-1",
        "scenario_id": "scenario-1",
        "family": "research",
        "status": status,
        **fields,
    }), encoding="utf-8")
    return path


# Write one compact v6 trace covering every Layer 5 table.
def write_trace(root: Path) -> Path:
    trace_dir = root / "run-1"
    trace_dir.mkdir()
    path = trace_dir / "full-trace-2.json"
    path.write_text(json.dumps(trace_payload()), encoding="utf-8")
    return path


# Build the minimal canonical trace fields consumed by Layer 5.
def trace_payload() -> dict[str, object]:
    return {
        "trace_version": "layer2_trace.v6",
        "runtime": {
            "thread_id": "run-1",
            "current_state": "completed",
            "termination_reason": None,
            "actor_turn_count": 3,
            "step_history": [{"node_name": "replanner"}],
        },
        "intake": {
            "goal": "Send a governed result.",
            "context": "Deterministic test.",
            "constraints": ["Require review."],
            "success_criteria": ["Operation executed."],
            "sensitivity": "high",
            "execution_intent": "execute_after_governance",
            "policy_profile_name": "default",
            "confidence_threshold": 0.7,
            "read_only_mode": False,
        },
        "planner_name": "test-planner",
        "planner_prompt_version": "planner-v1",
        "actor": {
            "actor_name": "test-actor",
            "actor_prompt_version": "actor-v1",
            "external_operation": None,
        },
        "tool_calls": [
            {"tool_name": "web_search", "success": True},
            {"tool_name": "fetch_url_text", "success": False},
        ],
        "governance_passes": [
            {
                "pass_index": 1,
                "directive": "wait_human_review",
                "policy_decision": "require_human_review",
                "reason_codes": ["judge_uncertain"],
                "requires_human_review": True,
                "policy_bundle_version": "policy-v1",
                "policy_config_version": "config-v1",
            },
            {
                "pass_index": 2,
                "directive": "continue",
                "policy_decision": "allow",
                "reason_codes": ["local_output_allowed"],
                "requires_human_review": False,
                "policy_bundle_version": "policy-v1",
                "policy_config_version": "config-v1",
            },
        ],
        "human_review": {
            "decision_history": [
                {
                    "request_id": "review-1",
                    "decision_type": "modify_and_continue",
                    "reviewer_id": "reviewer-1",
                    "note": "Corrected target.",
                    "external_operation_edit": {"target": "safe-target"},
                    "approved_operation_digest": "a" * 64,
                }
            ]
        },
        "external_execution": {
            "receipt_history": [
                {
                    "operation_id": "operation-1",
                    "capability_id": "smtp_email",
                    "payload_digest": "a" * 64,
                    "status": "executed",
                    "started_at": "2026-08-05T10:00:00+00:00",
                    "completed_at": "2026-08-05T10:00:02+00:00",
                }
            ]
        },
    }
