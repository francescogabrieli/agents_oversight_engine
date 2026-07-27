package layer2.governance

import rego.v1

# Low actor self-confidence alone is contextual uncertainty, not trajectory degradation.
test_low_confidence_alone_does_not_trigger_replan if {
    result := data.layer2.governance.pillars.trajectory.result with input as {
        "pillars": {"trajectory": {"raw_facts": {
            "actor_self_confidence": 0.2,
            "configured_confidence_threshold": 0.65,
            "consecutive_no_progress_turns": 0,
            "consecutive_provider_protocol_failures": 0,
            "recovery_exhausted": false,
        }}}
    }
    result.no_progress_replan == false
    result.low_confidence_no_progress == false
    result.recovery_exhausted == false
}

# The same low confidence contributes to recovery only with concrete absence of progress.
test_low_confidence_with_no_progress_triggers_recovery if {
    result := data.layer2.governance.pillars.trajectory.result with input as {
        "pillars": {"trajectory": {"raw_facts": {
            "actor_self_confidence": 0.2,
            "configured_confidence_threshold": 0.65,
            "consecutive_no_progress_turns": 1,
            "consecutive_provider_protocol_failures": 0,
            "recovery_exhausted": false,
        }}}
    }
    result.low_confidence_no_progress == true
}
