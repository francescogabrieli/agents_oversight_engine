from __future__ import annotations

from layer4_state_evidence_observability.behavioral import BehavioralRecommendedDirective
from ..judge import JudgeRecommendedDirective
from .contracts import (
    GovernanceDirective,
)


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
    recommended_directive: JudgeRecommendedDirective | None,
) -> GovernanceDirective | None:
    if recommended_directive is None or recommended_directive == JudgeRecommendedDirective.NOT_INVOKED:
        return None
    if recommended_directive == JudgeRecommendedDirective.WAIT_HUMAN_REVIEW:
        return GovernanceDirective.WAIT_HUMAN_REVIEW
    if recommended_directive == JudgeRecommendedDirective.REPLAN:
        return GovernanceDirective.REPLAN
    if recommended_directive == JudgeRecommendedDirective.REQUEST_MORE_EVIDENCE:
        return GovernanceDirective.REQUEST_MORE_EVIDENCE
    if recommended_directive == JudgeRecommendedDirective.CONTINUE:
        return GovernanceDirective.CONTINUE
    return None


__all__ = [
    "map_behavioral_directive",
    "map_judge_directive",
]
