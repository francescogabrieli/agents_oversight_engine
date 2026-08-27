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

default required_pillars := {"require_evidence": false, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false}

required_pillars := {"require_evidence": false, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false} if {
	actor.result_type == "propose_action"
	not propose_action_has_factual_content
}

required_pillars := {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false} if {
	actor.result_type == "propose_action"
	propose_action_has_factual_content
}

required_pillars := {"require_evidence": false, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false} if {
	actor.result_type == "needs_replan"
}

required_pillars := {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false} if {
	actor.result_type == "step_completed"
	not external_candidate
}

required_pillars := {"require_evidence": true, "require_risk": false, "require_trajectory": true, "require_behavioral": false, "require_judge": false} if {
	external_candidate
	not static_policy.result.hard_deny
}

propose_action_has_factual_content if {
	actor.result_type == "propose_action"
	object.get(actor, "has_factual_content", false) == true
}

# OPA admits the costly semantic judge only after deterministic pillars have passed.
default judge_admission := {"required": false, "reason": "deterministic_gate_not_admissible"}

judge_admission := {"required": true, "reason": "governed_external_semantic_candidate"} if {
	external_candidate
	input.policy_bundle_version == config.policy_bundle_version
	input.policy_config_version == config.thresholds_version
	input.pillar_requirements.require_evidence == true
	input.pillar_requirements.require_trajectory == true
	not static_policy.result.hard_deny
	not static_policy.result.clarification_signaled
	object.get(input.pillar_unavailable, "evidence", false) != true
	object.get(input.pillar_unavailable, "trajectory", false) != true
	not evidence.result.evidence_gap
	not trajectory.result.replan_request_stalled
	not trajectory.result.replan_request_admissible
	not trajectory.result.unchanged_resubmission
	not trajectory.result.repeated_rejected_payload_digest
	regex.match("^[0-9a-f]{64}$", object.get(operation, "payload_digest", ""))
}


external_candidate if {
	actor.result_type == "step_completed"
	object.get(operation, "execution_candidate", false) == true
	object.get(operation, "boundary", "local") == "governed_external"
}

decision := version_mismatch_decision if input.policy_bundle_version != config.policy_bundle_version

else := version_mismatch_decision if input.policy_config_version != config.thresholds_version

else := deny_decision if static_policy.result.hard_deny

else := judge_unavailable_decision if judge_required_unavailable

else := unavailable_required_pillar_decision if active_required_pillar_unavailable

else := clarification_decision if static_policy.result.clarification_signaled

else := hitl_decision("replan_ceiling_reached", "The current replanning episode reached the configured ceiling while the actor requested another replan.") if trajectory.result.replan_ceiling_reached

else := hitl_decision("consecutive_tool_failures", "Consecutive tool failures exceeded the human review threshold.") if trajectory.result.consecutive_tool_failures_review_threshold_reached

else := hitl_decision("step_exploration_budget_exhausted", "The plan step exhausted its read-only exploration budget without reaching a completion.") if trajectory.result.step_exploration_budget_exhausted

else := hitl_decision("action_churn", "The actor oscillated between variants of the current external operation without converging.") if trajectory.result.action_churn

else := hitl_decision("replan_request_stalled", "The actor repeated a replan request without a new observable execution basis.") if trajectory.result.replan_request_stalled

else := hitl_decision("unresolvable_revision", "The latest semantic revision request has no new observational basis and cannot be resolved by repeating the candidate.") if trajectory.result.unresolvable_revision

else := hitl_decision("rejected_candidate_resubmitted", "The actor resubmitted a payload digest already rejected by semantic governance.") if trajectory.result.repeated_rejected_payload_digest

else := hitl_decision("unchanged_candidate_resubmission", "The actor resubmitted the same external candidate without a substantive state or payload change.") if trajectory.result.unchanged_resubmission

else := replan_decision("actor_requested_replan", "The actor explicitly requested a replacement semantic trajectory from a new observable boundary.") if trajectory.result.replan_request_admissible

else := hitl_decision("judge_unsafe", "The semantic judge found the governed external operation unsafe.") if {
	input.pillar_requirements.require_judge == true
	judge.result.verdict == "unsafe"
}

else := hitl_decision("judge_uncertain", "The semantic judge could not establish that the governed external operation is safe.") if {
	input.pillar_requirements.require_judge == true
	judge.result.verdict == "uncertain"
}

else := hitl_decision("action_revision_limit_reached", "The external proposal exhausted its bounded semantic revisions and requires human review.") if {
	input.pillar_requirements.require_judge == true
	judge.result.verdict == "needs_revision"
	trajectory.result.action_revision_limit_reached
}

else := revise_decision if {
	input.pillar_requirements.require_judge == true
	judge.result.verdict == "needs_revision"
}

else := base_decision("wait_human_review", "require_human_review", array.concat(["evidence_request_limit_reached"], evidence.result.reason_codes), "The actor repeated completion without resolving required evidence and reached the request ceiling.", true) if {
	input.pillar_requirements.require_evidence == true
	evidence.result.evidence_gap
	evidence.result.evidence_request_ceiling_reached
}

else := evidence_decision(evidence_gap_reason_codes, "The completion references invalid or missing concrete evidence.") if {
	input.pillar_requirements.require_evidence == true
	evidence.result.evidence_gap
}

else := external_low_confidence_review if {
	external_candidate
	low_external_confidence
}

else := approval_mode_review if {
	external_candidate
	approval_required
}

else := read_only_decision if static_policy.result.read_only_required

else := continue_decision

low_external_confidence if {
	input.base_facts.uncertainty.actor_self_confidence != null
	input.base_facts.uncertainty.actor_self_confidence < input.base_facts.uncertainty.configured_confidence_threshold
}

external_low_confidence_review := hitl_decision("external_low_actor_self_confidence", "The governed external proposal remains uncertain and requires human review.")
approval_required if operation.approval_mode == "always"

approval_required if {
	operation.approval_mode == "conditional"
	input.base_facts.task.sensitivity == "high"
}

approval_required if {
	operation.approval_mode == "conditional"
	input.base_facts.task.sensitivity == "critical"
}

approval_mode_review := hitl_decision("capability_approval_required", "The capability approval mode requires a typed human authorization for this exact payload.")

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
	not judge_binding_valid
}

judge_binding_valid if {
	judge.result.digest_matches == true
	judge.result.operation_digest_matches == true
	judge.result.evidence_snapshot_matches == true
	judge.result.evidence_selection_matches == true
	judge.result.evaluated_operation_digest == judge.result.expected_operation_digest
	judge.result.evaluated_evidence_snapshot_digest == judge.result.expected_evidence_snapshot_digest
	judge.result.evaluated_evidence_selection_digest == judge.result.expected_evidence_selection_digest
}

candidate_alternatives[code] := reason if {
	code := "replan_ceiling_reached"
	reason := "The current replanning episode reached the configured ceiling while the actor requested another replan."
	trajectory.result.replan_ceiling_reached
}

candidate_alternatives[code] := reason if {
	code := "consecutive_tool_failures"
	reason := "Consecutive tool failures exceeded the human review threshold."
	trajectory.result.consecutive_tool_failures_review_threshold_reached
}

candidate_alternatives[code] := reason if {
	code := "step_exploration_budget_exhausted"
	reason := "The plan step exhausted its read-only exploration budget without reaching a completion."
	trajectory.result.step_exploration_budget_exhausted
}

candidate_alternatives[code] := reason if {
	code := "action_churn"
	reason := "The actor oscillated between variants of the current external operation without converging."
	trajectory.result.action_churn
}

candidate_alternatives[code] := reason if {
	code := "replan_request_stalled"
	reason := "The actor repeated a replan request without a new observable execution basis."
	trajectory.result.replan_request_stalled
}

candidate_alternatives[code] := reason if {
	code := "unresolvable_revision"
	reason := "The latest semantic revision request has no new observational basis and cannot be resolved by repeating the candidate."
	trajectory.result.unresolvable_revision
}

candidate_alternatives[code] := reason if {
	code := "rejected_candidate_resubmitted"
	reason := "The actor resubmitted a payload digest already rejected by semantic governance."
	trajectory.result.repeated_rejected_payload_digest
}

candidate_alternatives[code] := reason if {
	code := "unchanged_candidate_resubmission"
	reason := "The actor resubmitted the same external candidate without a substantive state or payload change."
	trajectory.result.unchanged_resubmission
}

candidate_alternatives[code] := reason if {
	code := "actor_requested_replan"
	reason := "The actor explicitly requested a replacement semantic trajectory from a new observable boundary."
	trajectory.result.replan_request_admissible
}

candidate_alternatives[code] := reason if {
	code := "judge_unsafe"
	reason := "The semantic judge found the governed external operation unsafe."
	input.pillar_requirements.require_judge == true
	judge.result.verdict == "unsafe"
}

candidate_alternatives[code] := reason if {
	code := "judge_uncertain"
	reason := "The semantic judge could not establish that the governed external operation is safe."
	input.pillar_requirements.require_judge == true
	judge.result.verdict == "uncertain"
}

candidate_alternatives[code] := reason if {
	code := "action_revision_limit_reached"
	reason := "The external proposal exhausted its bounded semantic revisions and requires human review."
	input.pillar_requirements.require_judge == true
	judge.result.verdict == "needs_revision"
	trajectory.result.action_revision_limit_reached
}

candidate_alternatives[code] := reason if {
	code := "judge_needs_revision"
	reason := "The external payload requires semantic revision."
	input.pillar_requirements.require_judge == true
	judge.result.verdict == "needs_revision"
}

candidate_alternatives[code] := reason if {
	code := "evidence_request_limit_reached"
	reason := "The actor repeated completion without resolving required evidence and reached the request ceiling."
	input.pillar_requirements.require_evidence == true
	evidence.result.evidence_gap
	evidence.result.evidence_request_ceiling_reached
}

candidate_alternatives[code] := reason if {
	code := "invalid_or_missing_selected_evidence"
	reason := "The completion references invalid or missing concrete evidence."
	input.pillar_requirements.require_evidence == true
	evidence.result.evidence_gap
}

candidate_alternatives[code] := reason if {
	code := "external_low_actor_self_confidence"
	reason := "The governed external proposal remains uncertain and requires human review."
	external_candidate
	low_external_confidence
}

candidate_alternatives[code] := reason if {
	code := "capability_approval_required"
	reason := "The capability approval mode requires a typed human authorization for this exact payload."
	external_candidate
	approval_required
}

candidate_alternatives[code] := reason if {
	code := "read_only_allowed"
	reason := "The read-only envelope is preserved."
	static_policy.result.read_only_required
}

rejected_alternatives(winning_reason_codes) := {code: reason |
	some code, reason in candidate_alternatives
	not array_contains(winning_reason_codes, code)
}

array_contains(arr, elem) if {
	some x in arr
	x == elem
}

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
evidence_decision(codes, reason) := base_decision("request_more_evidence", "allow", codes, reason, false)

# The gate code names the directive; the pillar codes name which structural family failed.
# Both travel on the decision so remediation and the evaluation store read one vocabulary.
evidence_gap_reason_codes := array.concat(
	["invalid_or_missing_selected_evidence"],
	[code | some code in evidence.result.reason_codes; code != "invalid_or_missing_selected_evidence"],
)
static_deny_reason_codes := static_policy.result.reason_codes if count(static_policy.result.reason_codes) > 0

else := ["static_policy_hard_deny"]

base_decision(directive, policy_decision, reason_codes, selected_reason, requires_review) := {"schema_version": "governance_decision_output.v1", "directive": directive, "policy_decision": policy_decision, "reason_codes": reason_codes, "judge_admission": judge_admission, "pillar_results": {"static_policy": static_policy.result, "evidence": evidence.result, "risk": risk.result, "trajectory": trajectory.result, "behavioral": behavioral.result, "judge": judge.result}, "selected_reason": selected_reason, "rejected_alternatives": rejected_alternatives(reason_codes), "safety_invariants_checked": ["static_hard_deny_is_absorbing", "deterministic_pillars_precede_judge", "behavioral_shadow_has_no_decision_influence", "operational_risk_shadow_has_no_decision_influence", "read_only_envelope_preserved", "judge_digest_matches_executable_payload"], "requires_human_review": requires_review, "policy_bundle_version": config.policy_bundle_version, "policy_config_version": config.thresholds_version}
