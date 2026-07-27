package layer2.governance.pillars.evidence

import rego.v1

facts := object.get(object.get(input.pillars, "evidence", {}), "raw_facts", {})
factual_completion := object.get(facts, "factual_completion_attempt", false)
refs_valid := object.get(facts, "selected_evidence_refs_valid", true)
external_candidate := object.get(facts, "external_operation_candidate", false)
selected_ref_count := object.get(facts, "selected_evidence_ref_count", 0)

# Evidence is event-specific. It evaluates references and observed source properties, never ranks source classes.
default evidence_gap := false
evidence_gap := true if {
    factual_completion == true
    refs_valid != true
}

evidence_gap := true if {
    external_candidate == true
    selected_ref_count == 0
}

result := {
    "evidence_gap": evidence_gap,
    "raw_facts": facts,
    "score_components": object.get(object.get(input.pillars, "evidence", {}), "score_components", {}),
    "score_formula_version": object.get(object.get(input.pillars, "evidence", {}), "score_formula_version", "evidence_facts_v3"),
    "thresholds_version": object.get(object.get(input.pillars, "evidence", {}), "thresholds_version", "unknown"),
}
