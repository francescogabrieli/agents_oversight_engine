package layer2.governance.pillars.evidence

import rego.v1

facts := object.get(object.get(input.pillars, "evidence", {}), "raw_facts", {})
factual_completion := object.get(facts, "factual_completion_attempt", false)
refs_valid := object.get(facts, "selected_evidence_refs_valid", true)
refs_unique := object.get(facts, "selected_evidence_refs_unique", true)
observation_ids_unique := object.get(facts, "observation_ids_unique", true)
content_present := object.get(facts, "selected_observations_have_content", true)
provenance_resolvable := object.get(facts, "selected_observations_provenance_resolvable", true)
known_steps := object.get(facts, "selected_observations_belong_to_known_steps", true)
all_title_only := object.get(facts, "selected_evidence_all_title_only", false)
candidate_binding_required := factual_completion
candidate_binding_present := object.get(facts, "candidate_evidence_binding_present", false)
candidate_snapshot_matches := object.get(facts, "candidate_evidence_snapshot_matches", false)
candidate_selection_matches := object.get(facts, "candidate_evidence_selection_matches", false)
candidate_operation_matches := object.get(facts, "candidate_external_operation_digest_matches", false)

# Evidence is event-specific. References are optional, but every reference the actor
# selects must resolve to a concrete observation recorded by the runtime.
default evidence_gap := false

evidence_gap if {
	factual_completion == true
	refs_valid != true
}

evidence_gap if {
	factual_completion == true
	refs_unique != true
}

evidence_gap if {
	factual_completion == true
	observation_ids_unique != true
}

evidence_gap if {
	factual_completion == true
	content_present != true
}

evidence_gap if {
	factual_completion == true
	provenance_resolvable != true
}

evidence_gap if {
	factual_completion == true
	known_steps != true
}

# A title-only selection is structurally fragile and must acquire direct support first.
evidence_gap if {
	factual_completion == true
	all_title_only == true
}

evidence_gap if {
	candidate_binding_required
	candidate_binding_present != true
}

evidence_gap if {
	candidate_binding_required
	candidate_snapshot_matches != true
}

evidence_gap if {
	candidate_binding_required
	candidate_selection_matches != true
}

evidence_gap if {
	candidate_binding_required
	candidate_operation_matches != true
}

result := {
	"evidence_gap": evidence_gap,
	"diagnostic_score": object.get(object.get(input.pillars, "evidence", {}), "diagnostic_score", null),
	"diagnostic_level": object.get(object.get(input.pillars, "evidence", {}), "diagnostic_level", "unscored"),
	"reason_facts": object.get(object.get(input.pillars, "evidence", {}), "reason_facts", []),
	"summary": object.get(object.get(input.pillars, "evidence", {}), "summary", "Evidence integrity and provenance observed."),
	"raw_facts": facts,
	"score_components": object.get(object.get(input.pillars, "evidence", {}), "score_components", {}),
	"score_formula_version": object.get(object.get(input.pillars, "evidence", {}), "score_formula_version", "evidence_integrity_provenance_v1"),
	"thresholds_version": object.get(object.get(input.pillars, "evidence", {}), "thresholds_version", "unknown"),
}
