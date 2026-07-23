package layer2.governance.pillars.risk

import rego.v1

facts := object.get(input.pillars.risk, "raw_facts", {})

# Operational consequence assessment is deliberately shadow-only in this policy version.
result := {
	"mode": "shadow",
	"decision_influence": false,
	"applicability": object.get(facts, "applicability", "not_applicable"),
	"impact_level": object.get(facts, "impact_level", "unknown"),
	"recoverability": object.get(facts, "recoverability", "unknown"),
	"raw_facts": facts,
	"score": object.get(input.pillars.risk, "diagnostic_score", null),
	"level": object.get(input.pillars.risk, "diagnostic_level", "not_applicable"),
	"score_components": object.get(input.pillars.risk, "score_components", {}),
	"score_formula_version": object.get(input.pillars.risk, "score_formula_version", "operational_consequence_shadow_v1"),
	"thresholds_version": object.get(input.pillars.risk, "thresholds_version", data.policy_config.thresholds_version),
}
