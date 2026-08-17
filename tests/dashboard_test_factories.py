from layer2_runtime_governance import (
    ActorResultType,
    EvidenceQualityAssessment,
    GovernanceDecision,
    GovernanceDirective,
    GovernanceReasonCode,
    OperationalRiskAssessment,
    OversightAssessment,
    OversightReasonCode,
    OversightRecommendedDirective,
    OversightSignalLevel,
    PolicyDecision,
    ToolName,
    TrajectoryHealthAssessment,
)


# Build the canonical governance decision shared by pending-review dashboard scenarios.
def build_dashboard_governance_decision() -> GovernanceDecision:
    return GovernanceDecision(
        directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        policy_decision=PolicyDecision.REQUIRE_HUMAN_REVIEW,
        reason_codes=(GovernanceReasonCode.HUMAN_REVIEW_REQUIRED,),
        summary="Human review is required.",
        constrains_capability_use=True,
        evaluated_actor_result_type=ActorResultType.PROPOSE_ACTION,
        evaluated_tool_name=ToolName.WEB_SEARCH,
        policy_engine_name="test_policy",
        policy_bundle_version="test_v1",
        policy_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        oversight_recommended_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        oversight_summary="Oversight recommends escalation.",
    )


# Build the deterministic oversight assessment shared by dashboard scenarios.
def build_dashboard_oversight_assessment() -> OversightAssessment:
    return OversightAssessment(
        evidence_quality=EvidenceQualityAssessment(
            score=0.52,
            level=OversightSignalLevel.MODERATE,
            reason_codes=(OversightReasonCode.EVIDENCE_MARKED_SUFFICIENT,),
        ),
        operational_risk=OperationalRiskAssessment(
            score=0.78,
            level=OversightSignalLevel.HIGH,
            reason_codes=(OversightReasonCode.TOOL_REQUIRES_HUMAN_REVIEW,),
        ),
        trajectory_health=TrajectoryHealthAssessment(
            score=0.44,
            level=OversightSignalLevel.DEGRADED,
            reason_codes=(OversightReasonCode.RECOMMEND_HUMAN_REVIEW,),
        ),
        recommended_directive=OversightRecommendedDirective.WAIT_HUMAN_REVIEW,
        summary="Evidence and risk warrant human review.",
        reason_codes=(OversightReasonCode.RECOMMEND_HUMAN_REVIEW,),
    )
