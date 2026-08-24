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
evidence_request_count := object.get(facts, "evidence_request_count", 0)
max_evidence_requests_before_review := object.get(data.policy_config.thresholds, "max_evidence_requests_before_review", 3)

coverage_evaluated := object.get(facts, "coverage_evaluated", false)
burden_satisfied := object.get(facts, "burden_satisfied", true)
factual_step_missing_evidence_burden := object.get(facts, "factual_step_missing_evidence_burden", false)
freshness_evaluated := object.get(facts, "freshness_evaluated", false)
freshness_satisfied := object.get(facts, "freshness_satisfied", true)
circular_reference_detected := object.get(facts, "circular_reference_detected", false)
has_unanchored_literals_in_selection := object.get(facts, "has_unanchored_literals_in_selection", false)
has_unanchored_literals_in_universe := object.get(facts, "has_unanchored_literals_in_universe", false)

default evidence_request_ceiling_reached := false

evidence_request_ceiling_reached if {
	evidence_request_count >= max_evidence_requests_before_review
}

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

evidence_gap if {
	coverage_evaluated == true
	burden_satisfied != true
}

evidence_gap if {
	factual_step_missing_evidence_burden == true
}

evidence_gap if {
	freshness_evaluated == true
	freshness_satisfied != true
}

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

evidence_gap if {
	circular_reference_detected == true
}

evidence_gap if {
	factual_completion == true
	has_unanchored_literals_in_selection == true
}

evidence_gap if {
	factual_completion == true
	has_unanchored_literals_in_universe == true
}

active_reason_codes := [code |
	some code in object.get(facts, "deficit_codes", [])
]

require_evidence := object.get(object.get(input, "pillar_requirements", {}), "require_evidence", false) == true

mode := "active" if {
	require_evidence
} else := "inactive"

decision_influence := true if {
	require_evidence
} else := false

result := {
	"mode": mode,
	"decision_influence": decision_influence,
	"evidence_gap": evidence_gap,
	"evidence_request_ceiling_reached": evidence_request_ceiling_reached,
	"diagnostic_score": object.get(object.get(input.pillars, "evidence", {}), "diagnostic_score", null),
	"diagnostic_level": object.get(object.get(input.pillars, "evidence", {}), "diagnostic_level", "unscored"),
	"reason_codes": active_reason_codes,
	"reason_facts": object.get(object.get(input.pillars, "evidence", {}), "reason_facts", []),
	"summary": object.get(object.get(input.pillars, "evidence", {}), "summary", "Evidence structural controls evaluated."),
	"raw_facts": facts,
	"score_components": object.get(object.get(input.pillars, "evidence", {}), "score_components", {}),
	"score_formula_version": "evidence_structural_v2",
	"thresholds_version": object.get(object.get(input.pillars, "evidence", {}), "thresholds_version", "unknown"),
}
