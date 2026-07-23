package layer2.governance.pillars.behavioral

import rego.v1

facts := object.get(input.pillars.behavioral, "raw_facts", {})

# Behavioral memory stays queryable and observable but cannot affect the directive.
result := {
	"mode": "shadow",
	"decision_influence": false,
	"status": object.get(facts, "status", "shadow"),
	"raw_facts": facts,
	"score": object.get(input.pillars.behavioral, "diagnostic_score", null),
	"level": object.get(input.pillars.behavioral, "diagnostic_level", "shadow"),
	"score_components": object.get(input.pillars.behavioral, "score_components", {}),
	"score_formula_version": object.get(input.pillars.behavioral, "score_formula_version", "behavioral_features_v2"),
	"thresholds_version": object.get(input.pillars.behavioral, "thresholds_version", data.policy_config.thresholds_version),
}
