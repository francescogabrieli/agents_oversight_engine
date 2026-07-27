package layer2.governance

import rego.v1

local_operation := {"operation_id": null, "boundary": "local", "operation_family": "generic", "normalized_effect": "read", "execution_candidate": false, "target": {"kind": "local", "value": "runtime", "destination_class": "internal"}, "payload_digest": null, "capability_profile_id": null, "payload": {"payload_type": "none"}}

base_facts := {
  "task": {"domain_type": "research", "domain_label": "generic", "sensitivity": "medium"},
  "policy": {"profile_name": "default", "confidence_threshold": 0.65, "read_only_mode": false, "budget": {"max_steps": 5, "max_tool_calls": 5}},
  "runtime": {"state": "acting", "flags": [], "actor_turn_count": 1, "tool_call_count": 0, "observed_evidence_count": 2},
  "step": {"id": "step-1", "objective": "Perform the step", "rationale": "needed", "expected_outcome": "done"},
  "actor": {"result_type": "step_completed", "capability_mode": "none", "external_impact": false, "blocking_reason": null, "has_output_text": true, "actor_self_confidence": 0.9},
  "tool": {"name": null, "has_query": false, "has_url": false, "is_read_only": null},
  "external_operation": local_operation,
  "external_communication": {"phase": null, "review_ready": false}
}

facts_evidence := {"raw_facts": {"completion_attempt": true, "factual_completion_attempt": true, "evidence_count": 2, "selected_evidence_ref_count": 1, "selected_evidence_refs_valid": true, "source_locator_present": true, "cited_context_present": true, "extracted_content_present": true, "official_source_present": false, "extraction_truncated": false, "extractability_limited": false, "external_operation_candidate": false, "actor_self_confidence": 0.9}, "score_components": {}, "score_formula_version": "evidence_facts_v3", "thresholds_version": "layer2_governance_thresholds_v1"}
facts_trajectory := {"raw_facts": {"consecutive_no_progress_turns": 0, "new_evidence_since_last_turn": true, "tool_call_since_last_turn": false, "plan_step_advanced": false, "draft_digest_changed": false, "equivalent_tool_failures_for_host": 0, "consecutive_provider_protocol_failures": 0, "replan_budget_remaining": 2, "recovery_exhausted": false, "actor_self_confidence": 0.9, "configured_confidence_threshold": 0.65}, "score_components": {}, "score_formula_version": "trajectory_facts_v3", "thresholds_version": "layer2_governance_thresholds_v1"}
shadow := {"raw_facts": {"mode": "shadow", "decision_influence": false, "pillar_unavailable": false}, "score_components": {}, "score_formula_version": "shadow", "thresholds_version": "layer2_governance_thresholds_v1"}
no_judge := {"raw_facts": {"invoked": false, "required": false, "status": "not_applicable", "pillar_unavailable": false, "verdict": "uncertain", "severity": "low", "digest_matches": false}, "score_components": {}, "score_formula_version": "judge_v3", "thresholds_version": "layer2_governance_thresholds_v1"}

base_input := {"schema_version": "governance_decision_input.v1", "policy_bundle_version": "layer2_governance_v3", "policy_config_version": "layer2_governance_thresholds_v1", "base_facts": base_facts, "pillar_requirements": {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false}, "pillars": {"evidence": facts_evidence, "trajectory": facts_trajectory, "risk": shadow, "behavioral": shadow, "judge": no_judge}, "pillar_unavailable": {"evidence": false, "trajectory": false, "risk": false, "behavioral": false, "judge": false}, "pillar_errors": {}}

governed_operation := {"operation_id": "op-1", "boundary": "governed_external", "operation_family": "communication", "normalized_effect": "send", "execution_candidate": true, "target": {"kind": "recipient", "value": "ops@example.com", "destination_class": "external"}, "payload_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "capability_profile_id": "communication.default", "payload": {"payload_type": "communication", "recipient": "ops@example.com"}}
safe_judge := {"raw_facts": {"invoked": true, "required": true, "status": "active", "pillar_unavailable": false, "verdict": "safe", "severity": "low", "digest_matches": true}, "score_components": {}, "score_formula_version": "judge_v3", "thresholds_version": "layer2_governance_thresholds_v1"}

test_read_only_tool_request_skips_evidence if {
  candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"actor": object.union(base_facts.actor, {"result_type": "propose_action", "capability_mode": "read_only"}), "tool": {"name": "web_search", "has_query": true, "has_url": false, "is_read_only": true}}), "pillar_requirements": {"require_evidence": false, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false}})
  result := decision with input as candidate
  result.directive == "continue"
}

test_invalid_selected_evidence_requests_more_evidence if {
  candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": object.union(facts_evidence, {"raw_facts": object.union(facts_evidence.raw_facts, {"selected_evidence_refs_valid": false})})})})
  result := decision with input as candidate
  result.directive == "request_more_evidence"
}

test_static_hard_deny_is_absorbing if {
  operation := object.union(governed_operation, {"target": {"kind": "recipient", "value": "blocked@example.com", "destination_class": "external"}})
  candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": operation}), "pillar_requirements": {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": true}})
  result := decision with input as candidate
  result.directive == "stop"
}

test_external_candidate_requires_judge_and_low_confidence_review if {
  candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation, "actor": object.union(base_facts.actor, {"capability_mode": "write", "external_impact": true, "actor_self_confidence": 0.2})}), "pillar_requirements": {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": true}, "pillars": object.union(base_input.pillars, {"judge": safe_judge, "trajectory": object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"actor_self_confidence": 0.2})})})})
  result := decision with input as candidate
  result.directive == "wait_human_review"
  "external_low_actor_self_confidence" in result.reason_codes
}

test_low_confidence_alone_keeps_progressing if {
  candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"actor": object.union(base_facts.actor, {"actor_self_confidence": 0.2})}), "pillars": object.union(base_input.pillars, {"trajectory": object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"actor_self_confidence": 0.2})})})})
  result := decision with input as candidate
  result.directive == "continue"
}

test_low_confidence_with_no_progress_replans if {
  candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"trajectory": object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"actor_self_confidence": 0.2, "consecutive_no_progress_turns": 1, "new_evidence_since_last_turn": false})})})})
  result := decision with input as candidate
  result.directive == "replan"
}

test_recovery_exhaustion_requires_review_not_deny if {
  candidate := object.union(base_input, {"pillars": object.union(base_input.pillars, {"trajectory": object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"recovery_exhausted": true})})})})
  result := decision with input as candidate
  result.directive == "wait_human_review"
  result.policy_decision != "deny"
}

test_action_revision_limit_requires_review_not_another_revision if {
  candidate := object.union(base_input, {"base_facts": object.union(base_facts, {"external_operation": governed_operation}), "pillar_requirements": {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": true}, "pillars": object.union(base_input.pillars, {"judge": object.union(safe_judge, {"raw_facts": object.union(safe_judge.raw_facts, {"verdict": "needs_revision"})}), "trajectory": object.union(facts_trajectory, {"raw_facts": object.union(facts_trajectory.raw_facts, {"external_action_revision_count": 2})})})})
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
