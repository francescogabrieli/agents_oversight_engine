package layer2.evaluation

base_evaluation_input := {
	"derived": {"completeness": "complete"},
	"trace": {
		"actor": {"precision_mismatch_detected": false},
		"observed_evidence": [],
		"governance_passes": [],
	},
}

# Aggregated web-search fallback cannot claim extracted source support.
test_web_search_detailed_fallback_fails if {
	trace := object.union(base_evaluation_input.trace, {
		"observed_evidence": [{
			"evidence_id": "step_1:tool:1:fallback",
			"locator": "official source query",
			"source_tool": "web_search",
			"support_level": "detailed_extracted",
		}],
	})
	result := compliance with input as object.union(base_evaluation_input, {"trace": trace})
	"web_search_evidence_support_overstated" in result.findings
}

# Query-only fallback evidence exposes the missing source-level locator.
test_web_search_query_fallback_fails if {
	trace := object.union(base_evaluation_input.trace, {
		"observed_evidence": [{
			"evidence_id": "step_1:tool:1:fallback",
			"locator": "official source query",
			"source_tool": "web_search",
			"support_level": "source_discovery",
		}],
	})
	result := compliance with input as object.union(base_evaluation_input, {"trace": trace})
	"web_search_fallback_missing_source_locator" in result.findings
}

# Actor and final governance must share the canonical precision-mismatch result.
test_precision_mismatch_disagreement_fails if {
	trace := object.union(base_evaluation_input.trace, {
		"actor": {"precision_mismatch_detected": true},
		"governance_passes": [{
			"directive": "continue",
			"policy_decision": "allow",
			"pillar_results": {"evidence": {"precision_mismatch": false}},
		}],
	})
	result := compliance with input as object.union(base_evaluation_input, {"trace": trace})
	"actor_governance_precision_mismatch_disagrees" in result.findings
}

# A strong numeric score cannot retain the old weak default level.
test_evidence_score_level_disagreement_fails if {
	trace := object.union(base_evaluation_input.trace, {
		"governance_passes": [{
			"pillar_results": {"evidence": {"score": 0.93, "level": "weak"}},
		}],
	})
	result := compliance with input as object.union(base_evaluation_input, {"trace": trace})
	"evidence_score_level_inconsistent" in result.findings
}
