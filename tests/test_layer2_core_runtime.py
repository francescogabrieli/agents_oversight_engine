from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver

from tests.layer2_runtime_fixtures import (
    LocalProgressPlanner,
    ProgressThenCompleteActor,
    StaticJudge,
    StubActor,
    StubPlanner,
    StubPolicyEngine,
    StubReplanner,
    StubToolGateway,
    _build_external_communication_intake_result_for_governance,
    _build_research_intake_result,
    _build_test_graph,
    _judge_wait_review_assessment,
)

from layer2_runtime_governance.actor_contract import ActorResult, ActorResultType, ActorTask, CapabilityAccessMode
from layer2_runtime_governance.domain import PlanStepStatus, RuntimeState, TaskState
from layer2_runtime_governance.governance_components import (
    GovernanceDirective,
    PolicyDecision,
)
from layer4_state_evidence_observability.infrastructure.checkpointing import (
    build_runtime_serializer,
)
from layer4_state_evidence_observability.infrastructure.runtime_artifacts import (
    RuntimeArtifactStore,
)
from main_layer2 import run_layer2_flow, write_layer2_trace_file
REPO_ROOT = Path(__file__).resolve().parents[1]


class _CrashingActor:
    actor_name = "crashing_actor"
    prompt_version = "crashing_actor_v1"

    # Simulate one unexpected runtime failure after a durable planner checkpoint.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        raise RuntimeError("synthetic actor failure")


class Layer2RuntimeCoreTests(unittest.TestCase):
    # Scenarios remain grouped by the capability whose behavior they verify.
    def test_complete_intake_reaches_completed_state(self) -> None:
        graph = _build_test_graph()
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-ready-to-execute-complete"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertTrue(task_state.bootstrap_completed)
        self.assertEqual(len(task_state.step_history), 11)
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "governance",
                "tool_gateway",
                "observe_tool_result",
                "actor",
                "governance",
                "actor",
                "governance",
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
        self.assertEqual(task_state.actor_result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertEqual(task_state.actor_result.capability_mode, CapabilityAccessMode.NONE)
        self.assertEqual(len(task_state.executed_steps), 2)
        self.assertEqual(task_state.actor_turn_count, 3)
        self.assertEqual(task_state.tool_call_count, 1)
        self.assertEqual(len(task_state.observations), 1)
        self.assertEqual(len(task_state.scratchpad), 4)
        self.assertEqual(len(task_state.step_validations), 2)
        self.assertEqual(len(task_state.governance_history), 3)
        self.assertIsNotNone(task_state.last_governance_decision)
        self.assertEqual(
            task_state.last_governance_decision.directive,
            GovernanceDirective.CONTINUE,
        )
        self.assertEqual(
            task_state.last_governance_decision.policy_decision,
            PolicyDecision.ALLOW,
        )
        self.assertTrue(all(item.actor_self_confidence > 0 for item in task_state.step_validations))
        self.assertEqual(
            task_state.final_output_text,
            "The DMA is an EU regulation that targets gatekeeper platforms.",
        )


    def test_task_state_bootstrap_does_not_treat_intake_as_evidence(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result_for_governance())

        self.assertEqual(task_state.observations, [])
        self.assertTrue(task_state.task_spec.goal)


    def test_intermediate_output_does_not_complete_step_without_validation(self) -> None:
        graph = _build_test_graph(
            planner_service=LocalProgressPlanner(),
            actor_service=ProgressThenCompleteActor(),
        )
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-intermediate-output-continues"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.actor_turn_count, 2)
        self.assertEqual(len(task_state.executed_steps), 1)
        self.assertEqual(len(task_state.step_validations), 2)
        self.assertEqual(len(task_state.governance_history), 2)
        self.assertEqual(
            [decision.directive for decision in task_state.governance_history],
            [GovernanceDirective.REPLAN, GovernanceDirective.CONTINUE],
        )
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "governance",
                "replanner",
                "actor",
                "governance",
                "finalize_bootstrap",
            ],
        )
        self.assertEqual(task_state.step_validations[0].actor_self_confidence, 0.45)
        self.assertEqual(task_state.step_validations[1].actor_self_confidence, 0.82)
        self.assertEqual(task_state.plan_steps[0].status, PlanStepStatus.COMPLETED)


    def test_full_trace_is_written_to_disk(self) -> None:
        trace = run_layer2_flow(
            raw_task_text="Find official sources about the DMA and summarize them.",
            context_hint="Prefer official EU sources.",
            thread_id="full-trace-file-test",
            demo_intake_profile="research",
            planner_service=StubPlanner(),
            actor_service=StubActor(),
            tool_gateway_service=StubToolGateway(),
            replanner_service=StubReplanner(),
            policy_engine_service=StubPolicyEngine(),
            checkpointer=InMemorySaver(serde=build_runtime_serializer()),
        )

        temp_dir = REPO_ROOT / "_layer2_trace_test_output"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            output_path = temp_dir / "full-trace-1.json"
            written_path = write_layer2_trace_file(trace, output_path=output_path)

            self.assertEqual(written_path, output_path)
            self.assertTrue(written_path.exists())

            payload = json.loads(written_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["runtime"]["thread_id"], "full-trace-file-test")
            self.assertEqual(payload["runtime"]["current_state"], "completed")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


    # A runtime exception preserves the latest checkpoint and a typed diagnostic artifact.
    def test_run_layer2_flow_persists_failure_artifact(self) -> None:
        run_id = uuid4().hex
        thread_id = f"failed-runtime-test-{run_id}"
        temp_dir = REPO_ROOT / f"_layer2_runtime_failure_test_{run_id}"
        artifact_store = RuntimeArtifactStore(root_path=temp_dir)

        try:
            with self.assertRaisesRegex(RuntimeError, "synthetic actor failure"):
                run_layer2_flow(
                    raw_task_text="Prepare a test result.",
                    thread_id=thread_id,
                    demo_intake_profile="research",
                    planner_service=StubPlanner(),
                    actor_service=_CrashingActor(),
                    tool_gateway_service=StubToolGateway(),
                    replanner_service=StubReplanner(),
                    policy_engine_service=StubPolicyEngine(),
                    artifact_store=artifact_store,
                    checkpointer=InMemorySaver(serde=build_runtime_serializer()),
                )

            failure_path = (
                artifact_store.build_paths(thread_id).thread_dir
                / "runtime_failure.json"
            )
            self.assertTrue(failure_path.exists())
            payload = json.loads(failure_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["thread_id"], thread_id)
            self.assertEqual(payload["error_type"], "RuntimeError")
            self.assertIn("synthetic actor failure", payload["error_message"])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            trace = run_layer2_flow(
                raw_task_text="Find official sources about the DMA and summarize them.",
                context_hint="Prefer official EU sources.",
                thread_id=f"persisted-runtime-test-{run_id}",
                demo_intake_profile="research",
                planner_service=StubPlanner(),
                actor_service=StubActor(),
                tool_gateway_service=StubToolGateway(),
                replanner_service=StubReplanner(),
                policy_engine_service=StubPolicyEngine(),
                judge_service=StaticJudge(_judge_wait_review_assessment()),
                artifact_store=artifact_store,
                checkpointer=InMemorySaver(serde=build_runtime_serializer()),
            )

            paths = artifact_store.build_paths(trace["thread_id"])
            self.assertTrue(paths.full_trace_path.exists())
            self.assertEqual(paths.full_trace_path.name, "full-trace-2.json")
            self.assertFalse((paths.thread_dir / "trace.compact.json").exists())
            self.assertTrue(paths.dashboard_snapshot_path.exists())
            self.assertTrue(paths.runtime_summary_path.exists())
            self.assertFalse(paths.pending_human_review_path.exists())
            governance_exchange_path = paths.thread_dir / "governance_exchange.md"
            self.assertTrue(governance_exchange_path.exists())
            governance_exchange = governance_exchange_path.read_text(encoding="utf-8")
            self.assertIn("# Actor, Governance and OPA Exchange", governance_exchange)
            self.assertIn("GOVERNANCE EXCHANGE: ACTOR RESULT", governance_exchange)
            self.assertIn("GOVERNANCE EXCHANGE: OPA PRELIMINARY DECISION RESPONSE", governance_exchange)
            self.assertEqual(
                sorted(path.name for path in paths.thread_dir.glob("*.json")),
                ["dashboard_snapshot.json", "full-trace-2.json", "runtime_summary.json"],
            )

            dashboard_payload = json.loads(paths.dashboard_snapshot_path.read_text(encoding="utf-8"))
            runtime_summary = json.loads(paths.runtime_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(dashboard_payload["runtime_state"], "completed")
            self.assertEqual(runtime_summary["thread_id"], trace["thread_id"])
            self.assertEqual(runtime_summary["artifact_version"], "layer2_runtime_artifacts_v2")
            self.assertEqual(
                runtime_summary["task_goal"],
                "Find official sources about the DMA and summarize them.",
            )
            self.assertEqual(runtime_summary["review_summary"], None)
            self.assertEqual(runtime_summary["pending_request_id"], None)
            self.assertEqual(runtime_summary["allowed_decisions"], [])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
