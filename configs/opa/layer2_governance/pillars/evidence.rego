package layer2.governance.pillars.evidence

import rego.v1

facts := object.get(object.get(input.pillars, "evidence", {}), "raw_facts", {})
factual_completion := object.get(facts, "factual_completion_attempt", false)
refs_valid := object.get(facts, "selected_evidence_refs_valid", true)
# Evidence is event-specific. References are optional, but every reference the actor
# selects must resolve to a concrete observation recorded by the runtime.
default evidence_gap := false
evidence_gap := true if {
    factual_completion == true
    refs_valid != true
}

result := {
    "evidence_gap": evidence_gap,
    "raw_facts": facts,
    "score_components": object.get(object.get(input.pillars, "evidence", {}), "score_components", {}),
    "score_formula_version": object.get(object.get(input.pillars, "evidence", {}), "score_formula_version", "evidence_facts_v3"),
    "thresholds_version": object.get(object.get(input.pillars, "evidence", {}), "thresholds_version", "unknown"),
}
