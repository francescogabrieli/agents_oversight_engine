from __future__ import annotations

import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

from layer1_intake import (
    ExecutionPolicy,
    IntakeMetadata,
    SensitivityLevel,
    TaskSpec,
)
from layer2_runtime_governance.actor_contract import ActorPlanStep, ActorTask
from layer2_runtime_governance.domain import (
    EvidenceBurden,
    PlanStepStatus,
    RuntimeState,
)
from layer2_runtime_governance.execution.action_schemas import (
    build_actor_runtime_tools,
    build_json_actor_output_shape,
    select_actor_runtime_tools_for_turn,
)
from layer2_runtime_governance.execution.errors import ActorExecutionError
from layer2_runtime_governance.execution.response_parser import convert_json_payload_to_tool_call
from layer3_tool_bus_external_capabilities.tool_contract import ToolObservation
from layer3_tool_bus_external_capabilities.tools import load_tool_gateway_config


class ActorActionSchemaTests(unittest.TestCase):
    # External synthesis must use the typed communication envelope, not generic text output.
    def test_external_synthesis_exposes_only_typed_completion_action(self) -> None:
        actions = select_actor_runtime_tools_for_turn(
            build_actor_runtime_tools(),
            task_spec=TaskSpec(
                goal="Prepare an email.",
                context="Use deterministic facts.",
                constraints=[],
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Prepare the draft."],
            ),
        )
        names = {str(action["name"]) for action in actions}

        self.assertIn("emit_external_communication_draft", names)
        self.assertIn("emit_step_completed", names)
        self.assertNotIn("emit_final_output", names)

    # Evidence and non-communication steps must not emit a communication prematurely.
    def test_generic_actor_turn_exposes_typed_outcome_actions(self) -> None:
        actions = select_actor_runtime_tools_for_turn(
            build_actor_runtime_tools(),
            task_spec=TaskSpec(
                goal="Collect evidence.",
                context="Use deterministic facts.",
                constraints=[],
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Collect evidence."],
            ),
        )
        names = {str(action["name"]) for action in actions}

        self.assertIn("emit_external_communication_draft", names)
        self.assertIn("emit_step_completed", names)

    # Runtime actions must remain unique, closed, and provider-neutral.
    def test_runtime_action_schemas_are_unique_closed_functions(self) -> None:
        actions = build_actor_runtime_tools()
        names = [str(action["name"]) for action in actions]

        self.assertEqual(len(names), len(set(names)))
        self.assertIn("request_web_search", names)
        self.assertNotIn("emit_final_output", names)
        self.assertIn("emit_external_communication_draft", names)
        self.assertIn("emit_needs_replan", names)
        for action in actions:
            self.assertEqual(action["type"], "function")
            parameters = action["parameters"]
            self.assertEqual(parameters["type"], "object")
            self.assertFalse(parameters["additionalProperties"])
            self.assertTrue(set(parameters["required"]).issubset(parameters["properties"]))

    # JSON-only providers must receive exactly the same action vocabulary.
    def test_json_output_shape_lists_all_runtime_actions(self) -> None:
        actions = build_actor_runtime_tools()
        output_shape = json.loads(build_json_actor_output_shape())

        self.assertEqual(
            output_shape["allowed_tool_names"],
            [action["name"] for action in actions],
        )

    # Production action binding exposes the configured read-only capabilities, never test MCP tools.
    def test_production_registry_exposes_only_configured_capabilities(self) -> None:
        config = load_tool_gateway_config(
            config_path=REPO_ROOT / "configs" / "layer2_tool_gateway.json"
        )
        names = [str(action["name"]) for action in build_actor_runtime_tools(config)]

        self.assertEqual(
            names[:4],
            [
                "request_web_search",
                "request_fetch_url_text",
                "request_controlled_document",
                "request_controlled_dataset",
            ],
        )
        self.assertFalse(any(name.startswith("request_mcp_") for name in names))
        self.assertNotIn("emit_final_output", names)
        self.assertIn("emit_needs_replan", names)

    # The explicit MCP test registry enables only its allowlisted local capabilities.
    def test_mcp_test_registry_exposes_allowlisted_mcp_capabilities(self) -> None:
        config = load_tool_gateway_config(
            config_path=REPO_ROOT / "configs" / "layer2_tool_gateway.test.json"
        )
        names = [str(action["name"]) for action in build_actor_runtime_tools(config)]

        self.assertEqual(
            names[:4],
            [
                "request_mcp_echo",
                "request_mcp_read_fixture",
                "request_mcp_text_stats",
                "request_mcp_calculate_expression",
            ],
        )
        self.assertIn("emit_blocked", names)
        self.assertIn("emit_ask_clarification", names)

    # Provider payload validation must reject capabilities absent from the active catalog.
    def test_inactive_mcp_action_is_rejected_before_tool_request(self) -> None:
        config = load_tool_gateway_config(
            config_path=REPO_ROOT / "configs" / "layer2_tool_gateway.json"
        )
        allowed_names = {
            str(action["name"]) for action in build_actor_runtime_tools(config)
        }

        with self.assertRaises(ActorExecutionError):
            convert_json_payload_to_tool_call(
                {
                    "tool_name": "request_mcp_echo",
                    "arguments": {
                        "summary": "Attempt inactive MCP capability.",
                        "reason": "Test rejection.",
                        "text": "hello",
                    },
                },
                allowed_action_names=allowed_names,
            )

    @staticmethod
    def _build_test_actor_task(
        *,
        evidence_burden: EvidenceBurden | None = None,
        observations: tuple[ToolObservation, ...] = (),
        evidence_request_episode_count: int = 0,
    ) -> ActorTask:
        task_spec = TaskSpec(
            goal="Test goal",
            context="Test context",
            constraints=[],
            sensitivity=SensitivityLevel.LOW,
            success_criteria=["Test criteria"],
        )
        plan_step = ActorPlanStep(
            step_id="step_1",
            objective="Collect evidence and synthesize.",
            rationale="Test rationale",
            expected_outcome="Test outcome",
            status=PlanStepStatus.IN_PROGRESS,
            produces_factual_claims=evidence_burden is not None,
            evidence_burden=evidence_burden,
        )
        return ActorTask(
            task_spec=task_spec,
            execution_policy=ExecutionPolicy(confidence_threshold=0.8),
            intake_metadata=IntakeMetadata(),
            current_state=RuntimeState.ACTING,
            runtime_flags=(),
            plan_steps=(plan_step,),
            past_steps=(),
            observations=observations,
            scratchpad=(),
            step_validations=(),
            last_tool_result=None,
            tool_call_count=0,
            actor_turn_count=1,
            current_plan_step_index=0,
            current_plan_step=plan_step,
            evidence_request_episode_count=evidence_request_episode_count,
        )

    # When evidence burden is unsatisfied, completion actions must be stripped from tools.
    def test_evidence_burden_gate_suppresses_completion_when_unsatisfied(self) -> None:
        task = self._build_test_actor_task(
            evidence_burden=EvidenceBurden(min_independent_sources=3),
            observations=(
                ToolObservation(observation_id="obs_1", content="OpenAI announcement", locator="https://openai.com/blog/1"),
                ToolObservation(observation_id="obs_2", content="Google announcement", locator="https://google.com/blog/2"),
            ),
        )
        actions = select_actor_runtime_tools_for_turn(
            build_actor_runtime_tools(),
            task_spec=task.task_spec,
            actor_task=task,
        )
        names = {str(action["name"]) for action in actions}
        self.assertNotIn("emit_step_completed", names)
        self.assertNotIn("emit_external_communication_draft", names)
        self.assertNotIn("emit_database_statement", names)
        self.assertIn("request_web_search", names)
        self.assertIn("request_fetch_url_text", names)

    # When enough independent sources with retrieved content are present, completion is enabled.
    def test_evidence_burden_gate_allows_completion_when_satisfied(self) -> None:
        task = self._build_test_actor_task(
            evidence_burden=EvidenceBurden(min_independent_sources=3),
            observations=(
                ToolObservation(observation_id="obs_1", content="OpenAI announcement", locator="https://openai.com/blog/1"),
                ToolObservation(observation_id="obs_2", content="Google announcement", locator="https://google.com/blog/2"),
                ToolObservation(observation_id="obs_3", content="Anthropic announcement", locator="https://anthropic.com/blog/3"),
            ),
        )
        actions = select_actor_runtime_tools_for_turn(
            build_actor_runtime_tools(),
            task_spec=task.task_spec,
            actor_task=task,
        )
        names = {str(action["name"]) for action in actions}
        self.assertIn("emit_step_completed", names)
        self.assertIn("emit_external_communication_draft", names)

    # Search result title stubs alone do not satisfy retrieved content requirements.
    def test_evidence_burden_gate_ignores_search_title_stubs(self) -> None:
        task = self._build_test_actor_task(
            evidence_burden=EvidenceBurden(min_independent_sources=2),
            observations=(
                ToolObservation(observation_id="obs_1", content="OpenAI real content", locator="https://openai.com/blog/1"),
                ToolObservation(observation_id="obs_2", content="Search result title: Google", locator="https://google.com/search_stub"),
            ),
        )
        actions = select_actor_runtime_tools_for_turn(
            build_actor_runtime_tools(),
            task_spec=task.task_spec,
            actor_task=task,
        )
        names = {str(action["name"]) for action in actions}
        # Only 1 retrieved content origin (openai.com), so 2 min sources is unsatisfied
        self.assertNotIn("emit_step_completed", names)
        self.assertNotIn("emit_external_communication_draft", names)

    # Safety valve: after reaching the threshold of evidence request retries, completion is re-enabled.
    def test_evidence_burden_gate_safety_valve_allows_completion_after_retries(self) -> None:
        task = self._build_test_actor_task(
            evidence_burden=EvidenceBurden(min_independent_sources=3),
            observations=(
                ToolObservation(observation_id="obs_1", content="OpenAI announcement", locator="https://openai.com/blog/1"),
            ),
            evidence_request_episode_count=3,
        )
        actions = select_actor_runtime_tools_for_turn(
            build_actor_runtime_tools(),
            task_spec=task.task_spec,
            actor_task=task,
        )
        names = {str(action["name"]) for action in actions}
        self.assertIn("emit_step_completed", names)
        self.assertIn("emit_external_communication_draft", names)


if __name__ == "__main__":
    unittest.main()
