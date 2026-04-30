from __future__ import annotations

import json
import sys
import unittest
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
    CapabilityAccessMode,
    EvidenceQualityAssessment,
    GovernanceDecision,
    GovernanceDirective,
    GovernanceReasonCode,
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
    StepRecord,
    StepValidation,
    StepValidationDecision,
    TaskState,
    ToolExecutionResult,
    ToolName,
    ToolRequest,
    ToolSource,
    TraceOversightAssessment,
    TrajectoryHealthAssessment,
    build_compact_trace_from_runtime_trace,
    build_layer2_runtime_trace,
    layer2_trace_to_dict,
    layer2_trace_to_json,
)
from layer2_runtime_governance.tool_contract import ObservedEvidence


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
        self.assertEqual(len(trace.governance_decisions), 2)
        self.assertEqual(trace.governance_decisions[0].directive, "continue_read_only")
        self.assertEqual(len(trace.oversight_assessments), 2)
        self.assertIsInstance(trace.oversight_assessments[0], TraceOversightAssessment)
        self.assertEqual(trace.oversight_assessments[0].evidence_quality.level, "strong")

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
        self.assertEqual(
            compact["governance_snapshot"]["last_oversight_assessment"]["trajectory_health"][
                "level"
            ],
            "healthy",
        )
        self.assertEqual(compact["tool_snapshot"]["observed_evidence_count"], 1)

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
    state.oversight_history = [_oversight(), _oversight()]
    state.last_oversight_assessment = state.oversight_history[-1]
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


if __name__ == "__main__":
    unittest.main()
