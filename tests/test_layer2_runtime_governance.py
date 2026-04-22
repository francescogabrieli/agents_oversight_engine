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
    build_actor_task,
    build_runtime_graph,
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

    # Return one stable read-only execution outcome for the current semantic step.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.PROPOSE_ACTION,
            summary="Propose a read-only documentary evidence collection action.",
            capability_mode=CapabilityAccessMode.READ_ONLY,
            external_impact=False,
        )


class Layer2RuntimeGraphTests(unittest.TestCase):
    # These tests verify the bootstrap-plus-planner-plus-actor behavior.
    def test_complete_intake_reaches_ready_tool_execution_state(self) -> None:
        graph = build_runtime_graph(
            planner_service=StubPlanner(),
            actor_service=StubActor(),
        )
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-ready-to-execute-complete"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.READY_TOOL_EXECUTION)
        self.assertTrue(task_state.bootstrap_completed)
        self.assertEqual(len(task_state.step_history), 4)
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "finalize_bootstrap",
            ],
        )
        self.assertEqual(task_state.runtime_flags, [])
        self.assertEqual(len(task_state.plan_steps), 2)
        self.assertEqual(task_state.current_plan_step_index, 0)
        self.assertEqual(
            task_state.current_plan_step.objective,
            "Collect the minimum official evidence needed for the task.",
        )
        self.assertEqual(task_state.current_plan_step.status, PlanStepStatus.IN_PROGRESS)
        self.assertEqual(task_state.planner_name, "stub_planner")
        self.assertEqual(task_state.planner_prompt_version, "stub_v1")
        self.assertEqual(task_state.actor_name, "stub_actor")
        self.assertEqual(task_state.actor_prompt_version, "stub_actor_v1")
        self.assertIsNotNone(task_state.actor_result)
        self.assertEqual(task_state.actor_result.result_type, ActorResultType.PROPOSE_ACTION)
        self.assertEqual(task_state.actor_result.capability_mode, CapabilityAccessMode.READ_ONLY)
        self.assertEqual(task_state.executed_steps, [])

    def test_needs_clarification_skips_actor(self) -> None:
        graph = build_runtime_graph(
            planner_service=StubPlanner(),
            actor_service=StubActor(),
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
        )
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-actor-contract-complete"}},
        )

        task_state = result["task_state"]
        actor_task = build_actor_task(task_state=task_state)

        self.assertIsInstance(actor_task, ActorTask)
        self.assertEqual(actor_task.current_state, RuntimeState.READY_TOOL_EXECUTION)
        self.assertEqual(len(actor_task.plan_steps), 2)
        self.assertIsInstance(actor_task.plan_steps, tuple)
        self.assertEqual(actor_task.past_steps, ())
        self.assertEqual(actor_task.current_plan_step_index, 0)
        self.assertEqual(
            actor_task.current_plan_step.objective,
            "Collect the minimum official evidence needed for the task.",
        )
        self.assertEqual(
            actor_task.plan_steps[actor_task.current_plan_step_index].step_id,
            actor_task.current_plan_step.step_id,
        )
        self.assertEqual(actor_task.current_plan_step.status, PlanStepStatus.IN_PROGRESS)
        self.assertEqual(actor_task.plan_steps[1].status, PlanStepStatus.PENDING)
        self.assertEqual(actor_task.planner_name, "stub_planner")
        self.assertEqual(actor_task.planner_prompt_version, "stub_v1")

    def test_build_actor_task_requires_current_plan_step(self) -> None:
        graph = build_runtime_graph(
            planner_service=StubPlanner(),
            actor_service=StubActor(),
        )
        result = graph.invoke(
            {"intake_result": _build_clarification_intake_result()},
            config={"configurable": {"thread_id": "layer2-actor-contract-clarification"}},
        )

        task_state = result["task_state"]

        with self.assertRaises(ValueError):
            build_actor_task(task_state=task_state)


def _build_research_intake_result() -> IntakeResult:
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
                budget=BudgetConfig(max_steps=5, max_tool_calls=10),
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


if __name__ == "__main__":
    unittest.main()
