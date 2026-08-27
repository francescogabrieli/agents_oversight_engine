package layer2.governance.pillars.static_policy

import rego.v1

operation := object.get(input.base_facts, "external_operation", {})
payload := object.get(operation, "payload", {})
effect := object.get(operation, "normalized_effect", "unknown")
profile_id := object.get(operation, "capability_profile_id", null)
target := object.get(operation, "target", {})
static_config := object.get(data.policy_config, "static_policy", {})
mutating_effects := {"create", "update", "delete", "truncate", "send", "publish", "execute", "unknown"}

default hard_deny := false
default read_only_required := false
default clarification_signaled := false

hard_deny if {
	input.base_facts.policy.read_only_mode == true
	effect in mutating_effects
	object.get(operation, "execution_candidate", false) == true
}

# The read-only profile list is configuration, not a literal: a new read-only database
# profile must be declared to be recognized rather than silently bypass this gate.
is_read_only_profile if {
	profile_id in object.get(static_config, "read_only_profile_ids", [])
}

is_read_only_profile if {
	object.get(payload, "is_read_only_profile", false) == true
}

hard_deny if {
	is_read_only_profile
	object.get(payload, "statement_class", "unknown") != "select"
}

hard_deny if {
	is_read_only_profile
	object.get(payload, "ambiguous", false) == true
}

hard_deny if {
	prohibited := object.get(static_config, "prohibited_destinations", [])
	object.get(target, "value", "") in prohibited
}

hard_deny if {
	prohibited_domains := object.get(static_config, "prohibited_destination_domains", [])
	some recipient in normalized_recipients
	some domain in prohibited_domains
	endswith(lower(recipient), concat("", ["@", lower(domain)]))
}

hard_deny if {
	prohibited := object.get(static_config, "prohibited_destinations", [])
	some recipient in normalized_recipients
	recipient in {lower(item) | some item in prohibited}
}

hard_deny if {
	object.get(payload, "destination_class", "unknown") == "prohibited"
}

read_only_required if {
	input.base_facts.policy.read_only_mode == true
	not hard_deny
}

read_only_required if {
	effect == "read"
	object.get(operation, "boundary", "local") != "local"
}

clarification_signaled if {
	input.base_facts.actor.result_type == "ask_clarification"
}

constraints contains "execution_policy_read_only" if {
	input.base_facts.policy.read_only_mode == true
}

constraints contains "database_profile_select_only" if {
	is_read_only_profile
}

violations contains "read_only_mode_mutating_effect" if {
	input.base_facts.policy.read_only_mode == true
	effect in mutating_effects
	object.get(operation, "execution_candidate", false) == true
}

violations contains "database_profile_mutation" if {
	is_read_only_profile
	object.get(payload, "statement_class", "unknown") != "select"
}

violations contains "sql_ambiguous_or_multiple" if {
	is_read_only_profile
	object.get(payload, "ambiguous", false) == true
}

violations contains "prohibited_destination" if {
	prohibited := object.get(static_config, "prohibited_destinations", [])
	object.get(target, "value", "") in prohibited
}

violations contains "prohibited_destination" if {
	prohibited_domains := object.get(static_config, "prohibited_destination_domains", [])
	some recipient in normalized_recipients
	some domain in prohibited_domains
	endswith(lower(recipient), concat("", ["@", lower(domain)]))
}

violations contains "prohibited_destination" if {
	prohibited := object.get(static_config, "prohibited_destinations", [])
	some recipient in normalized_recipients
	recipient in {lower(item) | some item in prohibited}
}

violations contains "prohibited_destination" if {
	object.get(payload, "destination_class", "unknown") == "prohibited"
}

reason_codes contains "read_only_mutation_blocked" if {
	"read_only_mode_mutating_effect" in violations
}

reason_codes contains "database_profile_mutation_blocked" if {
	"database_profile_mutation" in violations
}

reason_codes contains "sql_ambiguous_or_multiple" if {
	"sql_ambiguous_or_multiple" in violations
}

reason_codes contains "prohibited_destination" if {
	"prohibited_destination" in violations
}

reason_codes contains "read_only_allowed" if {
	read_only_required
	not hard_deny
}

reason_codes contains "ask_clarification_required" if {
	clarification_signaled
	not hard_deny
}

normalized_recipients contains lower(trim_space(item)) if {
	recipient := object.get(payload, "recipient", "")
	item := split(recipient, ",")[_]
	trim_space(item) != ""
}

normalized_recipients contains lower(trim_space(item)) if {
	some item in object.get(payload, "cc", [])
	trim_space(item) != ""
}

normalized_recipients contains lower(trim_space(item)) if {
	some item in object.get(payload, "bcc", [])
	trim_space(item) != ""
}

summary := "Static policy hard-denied one or more deterministic violations." if {
	hard_deny
} else := "Static policy preserved the read-only execution envelope." if {
	read_only_required
} else := "Static policy preserved the actor clarification request." if {
	clarification_signaled
} else := "Static deterministic guardrails passed."

result := {
	"hard_deny": hard_deny,
	"constraints": sort([item | item := constraints[_]]),
	"violations": sort([item | item := violations[_]]),
	"reason_codes": sort([item | item := reason_codes[_]]),
	"summary": summary,
	"read_only_required": read_only_required,
	"clarification_signaled": clarification_signaled,
}
