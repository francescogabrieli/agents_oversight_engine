package layer2.governance

import rego.v1

# Narration length and low self-confidence are observable context, not replan triggers.
test_narration_and_low_confidence_do_not_trigger_replan if {
    result := data.layer2.governance.pillars.trajectory.result with input as {
        "pillars": {"trajectory": {"raw_facts": {
            "actor_turn_count": 12,
            "completed_step_count": 0,
            "actor_self_confidence": 0.2,
            "configured_confidence_threshold": 0.65,
            "consecutive_provider_protocol_failures": 0,
            "needs_replan_signal": false,
            "replan_reason_present": false,
        }}}
    }
    result.actor_requested_replan == false
}

# Replanning is a semantic boundary explicitly requested by the actor.
test_explicit_actor_replan_signal_is_preserved if {
    result := data.layer2.governance.pillars.trajectory.result with input as {
        "pillars": {"trajectory": {"raw_facts": {
            "needs_replan_signal": true,
            "replan_reason_present": true,
            "first_replan_request": true,
        }}}
    }
    result.actor_requested_replan == true
    result.replan_request_admissible == true
}
