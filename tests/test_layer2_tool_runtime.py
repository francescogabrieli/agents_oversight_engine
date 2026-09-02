from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.layer2_runtime_fixtures import (
    RepeatFetchActor,
    SingleStepPlanner,
    StubPolicyEngine,
    _build_research_intake_result,
    _build_test_graph,
)

from layer2_runtime_governance.domain import (
    PlanStep,
    ToolDisposition,
    PlanStepStatus,
    RuntimeFlag,
    RuntimeState,
    TaskState,
)
from layer2_runtime_governance.actor_contract import ActorResult, ActorResultType
from layer2_runtime_governance.governance_components import (
    build_policy_evaluation_input,
)
from layer2_runtime_governance.signal_services import build_trajectory_signal
from layer2_runtime_governance.runtime.tool_service import observe_tool_result, tool_gateway
from layer3_tool_bus_external_capabilities.external_operations.runtime_projection import (
    prepare_external_operation_proposal,
)
from layer3_tool_bus_external_capabilities.tool_contract import (
    ToolErrorCode,
    ToolExecutionResult,
    ToolName,
    ToolObservation,
    ToolRequest,
    canonical_tool_request_identity,
)
from layer3_tool_bus_external_capabilities.tools import (
    build_default_tool_gateway_config,
)
from layer3_tool_bus_external_capabilities.tools.adapters.openai_search import (
    extract_web_observations,
)
class Layer2ToolRuntimeTests(unittest.TestCase):
    # Scenarios remain grouped by the capability whose behavior they verify.
    def test_complete_intake_supports_tool_execution(self) -> None:
        graph = _build_test_graph()
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-tool-execution"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.tool_call_count, 1)
        self.assertEqual(len(task_state.observations), 1)

    # A pending batch item replaces the prior item's governed proposal and digest.
    def test_batch_advance_rebinds_governance_to_current_tool_request(self) -> None:
        state = TaskState.from_intake(_build_research_intake_result())
        state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Validate two official sources.",
                rationale="Both pages need direct inspection.",
                expected_outcome="Two source observations.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        state.current_plan_step_index = 0
        anthropic = ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.FETCH_URL_TEXT,
            reason="Verify the Anthropic publication date.",
            url="https://www.anthropic.com/news/how-claudes-text-watermark-works",
        )
        fda = ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.FETCH_URL_TEXT,
            reason="Verify the FDA publication date.",
            url=(
                "https://www.fda.gov/news-events/press-announcements/"
                "fda-seeks-public-feedback-inform-regulatory-approach-"
                "generative-ai-enabled-medical-devices"
            ),
        )
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.PROPOSE_ACTION,
            summary="Fetch both official announcements.",
            tool_request=anthropic,
            tool_requests=(anthropic, fda),
        )
        state.current_tool_request = anthropic
        state.pending_tool_requests = [fda]
        config = build_default_tool_gateway_config()

        anthropic_proposal = prepare_external_operation_proposal(
            task_state=state,
            tool_gateway_config=config,
        )
        state.current_state = RuntimeState.ACTING
        state.current_tool_request = None
        state.last_tool_result = ToolExecutionResult(
            request=anthropic,
            success=False,
            summary="The first source could not be fetched.",
            error_code=ToolErrorCode.FORBIDDEN,
        )

        observe_tool_result(state)
        fda_proposal = prepare_external_operation_proposal(
            task_state=state,
            tool_gateway_config=config,
        )

        self.assertEqual(state.current_tool_request, fda)
        self.assertEqual(fda_proposal.target.value, fda.url)
        self.assertIn(fda.url, fda_proposal.payload_preview)
        self.assertNotIn(anthropic.url, fda_proposal.payload_preview)
        self.assertNotEqual(
            anthropic_proposal.payload_digest,
            fda_proposal.payload_digest,
        )
        self.assertEqual(
            state.actor_result.external_operation_proposal,
            fda_proposal,
        )
        trajectory = build_trajectory_signal(
            task_state=state,
            policy_input=build_policy_evaluation_input(task_state=state),
            thresholds_version="policy.v1",
        )["raw_facts"]
        self.assertTrue(
            all(
                proposal.boundary.value == "evidence_read"
                for proposal in state.external_operation_history
            )
        )
        self.assertEqual(trajectory["action_churn_count"], 0)
        self.assertFalse(trajectory["external_candidate"])

    # Wording-only changes cannot create a new operational fetch identity or digest.
    def test_fetch_reason_paraphrase_keeps_operational_identity(self) -> None:
        first = ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.FETCH_URL_TEXT,
            reason="Verify the publication date.",
            url="https://example.org/official-announcement",
        )
        second = ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.FETCH_URL_TEXT,
            reason="Please inspect this page for its date.",
            url="https://example.org/official-announcement",
        )

        self.assertEqual(
            canonical_tool_request_identity(first),
            canonical_tool_request_identity(second),
        )

        state = TaskState.from_intake(_build_research_intake_result())
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.PROPOSE_ACTION,
            summary="Fetch the announcement.",
            tool_request=first,
        )
        state.current_tool_request = first
        first_proposal = prepare_external_operation_proposal(
            task_state=state,
            tool_gateway_config=build_default_tool_gateway_config(),
        )
        state.current_tool_request = second
        second_proposal = prepare_external_operation_proposal(
            task_state=state,
            tool_gateway_config=build_default_tool_gateway_config(),
        )

        self.assertEqual(first_proposal.payload_digest, second_proposal.payload_digest)

    # Tracking parameters do not create a second operational fetch identity.
    def test_fetch_tracking_parameter_keeps_operational_identity(self) -> None:
        tracked = ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.FETCH_URL_TEXT,
            reason="Fetch the tracked result.",
            url="https://Example.org/announcement/?utm_source=openai&utm_campaign=test",
        )
        clean = ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.FETCH_URL_TEXT,
            reason="Fetch the canonical result.",
            url="https://example.org/announcement",
        )
        distinct_query = ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.FETCH_URL_TEXT,
            reason="Fetch a materially different view.",
            url="https://example.org/announcement?version=2",
        )

        self.assertEqual(
            canonical_tool_request_identity(tracked),
            canonical_tool_request_identity(clean),
        )
        self.assertNotEqual(
            canonical_tool_request_identity(clean),
            canonical_tool_request_identity(distinct_query),
        )


    # Regression for benchmark-web-email-12: an actor asked 58 times for a URL it had
    # never observed. The unanchored guard refused it before the backend, so it was
    # neither an executed failure nor a duplicate, and no convergence rule could see it.
    # Every locally settled disposition must escalate, not only a named subset.
    def test_every_locally_settled_disposition_escalates_repeated_requests(self) -> None:
        settled = [item for item in ToolDisposition if not item.reached_backend]
        self.assertIn(ToolDisposition.REJECTED_UNANCHORED_TARGET, settled)
        self.assertIn(ToolDisposition.REJECTED_CIRCUIT, settled)
        self.assertIn(ToolDisposition.REJECTED_DUPLICATE, settled)
        self.assertIn(ToolDisposition.CACHE_HIT, settled)
        self.assertEqual(
            [item for item in ToolDisposition if item.reached_backend],
            [
                ToolDisposition.EXECUTED_SUCCESS,
                ToolDisposition.EXECUTED_RETRYABLE_FAILURE,
                ToolDisposition.EXECUTED_TERMINAL_FAILURE,
            ],
        )

    # A locally rejected request never reaches the backend and never makes the gateway
    # decide anything beyond ACTING: only OPA, through Trajectory's
    # current_step_tool_attempt_count, may ever escalate a step stuck on one target.
    def test_unanchored_target_repeats_stay_local_without_gateway_replan(self) -> None:
        state = TaskState.from_intake(_build_research_intake_result())
        state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Collect sources",
                rationale="r",
                expected_outcome="o",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        state.current_plan_step_index = 0

        invented = "https://example.org/never-observed"
        gateway = SimpleNamespace(execute=lambda **_: self.fail("backend must not be reached"))
        for _ in range(2):
            state.current_state = RuntimeState.READY_TOOL_EXECUTION
            state.current_tool_request = ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.FETCH_URL_TEXT,
                reason="fetch an unobserved url",
                url=invented,
            )
            tool_gateway(state, tool_gateway_service=gateway)

        dispositions = [item.disposition for item in state.tool_attempts]
        self.assertTrue(
            all(item is ToolDisposition.REJECTED_UNANCHORED_TARGET for item in dispositions)
        )
        self.assertEqual(state.current_state, RuntimeState.ACTING)
        self.assertIsNone(state.actor_result)
        # A refused request never becomes a backend call in the trajectory facts.
        self.assertEqual(state.tool_call_count, 0)

    # The run-scoped exhaustion ceiling has to persist across a plan step boundary,
    # whatever produced that boundary - the gateway itself never replans, so the only
    # way a step changes underneath a fixated target is a decision made elsewhere
    # (the actor's own liveness fuse, or OPA). Once exhausted, the target stays refused.
    def test_exhausted_target_stays_refused_across_a_step_boundary(self) -> None:
        state = TaskState.from_intake(_build_research_intake_result())
        state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Collect sources",
                rationale="r",
                expected_outcome="o",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        state.current_plan_step_index = 0
        invented = "https://example.org/never-observed"
        gateway = SimpleNamespace(execute=lambda **_: self.fail("backend must not be reached"))

        # Three settled attempts reach the run-scoped ceiling; each is still evaluated
        # for anchoring directly, since exhaustion is measured before the attempt is
        # recorded and the ceiling is not yet reached during any of these three.
        for _ in range(3):
            state.current_state = RuntimeState.READY_TOOL_EXECUTION
            state.current_tool_request = ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.FETCH_URL_TEXT,
                reason="fetch an unobserved url",
                url=invented,
            )
            tool_gateway(state, tool_gateway_service=gateway)
        self.assertEqual(state.current_state, RuntimeState.ACTING)
        self.assertEqual(
            state.last_tool_result.error_code, ToolErrorCode.UNANCHORED_TARGET
        )

        # Simulate a step boundary produced elsewhere (a replan, for whatever reason).
        state.plan_steps.append(
            PlanStep(
                step_id="replan_step_1",
                objective="Collect sources",
                rationale="r",
                expected_outcome="o",
                status=PlanStepStatus.IN_PROGRESS,
            )
        )
        state.current_plan_step_index = 1

        # The fourth attempt, on the new step, is refused before the anchoring check
        # even runs: the target is exhausted at the run scope, and that state survives
        # the step change intact.
        state.current_state = RuntimeState.READY_TOOL_EXECUTION
        state.current_tool_request = ToolRequest(
            plan_step_id="replan_step_1",
            tool_name=ToolName.FETCH_URL_TEXT,
            reason="fetch the same unobserved url again",
            url=invented,
        )
        tool_gateway(state, tool_gateway_service=gateway)
        self.assertEqual(state.current_state, RuntimeState.ACTING)
        self.assertEqual(state.last_tool_result.error_code, ToolErrorCode.DUPLICATE_REQUEST)
        self.assertIn("will not be attempted again", state.last_tool_result.summary)

    def test_duplicate_fetch_request_is_blocked_before_reexecution(self) -> None:
        graph = _build_test_graph(
            planner_service=SingleStepPlanner(),
            actor_service=RepeatFetchActor(),
            policy_engine_service=StubPolicyEngine(),
        )
        result = graph.invoke(
            {"intake_result": _build_research_intake_result(explicit_url="https://example.org/source")},
            config={"configurable": {"thread_id": "layer2-duplicate-fetch-blocked"}},
        )

        task_state = result["task_state"]
        self.assertGreaterEqual(len(task_state.tool_results_history), 2)
        successful_results = [result for result in task_state.tool_results_history if result.success]
        failed_results = [result for result in task_state.tool_results_history if not result.success]

        self.assertGreaterEqual(len(successful_results), 1)
        self.assertGreaterEqual(len(failed_results), 1)
        self.assertTrue(
            all(
                sum(
                    1
                    for result in successful_results
                    if result.request.plan_step_id == plan_step_id
                )
                == 1
                for plan_step_id in {result.request.plan_step_id for result in successful_results}
            )
        )
        self.assertTrue(
            all(
                result.error_code == ToolErrorCode.DUPLICATE_REQUEST
                for result in failed_results
            )
        )


    # Adapter-produced observations remain separate and retain their provenance locators.
    def test_observe_tool_result_preserves_multiple_concrete_observations(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.observations = []
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Collect evidence.",
                rationale="The step needs grounded sources.",
                expected_outcome="Observed evidence.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.tool_call_count = 1
        task_state.last_tool_result = ToolExecutionResult(
            request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.WEB_SEARCH,
                reason="Collect sources.",
                query="official eu dma sources",
            ),
            success=True,
            summary="Web search executed successfully.",
            content="One global transcript covering several returned sources in aggregate.",
            observations=(
                ToolObservation(
                    observation_id="search-request:a",
                    locator="https://example.org/a",
                    content="Concrete content from source A.",
                ),
                ToolObservation(
                    observation_id="search-request:b",
                    locator="https://example.org/b",
                    content="Concrete content from source B.",
                ),
            ),
        )

        observed_state = observe_tool_result(task_state)

        self.assertEqual(len(observed_state.observations), 2)
        self.assertEqual(
            [item.locator for item in observed_state.observations],
            ["https://example.org/a", "https://example.org/b"],
        )
        self.assertTrue(all(item.content for item in observed_state.observations))


    # Conflicting adapter identifiers are normalized without losing source observations.
    def test_observe_tool_result_recovers_conflicting_observation_ids(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Collect evidence.",
                rationale="The step needs observed sources.",
                expected_outcome="Two distinct observations.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.last_tool_result = ToolExecutionResult(
            request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.WEB_SEARCH,
                reason="Collect two sources.",
                query="two sources",
            ),
            success=True,
            summary="Two source observations returned.",
            observations=(
                ToolObservation(
                    observation_id="request:source:1",
                    locator="https://example.org/a",
                    content="Observed content A.",
                ),
                ToolObservation(
                    observation_id="request:source:1",
                    locator="https://example.org/b",
                    content="Observed content B.",
                ),
            ),
        )

        observed_state = observe_tool_result(task_state)

        self.assertEqual(len(observed_state.observations), 2)
        self.assertEqual(
            len({item.observation_id for item in observed_state.observations}),
            2,
        )
        self.assertIn(":normalized:", observed_state.observations[1].observation_id)


    # URL deduplication happens before final enumeration so interleaving cannot collide.
    def test_openai_search_observation_ids_remain_unique_after_url_overwrite(self) -> None:
        annotations = [
            {"url": "https://example.org/a", "text": "A"},
            {"url": "https://example.org/b", "text": "B"},
            {"url": "https://example.org/a", "text": "A more informative excerpt"},
            {"url": "https://example.org/c", "text": "C"},
        ]
        response = SimpleNamespace(
            output=[
                SimpleNamespace(
                    content=[
                        SimpleNamespace(text="", annotations=annotations),
                    ]
                )
            ]
        )

        observations = extract_web_observations(response, request_id="request-1")

        self.assertEqual(len(observations), 3)
        self.assertEqual(
            [item.observation_id for item in observations],
            [
                "request-1:source:1",
                "request-1:source:2",
                "request-1:source:3",
            ],
        )
        self.assertEqual(observations[0].content, "A more informative excerpt")


    def test_observe_tool_result_marks_extractability_limit_on_empty_fetch(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.observations = []
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Collect evidence from an official source.",
                rationale="The step needs a concrete official source.",
                expected_outcome="Observed evidence.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.tool_call_count = 1
        task_state.last_tool_result = ToolExecutionResult(
            request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.FETCH_URL_TEXT,
                reason="Fetch official text.",
                url="https://eur-lex.europa.eu/legal-content/en/TXT/?uri=CELEX:32022R1925",
            ),
            success=False,
            summary="URL fetch succeeded but extracted readable text was empty.",
            content=None,
            error_message="URL fetch succeeded but extracted readable text was empty.",
            observations=(),
        )

        observed_state = observe_tool_result(task_state)

        self.assertIn(RuntimeFlag.EXTRACTABILITY_LIMITED, observed_state.runtime_flags)
        self.assertEqual(observed_state.observations, [])


    # Runtime never invents an observation from aggregate metadata omitted by the adapter.
    def test_observe_tool_result_does_not_synthesize_missing_observations(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.observations = []
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Collect official DMA sources.",
                rationale="The step needs a grounded official overview.",
                expected_outcome="Observed evidence.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.tool_call_count = 1
        task_state.last_tool_result = ToolExecutionResult(
            request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.WEB_SEARCH,
                reason="Find the Commission DMA overview.",
                query="Digital Markets Act European Commission official page",
            ),
            success=True,
            summary="Web search executed successfully.",
            content=(
                "Here are the European Commission official pages for the Digital Markets Act (DMA): "
                "DMA official website and DMA legislation hub, with overview statements about the "
                "gatekeepers, core platform services, obligations, and purpose of the DMA."
            ),
            observations=(),
        )

        observed_state = observe_tool_result(task_state)

        self.assertEqual(observed_state.observations, [])


    # Concrete adapter content is retained exactly without semantic classification.
    def test_observe_tool_result_retains_concrete_timeline_content(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.observations = []
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Collect official timeline evidence.",
                rationale="The step needs an official source.",
                expected_outcome="A source shortlist.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.tool_call_count = 1
        task_state.last_tool_result = ToolExecutionResult(
            request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.WEB_SEARCH,
                reason="Find the official timeline.",
                query="official DMA timeline",
            ),
            success=True,
            summary="Web search executed successfully.",
            observations=(
                ToolObservation(
                    observation_id="timeline:1",
                    locator="https://example.org/dma",
                    content="The regulation was adopted on 14 September 2022 and entered into force later.",
                ),
            ),
        )

        observed_state = observe_tool_result(task_state)

        self.assertIn("14 September 2022", observed_state.observations[0].content)


if __name__ == "__main__":
    unittest.main()
