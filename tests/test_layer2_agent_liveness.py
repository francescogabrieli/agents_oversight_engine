"""Characterization tests for the bounded agent progress loop."""

import unittest
from unittest.mock import patch

from tests.layer2_runtime_fixtures import _build_research_intake_result

from layer2_runtime_governance.actor_contract import (
    ActorResult,
    ActorResultType,
    CapabilityAccessMode,
    build_actor_task,
)
from layer2_runtime_governance.domain import (
    PlanStep,
    PlanStepStatus,
    RuntimeState,
    TaskState,
    ToolAttempt,
    ToolDisposition,
    ToolInvocation,
)
from layer2_runtime_governance.runtime.actor_service import _enforce_actor_runtime_contract, _record_actor_execution
from layer2_runtime_governance.runtime.tool_service import tool_gateway
from layer2_runtime_governance.execution.prompt_projection import build_actor_prompt_payload
from layer2_runtime_governance.domain.serialization import normalize_task_state_serialization
from layer3_tool_bus_external_capabilities.external_operations.contracts import GovernanceActionFeedback
from layer2_runtime_governance.planning import build_planner_system_prompt
from layer2_runtime_governance.domain import ExecutedPlanStep
from layer3_tool_bus_external_capabilities.tool_contract import (
    ToolErrorCode,
    ToolExecutionResult,
    ToolName,
    ToolRequest,
    canonical_tool_request_identity,
)
from main_layer2 import parse_cli_args
from layer2_runtime_governance.runtime.routing import route_after_actor
from layer2_runtime_governance.execution.action_schemas import build_actor_runtime_tools, select_actor_runtime_tools_for_turn
from layer2_runtime_governance.execution.providers.bedrock_runtime import _extract_tool_call_payloads
from layer2_runtime_governance.runtime.tool_service import observe_tool_result
from layer2_runtime_governance.runtime.routing import route_after_observe_tool_result
from layer2_runtime_governance.runtime.context import copy_task_state
class _Gateway:
    # A deterministic capability adapter used to isolate ProgressGuard behavior.
    gateway_name = "test"

    # Return the configured backend result and count only actual backend calls.
    def __init__(self, result: ToolExecutionResult) -> None:
        self.result = result
        self.calls = 0

    # Execute the fake backend without network access.
    def execute(self, *, tool_request: ToolRequest) -> ToolExecutionResult:
        self.calls += 1
        return ToolExecutionResult(
            request=tool_request, success=self.result.success, summary=self.result.summary,
            observations=self.result.observations, error_code=self.result.error_code,
            error_message=self.result.error_message,
        )


class Layer2AgentLivenessTests(unittest.TestCase):
    # Build one minimal tool-ready state for local guard tests.
    def _tool_state(self, request: ToolRequest, *, anchor_url: bool = True) -> TaskState:
        explicit_url = (
            request.url
            if anchor_url and request.tool_name == ToolName.FETCH_URL_TEXT
            else None
        )
        state = TaskState.from_intake(
            _build_research_intake_result(explicit_url=explicit_url)
        )
        state.plan_steps = [PlanStep("step_1", "Collect material.", "Needed.", "Material.", PlanStepStatus.IN_PROGRESS)]
        state.current_plan_step_index = 0
        state.current_state = RuntimeState.READY_TOOL_EXECUTION
        state.current_tool_request = request
        return state
    # A legacy narrative must not create an unbounded actor-to-actor edge.
    def test_legacy_intermediate_output_is_converted_to_finite_recovery(self) -> None:
        state = TaskState.from_intake(_build_research_intake_result())
        state.plan_steps = [PlanStep("step_1", "Produce a result.", "Needed.", "A result.", PlanStepStatus.IN_PROGRESS)]
        state.current_plan_step_index = 0
        result = ActorResult(
            plan_step_id="step_1", result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary="Thinking.", output_text="", capability_mode=CapabilityAccessMode.NONE,
        )

        enforced = _enforce_actor_runtime_contract(state, actor_result=result)

        self.assertEqual(enforced.result_type, ActorResultType.NEEDS_REPLAN)

    # The graph has no narration-only actor-to-actor control-flow branch.
    def test_actor_routing_always_reaches_governance_boundary(self) -> None:
        state = TaskState.from_intake(_build_research_intake_result())
        self.assertEqual(route_after_actor({"task_state": state}), "governance")

    # The technical fuse turns repeated actor turns without state delta into replan.
    def test_no_progress_fuse_requests_replan(self) -> None:
        state = TaskState.from_intake(_build_research_intake_result())
        state.plan_steps = [PlanStep("step_1", "Produce.", "Needed.", "Result.", PlanStepStatus.IN_PROGRESS)]
        state.current_plan_step_index = 0
        state.no_progress_turns = 4
        result = ActorResult("step_1", ActorResultType.BLOCKED, "blocked", blocking_reason="temporary")
        self.assertEqual(_enforce_actor_runtime_contract(state, actor_result=result).result_type, ActorResultType.NEEDS_REPLAN)

    # The turn-local capability surface avoids immediately repeating the observed capability.
    def test_capability_selection_hides_last_observed_capability(self) -> None:
        state = TaskState.from_intake(_build_research_intake_result())
        state.plan_steps = [PlanStep("step_1", "Collect.", "Needed.", "Material.", PlanStepStatus.IN_PROGRESS)]
        state.current_plan_step_index = 0
        request = ToolRequest("step_1", ToolName.WEB_SEARCH, "Find.", query="one")
        state.last_tool_result = ToolExecutionResult(request, True, "ok")
        tools = select_actor_runtime_tools_for_turn(build_actor_runtime_tools(), task_spec=state.task_spec, actor_task=build_actor_task(task_state=state))
        self.assertNotIn("request_web_search", {tool["name"] for tool in tools})
        self.assertIn("emit_step_completed", {tool["name"] for tool in tools})

    # Narrative reason wording is not part of the capability-owned action key.
    def test_request_paraphrase_does_not_bypass_canonical_deduplication(self) -> None:
        first = ToolRequest("step_1", ToolName.WEB_SEARCH, "Find material.", query="A   stable query", max_results=3)
        second = ToolRequest("step_1", ToolName.WEB_SEARCH, "Please locate the same material differently.", query="a stable query", max_results=3)
        self.assertEqual(
            canonical_tool_request_identity(first),
            canonical_tool_request_identity(second),
        )

    # Equivalent success is reused rather than sent again to the backend.
    def test_identical_success_is_a_cache_hit_without_second_invocation(self) -> None:
        request = ToolRequest("step_1", ToolName.WEB_SEARCH, "Find material.", query="stable")
        gateway = _Gateway(ToolExecutionResult(request, True, "ok"))
        state = tool_gateway(self._tool_state(request), tool_gateway_service=gateway)
        state.current_state = RuntimeState.READY_TOOL_EXECUTION
        state.current_tool_request = ToolRequest("step_1", ToolName.WEB_SEARCH, "Same request.", query="stable")
        state = tool_gateway(state, tool_gateway_service=gateway)
        self.assertEqual(gateway.calls, 1)
        self.assertEqual(state.tool_attempts[-1].disposition.value, "cache_hit")
        self.assertEqual(state.tool_call_count, 1)

    # Synthetic rejections cannot inflate invocation metrics or circuit state.
    def test_duplicate_and_circuit_do_not_increment_backend_invocations(self) -> None:
        request = ToolRequest("step_1", ToolName.FETCH_URL_TEXT, "Fetch.", url="https://example.test/a")
        terminal = _Gateway(ToolExecutionResult(request, False, "forbidden", error_code=ToolErrorCode.FORBIDDEN))
        state = tool_gateway(self._tool_state(request), tool_gateway_service=terminal)
        state.current_state = RuntimeState.READY_TOOL_EXECUTION
        state.current_tool_request = ToolRequest("step_1", ToolName.FETCH_URL_TEXT, "Again.", url="https://example.test/a")
        state = tool_gateway(state, tool_gateway_service=terminal)
        self.assertEqual(terminal.calls, 1)
        self.assertEqual(state.tool_call_count, 1)
        self.assertEqual(state.tool_invocations[0].retryable_failure, False)

    # Fetch targets must be anchored by an observed locator or an explicit task URL.
    def test_unanchored_fetch_is_rejected_before_backend_invocation(self) -> None:
        request = ToolRequest(
            "step_1",
            ToolName.FETCH_URL_TEXT,
            "Fetch an invented target.",
            url="https://invented.example/404",
        )
        gateway = _Gateway(ToolExecutionResult(request, True, "backend must not run"))

        result = tool_gateway(
            self._tool_state(request, anchor_url=False),
            tool_gateway_service=gateway,
        )

        self.assertEqual(gateway.calls, 0)
        self.assertEqual(result.tool_call_count, 0)
        self.assertEqual(result.last_tool_result.error_code, ToolErrorCode.UNANCHORED_TARGET)

    # A target-not-found result is terminal across progress epochs.
    def test_target_not_found_is_remembered_across_progress_epoch(self) -> None:
        request = ToolRequest(
            "step_1",
            ToolName.FETCH_URL_TEXT,
            "Fetch the explicit target.",
            url="https://example.test/missing",
        )
        gateway = _Gateway(
            ToolExecutionResult(
                request,
                False,
                "not found",
                error_code=ToolErrorCode.TARGET_NOT_FOUND,
            )
        )

        state = tool_gateway(self._tool_state(request), tool_gateway_service=gateway)
        state.current_state = RuntimeState.READY_TOOL_EXECUTION
        state.current_tool_request = request
        state.progress_epoch += 1
        state = tool_gateway(state, tool_gateway_service=gateway)

        self.assertEqual(gateway.calls, 1)
        self.assertEqual(state.last_tool_result.error_code, ToolErrorCode.DUPLICATE_REQUEST)

    # A legacy checkpoint with nested dicts must be normalized before Tool Gateway guards inspect it.
    def test_legacy_checkpoint_tool_history_is_normalized_at_runtime_boundary(self) -> None:
        request = ToolRequest(
            "step_1",
            ToolName.FETCH_URL_TEXT,
            "Fetch again.",
            url="https://example.test/a",
        )
        state = self._tool_state(request)
        action_key = canonical_tool_request_identity(request)
        state.tool_attempts = [
            {
                "action_key": action_key,
                "plan_step_id": "step_1",
                "progress_epoch": 0,
                "disposition": ToolDisposition.EXECUTED_TERMINAL_FAILURE.value,
            }
        ]
        state.tool_invocations = [
            {
                "action_key": action_key,
                "plan_step_id": "step_1",
                "progress_epoch": 0,
                "retryable_failure": False,
            }
        ]

        resumed_state = copy_task_state(state)
        self.assertIsInstance(resumed_state.tool_attempts[0], ToolAttempt)
        self.assertIsInstance(resumed_state.tool_invocations[0], ToolInvocation)

        gateway = _Gateway(
            ToolExecutionResult(
                request,
                False,
                "forbidden",
                error_code=ToolErrorCode.FORBIDDEN,
            )
        )
        result = tool_gateway(resumed_state, tool_gateway_service=gateway)

        self.assertEqual(gateway.calls, 0)
        self.assertEqual(result.last_tool_result.error_code, ToolErrorCode.DUPLICATE_REQUEST)

    # Retryable failures have a finite local retry path before semantic replanning.
    def test_same_epoch_retryable_repetition_requests_replan(self) -> None:
        request = ToolRequest("step_1", ToolName.WEB_SEARCH, "Find.", query="retry")
        gateway = _Gateway(ToolExecutionResult(request, False, "timeout", error_code=ToolErrorCode.TIMEOUT))
        state = tool_gateway(self._tool_state(request), tool_gateway_service=gateway)
        state.current_state = RuntimeState.READY_TOOL_EXECUTION
        state.current_tool_request = ToolRequest("step_1", ToolName.WEB_SEARCH, "Retry.", query="retry")
        state = tool_gateway(state, tool_gateway_service=gateway)
        self.assertEqual(state.current_state, RuntimeState.REPLANNING)
        self.assertEqual(state.actor_result.result_type, ActorResultType.NEEDS_REPLAN)

    # A durable artifact is the canonical local progress signal.
    def test_step_completion_artifact_advances_progress_epoch(self) -> None:
        state = TaskState.from_intake(_build_research_intake_result())
        state.plan_steps = [PlanStep("step_1", "Produce.", "Needed.", "Result.", PlanStepStatus.IN_PROGRESS)]
        state.current_plan_step_index = 0
        result = ActorResult("step_1", ActorResultType.STEP_COMPLETED, "done", output_text="durable output")
        _record_actor_execution(state, actor_result=result)
        self.assertEqual(state.progress_epoch, 0)
        self.assertEqual(state.pending_completion_candidate.output_text, "durable output")
        self.assertEqual(state.workspace.artifacts, [])

    # Actor context includes complete reusable outputs, not just their summaries.
    def test_prior_outputs_and_governance_feedback_are_projected(self) -> None:
        state = TaskState.from_intake(_build_research_intake_result())
        state.plan_steps = [PlanStep("step_1", "First.", "Needed.", "One.", PlanStepStatus.COMPLETED), PlanStep("step_2", "Second.", "Needed.", "Two.", PlanStepStatus.IN_PROGRESS)]
        state.current_plan_step_index = 1
        state.executed_steps = [ExecutedPlanStep("step_1", "First.", ActorResultType.STEP_COMPLETED, "summary", "complete prior output")]
        state.governance_action_feedback = [GovernanceActionFeedback("op", "digest", ("finding",), "full feedback", 1)]
        payload = build_actor_prompt_payload(actor_task=build_actor_task(task_state=state))
        self.assertEqual(payload["past_steps"][0]["output_text"], "complete prior output")
        self.assertEqual(payload["governance_feedback"][0]["summary"], "full feedback")

    # Planner contract explicitly rejects administrative/tool-level decomposition.
    def test_planner_prompt_forbids_meta_steps_and_tool_calls(self) -> None:
        prompt = build_planner_system_prompt()
        self.assertIn("Do not emit executable tool calls", prompt)
        self.assertIn("Do not include human review as a normal plan step", prompt)

    # New fields have defaults, so an existing canonical state remains serializable.
    def test_checkpoint_normalization_keeps_legacy_state_compatible(self) -> None:
        state = TaskState.from_intake(_build_research_intake_result())
        del state.workspace
        del state.progress_epoch
        del state.state_revision
        del state.no_progress_turns
        del state.tool_attempts
        del state.tool_invocations
        normalized = normalize_task_state_serialization(state)
        self.assertEqual(normalized.progress_epoch, 0)
        self.assertEqual(normalized.workspace.artifacts, [])
        self.assertEqual(normalized.pending_tool_requests, [])

    # Bedrock preserves all valid toolUse blocks in their original provider order.
    def test_bedrock_tool_use_batch_preserves_order(self) -> None:
        payloads = _extract_tool_call_payloads({"output": {"message": {"content": [
            {"toolUse": {"name": "request_web_search", "input": {"summary": "one"}}},
            {"toolUse": {"name": "request_fetch_url_text", "input": {"summary": "two"}}},
            {"toolUse": {"name": "request_mcp_echo", "input": {"summary": "three"}}},
        ]}}})
        self.assertEqual(tuple(item["tool_name"] for item in payloads), (
            "request_web_search", "request_fetch_url_text", "request_mcp_echo",
        ))

    # The actor materializes only the first request while retaining ordered successors in state.
    def test_actor_batch_populates_persisted_queue(self) -> None:
        state = TaskState.from_intake(_build_research_intake_result())
        state.current_state = RuntimeState.ACTING
        state.plan_steps = [PlanStep("step_1", "Collect.", "Needed.", "Material.", PlanStepStatus.IN_PROGRESS)]
        state.current_plan_step_index = 0
        requests = (
            ToolRequest("step_1", ToolName.WEB_SEARCH, "One.", query="one"),
            ToolRequest("step_1", ToolName.WEB_SEARCH, "Two.", query="two"),
            ToolRequest("step_1", ToolName.WEB_SEARCH, "Three.", query="three"),
        )
        result = ActorResult("step_1", ActorResultType.PROPOSE_ACTION, "Batch.", tool_request=requests[0], tool_requests=requests)
        _record_actor_execution(state, actor_result=result)
        state.current_tool_request = requests[0]
        state.pending_tool_requests = list(requests[1:])
        state.last_tool_result = ToolExecutionResult(requests[0], True, "ok")
        observed = observe_tool_result(state)
        self.assertEqual(observed.current_tool_request.query, "two")
        self.assertEqual([item.query for item in observed.pending_tool_requests], ["three"])
        self.assertEqual(route_after_observe_tool_result({"task_state": observed}), "governance")

    # CLI context is opt-in and does not inject a hidden task instruction.
    def test_cli_context_defaults_to_empty(self) -> None:
        with patch("sys.argv", ["main_layer2.py"]):
            self.assertIsNone(parse_cli_args().context)


if __name__ == "__main__":
    unittest.main()
