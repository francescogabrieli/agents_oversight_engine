package layer2.governance.pillars.risk

facts := input.pillars.risk.raw_facts
config := data.policy_config
weights := config.risk_weights

base := weights.tool_risk[facts.tool_risk_level]
multiplier := weights.sensitivity_multiplier[facts.sensitivity_level]

score := min([1, max([0, (((base * multiplier) + additive(facts.external_impact, weights.external_impact_additive)) + additive(facts.write_capability, weights.write_capability_additive)) + additive(facts.tool_requires_human_review, weights.requires_human_review_additive)])])

level := "high" if {
	score > config.thresholds.max_operational_risk
}

level := "medium" if {
	score <= config.thresholds.max_operational_risk
	score >= 0.3
}

level := "low" if {
	score < 0.3
}

result := {
	"score": score,
	"level": level,
	"high": level == "high",
	"raw_facts": facts,
	"score_components": input.pillars.risk.score_components,
	"score_formula_version": input.pillars.risk.score_formula_version,
	"thresholds_version": input.pillars.risk.thresholds_version,
}

additive(enabled, value) := value if {
	enabled == true
}

additive(enabled, _) := 0 if {
	enabled != true
}
