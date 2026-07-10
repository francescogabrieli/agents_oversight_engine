package layer2.governance.pillars.judge

facts := object.get(input.pillars.judge, "raw_facts", {})
config := data.policy_config
thresholds := config.thresholds

default reject_strong := false
default external_flag_strong := false
default strong_signal := false

reject_strong if {
  object.get(facts, "verdict", "approve") == "reject_output"
  object.get(facts, "confidence", 0) >= thresholds.judge_reject_confidence
}

external_flag_strong if {
  object.get(facts, "verdict", "approve") == "flag"
  object.get(facts, "confidence", 0) >= thresholds.judge_flag_external_confidence
  input.base_facts.external_communication.phase != null
}

result := {
  "invoked": object.get(facts, "invoked", false),
  "verdict": object.get(facts, "verdict", "approve"),
  "confidence": object.get(facts, "confidence", 0),
  "reject_strong": reject_strong,
  "external_flag_strong": external_flag_strong,
  "strong_signal": strong_signal,
  "raw_facts": facts,
  "score_components": object.get(input.pillars.judge, "score_components", {}),
  "score_formula_version": object.get(input.pillars.judge, "score_formula_version", "judge_v1"),
  "thresholds_version": object.get(input.pillars.judge, "thresholds_version", config.thresholds_version)
}

strong_signal if {
  reject_strong
}

strong_signal if {
  external_flag_strong
}
