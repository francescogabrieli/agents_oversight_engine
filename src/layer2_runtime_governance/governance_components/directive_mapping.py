from __future__ import annotations

from ..actor_contract import ActorResultType
from ..behavioral import BehavioralRecommendedDirective
from ..judge import JudgeRecommendedDirective
from .contracts import (
    GovernanceDirective,
    PolicyDecision,
    PolicyEvaluationInput,
    PolicyEvaluationResult,
    StaticPolicyAssessment,
)


# Map the deterministic static policy envelope onto one runtime directive.
def derive_governance_directive(
    *,
    policy_input: PolicyEvaluationInput,
    policy_result: PolicyEvaluationResult | None = None,
    static_policy_assessment: StaticPolicyAssessment | None = None,
) -> GovernanceDirective:
    resolved_policy_decision = (
        static_policy_assessment.policy_decision
        if static_policy_assessment is not None
        else policy_result.policy_decision
        if policy_result is not None
        else None
    )
    if resolved_policy_decision is None:
        raise ValueError(
            "derive_governance_directive requires a policy result or static assessment."
        )
    if resolved_policy_decision == PolicyDecision.DENY:
        return GovernanceDirective.STOP
    if resolved_policy_decision == PolicyDecision.REQUIRE_HUMAN_REVIEW:
        return GovernanceDirective.WAIT_HUMAN_REVIEW
    if resolved_policy_decision == PolicyDecision.ASK_CLARIFICATION:
        return GovernanceDirective.ASK_USER_CLARIFICATION
    if policy_input.actor_result_type == ActorResultType.NEEDS_REPLAN:
        return GovernanceDirective.REPLAN
    if resolved_policy_decision == PolicyDecision.ALLOW_READ_ONLY:
        return GovernanceDirective.CONTINUE_READ_ONLY
    return GovernanceDirective.CONTINUE


# Convert one behavioural recommendation into the shared runtime vocabulary.
def map_behavioral_directive(
    recommended_directive: BehavioralRecommendedDirective,
) -> GovernanceDirective:
    if recommended_directive == BehavioralRecommendedDirective.WAIT_HUMAN_REVIEW:
        return GovernanceDirective.WAIT_HUMAN_REVIEW
    if recommended_directive == BehavioralRecommendedDirective.REPLAN:
        return GovernanceDirective.REPLAN
    if recommended_directive == BehavioralRecommendedDirective.REQUEST_MORE_EVIDENCE:
        return GovernanceDirective.REQUEST_MORE_EVIDENCE
    return GovernanceDirective.CONTINUE


# Convert one judge recommendation into the shared runtime vocabulary.
def map_judge_directive(
    recommended_directive: JudgeRecommendedDirective,
) -> GovernanceDirective:
    if recommended_directive == JudgeRecommendedDirective.WAIT_HUMAN_REVIEW:
        return GovernanceDirective.WAIT_HUMAN_REVIEW
    if recommended_directive == JudgeRecommendedDirective.REPLAN:
        return GovernanceDirective.REPLAN
    if recommended_directive == JudgeRecommendedDirective.REQUEST_MORE_EVIDENCE:
        return GovernanceDirective.REQUEST_MORE_EVIDENCE
    return GovernanceDirective.CONTINUE


__all__ = [
    "derive_governance_directive",
    "map_behavioral_directive",
    "map_judge_directive",
]
