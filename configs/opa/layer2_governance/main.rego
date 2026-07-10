package layer2.governance

import rego.v1
import data.layer2.governance.pillars.static_policy
import data.layer2.governance.pillars.evidence
import data.layer2.governance.pillars.risk
import data.layer2.governance.pillars.trajectory
import data.layer2.governance.pillars.behavioral
import data.layer2.governance.pillars.judge

# Configuration is bundle data: PEP inputs only declare the version they expect.
config := data.policy_config

default required_pillars := {
  "require_evidence": true,
  "require_risk": true,
  "require_trajectory": true,
  "require_behavioral": true,
  "require_judge": false,
}

required_pillars := {
  "require_evidence": true,
  "require_risk": true,
  "require_trajectory": true,
  "require_behavioral": true,
  "require_judge": true,
} if {
  input.base_facts.external_communication.review_ready == true
} else := {
  "require_evidence": true,
  "require_risk": true,
  "require_trajectory": true,
  "require_behavioral": true,
  "require_judge": true,
} if {
  input.base_facts.actor.has_output_text == true
  input.base_facts.actor.result_type == "intermediate_output"
  input.base_facts.runtime.observed_evidence_count <= 1
}

# One else chain makes precedence deterministic and auditable.
decision := version_mismatch_decision if {
  input.policy_bundle_version != config.policy_bundle_version
} else := version_mismatch_decision if {
  input.policy_config_version != config.thresholds_version
} else := unavailable_required_pillar_decision if {
  required_pillar_unavailable
} else := deny_decision if {
  static_policy.result.write_blocked
} else := hitl_decision("external_communication_review_required", "External communication requires typed human review.") if {
  static_policy.result.hitl_mandatory
} else := clarification_decision if {
  static_policy.result.clarification_signaled
} else := stop_decision if {
  trajectory.result.replan_limit_exceeded
} else := hitl_decision("provider_failures_repeated", "Repeated provider failures require human review.") if {
  trajectory.result.provider_failures_repeated
} else := replan_decision("repeated_recovery", "Repeated recovery requires replanning.") if {
  trajectory.result.repeated_recovery
} else := replan_decision("provider_runtime_action_missing", "Provider returned no usable runtime action; replanning is the minimal recovery.") if {
  trajectory.result.provider_runtime_action_missing
} else := hitl_decision("judge_flag_external_draft", "Judge flagged an external draft with high confidence.") if {
  judge.result.external_flag_strong
} else := hitl_decision("judge_reject_external_or_high_risk", "Judge rejected output under external or high-risk conditions.") if {
  judge.result.reject_strong
  external_or_high_risk
} else := evidence_decision("judge_reject_output", "Judge rejected output; stronger evidence is required.") if {
  judge.result.reject_strong
} else := hitl_decision("precision_mismatch_external_or_sensitive", "Precision mismatch affects an external or high-sensitivity outcome.") if {
  evidence.result.precision_mismatch
  external_or_high_risk
} else := replan_decision("evidence_gap_and_trajectory_degraded", "Evidence gap and degraded trajectory require replanning.") if {
  evidence.result.evidence_gap
  trajectory.result.degraded_or_replan
} else := replan_decision("trajectory_degraded", "Trajectory degradation requires replanning.") if {
  trajectory.result.degraded_or_replan
} else := hitl_decision("high_operational_risk_external", "High operational risk with external impact requires human review.") if {
  risk.result.high
  risk.result.raw_facts.external_impact == true
} else := hitl_decision("behavioral_corroborated_risk", "Behavioral anomaly is corroborated by another strong signal.") if {
  behavioral.result.strong
  corroborating_strong_signal
} else := evidence_decision("behavioral_anomaly_needs_corroboration", "Behavioral anomaly alone can only request corroborating evidence.") if {
  behavioral.result.strong
} else := evidence_decision("evidence_support_below_step_need", "Evidence support is below the current step requirement.") if {
  evidence.result.evidence_gap
} else := evidence_decision("precision_mismatch", "Output claim precision exceeds available evidence support.") if {
  evidence.result.precision_mismatch
} else := read_only_decision if {
  static_policy.result.read_only_required
} else := continue_decision

required_pillar_unavailable if {
  input.pillar_unavailable.evidence == true
}
required_pillar_unavailable if {
  input.pillar_unavailable.risk == true
}
required_pillar_unavailable if {
  input.pillar_unavailable.trajectory == true
}
required_pillar_unavailable if {
  input.pillar_requirements.require_judge == true
  input.pillar_unavailable.judge == true
}

external_or_high_risk if { input.base_facts.actor.external_impact == true }
external_or_high_risk if { input.base_facts.task.sensitivity == "high" }
external_or_high_risk if { input.base_facts.task.sensitivity == "critical" }
external_or_high_risk if { risk.result.high }

corroborating_strong_signal if { evidence.result.evidence_gap }
corroborating_strong_signal if { risk.result.high }
corroborating_strong_signal if { trajectory.result.degraded_or_replan }
corroborating_strong_signal if { judge.result.strong_signal }

version_mismatch_decision := base_decision("wait_human_review", "require_human_review", ["policy_version_mismatch"], "The runtime and loaded policy bundle versions do not match.", true)
unavailable_required_pillar_decision := base_decision("wait_human_review", "require_human_review", ["required_pillar_unavailable"], "A required governance pillar is unavailable; review is required.", true)
deny_decision := base_decision("stop", "deny", ["write_capability_blocked"], "Write capability or external impact is blocked by policy.", false)
clarification_decision := base_decision("ask_user_clarification", "ask_clarification", ["ask_clarification_required"], "The actor requested clarification.", true)
stop_decision := base_decision("stop", "deny", ["replan_limit_exceeded"], "Replan limit was exceeded.", false)
read_only_decision := base_decision("continue_read_only", "allow_read_only", ["read_only_allowed"], "Read-only envelope is preserved.", false)
continue_decision := base_decision("continue", "allow", ["allowed_by_policy"], "No material governance signal requires intervention.", false)
hitl_decision(code, reason) := base_decision("wait_human_review", "require_human_review", [code], reason, true)
replan_decision(code, reason) := base_decision("replan", "allow", [code], reason, false)
evidence_decision(code, reason) := base_decision("request_more_evidence", "allow", [code], reason, false)

base_decision(directive, policy_decision, reason_codes, selected_reason, requires_review) := {
  "schema_version": "governance_decision_output.v1",
  "directive": directive,
  "policy_decision": policy_decision,
  "reason_codes": reason_codes,
  "pillar_results": {"static_policy": static_policy.result, "evidence": evidence.result, "risk": risk.result, "trajectory": trajectory.result, "behavioral": behavioral.result, "judge": judge.result},
  "selected_reason": selected_reason,
  "rejected_alternatives": rejected_alternatives(directive),
  "safety_invariants_checked": ["deny_is_absorbing", "hitl_mandatory_not_downgraded", "read_only_envelope_preserved", "behavioral_cannot_escalate_alone", "judge_cannot_hard_deny", "fail_closed_on_required_pillar_unavailable"],
  "requires_human_review": requires_review,
  "policy_bundle_version": config.policy_bundle_version,
  "policy_config_version": config.thresholds_version,
}

rejected_alternatives("request_more_evidence") := {"wait_human_review": "Evidence gap is recoverable without mandatory external approval."}
rejected_alternatives("replan") := {"request_more_evidence": "The trajectory is degraded enough that replanning is safer."}
rejected_alternatives(directive) := {} if {
  directive != "request_more_evidence"
  directive != "replan"
}
