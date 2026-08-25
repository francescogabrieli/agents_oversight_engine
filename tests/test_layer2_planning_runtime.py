from __future__ import annotations

# The fixture module bootstraps the repository src path before production imports.
# ruff: noqa: I001
import json
import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from layer1_intake import ExecutionIntent

from tests.layer2_runtime_fixtures import (
    CollidingReplanner,
    FakeReplannerClient,
    OverDecomposedSourceSummaryPlanner,
    ReplanRequestActor,
    StubPlanner,
    StubReplanner,
    _build_bedrock_actor_test_settings,
    _build_clarification_intake_result,
    _build_external_communication_intake_result,
    _build_policy_input_task_state,
    _build_replanning_task_state,
    _build_research_intake_result,
    _build_test_graph,
    _FlakyPlannerClient,
)

from layer2_runtime_governance.actor_contract import (
    ActorResult,
    ActorResultType,
    CapabilityAccessMode,
    build_actor_task,
)
from layer2_runtime_governance.domain import (
    EvidenceBurden,
    PlannerDecision,
    PlanStep,
    PlanStepStatus,
    RuntimeState,
    TaskState,
    validate_external_effect_plan_contract,
)
from layer2_runtime_governance.execution import (
    BedrockNovaActor,
    build_actor_prompt_payload,
)
from layer2_runtime_governance.governance_components import GovernanceDirective
from layer2_runtime_governance.planning import (
    OpenAIPlanner,
    OpenAIPlannerSettings,
    PlannerExecutionError,
    build_planner_system_prompt,
)
from layer2_runtime_governance.planning.replanning import (
    LocalConservativeReplanner,
    OpenAIReplanner,
    OpenAIReplannerSettings,
    ReplanDecision,
    build_replanner_system_prompt,
    build_replanner_user_prompt,
)
from layer2_runtime_governance.runtime.planning_service import planner
from layer2_runtime_governance.runtime.replanning_service import replanner
from layer3_tool_bus_external_capabilities.tool_contract import (
    ToolExecutionResult,
    ToolName,
    ToolObservation,
    ToolRequest,
)
from main_layer2 import build_compact_trace


class Layer2PlanningRuntimeTests(unittest.TestCase):
    # Scenarios remain grouped by the capability whose behavior they verify.
    def test_build_actor_task_exposes_full_plan_and_current_step(self) -> None:
        graph = _build_test_graph()
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-actor-contract-complete"}},
        )

        task_state = result["task_state"]
        self.assertEqual(len(task_state.executed_steps), 2)
        self.assertEqual(len(task_state.observations), 1)

        with self.assertRaises(ValueError):
            build_actor_task(task_state=task_state)


    def test_build_actor_task_requires_current_plan_step(self) -> None:
        graph = _build_test_graph()
        result = graph.invoke(
            {"intake_result": _build_clarification_intake_result()},
            config={"configurable": {"thread_id": "layer2-actor-contract-clarification"}},
        )

        task_state = result["task_state"]

        with self.assertRaises(ValueError):
            build_actor_task(task_state=task_state)


    def test_needs_replan_routes_through_replanner_and_continues(self) -> None:
        graph = _build_test_graph(actor_service=ReplanRequestActor())
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
                "governance",
                "replanner",
                "actor",
                "governance",
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
        self.assertEqual(len(task_state.governance_history), 2)
        self.assertEqual(task_state.governance_history[0].directive, GovernanceDirective.REPLAN)
        self.assertEqual(task_state.governance_history[1].directive, GovernanceDirective.CONTINUE)
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
        self.assertEqual(compact_trace["governance_snapshot"]["governance_pass_count"], 2)
        self.assertIsNotNone(compact_trace["governance_snapshot"]["last_governance_pass"])


    def test_replanner_clears_stale_live_tool_context(self) -> None:
        state = _build_replanning_task_state()
        state.last_tool_result = ToolExecutionResult(
            request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.FETCH_URL_TEXT,
                reason="Old failed request.",
                url="https://example.org/source",
            ),
            success=False,
            summary="Old step failed.",
            error_message="Duplicate request blocked.",
        )
        state.current_tool_request = ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.FETCH_URL_TEXT,
            reason="Old pending request.",
            url="https://example.org/source",
        )

        replanned_state = replanner(state, replanner_service=StubReplanner())

        self.assertIsNone(replanned_state.last_tool_result)
        self.assertIsNone(replanned_state.current_tool_request)
        self.assertEqual(replanned_state.current_plan_step.step_id, "step_1_replanned")


    def test_replanner_renames_replacement_steps_that_collide_with_completed_history(self) -> None:
        state = _build_replanning_task_state()
        state.plan_steps = [
            PlanStep(
                step_id="replan_step_1",
                objective="Already completed recovery step.",
                rationale="Completed replanned history must stay immutable.",
                expected_outcome="Completed output.",
                status=PlanStepStatus.COMPLETED,
            ),
            PlanStep(
                step_id="replan_step_2",
                objective="Blocked current recovery step.",
                rationale="The current step needs another replacement.",
                expected_outcome="A safer replacement.",
                status=PlanStepStatus.BLOCKED,
            ),
        ]
        state.current_plan_step_index = 1
        state.actor_result = ActorResult(
            plan_step_id="replan_step_2",
            result_type=ActorResultType.NEEDS_REPLAN,
            summary="The current replacement is still too broad.",
            capability_mode=CapabilityAccessMode.NONE,
            blocking_reason="Need another narrower step.",
        )

        replanned_state = replanner(state, replanner_service=CollidingReplanner())

        self.assertEqual(replanned_state.plan_steps[0].step_id, "replan_step_1")
        self.assertEqual(replanned_state.current_plan_step.step_id, "replan_step_2")
        self.assertEqual(
            len({step.step_id for step in replanned_state.plan_steps}),
            len(replanned_state.plan_steps),
        )


    def test_openai_replanner_converts_structured_payload_without_network(self) -> None:
        from layer2_runtime_governance.planning.replanning import (
            _ReplanningPayload,
            _ReplanStepPayload,
        )

        task_state = _build_replanning_task_state()
        payload = _ReplanningPayload(
            rationale_summary="The original step was too broad, so the remaining path was narrowed.",
            replacement_steps=[
                _ReplanStepPayload(
                    objective="Recover with a narrower semantic step.",
                    rationale="The actor reported the previous step was not executable.",
                    expected_outcome="A narrower step the actor can complete.",
                    produces_governed_external_effect_proposal=False,
                ),
                _ReplanStepPayload(
                    objective="Synthesize the recovered result.",
                    rationale="The task still requires a final grounded answer.",
                    expected_outcome="A concise final output.",
                    produces_governed_external_effect_proposal=False,
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
        self.assertEqual(decision.prompt_version, "replanner_v5")
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

    # Replanning receives the strongest still-pending proof dimensions explicitly.
    def test_replanner_prompt_projects_remaining_evidence_obligation(self) -> None:
        task_state = _build_replanning_task_state()
        task_state.plan_steps.append(
            PlanStep(
                step_id="step_2",
                objective="Produce a current, source-backed briefing.",
                rationale="The final result depends on current external facts.",
                expected_outcome="A briefing grounded in three recent sources.",
                produces_factual_claims=True,
                evidence_burden=EvidenceBurden(
                    min_independent_sources=3,
                    require_retrieved_content=True,
                    freshness_window_days=30,
                ),
            )
        )

        prompt = build_replanner_user_prompt(task_state=task_state)
        payload = json.loads(prompt.split("\n\n", 1)[1])

        self.assertEqual(
            payload["remaining_evidence_obligation"],
            {
                "factual_step_required": True,
                "min_independent_sources": 3,
                "require_retrieved_content": True,
                "freshness_window_days": 30,
            },
        )
        self.assertIn("must not", build_replanner_system_prompt())

    # A custom replanner cannot bypass an active burden by flipping every step to false.
    def test_runtime_rejects_replan_that_removes_factual_obligation(self) -> None:
        class WeakeningReplanner:
            replanner_name = "weakening_replanner"
            prompt_version = "weakening_v1"

            # Return a structurally valid replacement that semantically drops the burden.
            def replan(self, *, task_state: TaskState) -> ReplanDecision:
                return ReplanDecision(
                    replacement_steps=[
                        PlanStep(
                            step_id="replacement_1",
                            objective="Continue without a factual output.",
                            rationale="Exercise the obligation floor.",
                            expected_outcome="A procedural result.",
                        )
                    ],
                    rationale="Drop the remaining factual work.",
                    replanner_name=self.replanner_name,
                    prompt_version=self.prompt_version,
                )

        task_state = _build_replanning_task_state()
        task_state.plan_steps.append(
            PlanStep(
                step_id="step_2",
                objective="Produce a factual result.",
                rationale="The task requires independently sourced claims.",
                expected_outcome="A current, grounded answer.",
                produces_factual_claims=True,
                evidence_burden=EvidenceBurden(
                    min_independent_sources=3,
                    freshness_window_days=30,
                ),
            )
        )

        with self.assertRaisesRegex(ValueError, "removed the remaining factual"):
            replanner(task_state, replanner_service=WeakeningReplanner())

    # A custom replanner cannot erase the terminal governed-operation obligation.
    def test_runtime_rejects_replan_that_drops_external_terminal_marker(self) -> None:
        class DroppingExternalReplanner:
            replanner_name = "dropping_external_replanner"
            prompt_version = "dropping_external_v1"

            # Return a recovery step that silently omits the requested final effect.
            def replan(self, *, task_state: TaskState) -> ReplanDecision:
                return ReplanDecision(
                    replacement_steps=[
                        PlanStep(
                            step_id="replacement_1",
                            objective="Recover some local material.",
                            rationale="Exercise external-effect contract validation.",
                            expected_outcome="A local intermediate result.",
                        )
                    ],
                    rationale="Incorrectly drop the external proposal.",
                    replanner_name=self.replanner_name,
                    prompt_version=self.prompt_version,
                )

        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.current_state = RuntimeState.REPLANNING
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Prepare the governed external communication proposal.",
                rationale="The user requested delivery after governance.",
                expected_outcome="One exact execution-ready email proposal.",
                status=PlanStepStatus.BLOCKED,
                produces_governed_external_effect_proposal=True,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.NEEDS_REPLAN,
            summary="The proposal needs a different recovery path.",
            blocking_reason="The current approach cannot finish the proposal.",
        )

        with self.assertRaisesRegex(ValueError, "exactly one"):
            replanner(task_state, replanner_service=DroppingExternalReplanner())

    # The offline fallback retains pending work instead of collapsing the trajectory.
    def test_local_replanner_retains_future_factual_obligation(self) -> None:
        task_state = _build_replanning_task_state()
        burden = EvidenceBurden(
            min_independent_sources=3,
            freshness_window_days=30,
        )
        task_state.plan_steps.append(
            PlanStep(
                step_id="step_2",
                objective="Produce a factual result.",
                rationale="The task requires independently sourced claims.",
                expected_outcome="A current, grounded answer.",
                produces_factual_claims=True,
                evidence_burden=burden,
            )
        )

        decision = LocalConservativeReplanner().replan(task_state=task_state)

        self.assertEqual(len(decision.replacement_steps), 3)
        self.assertEqual(decision.replacement_steps[1].step_id, "step_2")
        self.assertTrue(decision.replacement_steps[2].produces_factual_claims)
        self.assertEqual(decision.replacement_steps[2].evidence_burden, burden)

    # The offline fallback retains a future terminal external proposal marker too.
    def test_local_replanner_retains_future_external_terminal_marker(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.current_state = RuntimeState.REPLANNING
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Recover the material needed by the external proposal.",
                rationale="The final proposal has a blocked prerequisite.",
                expected_outcome="Recovered proposal material.",
                status=PlanStepStatus.BLOCKED,
            ),
            PlanStep(
                step_id="step_2",
                objective="Prepare the governed external communication proposal.",
                rationale="The user requested delivery after governance.",
                expected_outcome="One exact execution-ready email proposal.",
                produces_governed_external_effect_proposal=True,
            ),
        ]
        task_state.current_plan_step_index = 0
        task_state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.NEEDS_REPLAN,
            summary="The prerequisite needs a conservative recovery.",
            blocking_reason="The current prerequisite cannot be completed as planned.",
        )

        decision = LocalConservativeReplanner().replan(task_state=task_state)

        self.assertFalse(
            decision.replacement_steps[0].produces_governed_external_effect_proposal
        )
        self.assertTrue(
            decision.replacement_steps[-1].produces_governed_external_effect_proposal
        )
        validate_external_effect_plan_contract(
            steps=decision.replacement_steps,
            execution_intent=task_state.execution_intent,
        )

    # The production replanner gets one bounded semantic correction before failing closed.
    def test_openai_replanner_retries_weakened_evidence_obligation(self) -> None:
        from layer2_runtime_governance.planning.replanning import (
            _ReplanEvidenceBurdenPayload,
            _ReplanningPayload,
            _ReplanStepPayload,
        )

        task_state = _build_replanning_task_state()
        task_state.plan_steps.append(
            PlanStep(
                step_id="step_2",
                objective="Produce a factual result.",
                rationale="The task requires independently sourced claims.",
                expected_outcome="A current, grounded answer.",
                produces_factual_claims=True,
                evidence_burden=EvidenceBurden(
                    min_independent_sources=3,
                    freshness_window_days=30,
                ),
            )
        )
        invalid_payload = _ReplanningPayload(
            rationale_summary="Incorrectly remove the factual obligation.",
            replacement_steps=[
                _ReplanStepPayload(
                    objective="Collect candidate material.",
                    rationale="Try another route.",
                    expected_outcome="A procedural candidate list.",
                    produces_governed_external_effect_proposal=False,
                )
            ],
        )
        corrected_payload = _ReplanningPayload(
            rationale_summary="Preserve the factual obligation.",
            replacement_steps=[
                _ReplanStepPayload(
                    objective="Validate and report current source-backed facts.",
                    rationale="The remaining task has a governed proof obligation.",
                    expected_outcome="A grounded result from three recent sources.",
                    produces_factual_claims=True,
                    evidence_burden=_ReplanEvidenceBurdenPayload(
                        min_independent_sources=3,
                        freshness_window_days=30,
                    ),
                    produces_governed_external_effect_proposal=False,
                )
            ],
        )
        replanner_service = OpenAIReplanner(
            settings=OpenAIReplannerSettings(api_key="test-key", model="test-model")
        )
        fake_client = FakeReplannerClient([invalid_payload, corrected_payload])
        replanner_service._client = fake_client

        decision = replanner_service.replan(task_state=task_state)

        self.assertEqual(fake_client.responses.call_count, 2)
        self.assertTrue(decision.replacement_steps[0].produces_factual_claims)
        correction_prompt = fake_client.responses.all_kwargs[1]["input"][1]["content"]
        correction_payload = json.loads(correction_prompt.split("\n\n", 1)[1])
        self.assertIn("removed", correction_payload["prior_contract_feedback"])

    # The production replanner also corrects a dropped external terminal marker once.
    def test_openai_replanner_corrects_missing_external_terminal_step(self) -> None:
        from layer2_runtime_governance.planning.replanning import (
            _ReplanningPayload,
            _ReplanStepPayload,
        )

        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.current_state = RuntimeState.REPLANNING
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Prepare the governed external communication proposal.",
                rationale="The user requested delivery after governance.",
                expected_outcome="One exact execution-ready email proposal.",
                status=PlanStepStatus.BLOCKED,
                produces_governed_external_effect_proposal=True,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.NEEDS_REPLAN,
            summary="The proposal needs a different recovery path.",
            blocking_reason="The current approach cannot finish the proposal.",
        )
        invalid_payload = _ReplanningPayload(
            rationale_summary="Incorrectly drop the external proposal.",
            replacement_steps=[
                _ReplanStepPayload(
                    objective="Recover a local intermediate result.",
                    rationale="Try another route.",
                    expected_outcome="A local result only.",
                    produces_governed_external_effect_proposal=False,
                )
            ],
        )
        corrected_payload = _ReplanningPayload(
            rationale_summary="Preserve the requested governed external proposal.",
            replacement_steps=[
                _ReplanStepPayload(
                    objective="Prepare the governed external communication proposal.",
                    rationale="The user still requests delivery after governance.",
                    expected_outcome="One exact execution-ready email proposal.",
                    produces_governed_external_effect_proposal=True,
                )
            ],
        )
        replanner_service = OpenAIReplanner(
            settings=OpenAIReplannerSettings(api_key="test-key", model="test-model")
        )
        fake_client = FakeReplannerClient([invalid_payload, corrected_payload])
        replanner_service._client = fake_client

        decision = replanner_service.replan(task_state=task_state)

        self.assertEqual(fake_client.responses.call_count, 2)
        self.assertTrue(
            decision.replacement_steps[-1].produces_governed_external_effect_proposal
        )
        correction_prompt = fake_client.responses.all_kwargs[1]["input"][1]["content"]
        correction_payload = json.loads(correction_prompt.split("\n\n", 1)[1])
        self.assertIn("exactly one", correction_payload["prior_contract_feedback"])

    # Planner-facing prompts must share exact, detached audit-item projections.
    def test_planning_prompt_item_projections_are_exact_and_audit_safe(self) -> None:
        task_state = _build_replanning_task_state()
        task_state.observations = [
            ToolObservation(
                observation_id="step_1:tool:1:source:1",
                content="A concrete observation used during recovery.",
                locator="https://example.org/source",
            )
        ]
        task_state.scratchpad[0].evidence_refs.append("step_1:tool:1:source:1")

        replanner_prompt = build_replanner_user_prompt(task_state=task_state)
        replanner_payload = json.loads(replanner_prompt.split("\n\n", 1)[1])
        actor_payload = build_actor_prompt_payload(
            actor_task=build_actor_task(task_state=task_state)
        )
        expected_step = {
            "step_id": "step_1",
            "objective": "Collect broad evidence for the task.",
            "rationale": "The initial plan assumed a broad evidence step was enough.",
            "expected_outcome": "A broad evidence base.",
            "status": "blocked",
            "produces_factual_claims": False,
            "produces_governed_external_effect_proposal": False,
            "evidence_burden": None,
        }
        expected_observation = {
            "observation_id": "step_1:tool:1:source:1",
            "locator": "https://example.org/source",
            "content": "A concrete observation used during recovery.",
            "source_timestamp": None,
        }
        expected_scratchpad = {
            "turn_index": 1,
            "plan_step_id": "step_1",
            "entry_type": "uncertainty",
            "summary": "The actor cannot execute the broad initial step reliably.",
            "evidence_refs": ["step_1:tool:1:source:1"],
        }

        self.assertEqual(replanner_payload["current_blocked_step"], expected_step)
        self.assertEqual(replanner_payload["plan_steps"][0], expected_step)
        self.assertEqual(actor_payload["plan"]["current_plan_step"], expected_step)
        self.assertEqual(replanner_payload["observations"], [expected_observation])
        self.assertEqual(actor_payload["observations"], [expected_observation])
        self.assertEqual(replanner_payload["scratchpad"], [expected_scratchpad])
        self.assertEqual(actor_payload["scratchpad"], [expected_scratchpad])

        task_state.scratchpad[0].evidence_refs.append("later-observation")
        self.assertEqual(
            actor_payload["scratchpad"][0]["evidence_refs"],
            ["step_1:tool:1:source:1"],
        )

    # Enforce the same factual burden invariant at every planner ingress.
    def test_provider_payload_rejects_factual_step_without_burden(self) -> None:
        from layer2_runtime_governance.planning import _PlanStepPayload
        from layer2_runtime_governance.planning.replanning import _ReplanStepPayload

        with self.assertRaises(ValueError):
            PlanStep(
                step_id="step_1",
                objective="Make a factual claim.",
                rationale="The test exercises the domain contract.",
                expected_outcome="A grounded factual output.",
                produces_factual_claims=True,
            )
        with self.assertRaises(ValidationError):
            _PlanStepPayload(
                objective="Make a factual claim.",
                rationale="The test exercises the planner contract.",
                expected_outcome="A grounded factual output.",
                produces_factual_claims=True,
                produces_governed_external_effect_proposal=False,
            )
        with self.assertRaises(ValidationError):
            _ReplanStepPayload(
                objective="Replace with a factual claim.",
                rationale="The test exercises the replanner contract.",
                expected_outcome="A grounded factual output.",
                produces_factual_claims=True,
                produces_governed_external_effect_proposal=False,
            )

    # Revalidate mutable output from a custom planner before execution begins.
    def test_runtime_rejects_mutated_factual_step_without_burden(self) -> None:
        class InvalidPlanner:
            planner_name = "invalid_planner"
            prompt_version = "invalid_v1"

            # Emulate a legacy or custom port that mutates a valid step after construction.
            def plan(self, *, task_state: TaskState) -> PlannerDecision:
                invalid_step = PlanStep(
                    step_id="step_1",
                    objective="Make a factual claim.",
                    rationale="The test exercises runtime revalidation.",
                    expected_outcome="A grounded factual output.",
                )
                invalid_step.produces_factual_claims = True
                return PlannerDecision(
                    plan_steps=[invalid_step],
                    rationale="Return an invalid legacy-style plan.",
                    planner_name=self.planner_name,
                    prompt_version=self.prompt_version,
                )

        task_state = TaskState.from_intake(_build_research_intake_result())
        with self.assertRaises(ValueError):
            planner(task_state, planner_service=InvalidPlanner())

    # Bind execution intent to one explicit terminal proposal instead of prompt wording.
    def test_external_effect_plan_contract_requires_one_terminal_marker(self) -> None:
        setup_step = PlanStep(
            step_id="step_1",
            objective="Prepare the source material.",
            rationale="The requested effect depends on prior material.",
            expected_outcome="Validated material for the final proposal.",
        )
        terminal_step = PlanStep(
            step_id="step_2",
            objective="Prepare the governed external operation.",
            rationale="The user requested execution after governance.",
            expected_outcome="One exact execution-ready proposal.",
            produces_governed_external_effect_proposal=True,
        )

        validate_external_effect_plan_contract(
            steps=[setup_step, terminal_step],
            execution_intent=ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
        )
        validate_external_effect_plan_contract(
            steps=[setup_step],
            execution_intent=ExecutionIntent.PROPOSAL_ONLY,
        )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validate_external_effect_plan_contract(
                steps=[setup_step],
                execution_intent=ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
            )
        with self.assertRaisesRegex(ValueError, "terminal semantic step"):
            validate_external_effect_plan_contract(
                steps=[terminal_step, setup_step],
                execution_intent=ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
            )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validate_external_effect_plan_contract(
                steps=[terminal_step, terminal_step],
                execution_intent=ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
            )
        with self.assertRaisesRegex(ValueError, "proposal-only"):
            validate_external_effect_plan_contract(
                steps=[terminal_step],
                execution_intent=ExecutionIntent.PROPOSAL_ONLY,
            )

    # Reject the exact benchmark failure shape before the actor can enter replanning.
    def test_runtime_rejects_incomplete_external_plan_before_actor(self) -> None:
        class IncompleteExternalPlanner:
            planner_name = "incomplete_external_planner"
            prompt_version = "incomplete_v1"

            # Return only a setup step, omitting the requested external-effect proposal.
            def plan(self, *, task_state: TaskState) -> PlannerDecision:
                return PlannerDecision(
                    plan_steps=[
                        PlanStep(
                            step_id="step_1",
                            objective="Define the requested recency window.",
                            rationale="The research has a supplied reference date.",
                            expected_outcome="A bounded date interval.",
                        )
                    ],
                    rationale="Start from the requested date window.",
                    planner_name=self.planner_name,
                    prompt_version=self.prompt_version,
                )

        task_state = TaskState.from_intake(_build_external_communication_intake_result())

        with self.assertRaisesRegex(ValueError, "exactly one"):
            planner(task_state, planner_service=IncompleteExternalPlanner())

    # The production planner receives one bounded correction for an incomplete plan.
    def test_openai_planner_corrects_missing_external_terminal_step(self) -> None:
        from layer2_runtime_governance.planning import (
            _PlanningPayload,
            _PlanStepPayload,
        )

        incomplete_payload = _PlanningPayload(
            rationale_summary="Define only the recency window.",
            plan_steps=[
                _PlanStepPayload(
                    objective="Define the requested recency window.",
                    rationale="The task supplies a reference date.",
                    expected_outcome="A bounded date interval.",
                    produces_governed_external_effect_proposal=False,
                )
            ],
        )
        corrected_payload = _PlanningPayload(
            rationale_summary="Cover the task through its requested external effect.",
            plan_steps=[
                _PlanStepPayload(
                    objective="Research and synthesize the requested briefing.",
                    rationale="The external communication depends on grounded content.",
                    expected_outcome="A source-backed briefing.",
                    produces_governed_external_effect_proposal=False,
                ),
                _PlanStepPayload(
                    objective="Prepare the governed external communication proposal.",
                    rationale="The user requested delivery after governance.",
                    expected_outcome="One exact execution-ready email proposal.",
                    produces_governed_external_effect_proposal=True,
                ),
            ],
        )
        planner_service = OpenAIPlanner(
            settings=OpenAIPlannerSettings(api_key="test-key", model="test-model")
        )
        fake_client = FakeReplannerClient([incomplete_payload, corrected_payload])
        planner_service._client = fake_client

        decision = planner_service.plan(
            task_state=TaskState.from_intake(
                _build_external_communication_intake_result()
            )
        )

        self.assertEqual(fake_client.responses.call_count, 2)
        self.assertEqual(decision.prompt_version, "planner_v6")
        self.assertTrue(
            decision.plan_steps[-1].produces_governed_external_effect_proposal
        )
        correction_prompt = fake_client.responses.all_kwargs[1]["input"][1]["content"]
        correction_payload = json.loads(correction_prompt.split("\n\n", 1)[1])
        self.assertIn("exactly one", correction_payload["prior_contract_feedback"])

    # Fail closed when the bounded correction still omits the requested effect.
    def test_openai_planner_rejects_two_incomplete_external_plans(self) -> None:
        from layer2_runtime_governance.planning import (
            _PlanningPayload,
            _PlanStepPayload,
        )

        incomplete_payload = _PlanningPayload(
            rationale_summary="Return only a setup step.",
            plan_steps=[
                _PlanStepPayload(
                    objective="Define the requested recency window.",
                    rationale="The task supplies a reference date.",
                    expected_outcome="A bounded date interval.",
                    produces_governed_external_effect_proposal=False,
                )
            ],
        )
        planner_service = OpenAIPlanner(
            settings=OpenAIPlannerSettings(api_key="test-key", model="test-model")
        )
        fake_client = FakeReplannerClient([incomplete_payload, incomplete_payload])
        planner_service._client = fake_client

        with self.assertRaisesRegex(PlannerExecutionError, "after correction"):
            planner_service.plan(
                task_state=TaskState.from_intake(
                    _build_external_communication_intake_result()
                )
            )

        self.assertEqual(fake_client.responses.call_count, 2)

    # Make the declared proof obligation visible to the bounded executor.
    def test_actor_prompt_projects_declared_evidence_burden(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Produce a current factual answer.",
                rationale="The answer requires independently sourced evidence.",
                expected_outcome="A source-backed factual answer.",
                status=PlanStepStatus.IN_PROGRESS,
                produces_factual_claims=True,
                evidence_burden=EvidenceBurden(
                    min_independent_sources=2,
                    require_retrieved_content=True,
                    freshness_window_days=7,
                ),
            )
        ]
        task_state.current_plan_step_index = 0

        payload = build_actor_prompt_payload(
            actor_task=build_actor_task(task_state=task_state)
        )

        self.assertEqual(
            payload["plan"]["current_plan_step"]["evidence_burden"],
            {
                "min_independent_sources": 2,
                "require_retrieved_content": True,
                "freshness_window_days": 7,
            },
        )


    def test_openai_planner_settings_default_timeout_without_env(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "layer2_runtime_governance.planning._load_env_file_values",
            return_value={},
        ):
            settings = OpenAIPlannerSettings.from_env()

        self.assertEqual(settings.timeout_seconds, 30.0)


    # Planner and replanner configuration must retain separate environment namespaces.
    def test_openai_planning_settings_keep_distinct_environment_keys(self) -> None:
        env = {
            "OPENAI_API_KEY": "test-key",
            "LAYER2_PLANNER_OPENAI_MODEL": "planner-model",
            "LAYER2_PLANNER_OPENAI_TIMEOUT_SECONDS": "11",
            "LAYER2_REPLANNER_OPENAI_MODEL": "replanner-model",
            "LAYER2_REPLANNER_OPENAI_TIMEOUT_SECONDS": "17",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "layer2_runtime_governance.planning._load_env_file_values",
                return_value={},
            ),
            patch(
                "layer2_runtime_governance.planning.replanning.load_env_file_values",
                return_value={},
            ),
        ):
            planner_settings = OpenAIPlannerSettings.from_env()
            replanner_settings = OpenAIReplannerSettings.from_env()

        self.assertEqual(planner_settings.model, "planner-model")
        self.assertEqual(planner_settings.timeout_seconds, 11.0)
        self.assertEqual(replanner_settings.model, "replanner-model")
        self.assertEqual(replanner_settings.timeout_seconds, 17.0)


    def test_openai_planner_retries_retryable_provider_failures(self) -> None:
        from layer2_runtime_governance.planning import (
            _PlanningPayload,
            _PlanStepPayload,
        )

        planner_service = OpenAIPlanner(
            settings=OpenAIPlannerSettings(api_key="test-key", model="test-model")
        )
        planner_service._client = _FlakyPlannerClient(
            _PlanningPayload(
                rationale_summary="Transient provider errors recovered cleanly.",
                plan_steps=[
                    _PlanStepPayload(
                        objective="Collect authoritative DMA sources.",
                        rationale="The task is research-oriented.",
                        expected_outcome="A compact source set.",
                        produces_governed_external_effect_proposal=False,
                    )
                ],
            ),
            failures=[429, 503],
        )

        with patch("llm_retry.time.sleep", return_value=None):
            decision = planner_service.plan(task_state=_build_policy_input_task_state())

        self.assertEqual(decision.rationale, "Transient provider errors recovered cleanly.")
        self.assertEqual(planner_service._client.responses.call_count, 3)


    def test_bedrock_actor_incomplete_tool_call_requests_replan(self) -> None:
        actor = BedrockNovaActor(settings=_build_bedrock_actor_test_settings())

        class IncompleteToolCallClient:
            # Return a valid action name with an incomplete contract, as some providers can do.
            def complete_tool_call(self, **_: object) -> dict[str, object]:
                return {
                    "tool_name": "request_web_search",
                    "arguments": {"reason": "Find official DMA sources."},
                }

        actor._client = IncompleteToolCallClient()  # type: ignore[attr-defined]
        actor_task = build_actor_task(task_state=_build_policy_input_task_state())

        result = actor.act(actor_task=actor_task)

        self.assertTrue(result.provider_protocol_failure)
        self.assertIn("incomplete runtime action", result.summary)
        self.assertIn("missing required string argument 'summary'", result.blocking_reason)


    def test_planner_prompt_is_detailed_and_general_purpose(self) -> None:
        prompt = build_planner_system_prompt()

        self.assertIn("complete semantic plan", prompt)
        self.assertIn("Identify prerequisites and dependencies", prompt)
        self.assertIn("There is no preferred fixed step count", prompt)
        self.assertIn("complete execution-ready proposal", prompt)
        self.assertIn("produces_governed_external_effect_proposal=true", prompt)
        self.assertIn("source identities, dates, organizations", prompt)
        self.assertIn("user-supplied task contract", prompt)
        self.assertIn("not additional plan steps", prompt)
        self.assertNotIn("email", prompt.lower())
        self.assertNotIn("research", prompt.lower())


    def test_planner_does_not_trim_the_semantic_plan(self) -> None:
        task_state = TaskState.from_intake(
            _build_research_intake_result()
        )
        planned_state = planner(task_state, planner_service=StubPlanner())

        self.assertEqual(planned_state.current_state, RuntimeState.PLANNING)
        self.assertEqual(len(planned_state.plan_steps), 2)
        self.assertEqual(planned_state.plan_steps[0].status, PlanStepStatus.IN_PROGRESS)
        self.assertNotIn("trimmed", task_state.step_history[0].summary.lower())


    def test_runtime_preserves_over_decomposed_planner_output_verbatim(self) -> None:
        task_state = TaskState.from_intake(
            _build_research_intake_result()
        )
        task_state = planner(task_state, planner_service=OverDecomposedSourceSummaryPlanner())
        self.assertEqual(len(task_state.plan_steps), 3)
        self.assertEqual(
            [step.objective for step in task_state.plan_steps],
            [
                "Down-select official sources for the short summary.",
                "Extract factual points from the selected official sources.",
                "Draft the brief summary.",
            ],
        )
        self.assertNotIn("normalized", task_state.step_history[0].summary.lower())


    def test_runtime_does_not_rewrite_external_plan(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())

        class GenericExternalEmailPlanner:
            planner_name = "generic_external_email_planner"
            prompt_version = "stub_v1"

            # Return a deliberately over-decomposed draft/governance trajectory.
            def plan(self, *, task_state):
                return PlannerDecision(
                    plan_steps=[
                        PlanStep(
                            step_id="step_1",
                            objective="Identify the approved product update points.",
                            rationale="The email must use only the supplied product notes.",
                            expected_outcome="A bounded list of approved update points.",
                        ),
                        PlanStep(
                            step_id="step_2",
                            objective="Draft a customer product update email.",
                            rationale="The requested channel is an external email.",
                            expected_outcome="A complete customer-facing draft.",
                        ),
                        PlanStep(
                            step_id="step_3",
                            objective="Refine wording for clarity.",
                            rationale="Keep the supplied tone and scope.",
                            expected_outcome="A clear draft without new claims.",
                        ),
                        PlanStep(
                            step_id="step_4",
                            objective="Prepare the draft for human review.",
                            rationale="External communication remains review-gated.",
                            expected_outcome="The same draft in review-ready form.",
                            produces_governed_external_effect_proposal=True,
                        ),
                    ],
                    rationale="Prepare the requested product update without adding facts.",
                    planner_name=self.planner_name,
                    prompt_version=self.prompt_version,
                )

        planned_state = planner(task_state, planner_service=GenericExternalEmailPlanner())
        plan_text = " ".join(
            f"{step.objective} {step.rationale} {step.expected_outcome}"
            for step in planned_state.plan_steps
        ).lower()

        self.assertEqual(len(planned_state.plan_steps), 4)
        self.assertIn("draft a customer product update email", plan_text)
        self.assertIn("human review", plan_text)
        for forbidden in ("60 minutes", "data loss", "delayed processing", "incident"):
            self.assertNotIn(forbidden, plan_text)

    # Planner-owned semantics remain untouched even when steps could be merged.
    def test_external_plan_preserves_all_planner_steps(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())

        class EvidenceExternalEmailPlanner:
            planner_name = "evidence_external_email_planner"
            prompt_version = "stub_v1"

            # Model source discovery and extraction separately to exercise compaction.
            def plan(self, *, task_state):
                return PlannerDecision(
                    plan_steps=[
                        PlanStep(
                            step_id="step_1",
                            objective="Discover the relevant official source.",
                            rationale="Find the source before extraction.",
                            expected_outcome="One official source.",
                        ),
                        PlanStep(
                            step_id="step_2",
                            objective="Extract the exact authorized facts for the email.",
                            rationale="The requested claims require extracted support.",
                            expected_outcome="Detailed evidence for the draft.",
                        ),
                        PlanStep(
                            step_id="step_3",
                            objective="Draft the email.",
                            rationale="Use the extracted evidence.",
                            expected_outcome="A complete email.",
                            produces_governed_external_effect_proposal=True,
                        ),
                    ],
                    rationale="Collect evidence and draft the requested email.",
                    planner_name=self.planner_name,
                    prompt_version=self.prompt_version,
                )

        planned_state = planner(task_state, planner_service=EvidenceExternalEmailPlanner())

        self.assertEqual(len(planned_state.plan_steps), 3)
        self.assertIn("Discover the relevant official source", planned_state.plan_steps[0].objective)
        self.assertIn("Extract the exact authorized facts", planned_state.plan_steps[1].objective)
        self.assertEqual("A complete email.", planned_state.plan_steps[2].expected_outcome)
        self.assertEqual(
            set(vars(planned_state.plan_steps[0])),
            {
                "step_id",
                "objective",
                "rationale",
                "expected_outcome",
                "status",
                "produces_factual_claims",
                "produces_governed_external_effect_proposal",
                "evidence_burden",
            },
        )


if __name__ == "__main__":
    unittest.main()
