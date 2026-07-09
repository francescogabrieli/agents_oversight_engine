package layer2.governance.pillars.behavioral

facts := object.get(input.pillars.behavioral, "raw_facts", {})
thresholds := input.policy_config.thresholds

high_novelty if {
  object.get(facts, "novelty_score", 0) >= thresholds.behavioral_high_novelty
}

high_deviation if {
  object.get(facts, "deviation_score", 0) >= thresholds.behavioral_high_deviation
}

history_sparse if {
  object.get(facts, "matched_run_count", 0) < 3
}

result := {
  "strong": strong,
  "high_novelty": high_novelty,
  "high_deviation": high_deviation,
  "history_sparse": history_sparse,
  "raw_facts": facts,
  "score_components": object.get(input.pillars.behavioral, "score_components", {}),
  "score_formula_version": object.get(input.pillars.behavioral, "score_formula_version", "behavioral_features_v2"),
  "thresholds_version": object.get(input.pillars.behavioral, "thresholds_version", input.policy_config.thresholds_version)
}

strong if {
  high_novelty
}

strong if {
  high_deviation
}
