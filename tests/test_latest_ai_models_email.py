from __future__ import annotations

import unittest
import sys
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from layer1_intake import (
    BudgetConfig,
    DomainType,
    ExecutionIntent,
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    RequestedOperation,
    RequestedOperationType,
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
from layer2_runtime_governance.behavioral import (
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
from layer2_runtime_governance.external_operations import normalize_communication_operation
from layer2_runtime_governance.governance_components import (
    GovernanceDirective,
    PolicyEnginePort,
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
from layer2_runtime_governance.tool_contract import (
    CitationAlignment,
    DocumentType,
    EvidenceContentState,
    Extractability,
    SourceAuthority,
    ToolErrorCode,
    ToolExecutionResult,
    ToolGatewayPort,
    ToolName,
    ToolRequest,
    ToolSource,
)
from layer2_runtime_governance.graph import build_runtime_graph
from layer2_runtime_governance.infrastructure.checkpointing import build_runtime_serializer
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
        graph = build_runtime_graph(
            planner_service=_ResearchEmailPlanner(),
            actor_service=_ResearchEmailActor(),
            tool_gateway_service=_ResearchEmailToolGateway(),
            policy_engine_service=_ReviewExternalPolicy(),
            judge_service=_SafeDigestJudge(),
            behavioral_store_service=_EmptyBehavioralStore(),
            checkpointer=InMemorySaver(serde=build_runtime_serializer()),
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
        self.assertIn("report", state.external_communication.draft.body.lower())
        self.assertEqual(state.external_communication.draft.recipient, "recipient@example.com")
        self.assertIsNotNone(state.external_operation_proposal)
        self.assertTrue(state.external_operation_proposal.execution_candidate)
        self.assertIsNotNone(state.pending_human_review_request)
        self.assertEqual(
            state.pending_human_review_request.operation_payload_digest,
            state.external_operation_proposal.payload_digest,
        )
        self.assertEqual(state.actor_result.result_type, ActorResultType.STEP_COMPLETED)
        self.assertEqual(len(state.executed_steps), 2)
        self.assertFalse(any(item.directive == GovernanceDirective.STOP for item in state.governance_history))
        self.assertIn(RuntimeState.WAITING_HUMAN_REVIEW, [item.runtime_state for item in state.step_history])
        self.assertTrue(any(item.request.tool_name == ToolName.WEB_SEARCH for item in state.tool_results_history))
        fetch_errors = [
            item.error_code
            for item in state.tool_results_history
            if item.request.tool_name == ToolName.FETCH_URL_TEXT
        ]
        self.assertEqual(fetch_errors, [ToolErrorCode.FORBIDDEN, ToolErrorCode.BYTE_LIMIT, ToolErrorCode.CIRCUIT_OPEN])
        self.assertTrue(state.runtime_flags)
        self.assertIn("provider_cited_summary", {item.content_state.value for item in state.observed_evidence})
        self.assertTrue(state.last_judge_assessment.invoked)
        self.assertEqual(
            state.last_judge_assessment.evaluated_operation_digest,
            state.external_operation_proposal.payload_digest,
        )

    # A completion with unusable evidence must recover into typed review, not denial or a loop.
    def test_missing_usable_evidence_stops_in_review_without_hard_deny(self) -> None:
        graph = build_runtime_graph(
            planner_service=_OneStepPlanner(),
            actor_service=_NoEvidenceActor(),
            tool_gateway_service=_ResearchEmailToolGateway(),
            policy_engine_service=StubPolicyEngine(),
            behavioral_store_service=_EmptyBehavioralStore(),
            checkpointer=InMemorySaver(serde=build_runtime_serializer()),
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
                constraints=["Keep the report concise."],
                domain_type=DomainType.RESEARCH,
                domain_label="latest_ai_models",
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Produce a grounded report draft for review."],
                execution_intent=ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
                requested_operations=(
                    [
                        RequestedOperation(
                            operation_type=RequestedOperationType.EXTERNAL_COMMUNICATION,
                            channel="email",
                            recipient="recipient@example.com",
                        )
                    ]
                    if with_email
                    else []
                ),
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.7,
                budget=BudgetConfig(max_steps=None, max_tool_calls=None),
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
            refs = tuple(item.evidence_id for item in actor_task.observed_evidence)
            return ActorResult(
                plan_step_id=step_id,
                result_type=ActorResultType.STEP_COMPLETED,
                summary="A concise report was prepared from available citations.",
                output_text="Report: current AI model names and dates are listed with linked sources; extraction limits are noted.",
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
            body="Report: current AI model names and dates are listed with linked sources; extraction limits are noted.",
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
                evidence_refs=tuple(item.evidence_id for item in actor_task.observed_evidence),
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
                content="Aggregate provider answer with model dates and links.",
                sources=(
                    ToolSource(
                        title="Model registry",
                        locator="https://models.example/registry",
                        snippet=None,
                        content_state=EvidenceContentState.PROVIDER_CITED_SUMMARY,
                        source_authority=SourceAuthority.PRIMARY,
                        document_type=DocumentType.DOCUMENTATION,
                        extractability=Extractability.COMPLETE,
                        citation_alignment=CitationAlignment.PROVIDER_ATTRIBUTED,
                    ),
                    ToolSource(
                        title="Release archive",
                        locator="https://models.example/releases",
                        snippet=None,
                        content_state=EvidenceContentState.PROVIDER_CITED_SUMMARY,
                        source_authority=SourceAuthority.SECONDARY,
                        document_type=DocumentType.UNKNOWN,
                        extractability=Extractability.COMPLETE,
                        citation_alignment=CitationAlignment.EXACT,
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
            prompt_version=self.prompt_version,
        )


if __name__ == "__main__":
    unittest.main()
