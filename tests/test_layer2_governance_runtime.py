from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from pathlib import Path
import shutil
import unittest

from langgraph.checkpoint.memory import InMemorySaver

from tests.layer2_runtime_fixtures import (
    FakeOPAResponse,
    JudgeRequiredPolicyEngine,
    ReviewPolicyEngine,
    StaticJudge,
    StubActor,
    StubPlanner,
    StubPolicyEngine,
    StubReplanner,
    StubToolGateway,
    _build_actor_ready_state_for_judge,
    _build_behavioral_candidate_state,
    _build_external_communication_intake_result_for_governance,
    _build_policy_input_task_state,
    _build_research_intake_result,
    _build_test_graph,
    _judge_more_evidence_assessment,
    _judge_wait_review_assessment,
    _seed_behavioral_history,
)

from layer1_intake import SensitivityLevel
from layer2_runtime_governance.actor_contract import (
    ActorResult,
    ActorResultType,
    CapabilityAccessMode,
)
from layer2_runtime_governance.domain import (
    PlanStep,
    PlanStepStatus,
    RuntimeState,
    StepRecord,
    StepValidation,
    TaskState,
)
from layer2_runtime_governance.governance_components import (
    GovernanceDecision,
    GovernanceDirective,
    GovernanceReasonCode,
    OPAHttpPolicyEngine,
    OPAHttpPolicyEngineSettings,
    PolicyDecision,
    build_policy_evaluation_input,
)
from layer2_runtime_governance.judge import JudgeAssessmentStatus
from layer2_runtime_governance.runtime.governance_service import (
    _apply_governance_decision,
    governance,
)
from layer3_tool_bus_external_capabilities.tool_contract import (
    ToolName,
    ToolObservation,
    ToolRequest,
)
from layer4_state_evidence_observability.behavioral import (
    BehavioralRecommendedDirective,
    BehavioralStoreSettings,
    SQLiteBehavioralStore,
)
from layer4_state_evidence_observability.infrastructure.checkpointing import (
    build_runtime_serializer,
)
from main_layer2 import run_layer2_flow
REPO_ROOT = Path(__file__).resolve().parents[1]


class Layer2GovernanceRuntimeTests(unittest.TestCase):
    # Scenarios remain grouped by the capability whose behavior they verify.
    def test_governance_input_uses_evaluated_step_context(self) -> None:
        task_state = _build_policy_input_task_state(read_only_mode=True)

        policy_input = build_policy_evaluation_input(task_state=task_state)

        self.assertEqual(policy_input.task_sensitivity, SensitivityLevel.LOW)
        self.assertTrue(policy_input.read_only_mode)
        self.assertEqual(policy_input.current_plan_step_id, "step_1")
        self.assertEqual(policy_input.current_plan_step_objective, "Collect the minimum official evidence needed for the task.")
        self.assertEqual(policy_input.actor_result_type, ActorResultType.PROPOSE_ACTION)
        self.assertEqual(policy_input.capability_mode, CapabilityAccessMode.READ_ONLY)
        self.assertEqual(policy_input.tool_name, ToolName.WEB_SEARCH)
        self.assertTrue(policy_input.tool_has_query)
        self.assertFalse(policy_input.tool_has_url)
        self.assertEqual(policy_input.tool_call_count, 0)
        self.assertEqual(policy_input.observation_count, 0)


    def test_opa_http_policy_engine_parses_structured_response_without_network(self) -> None:
        import json
        from unittest.mock import patch

        task_state = _build_policy_input_task_state(read_only_mode=True)
        policy_input = build_policy_evaluation_input(task_state=task_state)
        response_body = json.dumps(
            {
                "result": {
                    "policy_decision": "allow_read_only",
                    "reason_codes": ["read_only_allowed"],
                    "summary": "Read-only capability use is allowed.",
                }
            }
        ).encode("utf-8")

        engine = OPAHttpPolicyEngine(
            settings=OPAHttpPolicyEngineSettings(
                base_url="http://policy.test",
                decision_path="/v1/data/layer2/governance/decision",
                timeout_seconds=1.0,
            )
        )

        with patch(
            "layer2_runtime_governance.governance_components.policy_engine.urlopen",
            return_value=FakeOPAResponse(response_body),
        ), self.assertWarns(DeprecationWarning):
            result = engine.evaluate(policy_input=policy_input)

        self.assertEqual(result.policy_decision, PolicyDecision.ALLOW_READ_ONLY)
        self.assertEqual(result.reason_codes, (GovernanceReasonCode.READ_ONLY_ALLOWED,))
        self.assertEqual(result.summary, "Read-only capability use is allowed.")
        self.assertEqual(result.policy_engine_name, "opa_http_policy_engine")
        self.assertEqual(result.policy_bundle_version, "layer2_governance_v4")


    def test_read_only_tool_request_routes_through_governance(self) -> None:
        graph = _build_test_graph(policy_engine_service=StubPolicyEngine())
        result = graph.invoke(
            {"intake_result": _build_research_intake_result(read_only_mode=True)},
            config={"configurable": {"thread_id": "layer2-read-only-governance"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.tool_call_count, 1)
        self.assertEqual(len(task_state.governance_history), 3)
        self.assertEqual(task_state.governance_history[0].policy_decision, PolicyDecision.ALLOW_READ_ONLY)
        self.assertEqual(task_state.governance_history[0].directive, GovernanceDirective.CONTINUE_READ_ONLY)
        self.assertEqual(task_state.last_governance_decision.policy_decision, PolicyDecision.ALLOW)
        self.assertEqual(task_state.last_governance_decision.directive, GovernanceDirective.CONTINUE)
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


    def test_write_and_external_compatibility_flags_alone_are_not_denied(self) -> None:
        task_state = _build_policy_input_task_state(read_only_mode=False)
        task_state.actor_result = replace(
            task_state.actor_result,
            capability_mode=CapabilityAccessMode.WRITE,
            external_impact=True,
        )

        result = StubPolicyEngine().evaluate(
            policy_input=build_policy_evaluation_input(task_state=task_state)
        )

        self.assertEqual(result.policy_decision, PolicyDecision.ALLOW)
        self.assertNotIn(
            GovernanceReasonCode.WRITE_CAPABILITY_BLOCKED,
            result.reason_codes,
        )


    def test_request_more_evidence_preserves_pending_read_only_tool_request(self) -> None:
        task_state = _build_policy_input_task_state()
        policy_input = build_policy_evaluation_input(task_state=task_state)
        decision = GovernanceDecision(
            directive=GovernanceDirective.REQUEST_MORE_EVIDENCE,
            policy_decision=PolicyDecision.ALLOW,
            reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
            summary="Evidence support is below the current step requirement.",
            constrains_capability_use=True,
            evaluated_actor_result_type=ActorResultType.PROPOSE_ACTION,
            evaluated_tool_name=ToolName.WEB_SEARCH,
            policy_engine_name="test_policy",
            policy_bundle_version="test_v1",
            policy_directive=GovernanceDirective.CONTINUE_READ_ONLY,
            oversight_recommended_directive=GovernanceDirective.REQUEST_MORE_EVIDENCE,
        )

        _apply_governance_decision(
            task_state,
            governance_decision=decision,
            policy_input=policy_input,
        )

        self.assertEqual(task_state.current_state, RuntimeState.READY_TOOL_EXECUTION)
        self.assertIsNotNone(task_state.current_tool_request)
        self.assertEqual(task_state.current_tool_request.tool_name, ToolName.WEB_SEARCH)


    def test_behavioral_store_is_populated_and_second_run_sees_history(self) -> None:
        temp_dir = REPO_ROOT / "_behavioral_store_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(
                    database_path=temp_dir / "behavioral.sqlite3"
                )
            )

            first_trace = run_layer2_flow(
                raw_task_text="Find official sources about the DMA and summarize them.",
                context_hint="Prefer official EU sources.",
                thread_id="behavioral-run-1",
                demo_intake_profile="research",
                planner_service=StubPlanner(),
                actor_service=StubActor(),
                tool_gateway_service=StubToolGateway(),
                replanner_service=StubReplanner(),
                policy_engine_service=StubPolicyEngine(),
                behavioral_store_service=behavioral_store,
                checkpointer=InMemorySaver(serde=build_runtime_serializer()),
            )
            first_state = first_trace["final_result"]["task_state"]
            self.assertFalse(first_state.last_behavioral_assessment.history_available)
            self.assertEqual(first_state.last_behavioral_assessment.historical_run_count, 0)

            second_trace = run_layer2_flow(
                raw_task_text="Find official sources about the DMA and summarize them.",
                context_hint="Prefer official EU sources.",
                thread_id="behavioral-run-2",
                demo_intake_profile="research",
                planner_service=StubPlanner(),
                actor_service=StubActor(),
                tool_gateway_service=StubToolGateway(),
                replanner_service=StubReplanner(),
                policy_engine_service=StubPolicyEngine(),
                behavioral_store_service=behavioral_store,
                checkpointer=InMemorySaver(serde=build_runtime_serializer()),
            )
            second_state = second_trace["final_result"]["task_state"]
            self.assertTrue(second_state.last_behavioral_assessment.history_available)
            self.assertGreaterEqual(second_state.last_behavioral_assessment.historical_run_count, 1)
            self.assertTrue((temp_dir / "behavioral.sqlite3").exists())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


    def test_behavioral_below_threshold_stays_descriptive_and_continue(self) -> None:
        temp_dir = REPO_ROOT / "_behavioral_below_threshold_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(database_path=temp_dir / "behavioral.sqlite3")
            )
            state = _build_behavioral_candidate_state()

            assessment = behavioral_store.build_behavioral_assessment(
                thread_id="current-run",
                task_state=state,
            )

            self.assertEqual(
                assessment.behavioral_recommended_directive,
                BehavioralRecommendedDirective.CONTINUE,
            )
            self.assertIsNone(assessment.historical_similarity_score)
            self.assertIsNone(assessment.behavior_deviation_score)
            self.assertIsNone(assessment.novelty_score)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


    def test_behavioral_same_family_high_similarity_stays_continue(self) -> None:
        temp_dir = REPO_ROOT / "_behavioral_similarity_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(database_path=temp_dir / "behavioral.sqlite3")
            )
            state = _build_behavioral_candidate_state()
            _seed_behavioral_history(
                behavioral_store=behavioral_store,
                count=24,
                prefix="similar",
                profile_overrides={},
            )

            assessment = behavioral_store.build_behavioral_assessment(
                thread_id="current-run",
                task_state=state,
            )

            self.assertEqual(
                assessment.behavioral_recommended_directive,
                BehavioralRecommendedDirective.CONTINUE,
            )
            self.assertIsNotNone(assessment.historical_similarity_score)
            self.assertGreaterEqual(assessment.historical_similarity_score, 0.75)
            self.assertGreaterEqual(assessment.matched_run_count, 5)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


    def test_behavioral_high_novelty_with_fragile_evidence_requests_more_evidence(self) -> None:
        temp_dir = REPO_ROOT / "_behavioral_novelty_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(database_path=temp_dir / "behavioral.sqlite3")
            )
            state = _build_behavioral_candidate_state()
            state.plan_steps.extend(
                [
                    PlanStep(
                        step_id="step_4",
                        objective="Collect additional niche evidence.",
                        rationale="Make the current profile longer than history.",
                        expected_outcome="Extra context.",
                    ),
                    PlanStep(
                        step_id="step_5",
                        objective="Produce completion attempt.",
                        rationale="Force a completion-shaped runtime state.",
                        expected_outcome="Completion attempt.",
                    ),
                ]
            )
            state.actor_turn_count = 5
            state.tool_call_count = 2
            state.current_state = RuntimeState.ABORTED
            state.step_history.extend(
                [
                    StepRecord(3, RuntimeState.REPLANNING, "replanner", "Replanned."),
                    StepRecord(4, RuntimeState.REPLANNING, "replanner", "Replanned."),
                ]
            )
            state.governance_history = [
                GovernanceDecision(
                    directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                    policy_decision=PolicyDecision.ALLOW,
                    reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
                    summary="Escalated once.",
                    constrains_capability_use=False,
                    evaluated_actor_result_type=ActorResultType.INTERMEDIATE_OUTPUT,
                    evaluated_tool_name=None,
                    policy_engine_name="test_policy",
                    policy_bundle_version="test_v1",
                    policy_directive=GovernanceDirective.CONTINUE,
                    oversight_recommended_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                )
            ]
            state.observations[0] = ToolObservation(
                observation_id=state.observations[0].observation_id,
                locator=state.observations[0].locator,
                content="Limited source metadata.",
            )
            state.actor_result = ActorResult(
                plan_step_id="step_3",
                result_type=ActorResultType.STEP_COMPLETED,
                summary="Final output attempted from weak evidence.",
                capability_mode=CapabilityAccessMode.NONE,
                output_text="A detailed final output from limited support.",
                step_validation=StepValidation(
                    plan_step_id="step_3",
                    actor_self_confidence=0.44,
                    evidence_refs=(),
                    rationale_summary="The output is still weakly supported.",
                ),
            )
            _seed_behavioral_history(
                behavioral_store=behavioral_store,
                count=24,
                prefix="novelty",
                profile_overrides={
                    "sensitivity": SensitivityLevel.MEDIUM.value,
                    "policy_profile_name": "legacy_profile",
                    "step_count": 2,
                    "actor_turn_count": 1,
                    "tool_call_count": 0,
                    "tool_name": ToolName.MCP_LOCAL_TEST_ECHO.value,
                },
            )

            assessment = behavioral_store.build_behavioral_assessment(
                thread_id="current-run",
                task_state=state,
            )

            self.assertEqual(
                assessment.behavioral_recommended_directive,
                BehavioralRecommendedDirective.REQUEST_MORE_EVIDENCE,
            )
            self.assertIsNotNone(assessment.historical_similarity_score)
            self.assertLess(assessment.historical_similarity_score, 0.35)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


    def test_behavioral_deviation_can_trigger_replan(self) -> None:
        temp_dir = REPO_ROOT / "_behavioral_replan_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(database_path=temp_dir / "behavioral.sqlite3")
            )
            state = _build_behavioral_candidate_state()
            state.plan_steps.extend(
                [
                    PlanStep(
                        step_id="step_4",
                        objective="Recover from divergence.",
                        rationale="Force a wider trajectory gap.",
                        expected_outcome="Recovery path 1.",
                    ),
                    PlanStep(
                        step_id="step_5",
                        objective="Recover further.",
                        rationale="Force a wider trajectory gap.",
                        expected_outcome="Recovery path 2.",
                    ),
                    PlanStep(
                        step_id="step_6",
                        objective="Recover further.",
                        rationale="Force a wider trajectory gap.",
                        expected_outcome="Recovery path 3.",
                    ),
                    PlanStep(
                        step_id="step_7",
                        objective="Recover further.",
                        rationale="Force a wider trajectory gap.",
                        expected_outcome="Recovery path 4.",
                    ),
                ]
            )
            state.actor_turn_count = 10
            state.tool_call_count = 6
            state.plan_steps = state.plan_steps[:3]
            state.step_history.extend(
                [
                    StepRecord(3, RuntimeState.REPLANNING, "replanner", "Replanned."),
                    StepRecord(4, RuntimeState.REPLANNING, "replanner", "Replanned."),
                    StepRecord(5, RuntimeState.REPLANNING, "replanner", "Replanned."),
                    StepRecord(6, RuntimeState.REPLANNING, "replanner", "Replanned."),
                    StepRecord(7, RuntimeState.REPLANNING, "replanner", "Replanned."),
                ]
            )
            state.governance_history = [
                GovernanceDecision(
                    directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                    policy_decision=PolicyDecision.ALLOW,
                    reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
                    summary="Escalated once.",
                    constrains_capability_use=False,
                    evaluated_actor_result_type=ActorResultType.INTERMEDIATE_OUTPUT,
                    evaluated_tool_name=None,
                    policy_engine_name="test_policy",
                    policy_bundle_version="test_v1",
                    policy_directive=GovernanceDirective.CONTINUE,
                    oversight_recommended_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                ),
                GovernanceDecision(
                    directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                    policy_decision=PolicyDecision.ALLOW,
                    reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
                    summary="Escalated twice.",
                    constrains_capability_use=False,
                    evaluated_actor_result_type=ActorResultType.INTERMEDIATE_OUTPUT,
                    evaluated_tool_name=None,
                    policy_engine_name="test_policy",
                    policy_bundle_version="test_v1",
                    policy_directive=GovernanceDirective.CONTINUE,
                    oversight_recommended_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                ),
            ]
            _seed_behavioral_history(
                behavioral_store=behavioral_store,
                count=24,
                prefix="replan",
                profile_overrides={
                    "actor_turn_count": 2,
                    "tool_call_count": 1,
                    "replan_count": 0,
                },
            )

            assessment = behavioral_store.build_behavioral_assessment(
                thread_id="current-run",
                task_state=state,
            )

            self.assertEqual(
                assessment.behavioral_recommended_directive,
                BehavioralRecommendedDirective.REPLAN,
            )
            self.assertIsNotNone(assessment.behavior_deviation_score)
            self.assertGreaterEqual(assessment.behavior_deviation_score, 0.60)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


    def test_behavioral_high_review_rate_with_unhealthy_dynamic_signals_waits_review(self) -> None:
        temp_dir = REPO_ROOT / "_behavioral_review_rate_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(database_path=temp_dir / "behavioral.sqlite3")
            )
            state = _build_behavioral_candidate_state()
            _seed_behavioral_history(
                behavioral_store=behavioral_store,
                count=24,
                prefix="review-heavy",
                profile_overrides={"review_count": 1},
            )

            assessment = behavioral_store.build_behavioral_assessment(
                thread_id="current-run",
                task_state=state,
            )

            self.assertEqual(
                assessment.behavioral_recommended_directive,
                BehavioralRecommendedDirective.WAIT_HUMAN_REVIEW,
            )
            self.assertIn("historical_review_rate_high", assessment.behavioral_flags)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


    def test_judge_is_not_invoked_for_static_review(self) -> None:
        state = _build_actor_ready_state_for_judge()
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.PROPOSE_ACTION,
            summary="Propose one external read-only action.",
            capability_mode=CapabilityAccessMode.READ_ONLY,
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.WEB_SEARCH,
                reason="Need one governed lookup.",
                query="governed lookup",
            ),
        )
        state.current_tool_request = state.actor_result.tool_request
        result = governance(
            state,
            policy_engine_service=ReviewPolicyEngine(),
            judge_service=StaticJudge(_judge_wait_review_assessment()),
            thread_id="judge-static-review",
        )

        self.assertIsNotNone(result.last_judge_assessment)
        self.assertFalse(result.last_judge_assessment.invoked)
        self.assertEqual(
            result.last_governance_decision.directive,
            GovernanceDirective.WAIT_HUMAN_REVIEW,
        )


    def test_judge_is_not_invoked_for_local_fragile_completion(self) -> None:
        state = _build_actor_ready_state_for_judge()
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Attempt fragile completion.",
            capability_mode=CapabilityAccessMode.NONE,
            output_text="A precise output from limited evidence.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.40,
                evidence_refs=(),
                rationale_summary="Still not sufficiently grounded.",
            ),
        )
        state.current_tool_request = None

        result = governance(
            state,
            policy_engine_service=StubPolicyEngine(),
            judge_service=StaticJudge(_judge_more_evidence_assessment()),
            thread_id="judge-fragile-completion",
        )

        self.assertFalse(result.last_judge_assessment.invoked)
        self.assertEqual(result.last_judge_assessment.status, JudgeAssessmentStatus.NOT_APPLICABLE)


    def test_governance_keeps_intermediate_recovery_local_when_evidence_contract_is_satisfied(self) -> None:
        state = TaskState.from_intake(_build_external_communication_intake_result_for_governance())
        state.current_state = RuntimeState.ACTING
        state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Draft a first local response using the provided incident facts.",
                rationale="Use the supplied incident details before gathering any external evidence.",
                expected_outcome="A partial local draft grounded only in the intake facts.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        state.current_plan_step_index = 0
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary="Partial draft prepared from the provided incident facts.",
            capability_mode=CapabilityAccessMode.NONE,
            output_text="We are preparing an incident update based on the currently provided facts.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.42,
                evidence_refs=(),
                rationale_summary="More evidence is required before this draft can be finalized.",
                residual_uncertainty="The runtime should continue locally instead of opening HITL.",
            ),
        )

        result = governance(
            state,
            policy_engine_service=StubPolicyEngine(),
            judge_service=StaticJudge(_judge_wait_review_assessment()),
            thread_id="intermediate-softening",
        )

        self.assertEqual(
            result.last_governance_decision.directive,
            GovernanceDirective.CONTINUE,
        )
        self.assertEqual(result.current_state, RuntimeState.ACTING)
        self.assertIsNone(result.pending_human_review_request)
        self.assertIsNone(result.external_communication)


    def test_required_judge_without_external_proposal_fails_closed(self) -> None:
        state = _build_actor_ready_state_for_judge()
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Fragile final output.",
            capability_mode=CapabilityAccessMode.NONE,
            output_text="Possibly unsupported completion.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.42,
                evidence_refs=(),
                rationale_summary="Output is not strongly grounded.",
            ),
        )
        state.current_tool_request = None

        result = governance(
            state,
            policy_engine_service=JudgeRequiredPolicyEngine(),
            judge_service=StaticJudge(_judge_wait_review_assessment()),
            thread_id="judge-precedence",
        )

        self.assertEqual(
            result.last_governance_decision.directive,
            GovernanceDirective.WAIT_HUMAN_REVIEW,
        )
        self.assertFalse(result.last_judge_assessment.invoked)
        self.assertEqual(result.last_judge_assessment.status, JudgeAssessmentStatus.UNAVAILABLE)


    def test_judge_snapshot_is_persisted_in_sqlite(self) -> None:
        temp_dir = REPO_ROOT / "_judge_persistence_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(database_path=temp_dir / "behavioral.sqlite3")
            )
            state = _build_actor_ready_state_for_judge()
            governance(
                state,
                policy_engine_service=StubPolicyEngine(),
                judge_service=StaticJudge(_judge_wait_review_assessment()),
                thread_id="judge-persisted-run",
                behavioral_store_service=behavioral_store,
            )
            self.assertGreaterEqual(len(state.judge_history), 1)

            import sqlite3

            with closing(sqlite3.connect(temp_dir / "behavioral.sqlite3")) as connection:
                row_count = connection.execute(
                    "SELECT COUNT(*) FROM judge_assessments WHERE thread_id = ?",
                    ("judge-persisted-run",),
                ).fetchone()[0]
            self.assertGreaterEqual(int(row_count), 1)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # Existing behavioral databases lose the retired task taxonomy column without losing runs.
    def test_behavioral_runs_schema_migrates_legacy_column_and_preserves_data(self) -> None:
        import sqlite3

        temp_dir = REPO_ROOT / "_behavioral_schema_migration_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir()
        database_path = temp_dir / "behavioral.sqlite3"

        try:
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE runs (
                        thread_id TEXT PRIMARY KEY,
                        final_state TEXT NOT NULL,
                        domain_type TEXT NOT NULL,
                        sensitivity TEXT NOT NULL,
                        policy_profile_name TEXT NOT NULL,
                        actor_turn_count INTEGER NOT NULL,
                        tool_call_count INTEGER NOT NULL,
                        replan_count INTEGER NOT NULL,
                        review_count INTEGER NOT NULL,
                        abort_count INTEGER NOT NULL,
                        has_external_communication INTEGER NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO runs VALUES (
                        'legacy-thread', 'completed', 'research', 'low', 'default',
                        3, 2, 0, 1, 0, 1
                    )
                    """
                )
                connection.commit()

            store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(database_path=database_path)
            )
            with closing(store._connect()) as connection:
                store._ensure_schema(connection)
                columns = tuple(
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(runs)").fetchall()
                )
                migrated = connection.execute(
                    """
                    SELECT thread_id, final_state, sensitivity, policy_profile_name,
                           actor_turn_count, tool_call_count, review_count,
                           has_external_communication
                    FROM runs
                    WHERE thread_id = 'legacy-thread'
                    """
                ).fetchone()

            self.assertNotIn("domain_type", columns)
            self.assertEqual(
                migrated,
                ("legacy-thread", "completed", "low", "default", 3, 2, 1, 1),
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
