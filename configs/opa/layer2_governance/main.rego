package layer2.governance

import data.layer2.governance.pillars.behavioral
import data.layer2.governance.pillars.evidence
import data.layer2.governance.pillars.judge
import data.layer2.governance.pillars.risk
import data.layer2.governance.pillars.static_policy
import data.layer2.governance.pillars.trajectory
import rego.v1

config := data.policy_config
operation := object.get(input.base_facts, "external_operation", {})

default required_pillars := {
	"require_evidence": true,
	"require_risk": false,
	"require_trajectory": true,
	"require_behavioral": false,
	"require_judge": false,
}

required_pillars := {
	"require_evidence": true,
	"require_risk": false,
	"require_trajectory": true,
	"require_behavioral": false,
	"require_judge": true,
} if {
	object.get(operation, "execution_candidate", false) == true
	object.get(operation, "boundary", "local") == "governed_external"
	not static_policy.result.hard_deny
}

# One else-chain preserves deterministic precedence under the sole OPA authority.
decision := version_mismatch_decision if {
	input.policy_bundle_version != config.policy_bundle_version
} else := version_mismatch_decision if {
	input.policy_config_version != config.thresholds_version
} else := deny_decision if {
	static_policy.result.hard_deny
} else := judge_unavailable_decision if {
	judge_required_unavailable
} else := unavailable_required_pillar_decision if {
	active_required_pillar_unavailable
} else := clarification_decision if {
	static_policy.result.clarification_signaled
} else := stop_decision if {
	trajectory.result.replan_limit_exceeded
} else := hitl_decision("provider_failures_repeated", "Repeated provider failures require human review.") if {
	trajectory.result.provider_failures_repeated
} else := hitl_decision("repeated_action_revision", "Repeated payload revision attempts require human review.") if {
	trajectory.result.action_revision_limit_exceeded
} else := replan_decision("repeated_recovery", "Repeated evidence recovery requires replanning.") if {
	trajectory.result.repeated_recovery
} else := replan_decision("provider_runtime_action_missing", "Provider returned no usable runtime action; replanning is the minimal recovery.") if {
	trajectory.result.provider_runtime_action_missing
} else := hitl_decision("judge_unsafe", "The semantic judge found the governed external operation unsafe.") if {
	judge.result.verdict == "unsafe"
} else := hitl_decision("judge_uncertain", "The semantic judge could not establish that the governed external operation is safe.") if {
	judge.result.verdict == "uncertain"
	input.pillar_requirements.require_judge == true
} else := revise_decision if {
	judge.result.verdict == "needs_revision"
} else := replan_decision("evidence_gap_and_trajectory_degraded", "Evidence gap and degraded trajectory require replanning.") if {
	evidence.result.evidence_gap
	trajectory.result.degraded_or_replan
} else := replan_decision("trajectory_degraded", "Trajectory degradation requires replanning.") if {
	trajectory.result.degraded_or_replan
} else := evidence_decision("evidence_support_below_step_need", "Evidence support is below the current step requirement.") if {
	evidence.result.evidence_gap
} else := evidence_decision("precision_mismatch", "Output claim precision exceeds available evidence support.") if {
	evidence.result.precision_mismatch
} else := read_only_decision if {
	static_policy.result.read_only_required
} else := continue_decision

active_required_pillar_unavailable if {
	input.pillar_requirements.require_evidence == true
	object.get(input.pillar_unavailable, "evidence", false) == true
}

active_required_pillar_unavailable if {
	input.pillar_requirements.require_trajectory == true
	object.get(input.pillar_unavailable, "trajectory", false) == true
}

judge_required_unavailable if {
	input.pillar_requirements.require_judge == true
	object.get(input.pillar_unavailable, "judge", false) == true
}

judge_required_unavailable if {
	input.pillar_requirements.require_judge == true
	judge.result.invoked != true
}

judge_required_unavailable if {
	input.pillar_requirements.require_judge == true
	judge.result.digest_matches != true
}

version_mismatch_decision := base_decision("wait_human_review", "require_human_review", ["policy_version_mismatch"], "The runtime and loaded policy bundle versions do not match.", true)
unavailable_required_pillar_decision := base_decision("wait_human_review", "require_human_review", ["required_pillar_unavailable"], "An active governance pillar is unavailable; review is required.", true)
judge_unavailable_decision := base_decision("wait_human_review", "require_human_review", ["judge_required_unavailable"], "The mandatory semantic judge assessment is unavailable or invalid.", true)
deny_decision := base_decision("stop", "deny", static_deny_reason_codes, static_policy.result.summary, false)
clarification_decision := base_decision("ask_user_clarification", "ask_clarification", ["ask_clarification_required"], "The actor requested clarification.", true)
stop_decision := base_decision("stop", "deny", ["replan_limit_exceeded"], "Replan limit was exceeded.", false)
read_only_decision := base_decision("continue_read_only", "allow_read_only", ["read_only_allowed"], "The read-only envelope is preserved.", false)
continue_decision := base_decision("continue", "allow", ["allowed_by_policy"], "All active governance pillars allow continuation.", false)
revise_decision := base_decision("revise_action", "allow", ["judge_needs_revision"], "The external payload requires semantic revision before it can be reconsidered.", false)
hitl_decision(code, reason) := base_decision("wait_human_review", "require_human_review", [code], reason, true)
replan_decision(code, reason) := base_decision("replan", "allow", [code], reason, false)
evidence_decision(code, reason) := base_decision("request_more_evidence", "allow", [code], reason, false)

static_deny_reason_codes := static_policy.result.reason_codes if {
	count(static_policy.result.reason_codes) > 0
} else := ["static_policy_hard_deny"]

base_decision(directive, policy_decision, reason_codes, selected_reason, requires_review) := {
	"schema_version": "governance_decision_output.v1",
	"directive": directive,
	"policy_decision": policy_decision,
	"reason_codes": reason_codes,
	"pillar_results": {
		"static_policy": static_policy.result,
		"evidence": evidence.result,
		"risk": risk.result,
		"trajectory": trajectory.result,
		"behavioral": behavioral.result,
		"judge": judge.result,
	},
	"selected_reason": selected_reason,
	"rejected_alternatives": rejected_alternatives(directive),
	"safety_invariants_checked": [
		"static_hard_deny_is_absorbing",
		"externality_alone_has_no_directive_effect",
		"judge_required_for_governed_external_candidate",
		"judge_cannot_hard_deny",
		"behavioral_shadow_has_no_decision_influence",
		"operational_risk_shadow_has_no_decision_influence",
		"read_only_envelope_preserved",
		"fail_closed_on_active_required_pillar_unavailable",
		"judge_digest_matches_executable_payload",
	],
	"requires_human_review": requires_review,
	"policy_bundle_version": config.policy_bundle_version,
	"policy_config_version": config.thresholds_version,
}

rejected_alternatives("request_more_evidence") := {
	"wait_human_review": "The evidence gap is recoverable without mandatory review.",
	"revise_action": "The active evidence contract must be satisfied before semantic payload approval.",
}

rejected_alternatives("replan") := {
	"request_more_evidence": "Trajectory degradation requires changing the recovery path.",
}

rejected_alternatives("revise_action") := {
	"request_more_evidence": "The judge finding concerns the payload semantics rather than missing evidence.",
	"replan": "The operation can be corrected without replacing the semantic plan.",
}

rejected_alternatives(directive) := {} if {
	directive != "request_more_evidence"
	directive != "replan"
	directive != "revise_action"
}
