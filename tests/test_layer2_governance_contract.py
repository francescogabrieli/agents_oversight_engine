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
    GovernanceEvent,
    GovernanceEventType,
    GovernanceStreamSnapshot,
    RuntimeState,
    build_actor_task,
    build_governance_stream_snapshot,
    build_runtime_graph,
    PlanStep,
    PlannerDecision,
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


class GovernanceContractTests(unittest.TestCase):
    # These tests validate the stream contract between Agent Core and Governance Core.
    def test_build_governance_stream_snapshot_accepts_current_actor_step(self) -> None:
        # A stream window is valid when its latest event still points to the current step.
        graph = build_runtime_graph(planner_service=StubPlanner())
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-governance-contract-complete"}},
        )

        task_state = result["task_state"]
        actor_task = build_actor_task(task_state=task_state)

        event_history = (
            GovernanceEvent(
                event_index=1,
                plan_step_id=actor_task.current_plan_step.step_id,
                plan_step_index=actor_task.current_plan_step_index,
                event_type=GovernanceEventType.STEP_STARTED,
                summary="The actor started executing the current semantic step.",
                current_state=RuntimeState.PLANNING,
            ),
            GovernanceEvent(
                event_index=2,
                plan_step_id=actor_task.current_plan_step.step_id,
                plan_step_index=actor_task.current_plan_step_index,
                event_type=GovernanceEventType.INTENT_UPDATE,
                summary="The actor is collecting official documentary evidence.",
                current_state=RuntimeState.PLANNING,
                evidence_refs=("source://eu-dma-overview",),
            ),
        )

        snapshot = build_governance_stream_snapshot(
            actor_task=actor_task,
            event_history=event_history,
        )

        self.assertIsInstance(snapshot, GovernanceStreamSnapshot)
        self.assertEqual(snapshot.latest_event.event_type, GovernanceEventType.INTENT_UPDATE)
        self.assertEqual(len(snapshot.recent_events), 2)
        self.assertEqual(
            snapshot.latest_event.plan_step_id,
            actor_task.current_plan_step.step_id,
        )
        self.assertEqual(
            snapshot.latest_event.plan_step_index,
            actor_task.current_plan_step_index,
        )

    def test_build_governance_stream_snapshot_requires_event_history(self) -> None:
        # Governance cannot evaluate an empty stream because there is no current observation.
        graph = build_runtime_graph(planner_service=StubPlanner())
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-governance-contract-empty"}},
        )

        task_state = result["task_state"]
        actor_task = build_actor_task(task_state=task_state)

        with self.assertRaises(ValueError):
            build_governance_stream_snapshot(
                actor_task=actor_task,
                event_history=(),
            )

    def test_build_governance_stream_snapshot_rejects_mismatched_step(self) -> None:
        # Events from another plan step cannot be reused for the current evaluation window.
        graph = build_runtime_graph(planner_service=StubPlanner())
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-governance-contract-mismatch"}},
        )

        task_state = result["task_state"]
        actor_task = build_actor_task(task_state=task_state)

        event_history = (
            GovernanceEvent(
                event_index=1,
                plan_step_id="step_999",
                plan_step_index=actor_task.current_plan_step_index,
                event_type=GovernanceEventType.STEP_STARTED,
                summary="This event points to a different plan step.",
                current_state=RuntimeState.PLANNING,
            ),
        )

        with self.assertRaises(ValueError):
            build_governance_stream_snapshot(
                actor_task=actor_task,
                event_history=event_history,
            )


def _build_research_intake_result() -> IntakeResult:
    # Build a canonical complete intake payload for governance contract tests.
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


if __name__ == "__main__":
    unittest.main()
