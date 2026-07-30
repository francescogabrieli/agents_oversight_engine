package layer2.governance.pillars.trajectory

import rego.v1

facts := object.get(object.get(input.pillars, "trajectory", {}), "raw_facts", {})
actor_requested_replan := object.get(facts, "needs_replan_signal", false)
replan_reason_present := object.get(facts, "replan_reason_present", false)
first_replan_request := object.get(facts, "first_replan_request", true)
observable_change_since_last_replan := object.get(facts, "observable_change_since_last_replan", false)
action_revision_count := object.get(facts, "external_action_revision_count", 0)
unchanged_resubmission := object.get(facts, "unchanged_resubmission", false)
repeated_rejected_payload_digest := object.get(facts, "repeated_rejected_payload_digest", false)
max_action_revisions_before_review := object.get(data.policy_config.thresholds, "max_action_revisions_before_review", 3)

default replan_request_admissible := false

replan_request_admissible if {
	actor_requested_replan
	replan_reason_present
	first_replan_request
}

replan_request_admissible if {
	actor_requested_replan
	replan_reason_present
	observable_change_since_last_replan
}

default replan_request_stalled := false

replan_request_stalled if {
	actor_requested_replan
	not replan_request_admissible
}

replan_request_stalled if unchanged_resubmission
replan_request_stalled if repeated_rejected_payload_digest

default action_revision_limit_reached := false

action_revision_limit_reached if {
	action_revision_count >= max_action_revisions_before_review - 1
}

result := {
	"actor_requested_replan": actor_requested_replan,
	"replan_request_admissible": replan_request_admissible,
	"replan_request_stalled": replan_request_stalled,
	"action_revision_limit_reached": action_revision_limit_reached,
	"unchanged_resubmission": unchanged_resubmission,
	"repeated_rejected_payload_digest": repeated_rejected_payload_digest,
	"raw_facts": facts,
	"score_components": object.get(object.get(input.pillars, "trajectory", {}), "score_components", {}),
	"score_formula_version": object.get(object.get(input.pillars, "trajectory", {}), "score_formula_version", "trajectory_health_v1"),
	"thresholds_version": object.get(object.get(input.pillars, "trajectory", {}), "thresholds_version", "unknown"),
}
