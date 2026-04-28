from __future__ import annotations

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
    ActorPort,
    ActorTask,
    CapabilityAccessMode,
    GovernanceDirective,
    GovernanceReasonCode,
    PlanStep,
    PlanStepStatus,
    PlannerDecision,
    PolicyEnginePort,
    PolicyDecision,
    PolicyEvaluationResult,
    ReplanDecision,
    ReplannerPort,
    RuntimeState,
    StepValidation,
    StepValidationDecision,
    TaskState,
    ToolExecutionResult,
    ToolName,
    ToolRequest,
    ToolSource,
    build_governance_decision,
    build_policy_evaluation_input,
    build_runtime_graph,
)
from layer2_runtime_governance.oversight import (
    OversightReasonCode,
    OversightSignalLevel,
    evaluate_confidence_risk_trajectory,
    evaluate_evidence_quality,
    evaluate_operational_risk,
    evaluate_trajectory_health,
    get_default_oversight_policy_config_path,
    load_oversight_policy_config,
)
from layer2_runtime_governance.tool_contract import ObservedEvidence
from layer2_runtime_governance.tool_gateway import (
    ToolBackend,
    ToolCapability,
    ToolDefinition,
    ToolGatewayConfig,
    ToolRiskLevel,
)


class Layer2OversightTests(unittest.TestCase):
    def test_oversight_config_loads_scoring_knobs(self) -> None:
        config = load_oversight_policy_config(
            config_path=get_default_oversight_policy_config_path()
        )

        self.assertEqual(config.min_actor_confidence, 0.65)
        self.assertEqual(config.min_evidence_quality, 0.70)
        self.assertEqual(config.evidence_quality_weights.evidence_sufficient, 0.30)
        self.assertEqual(config.risk_scores.tool_risk["low"], 0.15)
        self.assertEqual(config.trajectory_penalties.actor_turn_overage_penalty, 0.15)

    def test_evidence_quality_strong_when_validation_and_sources_are_strong(self) -> None:
        task_state = _build_task_state_with_actor_result(
            actor_result_type=ActorResultType.FINAL_OUTPUT,
            validation=StepValidation(
                plan_step_id="step_1",
                objective_satisfied=True,
                evidence_sufficient=True,
                confidence=0.9,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_summary="The evidence supports the answer.",
            ),
        )
        task_state.observed_evidence.append(_evidence())
        task_state.last_tool_result = _successful_tool_result()

        assessment = evaluate_evidence_quality(
            task_state=task_state,
            config=load_oversight_policy_config(),
        )

        self.assertAlmostEqual(assessment.score, 0.98)
        self.assertEqual(assessment.level, OversightSignalLevel.STRONG)
        self.assertIn(
            OversightReasonCode.EVIDENCE_MARKED_SUFFICIENT,
            assessment.reason_codes,
        )

    def test_evidence_quality_weak_without_evidence_or_positive_validation(self) -> None:
        task_state = _build_task_state_with_actor_result(
            actor_result_type=ActorResultType.FINAL_OUTPUT,
            validation=StepValidation(
                plan_step_id="step_1",
                objective_satisfied=False,
                evidence_sufficient=False,
                confidence=0.3,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_summary="The answer is not sufficiently grounded.",
                residual_uncertainty="Missing support.",
            ),
        )

        assessment = evaluate_evidence_quality(
            task_state=task_state,
            config=load_oversight_policy_config(),
        )

        self.assertLess(assessment.score, 0.35)
        self.assertEqual(assessment.level, OversightSignalLevel.WEAK)
        self.assertIn(OversightReasonCode.OBSERVED_EVIDENCE_MISSING, assessment.reason_codes)

    def test_operational_risk_uses_tool_registry_and_sensitivity(self) -> None:
        task_state = _build_task_state_with_actor_result(
            actor_result_type=ActorResultType.PROPOSE_ACTION,
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
                reason="Use low-risk text stats.",
                text="agent agent governance",
            ),
        )
        policy_input = build_policy_evaluation_input(task_state=task_state)

        assessment = evaluate_operational_risk(
            policy_input=policy_input,
            config=load_oversight_policy_config(),
            tool_gateway_config=_tool_config(
                risk_level=ToolRiskLevel.LOW,
                requires_human_review=False,
            ),
        )

        self.assertAlmostEqual(assessment.score, 0.15)
        self.assertEqual(assessment.level, OversightSignalLevel.LOW)
        self.assertIn(OversightReasonCode.TOOL_RISK_LOW, assessment.reason_codes)

    def test_operational_risk_escalates_for_review_required_tool(self) -> None:
        task_state = _build_task_state_with_actor_result(
            actor_result_type=ActorResultType.PROPOSE_ACTION,
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
                reason="Use tool that requires review.",
                text="agent",
            ),
        )
        task_state.task_spec.sensitivity = SensitivityLevel.HIGH
        policy_input = build_policy_evaluation_input(task_state=task_state)

        assessment = evaluate_operational_risk(
            policy_input=policy_input,
            config=load_oversight_policy_config(),
            tool_gateway_config=_tool_config(
                risk_level=ToolRiskLevel.HIGH,
                requires_human_review=True,
            ),
        )

        self.assertEqual(assessment.level, OversightSignalLevel.HIGH)
        self.assertIn(
            OversightReasonCode.TOOL_REQUIRES_HUMAN_REVIEW,
            assessment.reason_codes,
        )

    def test_trajectory_health_degrades_for_turn_overage_and_replan_signal(self) -> None:
        task_state = _build_task_state_with_actor_result(
            actor_result_type=ActorResultType.NEEDS_REPLAN,
            validation=None,
        )
        task_state.actor_turn_count = 6
        task_state.last_tool_result = _failed_tool_result()
        policy_input = build_policy_evaluation_input(task_state=task_state)

        assessment = evaluate_trajectory_health(
            task_state=task_state,
            policy_input=policy_input,
            config=load_oversight_policy_config(),
        )

        self.assertLess(assessment.score, 0.60)
        self.assertIn(OversightReasonCode.ACTOR_TURNS_EXCEEDED, assessment.reason_codes)
        self.assertIn(OversightReasonCode.NEEDS_REPLAN_SIGNAL, assessment.reason_codes)

    def test_opa_deny_remains_stop_even_when_oversight_is_healthy(self) -> None:
        task_state = _healthy_completion_state()
        policy_input = build_policy_evaluation_input(task_state=task_state)
        oversight = evaluate_confidence_risk_trajectory(
            task_state=task_state,
            policy_input=policy_input,
            tool_gateway_config=_tool_config(),
        )

        decision = build_governance_decision(
            policy_input=policy_input,
            policy_result=_policy_result(PolicyDecision.DENY),
            oversight_assessment=oversight,
        )

        self.assertEqual(decision.directive, GovernanceDirective.STOP)
        self.assertEqual(decision.policy_directive, GovernanceDirective.STOP)

    def test_opa_allow_with_high_risk_waits_for_human_review(self) -> None:
        task_state = _build_task_state_with_actor_result(
            actor_result_type=ActorResultType.PROPOSE_ACTION,
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
                reason="Use high-risk tool.",
                text="agent",
            ),
        )
        policy_input = build_policy_evaluation_input(task_state=task_state)
        oversight = evaluate_confidence_risk_trajectory(
            task_state=task_state,
            policy_input=policy_input,
            tool_gateway_config=_tool_config(
                risk_level=ToolRiskLevel.HIGH,
                requires_human_review=True,
            ),
        )

        decision = build_governance_decision(
            policy_input=policy_input,
            policy_result=_policy_result(PolicyDecision.ALLOW),
            oversight_assessment=oversight,
        )

        self.assertEqual(decision.directive, GovernanceDirective.WAIT_HUMAN_REVIEW)
        self.assertEqual(
            decision.oversight_recommended_directive,
            GovernanceDirective.WAIT_HUMAN_REVIEW,
        )

    def test_opa_allow_with_weak_completion_requests_more_evidence(self) -> None:
        task_state = _build_task_state_with_actor_result(
            actor_result_type=ActorResultType.FINAL_OUTPUT,
            validation=StepValidation(
                plan_step_id="step_1",
                objective_satisfied=False,
                evidence_sufficient=False,
                confidence=0.3,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_summary="Completion is weak.",
                residual_uncertainty="Evidence is missing.",
            ),
        )
        policy_input = build_policy_evaluation_input(task_state=task_state)
        oversight = evaluate_confidence_risk_trajectory(
            task_state=task_state,
            policy_input=policy_input,
            tool_gateway_config=_tool_config(),
        )

        decision = build_governance_decision(
            policy_input=policy_input,
            policy_result=_policy_result(PolicyDecision.ALLOW),
            oversight_assessment=oversight,
        )

        self.assertEqual(decision.directive, GovernanceDirective.REQUEST_MORE_EVIDENCE)
        self.assertEqual(
            decision.oversight_recommended_directive,
            GovernanceDirective.REQUEST_MORE_EVIDENCE,
        )

    def test_opa_allow_with_healthy_signals_preserves_continue(self) -> None:
        task_state = _healthy_completion_state()
        policy_input = build_policy_evaluation_input(task_state=task_state)
        oversight = evaluate_confidence_risk_trajectory(
            task_state=task_state,
            policy_input=policy_input,
            tool_gateway_config=_tool_config(),
        )

        decision = build_governance_decision(
            policy_input=policy_input,
            policy_result=_policy_result(PolicyDecision.ALLOW),
            oversight_assessment=oversight,
        )

        self.assertEqual(decision.directive, GovernanceDirective.CONTINUE)
        self.assertEqual(decision.policy_directive, GovernanceDirective.CONTINUE)

    def test_runtime_weak_completion_returns_to_actor_for_more_evidence(self) -> None:
        graph = build_runtime_graph(
            planner_service=SingleStepPlanner(),
            actor_service=WeakThenStrongCompletionActor(),
            tool_gateway_service=NoopToolGateway(),
            replanner_service=NoopReplanner(),
            policy_engine_service=AllowPolicyEngine(),
        )

        result = graph.invoke(
            {"intake_result": _intake_result()},
            config={"configurable": {"thread_id": "oversight-more-evidence-loop"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.actor_turn_count, 2)
        self.assertEqual(len(task_state.oversight_history), 2)
        self.assertEqual(
            task_state.governance_history[0].directive,
            GovernanceDirective.REQUEST_MORE_EVIDENCE,
        )
        self.assertEqual(task_state.governance_history[1].directive, GovernanceDirective.CONTINUE)


# Build a canonical task state ready for oversight evaluation.
def _build_task_state_with_actor_result(
    *,
    actor_result_type: ActorResultType,
    validation: StepValidation | None = None,
    tool_request: ToolRequest | None = None,
) -> TaskState:
    state = TaskState.from_intake(_intake_result())
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Evaluate the current step.",
            rationale="The test needs one current step.",
            expected_outcome="A governed result.",
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    state.current_plan_step_index = 0
    state.current_state = RuntimeState.ACTING
    state.actor_turn_count = 1
    state.actor_result = ActorResult(
        plan_step_id="step_1",
        result_type=actor_result_type,
        summary="Actor result under oversight test.",
        capability_mode=(
            CapabilityAccessMode.READ_ONLY
            if tool_request is not None
            else CapabilityAccessMode.NONE
        ),
        external_impact=False,
        tool_request=tool_request,
        output_text="Test output.",
        step_validation=validation,
    )
    state.current_tool_request = tool_request
    if validation is not None:
        state.step_validations.append(validation)
    return state


# Build a strong evidence completion state.
def _healthy_completion_state() -> TaskState:
    state = _build_task_state_with_actor_result(
        actor_result_type=ActorResultType.FINAL_OUTPUT,
        validation=StepValidation(
            plan_step_id="step_1",
            objective_satisfied=True,
            evidence_sufficient=True,
            confidence=0.9,
            next_action=StepValidationDecision.COMPLETE_STEP,
            rationale_summary="Evidence is strong.",
        ),
    )
    state.observed_evidence.append(_evidence())
    state.last_tool_result = _successful_tool_result()
    return state


# Build a small evidence item with provenance.
def _evidence() -> ObservedEvidence:
    return ObservedEvidence(
        evidence_id="step_1:tool:1:source:1",
        plan_step_id="step_1",
        title="MCP text stats",
        locator="mcp://local_test/text_stats",
        excerpt="Evidence text.",
        source_tool=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
    )


# Build one successful tool result.
def _successful_tool_result() -> ToolExecutionResult:
    request = ToolRequest(
        plan_step_id="step_1",
        tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
        reason="Collect text stats.",
        text="agent agent governance",
    )
    return ToolExecutionResult(
        request=request,
        success=True,
        summary="Tool succeeded.",
        content="stats",
        sources=(
            ToolSource(
                title="MCP text stats",
                locator="mcp://local_test/text_stats",
                snippet="stats",
            ),
        ),
    )


# Build one failed tool result.
def _failed_tool_result() -> ToolExecutionResult:
    request = ToolRequest(
        plan_step_id="step_1",
        tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
        reason="Collect text stats.",
        text="agent",
    )
    return ToolExecutionResult(
        request=request,
        success=False,
        summary="Tool failed.",
        error_message="Failed.",
    )


# Build one tool gateway config containing the MCP text stats test tool.
def _tool_config(
    *,
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW,
    requires_human_review: bool = False,
) -> ToolGatewayConfig:
    return ToolGatewayConfig(
        tools={
            ToolName.MCP_LOCAL_TEST_TEXT_STATS: ToolDefinition(
                tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
                backend=ToolBackend.MCP,
                capability=ToolCapability.READ,
                risk_level=risk_level,
                enabled=True,
                requires_human_review=requires_human_review,
                timeout_seconds=5.0,
                required_fields=("text",),
                mcp_server_id="local_test",
                mcp_tool_name="text_stats",
            )
        }
    )


# Build one deterministic policy result.
def _policy_result(policy_decision: PolicyDecision) -> PolicyEvaluationResult:
    reason_code = (
        GovernanceReasonCode.DENIED_BY_POLICY
        if policy_decision == PolicyDecision.DENY
        else GovernanceReasonCode.LOCAL_OUTPUT_ALLOWED
    )
    return PolicyEvaluationResult(
        policy_decision=policy_decision,
        reason_codes=(reason_code,),
        summary="Policy test result.",
        policy_engine_name="stub_policy_engine",
        policy_bundle_version="stub_policy_bundle_v1",
    )


# Build one low-risk intake result.
def _intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Evaluate one governed step.",
                context="Oversight test.",
                constraints=[],
                domain_type=DomainType.RESEARCH,
                domain_label="oversight_test",
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Produce a governed output."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                budget=BudgetConfig(max_steps=3, max_tool_calls=3),
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="stub_parser",
            prompt_version="test_v1",
            schema_version="1.0.0",
        ),
    )


class SingleStepPlanner:
    planner_name = "single_step_planner"
    prompt_version = "single_step_v1"

    # Return one semantic step so the runtime test focuses on oversight routing.
    def plan(self, *, task_state) -> PlannerDecision:
        return PlannerDecision(
            plan_steps=[
                PlanStep(
                    step_id="step_1",
                    objective="Complete one step under oversight.",
                    rationale="The test validates adaptive governance routing.",
                    expected_outcome="A completed step after one retry.",
                )
            ],
            rationale="Single-step oversight test.",
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


class WeakThenStrongCompletionActor(ActorPort):
    actor_name = "weak_then_strong_actor"
    prompt_version = "weak_then_strong_v1"

    # First attempts weak completion, then recovers after oversight sends it back.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        is_retry = len(actor_task.step_validations) > 0
        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.FINAL_OUTPUT,
            summary="Complete the step.",
            capability_mode=CapabilityAccessMode.NONE,
            output_text="Completed after adaptive oversight retry.",
            step_validation=StepValidation(
                plan_step_id=actor_task.current_plan_step.step_id,
                objective_satisfied=is_retry,
                evidence_sufficient=is_retry,
                confidence=0.9 if is_retry else 0.3,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_summary=(
                    "The retry is now sufficiently supported."
                    if is_retry
                    else "The first completion attempt is weak."
                ),
                residual_uncertainty=None if is_retry else "Evidence is missing.",
            ),
        )


class AllowPolicyEngine(PolicyEnginePort):
    policy_engine_name = "allow_policy_engine"
    policy_bundle_version = "allow_policy_v1"

    # Always allow so the test isolates adaptive oversight behavior.
    def evaluate(self, *, policy_input) -> PolicyEvaluationResult:
        return _policy_result(PolicyDecision.ALLOW)


class NoopToolGateway:
    gateway_name = "noop_tool_gateway"

    # This test never executes tools.
    def execute(self, *, tool_request):
        raise AssertionError("No tool execution expected.")


class NoopReplanner(ReplannerPort):
    replanner_name = "noop_replanner"
    prompt_version = "noop_v1"

    # This test never replans.
    def replan(self, *, task_state) -> ReplanDecision:
        return ReplanDecision(
            replacement_steps=[],
            rationale="No replanning expected.",
            replanner_name=self.replanner_name,
            prompt_version=self.prompt_version,
        )


if __name__ == "__main__":
    unittest.main()
