package layer2.governance.pillars.trajectory

import rego.v1

facts := object.get(object.get(input.pillars, "trajectory", {}), "raw_facts", {})

# Keep the policy boundary explicit: only convergence and replay facts can alter a directive.
policy_fact_names := [
	"needs_replan_signal",
	"replan_reason_present",
	"first_replan_request",
	"observable_change_since_last_replan",
	"external_action_revision_count",
	"unchanged_resubmission",
	"repeated_rejected_payload_digest",
	"replan_count",
	"consecutive_tool_failures",
	"action_churn_count",
]

# Runtime counters and recovery markers remain replayable telemetry, not policy triggers.
observational_only_fact_names := [
	"actor_turn_count",
	"progress_epoch",
	"completed_step_count",
	"tool_call_count",
	"successful_tool_call_count",
	"failed_tool_call_count",
	"observation_count",
	"consecutive_provider_protocol_failures",
	"completion_signal",
	"external_candidate",
	"provider_recovery_active",
	"provider_protocol_retry_exhausted",
]

fact_usage := {
	"policy_facts": policy_fact_names,
	"observational_only_facts": observational_only_fact_names,
}

actor_requested_replan := object.get(facts, "needs_replan_signal", false)
replan_reason_present := object.get(facts, "replan_reason_present", false)
first_replan_request := object.get(facts, "first_replan_request", true)
observable_change_since_last_replan := object.get(facts, "observable_change_since_last_replan", false)
action_revision_count := object.get(facts, "external_action_revision_count", 0)
unchanged_resubmission := object.get(facts, "unchanged_resubmission", false)
repeated_rejected_payload_digest := object.get(facts, "repeated_rejected_payload_digest", false)
replan_count := object.get(facts, "replan_count", 0)
consecutive_tool_failures := object.get(facts, "consecutive_tool_failures", 0)
action_churn_count := object.get(facts, "action_churn_count", 0)

max_action_revisions_before_review := object.get(data.policy_config.thresholds, "max_action_revisions_before_review", 3)
max_replans_before_review := object.get(data.policy_config.thresholds, "max_replans_before_review", 3)
max_consecutive_tool_failures_before_replan := object.get(data.policy_config.thresholds, "max_consecutive_tool_failures_before_replan", 2)
max_consecutive_tool_failures_before_review := object.get(data.policy_config.thresholds, "max_consecutive_tool_failures_before_review", 4)
max_action_churn_before_review := object.get(data.policy_config.thresholds, "max_action_churn_before_review", 2)

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

default action_revision_limit_reached := false

action_revision_limit_reached if {
	action_revision_count >= max_action_revisions_before_review
}

default replan_ceiling_reached := false

replan_ceiling_reached if {
	replan_count >= max_replans_before_review
}

default consecutive_tool_failures_replan_threshold_reached := false

consecutive_tool_failures_replan_threshold_reached if {
	consecutive_tool_failures >= max_consecutive_tool_failures_before_replan
	consecutive_tool_failures < max_consecutive_tool_failures_before_review
}

default consecutive_tool_failures_review_threshold_reached := false

consecutive_tool_failures_review_threshold_reached if {
	consecutive_tool_failures >= max_consecutive_tool_failures_before_review
}

default action_churn := false

action_churn if {
	action_churn_count >= max_action_churn_before_review
}

require_trajectory := object.get(object.get(input, "pillar_requirements", {}), "require_trajectory", false) == true

mode := "active" if {
	require_trajectory
} else := "inactive"

decision_influence := true if {
	require_trajectory
} else := false

result := {
	"mode": mode,
	"decision_influence": decision_influence,
	"actor_requested_replan": actor_requested_replan,
	"replan_request_admissible": replan_request_admissible,
	"replan_request_stalled": replan_request_stalled,
	"action_revision_limit_reached": action_revision_limit_reached,
	"replan_ceiling_reached": replan_ceiling_reached,
	"consecutive_tool_failures_replan_threshold_reached": consecutive_tool_failures_replan_threshold_reached,
	"consecutive_tool_failures_review_threshold_reached": consecutive_tool_failures_review_threshold_reached,
	"action_churn": action_churn,
	"unchanged_resubmission": unchanged_resubmission,
	"repeated_rejected_payload_digest": repeated_rejected_payload_digest,
	"diagnostic_score": object.get(object.get(input.pillars, "trajectory", {}), "diagnostic_score", null),
	"diagnostic_level": object.get(object.get(input.pillars, "trajectory", {}), "diagnostic_level", "observed"),
	"reason_facts": object.get(object.get(input.pillars, "trajectory", {}), "reason_facts", []),
	"summary": object.get(object.get(input.pillars, "trajectory", {}), "summary", "Trajectory health observed."),
	"raw_facts": facts,
	"fact_usage": fact_usage,
	"score_components": object.get(object.get(input.pillars, "trajectory", {}), "score_components", {}),
	"score_formula_version": object.get(object.get(input.pillars, "trajectory", {}), "score_formula_version", "trajectory_health_v1"),
	"thresholds_version": object.get(object.get(input.pillars, "trajectory", {}), "thresholds_version", "unknown"),
}
