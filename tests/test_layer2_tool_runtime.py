from __future__ import annotations

from tests.layer2_runtime_fixtures import *
from layer2_runtime_governance.tool_contract import ToolErrorCode


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
        self.assertTrue(
            any(item.source_kind == EvidenceSourceKind.TOOL_OBSERVATION for item in task_state.observed_evidence)
        )


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


    # Snippetless sources remain individually traceable discovery evidence.
    def test_observe_tool_result_preserves_multi_source_metadata_without_snippets(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.observed_evidence = []
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
            sources=(
                ToolSource(
                    title="Source A",
                    locator="https://example.org/a",
                    snippet=None,
                ),
                ToolSource(
                    title="Source B",
                    locator="https://example.org/b",
                    snippet=None,
                ),
            ),
        )

        observed_state = observe_tool_result(task_state)

        self.assertEqual(len(observed_state.observed_evidence), 2)
        self.assertEqual(
            [item.locator for item in observed_state.observed_evidence],
            ["https://example.org/a", "https://example.org/b"],
        )
        self.assertTrue(
            all(
                item.content_state == EvidenceContentState.METADATA_ONLY
                for item in observed_state.observed_evidence
            )
        )


    def test_observe_tool_result_marks_extractability_limit_on_empty_fetch(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.observed_evidence = []
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
            sources=(),
        )

        observed_state = observe_tool_result(task_state)

        self.assertIn(RuntimeFlag.EXTRACTABILITY_LIMITED, observed_state.runtime_flags)
        self.assertEqual(observed_state.observed_evidence, [])


    # Aggregated search content must not upgrade a snippetless source.
    def test_observe_tool_result_keeps_snippetless_web_source_at_source_discovery(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.observed_evidence = []
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
            sources=(
                ToolSource(
                    title="web_search",
                    locator="Digital Markets Act European Commission official page",
                    snippet=None,
                ),
            ),
        )

        observed_state = observe_tool_result(task_state)

        self.assertEqual(
            observed_state.observed_evidence[0].content_state,
            EvidenceContentState.METADATA_ONLY,
        )


    # A direct source-level timeline snippet can retain detailed support.
    def test_observe_tool_result_allows_detailed_support_for_source_level_timeline_snippet(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.observed_evidence = []
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
            sources=(
                ToolSource(
                    title="Official DMA overview",
                    locator="https://example.org/dma",
                    snippet="The regulation was adopted on 14 September 2022 and entered into force later.",
                    content_state=EvidenceContentState.EXTRACTED_FULL,
                    source_authority=SourceAuthority.OFFICIAL_PRIMARY,
                    extractability=Extractability.COMPLETE,
                    citation_alignment=CitationAlignment.EXACT,
                ),
            ),
        )

        observed_state = observe_tool_result(task_state)

        self.assertEqual(
            observed_state.observed_evidence[0].content_state,
            EvidenceContentState.EXTRACTED_FULL,
        )


if __name__ == "__main__":
    unittest.main()
