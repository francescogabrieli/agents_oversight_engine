package layer2.governance.pillars.trajectory

import rego.v1

facts := object.get(object.get(input.pillars, "trajectory", {}), "raw_facts", {})
no_progress_turns := object.get(facts, "consecutive_no_progress_turns", 0)
actor_self_confidence := object.get(facts, "actor_self_confidence", null)
confidence_threshold := object.get(facts, "configured_confidence_threshold", 0)
provider_failures := object.get(facts, "consecutive_provider_protocol_failures", 0)
recovery_exhausted_fact := object.get(facts, "recovery_exhausted", false)
action_revision_count := object.get(facts, "external_action_revision_count", 0)
max_action_revisions_before_review := object.get(data.policy_config.thresholds, "max_action_revisions_before_review", 3)

default no_progress_replan := false
no_progress_replan := true if { no_progress_turns >= 2 }
default low_confidence_no_progress := false
low_confidence_no_progress := true if {
    actor_self_confidence != null
    actor_self_confidence < confidence_threshold
    no_progress_turns >= 1
}
default provider_protocol_recovery := false
provider_protocol_recovery := true if { provider_failures >= 2 }
default action_revision_limit_reached := false
action_revision_limit_reached := true if {
    action_revision_count >= max_action_revisions_before_review - 1
}

result := {
    "no_progress_replan": no_progress_replan,
    "low_confidence_no_progress": low_confidence_no_progress,
    "provider_protocol_recovery": provider_protocol_recovery,
    "action_revision_limit_reached": action_revision_limit_reached,
    "recovery_exhausted": recovery_exhausted_fact,
    "raw_facts": facts,
    "score_components": object.get(object.get(input.pillars, "trajectory", {}), "score_components", {}),
    "score_formula_version": object.get(object.get(input.pillars, "trajectory", {}), "score_formula_version", "trajectory_facts_v3"),
    "thresholds_version": object.get(object.get(input.pillars, "trajectory", {}), "thresholds_version", "unknown"),
}
