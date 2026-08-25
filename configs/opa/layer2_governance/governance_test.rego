package layer2.governance

import rego.v1

local_operation := {"operation_id": null, "boundary": "local", "operation_family": "generic", "normalized_effect": "read", "execution_candidate": false, "target": {"kind": "local", "value": "runtime", "destination_class": "internal"}, "payload_digest": null, "capability_profile_id": null, "payload": {"payload_type": "none"}}

base_facts := {
	"task": {"sensitivity": "medium"},
	"policy": {"profile_name": "default", "confidence_threshold": 0.65, "read_only_mode": false},
	"runtime": {"state": "acting", "flags": [], "actor_turn_count": 1, "tool_call_count": 0, "observation_count": 2},
	"step": {"id": "step-1", "objective": "Perform the step", "rationale": "needed", "expected_outcome": "done"},
	"actor": {"result_type": "step_completed", "capability_mode": "none", "external_impact": false, "blocking_reason": null, "has_output_text": true},
	"tool": {"name": null, "has_query": false, "has_url": false, "is_read_only": null},
	"external_operation": local_operation,
	"external_communication": {"phase": null, "review_ready": false},
	"uncertainty": {"actor_self_confidence": 0.9, "configured_confidence_threshold": 0.65},
}

facts_evidence := {"raw_facts": {"completion_attempt": true, "factual_completion_attempt": true, "evidence_count": 2, "selected_evidence_ref_count": 1, "selected_evidence_refs_valid": true, "selected_evidence_refs_unique": true, "observation_ids_unique": true, "selected_observations_have_content": true, "selected_observations_provenance_resolvable": true, "selected_observations_belong_to_known_steps": true, "selected_evidence_all_title_only": false, "evidence_snapshot_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "evidence_selection_digest": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", "candidate_evidence_binding_present": true, "candidate_evidence_snapshot_matches": true, "candidate_evidence_selection_matches": true, "candidate_external_operation_digest_matches": true}, "score_components": {}, "score_formula_version": "evidence_integrity_provenance_v1", "thresholds_version": "layer2_governance_thresholds_v2"}
facts_trajectory := {"raw_facts": {"actor_turn_count": 4, "completed_step_count": 1, "tool_call_count": 2, "successful_tool_call_count": 2, "failed_tool_call_count": 0, "synthetic_tool_result_count": 0, "observation_count": 2, "replan_count": 0, "replan_episode_count": 0, "consecutive_provider_protocol_failures": 0, "needs_replan_signal": false, "replan_reason_present": false, "first_replan_request": true, "observable_change_since_last_replan": true, "completion_signal": true, "external_candidate": false, "external_action_revision_count": 0, "unchanged_resubmission": false, "repeated_rejected_payload_digest": false}, "score_components": {}, "score_formula_version": "trajectory_health_v1", "thresholds_version": "layer2_governance_thresholds_v2"}
shadow := {"raw_facts": {"mode": "shadow", "decision_influence": false, "pillar_unavailable": false}, "score_components": {}, "score_formula_version": "shadow", "thresholds_version": "layer2_governance_thresholds_v2"}
no_judge := {"raw_facts": {"invoked": false, "required": false, "status": "not_applicable", "pillar_unavailable": false, "verdict": "uncertain", "severity": "low", "digest_matches": false}, "score_components": {}, "score_formula_version": "judge_v4", "thresholds_version": "layer2_governance_thresholds_v2"}

base_input := {"schema_version": "governance_decision_input.v1", "policy_bundle_version": "layer2_governance_v5", "policy_config_version": "layer2_governance_thresholds_v2", "base_facts": base_facts, "pillar_requirements": {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false}, "pillars": {"evidence": facts_evidence, "trajectory": facts_trajectory, "risk": shadow, "behavioral": shadow, "judge": no_judge}, "pillar_unavailable": {"evidence": false, "trajectory": false, "risk": false, "behavioral": false, "judge": false}, "pillar_errors": {}}

governed_operation := {"operation_id": "op-1", "boundary": "governed_external", "operation_family": "communication", "normalized_effect": "send", "execution_candidate": true, "target": {"kind": "recipient", "value": "ops@example.com", "destination_class": "external"}, "payload_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "capability_profile_id": "communication.default", "payload": {"payload_type": "communication", "recipient": "ops@example.com"}}
safe_judge := {"raw_facts": {"invoked": true, "required": true, "status": "active", "pillar_unavailable": false, "verdict": "safe", "severity": "low", "evaluated_operation_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "expected_operation_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "operation_digest_matches": true, "evaluated_evidence_snapshot_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "expected_evidence_snapshot_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "evidence_snapshot_matches": true, "evaluated_evidence_selection_digest": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", "expected_evidence_selection_digest": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", "evidence_selection_matches": true, "digest_matches": true}, "score_components": {}, "score_formula_version": "external_operation_semantic_judge_v2", "thresholds_version": "layer2_governance_thresholds_v2"}

test_intermediate_output_requires_no_active_semantic_pillars if {
	facts := object.union(base_facts, {"actor": object.union(base_facts.actor, {"result_type": "intermediate_output"})})
	result := required_pillars with input as {"base_facts": facts}
	result.require_evidence == false
	result.require_trajectory == true
	result.require_judge == false
}

test_read_only_tool_request_requires_static_policy_only if {
	facts := object.union(base_facts, {"actor": object.union(base_facts.actor, {"result_type": "propose_action", "capability_mode": "read_only", "has_factual_content": false})})
	result := required_pillars with input as {"base_facts": facts}
	result.require_evidence == false
	result.require_trajectory == true
	result.require_judge == false
}

test_propose_action_with_factual_content_requires_evidence if {
	facts := object.union(base_facts, {"actor": object.union(base_facts.actor, {"result_type": "propose_action", "capability_mode": "read_only", "has_factual_content": true})})
	result := required_pillars with input as {"base_facts": facts}
	result.require_evidence == true
	result.require_trajectory == true
	result.require_judge == false
}

test_needs_replan_requires_trajectory_only if {
	facts := object.union(base_facts, {"actor": object.union(base_facts.actor, {"result_type": "needs_replan"})})
	result := required_pillars with input as {"base_facts": facts}
	result.require_evidence == false
	result.require_trajectory == true
	result.require_judge == false
}

test_local_completion_requires_evidence_and_trajectory if {
	result := required_pillars with input as {"base_facts": base_facts}
	result.require_evidence == true
	result.require_trajectory == true
	result.require_judge == false
}

# Active deterministic pillars retain their non-scored diagnostic levels and declare active mode for the dashboard.
test_active_diagnostic_levels_are_projected if {
	result := decision with input as base_input
	result.pillar_results.evidence.diagnostic_level == "unscored"
	result.pillar_results.evidence.mode == "active"
	result.pillar_results.evidence.decision_influence == true
	result.pillar_results.trajectory.diagnostic_level == "observed"
	result.pillar_results.trajectory.mode == "active"
	result.pillar_results.trajectory.decision_influence == true
}

test_trajectory_fact_usage_manifest_is_explicit if {
	result := data.layer2.governance.pillars.trajectory.result with input as base_input
	result.fact_usage.policy_facts == [
		"needs_replan_signal",
		"replan_reason_present",
		"first_replan_request",
		"observable_change_since_last_replan",
		"external_action_revision_count",
                "unchanged_resubmission",
                "repeated_rejected_payload_digest",
                "unresolvable_revision",
                "replan_episode_count",
		"consecutive_tool_failures",
		"action_churn_count",
		"external_candidate",
	]
	result.fact_usage.observational_only_facts == [
		"actor_turn_count",
		"progress_epoch",
		"completed_step_count",
		"tool_call_count",
		"successful_tool_call_count",
		"failed_tool_call_count",
		"synthetic_tool_result_count",
		"observation_count",
		"replan_count",
		"consecutive_provider_protocol_failures",
		"completion_signal",
		"provider_recovery_active",
		"provider_protocol_retry_exhausted",
	]
}

test_inactive_deterministic_pillars_declare_inactive_mode if {
	candidate := object.union(base_input, {"pillar_requirements": {"require_evidence": false, "require_risk": false, "require_trajectory": false, "require_behavioral": false, "require_judge": false}})
	result := decision with input as candidate
	result.pillar_results.evidence.mode == "inactive"
	result.pillar_results.evidence.decision_influence == false
	result.pillar_results.trajectory.mode == "inactive"
	result.pillar_results.trajectory.decision_influence == false
}

test_governed_external_candidate_defers_judge_to_staged_admission if {
	facts := object.union(base_facts, {"external_operation": governed_operation})
	result := required_pillars with input as {"base_facts": facts}
	result.require_evidence == true
	result.require_trajectory == true
	result.require_judge == false
}

test_healthy_governed_external_candidate_admits_judge if {
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation})})
	result := judge_admission with input as candidate
	result.required == true
}

test_incomplete_governed_external_proposal_does_not_admit_judge if {
	actor_facts := object.union(base_facts.actor, {"result_type": "propose_action"})
	candidate_facts := object.union(base_facts, {"actor": actor_facts, "external_operation": governed_operation})
	candidate := object.union(base_input, {"base_facts": candidate_facts})
	result := judge_admission with input as candidate
	result.required == false
}

test_evidence_gap_prevents_judge_admission if {
	invalid_evidence := object.union(facts_evidence, {"raw_facts": object.union(facts_evidence.raw_facts, {"selected_evidence_refs_valid": false})})
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation}), "pillars": object.union(base_input.pillars, {"evidence": invalid_evidence})})
	result := judge_admission with input as candidate
	result.required == false
}

test_candidate_snapshot_mismatch_prevents_judge_admission if {
	mismatched_evidence := object.union(facts_evidence, {"raw_facts": object.union(facts_evidence.raw_facts, {"candidate_evidence_binding_required": true, "candidate_evidence_binding_present": true, "candidate_evidence_snapshot_matches": false, "candidate_evidence_selection_matches": true, "candidate_external_operation_digest_matches": true})})
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation}), "pillars": object.union(base_input.pillars, {"evidence": mismatched_evidence})})
	result := judge_admission with input as candidate
	result.required == false
}

test_read_only_tool_request_skips_evidence if {
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"actor": object.union(base_facts.actor, {"result_type": "propose_action", "capability_mode": "read_only"}), "tool": {"name": "web_search", "has_query": true, "has_url": false, "is_read_only": true}}), "pillar_requirements": {"require_evidence": false, "require_risk": false, "require_trajectory": false, "require_behavioral": false, "require_judge": false}})
	result := decision with input as candidate
	result.directive == "continue"
}

test_invalid_selected_evidence_requests_more_evidence if {
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": object.union(facts_evidence, {"raw_facts": object.union(facts_evidence.raw_facts, {"selected_evidence_refs_valid": false})})})})
	result := decision with input as candidate
	result.directive == "request_more_evidence"
}

test_unanchored_literal_requests_more_evidence if {
	evidence := object.union(facts_evidence, {"raw_facts": object.union(facts_evidence.raw_facts, {"has_unanchored_literals_in_selection": true})})
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": evidence})})
	result := decision with input as candidate
	result.directive == "request_more_evidence"
}

test_nonfactual_completion_keeps_literal_gaps_observational if {
	raw_facts := object.union(facts_evidence.raw_facts, {"factual_completion_attempt": false, "has_unanchored_literals_in_selection": true, "has_unanchored_literals_in_universe": true, "deficit_codes": []})
	evidence := object.union(facts_evidence, {"raw_facts": raw_facts})
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": evidence})})
	result := decision with input as candidate
	result.directive == "continue"
	result.pillar_results.evidence.evidence_gap == false
}

test_nonfactual_completion_still_enforces_selected_reference_integrity if {
	raw_facts := object.union(facts_evidence.raw_facts, {"factual_completion_attempt": false, "selected_evidence_refs_valid": false, "deficit_codes": ["invalid_evidence_ref"]})
	evidence := object.union(facts_evidence, {"raw_facts": raw_facts})
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": evidence})})
	result := decision with input as candidate
	result.directive == "request_more_evidence"
}

test_freshness_deficit_remains_diagnostic_while_enforcement_disabled if {
	evidence := object.union(facts_evidence, {"raw_facts": object.union(facts_evidence.raw_facts, {"coverage_evaluated": true, "burden_satisfied": false, "independent_sources_satisfied": true, "retrieved_content_satisfied": true, "freshness_evaluated": true, "freshness_satisfied": false, "deficit_codes": ["freshness_metadata_missing"]})})
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": evidence})})
	result := decision with input as candidate
        result.directive == "continue"
        result.pillar_results.evidence.evidence_gap == false
        result.pillar_results.evidence.freshness_enforcement_enabled == false
        not "freshness_metadata_missing" in result.pillar_results.evidence.reason_codes
}

test_stale_evidence_requests_more_evidence_when_enforcement_enabled if {
	evidence := object.union(facts_evidence, {"raw_facts": object.union(facts_evidence.raw_facts, {"coverage_evaluated": true, "burden_satisfied": false, "independent_sources_satisfied": true, "retrieved_content_satisfied": true, "freshness_evaluated": true, "freshness_satisfied": false})})
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": evidence})})
	result := decision with input as candidate with data.policy_config.evidence.freshness_enforcement_enabled as true
	result.directive == "request_more_evidence"
}

test_explicit_insufficiency_remains_valid_when_freshness_enforcement_enabled if {
    raw_facts := object.union(facts_evidence.raw_facts, {"coverage_evaluated": true, "independent_sources_satisfied": false, "retrieved_content_satisfied": false, "freshness_evaluated": false, "insufficiency_declaration_permitted": true, "insufficiency_declared_in_payload": true})
    evidence := object.union(facts_evidence, {"raw_facts": raw_facts})
    candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": evidence})})
    result := decision with input as candidate with data.policy_config.evidence.freshness_enforcement_enabled as true
    result.directive == "continue"
    result.pillar_results.evidence.evidence_gap == false
}

test_circular_evidence_requests_more_evidence if {
	evidence := object.union(facts_evidence, {"raw_facts": object.union(facts_evidence.raw_facts, {"circular_reference_detected": true})})
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": evidence})})
	result := decision with input as candidate
	result.directive == "request_more_evidence"
}

test_missing_factual_burden_remains_diagnostic if {
	diagnostic_facts := object.union(facts_evidence.raw_facts, {"factual_step_missing_evidence_burden": true, "deficit_codes": ["missing_evidence_burden"]})
	evidence := object.union(facts_evidence, {"raw_facts": diagnostic_facts})
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": evidence})})
	result := decision with input as candidate
	result.directive == "continue"
	result.pillar_results.evidence.evidence_gap == false
	"missing_evidence_burden" in result.pillar_results.evidence.reason_codes
	object.get(result.rejected_alternatives, "missing_evidence_burden", null) == null
}

test_external_candidate_without_selected_evidence_can_reach_judge if {
	no_refs := object.union(facts_evidence, {"raw_facts": object.union(facts_evidence.raw_facts, {"selected_evidence_ref_count": 0, "external_operation_candidate": true})})
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation}), "pillar_requirements": {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": true}, "pillars": object.union(base_input.pillars, {"evidence": no_refs, "judge": safe_judge})})
	result := decision with input as candidate
	result.directive == "continue"
}

test_static_hard_deny_is_absorbing if {
	operation := object.union(governed_operation, {"target": {"kind": "recipient", "value": "blocked@example.com", "destination_class": "external"}})
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": operation}), "pillar_requirements": {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": true}})
	result := decision with input as candidate
	result.directive == "stop"
}

test_external_candidate_requires_judge_and_low_confidence_review if {
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation, "actor": object.union(base_facts.actor, {"capability_mode": "write", "external_impact": true}), "uncertainty": {"actor_self_confidence": 0.2, "configured_confidence_threshold": 0.65}}), "pillar_requirements": {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": true}, "pillars": object.union(base_input.pillars, {"judge": safe_judge})})
	result := decision with input as candidate
	result.directive == "wait_human_review"
	"external_low_actor_self_confidence" in result.reason_codes
}

test_low_confidence_alone_keeps_progressing if {
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"uncertainty": {"actor_self_confidence": 0.2, "configured_confidence_threshold": 0.65}})})
	result := decision with input as candidate
	result.directive == "continue"
}

test_actor_requested_replan_replans if {
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"actor": object.union(base_facts.actor, {"result_type": "needs_replan"})}), "pillar_requirements": {"require_evidence": false, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false}, "pillars": object.union(base_input.pillars, {"trajectory": object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"needs_replan_signal": true, "replan_reason_present": true, "first_replan_request": true, "completion_signal": false})})})})
	result := decision with input as candidate
	result.directive == "replan"
	"actor_requested_replan" in result.reason_codes
}

test_repeated_replan_without_new_observable_basis_requires_review if {
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"actor": object.union(base_facts.actor, {"result_type": "needs_replan"})}), "pillar_requirements": {"require_evidence": false, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false}, "pillars": object.union(base_input.pillars, {"trajectory": object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"needs_replan_signal": true, "replan_reason_present": true, "first_replan_request": false, "observable_change_since_last_replan": false, "completion_signal": false})})})})
	result := decision with input as candidate
	result.directive == "wait_human_review"
	"replan_request_stalled" in result.reason_codes
}

test_unchanged_external_candidate_does_not_admit_judge if {
	trajectory := object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"external_candidate": true, "unchanged_resubmission": true})})
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation}), "pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
	result := judge_admission with input as candidate
	result.required == false
}

test_unchanged_external_candidate_requires_non_authorizing_review if {
	trajectory := object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"external_candidate": true, "unchanged_resubmission": true})})
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation}), "pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
	result := decision with input as candidate
	result.directive == "wait_human_review"
	"unchanged_candidate_resubmission" in result.reason_codes
}

test_rejected_external_candidate_does_not_admit_judge if {
	trajectory := object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"external_candidate": true, "repeated_rejected_payload_digest": true})})
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation}), "pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
	result := judge_admission with input as candidate
	result.required == false
}

test_rejected_external_candidate_requires_non_authorizing_review if {
        trajectory := object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"external_candidate": true, "repeated_rejected_payload_digest": true})})
        candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation}), "pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
        result := decision with input as candidate
        result.directive == "wait_human_review"
        "rejected_candidate_resubmitted" in result.reason_codes
}

test_unresolvable_revision_precedes_rejected_candidate_resubmission if {
        trajectory := object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"external_candidate": true, "unresolvable_revision": true, "repeated_rejected_payload_digest": true})})
        candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation}), "pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
        result := decision with input as candidate
        result.directive == "wait_human_review"
        "unresolvable_revision" in result.reason_codes
        "rejected_candidate_resubmitted" in object.keys(result.rejected_alternatives)
}

test_action_revision_below_limit_allows_revision if {
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation}), "pillar_requirements": {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": true}, "pillars": object.union(base_input.pillars, {"judge": object.union(safe_judge, {"raw_facts": object.union(safe_judge.raw_facts, {"verdict": "needs_revision"})}), "trajectory": object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"external_action_revision_count": 2})})})})
	result := decision with input as candidate
	result.directive == "revise_action"
	"judge_needs_revision" in result.reason_codes
}

test_action_revision_limit_requires_review_not_another_revision if {
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation}), "pillar_requirements": {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": true}, "pillars": object.union(base_input.pillars, {"judge": object.union(safe_judge, {"raw_facts": object.union(safe_judge.raw_facts, {"verdict": "needs_revision"})}), "trajectory": object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"external_action_revision_count": 3})})})})
	result := decision with input as candidate
	result.directive == "wait_human_review"
	"action_revision_limit_reached" in result.reason_codes
}

test_shadow_pillars_do_not_change_directive if {
	baseline := decision with input as base_input
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"risk": {"raw_facts": {"mode": "shadow", "decision_influence": false, "impact_level": "critical"}}, "behavioral": {"raw_facts": {"mode": "shadow", "decision_influence": false, "novelty_score": 1}}})})
	changed := decision with input as candidate
	baseline.directive == changed.directive
}

test_trajectory_observational_facts_do_not_change_directive if {
	baseline := decision with input as base_input
	changed_raw_facts := object.union(facts_trajectory.raw_facts, {
		"actor_turn_count": 999,
		"progress_epoch": 999,
		"completed_step_count": 999,
		"tool_call_count": 999,
		"successful_tool_call_count": 999,
		"failed_tool_call_count": 999,
		"synthetic_tool_result_count": 999,
		"observation_count": 999,
		"replan_count": 999,
		"consecutive_provider_protocol_failures": 999,
		"completion_signal": false,
		"external_candidate": true,
		"provider_recovery_active": true,
		"provider_protocol_retry_exhausted": true,
	})
	changed_trajectory := object.union(facts_trajectory, {"raw_facts": changed_raw_facts})
	changed_input := object.union(base_input, {"pillars": object.union(base_input.pillars, {"trajectory": changed_trajectory})})
	changed := decision with input as changed_input
	baseline.directive == changed.directive
	baseline.reason_codes == changed.reason_codes
}

test_replan_ceiling_reached_requires_review if {
	trajectory := object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"needs_replan_signal": true, "replan_reason_present": true, "first_replan_request": false, "observable_change_since_last_replan": true, "replan_count": 3, "replan_episode_count": 3})})
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"actor": object.union(base_facts.actor, {"result_type": "needs_replan"})}), "pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
	result := decision with input as candidate
	result.directive == "wait_human_review"
	"replan_ceiling_reached" in result.reason_codes
	"actor_requested_replan" in object.keys(result.rejected_alternatives)
}

test_replan_ceiling_does_not_block_unrelated_progress_after_review if {
	trajectory := object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"replan_count": 3, "replan_episode_count": 0})})
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
	result := decision with input as candidate
	result.directive == "continue"
	result.pillar_results.trajectory.replan_ceiling_reached == false
	object.get(result.rejected_alternatives, "replan_ceiling_reached", null) == null
}

test_approved_replan_episode_starts_with_a_fresh_ceiling if {
	trajectory := object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"needs_replan_signal": true, "replan_reason_present": true, "first_replan_request": false, "observable_change_since_last_replan": true, "replan_count": 4, "replan_episode_count": 1})})
	candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"actor": object.union(base_facts.actor, {"result_type": "needs_replan"})}), "pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
	result := decision with input as candidate
	result.directive == "replan"
	result.pillar_results.trajectory.replan_ceiling_reached == false
}

test_consecutive_tool_failures_below_review_threshold_remain_observational if {
	trajectory := object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"consecutive_tool_failures": 2})})
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
	result := decision with input as candidate
	result.directive == "continue"
	result.pillar_results.trajectory.raw_facts.consecutive_tool_failures == 2
	object.get(result.rejected_alternatives, "consecutive_tool_failures", null) == null
}

test_consecutive_tool_failures_review_threshold if {
	trajectory := object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"consecutive_tool_failures": 4})})
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
	result := decision with input as candidate
	result.directive == "wait_human_review"
	"consecutive_tool_failures" in result.reason_codes
}

test_action_churn_requires_review if {
	trajectory := object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"action_churn_count": 2, "external_candidate": true})})
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
	result := decision with input as candidate
	result.directive == "wait_human_review"
	"action_churn" in result.reason_codes
}

test_old_action_churn_does_not_block_nonexternal_progress if {
	trajectory := object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"action_churn_count": 9, "external_candidate": false})})
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
	result := decision with input as candidate
	result.directive == "continue"
	result.pillar_results.trajectory.action_churn == false
	object.get(result.rejected_alternatives, "action_churn", null) == null
}

test_evidence_request_limit_reached_requires_review if {
        evidence := object.union(facts_evidence, {"raw_facts": object.union(facts_evidence.raw_facts, {"selected_evidence_refs_valid": false, "evidence_request_count": 3})})
	candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": evidence})})
	result := decision with input as candidate
	result.directive == "wait_human_review"
	"evidence_request_limit_reached" in result.reason_codes
        "invalid_or_missing_selected_evidence" in object.keys(result.rejected_alternatives)
}

test_whole_run_evidence_count_does_not_exhaust_fresh_episode if {
        evidence := object.union(facts_evidence, {"raw_facts": object.union(facts_evidence.raw_facts, {"selected_evidence_refs_valid": false, "evidence_request_count": 9, "evidence_request_episode_count": 1})})
        candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": evidence})})
        result := decision with input as candidate
        result.directive == "request_more_evidence"
        "invalid_or_missing_selected_evidence" in result.reason_codes
}

test_current_evidence_episode_reaches_review_ceiling if {
        evidence := object.union(facts_evidence, {"raw_facts": object.union(facts_evidence.raw_facts, {"selected_evidence_refs_valid": false, "evidence_request_count": 9, "evidence_request_episode_count": 3})})
        candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": evidence})})
        result := decision with input as candidate
        result.directive == "wait_human_review"
        "evidence_request_limit_reached" in result.reason_codes
}
