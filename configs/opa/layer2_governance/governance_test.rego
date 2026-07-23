package layer2.governance

import rego.v1

local_operation := {
	"operation_id": null,
	"boundary": "local",
	"operation_family": "generic",
	"normalized_effect": "read",
	"execution_candidate": false,
	"target": {"kind": "local", "value": "runtime", "destination_class": "internal"},
	"payload_digest": null,
	"capability_profile_id": null,
	"payload": {"payload_type": "none"},
}

base_facts := {
	"task": {"domain_type": "analysis", "domain_label": "generic", "sensitivity": "medium"},
	"policy": {"profile_name": "default", "confidence_threshold": 0.65, "read_only_mode": false, "budget": {"max_steps": 5, "max_tool_calls": 5}},
	"runtime": {"state": "acting", "flags": [], "actor_turn_count": 1, "tool_call_count": 0, "observed_evidence_count": 2},
	"step": {"id": "step-1", "objective": "Perform the step", "step_kind": "local_transformation", "evidence_requirement": "local_only", "claim_precision_ceiling": "local_only"},
	"actor": {"result_type": "final_output", "capability_mode": "none", "external_impact": false, "blocking_reason": null, "has_output_text": true},
	"tool": {"name": null, "has_query": false, "has_url": false, "is_read_only": null},
	"external_operation": local_operation,
	"external_communication": {"phase": null, "review_ready": false},
	"step_validation": {"objective_satisfied": true, "evidence_sufficient": true, "confidence": 0.9, "next_action": "complete_step"},
}

strong_evidence := {
	"raw_facts": {"actor_evidence_sufficient": true, "actor_objective_satisfied": true, "actor_confidence": 0.9, "observed_evidence_present": true, "source_locator_present": true, "last_tool_succeeded": true, "support_rank_required": 0, "support_rank_available": 3, "precision_mismatch": false},
	"score_components": {},
	"score_formula_version": "evidence_quality_v2_raw_facts",
	"thresholds_version": "layer2_governance_thresholds_v1",
}

healthy_trajectory := {
	"raw_facts": {"actor_turn_count_for_step": 1, "replan_count": 0, "failed_tool_count": 0, "needs_replan_signal": false, "low_confidence_uncertainty": false, "completion_attempt": true, "provider_runtime_action_missing": false, "consecutive_provider_failures": 0, "repeated_recovery": false, "action_revision_count": 0},
	"score_components": {},
	"score_formula_version": "trajectory_health_v2_raw_facts",
	"thresholds_version": "layer2_governance_thresholds_v1",
}

shadow_risk := {
	"raw_facts": {"mode": "shadow", "decision_influence": false, "applicability": "not_applicable", "pillar_unavailable": false},
	"diagnostic_score": null,
	"diagnostic_level": "not_applicable",
	"score_components": {},
	"score_formula_version": "operational_consequence_shadow_v1",
	"thresholds_version": "layer2_governance_thresholds_v1",
}

shadow_behavioral := {
	"raw_facts": {"mode": "shadow", "decision_influence": false, "status": "shadow", "pillar_unavailable": false, "deviation_score": 0.1, "novelty_score": 0.1},
	"diagnostic_score": 0.1,
	"score_components": {},
	"score_formula_version": "behavioral_features_v2",
	"thresholds_version": "layer2_governance_thresholds_v1",
}

not_required_judge := {
	"raw_facts": {"invoked": false, "required": false, "status": "not_applicable", "pillar_unavailable": false, "verdict": "uncertain", "severity": "low", "confidence": 0, "findings": [], "evaluated_operation_digest": null, "expected_operation_digest": null, "digest_matches": false},
	"score_components": {},
	"score_formula_version": "external_operation_semantic_judge_v2",
	"thresholds_version": "layer2_governance_thresholds_v1",
}

base_input := {
	"schema_version": "governance_decision_input.v1",
	"policy_bundle_version": "layer2_governance_v3",
	"policy_config_version": "layer2_governance_thresholds_v1",
	"base_facts": base_facts,
	"pillar_requirements": {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false},
	"pillars": {"evidence": strong_evidence, "risk": shadow_risk, "trajectory": healthy_trajectory, "behavioral": shadow_behavioral, "judge": not_required_judge},
	"pillar_unavailable": {"evidence": false, "risk": false, "trajectory": false, "behavioral": false, "judge": false},
	"pillar_errors": {},
}

governed_operation(effect, family, profile, payload) := {
	"operation_id": "op-1",
	"boundary": "governed_external",
	"operation_family": family,
	"normalized_effect": effect,
	"execution_candidate": true,
	"target": {"kind": "recipient", "value": "ops@example.com", "destination_class": "external"},
	"payload_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
	"capability_profile_id": profile,
	"payload": payload,
}

safe_judge := {
	"raw_facts": {"invoked": true, "required": true, "status": "active", "pillar_unavailable": false, "verdict": "safe", "severity": "low", "confidence": 0.95, "findings": [], "evaluated_operation_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "expected_operation_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "digest_matches": true},
	"score_components": {},
	"score_formula_version": "external_operation_semantic_judge_v2",
	"thresholds_version": "layer2_governance_thresholds_v1",
}

external_input(operation, judge_payload) := object.union_n([
	base_input,
	{"base_facts": object.union(base_facts, {"external_operation": operation, "actor": object.union(base_facts.actor, {"capability_mode": "write", "external_impact": true})})},
	{"pillar_requirements": {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": true}},
	{"pillars": object.union(base_input.pillars, {"judge": judge_payload})},
])

test_external_and_write_alone_do_not_deny_or_hitl if {
	op := governed_operation("send", "communication", "communication.default", {"payload_type": "communication", "recipient": "ops@example.com"})
	result := decision with input as external_input(op, safe_judge)
	result.directive == "continue"
	result.policy_decision == "allow"
}

test_read_only_mode_and_mutation_hard_denies if {
	op := governed_operation("update", "generic", "generic.default", {"payload_type": "generic"})
	candidate := external_input(op, safe_judge)
	result := decision with input as object.union(candidate, {"base_facts": object.union(candidate.base_facts, {"policy": object.union(base_facts.policy, {"read_only_mode": true})})})
	result.directive == "stop"
	result.policy_decision == "deny"
	"read_only_mutation_blocked" in result.reason_codes
}

test_sql_select_passes_static_and_requires_judge if {
	op := governed_operation("read", "database", "enterprise_db.read_only", {"payload_type": "database", "statement_class": "select", "ambiguous": false})
	requirements := required_pillars with input as {"base_facts": object.union(base_facts, {"external_operation": op})}
	requirements.require_judge == true
	result := decision with input as external_input(op, safe_judge)
	result.pillar_results.static_policy.hard_deny == false
	result.directive == "continue_read_only"
}

test_sql_mutations_and_ambiguous_multiple_are_denied if {
	effects := {"update", "delete", "truncate"}
	some effect in effects
	op := governed_operation(effect, "database", "enterprise_db.read_only", {"payload_type": "database", "statement_class": effect, "ambiguous": false})
	result := decision with input as external_input(op, safe_judge)
	result.directive == "stop"
}

test_sql_multiple_is_conservatively_denied if {
	op := governed_operation("unknown", "database", "enterprise_db.read_only", {"payload_type": "database", "statement_class": "multiple", "ambiguous": true})
	result := decision with input as external_input(op, safe_judge)
	result.directive == "stop"
	"sql_ambiguous_or_multiple" in result.reason_codes
}

test_email_tone_is_not_static_policy if {
	op := governed_operation("send", "communication", "communication.default", {"payload_type": "communication", "recipient": "ops@example.com", "subject": "Threatening content"})
	result := decision with input as external_input(op, safe_judge)
	result.pillar_results.static_policy.hard_deny == false
}

test_prohibited_destination_hard_denies_without_judge if {
	op := object.union(governed_operation("send", "communication", "communication.default", {"payload_type": "communication", "recipient": "blocked@example.com"}), {"target": {"kind": "recipient", "value": "blocked@example.com", "destination_class": "external"}})
	requirements := required_pillars with input as {"base_facts": object.union(base_facts, {"external_operation": op})}
	requirements.require_judge == false
	result := decision with input as external_input(op, safe_judge)
	result.directive == "stop"
	"prohibited_destination" in result.reason_codes
}

test_evidence_read_and_local_do_not_require_judge if {
	evidence_op := object.union(governed_operation("read", "generic", "public_evidence.web_search", {"payload_type": "generic"}), {"boundary": "evidence_read"})
	evidence_requirements := required_pillars with input as {"base_facts": object.union(base_facts, {"external_operation": evidence_op})}
	local_requirements := required_pillars with input as {"base_facts": base_facts}
	evidence_requirements.require_judge == false
	local_requirements.require_judge == false
}

test_judge_required_unavailable_fails_closed if {
	op := governed_operation("send", "communication", "communication.default", {"payload_type": "communication"})
	unavailable := object.union(safe_judge, {"raw_facts": object.union(safe_judge.raw_facts, {"invoked": false, "status": "unavailable", "pillar_unavailable": true, "digest_matches": false})})
	candidate := external_input(op, unavailable)
	result := decision with input as object.union(candidate, {"pillar_unavailable": object.union(candidate.pillar_unavailable, {"judge": true})})
	result.directive == "wait_human_review"
	"judge_required_unavailable" in result.reason_codes
}

test_judge_needs_revision_maps_to_revise_action if {
	op := governed_operation("send", "communication", "communication.default", {"payload_type": "communication"})
	revision := object.union(safe_judge, {"raw_facts": object.union(safe_judge.raw_facts, {"verdict": "needs_revision", "findings": [{"code": "tone_mismatch"}]})})
	result := decision with input as external_input(op, revision)
	result.directive == "revise_action"
}

test_judge_unsafe_and_uncertain_map_to_hitl_not_deny if {
	op := governed_operation("execute", "generic", "generic.default", {"payload_type": "generic"})
	verdicts := {"unsafe", "uncertain"}
	some verdict in verdicts
	assessment := object.union(safe_judge, {"raw_facts": object.union(safe_judge.raw_facts, {"verdict": verdict})})
	result := decision with input as external_input(op, assessment)
	result.directive == "wait_human_review"
	result.policy_decision == "require_human_review"
}

test_behavioral_and_risk_shadow_changes_do_not_change_directive if {
	baseline := decision with input as base_input
	changed_pillars := object.union(base_input.pillars, {
		"behavioral": object.union(shadow_behavioral, {"diagnostic_score": 1, "raw_facts": object.union(shadow_behavioral.raw_facts, {"deviation_score": 1, "novelty_score": 1})}),
		"risk": object.union(shadow_risk, {"diagnostic_level": "critical", "raw_facts": object.union(shadow_risk.raw_facts, {"impact_level": "critical"})}),
	})
	changed := decision with input as object.union(base_input, {"pillars": changed_pillars})
	baseline.directive == changed.directive
	baseline.directive == "continue"
}

test_shadow_failures_do_not_fail_closed if {
	shadow_failures := object.union(base_input, {
		"pillar_unavailable": object.union(base_input.pillar_unavailable, {"risk": true, "behavioral": true}),
	})
	result := decision with input as shadow_failures
	result.directive == "continue"
}

test_active_evidence_failure_fails_closed if {
	failed := object.union(base_input, {"pillar_unavailable": object.union(base_input.pillar_unavailable, {"evidence": true})})
	result := decision with input as failed
	result.directive == "wait_human_review"
	"required_pillar_unavailable" in result.reason_codes
}

test_evidence_and_trajectory_authority_is_preserved if {
	gap_facts := object.union(strong_evidence.raw_facts, {"support_rank_required": 3, "support_rank_available": 1})
	gap_pillars := object.union(base_input.pillars, {"evidence": object.union(strong_evidence, {"raw_facts": gap_facts})})
	gap_result := decision with input as object.union(base_input, {"pillars": gap_pillars})
	gap_result.directive == "request_more_evidence"

	degraded_facts := object.union(healthy_trajectory.raw_facts, {"needs_replan_signal": true})
	degraded_pillars := object.union(base_input.pillars, {"trajectory": object.union(healthy_trajectory, {"raw_facts": degraded_facts})})
	degraded_result := decision with input as object.union(base_input, {"pillars": degraded_pillars})
	degraded_result.directive == "replan"
}

test_revision_loop_contributes_to_trajectory if {
	revision_facts := object.union(healthy_trajectory.raw_facts, {"action_revision_count": 3})
	revision_pillars := object.union(base_input.pillars, {"trajectory": object.union(healthy_trajectory, {"raw_facts": revision_facts})})
	result := decision with input as object.union(base_input, {"pillars": revision_pillars})
	result.directive == "wait_human_review"
	"repeated_action_revision" in result.reason_codes
}
