from __future__ import annotations

# Which pillar a reason code belongs to drives the headline breakdown chart of the
# evaluation, so it is declared here explicitly, code by code.
#
# This replaced substring heuristics over the code name. Those silently misattributed
# four codes: `step_exploration_budget_exhausted` and `unresolvable_revision` are
# Trajectory guards but matched no Trajectory keyword and fell through to the catch-all;
# `candidate_digest_mismatch` is Evidence family 1 but contains no "evidence" substring;
# and `external_low_actor_self_confidence` was pulled into the Judge purely by the word
# "confidence" although no judge verdict is involved. `GovernanceReasonCode` is a closed
# typed enum, so an exhaustive mapping is both possible and checkable - the coverage test
# fails when a new code is added without a pillar rather than letting it drift into an
# "unclassified" bucket that nobody reads.

EVIDENCE_PILLAR = "Evidence Pillar"
TRAJECTORY_PILLAR = "Trajectory Pillar"
SEMANTIC_JUDGE = "Semantic Judge"
BEHAVIORAL_RISK_PILLAR = "Behavioral / Risk Pillar"
STATIC_POLICY_SAFETY = "Static Policy & Safety"
EXTERNAL_EXECUTION_BOUNDARY = "External Execution Boundary"
RUNTIME_AVAILABILITY = "Runtime & Availability"
ROUTINE_PROGRESS = "Routine Progress"

REASON_CODE_PILLARS: dict[str, str] = {
    # Routine allow decisions: they dominate by volume and describe no intervention.
    "allowed_by_policy": ROUTINE_PROGRESS,
    "read_only_allowed": ROUTINE_PROGRESS,
    "local_output_allowed": ROUTINE_PROGRESS,
    "replan_allowed": ROUTINE_PROGRESS,
    "no_tool_request": ROUTINE_PROGRESS,
    # Evidence: referential integrity, digest binding, burden coverage, anchoring.
    "invalid_or_missing_selected_evidence": EVIDENCE_PILLAR,
    "evidence_request_limit_reached": EVIDENCE_PILLAR,
    "evidence_anchoring_requires_review": EVIDENCE_PILLAR,
    "invalid_evidence_ref": EVIDENCE_PILLAR,
    "missing_evidence_ref": EVIDENCE_PILLAR,
    "duplicate_evidence_ref": EVIDENCE_PILLAR,
    "empty_observation_content": EVIDENCE_PILLAR,
    "unresolvable_provenance": EVIDENCE_PILLAR,
    "unknown_step_lineage": EVIDENCE_PILLAR,
    "candidate_digest_mismatch": EVIDENCE_PILLAR,
    "missing_evidence_burden": EVIDENCE_PILLAR,
    "insufficient_independent_sources": EVIDENCE_PILLAR,
    "retrieved_content_missing": EVIDENCE_PILLAR,
    "title_only_evidence_fragility": EVIDENCE_PILLAR,
    "circular_evidence_reference": EVIDENCE_PILLAR,
    "unanchored_literals_in_selection": EVIDENCE_PILLAR,
    "unanchored_literals_in_universe": EVIDENCE_PILLAR,
    "unanchored_locators_in_selection": EVIDENCE_PILLAR,
    "unanchored_locators_in_universe": EVIDENCE_PILLAR,
    # Retired freshness family: nothing emits these, they stay classified so archived
    # pre-retirement traces still aggregate into the pillar they belonged to.
    "freshness_metadata_missing": EVIDENCE_PILLAR,
    "stale_evidence": EVIDENCE_PILLAR,
    "future_source_timestamp": EVIDENCE_PILLAR,
    # Trajectory: convergence, replanning, churn, exploration budget.
    "trajectory_degraded": TRAJECTORY_PILLAR,
    "actor_requested_replan": TRAJECTORY_PILLAR,
    "replan_request_stalled": TRAJECTORY_PILLAR,
    "replan_ceiling_reached": TRAJECTORY_PILLAR,
    "consecutive_tool_failures": TRAJECTORY_PILLAR,
    "action_churn": TRAJECTORY_PILLAR,
    "step_exploration_budget_exhausted": TRAJECTORY_PILLAR,
    "unchanged_candidate_resubmission": TRAJECTORY_PILLAR,
    "rejected_candidate_resubmitted": TRAJECTORY_PILLAR,
    "unresolvable_revision": TRAJECTORY_PILLAR,
    "action_revision_limit_reached": TRAJECTORY_PILLAR,
    "provider_failures_repeated": TRAJECTORY_PILLAR,
    "repeated_recovery": TRAJECTORY_PILLAR,
    # A decision produced by two pillars together keeps its own identity rather than
    # being credited to either one alone.
    "evidence_gap_and_trajectory_degraded": "Evidence + Trajectory",
    # Semantic judge verdicts only; actor self-confidence is a separate gate.
    "judge_reject_output": SEMANTIC_JUDGE,
    "judge_reject_external_or_high_risk": SEMANTIC_JUDGE,
    "judge_flag_external_draft": SEMANTIC_JUDGE,
    "judge_needs_revision": SEMANTIC_JUDGE,
    "judge_unsafe": SEMANTIC_JUDGE,
    "judge_uncertain": SEMANTIC_JUDGE,
    "judge_required_unavailable": SEMANTIC_JUDGE,
    # Shadow pillars, kept separate because they never influence a directive.
    "behavioral_anomaly_needs_corroboration": BEHAVIORAL_RISK_PILLAR,
    "behavioral_corroborated_risk": BEHAVIORAL_RISK_PILLAR,
    "high_operational_risk_external": BEHAVIORAL_RISK_PILLAR,
    # Static policy: the fixed, non-negotiable envelope.
    "static_policy_hard_deny": STATIC_POLICY_SAFETY,
    "denied_by_policy": STATIC_POLICY_SAFETY,
    "write_capability_blocked": STATIC_POLICY_SAFETY,
    "read_only_mode_enforced": STATIC_POLICY_SAFETY,
    "read_only_mutation_blocked": STATIC_POLICY_SAFETY,
    "database_profile_mutation_blocked": STATIC_POLICY_SAFETY,
    "sql_ambiguous_or_multiple": STATIC_POLICY_SAFETY,
    "prohibited_destination": STATIC_POLICY_SAFETY,
    # The boundary where work leaves the system: authorization, uncertainty, receipts.
    "capability_approval_required": EXTERNAL_EXECUTION_BOUNDARY,
    "external_capability_requested": EXTERNAL_EXECUTION_BOUNDARY,
    "external_communication_review_required": EXTERNAL_EXECUTION_BOUNDARY,
    "external_execution_reconciliation_required": EXTERNAL_EXECUTION_BOUNDARY,
    "external_low_actor_self_confidence": EXTERNAL_EXECUTION_BOUNDARY,
    # Infrastructure faults and operator-directed pauses: not a pillar judgement.
    "policy_version_mismatch": RUNTIME_AVAILABILITY,
    "required_pillar_unavailable": RUNTIME_AVAILABILITY,
    "opa_failure_fail_closed": RUNTIME_AVAILABILITY,
    "provider_runtime_action_missing": RUNTIME_AVAILABILITY,
    "ask_clarification_required": RUNTIME_AVAILABILITY,
    "human_review_required": RUNTIME_AVAILABILITY,
}

UNCLASSIFIED_PILLAR = "Unclassified"


# Resolve one raw governance reason code to its pillar for evaluation aggregation.
def classify_reason_code_pillar(code: str) -> str:
    return REASON_CODE_PILLARS.get(code.strip(), UNCLASSIFIED_PILLAR)


__all__ = [
    "REASON_CODE_PILLARS",
    "UNCLASSIFIED_PILLAR",
    "classify_reason_code_pillar",
]
