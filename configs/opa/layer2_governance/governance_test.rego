package layer2.governance

import rego.v1

local_operation := {"operation_id": null, "boundary": "local", "operation_family": "generic", "normalized_effect": "read", "execution_candidate": false, "target": {"kind": "local", "value": "runtime", "destination_class": "internal"}, "payload_digest": null, "capability_profile_id": null, "payload": {"payload_type": "none"}}

base_facts := {
  "task": {"sensitivity": "medium"},
  "policy": {"profile_name": "default", "confidence_threshold": 0.65, "read_only_mode": false, "budget": {"max_steps": 5, "max_tool_calls": 5}},
  "runtime": {"state": "acting", "flags": [], "actor_turn_count": 1, "tool_call_count": 0, "observation_count": 2},
  "step": {"id": "step-1", "objective": "Perform the step", "rationale": "needed", "expected_outcome": "done"},
  "actor": {"result_type": "step_completed", "capability_mode": "none", "external_impact": false, "blocking_reason": null, "has_output_text": true, "actor_self_confidence": 0.9},
  "tool": {"name": null, "has_query": false, "has_url": false, "is_read_only": null},
  "external_operation": local_operation,
  "external_communication": {"phase": null, "review_ready": false}
}

facts_evidence := {"raw_facts": {"completion_attempt": true, "factual_completion_attempt": true, "evidence_count": 2, "selected_evidence_ref_count": 1, "selected_evidence_refs_valid": true, "source_locator_present": true, "cited_context_present": true, "extracted_content_present": true, "official_source_present": false, "extraction_truncated": false, "extractability_limited": false, "external_operation_candidate": false, "actor_self_confidence": 0.9}, "score_components": {}, "score_formula_version": "evidence_facts_v3", "thresholds_version": "layer2_governance_thresholds_v2"}
facts_trajectory := {"raw_facts": {"actor_turn_count": 4, "completed_step_count": 1, "tool_call_count": 2, "successful_tool_call_count": 2, "failed_tool_call_count": 0, "observation_count": 2, "replan_count": 0, "consecutive_provider_protocol_failures": 0, "needs_replan_signal": false, "replan_reason_present": false, "first_replan_request": true, "observable_change_since_last_replan": true, "completion_signal": true, "external_candidate": false, "external_action_revision_count": 0, "actor_self_confidence": 0.9, "configured_confidence_threshold": 0.65}, "score_components": {}, "score_formula_version": "trajectory_facts_v4", "thresholds_version": "layer2_governance_thresholds_v2"}
shadow := {"raw_facts": {"mode": "shadow", "decision_influence": false, "pillar_unavailable": false}, "score_components": {}, "score_formula_version": "shadow", "thresholds_version": "layer2_governance_thresholds_v2"}
no_judge := {"raw_facts": {"invoked": false, "required": false, "status": "not_applicable", "pillar_unavailable": false, "verdict": "uncertain", "severity": "low", "digest_matches": false}, "score_components": {}, "score_formula_version": "judge_v4", "thresholds_version": "layer2_governance_thresholds_v2"}

base_input := {"schema_version": "governance_decision_input.v1", "policy_bundle_version": "layer2_governance_v4", "policy_config_version": "layer2_governance_thresholds_v2", "base_facts": base_facts, "pillar_requirements": {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false}, "pillars": {"evidence": facts_evidence, "trajectory": facts_trajectory, "risk": shadow, "behavioral": shadow, "judge": no_judge}, "pillar_unavailable": {"evidence": false, "trajectory": false, "risk": false, "behavioral": false, "judge": false}, "pillar_errors": {}}

governed_operation := {"operation_id": "op-1", "boundary": "governed_external", "operation_family": "communication", "normalized_effect": "send", "execution_candidate": true, "target": {"kind": "recipient", "value": "ops@example.com", "destination_class": "external"}, "payload_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "capability_profile_id": "communication.default", "payload": {"payload_type": "communication", "recipient": "ops@example.com"}}
safe_judge := {"raw_facts": {"invoked": true, "required": true, "status": "active", "pillar_unavailable": false, "verdict": "safe", "severity": "low", "digest_matches": true}, "score_components": {}, "score_formula_version": "judge_v4", "thresholds_version": "layer2_governance_thresholds_v2"}

test_intermediate_output_requires_no_active_semantic_pillars if {
  facts := object.union(base_facts, {"actor": object.union(base_facts.actor, {"result_type": "intermediate_output"})})
  result := required_pillars with input as {"base_facts": facts}
  result.require_evidence == false
  result.require_trajectory == false
  result.require_judge == false
}

test_read_only_tool_request_requires_static_policy_only if {
  facts := object.union(base_facts, {"actor": object.union(base_facts.actor, {"result_type": "propose_action", "capability_mode": "read_only"})})
  result := required_pillars with input as {"base_facts": facts}
  result.require_evidence == false
  result.require_trajectory == false
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

test_governed_external_candidate_also_requires_judge if {
  facts := object.union(base_facts, {"external_operation": governed_operation})
  result := required_pillars with input as {"base_facts": facts}
  result.require_evidence == true
  result.require_trajectory == true
  result.require_judge == true
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
