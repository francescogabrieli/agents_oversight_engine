from __future__ import annotations

import unittest
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver

from layer1_intake import (
    ExecutionIntent,
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskSpec,
)
from layer2_runtime_governance.actor_contract import (
    ActorPort,
    ActorResult,
    ActorResultType,
    CapabilityAccessMode,
    ActorTask,
)
from layer4_state_evidence_observability.behavioral import (
    BehavioralAssessment,
    BehavioralRecommendedDirective,
    BehavioralStorePort,
)
from layer2_runtime_governance.domain import (
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    PlanStep,
    PlannerDecision,
    RuntimeState,
    StepValidation,
)
from layer3_tool_bus_external_capabilities.external_operations import normalize_communication_operation
from layer2_runtime_governance.governance_components import (
    GovernanceDirective,
)
from layer2_runtime_governance.judge import (
    JudgeAssessment,
    JudgeAssessmentStatus,
    JudgePort,
    JudgeRecommendedDirective,
    JudgeSeverity,
    JudgeVerdict,
)
from layer2_runtime_governance.planning import PlannerPort
from layer3_tool_bus_external_capabilities.tool_contract import (
    ToolErrorCode,
    ToolExecutionResult,
    ToolGatewayPort,
    ToolName,
    ToolRequest,
    ToolObservation,
)
from layer2_runtime_governance.composition import (
    Layer2RuntimeDependencies,
    compose_layer2_runtime,
)
from layer4_state_evidence_observability.infrastructure.checkpointing import build_runtime_serializer
from tests.layer2_runtime_fixtures import StubPolicyEngine, _build_stub_opa_decision


class _EmptyBehavioralStore(BehavioralStorePort):
    # Keep the regression independent from SQLite while preserving the port contract.
    def build_behavioral_assessment(self, *, thread_id, task_state):
        return BehavioralAssessment(
            history_available=False,
            historical_run_count=0,
            feature_snapshot_version="test",
            summary="No historical run is used in this regression.",
            notes=(),
            behavioral_recommended_directive=BehavioralRecommendedDirective.CONTINUE,
        )

    # Persistence is outside the deterministic regression's scope.
    def persist_behavioral_snapshot(self, *, thread_id, intake_result, task_state):
        return None

    # Judge snapshots are not authoritative for this isolated test.
    def persist_judge_snapshot(self, *, thread_id, task_state):
        return None

    # Keep stable-boundary persistence compatible with the runtime port.
    def persist_runtime_snapshot(self, *, thread_id, intake_result, task_state):
        return None


class LatestAIModelsEmailRegressionTests(unittest.TestCase):
    # The deterministic research-to-email path must pause before any send.
    def test_latest_ai_models_email_reaches_review_ready_hitl(self) -> None:
        graph = compose_layer2_runtime(
            Layer2RuntimeDependencies(
                planner=_ResearchEmailPlanner(),
                actor=_ResearchEmailActor(),
                tool_gateway=_ResearchEmailToolGateway(),
                policy_engine=_ReviewExternalPolicy(),
                judge=_SafeDigestJudge(),
                behavioral_store=_EmptyBehavioralStore(),
                checkpointer=InMemorySaver(serde=build_runtime_serializer()),
            )
        )
        result = graph.invoke(
            {"intake_result": _research_email_intake()},
            config={"configurable": {"thread_id": f"latest-ai-models-email-{uuid4()}"}},
        )
        state = result["task_state"]

        self.assertEqual(state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertNotEqual(state.current_state, RuntimeState.ABORTED)
        self.assertEqual(state.external_communication.phase, ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED)
        self.assertTrue(state.external_communication.review_ready)
        self.assertIn("OpenAI", state.external_communication.draft.body)
        self.assertIn("Anthropic", state.external_communication.draft.body)
        self.assertIn("Google", state.external_communication.draft.body)
        self.assertEqual(state.external_communication.draft.recipient, "recipient@example.com")
        self.assertIsNotNone(state.external_operation_proposal)
        self.assertTrue(state.external_operation_proposal.execution_candidate)
        self.assertIsNotNone(state.pending_human_review_request)
        self.assertEqual(
            state.pending_human_review_request.operation_payload_digest,
            state.external_operation_proposal.payload_digest,
        )
        self.assertEqual(state.actor_result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertEqual(len(state.executed_steps), 1)
        self.assertIsNotNone(state.pending_completion_candidate)
        self.assertFalse(any(item.directive == GovernanceDirective.STOP for item in state.governance_history))
        self.assertIn(RuntimeState.WAITING_HUMAN_REVIEW, [item.runtime_state for item in state.step_history])
        self.assertTrue(any(item.request.tool_name == ToolName.WEB_SEARCH for item in state.tool_results_history))
        fetch_errors = [
            item.error_code
            for item in state.tool_results_history
            if item.request.tool_name == ToolName.FETCH_URL_TEXT
        ]
        self.assertEqual(fetch_errors, [ToolErrorCode.FORBIDDEN, ToolErrorCode.BYTE_LIMIT, ToolErrorCode.BYTE_LIMIT])
        self.assertTrue(state.runtime_flags)
        self.assertEqual(len(state.observations), 3)
        self.assertTrue(all(item.content for item in state.observations))
        self.assertTrue(state.last_judge_assessment.invoked)
        self.assertEqual(
            state.last_judge_assessment.evaluated_operation_digest,
            state.external_operation_proposal.payload_digest,
        )

    # A completion with unusable evidence must recover into typed review, not denial or a loop.
    def test_missing_usable_evidence_stops_in_review_without_hard_deny(self) -> None:
        graph = compose_layer2_runtime(
            Layer2RuntimeDependencies(
                planner=_OneStepPlanner(),
                actor=_NoEvidenceActor(),
                tool_gateway=_ResearchEmailToolGateway(),
                policy_engine=StubPolicyEngine(),
                behavioral_store=_EmptyBehavioralStore(),
                checkpointer=InMemorySaver(serde=build_runtime_serializer()),
            )
        )
        intake = _research_email_intake(with_email=False)
        result = graph.invoke(
            {"intake_result": intake},
            config={"configurable": {"thread_id": f"missing-evidence-{uuid4()}"}},
        )
        state = result["task_state"]

        self.assertEqual(state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertFalse(any(item.directive == GovernanceDirective.STOP for item in state.governance_history))
        self.assertLessEqual(state.actor_turn_count, 2)
        self.assertTrue(state.governance_history)


# Build the exact mixed research and requested-email intake used by the regression.
def _research_email_intake(*, with_email: bool = True) -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Research the latest AI models and email a concise report.",
                context="Use deterministic source fixtures; do not alter the recipient.",
                constraints=[
                    "Keep the report concise.",
                    "Send only to recipient@example.com.",
                ],
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Produce a grounded report draft for review."],
                execution_intent=(
                    ExecutionIntent.EXECUTE_AFTER_GOVERNANCE
                    if with_email
                    else ExecutionIntent.PROPOSAL_ONLY
                ),
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.7,
                read_only_mode=False,
            ),
        ),
        metadata=IntakeMetadata(
            request_id="latest-ai-models-email-intake",
            parse_status=ParseStatus.COMPLETE,
            parser_name="deterministic_regression",
            prompt_version="v1",
            schema_version="1.0.0",
        ),
    )


class _ResearchEmailPlanner(PlannerPort):
    planner_name = "latest_ai_models_planner"
    prompt_version = "v1"

    # Keep planning semantic: evidence gathering then local report/draft production.
    def plan(self, *, task_state) -> PlannerDecision:
        return PlannerDecision(
            plan_steps=[
                PlanStep(
                    step_id="research",
                    objective="Collect source-backed facts about current AI models.",
                    rationale="The report must be grounded in observed sources.",
                    expected_outcome="A concise source-backed report.",
                ),
                PlanStep(
                    step_id="email",
                    objective="Prepare the requested email draft containing the report.",
                    rationale="The requested operation is reviewable communication, not direct sending.",
                    expected_outcome="A typed email proposal for human review.",
                ),
            ],
            rationale="Research first, then prepare the governed communication draft.",
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


class _OneStepPlanner(PlannerPort):
    planner_name = "missing_evidence_planner"
    prompt_version = "v1"

    # Keep the missing-evidence case to one semantic step.
    def plan(self, *, task_state) -> PlannerDecision:
        return PlannerDecision(
            plan_steps=[
                PlanStep(
                    step_id="step_1",
                    objective="Produce a grounded local answer.",
                    rationale="The answer needs usable observed evidence.",
                    expected_outcome="A bounded answer.",
                )
            ],
            rationale="One bounded completion step.",
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


class _NoEvidenceActor(ActorPort):
    actor_name = "missing_evidence_actor"
    prompt_version = "v1"

    # First expose invalid evidence; on recovery expose a typed block for HITL.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        if actor_task.actor_turn_count == 1:
            return ActorResult(
                plan_step_id=actor_task.current_plan_step.step_id,
                result_type=ActorResultType.STEP_COMPLETED,
                summary="Completion attempted without usable evidence.",
                output_text="Unsupported answer.",
                step_validation=StepValidation(
                    plan_step_id=actor_task.current_plan_step.step_id,
                    actor_self_confidence=0.3,
                    evidence_refs=("missing-evidence-ref",),
                    rationale_summary="No usable source was available.",
                ),
            )
        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.BLOCKED,
            summary="No usable evidence remains after recovery.",
            blocking_reason="No usable evidence remains after the bounded recovery attempt.",
        )


class _ResearchEmailActor(ActorPort):
    actor_name = "latest_ai_models_actor"
    prompt_version = "v1"

    # Produce distinct read-only requests before completing the research step.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        step_id = actor_task.current_plan_step.step_id
        if step_id in {"research", "step_1"}:
            turn = actor_task.actor_turn_count
            if turn == 1:
                return ActorResult(
                    plan_step_id=step_id,
                    result_type=ActorResultType.PROPOSE_ACTION,
                    summary="Search for current model facts and sources.",
                    tool_request=ToolRequest(
                        plan_step_id=step_id,
                        tool_name=ToolName.WEB_SEARCH,
                        reason="Collect provider-cited model facts.",
                        query="latest AI models dates links",
                    ),
                )
            paths = ("forbidden", "large", "alternative")
            if turn in {2, 3, 4}:
                return ActorResult(
                    plan_step_id=step_id,
                    result_type=ActorResultType.PROPOSE_ACTION,
                    summary="Attempt one bounded extraction path.",
                    tool_request=ToolRequest(
                        plan_step_id=step_id,
                        tool_name=ToolName.FETCH_URL_TEXT,
                        reason="Try a source extraction path after search.",
                        url=f"https://sources.example/{paths[turn - 2]}",
                    ),
                )
            refs = tuple(item.observation_id for item in actor_task.observations)
            report = (
                "Report:\n"
                "- OpenAI: current model release documented in the official model catalog "
                "(https://openai.com/models).\n"
                "- Anthropic: current Claude release documented in the official model overview "
                "(https://anthropic.com/claude).\n"
                "- Google: current Gemini release documented in the official Gemini models page "
                "(https://ai.google.dev/gemini-api/docs/models).\n"
                "Extraction limits encountered on secondary fetch paths are recorded."
            )
            return ActorResult(
                plan_step_id=step_id,
                result_type=ActorResultType.STEP_COMPLETED,
                summary="A concise report was prepared from available citations.",
                output_text=report,
                step_validation=StepValidation(
                    plan_step_id=step_id,
                    actor_self_confidence=0.78,
                    evidence_refs=refs,
                    rationale_summary="The report uses the provider-cited source evidence and records extraction limits.",
                ),
            )

        draft = ExternalCommunicationDraft(
            channel="email",
            recipient="recipient@example.com",
            subject="Latest AI models report",
            body=(
                "Report:\n"
                "- OpenAI: current model release documented at https://openai.com/models.\n"
                "- Anthropic: current Claude release documented at https://anthropic.com/claude.\n"
                "- Google: current Gemini release documented at "
                "https://ai.google.dev/gemini-api/docs/models.\n"
                "Extraction limits on secondary paths are noted."
            ),
        )
        proposal = normalize_communication_operation(
            operation_id="latest-ai-models-email-operation",
            plan_step_id=step_id,
            capability_id="communication.email.send",
            draft=draft,
            execution_candidate=True,
        )
        return ActorResult(
            plan_step_id=step_id,
            result_type=ActorResultType.STEP_COMPLETED,
            summary="The report was inserted into the requested email draft.",
            capability_mode=CapabilityAccessMode.WRITE,
            external_impact=True,
            output_text=draft.body,
            external_communication_draft=draft,
            external_operation_proposal=proposal,
            step_validation=StepValidation(
                plan_step_id=step_id,
                actor_self_confidence=0.74,
                evidence_refs=tuple(
                    item.observation_id for item in actor_task.observations
                ),
                rationale_summary="The draft preserves the requested recipient and contains the grounded report.",
            ),
        )


class _ResearchEmailToolGateway(ToolGatewayPort):
    gateway_name = "latest_ai_models_tool_gateway"

    # Return provider-cited summaries and typed deterministic extraction failures.
    def execute(self, *, tool_request: ToolRequest) -> ToolExecutionResult:
        if tool_request.tool_name == ToolName.WEB_SEARCH:
            return ToolExecutionResult(
                request=tool_request,
                success=True,
                summary="Provider-cited search summary.",
                content="Official provider model pages returned concrete release information.",
                observations=(
                    ToolObservation(
                        observation_id=f"{tool_request.request_id}:openai",
                        locator="https://models.example/registry",
                        content="OpenAI model catalog: current model release and capabilities.",
                    ),
                    ToolObservation(
                        observation_id=f"{tool_request.request_id}:anthropic",
                        locator="https://models.example/releases",
                        content="Anthropic Claude overview: current model release and capabilities.",
                    ),
                    ToolObservation(
                        observation_id=f"{tool_request.request_id}:google",
                        locator="https://ai.google.dev/gemini-api/docs/models",
                        content="Google Gemini models page: current model release and capabilities.",
                    ),
                ),
            )
        error_code = (
            ToolErrorCode.FORBIDDEN
            if tool_request.url and tool_request.url.endswith("forbidden")
            else ToolErrorCode.BYTE_LIMIT
        )
        return ToolExecutionResult(
            request=tool_request,
            success=False,
            summary="Extraction was limited.",
            error_message="The deterministic source cannot be fully extracted.",
            error_code=error_code,
            extractability_limited=True,
        )


class _ReviewExternalPolicy(StubPolicyEngine):
    # Let the normal OPA-shaped fixture evaluate facts, then require HITL for the exact draft.
    def evaluate_decision(self, *, decision_input):
        operation = decision_input["base_facts"].get("external_operation", {})
        if (
            operation.get("execution_candidate") is True
            and decision_input["base_facts"]["actor"].get("result_type")
            == ActorResultType.STEP_COMPLETED.value
            and operation.get("payload_digest")
        ):
            return _build_stub_opa_decision(
                decision_input=decision_input,
                policy_engine=self,
                directive="wait_human_review",
                policy_decision="require_human_review",
                reason_code="human_review_required",
                summary="The exact governed email draft requires human review.",
            )
        return super().evaluate_decision(decision_input=decision_input)


class _SafeDigestJudge(JudgePort):
    judge_name = "latest_ai_models_safe_judge"
    prompt_version = "v1"

    # Return a safe verdict bound to the exact operation digest supplied by governance.
    def evaluate(self, *, context) -> JudgeAssessment:
        return JudgeAssessment(
            invoked=True,
            recommendation=JudgeRecommendedDirective.CONTINUE,
            confidence=0.9,
            summary="The report draft is safe for human review.",
            rationale="The typed proposal preserves the requested recipient and bounded body.",
            flags=(),
            evaluated_target=context.external_operation.operation_id,
            status=JudgeAssessmentStatus.ACTIVE,
            verdict=JudgeVerdict.SAFE,
            severity=JudgeSeverity.LOW,
            evaluated_operation_digest=context.external_operation.payload_digest,
            evaluated_evidence_snapshot_digest=context.evidence_snapshot_digest,
            evaluated_evidence_selection_digest=context.evidence_selection_digest,
            prompt_version=self.prompt_version,
        )


if __name__ == "__main__":
    unittest.main()
