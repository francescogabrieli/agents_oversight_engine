package layer2.evaluation

default compliance := {
	"status": "inconclusive",
	"findings": [],
	"policy_bundle_version": "layer2_governance_v3",
}

compliance := {
	"status": status,
	"findings": findings,
	"policy_bundle_version": "layer2_governance_v3",
}

status := "inconclusive" if {
	input.derived.completeness == "inconclusive"
}

status := "inconclusive" if {
	input.derived.completeness == "partial"
}

status := "fail" if {
	input.derived.completeness == "complete"
	count(findings) > 0
}

status := "pass" if {
	input.derived.completeness == "complete"
	count(findings) == 0
}

findings contains "deny_must_come_from_static_policy" if {
	some decision in input.trace.governance_passes
	decision.directive == "stop"
	not decision.policy_decision == "deny"
}

findings contains "hitl_directive_requires_human_review" if {
	some decision in input.trace.governance_passes
	decision.directive == "wait_human_review"
	decision.requires_human_review != true
}

findings contains "evidence_precision_not_supported" if {
	some decision in input.trace.governance_passes
	evidence := decision.pillar_results.evidence
	evidence.precision_mismatch == true
	decision.directive == "continue"
}

findings contains "replan_limit_not_stopped" if {
	some decision in input.trace.governance_passes
	trajectory := decision.pillar_results.trajectory
	trajectory.replan_limit_exceeded == true
	decision.directive != "stop"
}

findings contains "provider_loop_not_replanned_or_reviewed" if {
	some decision in input.trace.governance_passes
	trajectory := decision.pillar_results.trajectory
	trajectory.provider_runtime_action_missing == true
	not decision.directive in {"replan", "wait_human_review", "stop"}
}

findings contains "fail_closed_not_reviewed" if {
	some decision in input.trace.governance_passes
	"opa_failure_fail_closed" in decision.reason_codes
	decision.directive != "wait_human_review"
}

findings contains "behavioral_pillar_not_shadow_only" if {
	some decision in input.trace.governance_passes
	behavioral := decision.pillar_results.behavioral
	behavioral.mode != "shadow"
}

findings contains "behavioral_pillar_not_shadow_only" if {
	some decision in input.trace.governance_passes
	decision.pillar_results.behavioral.decision_influence != false
}

findings contains "operational_risk_pillar_not_shadow_only" if {
	some decision in input.trace.governance_passes
	risk := decision.pillar_results.risk
	risk.mode != "shadow"
}

findings contains "operational_risk_pillar_not_shadow_only" if {
	some decision in input.trace.governance_passes
	decision.pillar_results.risk.decision_influence != false
}

findings contains "judge_used_as_hard_deny" if {
	some decision in input.trace.governance_passes
	decision.policy_decision == "deny"
	decision.pillar_results.judge.invoked == true
	decision.pillar_results.static_policy.hard_deny != true
}

findings contains "required_judge_unavailable_not_reviewed" if {
	some decision in input.trace.governance_passes
	decision.pillar_requirements.require_judge == true
	decision.pillar_results.judge.available != true
	decision.directive != "wait_human_review"
}

findings contains "judge_digest_mismatch_not_reviewed" if {
	some decision in input.trace.governance_passes
	decision.pillar_requirements.require_judge == true
	decision.pillar_results.judge.digest_matches != true
	decision.directive != "wait_human_review"
}

findings contains "revision_directive_without_revision_verdict" if {
	some decision in input.trace.governance_passes
	decision.directive == "revise_action"
	decision.pillar_results.judge.verdict != "needs_revision"
}

# Aggregated search fallback text cannot represent extracted source-level content.
findings contains "web_search_evidence_support_overstated" if {
	some evidence in input.trace.observed_evidence
	evidence.source_tool == "web_search"
	endswith(evidence.evidence_id, ":fallback")
	evidence.support_level in {"detailed_extracted", "primary_legal"}
}

# A fallback tied only to the search query does not preserve source-level provenance.
findings contains "web_search_fallback_missing_source_locator" if {
	some evidence in input.trace.observed_evidence
	evidence.source_tool == "web_search"
	endswith(evidence.evidence_id, ":fallback")
	not startswith(lower(evidence.locator), "http://")
	not startswith(lower(evidence.locator), "https://")
}

# The final actor and governance views must expose the same deterministic mismatch fact.
findings contains "actor_governance_precision_mismatch_disagrees" if {
	count(input.trace.governance_passes) > 0
	final_index := count(input.trace.governance_passes) - 1
	final_decision := input.trace.governance_passes[final_index]
	input.trace.actor.precision_mismatch_detected != final_decision.pillar_results.evidence.precision_mismatch
}

# Scored OPA evidence must not be serialized with a stale default diagnostic level.
findings contains "evidence_score_level_inconsistent" if {
	some decision in input.trace.governance_passes
	decision.pillar_results.evidence.score >= data.policy_config.thresholds.min_evidence_quality
	decision.pillar_results.evidence.level != "strong"
}

findings contains "evidence_score_level_inconsistent" if {
	some decision in input.trace.governance_passes
	decision.pillar_results.evidence.score < 0.35
	decision.pillar_results.evidence.level != "weak"
}
