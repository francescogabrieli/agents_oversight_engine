package layer2.evaluation

import rego.v1

base_evaluation_input := {"derived": {"completeness": "complete"}, "trace": {"observed_evidence": [], "governance_passes": []}}

test_cited_search_requires_source_locator if {
  trace := object.union(base_evaluation_input.trace, {"observed_evidence": [{"evidence_id": "step:tool:1:source:1", "locator": "search query", "source_tool": "web_search", "content_state": "provider_cited_summary"}]})
  result := compliance with input as object.union(base_evaluation_input, {"trace": trace})
  "web_search_citation_missing_locator" in result.findings
}

test_aggregate_search_fallback_is_not_content if {
  trace := object.union(base_evaluation_input.trace, {"observed_evidence": [{"evidence_id": "step:tool:1:fallback", "locator": "https://example.test", "source_tool": "web_search", "content_state": "provider_cited_summary"}]})
  result := compliance with input as object.union(base_evaluation_input, {"trace": trace})
  "web_search_aggregate_copied_as_content" in result.findings
}

test_recovery_exhaustion_must_not_be_deny if {
  trace := object.union(base_evaluation_input.trace, {"governance_passes": [{"directive": "stop", "policy_decision": "deny", "pillar_results": {"trajectory": {"recovery_exhausted": true}, "static_policy": {"hard_deny": false}, "judge": {"invoked": false}, "behavioral": {"raw_facts": {"mode": "shadow", "decision_influence": false}}, "risk": {"raw_facts": {"mode": "shadow", "decision_influence": false}}}}]})
  result := compliance with input as object.union(base_evaluation_input, {"trace": trace})
  "recovery_exhaustion_denied" in result.findings
}
