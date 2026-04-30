package layer2.governance

# Layer 2 governance policy bundle.
# The Python adapter posts a typed input document to this package and reads back `decision`.

default decision := {
  "policy_decision": "allow",
  "reason_codes": ["allowed_by_policy"],
  "summary": "Allowed by default."
}

decision := {
  "policy_decision": "deny",
  "reason_codes": ["write_capability_blocked"],
  "summary": "Write-like capability is blocked by policy."
} if {
  write_capability_blocked
}

decision := {
  "policy_decision": "allow_read_only",
  "reason_codes": ["read_only_allowed"],
  "summary": "Read-only capability use is allowed by the active policy."
} if {
  not write_capability_blocked
  input.tool.name != null
  read_only_tool_request
}

decision := {
  "policy_decision": "ask_clarification",
  "reason_codes": ["ask_clarification_required"],
  "summary": "The actor requested clarification."
} if {
  not write_capability_blocked
  not read_only_tool_request
  clarification_requested
}

decision := {
  "policy_decision": "allow",
  "reason_codes": ["local_output_allowed"],
  "summary": "Local output and replanning remain within policy."
} if {
  not write_capability_blocked
  input.tool.name == null
  not read_only_tool_request
  not clarification_requested
}

write_capability_blocked if {
  input.actor.external_impact == true
}

write_capability_blocked if {
  input.actor.capability_mode == "write"
}

read_only_tool_request if {
  input.actor.capability_mode == "read_only"
}

clarification_requested if {
  input.actor.result_type == "ask_clarification"
}
