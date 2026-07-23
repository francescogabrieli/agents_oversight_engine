package layer2.governance.pillars.judge

import rego.v1

facts := object.get(input.pillars.judge, "raw_facts", {})

result := {
	"invoked": object.get(facts, "invoked", false),
	"required": object.get(facts, "required", false),
	"status": object.get(facts, "status", "not_applicable"),
	"unavailable": object.get(facts, "pillar_unavailable", false),
	"verdict": object.get(facts, "verdict", "uncertain"),
	"severity": object.get(facts, "severity", "high"),
	"confidence": object.get(facts, "confidence", 0),
	"findings": object.get(facts, "findings", []),
	"evaluated_operation_digest": object.get(facts, "evaluated_operation_digest", null),
	"expected_operation_digest": object.get(facts, "expected_operation_digest", null),
	"digest_matches": object.get(facts, "digest_matches", false),
	"raw_facts": facts,
	"score_components": object.get(input.pillars.judge, "score_components", {}),
	"score_formula_version": object.get(input.pillars.judge, "score_formula_version", "external_operation_semantic_judge_v2"),
	"thresholds_version": object.get(input.pillars.judge, "thresholds_version", data.policy_config.thresholds_version),
}
