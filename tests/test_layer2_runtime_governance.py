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
    ActorPort,
    ActorResult,
    ActorResultType,
    ActorTask,
    CapabilityAccessMode,
    PlanStep,
    PlanStepStatus,
    PlannerDecision,
    RuntimeFlag,
    RuntimeState,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepValidation,
    StepValidationDecision,
    TaskState,
    ToolExecutionResult,
    ToolGatewayPort,
    ToolName,
    ToolRequest,
    ToolSource,
    build_actor_task,
    build_runtime_graph,
)
from main_layer2 import build_compact_trace
from layer2_runtime_governance.replanning import (
    OpenAIReplanner,
    OpenAIReplannerSettings,
    ReplanDecision,
    ReplannerPort,
    build_replanner_system_prompt,
    build_replanner_user_prompt,
)


class StubPlanner:
    planner_name = "stub_planner"
    prompt_version = "stub_v1"

    # Return a stable semantic mini-plan so tests never depend on network calls.
    def plan(self, *, task_state):
        return PlannerDecision(
            plan_steps=[
                PlanStep(
                    step_id="step_1",
                    objective="Collect the minimum official evidence needed for the task.",
                    rationale="Research tasks should begin from documentary grounding.",
                    expected_outcome="A compact evidence base relevant to the goal.",
                ),
                PlanStep(
                    step_id="step_2",
                    objective="Synthesize the grounded result.",
                    rationale="The runtime should synthesize only after evidence collection.",
                    expected_outcome="A concise grounded output.",
                ),
            ],
            rationale="The stub planner selected a short evidence-first trajectory.",
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


class StubActor(ActorPort):
    actor_name = "stub_actor"
    prompt_version = "stub_actor_v1"

    # Drive one deterministic actor/tool/observe loop across two semantic steps.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        if actor_task.current_plan_step.step_id == "step_1" and not actor_task.observed_evidence:
            return ActorResult(
                plan_step_id=actor_task.current_plan_step.step_id,
                result_type=ActorResultType.PROPOSE_ACTION,
                summary="Request a read-only evidence search for the first step.",
                capability_mode=CapabilityAccessMode.READ_ONLY,
                external_impact=False,
                tool_request=ToolRequest(
                    plan_step_id=actor_task.current_plan_step.step_id,
                    tool_name=ToolName.WEB_SEARCH,
                    reason="The first semantic step requires authoritative documentary evidence.",
                    query="Digital Markets Act official EU sources",
                    max_results=3,
                    extractive_goal="Find official EU sources about the Digital Markets Act.",
                ),
                scratchpad_entry=ScratchpadEntry(
                    turn_index=actor_task.actor_turn_count,
                    plan_step_id=actor_task.current_plan_step.step_id,
                    entry_type=ScratchpadEntryType.TOOL_DECISION,
                    summary="The current step needs external documentary evidence.",
                ),
            )

        if actor_task.current_plan_step.step_id == "step_1":
            return ActorResult(
                plan_step_id=actor_task.current_plan_step.step_id,
                result_type=ActorResultType.INTERMEDIATE_OUTPUT,
                summary="The first step is complete and produced grounded evidence.",
                capability_mode=CapabilityAccessMode.NONE,
                output_text="Collected grounded evidence from official sources.",
                scratchpad_entry=ScratchpadEntry(
                    turn_index=actor_task.actor_turn_count,
                    plan_step_id=actor_task.current_plan_step.step_id,
                    entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                    summary="The observed source satisfies the evidence collection step.",
                    evidence_refs=[item.evidence_id for item in actor_task.observed_evidence],
                ),
                step_validation=StepValidation(
                    plan_step_id=actor_task.current_plan_step.step_id,
                    objective_satisfied=True,
                    evidence_sufficient=True,
                    confidence=0.86,
                    next_action=StepValidationDecision.COMPLETE_STEP,
                    rationale_summary="Official evidence was collected for the step objective.",
                ),
            )

        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.FINAL_OUTPUT,
            summary="The task can now be completed from the grounded evidence.",
            capability_mode=CapabilityAccessMode.NONE,
            external_impact=False,
            output_text="The DMA is an EU regulation that targets gatekeeper platforms.",
            scratchpad_entry=ScratchpadEntry(
                turn_index=actor_task.actor_turn_count,
                plan_step_id=actor_task.current_plan_step.step_id,
                entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                summary="The final synthesis satisfies the remaining plan step.",
                evidence_refs=[item.evidence_id for item in actor_task.observed_evidence],
            ),
            step_validation=StepValidation(
                plan_step_id=actor_task.current_plan_step.step_id,
                objective_satisfied=True,
                evidence_sufficient=True,
                confidence=0.84,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_summary="The synthesis uses the collected official evidence.",
            ),
        )


class StubToolGateway(ToolGatewayPort):
    gateway_name = "stub_tool_gateway"

    # Return one stable tool result so the actor can continue its local loop.
    def execute(self, *, tool_request: ToolRequest) -> ToolExecutionResult:
        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary="Stub web search completed successfully.",
            content="Official EU sources describe the Digital Markets Act as a regulation for gatekeeper platforms.",
            sources=(
                ToolSource(
                    title="European Commission - Digital Markets Act",
                    locator="https://commission.europa.eu/dma",
                    snippet="The DMA sets rules for gatekeeper platforms.",
                ),
            ),
        )


class ProgressThenCompleteActor(ActorPort):
    actor_name = "progress_then_complete_actor"
    prompt_version = "progress_then_complete_v1"

    # Emit one progress note before validating the step as complete.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        if actor_task.current_plan_step.step_id == "step_1" and not actor_task.step_validations:
            return ActorResult(
                plan_step_id=actor_task.current_plan_step.step_id,
                result_type=ActorResultType.INTERMEDIATE_OUTPUT,
                summary="Progress was recorded but the step is not complete yet.",
                capability_mode=CapabilityAccessMode.NONE,
                output_text="Partial local synthesis.",
                scratchpad_entry=ScratchpadEntry(
                    turn_index=actor_task.actor_turn_count,
                    plan_step_id=actor_task.current_plan_step.step_id,
                    entry_type=ScratchpadEntryType.PROGRESS_NOTE,
                    summary="The actor needs one more local pass before completion.",
                ),
                step_validation=StepValidation(
                    plan_step_id=actor_task.current_plan_step.step_id,
                    objective_satisfied=False,
                    evidence_sufficient=False,
                    confidence=0.45,
                    next_action=StepValidationDecision.CONTINUE_ACTING,
                    rationale_summary="The current partial output is not enough to complete the step.",
                    residual_uncertainty="The step still needs a completion pass.",
                ),
            )

        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.FINAL_OUTPUT,
            summary="The single-step task is now complete.",
            capability_mode=CapabilityAccessMode.NONE,
            output_text="Completed after a second local actor pass.",
            scratchpad_entry=ScratchpadEntry(
                turn_index=actor_task.actor_turn_count,
                plan_step_id=actor_task.current_plan_step.step_id,
                entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                summary="The second pass satisfies the single-step plan.",
            ),
            step_validation=StepValidation(
                plan_step_id=actor_task.current_plan_step.step_id,
                objective_satisfied=True,
                evidence_sufficient=True,
                confidence=0.82,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_summary="The step objective is satisfied after the second pass.",
            ),
        )


class ReplanRequestActor(ActorPort):
    actor_name = "replan_request_actor"
    prompt_version = "replan_request_v1"

    # Request replanning once, then complete the replacement step produced by the replanner.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        if actor_task.current_plan_step.step_id == "step_1":
            return ActorResult(
                plan_step_id=actor_task.current_plan_step.step_id,
                result_type=ActorResultType.NEEDS_REPLAN,
                summary="The current step is not executable as planned.",
                capability_mode=CapabilityAccessMode.NONE,
                blocking_reason="The initial step is too broad and needs a narrower recovery step.",
                scratchpad_entry=ScratchpadEntry(
                    turn_index=actor_task.actor_turn_count,
                    plan_step_id=actor_task.current_plan_step.step_id,
                    entry_type=ScratchpadEntryType.UNCERTAINTY,
                    summary="The actor cannot execute the broad initial step reliably.",
                ),
            )

        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.FINAL_OUTPUT,
            summary="The replanned task is now complete.",
            capability_mode=CapabilityAccessMode.NONE,
            output_text="Completed from the replanned recovery step.",
            scratchpad_entry=ScratchpadEntry(
                turn_index=actor_task.actor_turn_count,
                plan_step_id=actor_task.current_plan_step.step_id,
                entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                summary="The replacement step is narrow enough to complete directly.",
            ),
            step_validation=StepValidation(
                plan_step_id=actor_task.current_plan_step.step_id,
                objective_satisfied=True,
                evidence_sufficient=True,
                confidence=0.8,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_summary="The replanned step satisfies the task within the test scope.",
            ),
        )


class StubReplanner(ReplannerPort):
    replanner_name = "stub_replanner"
    prompt_version = "stub_replanner_v1"

    # Replace the blocked current/pending trajectory with a narrow executable step.
    def replan(self, *, task_state) -> ReplanDecision:
        return ReplanDecision(
            replacement_steps=[
                PlanStep(
                    step_id="step_1_replanned",
                    objective="Complete a narrower recovery step after the actor requested replanning.",
                    rationale="The actor reported that the previous step was too broad.",
                    expected_outcome="A completed recovery output.",
                )
            ],
            rationale="The stub replanner replaced the blocked trajectory with one recovery step.",
            replanner_name=self.replanner_name,
            prompt_version=self.prompt_version,
        )


class FakeParsedReplanResponse:
    # Minimal fake response object matching the OpenAI SDK parsed response shape.
    def __init__(self, output_parsed) -> None:
        self.output_parsed = output_parsed


class FakeReplannerResponses:
    # Capture parse inputs while returning a deterministic structured payload.
    def __init__(self, output_parsed) -> None:
        self.output_parsed = output_parsed
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeParsedReplanResponse(self.output_parsed)


class FakeReplannerClient:
    # Expose the responses.parse surface used by OpenAIReplanner.
    def __init__(self, output_parsed) -> None:
        self.responses = FakeReplannerResponses(output_parsed)


class Layer2RuntimeGraphTests(unittest.TestCase):
    # These tests verify the bootstrap-plus-planner-plus-actor/tool behavior.
    def test_complete_intake_reaches_completed_state(self) -> None:
        graph = build_runtime_graph(
            planner_service=StubPlanner(),
            actor_service=StubActor(),
            tool_gateway_service=StubToolGateway(),
            replanner_service=StubReplanner(),
        )
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-ready-to-execute-complete"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertTrue(task_state.bootstrap_completed)
        self.assertEqual(len(task_state.step_history), 8)
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "tool_gateway",
                "observe_tool_result",
                "actor",
                "actor",
                "finalize_bootstrap",
            ],
        )
        self.assertEqual(task_state.runtime_flags, [])
        self.assertEqual(len(task_state.plan_steps), 2)
        self.assertIsNone(task_state.current_plan_step_index)
        self.assertIsNone(task_state.current_plan_step)
        self.assertEqual(task_state.plan_steps[0].status, PlanStepStatus.COMPLETED)
        self.assertEqual(task_state.plan_steps[1].status, PlanStepStatus.COMPLETED)
        self.assertEqual(task_state.planner_name, "stub_planner")
        self.assertEqual(task_state.planner_prompt_version, "stub_v1")
        self.assertEqual(task_state.actor_name, "stub_actor")
        self.assertEqual(task_state.actor_prompt_version, "stub_actor_v1")
        self.assertIsNotNone(task_state.actor_result)
        self.assertEqual(task_state.actor_result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertEqual(task_state.actor_result.capability_mode, CapabilityAccessMode.NONE)
        self.assertEqual(len(task_state.executed_steps), 2)
        self.assertEqual(task_state.tool_call_count, 1)
        self.assertEqual(len(task_state.observed_evidence), 1)
        self.assertEqual(len(task_state.scratchpad), 4)
        self.assertEqual(len(task_state.step_validations), 2)
        self.assertTrue(all(item.confidence > 0 for item in task_state.step_validations))
        self.assertEqual(
            task_state.final_output_text,
            "The DMA is an EU regulation that targets gatekeeper platforms.",
        )

    def test_needs_clarification_skips_actor(self) -> None:
        graph = build_runtime_graph(
            planner_service=StubPlanner(),
            actor_service=StubActor(),
            tool_gateway_service=StubToolGateway(),
            replanner_service=StubReplanner(),
        )
        result = graph.invoke(
            {"intake_result": _build_clarification_intake_result()},
            config={"configurable": {"thread_id": "layer2-ready-to-execute-clarification"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertTrue(task_state.bootstrap_completed)
        self.assertIn(RuntimeFlag.CLARIFICATION_REQUIRED, task_state.runtime_flags)
        self.assertIn(RuntimeFlag.HIGH_SENSITIVITY, task_state.runtime_flags)
        self.assertEqual(task_state.step_history[0].runtime_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "finalize_bootstrap",
            ],
        )
        self.assertEqual(task_state.plan_steps, [])
        self.assertIsNone(task_state.current_plan_step_index)
        self.assertIsNone(task_state.planner_rationale)
        self.assertIsNone(task_state.actor_result)

    def test_build_actor_task_exposes_full_plan_and_current_step(self) -> None:
        graph = build_runtime_graph(
            planner_service=StubPlanner(),
            actor_service=StubActor(),
            tool_gateway_service=StubToolGateway(),
            replanner_service=StubReplanner(),
        )
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-actor-contract-complete"}},
        )

        task_state = result["task_state"]
        self.assertEqual(len(task_state.executed_steps), 2)
        self.assertEqual(len(task_state.observed_evidence), 1)

        with self.assertRaises(ValueError):
            build_actor_task(task_state=task_state)

    def test_build_actor_task_requires_current_plan_step(self) -> None:
        graph = build_runtime_graph(
            planner_service=StubPlanner(),
            actor_service=StubActor(),
            tool_gateway_service=StubToolGateway(),
            replanner_service=StubReplanner(),
        )
        result = graph.invoke(
            {"intake_result": _build_clarification_intake_result()},
            config={"configurable": {"thread_id": "layer2-actor-contract-clarification"}},
        )

        task_state = result["task_state"]

        with self.assertRaises(ValueError):
            build_actor_task(task_state=task_state)

    def test_complete_intake_supports_unbounded_tool_budget(self) -> None:
        graph = build_runtime_graph(
            planner_service=StubPlanner(),
            actor_service=StubActor(),
            tool_gateway_service=StubToolGateway(),
            replanner_service=StubReplanner(),
        )
        result = graph.invoke(
            {"intake_result": _build_research_intake_result(max_tool_calls=None)},
            config={"configurable": {"thread_id": "layer2-unbounded-tool-budget"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.tool_call_count, 1)
        self.assertEqual(len(task_state.observed_evidence), 1)

    def test_intermediate_output_does_not_complete_step_without_validation(self) -> None:
        graph = build_runtime_graph(
            planner_service=StubPlanner(),
            actor_service=ProgressThenCompleteActor(),
            tool_gateway_service=StubToolGateway(),
            replanner_service=StubReplanner(),
        )
        result = graph.invoke(
            {"intake_result": _build_research_intake_result(max_steps=1, max_tool_calls=10)},
            config={"configurable": {"thread_id": "layer2-intermediate-output-continues"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.actor_turn_count, 2)
        self.assertEqual(len(task_state.executed_steps), 1)
        self.assertEqual(len(task_state.step_validations), 2)
        self.assertEqual(
            task_state.step_validations[0].next_action,
            StepValidationDecision.CONTINUE_ACTING,
        )
        self.assertEqual(
            task_state.step_validations[1].next_action,
            StepValidationDecision.COMPLETE_STEP,
        )
        self.assertEqual(task_state.plan_steps[0].status, PlanStepStatus.COMPLETED)

    def test_needs_replan_routes_through_replanner_and_continues(self) -> None:
        graph = build_runtime_graph(
            planner_service=StubPlanner(),
            actor_service=ReplanRequestActor(),
            tool_gateway_service=StubToolGateway(),
            replanner_service=StubReplanner(),
        )
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-replanner-continues"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.actor_turn_count, 2)
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "replanner",
                "actor",
                "finalize_bootstrap",
            ],
        )
        self.assertEqual(len(task_state.plan_steps), 1)
        self.assertEqual(task_state.plan_steps[0].step_id, "step_1_replanned")
        self.assertEqual(task_state.plan_steps[0].status, PlanStepStatus.COMPLETED)
        self.assertEqual(task_state.planner_name, "stub_replanner")
        self.assertEqual(task_state.planner_prompt_version, "stub_replanner_v1")
        self.assertEqual(len(task_state.executed_steps), 1)
        self.assertEqual(task_state.executed_steps[0].step_id, "step_1_replanned")
        self.assertEqual(
            task_state.final_output_text,
            "Completed from the replanned recovery step.",
        )
        compact_trace = build_compact_trace(
            {
                "thread_id": "layer2-replanner-continues",
                "intake_result": _build_research_intake_result(),
                "final_result": result,
            }
        )
        self.assertEqual(compact_trace["planning_snapshot"]["planning_source"], "replanner")

    def test_openai_replanner_converts_structured_payload_without_network(self) -> None:
        from layer2_runtime_governance.replanning import _ReplanningPayload, _ReplanStepPayload

        task_state = _build_replanning_task_state()
        payload = _ReplanningPayload(
            rationale_summary="The original step was too broad, so the remaining path was narrowed.",
            replacement_steps=[
                _ReplanStepPayload(
                    objective="Recover with a narrower semantic step.",
                    rationale="The actor reported the previous step was not executable.",
                    expected_outcome="A narrower step the actor can complete.",
                ),
                _ReplanStepPayload(
                    objective="Synthesize the recovered result.",
                    rationale="The task still requires a final grounded answer.",
                    expected_outcome="A concise final output.",
                ),
            ],
        )
        replanner = OpenAIReplanner(
            settings=OpenAIReplannerSettings(api_key="test-key", model="test-model")
        )
        fake_client = FakeReplannerClient(payload)
        replanner._client = fake_client

        decision = replanner.replan(task_state=task_state)

        self.assertEqual(decision.replanner_name, "openai_layer2_replanner")
        self.assertEqual(decision.prompt_version, "replanner_v1")
        self.assertEqual(len(decision.replacement_steps), 2)
        self.assertEqual(decision.replacement_steps[0].step_id, "replan_step_1")
        self.assertEqual(decision.replacement_steps[0].status, PlanStepStatus.PENDING)
        self.assertEqual(fake_client.responses.last_kwargs["model"], "test-model")
        self.assertEqual(fake_client.responses.last_kwargs["text_format"], _ReplanningPayload)

    def test_replanner_prompts_include_recovery_context(self) -> None:
        task_state = _build_replanning_task_state()

        system_prompt = build_replanner_system_prompt()
        user_prompt = build_replanner_user_prompt(task_state=task_state)

        self.assertIn("Do not modify or repeat completed steps", system_prompt)
        self.assertIn("actor_replan_request", user_prompt)
        self.assertIn("The initial step is too broad", user_prompt)
        self.assertIn("current_blocked_step", user_prompt)

    def test_planner_trims_semantic_plan_to_max_steps(self) -> None:
        graph = build_runtime_graph(
            planner_service=StubPlanner(),
            actor_service=StubActor(),
            tool_gateway_service=StubToolGateway(),
            replanner_service=StubReplanner(),
        )
        result = graph.invoke(
            {"intake_result": _build_research_intake_result(max_steps=1, max_tool_calls=10)},
            config={"configurable": {"thread_id": "layer2-max-steps-trimmed"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(len(task_state.plan_steps), 1)
        self.assertEqual(task_state.plan_steps[0].status, PlanStepStatus.COMPLETED)
        self.assertEqual(len(task_state.executed_steps), 1)
        self.assertIsNone(task_state.final_output_text)
        self.assertIn("trimmed to the configured max_steps budget", task_state.step_history[1].summary)


def _build_research_intake_result(
    *,
    max_steps: int = 5,
    max_tool_calls: int | None = 10,
) -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Find official sources about the Digital Markets Act and summarize them.",
                context="Use official EU sources.",
                constraints=["Use official EU sources."],
                domain_type=DomainType.RESEARCH,
                domain_label="dma_research",
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Produce a grounded short summary."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                budget=BudgetConfig(max_steps=max_steps, max_tool_calls=max_tool_calls),
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="stub_parser",
            prompt_version="test_v1",
            schema_version="1.0.0",
        ),
    )


def _build_clarification_intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Review this contract and tell me whether I should sign it.",
                context="Contract review request.",
                constraints=[],
                domain_type=DomainType.ANALYSIS,
                domain_label="contract_review",
                sensitivity=SensitivityLevel.HIGH,
                success_criteria=[],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.8,
                budget=BudgetConfig(max_steps=4, max_tool_calls=6),
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.NEEDS_CLARIFICATION,
            parser_name="stub_parser",
            prompt_version="test_v1",
            schema_version="1.0.0",
            missing_information=["constraints", "success_criteria"],
        ),
    )


def _build_replanning_task_state() -> TaskState:
    state = TaskState.from_intake(_build_research_intake_result())
    state.current_state = RuntimeState.REPLANNING
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Collect broad evidence for the task.",
            rationale="The initial plan assumed a broad evidence step was enough.",
            expected_outcome="A broad evidence base.",
            status=PlanStepStatus.BLOCKED,
        ),
        PlanStep(
            step_id="step_2",
            objective="Synthesize the result.",
            rationale="Synthesis should follow evidence collection.",
            expected_outcome="A final answer.",
            status=PlanStepStatus.PENDING,
        ),
    ]
    state.current_plan_step_index = 0
    state.actor_turn_count = 1
    state.actor_result = ActorResult(
        plan_step_id="step_1",
        result_type=ActorResultType.NEEDS_REPLAN,
        summary="The current step is not executable as planned.",
        capability_mode=CapabilityAccessMode.NONE,
        blocking_reason="The initial step is too broad and needs a narrower recovery step.",
    )
    state.scratchpad.append(
        ScratchpadEntry(
            turn_index=1,
            plan_step_id="step_1",
            entry_type=ScratchpadEntryType.UNCERTAINTY,
            summary="The actor cannot execute the broad initial step reliably.",
        )
    )
    return state


if __name__ == "__main__":
    unittest.main()
