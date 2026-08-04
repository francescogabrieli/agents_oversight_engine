from __future__ import annotations

from types import SimpleNamespace

from tests.layer2_runtime_fixtures import *
from layer3_tool_bus_external_capabilities.tool_contract import ToolErrorCode
from layer3_tool_bus_external_capabilities.tools.adapters.openai_search import (
    extract_web_observations,
)


class Layer2ToolRuntimeTests(unittest.TestCase):
    # Scenarios remain grouped by the capability whose behavior they verify.
    def test_complete_intake_supports_unbounded_tool_budget(self) -> None:
        graph = _build_test_graph()
        result = graph.invoke(
            {"intake_result": _build_research_intake_result(max_tool_calls=None)},
            config={"configurable": {"thread_id": "layer2-unbounded-tool-budget"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.tool_call_count, 1)
        self.assertEqual(len(task_state.observations), 1)


    def test_duplicate_fetch_request_is_blocked_before_reexecution(self) -> None:
        graph = _build_test_graph(
            planner_service=SingleStepPlanner(),
            actor_service=RepeatFetchActor(),
            policy_engine_service=StubPolicyEngine(),
        )
        result = graph.invoke(
            {"intake_result": _build_research_intake_result(max_steps=1, max_tool_calls=10)},
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
