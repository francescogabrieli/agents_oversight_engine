from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from layer1_intake import (
    BudgetConfig,
    DomainType,
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskSpec,
)
from layer2_runtime_governance import (
    ActorResult,
    ActorResultType,
    BehavioralAssessment,
    BehavioralRecommendedDirective,
    CapabilityAccessMode,
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    EvidenceSupportLevel,
    EvidenceQualityAssessment,
    GovernanceDecision,
    GovernanceDirective,
    GovernanceReasonCode,
    JudgeAssessment,
    JudgeRecommendedDirective,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    HumanReviewRequest,
    OperationalRiskAssessment,
    OversightAssessment,
    OversightReasonCode,
    OversightRecommendedDirective,
    OversightSignalLevel,
    PlanStep,
    PlanStepStatus,
    PolicyDecision,
    RuntimeState,
    ScratchpadEntry,
    ScratchpadEntryType,
    StaticPolicyAssessment,
    StepRecord,
    StepValidation,
    StepValidationDecision,
    TaskState,
    ToolExecutionResult,
    ToolName,
    ToolRequest,
    ToolSource,
    TraceOversightAssessment,
    TraceJudgeAssessment,
    TrajectoryHealthAssessment,
    build_compact_trace_from_runtime_trace,
    build_layer2_runtime_trace,
    layer2_trace_to_dict,
    layer2_trace_to_json,
)
from layer2_runtime_governance.tool_contract import ObservedEvidence
from layer2_runtime_governance.signal_services.evidence_service import build_evidence_signal


class Layer2TraceTests(unittest.TestCase):
    def test_runtime_trace_includes_core_sections_and_histories(self) -> None:
        state = _complete_task_state()

        trace = build_layer2_runtime_trace(
            thread_id="trace-test",
            intake_result=state.intake_payload,
            task_state=state,
        )

        self.assertEqual(trace.runtime.thread_id, "trace-test")
        self.assertEqual(trace.runtime.current_state, "completed")
        self.assertEqual(trace.intake.goal, "Trace the governed runtime.")
        self.assertEqual(len(trace.plan_steps), 2)
        self.assertEqual(trace.actor.result_type, "final_output")
        self.assertEqual(trace.execution.final_output_text, "Final trace answer.")
        self.assertEqual(len(trace.tool_calls), 2)
        self.assertEqual(trace.tool_calls[0].tool_name, "mcp.local_test.text_stats")
        self.assertFalse(trace.tool_calls[1].success)
        self.assertEqual(len(trace.observed_evidence), 1)
        self.assertEqual(trace.observed_evidence[0].locator, "mcp://local_test/text_stats")
        self.assertEqual(trace.observed_evidence[0].source_kind, "tool_observation")
        self.assertEqual(len(trace.governance_decisions), 2)
        self.assertEqual(trace.governance_decisions[0].directive, "continue_read_only")
        self.assertEqual(len(trace.oversight_assessments), 2)
        self.assertIsInstance(trace.oversight_assessments[0], TraceOversightAssessment)
        self.assertEqual(trace.oversight_assessments[0].evidence_quality.level, "strong")
        self.assertEqual(len(trace.static_policy_assessments), 2)
        self.assertEqual(len(trace.dynamic_policy_assessments), 2)
        self.assertEqual(len(trace.behavioral_assessments), 1)
        self.assertTrue(trace.behavioral_assessments[0].history_available)
        self.assertEqual(len(trace.judge_assessments), 1)
        self.assertIsInstance(trace.judge_assessments[0], TraceJudgeAssessment)
        self.assertEqual(trace.judge_assessments[0].recommendation, "wait_human_review")

    def test_runtime_trace_handles_legacy_string_step_kind_values(self) -> None:
        state = _complete_task_state()
        state.plan_steps[0].step_kind = "source_discovery"
        state.plan_steps[0].evidence_requirement = "source_discovery"
        state.plan_steps[0].claim_precision_ceiling = "high_level_summary"

        trace = build_layer2_runtime_trace(
            thread_id="legacy-step-kind",
            intake_result=state.intake_payload,
            task_state=state,
        )

        self.assertEqual(trace.plan_steps[0].step_kind, "source_discovery")
        self.assertEqual(trace.plan_steps[0].evidence_requirement, "source_discovery")
        self.assertEqual(trace.plan_steps[0].claim_precision_ceiling, "high_level_summary")

    # Actor observability and governance facts must call the same mismatch resolver.
    def test_actor_trace_and_governance_signal_share_precision_mismatch_resolution(self) -> None:
        state = _complete_task_state()
        state.plan_steps[1].evidence_requirement = EvidenceSupportLevel.HIGH_LEVEL_SUMMARY
        state.plan_steps[1].claim_precision_ceiling = EvidenceSupportLevel.HIGH_LEVEL_SUMMARY
        state.observed_evidence = [
            ObservedEvidence(
                evidence_id="step_1:tool:1:source:1",
                plan_step_id="step_1",
                title="Discovered source",
                locator="https://example.org/source",
                excerpt="Discovered source metadata. Content has not been extracted.",
                source_tool=ToolName.WEB_SEARCH,
                support_level=EvidenceSupportLevel.SOURCE_DISCOVERY,
            )
        ]
        state.actor_result = replace(
            state.actor_result,
            output_text="Regulation (EU) 2022/1925 was adopted on 14 September 2022.",
        )

        trace = build_layer2_runtime_trace(
            thread_id="precision-mismatch-consistency",
            intake_result=state.intake_payload,
            task_state=state,
        )
        evidence_signal = build_evidence_signal(
            task_state=state,
            thresholds_version="test_thresholds",
        )

        self.assertTrue(trace.actor.precision_mismatch_detected)
        self.assertTrue(evidence_signal["raw_facts"]["precision_mismatch"])

    def test_trace_dict_and_json_are_json_safe(self) -> None:
        state = _complete_task_state()
        trace = build_layer2_runtime_trace(
            thread_id="json-safe",
            intake_result=state.intake_payload,
            task_state=state,
        )

        payload = layer2_trace_to_dict(trace)
        encoded = layer2_trace_to_json(trace)
        decoded = json.loads(encoded)

        self.assertEqual(payload["runtime"]["current_state"], "completed")
        self.assertEqual(decoded["governance_decisions"][0]["policy_decision"], "allow_read_only")
        self.assertEqual(
            decoded["oversight_assessments"][0]["operational_risk"]["reason_codes"],
            ["tool_risk_low"],
        )

    def test_compact_trace_is_derived_from_runtime_trace(self) -> None:
        state = _complete_task_state()
        trace = build_layer2_runtime_trace(
            thread_id="compact",
            intake_result=state.intake_payload,
            task_state=state,
        )

        compact = build_compact_trace_from_runtime_trace(trace)

        self.assertEqual(compact["planning_snapshot"]["plan_step_count"], 2)
        self.assertEqual(compact["governance_snapshot"]["governance_pass_count"], 2)
        self.assertEqual(compact["governance_snapshot"]["oversight_pass_count"], 2)
        self.assertEqual(compact["governance_snapshot"]["static_policy_pass_count"], 2)
        self.assertEqual(compact["governance_snapshot"]["dynamic_policy_pass_count"], 2)
        self.assertEqual(compact["governance_snapshot"]["behavioral_pass_count"], 1)
        self.assertEqual(compact["governance_snapshot"]["judge_pass_count"], 1)
        self.assertEqual(
            compact["governance_snapshot"]["last_oversight_assessment"]["trajectory_health"][
                "level"
            ],
            "healthy",
        )
        self.assertEqual(compact["tool_snapshot"]["observed_evidence_count"], 1)
        self.assertIsNone(compact["tool_snapshot"]["current_tool_request"])

    def test_waiting_review_and_aborted_states_are_explicit(self) -> None:
        review_state = _complete_task_state()
        review_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        aborted_state = _complete_task_state()
        aborted_state.current_state = RuntimeState.ABORTED

        review_trace = build_layer2_runtime_trace(
            thread_id="review",
            intake_result=review_state.intake_payload,
            task_state=review_state,
        )
        aborted_trace = build_layer2_runtime_trace(
            thread_id="aborted",
            intake_result=aborted_state.intake_payload,
            task_state=aborted_state,
        )

        self.assertEqual(review_trace.runtime.current_state, "waiting_human_review")
        self.assertEqual(aborted_trace.runtime.current_state, "aborted")

    def test_trace_includes_external_communication_edit_history(self) -> None:
        state = _external_communication_task_state()

        trace = build_layer2_runtime_trace(
            thread_id="external-trace",
            intake_result=state.intake_payload,
            task_state=state,
        )

        self.assertIsNotNone(trace.human_review)
        self.assertIsNotNone(trace.external_communication)
        self.assertEqual(trace.external_communication.phase, "approved_for_execution")
        self.assertEqual(trace.external_communication.draft.channel, "email")
        self.assertEqual(
            trace.external_communication.last_applied_edit.note,
            "Updated the target before submission.",
        )
        self.assertEqual(len(trace.external_communication.edit_history), 1)
        self.assertEqual(
            trace.human_review.pending_request.external_communication_target_type,
            "email",
        )
        self.assertEqual(
            trace.human_review.pending_request.external_communication_draft.channel,
            "email",
        )
        self.assertEqual(
            trace.human_review.decision_history[0].external_communication_edit.draft.channel,
            "slack",
        )


# Build a canonical IntakeResult for trace tests.
def _intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Trace the governed runtime.",
                context="Use deterministic fixtures.",
                constraints=["Stay read-only."],
                domain_type=DomainType.ANALYSIS,
                domain_label="trace_test",
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Produce an audit-safe trace."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                budget=BudgetConfig(max_steps=4, max_tool_calls=3),
                policy_profile_name="trace_profile",
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="trace_test_intake",
            prompt_version="trace_test_v1",
            schema_version="1.0.0",
            warnings=[],
            missing_information=[],
        ),
    )


# Build an IntakeResult for the external communication trace coverage.
def _external_communication_intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Prepare a governed external communication draft.",
                context="Use deterministic fixtures.",
                constraints=["Keep the draft local."],
                domain_type=DomainType.EXTERNAL_COMMUNICATION,
                domain_label="trace_external_comm",
                sensitivity=SensitivityLevel.HIGH,
                success_criteria=["Expose the typed external communication trace."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.7,
                budget=BudgetConfig(max_steps=4, max_tool_calls=3),
                policy_profile_name="trace_external_profile",
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="trace_external_intake",
            prompt_version="trace_external_v1",
            schema_version="1.0.0",
            warnings=[],
            missing_information=[],
        ),
    )


# Build a complete TaskState covering all major trace sections.
def _complete_task_state() -> TaskState:
    state = TaskState.from_intake(_intake_result())
    state.current_state = RuntimeState.COMPLETED
    state.bootstrap_completed = True
    state.step_history = [
        StepRecord(1, RuntimeState.PLANNING, "bootstrap_from_intake", "Bootstrapped."),
        StepRecord(2, RuntimeState.ACTING, "actor", "Actor executed."),
    ]
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Collect deterministic text stats.",
            rationale="The task needs evidence.",
            expected_outcome="Stats collected.",
            status=PlanStepStatus.COMPLETED,
        ),
        PlanStep(
            step_id="step_2",
            objective="Write final answer.",
            rationale="The task needs a final output.",
            expected_outcome="Answer written.",
            status=PlanStepStatus.COMPLETED,
        ),
    ]
    state.current_plan_step_index = None
    state.planner_name = "stub_planner"
    state.planner_prompt_version = "planner_v1"
    state.planner_rationale = "Two-step trace plan."
    state.actor_name = "stub_actor"
    state.actor_prompt_version = "actor_v1"
    state.actor_turn_count = 2
    state.tool_call_count = 1
    state.final_output_text = "Final trace answer."
    state.actor_result = ActorResult(
        plan_step_id="step_2",
        result_type=ActorResultType.FINAL_OUTPUT,
        summary="Final output produced.",
        capability_mode=CapabilityAccessMode.NONE,
        external_impact=False,
        output_text="Final trace answer.",
        step_validation=_validation("step_2"),
    )
    state.executed_steps = [
        _executed_step("step_1", ActorResultType.INTERMEDIATE_OUTPUT),
        _executed_step("step_2", ActorResultType.FINAL_OUTPUT),
    ]
    state.scratchpad = [
        ScratchpadEntry(
            turn_index=1,
            plan_step_id="step_1",
            entry_type=ScratchpadEntryType.OBSERVATION,
            summary="Observed deterministic stats.",
            evidence_refs=["step_1:tool:1:source:1"],
        )
    ]
    state.step_validations = [_validation("step_1"), _validation("step_2")]
    state.last_tool_result = _tool_result()
    state.tool_results_history = [_tool_result(), _second_tool_result()]
    state.observed_evidence = [_evidence()]
    state.governance_history = [_governance_read_only(), _governance_continue()]
    state.last_governance_decision = state.governance_history[-1]
    state.static_policy_history = [_static_policy_read_only(), _static_policy_allow()]
    state.last_static_policy_assessment = state.static_policy_history[-1]
    state.dynamic_policy_history = [_oversight(), _oversight()]
    state.last_dynamic_policy_assessment = state.dynamic_policy_history[-1]
    state.oversight_history = [_oversight(), _oversight()]
    state.last_oversight_assessment = state.oversight_history[-1]
    state.behavioral_history = [_behavioral()]
    state.last_behavioral_assessment = state.behavioral_history[-1]
    state.judge_history = [_judge()]
    state.last_judge_assessment = state.judge_history[-1]
    return state


# Build a complete TaskState that exercises the typed external communication review history.
def _external_communication_task_state() -> TaskState:
    state = TaskState.from_intake(_external_communication_intake_result())
    state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
    state.bootstrap_completed = True
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Draft the external communication.",
            rationale="The draft must remain local until review is complete.",
            expected_outcome="A reviewable external draft.",
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    state.current_plan_step_index = 0
    state.external_communication.phase = ExternalCommunicationPhase.APPROVED_FOR_EXECUTION
    state.external_communication.draft = ExternalCommunicationDraft(
        channel="email",
        recipient="ops@example.com",
        subject="Draft update",
        body="Please review this draft before sending.",
        metadata={"priority": "high"},
    )
    applied_edit = HumanReviewExternalEdit(
        draft=ExternalCommunicationDraft(
            channel="slack",
            recipient="#ops",
            subject="Updated subject",
            body="Updated body text.",
            metadata={"priority": "critical"},
        ),
        note="Updated the target before submission.",
    )
    state.external_communication.last_applied_edit = applied_edit
    state.external_communication.edit_history = [applied_edit]
    pending_request = HumanReviewRequest(
        request_id="external-trace:human_review",
        thread_id="external-trace",
        current_state=RuntimeState.WAITING_HUMAN_REVIEW,
        resume_state=RuntimeState.ACTING,
        current_plan_step_id="step_1",
        current_plan_step_objective="Draft the external communication.",
        actor_result_type=ActorResultType.INTERMEDIATE_OUTPUT,
        current_tool_request=None,
        governance_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        governance_summary="External communication requires review.",
        oversight_summary="Review is still required.",
        external_communication_target_type="email",
        external_communication_draft=state.external_communication.draft,
        reason_codes=("human_review_required",),
        allowed_decisions=(
            HumanReviewDecisionType.APPROVE_CONTINUE,
            HumanReviewDecisionType.MODIFY_AND_CONTINUE,
        ),
        review_summary="The external draft may be edited and resubmitted.",
    )
    state.pending_human_review_request = pending_request
    state.last_human_review_request = pending_request
    decision = HumanReviewDecision(
        request_id=pending_request.request_id,
        decision_type=HumanReviewDecisionType.MODIFY_AND_CONTINUE,
        external_communication_edit=applied_edit,
        reviewer_id="operator-1",
    )
    state.human_review_history = [decision]
    state.last_human_review_decision = decision
    return state


# Build one executed step for the trace execution section.
def _executed_step(step_id: str, result_type: ActorResultType) -> Any:
    from layer2_runtime_governance import ExecutedPlanStep

    return ExecutedPlanStep(
        step_id=step_id,
        objective=f"Objective for {step_id}.",
        result_type=result_type,
        summary=f"Summary for {step_id}.",
        output_text=f"Output for {step_id}.",
    )


# Build one positive step validation.
def _validation(step_id: str) -> StepValidation:
    return StepValidation(
        plan_step_id=step_id,
        objective_satisfied=True,
        evidence_sufficient=True,
        confidence=0.9,
        next_action=StepValidationDecision.COMPLETE_STEP,
        rationale_summary="Validation passed.",
    )


# Build one retained tool result.
def _tool_result() -> ToolExecutionResult:
    request = ToolRequest(
        plan_step_id="step_1",
        tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
        reason="Collect text stats.",
        text="agent agent evidence",
        limit=5,
    )
    return ToolExecutionResult(
        request=request,
        success=True,
        summary="Stats collected.",
        content='{"words": 3}',
        sources=(
            ToolSource(
                title="MCP stats",
                locator="mcp://local_test/text_stats",
                snippet='{"words": 3}',
            ),
        ),
    )


# Build a second retained tool result so trace exports full tool history.
def _second_tool_result() -> ToolExecutionResult:
    request = ToolRequest(
        plan_step_id="step_2",
        tool_name=ToolName.FETCH_URL_TEXT,
        reason="Fetch source text.",
        url="https://example.org/source",
    )
    return ToolExecutionResult(
        request=request,
        success=False,
        summary="Duplicate tool request blocked before execution.",
        error_message="The same tool request was already executed for this plan step.",
    )


# Build one observed evidence item.
def _evidence() -> ObservedEvidence:
    return ObservedEvidence(
        evidence_id="step_1:tool:1:source:1",
        plan_step_id="step_1",
        title="MCP stats",
        locator="mcp://local_test/text_stats",
        excerpt='{"words": 3}',
        source_tool=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
    )


# Build the read-only governance decision for tool use.
def _governance_read_only() -> GovernanceDecision:
    return GovernanceDecision(
        directive=GovernanceDirective.CONTINUE_READ_ONLY,
        policy_decision=PolicyDecision.ALLOW_READ_ONLY,
        reason_codes=(GovernanceReasonCode.READ_ONLY_ALLOWED,),
        summary="Read-only tool use allowed.",
        constrains_capability_use=True,
        evaluated_actor_result_type=ActorResultType.PROPOSE_ACTION,
        evaluated_tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
        policy_engine_name="test_policy",
        policy_bundle_version="test_v1",
        policy_directive=GovernanceDirective.CONTINUE_READ_ONLY,
        oversight_recommended_directive=GovernanceDirective.CONTINUE,
        oversight_summary="Oversight healthy.",
    )


# Build the local-output governance decision.
def _governance_continue() -> GovernanceDecision:
    return GovernanceDecision(
        directive=GovernanceDirective.CONTINUE,
        policy_decision=PolicyDecision.ALLOW,
        reason_codes=(GovernanceReasonCode.LOCAL_OUTPUT_ALLOWED,),
        summary="Local output allowed.",
        constrains_capability_use=False,
        evaluated_actor_result_type=ActorResultType.FINAL_OUTPUT,
        evaluated_tool_name=None,
        policy_engine_name="test_policy",
        policy_bundle_version="test_v1",
        policy_directive=GovernanceDirective.CONTINUE,
        oversight_recommended_directive=GovernanceDirective.CONTINUE,
        oversight_summary="Oversight healthy.",
    )


# Build one strong oversight assessment.
def _oversight() -> OversightAssessment:
    return OversightAssessment(
        evidence_quality=EvidenceQualityAssessment(
            score=0.98,
            level=OversightSignalLevel.STRONG,
            reason_codes=(OversightReasonCode.EVIDENCE_MARKED_SUFFICIENT,),
        ),
        operational_risk=OperationalRiskAssessment(
            score=0.15,
            level=OversightSignalLevel.LOW,
            reason_codes=(OversightReasonCode.TOOL_RISK_LOW,),
        ),
        trajectory_health=TrajectoryHealthAssessment(
            score=1.0,
            level=OversightSignalLevel.HEALTHY,
            reason_codes=(OversightReasonCode.NO_REPLAN_NEEDED,),
        ),
        recommended_directive=OversightRecommendedDirective.CONTINUE,
        summary="Evidence strong, risk low, trajectory healthy.",
        reason_codes=(OversightReasonCode.RECOMMEND_CONTINUE,),
    )


# Build one static policy assessment.
def _static_policy_read_only() -> StaticPolicyAssessment:
    return StaticPolicyAssessment(
        policy_decision=PolicyDecision.ALLOW_READ_ONLY,
        reason_codes=(GovernanceReasonCode.READ_ONLY_ALLOWED,),
        summary="Read-only tool use allowed.",
        policy_engine_name="test_policy",
        policy_bundle_version="test_v1",
        policy_directive=GovernanceDirective.CONTINUE_READ_ONLY,
    )


# Build one local-output static policy assessment.
def _static_policy_allow() -> StaticPolicyAssessment:
    return StaticPolicyAssessment(
        policy_decision=PolicyDecision.ALLOW,
        reason_codes=(GovernanceReasonCode.LOCAL_OUTPUT_ALLOWED,),
        summary="Local output allowed.",
        policy_engine_name="test_policy",
        policy_bundle_version="test_v1",
        policy_directive=GovernanceDirective.CONTINUE,
    )


# Build one behavioural foundation assessment.
def _behavioral() -> BehavioralAssessment:
    return BehavioralAssessment(
        history_available=True,
        historical_run_count=3,
        feature_snapshot_version="behavioral_features_v2",
        summary="Behavioral analysis matched 3 comparable runs.",
        notes=("sqlite_history_available",),
        matched_run_count=3,
        historical_similarity_score=0.82,
        behavior_deviation_score=0.21,
        novelty_score=0.18,
        behavioral_recommended_directive=BehavioralRecommendedDirective.CONTINUE,
        behavioral_flags=("stable_history",),
        matched_run_ids=("run-1", "run-2", "run-3"),
    )


# Build one selective judge assessment for trace export.
def _judge() -> JudgeAssessment:
    return JudgeAssessment(
        invoked=True,
        recommendation=JudgeRecommendedDirective.WAIT_HUMAN_REVIEW,
        confidence=0.87,
        summary="Judge recommends human review.",
        rationale="The output is semantically risky.",
        flags=("semantic_risk_high",),
        evaluated_target="external_draft",
    )


if __name__ == "__main__":
    unittest.main()
