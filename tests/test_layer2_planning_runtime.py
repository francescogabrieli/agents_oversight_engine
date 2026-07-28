from __future__ import annotations

from tests.layer2_runtime_fixtures import *


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
                ),
                _ReplanStepPayload(
                    objective="Synthesize the recovered result.",
                    rationale="The task still requires a final grounded answer.",
                    expected_outcome="A concise final output.",
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
        self.assertEqual(decision.prompt_version, "replanner_v3")
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


    def test_openai_planner_settings_default_timeout_without_env(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "layer2_runtime_governance.planning._load_env_file_values",
            return_value={},
        ):
            settings = OpenAIPlannerSettings.from_env()

        self.assertEqual(settings.timeout_seconds, 30.0)


    def test_openai_planner_retries_retryable_provider_failures(self) -> None:
        from layer2_runtime_governance.planning import _PlanningPayload, _PlanStepPayload

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

        self.assertEqual(result.result_type, ActorResultType.NEEDS_REPLAN)
        self.assertIn("incomplete runtime action", result.summary)
        self.assertIn("missing required string argument 'summary'", result.blocking_reason)


    def test_planner_prompt_is_detailed_and_general_purpose(self) -> None:
        prompt = build_planner_system_prompt()

        self.assertIn("complete semantic plan", prompt)
        self.assertIn("Identify prerequisites and dependencies", prompt)
        self.assertIn("There is no preferred fixed step count", prompt)
        self.assertNotIn("email", prompt.lower())
        self.assertNotIn("research", prompt.lower())


    def test_planner_does_not_trim_when_step_budget_is_unbounded(self) -> None:
        task_state = TaskState.from_intake(
            _build_research_intake_result(max_steps=None, max_tool_calls=None)
        )
        planned_state = planner(task_state, planner_service=StubPlanner())

        self.assertEqual(planned_state.current_state, RuntimeState.PLANNING)
        self.assertEqual(len(planned_state.plan_steps), 2)
        self.assertEqual(planned_state.plan_steps[0].status, PlanStepStatus.IN_PROGRESS)
        self.assertNotIn("trimmed to the configured", task_state.step_history[0].summary)


    def test_runtime_preserves_over_decomposed_planner_output_verbatim(self) -> None:
        task_state = TaskState.from_intake(
            _build_research_intake_result(max_steps=None, max_tool_calls=None)
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
            {"step_id", "objective", "rationale", "expected_outcome", "status"},
        )


if __name__ == "__main__":
    unittest.main()
