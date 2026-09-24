"""Boundary tests for the offline fact-to-policy audit oracle."""

from scripts.audit_realistic_governance import (
    audit_event,
    evidence_predicates,
    expected_intervention,
    trajectory_predicates,
)


THRESHOLDS = {
    "max_evidence_requests_before_review": 3,
    "max_action_revisions_before_review": 3,
    "max_replans_before_review": 3,
    "max_consecutive_tool_failures_before_review": 4,
    "max_action_churn_before_review": 2,
    "max_tool_attempts_per_step_before_review": 30,
}


# Keep a minimal trace-like pass so tests challenge the oracle without live OPA.
def event_template() -> dict:
    return {
        "pass_index": 1,
        "policy_bundle_version": "layer2_governance_v6",
        "policy_config_version": "layer2_governance_thresholds_v3",
        "evaluated_actor_result_type": "step_completed",
        "evaluated_operation_digest": "a" * 64,
        "pillar_requirements": {
            "require_evidence": True,
            "require_trajectory": True,
            "require_judge": False,
        },
        "pillar_results": {
            "static_policy": {
                "hard_deny": False,
                "clarification_signaled": False,
                "read_only_required": False,
            },
            "evidence": {"raw_facts": {}},
            "trajectory": {"raw_facts": {}},
            "judge": {"raw_facts": {}, "invoked": False},
        },
        "judge_admission": {"required": False},
        "directive": "continue",
        "reason_codes": ["allowed_by_policy"],
        "requires_human_review": False,
    }


# Verify independent-source failure is fixable unless the request ceiling is hit.
def test_evidence_deficit_and_ceiling() -> None:
    event = event_template()
    facts = event["pillar_results"]["evidence"]["raw_facts"]
    facts.update({"coverage_evaluated": True, "independent_sources_satisfied": False})
    evidence = evidence_predicates(facts, THRESHOLDS)
    trajectory = trajectory_predicates({}, THRESHOLDS)
    assert expected_intervention(event, evidence, trajectory) == (
        "evidence", "invalid_or_missing_selected_evidence", "request_more_evidence"
    )
    facts["evidence_request_episode_count"] = 3
    evidence = evidence_predicates(facts, THRESHOLDS)
    assert expected_intervention(event, evidence, trajectory) == (
        "evidence", "evidence_request_limit_reached", "wait_human_review"
    )


# Verify an unanchored literal escalates only when it has not been adjudicated.
def test_evidence_anchoring_adjudication() -> None:
    facts = {"evidence_completion_attempt": True, "has_unanchored_literals_in_selection": True}
    assert evidence_predicates(facts, THRESHOLDS)["evidence_concern"]
    facts["anchoring_concern_adjudicated"] = True
    assert not evidence_predicates(facts, THRESHOLDS)["evidence_concern"]


# Verify the four-failure threshold outranks a simultaneous Evidence deficit.
def test_trajectory_precedence_over_evidence() -> None:
    event = event_template()
    evidence = evidence_predicates(
        {"coverage_evaluated": True, "independent_sources_satisfied": False}, THRESHOLDS
    )
    trajectory = trajectory_predicates({"consecutive_tool_failures": 4}, THRESHOLDS)
    assert expected_intervention(event, evidence, trajectory) == (
        "trajectory", "consecutive_tool_failures", "wait_human_review"
    )


# Verify routine directives are replayed when no unexported external gate applies.
def test_routine_directive_mismatch_detected() -> None:
    event = event_template()
    event["pillar_results"]["static_policy"]["read_only_required"] = True
    run = {"run_id": "r", "scenario_id": "rp01", "trace_path": "trace", "trace_digest": "digest"}
    row = audit_event("test", run, event, THRESHOLDS)
    assert row["directive_checkable"]
    assert "directive_mismatch:routine" in row["issues"]
