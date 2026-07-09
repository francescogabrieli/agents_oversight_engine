package layer2.governance

import data.layer2.governance.pillars.static_policy
import data.layer2.governance.pillars.evidence
import data.layer2.governance.pillars.risk
import data.layer2.governance.pillars.trajectory
import data.layer2.governance.pillars.behavioral
import data.layer2.governance.pillars.judge

default required_pillars := {
  "require_evidence": true,
  "require_risk": true,
  "require_trajectory": true,
  "require_behavioral": true,
  "require_judge": false
}

required_pillars := {
  "require_evidence": true,
  "require_risk": true,
  "require_trajectory": true,
  "require_behavioral": true,
  "require_judge": require_judge
}

require_judge if {
  input.external_communication.review_ready == true
}

require_judge if {
  input.actor.result_type == "final_output"
  input.runtime.observed_evidence_count == 0
}

require_judge if {
  input.actor.result_type == "intermediate_output"
  input.external_communication.phase == "draft_proposal"
}

require_judge if {
  input.actor.has_output_text == true
  input.actor.result_type == "intermediate_output"
  input.runtime.observed_evidence_count <= 1
}

default decision := continue_decision

decision := deny_decision if {
  static_policy.result.write_blocked
}

decision := hitl_decision("external_communication_review_required", "External communication requires typed human review.") if {
  not static_policy.result.write_blocked
  static_policy.result.hitl_mandatory
}

decision := clarification_decision if {
  not static_policy.result.write_blocked
  not static_policy.result.hitl_mandatory
  static_policy.result.clarification_signaled
}

decision := stop_decision if {
  not static_policy.result.write_blocked
  not static_policy.result.hitl_mandatory
  trajectory.result.replan_limit_exceeded
}

decision := hitl_decision("provider_failures_repeated", "Repeated provider failures require human review.") if {
  not terminal_static
  trajectory.result.provider_failures_repeated
}

decision := replan_decision("repeated_recovery", "Repeated recovery requires replanning.") if {
  not terminal_static
  trajectory.result.repeated_recovery
}

decision := replan_decision("provider_runtime_action_missing", "Provider returned no usable runtime action; replanning is the minimal recovery.") if {
  not terminal_static
  trajectory.result.provider_runtime_action_missing
}

decision := hitl_decision("judge_flag_external_draft", "Judge flagged an external draft with high confidence.") if {
  not terminal_static
  judge.result.external_flag_strong
}

decision := hitl_decision("judge_reject_external_or_high_risk", "Judge rejected output under external or high-risk conditions.") if {
  not terminal_static
  judge.result.reject_strong
  external_or_high_risk
}

decision := evidence_decision("judge_reject_output", "Judge rejected output; stronger evidence is required.") if {
  not terminal_static
  judge.result.reject_strong
  not external_or_high_risk
}

decision := hitl_decision("precision_mismatch_external_or_sensitive", "Precision mismatch affects an external or high-sensitivity outcome.") if {
  not terminal_static
  evidence.result.precision_mismatch
  external_or_high_risk
}

decision := replan_decision("evidence_gap_and_trajectory_degraded", "Evidence gap and degraded trajectory require replanning.") if {
  not terminal_static
  evidence.result.evidence_gap
  trajectory.result.degraded_or_replan
}

decision := replan_decision("trajectory_degraded", "Trajectory degradation requires replanning.") if {
  not terminal_static
  trajectory.result.degraded_or_replan
  not evidence.result.evidence_gap
}

decision := hitl_decision("high_operational_risk_external", "High operational risk with external impact requires human review.") if {
  not terminal_static
  risk.result.high
  risk.result.raw_facts.external_impact == true
}

decision := hitl_decision("behavioral_corroborated_risk", "Behavioral anomaly is corroborated by another strong signal.") if {
  not terminal_static
  behavioral.result.strong
  corroborating_strong_signal
}

decision := evidence_decision("behavioral_anomaly_needs_corroboration", "Behavioral anomaly alone can only request corroborating evidence.") if {
  not terminal_static
  behavioral.result.strong
  not corroborating_strong_signal
}

decision := evidence_decision("evidence_support_below_step_need", "Evidence support is below the current step requirement.") if {
  not terminal_static
  evidence.result.evidence_gap
}

decision := evidence_decision("precision_mismatch", "Output claim precision exceeds available evidence support.") if {
  not terminal_static
  evidence.result.precision_mismatch
}

decision := read_only_decision if {
  not terminal_static
  not evidence.result.evidence_gap
  not evidence.result.precision_mismatch
  not trajectory.result.degraded_or_replan
  not risk.result.high
  static_policy.result.read_only_required
}

terminal_static if {
  static_policy.result.write_blocked
}

terminal_static if {
  static_policy.result.hitl_mandatory
}

terminal_static if {
  static_policy.result.clarification_signaled
}

external_or_high_risk if {
  input.base_facts.actor.external_impact == true
}

external_or_high_risk if {
  input.base_facts.task.sensitivity == "high"
}

external_or_high_risk if {
  input.base_facts.task.sensitivity == "critical"
}

external_or_high_risk if {
  risk.result.high
}

corroborating_strong_signal if {
  evidence.result.evidence_gap
}

corroborating_strong_signal if {
  risk.result.high
}

corroborating_strong_signal if {
  trajectory.result.degraded_or_replan
}

corroborating_strong_signal if {
  judge.result.strong_signal
}

deny_decision := base_decision("deny", "deny", ["write_capability_blocked"], "Write capability or external impact is blocked by policy.", true)

clarification_decision := base_decision("ask_user_clarification", "ask_clarification", ["ask_clarification_required"], "The actor requested clarification.", true)

stop_decision := base_decision("stop", "deny", ["replan_limit_exceeded"], "Replan limit was exceeded.", false)

read_only_decision := base_decision("continue_read_only", "allow_read_only", ["read_only_allowed"], "Read-only envelope is preserved.", false)

continue_decision := base_decision("continue", "allow", ["allowed_by_policy"], "No material governance signal requires intervention.", false)

hitl_decision(code, reason) := base_decision("wait_human_review", "require_human_review", [code], reason, true)

replan_decision(code, reason) := base_decision("replan", "allow", [code], reason, false)

evidence_decision(code, reason) := base_decision("request_more_evidence", "allow", [code], reason, false)

base_decision(directive, policy_decision, reason_codes, selected_reason, requires_review) := {
  "directive": directive,
  "policy_decision": policy_decision,
  "reason_codes": reason_codes,
  "pillar_results": {
    "static_policy": static_policy.result,
    "evidence": evidence.result,
    "risk": risk.result,
    "trajectory": trajectory.result,
    "behavioral": behavioral.result,
    "judge": judge.result
  },
  "selected_reason": selected_reason,
  "rejected_alternatives": rejected_alternatives(directive),
  "safety_invariants_checked": [
    "deny_is_absorbing",
    "hitl_mandatory_not_downgraded",
    "read_only_envelope_preserved",
    "behavioral_cannot_escalate_alone",
    "judge_cannot_hard_deny"
  ],
  "requires_human_review": requires_review,
  "policy_bundle_version": "layer2_governance_v2"
}

rejected_alternatives("request_more_evidence") := {
  "wait_human_review": "Evidence gap is recoverable without mandatory external approval."
}

rejected_alternatives("replan") := {
  "request_more_evidence": "The trajectory is degraded enough that replanning is safer."
}

rejected_alternatives(_) := {}
