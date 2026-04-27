package layer2.governance

# Layer 2 governance policy bundle.
# The Python adapter posts a typed input document to this package and reads back `decision`.

default decision = {
  "policy_decision": "allow",
  "reason_codes": ["allowed_by_policy"],
  "summary": "Allowed by default."
}

decision = {
  "policy_decision": "deny",
  "reason_codes": ["write_capability_blocked"],
  "summary": "Write-like capability is blocked by policy."
} {
  write_capability_blocked
}

decision = {
  "policy_decision": "deny",
  "reason_codes": ["tool_disallowed"],
  "summary": "Requested tool is explicitly disallowed."
} {
  not write_capability_blocked
  tool_is_disallowed
}

decision = {
  "policy_decision": "require_human_review",
  "reason_codes": ["tool_not_allowed"],
  "summary": "Requested tool is outside the allowed tool list."
} {
  not write_capability_blocked
  not tool_is_disallowed
  allowed_tool_list_present
  input.tool.name != null
  not tool_is_allowed
}

decision = {
  "policy_decision": "allow_read_only",
  "reason_codes": ["read_only_allowed"],
  "summary": "Read-only capability use is allowed by the active policy."
} {
  not write_capability_blocked
  not tool_is_disallowed
  input.tool.name != null
  read_only_tool_request
  not allowed_tool_list_present
}

decision = {
  "policy_decision": "allow_read_only",
  "reason_codes": ["read_only_allowed"],
  "summary": "Read-only capability use is allowed by the active policy."
} {
  not write_capability_blocked
  not tool_is_disallowed
  input.tool.name != null
  read_only_tool_request
  allowed_tool_list_present
  tool_is_allowed
}

decision = {
  "policy_decision": "ask_clarification",
  "reason_codes": ["ask_clarification_required"],
  "summary": "The actor requested clarification."
} {
  not write_capability_blocked
  not tool_is_disallowed
  not read_only_tool_request
  clarification_requested
}

decision = {
  "policy_decision": "allow",
  "reason_codes": ["local_output_allowed"],
  "summary": "Local output and replanning remain within policy."
} {
  not write_capability_blocked
  not tool_is_disallowed
  input.tool.name == null
  not read_only_tool_request
  not clarification_requested
}

write_capability_blocked {
  input.actor.external_impact == true
}

tool_is_disallowed {
  input.tool.name != null
  input.tool.name == input.policy.disallowed_tools[_]
}

allowed_tool_list_present {
  count(input.policy.allowed_tools) > 0
}

tool_is_allowed {
  input.tool.name != null
  input.tool.name == input.policy.allowed_tools[_]
}

read_only_tool_request {
  input.actor.capability_mode == "read_only"
}

clarification_requested {
  input.actor.result_type == "ask_clarification"
}
