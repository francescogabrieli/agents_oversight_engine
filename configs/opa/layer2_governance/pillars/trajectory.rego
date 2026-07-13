package layer2.governance.pillars.trajectory

facts := input.pillars.trajectory.raw_facts
config := data.policy_config
weights := config.trajectory_weights
thresholds := config.thresholds

default degraded_or_replan := false

turn_overage := max([0, facts.actor_turn_count_for_step - weights.max_actor_turns_per_step])

turn_penalty := (turn_overage * weights.actor_turn_overage_penalty) * completion_scale

completion_scale := weights.completion_attempt_turn_overage_scale if {
	facts.completion_attempt == true
}

completion_scale := 1 if {
	facts.completion_attempt != true
}

score := min([1, max([0, (((1 - turn_penalty) - additive(facts.failed_tool_count > 0, weights.failed_tool_result_penalty)) - additive(facts.needs_replan_signal, weights.needs_replan_penalty)) - additive(facts.low_confidence_uncertainty, weights.low_confidence_uncertainty_penalty)])])

level := "healthy" if {
	score >= thresholds.min_trajectory_health
}

level := "watch" if {
	score < thresholds.min_trajectory_health
	score >= 0.3
}

level := "degraded" if {
	score < 0.3
}

result := {
	"score": score,
	"level": level,
	"degraded_or_replan": degraded_or_replan,
	"provider_runtime_action_missing": facts.provider_runtime_action_missing == true,
	"replan_limit_exceeded": facts.replan_count >= weights.max_replans,
	"provider_failures_repeated": facts.consecutive_provider_failures >= thresholds.provider_failure_review_count,
	"repeated_recovery": facts.repeated_recovery,
	"raw_facts": facts,
	"score_components": input.pillars.trajectory.score_components,
	"score_formula_version": input.pillars.trajectory.score_formula_version,
	"thresholds_version": input.pillars.trajectory.thresholds_version,
}

degraded_or_replan if {
	level == "degraded"
}

degraded_or_replan if {
	facts.needs_replan_signal == true
}

additive(enabled, value) := value if {
	enabled == true
}

additive(enabled, _) := 0 if {
	enabled != true
}
