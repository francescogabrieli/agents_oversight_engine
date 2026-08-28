package layer2.governance.pillars.evidence

import rego.v1

facts := object.get(object.get(input.pillars, "evidence", {}), "raw_facts", {})
completion_attempt := object.get(facts, "completion_attempt", false)
factual_completion := object.get(facts, "factual_completion_attempt", false)
evidence_completion := object.get(facts, "evidence_completion_attempt", factual_completion)
refs_valid := object.get(facts, "selected_evidence_refs_valid", true)
refs_unique := object.get(facts, "selected_evidence_refs_unique", true)
observation_ids_unique := object.get(facts, "observation_ids_unique", true)
content_present := object.get(facts, "selected_observations_have_content", true)
provenance_resolvable := object.get(facts, "selected_observations_provenance_resolvable", true)
known_steps := object.get(facts, "selected_observations_belong_to_known_steps", true)
all_title_only := object.get(facts, "selected_evidence_all_title_only", false)
candidate_binding_required := object.get(facts, "candidate_evidence_binding_required", evidence_completion)
candidate_binding_present := object.get(facts, "candidate_evidence_binding_present", false)
candidate_snapshot_matches := object.get(facts, "candidate_evidence_snapshot_matches", false)
candidate_selection_matches := object.get(facts, "candidate_evidence_selection_matches", false)
candidate_operation_matches := object.get(facts, "candidate_external_operation_digest_matches", false)
evidence_request_count := object.get(facts, "evidence_request_count", 0)
evidence_request_episode_count := object.get(facts, "evidence_request_episode_count", evidence_request_count)
max_evidence_requests_before_review := object.get(data.policy_config.thresholds, "max_evidence_requests_before_review", 3)

coverage_evaluated := object.get(facts, "coverage_evaluated", false)
independent_sources_satisfied := object.get(facts, "independent_sources_satisfied", true)
retrieved_content_satisfied := object.get(facts, "retrieved_content_satisfied", true)
insufficiency_declaration_permitted := object.get(facts, "insufficiency_declaration_permitted", false)
insufficiency_declared_in_payload := object.get(facts, "insufficiency_declared_in_payload", false)
circular_reference_detected := object.get(facts, "circular_reference_detected", false)
has_unanchored_literals_in_selection := object.get(facts, "has_unanchored_literals_in_selection", false)
has_unanchored_literals_in_universe := object.get(facts, "has_unanchored_literals_in_universe", false)
has_unanchored_locators_in_selection := object.get(facts, "has_unanchored_locators_in_selection", false)
has_unanchored_locators_in_universe := object.get(facts, "has_unanchored_locators_in_universe", false)
anchoring_concern_adjudicated := object.get(facts, "anchoring_concern_adjudicated", false)

default evidence_request_ceiling_reached := false

evidence_request_ceiling_reached if {
	evidence_request_episode_count >= max_evidence_requests_before_review
}

# Two structural classes with different remediation semantics. A violation is
# resolvable by retrieving or selecting differently, so it goes back to the actor.
# A concern is not: no further retrieval can turn a computed value into a quoted
# one, and the actor's only available fix is deleting the claim. Only a human can
# tell an invented literal from a derived one, so a concern escalates instead.
default evidence_violation := false
default evidence_concern := false
default enforced_burden_satisfied := false

enforced_burden_satisfied if {
	independent_sources_satisfied == true
	retrieved_content_satisfied == true
}

# A task may authorize declaring the shortfall instead of meeting the source count.
enforced_burden_satisfied if {
	insufficiency_declaration_permitted == true
	insufficiency_declared_in_payload == true
}

evidence_violation if {
	completion_attempt == true
	refs_valid != true
}

evidence_violation if {
	completion_attempt == true
	refs_unique != true
}

evidence_violation if {
	completion_attempt == true
	observation_ids_unique != true
}

evidence_violation if {
	completion_attempt == true
	content_present != true
}

evidence_violation if {
	completion_attempt == true
	provenance_resolvable != true
}

evidence_violation if {
	completion_attempt == true
	known_steps != true
}

evidence_violation if {
	coverage_evaluated == true
	enforced_burden_satisfied != true
}

evidence_violation if {
	evidence_completion == true
	all_title_only == true
}

evidence_violation if {
	candidate_binding_required
	candidate_binding_present != true
}

evidence_violation if {
	candidate_binding_required
	candidate_snapshot_matches != true
}

evidence_violation if {
	candidate_binding_required
	candidate_selection_matches != true
}

evidence_violation if {
	candidate_binding_required
	candidate_operation_matches != true
}

evidence_violation if {
	evidence_completion == true
	circular_reference_detected == true
}

# A locator the completion names but never retrieved is resolvable by retrieval:
# the actor fetches what it cited, or removes a source that does not exist.
evidence_violation if {
	evidence_completion == true
	has_unanchored_locators_in_selection == true
}

evidence_violation if {
	evidence_completion == true
	has_unanchored_locators_in_universe == true
}

# Without the adjudication guard the gate never terminates: a review resumes the
# actor, the actor recompletes with the same values, and the same concern escalates
# again. The remediation loop is bounded by its request ceiling; this one is bounded
# by the human ruling on exactly this selection.
evidence_concern if {
	evidence_completion == true
	has_unanchored_literals_in_selection == true
	not anchoring_concern_adjudicated
}

evidence_concern if {
	evidence_completion == true
	has_unanchored_literals_in_universe == true
	not anchoring_concern_adjudicated
}

# The union keeps the historical gate name meaningful: any structural deficit still
# blocks judge admission and completion, whichever class produced it.
default evidence_gap := false

evidence_gap if evidence_violation

evidence_gap if evidence_concern

# Every deficit the runtime observes is enforced; the pillar no longer reports a
# structural finding it cannot act on. These codes reach the decision itself, so
# remediation and the evaluation store read the same vocabulary the rules used.
active_reason_codes := object.get(facts, "deficit_codes", [])

# Remediation is built from the codes a decision carries, so the classes must be
# separable: telling the actor to "remove these literals" alongside a fixable
# deficit is how a correct derived value gets silently deleted.
anchoring_reason_codes := {
	"unanchored_literals_in_selection",
	"unanchored_literals_in_universe",
}

violation_reason_codes := [code |
	some code in active_reason_codes
	not anchoring_reason_codes[code]
]

concern_reason_codes := [code |
	some code in active_reason_codes
	anchoring_reason_codes[code]
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
	"evidence_violation": evidence_violation,
	"evidence_concern": evidence_concern,
	"violation_reason_codes": violation_reason_codes,
	"concern_reason_codes": concern_reason_codes,
	"evidence_request_ceiling_reached": evidence_request_ceiling_reached,
	"enforced_burden_satisfied": enforced_burden_satisfied,
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
