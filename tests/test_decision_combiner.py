from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from layer2_runtime_governance.behavioral import (
    BehavioralAssessment,
    BehavioralRecommendedDirective,
)
from layer2_runtime_governance.decision_combiner import combine_decision_signals
from layer2_runtime_governance.governance import (
    GovernanceDirective,
    PolicyDecision,
    PolicyEvaluationInput,
    PolicyEvaluationResult,
    StaticPolicyAssessment,
    build_governance_decision,
    combine_governance_directives,
)
from layer2_runtime_governance.judge import JudgeAssessment, JudgeRecommendedDirective
from layer2_runtime_governance.oversight import (
    DynamicPolicyAssessment,
    DynamicPolicyRecommendedDirective,
    EvidenceQualityAssessment,
    OperationalRiskAssessment,
    OversightReasonCode,
    OversightSignalLevel,
    TrajectoryHealthAssessment,
)
from layer2_runtime_governance.actor_contract import ActorResultType, CapabilityAccessMode
from layer2_runtime_governance.state import ExternalCommunicationPhase, RuntimeState


# Build a compact static-policy fixture for combiner tests.
def _static_policy(
    *,
    decision: PolicyDecision = PolicyDecision.ALLOW,
    directive: GovernanceDirective = GovernanceDirective.CONTINUE,
) -> StaticPolicyAssessment:
    return StaticPolicyAssessment(
        policy_decision=decision,
        reason_codes=(),
        summary=f"Static policy returned {decision.value}.",
        policy_engine_name="test_policy_engine",
        policy_bundle_version="test_bundle_v1",
        policy_directive=directive,
    )


# Build a dynamic-policy assessment with controllable evidence, risk, and trajectory signals.
def _dynamic_policy(
    *,
    recommended_directive: DynamicPolicyRecommendedDirective = DynamicPolicyRecommendedDirective.CONTINUE,
    evidence_score: float = 0.92,
    evidence_level: OversightSignalLevel = OversightSignalLevel.STRONG,
    evidence_reason_codes: tuple[OversightReasonCode, ...] = (),
    risk_score: float = 0.18,
    risk_level: OversightSignalLevel = OversightSignalLevel.LOW,
    risk_reason_codes: tuple[OversightReasonCode, ...] = (),
    trajectory_score: float = 0.90,
    trajectory_level: OversightSignalLevel = OversightSignalLevel.HEALTHY,
    trajectory_reason_codes: tuple[OversightReasonCode, ...] = (),
) -> DynamicPolicyAssessment:
    return DynamicPolicyAssessment(
        evidence_quality=EvidenceQualityAssessment(
            score=evidence_score,
            level=evidence_level,
            reason_codes=evidence_reason_codes,
        ),
        operational_risk=OperationalRiskAssessment(
            score=risk_score,
            level=risk_level,
            reason_codes=risk_reason_codes,
        ),
        trajectory_health=TrajectoryHealthAssessment(
            score=trajectory_score,
            level=trajectory_level,
            reason_codes=trajectory_reason_codes,
        ),
        recommended_directive=recommended_directive,
        summary="Dynamic policy assessment.",
        reason_codes=(),
    )


# Build a behavioral assessment with one advisory recommendation.
def _behavioral(
    *,
    directive: BehavioralRecommendedDirective = BehavioralRecommendedDirective.CONTINUE,
    deviation: float | None = None,
    novelty: float | None = None,
) -> BehavioralAssessment:
    return BehavioralAssessment(
        history_available=True,
        historical_run_count=32,
        feature_snapshot_version="behavioral_features_v2",
        summary="Behavioral assessment.",
        notes=(),
        matched_run_count=5,
        historical_similarity_score=0.61,
        behavior_deviation_score=deviation,
        novelty_score=novelty,
        behavioral_recommended_directive=directive,
        behavioral_flags=(),
        matched_run_ids=("run-1",),
    )


# Build a judge assessment with explicit confidence and target.
def _judge(
    *,
    directive: JudgeRecommendedDirective = JudgeRecommendedDirective.CONTINUE,
    confidence: float = 0.85,
    evaluated_target: str = "precision_mismatch",
) -> JudgeAssessment:
    return JudgeAssessment(
        invoked=directive != JudgeRecommendedDirective.CONTINUE,
        recommendation=directive,
        confidence=confidence,
        summary="Judge detected a semantic mismatch.",
        rationale="The output exceeds the safely supported claim precision.",
        flags=("semantic_mismatch",),
        evaluated_target=evaluated_target,
    )


# Build the minimal policy-input snapshot required by the legacy public builder.
def _policy_input() -> PolicyEvaluationInput:
    from layer1_intake import DomainType, SensitivityLevel

    return PolicyEvaluationInput(
        task_domain_type=DomainType.RESEARCH,
        task_domain_label="test",
        task_sensitivity=SensitivityLevel.MEDIUM,
        policy_profile_name="test_profile",
        confidence_threshold=0.65,
        read_only_mode=False,
        budget_max_steps=4,
        budget_max_tool_calls=4,
        runtime_state=RuntimeState.ACTING,
        runtime_flags=(),
        current_plan_step_id="step_1",
        current_plan_step_objective="Collect stronger evidence.",
        actor_result_type=ActorResultType.FINAL_OUTPUT,
        capability_mode=CapabilityAccessMode.NONE,
        external_impact=False,
        blocking_reason=None,
        actor_has_output_text=True,
        tool_name=None,
        tool_has_query=False,
        tool_has_url=False,
        tool_call_count=0,
        observed_evidence_count=1,
        actor_turn_count=1,
        external_communication_phase=ExternalCommunicationPhase.EVIDENCE_COLLECTION,
        external_communication_review_ready=False,
    )


class DecisionCombinerTests(unittest.TestCase):
    def test_static_deny_is_absorbing(self) -> None:
        result = combine_decision_signals(static_policy_assessment=_static_policy(decision=PolicyDecision.DENY))
        self.assertEqual(result.final_directive, GovernanceDirective.STOP)

    def test_static_human_review_cannot_be_downgraded(self) -> None:
        directive = combine_governance_directives(
            static_policy_assessment=_static_policy(
                decision=PolicyDecision.REQUIRE_HUMAN_REVIEW,
                directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
            ),
            dynamic_policy_assessment=_dynamic_policy(),
            behavioral_assessment=_behavioral(),
        )
        self.assertEqual(directive, GovernanceDirective.WAIT_HUMAN_REVIEW)

    def test_static_ask_clarification_cannot_be_downgraded(self) -> None:
        directive = combine_governance_directives(
            static_policy_assessment=_static_policy(
                decision=PolicyDecision.ASK_CLARIFICATION,
                directive=GovernanceDirective.ASK_USER_CLARIFICATION,
            ),
            judge_assessment=_judge(directive=JudgeRecommendedDirective.REQUEST_MORE_EVIDENCE),
        )
        self.assertEqual(directive, GovernanceDirective.ASK_USER_CLARIFICATION)

    def test_read_only_static_restrictions_are_preserved(self) -> None:
        result = combine_decision_signals(
            static_policy_assessment=_static_policy(
                decision=PolicyDecision.ALLOW_READ_ONLY,
                directive=GovernanceDirective.CONTINUE_READ_ONLY,
            )
        )
        self.assertEqual(result.final_directive, GovernanceDirective.CONTINUE_READ_ONLY)

    def test_evidence_gap_prefers_request_more_evidence_before_human_review(self) -> None:
        result = combine_decision_signals(
            static_policy_assessment=_static_policy(),
            dynamic_policy_assessment=_dynamic_policy(
                recommended_directive=DynamicPolicyRecommendedDirective.REQUEST_MORE_EVIDENCE,
                evidence_score=0.32,
                evidence_level=OversightSignalLevel.WEAK,
                evidence_reason_codes=(OversightReasonCode.EVIDENCE_SUPPORT_BELOW_STEP_NEED,),
            ),
        )
        self.assertEqual(result.final_directive, GovernanceDirective.REQUEST_MORE_EVIDENCE)

    def test_repeated_or_severe_trajectory_degradation_prefers_replan(self) -> None:
        result = combine_decision_signals(
            static_policy_assessment=_static_policy(),
            dynamic_policy_assessment=_dynamic_policy(
                recommended_directive=DynamicPolicyRecommendedDirective.REPLAN,
                trajectory_score=0.28,
                trajectory_level=OversightSignalLevel.DEGRADED,
                trajectory_reason_codes=(OversightReasonCode.NEEDS_REPLAN_SIGNAL,),
            ),
            repeated_recovery=True,
        )
        self.assertEqual(result.final_directive, GovernanceDirective.REPLAN)

    def test_high_operational_risk_with_external_impact_prefers_human_review(self) -> None:
        result = combine_decision_signals(
            static_policy_assessment=_static_policy(),
            dynamic_policy_assessment=_dynamic_policy(
                recommended_directive=DynamicPolicyRecommendedDirective.WAIT_HUMAN_REVIEW,
                risk_score=0.84,
                risk_level=OversightSignalLevel.HIGH,
                risk_reason_codes=(OversightReasonCode.TOOL_REQUIRES_HUMAN_REVIEW,),
            ),
            external_impact=True,
        )
        self.assertEqual(result.final_directive, GovernanceDirective.WAIT_HUMAN_REVIEW)

    def test_behavioral_anomaly_alone_does_not_force_human_review(self) -> None:
        result = combine_decision_signals(
            static_policy_assessment=_static_policy(),
            behavioral_assessment=_behavioral(
                directive=BehavioralRecommendedDirective.WAIT_HUMAN_REVIEW,
                deviation=0.86,
            ),
        )
        self.assertEqual(result.final_directive, GovernanceDirective.CONTINUE)

    def test_strong_judge_semantic_mismatch_can_escalate_from_continue(self) -> None:
        result = combine_decision_signals(
            static_policy_assessment=_static_policy(),
            judge_assessment=_judge(
                directive=JudgeRecommendedDirective.REQUEST_MORE_EVIDENCE,
                confidence=0.91,
            ),
        )
        self.assertEqual(result.final_directive, GovernanceDirective.REQUEST_MORE_EVIDENCE)

    def test_non_escalating_layers_do_not_dilute_a_strong_escalating_signal(self) -> None:
        directive = combine_governance_directives(
            static_policy_assessment=_static_policy(),
            dynamic_policy_assessment=_dynamic_policy(
                recommended_directive=DynamicPolicyRecommendedDirective.REQUEST_MORE_EVIDENCE,
                evidence_score=0.25,
                evidence_level=OversightSignalLevel.WEAK,
                evidence_reason_codes=(OversightReasonCode.EVIDENCE_SUPPORT_BELOW_STEP_NEED,),
            ),
            behavioral_assessment=_behavioral(),
            judge_assessment=_judge(),
        )
        self.assertEqual(directive, GovernanceDirective.REQUEST_MORE_EVIDENCE)

    def test_combiner_trace_includes_signals_reason_and_rejected_alternatives(self) -> None:
        decision = build_governance_decision(
            policy_input=_policy_input(),
            policy_result=PolicyEvaluationResult(
                policy_decision=PolicyDecision.ALLOW,
                reason_codes=(),
                summary="Static policy allowed the action.",
                policy_engine_name="test_policy_engine",
                policy_bundle_version="test_bundle_v1",
            ),
            oversight_assessment=_dynamic_policy(
                recommended_directive=DynamicPolicyRecommendedDirective.REQUEST_MORE_EVIDENCE,
                evidence_score=0.30,
                evidence_level=OversightSignalLevel.WEAK,
                evidence_reason_codes=(OversightReasonCode.EVIDENCE_SUPPORT_BELOW_STEP_NEED,),
            ),
            judge_assessment=_judge(
                directive=JudgeRecommendedDirective.REQUEST_MORE_EVIDENCE,
                confidence=0.88,
            ),
        )
        self.assertTrue(decision.combiner_signals)
        self.assertTrue(decision.combiner_selected_reason)
        self.assertIsInstance(decision.combiner_rejected_alternatives, dict)


if __name__ == "__main__":
    unittest.main()
