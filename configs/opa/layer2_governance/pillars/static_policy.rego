package layer2.governance.pillars.static_policy

default result := {
  "write_blocked": false,
  "read_only_required": false,
  "hitl_mandatory": false,
  "clarification_signaled": false,
  "deny_reason": null,
  "reason_codes": []
}

result := {
  "write_blocked": write_blocked,
  "read_only_required": read_only_required,
  "hitl_mandatory": hitl_mandatory,
  "clarification_signaled": clarification_signaled,
  "deny_reason": deny_reason,
  "reason_codes": reason_codes
}

write_blocked if {
  input.base_facts.actor.capability_mode == "write"
}

write_blocked if {
  input.base_facts.actor.external_impact == true
}

read_only_required if {
  input.base_facts.policy.read_only_mode == true
}

read_only_required if {
  input.base_facts.actor.capability_mode == "read_only"
}

hitl_mandatory if {
  input.base_facts.external_communication.review_ready == true
}

clarification_signaled if {
  input.base_facts.actor.result_type == "ask_clarification"
}

deny_reason := "write capability or external impact blocked by policy" if {
  write_blocked
}

deny_reason := null if {
  not write_blocked
}

reason_codes contains "write_capability_blocked" if {
  write_blocked
}

reason_codes contains "read_only_allowed" if {
  read_only_required
}

reason_codes contains "external_communication_review_required" if {
  hitl_mandatory
}

reason_codes contains "ask_clarification_required" if {
  clarification_signaled
}
