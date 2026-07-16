from __future__ import annotations

from tests.layer2_runtime_fixtures import *


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
            "layer2_runtime_governance.bedrock_client._load_env_file_values",
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

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertEqual(result.output_text, "OpenRouter actor completed the step.")
        self.assertIsNotNone(result.step_validation)
        self.assertEqual(result.step_validation.confidence, 0.82)


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
                                        "name": "emit_final_output",
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
                        "name": "emit_final_output",
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

        self.assertEqual(payload["tool_name"], "emit_final_output")
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

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
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


    def test_bedrock_rejects_any_model_except_the_approved_inference_profile(self) -> None:
        with self.assertRaisesRegex(
            bedrock_client_module.BedrockRuntimeError,
            "must be 'eu.amazon.nova-2-lite-v1:0'",
        ):
            bedrock_client_module.BedrockChatClient(
                bedrock_client_module.BedrockRuntimeSettings(
                    model_id="some-other-model",
                    access_key_id="test-key",
                    secret_access_key="test-secret-key",
                )
            )


    def test_bedrock_rejects_any_region_except_eu_central_1(self) -> None:
        with self.assertRaisesRegex(
            bedrock_client_module.BedrockRuntimeError,
            "region must be 'eu-central-1'",
        ):
            bedrock_client_module.BedrockChatClient(
                bedrock_client_module.BedrockRuntimeSettings(
                    region_name="eu-west-1",
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

        self.assertEqual(result.result_type, ActorResultType.INTERMEDIATE_OUTPUT)
        self.assertIsNotNone(result.step_validation)
        self.assertTrue(result.step_validation.objective_satisfied)
        self.assertTrue(result.step_validation.evidence_sufficient)
        self.assertEqual(
            result.step_validation.next_action,
            StepValidationDecision.COMPLETE_STEP,
        )
        self.assertIsNone(result.step_validation.residual_uncertainty)


    def test_actor_prompts_bias_toward_current_step_completion(self) -> None:
        state = _build_policy_input_task_state()
        state.current_plan_step.step_id = "replan_step_1"
        state.current_plan_step.objective = (
            "Use the already-fetched Commission page content to extract high-level supported themes."
        )
        state.observed_evidence.append(
            ObservedEvidence(
                evidence_id="replan_step_1:tool:1:source:1",
                plan_step_id="replan_step_1",
                title="Commission DMA Legislation",
                locator="https://digital-markets-act.ec.europa.eu/legislation_en",
                excerpt="The DMA contains the main rules for the designation of gatekeepers and the implementation of obligations and prohibitions.",
                source_tool=ToolName.FETCH_URL_TEXT,
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

        self.assertIn("Do not gather evidence for future steps", system_prompt)
        self.assertIn("replacement step objective overrides the older blocked path", system_prompt)
        self.assertIn("A discovered source URL is not factual support", system_prompt)
        self.assertIn('"prefer_completion_with_existing_evidence": true', user_prompt)
        self.assertIn('"replan_replacement_step_active": true', user_prompt)
        self.assertIn('"source_discovery_is_not_direct_fact_support": true', user_prompt)
        self.assertIn('"available_evidence_support_level": "high_level_summary"', user_prompt)


    def test_actor_prompts_high_level_summary_to_avoid_granular_examples(self) -> None:
        state = _build_conservative_replan_summary_task_state()
        state.observed_evidence.append(
            ObservedEvidence(
                evidence_id="replan_step_1:tool:6:source:1",
                plan_step_id="replan_step_1",
                title="Digital Markets Act (DMA) Legislation",
                locator="https://digital-markets-act.ec.europa.eu/legislation_en",
                excerpt=(
                    "The Procedural Implementing Regulation (EU) 2023/814 details "
                    "procedural aspects related to the implementation and enforcement "
                    "of the DMA."
                ),
                source_tool=ToolName.FETCH_URL_TEXT,
            )
        )
        actor_task = build_actor_task(task_state=state)

        system_prompt = build_actor_system_prompt()
        user_prompt = build_actor_user_prompt(actor_task=actor_task)

        self.assertIn("keep obligation language broad", system_prompt)
        self.assertIn(
            "describe them as procedural/implementation sources",
            system_prompt,
        )
        self.assertIn(
            '"current_or_prior_steps_have_procedural_implementation_source": true',
            user_prompt,
        )
        self.assertIn('"high_level_summary_mode": true', user_prompt)
        self.assertIn(
            '"avoid_granular_obligation_examples_unless_directly_observed": true',
            user_prompt,
        )
        self.assertIn("self-preferencing", user_prompt)
        self.assertIn("obligations and prohibitions for gatekeepers", user_prompt)


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

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertIsNotNone(result.step_validation)
        self.assertFalse(result.step_validation.objective_satisfied)
        self.assertFalse(result.step_validation.evidence_sufficient)
        self.assertEqual(
            result.step_validation.next_action,
            StepValidationDecision.REQUEST_MORE_EVIDENCE,
        )
        self.assertIn("more specific than the observed evidence", result.summary.lower())
        self.assertIsNotNone(result.step_validation.residual_uncertainty)
        self.assertIn("more specific legal or factual claims", result.step_validation.residual_uncertainty)


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

        self.assertEqual(result.result_type, ActorResultType.INTERMEDIATE_OUTPUT)
        self.assertIsNotNone(result.step_validation)
        self.assertTrue(result.step_validation.objective_satisfied)
        self.assertTrue(result.step_validation.evidence_sufficient)
        self.assertEqual(
            result.step_validation.next_action,
            StepValidationDecision.COMPLETE_STEP,
        )
        self.assertNotIn("more specific than the observed evidence", result.summary.lower())


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

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
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

        self.assertEqual(result.result_type, ActorResultType.INTERMEDIATE_OUTPUT)
        self.assertIsNotNone(result.step_validation)
        self.assertTrue(result.step_validation.objective_satisfied)
        self.assertTrue(result.step_validation.evidence_sufficient)
        self.assertEqual(result.step_validation.next_action, StepValidationDecision.COMPLETE_STEP)


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

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertIsNotNone(result.step_validation)
        self.assertTrue(result.step_validation.objective_satisfied)
        self.assertTrue(result.step_validation.evidence_sufficient)
        self.assertEqual(result.step_validation.next_action, StepValidationDecision.COMPLETE_STEP)


    def test_openrouter_procedural_implementation_summary_softens_enforcement_language(self) -> None:
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

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertNotIn("enforces the rules", result.output_text.lower())
        self.assertNotIn(
            "establishes enforcement by the european commission",
            result.output_text.lower(),
        )
        self.assertIn(
            "describes implementation and enforcement through the European Commission",
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
            observed_state.observed_evidence[0].support_level,
            EvidenceSupportLevel.DETAILED_EXTRACTED,
        )

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertIsNotNone(result.step_validation)
        self.assertTrue(result.step_validation.objective_satisfied)
        self.assertTrue(result.step_validation.evidence_sufficient)
        self.assertEqual(result.step_validation.next_action, StepValidationDecision.COMPLETE_STEP)


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

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertIsNotNone(result.step_validation)
        self.assertTrue(result.step_validation.objective_satisfied)
        self.assertTrue(result.step_validation.evidence_sufficient)
        self.assertEqual(result.step_validation.next_action, StepValidationDecision.COMPLETE_STEP)


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
        task_state = TaskState.from_intake(_build_research_intake_result(max_steps=None, max_tool_calls=None))
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Collect official sources.",
                rationale="The step is source discovery.",
                expected_outcome="A shortlist of official sources.",
                step_kind=PlanStepKind.SOURCE_DISCOVERY,
                evidence_requirement=EvidenceSupportLevel.SOURCE_DISCOVERY,
                claim_precision_ceiling=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0

        actor_task = build_actor_task(task_state=task_state)

        self.assertEqual(actor_task.step_kind, PlanStepKind.SOURCE_DISCOVERY)
        self.assertEqual(actor_task.required_evidence_support_level, EvidenceSupportLevel.SOURCE_DISCOVERY)
        self.assertEqual(actor_task.max_supported_claim_precision, EvidenceSupportLevel.HIGH_LEVEL_SUMMARY)


if __name__ == "__main__":
    unittest.main()
