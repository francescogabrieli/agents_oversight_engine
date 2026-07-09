package layer2.governance

base_input := {
  "schema_version": "governance_decision_input.v1",
  "policy_config": {
    "thresholds_version": "test_thresholds_v1",
    "thresholds": {
      "min_actor_confidence": 0.65,
      "min_evidence_quality": 0.7,
      "max_operational_risk": 0.6,
      "min_trajectory_health": 0.6,
      "judge_reject_confidence": 0.7,
      "judge_flag_external_confidence": 0.8,
      "behavioral_high_novelty": 0.7,
      "behavioral_high_deviation": 0.8,
      "provider_failure_review_count": 3
    },
    "evidence_weights": {
      "evidence_sufficient": 0.3,
      "objective_satisfied": 0.2,
      "actor_confidence": 0.2,
      "observed_evidence_present": 0.15,
      "source_locator_present": 0.1,
      "last_tool_success": 0.05
    },
    "risk_weights": {
      "tool_risk": {"none": 0, "low": 0.15, "medium": 0.45, "high": 0.75, "critical": 0.95},
      "sensitivity_multiplier": {"low": 1, "medium": 1.15, "high": 1.35, "critical": 1.6},
      "external_impact_additive": 0.25,
      "write_capability_additive": 0.3,
      "requires_human_review_additive": 0.4
    },
    "trajectory_weights": {
      "max_actor_turns_per_step": 5,
      "max_replans": 2,
      "actor_turn_overage_penalty": 0.1,
      "completion_attempt_turn_overage_scale": 0.5,
      "failed_tool_result_penalty": 0.2,
      "needs_replan_penalty": 0.25,
      "low_confidence_uncertainty_penalty": 0.2
    }
  },
  "base_facts": {
    "task": {"domain_type": "research", "domain_label": "test", "sensitivity": "medium"},
    "policy": {"profile_name": "test", "confidence_threshold": 0.65, "read_only_mode": false, "budget": {"max_steps": 4, "max_tool_calls": 4}},
    "runtime": {"state": "acting", "flags": [], "actor_turn_count": 1, "tool_call_count": 0, "observed_evidence_count": 1},
    "step": {"id": "step_1", "objective": "Test step", "step_kind": "high_level_summary", "evidence_requirement": "high_level_summary", "claim_precision_ceiling": "high_level_summary"},
    "actor": {"result_type": "final_output", "capability_mode": "none", "external_impact": false, "blocking_reason": null, "has_output_text": true},
    "tool": {"name": null, "has_query": false, "has_url": false, "is_read_only": null},
    "external_communication": {"phase": null, "review_ready": false},
    "step_validation": {"objective_satisfied": true, "evidence_sufficient": true, "confidence": 0.9, "next_action": "complete_step"}
  },
  "pillar_requirements": {"require_evidence": true, "require_risk": true, "require_trajectory": true, "require_behavioral": true, "require_judge": false},
  "pillars": {
    "evidence": {
      "raw_facts": {"actor_evidence_sufficient": true, "actor_objective_satisfied": true, "actor_confidence": 0.9, "meaningful_observed_evidence_count": 1, "observed_evidence_present": true, "source_locator_present": true, "last_tool_succeeded": true, "support_level_required": "high_level_summary", "support_level_available": "high_level_summary", "support_rank_required": 2, "support_rank_available": 2, "precision_mismatch": false, "output_claim_support_level": "high_level_summary"},
      "score_components": {},
      "score_formula_version": "test",
      "thresholds_version": "test_thresholds_v1"
    },
    "risk": {
      "raw_facts": {"tool_risk_level": "none", "tool_risk_base": 0, "sensitivity_level": "medium", "sensitivity_multiplier": 1.15, "external_impact": false, "write_capability": false, "tool_requires_human_review": false},
      "score_components": {},
      "score_formula_version": "test",
      "thresholds_version": "test_thresholds_v1"
    },
    "trajectory": {
      "raw_facts": {"actor_turn_count_for_step": 1, "max_actor_turns_per_step": 5, "replan_count": 0, "max_replans": 2, "failed_tool_count": 0, "needs_replan_signal": false, "low_confidence_uncertainty": false, "completion_attempt": true, "consecutive_provider_failures": 0, "repeated_recovery": false, "provider_runtime_action_missing": false},
      "score_components": {},
      "score_formula_version": "test",
      "thresholds_version": "test_thresholds_v1"
    },
    "behavioral": {
      "raw_facts": {"history_available": true, "historical_run_count": 20, "matched_run_count": 5, "similarity_score": 0.7, "deviation_score": 0.1, "novelty_score": 0.1, "historical_failure_rate": 0.1, "historical_review_rate": 0.1, "deviation_driver": null},
      "score_components": {},
      "score_formula_version": "test",
      "thresholds_version": "test_thresholds_v1"
    },
    "judge": {
      "raw_facts": {"invoked": false, "required": false, "verdict": "approve", "confidence": 0, "concerns": [], "summary": "Not invoked."},
      "score_components": {},
      "score_formula_version": "test",
      "thresholds_version": "test_thresholds_v1"
    }
  },
  "pillar_errors": {}
}

test_static_write_blocked_deny if {
  result := decision with input as object.union_n([base_input, {"base_facts": object.union(base_input.base_facts, {"actor": object.union(base_input.base_facts.actor, {"capability_mode": "write"})})}])
  result.directive == "deny"
}

test_external_review_ready_waits_human_review if {
  next := object.union_n([base_input, {"base_facts": object.union(base_input.base_facts, {"task": object.union(base_input.base_facts.task, {"domain_type": "external_communication"}), "external_communication": {"phase": "draft_proposal", "review_ready": true}})}])
  result := decision with input as next
  result.directive == "wait_human_review"
}

test_actor_ask_clarification if {
  next := object.union_n([base_input, {"base_facts": object.union(base_input.base_facts, {"actor": object.union(base_input.base_facts.actor, {"result_type": "ask_clarification"})})}])
  result := decision with input as next
  result.directive == "ask_user_clarification"
}

test_evidence_strong_continues if {
  result := decision with input as base_input
  result.directive == "continue"
}

test_evidence_support_mismatch_requests_more_evidence if {
  evidence := object.union(base_input.pillars.evidence, {"raw_facts": object.union(base_input.pillars.evidence.raw_facts, {"support_rank_available": 1, "support_level_available": "source_discovery"})})
  next := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": evidence})})
  result := decision with input as next
  result.directive == "request_more_evidence"
}

test_evidence_mismatch_and_trajectory_degraded_replans if {
  evidence := object.union(base_input.pillars.evidence, {"raw_facts": object.union(base_input.pillars.evidence.raw_facts, {"support_rank_available": 1})})
  trajectory := object.union(base_input.pillars.trajectory, {"raw_facts": object.union(base_input.pillars.trajectory.raw_facts, {"needs_replan_signal": true})})
  next := object.union(base_input, {"pillars": object.union(base_input.pillars, {"evidence": evidence, "trajectory": trajectory})})
  result := decision with input as next
  result.directive == "replan"
}

test_risk_high_external_waits_human_review if {
  risk := object.union(base_input.pillars.risk, {"raw_facts": object.union(base_input.pillars.risk.raw_facts, {"tool_risk_level": "high", "external_impact": true})})
  next := object.union_n([base_input, {"base_facts": object.union(base_input.base_facts, {"actor": object.union(base_input.base_facts.actor, {"external_impact": false})}), "pillars": object.union(base_input.pillars, {"risk": risk})}])
  result := decision with input as next
  result.directive == "wait_human_review"
}

test_replan_limit_exceeded_stops if {
  trajectory := object.union(base_input.pillars.trajectory, {"raw_facts": object.union(base_input.pillars.trajectory.raw_facts, {"replan_count": 2})})
  next := object.union(base_input, {"pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
  result := decision with input as next
  result.directive == "stop"
}

test_provider_failures_wait_human_review if {
  trajectory := object.union(base_input.pillars.trajectory, {"raw_facts": object.union(base_input.pillars.trajectory.raw_facts, {"consecutive_provider_failures": 3})})
  next := object.union(base_input, {"pillars": object.union(base_input.pillars, {"trajectory": trajectory})})
  result := decision with input as next
  result.directive == "wait_human_review"
}

test_behavioral_anomaly_alone_no_hitl if {
  behavioral := object.union(base_input.pillars.behavioral, {"raw_facts": object.union(base_input.pillars.behavioral.raw_facts, {"deviation_score": 0.9})})
  next := object.union(base_input, {"pillars": object.union(base_input.pillars, {"behavioral": behavioral})})
  result := decision with input as next
  result.directive != "wait_human_review"
}

test_behavioral_correlated_can_wait_human_review if {
  behavioral := object.union(base_input.pillars.behavioral, {"raw_facts": object.union(base_input.pillars.behavioral.raw_facts, {"deviation_score": 0.9})})
  risk := object.union(base_input.pillars.risk, {"raw_facts": object.union(base_input.pillars.risk.raw_facts, {"tool_risk_level": "high"})})
  next := object.union(base_input, {"pillars": object.union(base_input.pillars, {"behavioral": behavioral, "risk": risk})})
  result := decision with input as next
  result.directive == "wait_human_review"
}

test_judge_reject_no_deny if {
  judge_pillar := object.union(base_input.pillars.judge, {"raw_facts": object.union(base_input.pillars.judge.raw_facts, {"invoked": true, "verdict": "reject_output", "confidence": 0.8})})
  next := object.union(base_input, {"pillars": object.union(base_input.pillars, {"judge": judge_pillar})})
  result := decision with input as next
  result.directive != "deny"
}

test_judge_flag_external_draft_waits_human_review if {
  judge_pillar := object.union(base_input.pillars.judge, {"raw_facts": object.union(base_input.pillars.judge.raw_facts, {"invoked": true, "verdict": "flag", "confidence": 0.85})})
  next := object.union_n([base_input, {"base_facts": object.union(base_input.base_facts, {"external_communication": {"phase": "draft_proposal", "review_ready": false}}), "pillars": object.union(base_input.pillars, {"judge": judge_pillar})}])
  result := decision with input as next
  result.directive == "wait_human_review"
}

test_read_only_envelope_preserved if {
  next := object.union_n([base_input, {"base_facts": object.union(base_input.base_facts, {"policy": object.union(base_input.base_facts.policy, {"read_only_mode": true})})}])
  result := decision with input as next
  result.directive == "continue_read_only"
}
