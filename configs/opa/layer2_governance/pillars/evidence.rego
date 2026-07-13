package layer2.governance.pillars.evidence

facts := input.pillars.evidence.raw_facts
config := data.policy_config
weights := config.evidence_weights
thresholds := config.thresholds

raw_evidence_score := score if {
	score := (((((bool_score(facts.actor_evidence_sufficient) * weights.evidence_sufficient) + (bool_score(facts.actor_objective_satisfied) * weights.objective_satisfied)) + (facts.actor_confidence * weights.actor_confidence)) + (bool_score(facts.observed_evidence_present) * weights.observed_evidence_present)) + (bool_score(facts.source_locator_present) * weights.source_locator_present)) + (bool_score(facts.last_tool_succeeded) * weights.last_tool_success)
}

score := capped if {
	facts.support_rank_available < facts.support_rank_required
	capped := min([raw_evidence_score, thresholds.min_evidence_quality * 0.49])
}

score := raw_evidence_score if {
	facts.support_rank_available >= facts.support_rank_required
}

level := "strong" if {
	score >= thresholds.min_evidence_quality
}

level := "moderate" if {
	score < thresholds.min_evidence_quality
	score >= 0.35
}

level := "weak" if {
	score < 0.35
}

result := {
	"score": score,
	"raw_score": raw_evidence_score,
	"level": level,
	"evidence_gap": facts.support_rank_available < facts.support_rank_required,
	"precision_mismatch": facts.precision_mismatch,
	"raw_facts": facts,
	"score_components": input.pillars.evidence.score_components,
	"score_formula_version": input.pillars.evidence.score_formula_version,
	"thresholds_version": input.pillars.evidence.thresholds_version,
}

bool_score(value) := 1 if {
	value == true
}

bool_score(value) := 0 if {
	value != true
}
