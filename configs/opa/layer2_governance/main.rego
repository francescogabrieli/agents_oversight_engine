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
actor := input.base_facts.actor

default required_pillars := {"require_evidence": false, "require_risk": false, "require_trajectory": false, "require_behavioral": false, "require_judge": false}

required_pillars := {"require_evidence": false, "require_risk": false, "require_trajectory": false, "require_behavioral": false, "require_judge": false} if {
    actor.result_type == "propose_action"
}

required_pillars := {"require_evidence": false, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false} if {
    actor.result_type == "needs_replan"
}

required_pillars := {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false} if {
    actor.result_type == "step_completed"
    not external_candidate
}

required_pillars := {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": true} if {
    external_candidate
    not static_policy.result.hard_deny
}

external_candidate if {
    object.get(operation, "execution_candidate", false) == true
    object.get(operation, "boundary", "local") == "governed_external"
}

decision := version_mismatch_decision if { input.policy_bundle_version != config.policy_bundle_version } else := version_mismatch_decision if { input.policy_config_version != config.thresholds_version } else := deny_decision if { static_policy.result.hard_deny } else := judge_unavailable_decision if { judge_required_unavailable } else := unavailable_required_pillar_decision if { active_required_pillar_unavailable } else := clarification_decision if { static_policy.result.clarification_signaled } else := hitl_decision("replan_request_stalled", "The actor repeated a replan request without a new observable execution basis.") if { trajectory.result.replan_request_stalled } else := replan_decision("actor_requested_replan", "The actor explicitly requested a replacement semantic trajectory from a new observable boundary.") if { trajectory.result.replan_request_admissible } else := hitl_decision("judge_unsafe", "The semantic judge found the governed external operation unsafe.") if { judge.result.verdict == "unsafe" } else := hitl_decision("judge_uncertain", "The semantic judge could not establish that the governed external operation is safe.") if { input.pillar_requirements.require_judge == true; judge.result.verdict == "uncertain" } else := hitl_decision("action_revision_limit_reached", "The external proposal exhausted its bounded semantic revisions and requires human review.") if { judge.result.verdict == "needs_revision"; trajectory.result.action_revision_limit_reached } else := revise_decision if { judge.result.verdict == "needs_revision" } else := evidence_decision("invalid_or_missing_selected_evidence", "The completion references invalid or missing concrete evidence.") if { input.pillar_requirements.require_evidence == true; evidence.result.evidence_gap } else := external_low_confidence_review if { external_candidate; low_external_confidence } else := approval_mode_review if { external_candidate; approval_required } else := read_only_decision if { static_policy.result.read_only_required } else := continue_decision

low_external_confidence if { input.pillars.trajectory.raw_facts.actor_self_confidence != null; input.pillars.trajectory.raw_facts.actor_self_confidence < input.pillars.trajectory.raw_facts.configured_confidence_threshold }

external_low_confidence_review := hitl_decision("external_low_actor_self_confidence", "The governed external proposal remains uncertain and requires human review.")
approval_required if { operation.approval_mode == "always" }
approval_required if { operation.approval_mode == "conditional"; input.base_facts.task.sensitivity == "high" }
approval_required if { operation.approval_mode == "conditional"; input.base_facts.task.sensitivity == "critical" }
approval_mode_review := hitl_decision("capability_approval_required", "The capability approval mode requires a typed human authorization for this exact payload.")
active_required_pillar_unavailable if { input.pillar_requirements.require_evidence == true; object.get(input.pillar_unavailable, "evidence", false) == true }
active_required_pillar_unavailable if { input.pillar_requirements.require_trajectory == true; object.get(input.pillar_unavailable, "trajectory", false) == true }
judge_required_unavailable if { input.pillar_requirements.require_judge == true; object.get(input.pillar_unavailable, "judge", false) == true }
judge_required_unavailable if { input.pillar_requirements.require_judge == true; judge.result.invoked != true }
judge_required_unavailable if { input.pillar_requirements.require_judge == true; judge.result.digest_matches != true }

version_mismatch_decision := base_decision("wait_human_review", "require_human_review", ["policy_version_mismatch"], "The runtime and loaded policy bundle versions do not match.", true)
unavailable_required_pillar_decision := base_decision("wait_human_review", "require_human_review", ["required_pillar_unavailable"], "An active governance pillar is unavailable; review is required.", true)
judge_unavailable_decision := base_decision("wait_human_review", "require_human_review", ["judge_required_unavailable"], "The mandatory semantic judge assessment is unavailable or invalid.", true)
deny_decision := base_decision("stop", "deny", static_deny_reason_codes, static_policy.result.summary, false)
clarification_decision := base_decision("ask_user_clarification", "ask_clarification", ["ask_clarification_required"], "The actor requested clarification.", true)
read_only_decision := base_decision("continue_read_only", "allow_read_only", ["read_only_allowed"], "The read-only envelope is preserved.", false)
continue_decision := base_decision("continue", "allow", ["allowed_by_policy"], "All relevant governance pillars allow continuation.", false)
revise_decision := base_decision("revise_action", "allow", ["judge_needs_revision"], "The external payload requires semantic revision.", false)
hitl_decision(code, reason) := base_decision("wait_human_review", "require_human_review", [code], reason, true)
replan_decision(code, reason) := base_decision("replan", "allow", [code], reason, false)
evidence_decision(code, reason) := base_decision("request_more_evidence", "allow", [code], reason, false)
static_deny_reason_codes := static_policy.result.reason_codes if { count(static_policy.result.reason_codes) > 0 } else := ["static_policy_hard_deny"]

base_decision(directive, policy_decision, reason_codes, selected_reason, requires_review) := {"schema_version": "governance_decision_output.v1", "directive": directive, "policy_decision": policy_decision, "reason_codes": reason_codes, "pillar_results": {"static_policy": static_policy.result, "evidence": evidence.result, "risk": risk.result, "trajectory": trajectory.result, "behavioral": behavioral.result, "judge": judge.result}, "selected_reason": selected_reason, "rejected_alternatives": {}, "safety_invariants_checked": ["static_hard_deny_is_absorbing", "judge_required_for_governed_external_candidate", "behavioral_shadow_has_no_decision_influence", "operational_risk_shadow_has_no_decision_influence", "read_only_envelope_preserved", "judge_digest_matches_executable_payload"], "requires_human_review": requires_review, "policy_bundle_version": config.policy_bundle_version, "policy_config_version": config.thresholds_version}
