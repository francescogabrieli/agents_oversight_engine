package layer2.evaluation

import rego.v1

base_evaluation_input := {"derived": {"completeness": "complete"}, "trace": {"observations": [], "governance_passes": []}}

test_stop_without_deny_is_reported if {
  trace := object.union(base_evaluation_input.trace, {"governance_passes": [{"directive": "stop", "policy_decision": "allow", "pillar_results": {"trajectory": {"actor_requested_replan": false}, "static_policy": {"hard_deny": false}, "judge": {"invoked": false}, "behavioral": {"raw_facts": {"mode": "shadow", "decision_influence": false}}, "risk": {"raw_facts": {"mode": "shadow", "decision_influence": false}}}}]})
  result := compliance with input as object.union(base_evaluation_input, {"trace": trace})
  "deny_must_come_from_static_policy" in result.findings
}
