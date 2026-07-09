package layer2.evaluation

default compliance := {
  "status": "pass",
  "findings": [],
  "policy_bundle_version": "layer2_governance_v2"
}

compliance := {
  "status": status,
  "findings": findings,
  "policy_bundle_version": "layer2_governance_v2"
}

status := "fail" if {
  count(findings) > 0
}

status := "pass" if {
  count(findings) == 0
}

findings contains "deny_must_come_from_static_policy" if {
  some decision in input.trace.governance_decisions
  decision.directive == "deny"
  not decision.policy_decision == "deny"
}

findings contains "behavioral_forced_hitl_without_corroboration" if {
  some decision in input.trace.governance_decisions
  decision.directive == "wait_human_review"
  decision.behavioral_recommended_directive == "wait_human_review"
  decision.oversight_recommended_directive == "continue"
  decision.judge_recommended_directive == "continue"
}

findings contains "judge_used_as_hard_deny" if {
  some decision in input.trace.governance_decisions
  decision.policy_decision == "deny"
  decision.judge_invoked == true
  decision.static_policy_summary != null
  not contains(lower(decision.static_policy_summary), "write")
}
