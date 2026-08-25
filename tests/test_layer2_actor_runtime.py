from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from tests.layer2_runtime_fixtures import (
    OpenRouterActor,
    OpenRouterActorSettings,
    _build_conservative_replan_summary_task_state,
    _build_external_communication_intake_result_for_governance,
    _build_external_incident_draft_task_state,
    _build_high_precision_research_task_state,
    _build_policy_input_task_state,
    _build_research_intake_result,
    _build_source_shortlist_task_state,
    _build_summary_task_state_with_generic_timeline,
    _build_summary_task_state_with_year_in_citation,
    _FakeOpenRouterClient,
    _FakeOpenRouterCompletionWithInternalRefsClient,
    _FakeOpenRouterCompletionWithoutRationaleClient,
    _FakeOpenRouterConservativeSummaryAfterExtractabilityLimitClient,
    _FakeOpenRouterEmptyResponseClient,
    _FakeOpenRouterFalseNoToolBlockedClient,
    _FakeOpenRouterIntakeSupportedExternalDraftClient,
    _FakeOpenRouterJsonWithoutEnvelopeClient,
    _FakeOpenRouterProceduralImplementationSummaryClient,
    _FakeOpenRouterRecoveredHighLevelStepOneCompletionClient,
    _FakeOpenRouterSearchWithoutQueryClient,
    _FakeOpenRouterSourceShortlistCompletionClient,
    _FakeOpenRouterSummaryWithGenericTimelineClient,
    _FakeOpenRouterSummaryWithRegulationYearClient,
    _FakeOpenRouterUnsupportedPrecisionCompletionClient,
)

import layer2_runtime_governance.execution.providers.bedrock_runtime as bedrock_client_module
from layer1_intake import ExecutionIntent
from layer2_runtime_governance.actor_contract import (
    ActorResult,
    ActorResultType,
    CapabilityAccessMode,
    build_actor_task,
)
from layer2_runtime_governance.domain import ExecutedPlanStep
from layer2_runtime_governance.domain import (
    PlanStep,
    PlanStepStatus,
    RuntimeState,
    ScratchpadEntry,
    ScratchpadEntryType,
    TaskState,
)
from layer2_runtime_governance.execution import (
    ActorExecutionError,
    BedrockNovaActor,
    build_actor_system_prompt,
    build_actor_user_prompt,
    build_default_actor,
)
from layer2_runtime_governance.runtime.actor_service import (
    _enforce_actor_runtime_contract,
)
from layer2_runtime_governance.runtime.lifecycle_service import finalize_bootstrap
from layer2_runtime_governance.runtime.tool_service import observe_tool_result
from layer3_tool_bus_external_capabilities.tool_contract import (
    ToolExecutionResult,
    ToolName,
    ToolObservation,
    ToolRequest,
)
class Layer2ActorExecutionTests(unittest.TestCase):
    # Scenarios remain grouped by the capability whose behavior they verify.
    def test_default_actor_can_be_configured_to_bedrock(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LAYER2_ACTOR_PROVIDER": "bedrock",
                "AWS_REGION": "eu-central-1",
                "AWS_ACCESS_KEY_ID": "test-key",
                "AWS_SECRET_ACCESS_KEY": "test-secret-key",
            },
            clear=True,
        ), patch(
            "layer2_runtime_governance.execution.providers.bedrock.BedrockChatClient"
        ):
            actor = build_default_actor()

        self.assertIsInstance(actor, BedrockNovaActor)


    def test_bedrock_settings_load_aws_profile_from_environment(self) -> None:
        with patch.dict(os.environ, {"AWS_PROFILE": "datareply-pua"}, clear=True), patch(
            "layer2_runtime_governance.execution.providers.bedrock_runtime._load_env_file_values",
            return_value={},
        ):
            settings = bedrock_client_module.BedrockRuntimeSettings.from_env()

        self.assertEqual(settings.profile_name, "datareply-pua")
        self.assertIsNone(settings.access_key_id)


    def test_openrouter_actor_converts_json_action_to_actor_result_without_network(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterClient()  # type: ignore[attr-defined]
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertEqual(result.output_text, "OpenRouter actor completed the step.")
        self.assertIsNotNone(result.step_validation)
        self.assertEqual(result.step_validation.actor_self_confidence, 0.82)
        self.assertEqual(result.step_validation.evidence_refs, ())


    def test_bedrock_client_retries_retryable_failures_and_omits_inference_config(self) -> None:
        client = bedrock_client_module.BedrockChatClient(
            bedrock_client_module.BedrockRuntimeSettings(
                access_key_id="test-key",
                secret_access_key="test-secret-key",
                read_timeout_seconds=1.0,
            )
        )

        class FlakyBedrockClient:
            # Return two transient failures before the expected typed Nova tool use.
            def __init__(self) -> None:
                self.call_count = 0
                self.last_kwargs: dict[str, object] | None = None

            def converse(self, **kwargs: object) -> dict[str, object]:
                self.call_count += 1
                self.last_kwargs = kwargs
                if self.call_count < 3:
                    raise bedrock_client_module.ClientError(
                        {
                            "Error": {"Code": "ThrottlingException", "Message": "rate limited"},
                            "ResponseMetadata": {"HTTPStatusCode": 429},
                        },
                        "Converse",
                    )
                return {
                    "output": {
                        "message": {
                            "content": [
                                {
                                    "toolUse": {
                                        "toolUseId": "test-tool-use",
                                        "name": "emit_step_completed",
                                        "input": {"output_text": "Recovered output."},
                                    }
                                }
                            ]
                        }
                    }
                }

        fake_client = FlakyBedrockClient()
        client._client = fake_client  # type: ignore[attr-defined]
        with patch("llm_retry.time.sleep", return_value=None):
            payload = client.complete_tool_call(
                system_prompt="system",
                user_prompt="user",
                tools=[
                    {
                        "type": "function",
                        "name": "emit_step_completed",
                        "description": "Return the final output envelope.",
                        "parameters": {
                            "type": "object",
                            "properties": {"output_text": {"type": "string"}},
                            "required": ["output_text"],
                            "additionalProperties": False,
                        },
                    }
                ],
            )

        self.assertEqual(payload["tool_name"], "emit_step_completed")
        self.assertEqual(fake_client.call_count, 3)
        self.assertIsNotNone(fake_client.last_kwargs)
        self.assertNotIn("inferenceConfig", fake_client.last_kwargs)
        self.assertNotIn("temperature", fake_client.last_kwargs)
        self.assertNotIn("maxTokens", fake_client.last_kwargs)
        tool_schema = fake_client.last_kwargs["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
        self.assertNotIn("additionalProperties", tool_schema)
        self.assertEqual(fake_client.last_kwargs["toolConfig"]["toolChoice"], {"any": {}})


    def test_openrouter_actor_web_search_without_query_uses_step_fallback(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterSearchWithoutQueryClient()  # type: ignore[attr-defined]
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.PROPOSE_ACTION)
        self.assertIsNotNone(result.tool_request)
        self.assertEqual(result.tool_request.tool_name, ToolName.WEB_SEARCH)
        self.assertIn("Collect the minimum official evidence", result.tool_request.query)


    def test_openrouter_actor_completion_without_rationale_uses_summary(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterCompletionWithoutRationaleClient()  # type: ignore[attr-defined]
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertEqual(result.summary, "Grounded concise final answer.")
        self.assertIsNotNone(result.step_validation)
        self.assertEqual(
            result.step_validation.rationale_summary,
            "Grounded concise final answer.",
        )
        self.assertIsNotNone(result.scratchpad_entry)
        self.assertEqual(
            result.scratchpad_entry.summary,
            "Grounded concise final answer.",
        )


    def test_bedrock_response_without_tool_use_is_recoverable_provider_failure(self) -> None:
        with self.assertRaisesRegex(
            bedrock_client_module.BedrockRuntimeError,
            "no tool use",
        ):
            bedrock_client_module._extract_tool_call_payload(  # type: ignore[attr-defined]
                {"output": {"message": {"content": [{"text": "Plain text response."}]}}}
            )


    def test_bedrock_allows_a_configured_model_identifier(self) -> None:
        bedrock_client_module._validate_settings(  # type: ignore[attr-defined]
            settings=bedrock_client_module.BedrockRuntimeSettings(
                model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
                access_key_id="test-key",
                secret_access_key="test-secret-key",
            )
        )


    def test_bedrock_rejects_an_empty_model_identifier(self) -> None:
        with self.assertRaisesRegex(
            bedrock_client_module.BedrockRuntimeError,
            "must be a non-empty",
        ):
            bedrock_client_module._validate_settings(  # type: ignore[attr-defined]
                settings=bedrock_client_module.BedrockRuntimeSettings(
                    model_id=" ",
                    access_key_id="test-key",
                    secret_access_key="test-secret-key",
                )
            )


    def test_bedrock_rejects_an_empty_region(self) -> None:
        with self.assertRaisesRegex(
            bedrock_client_module.BedrockRuntimeError,
            "AWS_REGION must be a non-empty",
        ):
            bedrock_client_module._validate_settings(  # type: ignore[attr-defined]
                settings=bedrock_client_module.BedrockRuntimeSettings(
                    region_name=" ",
                    access_key_id="test-key",
                    secret_access_key="test-secret-key",
                )
            )


    def test_openrouter_json_without_envelope_is_blocked_when_not_recoverable(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterJsonWithoutEnvelopeClient()  # type: ignore[attr-defined]
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.BLOCKED)
        self.assertIn("malformed JSON", result.summary)
        self.assertIsNotNone(result.blocking_reason)
        self.assertIn("without a tool_name", result.blocking_reason)


    def test_openrouter_recovered_high_level_completion_has_no_protocol_uncertainty(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterRecoveredHighLevelStepOneCompletionClient()  # type: ignore[attr-defined]
        state = _build_conservative_replan_summary_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertIsNotNone(result.step_validation)
        self.assertGreater(result.step_validation.actor_self_confidence, 0.0)
        self.assertEqual(result.step_validation.evidence_refs, ())
        self.assertIsNone(result.step_validation.residual_uncertainty)


    def test_actor_prompts_bias_toward_current_step_completion(self) -> None:
        state = _build_policy_input_task_state()
        state.current_plan_step.step_id = "replan_step_1"
        state.current_plan_step.objective = (
            "Use the already-fetched Commission page content to extract high-level supported themes."
        )
        state.observations.append(
            ToolObservation(
                observation_id="replan_step_1:tool:1:source:1",
                locator="https://digital-markets-act.ec.europa.eu/legislation_en",
                content="The DMA contains the main rules for the designation of gatekeepers and the implementation of obligations and prohibitions.",
            )
        )
        state.scratchpad.append(
            ScratchpadEntry(
                turn_index=1,
                plan_step_id="replan_step_1",
                entry_type=ScratchpadEntryType.OBSERVATION,
                summary="Observed tool result from 'fetch_url_text': URL text fetched successfully.",
            )
        )
        actor_task = build_actor_task(task_state=state)

        system_prompt = build_actor_system_prompt()
        user_prompt = build_actor_user_prompt(actor_task=actor_task)

        self.assertIn("only step you may complete or commit", system_prompt)
        self.assertIn("Use the full plan as context", system_prompt)
        self.assertIn("landing/index page has no source timestamp", system_prompt)
        self.assertIn("never resubmit an unchanged payload, completion", system_prompt)
        self.assertIn('"observations":', user_prompt)
        self.assertNotIn("content_state", user_prompt)
        self.assertIn('"observation_id": "replan_step_1:tool:1:source:1"', user_prompt)


    # A requested external effect cannot disappear behind an ordinary terminal completion.
    def test_terminal_local_completion_becomes_replan_for_execution_task(self) -> None:
        state = TaskState.from_intake(
            _build_external_communication_intake_result_for_governance()
        )
        state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Produce and send the requested communication.",
                rationale="The user requested an external effect.",
                expected_outcome="A governed external communication candidate.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        state.current_plan_step_index = 0
        state.actor_turn_count = 1
        local_completion = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="The task is complete.",
            output_text="The message was prepared.",
        )

        normalized = _enforce_actor_runtime_contract(
            state,
            actor_result=local_completion,
        )

        self.assertEqual(normalized.result_type, ActorResultType.NEEDS_REPLAN)
        self.assertIn("no typed external-operation candidate", normalized.blocking_reason)


    # Multi-turn reasoning remains valid, while exact replay is a recoverable stuck protocol.
    def test_identical_intermediate_replay_becomes_replan(self) -> None:
        state = _build_policy_input_task_state()
        previous = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary="Derived one grounded comparison.",
            output_text="Comparison draft.",
            scratchpad_entry=ScratchpadEntry(
                turn_index=1,
                plan_step_id="step_1",
                entry_type=ScratchpadEntryType.PROGRESS_NOTE,
                summary="One grounded comparison is now available.",
            ),
        )
        state.actor_result = previous
        state.actor_turn_count = 2
        repeated = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary="  DERIVED one grounded comparison. ",
            output_text="Comparison   draft.",
            scratchpad_entry=ScratchpadEntry(
                turn_index=2,
                plan_step_id="step_1",
                entry_type=ScratchpadEntryType.PROGRESS_NOTE,
                summary="One grounded comparison is now available.",
            ),
        )

        normalized = _enforce_actor_runtime_contract(
            state,
            actor_result=repeated,
        )

        self.assertEqual(normalized.result_type, ActorResultType.NEEDS_REPLAN)
        self.assertIn("repeated the same", normalized.blocking_reason)


    # Finalization is the defense-in-depth boundary for execution receipt integrity.
    def test_finalize_rejects_completed_execution_task_without_receipt(self) -> None:
        state = TaskState.from_intake(
            _build_external_communication_intake_result_for_governance()
        )
        state.current_state = RuntimeState.COMPLETED

        with self.assertRaisesRegex(
            RuntimeError,
            "unsatisfied terminal contract",
        ):
            finalize_bootstrap(state)


    # Finalization rejects a nominal completion that leaves live semantic work behind.
    def test_finalize_rejects_completed_task_with_unexhausted_plan(self) -> None:
        state = TaskState.from_intake(_build_research_intake_result())
        state.execution_intent = ExecutionIntent.PROPOSAL_ONLY
        state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Prepare the result.",
                rationale="Required substantive work.",
                expected_outcome="A prepared result.",
                status=PlanStepStatus.COMPLETED,
            ),
            PlanStep(
                step_id="step_2",
                objective="Perform stale residual work.",
                rationale="Over-decomposed lifecycle work.",
                expected_outcome="A redundant outcome.",
                status=PlanStepStatus.IN_PROGRESS,
            ),
        ]
        state.current_plan_step_index = 1
        state.executed_steps = [
            ExecutedPlanStep(
                step_id="step_1",
                objective="Prepare the result.",
                result_type=ActorResultType.STEP_COMPLETED,
                summary="Prepared the result.",
            )
        ]
        state.current_state = RuntimeState.COMPLETED

        with self.assertRaisesRegex(RuntimeError, "unsatisfied terminal contract"):
            finalize_bootstrap(state)


    def test_actor_prompt_has_no_case_specific_claim_rewriting(self) -> None:
        state = _build_conservative_replan_summary_task_state()
        state.observations.append(
            ToolObservation(
                observation_id="replan_step_1:tool:6:source:1",
                locator="https://digital-markets-act.ec.europa.eu/legislation_en",
                content=(
                    "The Procedural Implementing Regulation (EU) 2023/814 details "
                    "procedural aspects related to the implementation and enforcement "
                    "of the DMA."
                ),
            )
        )
        actor_task = build_actor_task(task_state=state)

        system_prompt = build_actor_system_prompt()
        user_prompt = build_actor_user_prompt(actor_task=actor_task)

        self.assertIn("Base externally verifiable claims on concrete observations", system_prompt)
        self.assertNotIn("procedural/implementation sources", system_prompt)
        self.assertNotIn("content_state", user_prompt)
        self.assertNotIn("source_authority", user_prompt)
        self.assertNotIn("self-preferencing", user_prompt)


    def test_actor_prompt_projection_avoids_duplicates_and_keeps_prior_evidence(self) -> None:
        state = _build_policy_input_task_state()
        state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Collect source evidence.",
                rationale="The final synthesis needs grounded material.",
                expected_outcome="A relevant source excerpt.",
                status=PlanStepStatus.COMPLETED,
            ),
            PlanStep(
                step_id="step_2",
                objective="Synthesize the final answer.",
                rationale="Use evidence collected in the completed source step.",
                expected_outcome="A concise grounded final answer.",
                status=PlanStepStatus.IN_PROGRESS,
            ),
        ]
        state.current_plan_step_index = 1
        state.executed_steps = [
            ExecutedPlanStep(
                step_id="step_1",
                objective="Collect source evidence.",
                result_type=ActorResultType.INTERMEDIATE_OUTPUT,
                summary="Collected the source excerpt.",
                output_text="A large intermediate transcript that is not needed twice.",
            )
        ]
        state.observations = [
            ToolObservation(
                observation_id="step_1:tool:1:source:1",
                locator="https://example.org/prior-source",
                content="Evidence collected during the completed source step.",
            ),
        ]
        state.last_tool_result = ToolExecutionResult(
            request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.FETCH_URL_TEXT,
                reason="Collect prior source evidence.",
                url="https://example.org/prior-source",
            ),
            success=True,
            summary="Prior source fetched.",
            content="Evidence collected during the completed source step.",
            observations=(
                ToolObservation(
                    observation_id="step_1:tool:1:source:1",
                    locator="https://example.org/prior-source",
                    content="Evidence collected during the completed source step.",
                ),
            ),
        )

        prompt = build_actor_user_prompt(actor_task=build_actor_task(task_state=state))
        payload = json.loads(prompt.split("\n\n", 1)[1])

        self.assertNotIn("objective", payload)
        self.assertEqual(payload["task_spec"]["goal"], state.task_spec.goal)
        self.assertEqual(payload["plan"]["current_plan_step"]["step_id"], "step_2")
        self.assertEqual(
            [step["step_id"] for step in payload["plan"]["other_steps"]],
            ["step_1"],
        )
        self.assertIn("rationale", payload["plan"]["other_steps"][0])
        self.assertEqual(
            [item["observation_id"] for item in payload["observations"]],
            ["step_1:tool:1:source:1"],
        )
        self.assertNotIn("content", payload["last_tool_result"])
        self.assertNotIn("sources", payload["last_tool_result"])
        self.assertEqual(
            payload["past_steps"][0]["output_text"],
            "A large intermediate transcript that is not needed twice.",
        )


    def test_openrouter_completion_with_unsupported_precision_stays_in_progress(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterUnsupportedPrecisionCompletionClient()  # type: ignore[attr-defined]
        state = _build_high_precision_research_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertIsNotNone(result.step_validation)
        self.assertEqual(result.step_validation.evidence_refs, ())
        self.assertGreaterEqual(result.step_validation.actor_self_confidence, 0.0)


    def test_openrouter_completion_allows_precise_facts_from_intake(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterIntakeSupportedExternalDraftClient()  # type: ignore[attr-defined]
        state = _build_external_incident_draft_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertIsNotNone(result.step_validation)
        self.assertGreater(result.step_validation.actor_self_confidence, 0.0)


    def test_openrouter_completion_sanitizes_internal_trace_refs(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterCompletionWithInternalRefsClient()  # type: ignore[attr-defined]
        state = _build_summary_task_state_with_year_in_citation()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertNotIn("【", result.output_text or "")
        self.assertNotIn("step_1:tool:", result.output_text or "")
        self.assertNotIn("【", result.step_validation.rationale_summary)


    def test_openrouter_source_shortlist_completion_stays_at_source_discovery(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterSourceShortlistCompletionClient()  # type: ignore[attr-defined]
        state = _build_source_shortlist_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertIsNotNone(result.step_validation)
        self.assertGreater(result.step_validation.actor_self_confidence, 0.0)


    def test_openrouter_summary_with_regulation_year_stays_supported(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterSummaryWithRegulationYearClient()  # type: ignore[attr-defined]
        state = _build_summary_task_state_with_year_in_citation()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertIsNotNone(result.step_validation)
        self.assertGreater(result.step_validation.actor_self_confidence, 0.0)


    def test_openrouter_does_not_rewrite_provider_text_with_domain_specific_regex(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterProceduralImplementationSummaryClient()  # type: ignore[attr-defined]
        state = _build_summary_task_state_with_year_in_citation()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertIn(
            "establishes enforcement by the European Commission",
            result.output_text,
        )


    def test_openrouter_summary_with_generic_timeline_stays_supported(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterSummaryWithGenericTimelineClient()  # type: ignore[attr-defined]
        state = _build_summary_task_state_with_generic_timeline()
        observed_state = observe_tool_result(state)
        actor_task = build_actor_task(task_state=observed_state)

        self.assertEqual(
            observed_state.observations[0].content,
            "The AI Act entered into force on 1 August 2024.",
        )

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertIsNotNone(result.step_validation)
        self.assertGreater(result.step_validation.actor_self_confidence, 0.0)


    def test_openrouter_conservative_summary_after_extractability_limit_completes(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterConservativeSummaryAfterExtractabilityLimitClient()  # type: ignore[attr-defined]
        state = _build_conservative_replan_summary_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertIsNotNone(result.step_validation)
        self.assertGreater(result.step_validation.actor_self_confidence, 0.0)


    def test_openrouter_false_no_tool_blocked_recovers_to_web_search(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterFalseNoToolBlockedClient()  # type: ignore[attr-defined]
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.PROPOSE_ACTION)
        self.assertEqual(result.capability_mode, CapabilityAccessMode.READ_ONLY)
        self.assertIsNotNone(result.tool_request)
        self.assertEqual(result.tool_request.tool_name, ToolName.WEB_SEARCH)


    def test_openrouter_empty_response_becomes_blocked_actor_result(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterEmptyResponseClient()  # type: ignore[attr-defined]
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.BLOCKED)
        self.assertIn("no usable runtime action", result.summary)
        self.assertIsNotNone(result.blocking_reason)


    def test_openrouter_actor_timeout_is_normalized(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        class TimeoutBedrockClient:
            # Surface a normalized provider timeout through the actor boundary.
            def complete_tool_call(self, **kwargs: object) -> dict[str, object]:
                raise bedrock_client_module.BedrockRuntimeError("Bedrock runtime request timed out")

        actor._client = TimeoutBedrockClient()  # type: ignore[attr-defined]
        with self.assertRaisesRegex(ActorExecutionError, "timed out"):
            actor.act(actor_task=actor_task)


    def test_build_actor_task_uses_typed_evidence_metadata(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Collect official sources.",
                rationale="The step is source discovery.",
                expected_outcome="A shortlist of official sources.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0

        actor_task = build_actor_task(task_state=task_state)

        self.assertEqual(actor_task.current_plan_step.step_id, "step_1")
        self.assertEqual(actor_task.current_plan_step.objective, "Collect official sources.")
        self.assertEqual(
            set(vars(actor_task.current_plan_step)),
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
