from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from tests.layer2_runtime_fixtures import (
    ActorPort,
    ActorResult,
    ActorResultType,
    CapabilityAccessMode,
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    JudgeAssessment,
    JudgeAssessmentStatus,
    JudgeRecommendedDirective,
    JudgeSeverity,
    JudgeVerdict,
    Layer2RuntimeDependencies,
    ParseStatus,
    PlanStep,
    PlannerDecision,
    SensitivityLevel,
    StaticJudge,
    StepValidation,
    StructuredTask,
    StubPolicyEngine,
    TaskSpec,
    build_runtime_serializer,
    compose_layer2_runtime,
    _build_stub_opa_decision,
)
from layer1_intake import (
    ExecutionIntent,
)
from layer2_runtime_governance.domain import (
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    HumanReviewDecision,
    HumanReviewDecisionType,
    RuntimeFlag,
    RuntimeState,
    StepCompletionCandidate,
    TaskState,
    build_evidence_selection_digest,
    build_evidence_snapshot_manifest,
    digest_json,
)
from layer3_tool_bus_external_capabilities.external_operations import (
    ExternalCapabilityDefinition,
    ExternalCapabilityRegistry,
    ExternalConnectorKind,
    ExternalExecutionAuthorizationSource,
    ExternalExecutionErrorCode,
    ExternalExecutionReceipt,
    ExternalExecutionStatus,
    ExternalMCPServerDefinition,
    ExternalOperationEffect,
    ExternalOperationFamily,
    IdempotencySupport,
    MCPTransportKind,
    OperationBoundary,
    build_external_execution_request,
    normalize_communication_operation,
)
from layer2_runtime_governance.governance_components import GovernanceReasonCode
from layer2_runtime_governance.hitl import build_human_review_request
from layer2_runtime_governance.runtime.external_execution_service import (
    external_execution,
)


class RecordingExternalExecutor:
    # The test port records calls while returning one configured typed outcome.
    def __init__(
        self,
        status: ExternalExecutionStatus = ExternalExecutionStatus.EXECUTED,
        *,
        retryable: bool = False,
    ) -> None:
        self.status = status
        self.retryable = retryable
        self.calls = []

    # Return an audit-safe receipt without performing an external side effect.
    def execute(self, request):
        self.calls.append(request)
        now = datetime.now(timezone.utc)
        return ExternalExecutionReceipt(
            operation_id=request.operation_id,
            capability_id=request.capability_id,
            payload_digest=request.current_payload_digest,
            status=self.status,
            idempotency_key=request.idempotency_key,
            started_at=now,
            completed_at=now,
            external_reference=(
                "capture://message-1"
                if self.status == ExternalExecutionStatus.EXECUTED
                else None
            ),
            summary=f"Fixture returned {self.status.value}.",
            error_code=(
                ExternalExecutionErrorCode.NONE
                if self.status == ExternalExecutionStatus.EXECUTED
                else ExternalExecutionErrorCode.OUTCOME_INDETERMINATE
                if self.status == ExternalExecutionStatus.INDETERMINATE
                else ExternalExecutionErrorCode.CONNECTOR_FAILURE
            ),
            retryable=self.retryable,
            connector_kind="mcp",
            connector_id="mcp://communication/send_email",
            server_id="communication",
            tool_name="send_email",
        )


class MismatchedReceiptExecutor(RecordingExternalExecutor):
    # Simulate a connector returning a receipt for the wrong request binding.
    def execute(self, request):
        receipt = super().execute(request)
        return replace(receipt, payload_digest="f" * 64)


class RaisingExternalExecutor(RecordingExternalExecutor):
    # Simulate a transport failure after the connector boundary was entered.
    def execute(self, request):
        self.calls.append(request)
        raise RuntimeError("fixture connector lost its response")


class OneStepExternalPlanner:
    planner_name = "external_fixture_planner"
    prompt_version = "external_fixture_planner.v1"

    # Produce one semantic communication step without embedding a connector call.
    def plan(self, *, task_state):
        return PlannerDecision(
            plan_steps=[
                PlanStep(
                    step_id="send_step",
                    objective="Prepare the requested email operation.",
                    rationale="The actor must produce one exact governed proposal.",
                    expected_outcome="A typed email proposal.",
                )
            ],
            rationale="One bounded external-operation step is sufficient.",
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


class ExternalEmailActor(ActorPort):
    actor_name = "external_email_fixture_actor"
    prompt_version = "external_email_fixture_actor.v1"

    # Produce a canonical proposal whose execution_candidate follows intake intent.
    def act(self, *, actor_task):
        step_id = actor_task.current_plan_step.step_id
        draft = ExternalCommunicationDraft(
            channel="email",
            recipient="capture@example.test",
            subject="Controlled integration",
            body="This message is captured by a test double.",
        )
        proposal = normalize_communication_operation(
            operation_id=f"{actor_task.intake_metadata.request_id}:{step_id}:email",
            plan_step_id=step_id,
            capability_id="communication.email.send",
            draft=draft,
            execution_candidate=(
                actor_task.task_spec.execution_intent
                == ExecutionIntent.EXECUTE_AFTER_GOVERNANCE
            ),
        )
        return ActorResult(
            plan_step_id=step_id,
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Prepared one exact governed email proposal.",
            capability_mode=CapabilityAccessMode.WRITE,
            external_impact=True,
            output_text=draft.body,
            external_communication_draft=draft,
            external_operation_proposal=proposal,
            step_validation=StepValidation(
                plan_step_id=step_id,
                actor_self_confidence=0.99,
                evidence_refs=tuple(
                    observation.observation_id for observation in actor_task.observations
                ),
                rationale_summary="The typed proposal is complete.",
            ),
        )


class ExternalReviewPolicy(StubPolicyEngine):
    policy_engine_name = "external_review_fixture_policy"

    # Route every governed execution candidate to digest-bound human review.
    def evaluate_decision(self, *, decision_input):
        operation = decision_input["base_facts"].get("external_operation", {})
        if operation.get("execution_candidate") is True:
            return _build_stub_opa_decision(
                decision_input=decision_input,
                policy_engine=self,
                directive="wait_human_review",
                policy_decision="require_human_review",
                reason_code="capability_approval_required",
                summary="The external proposal requires human authorization.",
            )
        return super().evaluate_decision(decision_input=decision_input)


# Build one explicit Layer 1 handoff for external execution tests.
def _intake(
    intent: ExecutionIntent,
    *,
    read_only: bool = False,
) -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Send a controlled integration email.",
                context="The recipient is a local capture transport.",
                constraints=["Use only the registered capability."],
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Return a typed receipt."],
                execution_intent=intent,
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.7,
                read_only_mode=read_only,
            ),
        ),
        metadata=IntakeMetadata(
            request_id=f"external-execution-{uuid4()}",
            parse_status=ParseStatus.COMPLETE,
            parser_name="external_execution_fixture",
            prompt_version="v1",
            schema_version="1.0.0",
        ),
    )


# Build the dedicated external registry without exposing the capability to the actor.
def _registry(
    *,
    enabled: bool = True,
    allowlisted: bool = True,
    max_attempts: int = 1,
) -> ExternalCapabilityRegistry:
    server = ExternalMCPServerDefinition(
        server_id="communication",
        enabled=enabled,
        transport=MCPTransportKind.STDIO,
        command=sys.executable,
        args=("-m", "layer3_tool_bus_external_capabilities.external_capability_mcp", "communication"),
        allowed_tools=("send_email",) if allowlisted else (),
        timeout_seconds=1,
    )
    capability = ExternalCapabilityDefinition(
        capability_id="communication.email.send",
        operation_family=ExternalOperationFamily.COMMUNICATION,
        normalized_effect=ExternalOperationEffect.SEND,
        operation_boundary=OperationBoundary.GOVERNED_EXTERNAL,
        enabled=enabled,
        connector_kind=ExternalConnectorKind.MCP,
        mcp_server_id="communication",
        mcp_tool_name="send_email",
        allowed_normalized_effects=(ExternalOperationEffect.SEND,),
        input_schema_version="communication.email.send.input.v1",
        input_schema={
            "required": ["recipient", "subject", "body", "idempotency_key"],
            "optional": ["cc", "bcc"],
        },
        output_schema_version="external_execution.tool_response.v1",
        timeout_seconds=1,
        idempotency_support=IdempotencySupport.CLIENT_KEY,
        max_attempts=max_attempts,
    )
    return ExternalCapabilityRegistry(
        registry_version="external_capabilities.test.v1",
        capabilities={capability.capability_id: capability},
        mcp_servers={server.server_id: server},
    )


# Build a pre-authorized state for deterministic application-service tests.
def _authorized_state(
    *,
    intent: ExecutionIntent = ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
    approved: bool = True,
    approved_digest: str | None = None,
    read_only: bool = False,
    capability_id: str = "communication.email.send",
) -> TaskState:
    state = TaskState.from_intake(_intake(intent, read_only=read_only))
    draft = ExternalCommunicationDraft(
        channel="email",
        recipient="capture@example.test",
        subject="Controlled",
        body="Captured only.",
    )
    proposal = normalize_communication_operation(
        operation_id="operation-1",
        plan_step_id="send_step",
        capability_id=capability_id,
        draft=draft,
        execution_candidate=intent == ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
    )
    authorized_digest = approved_digest or proposal.payload_digest
    state.plan_steps = [
        PlanStep(
            step_id="send_step",
            objective="Prepare the requested email operation.",
            rationale="The service fixture begins after governance authorization.",
            expected_outcome="A typed email proposal.",
        )
    ]
    state.current_plan_step_index = 0
    evidence_snapshot_digest = digest_json(
        build_evidence_snapshot_manifest(task_state=state)
    )
    state.pending_completion_candidate = StepCompletionCandidate(
        candidate_id="send_step:candidate:1",
        step_id="send_step",
        summary="Prepared one exact governed email proposal.",
        output_text=draft.body or proposal.payload_preview,
        external_operation_id=proposal.operation_id,
        external_operation_digest=proposal.payload_digest,
        evidence_snapshot_digest=evidence_snapshot_digest,
        evidence_selection_digest=build_evidence_selection_digest(
            selected_refs=(),
            evidence_snapshot_digest=evidence_snapshot_digest,
            external_operation_digest=proposal.payload_digest,
        ),
    )
    state.external_operation_proposal = proposal
    state.current_external_execution_request = build_external_execution_request(
        proposal=proposal,
        authorized_payload_digest=authorized_digest,
        execution_intent=intent,
        authorization_source=ExternalExecutionAuthorizationSource.HUMAN_REVIEW,
        reviewer_id="reviewer-1",
    )
    if approved:
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        review_request = replace(
            build_human_review_request(state=state, thread_id="service-fixture"),
            request_id="review-1",
            reason_codes=("capability_approval_required",),
            allowed_decisions=(
                HumanReviewDecisionType.APPROVE_CONTINUE,
                HumanReviewDecisionType.MODIFY_AND_CONTINUE,
                HumanReviewDecisionType.ABORT,
            ),
        )
        state.last_human_review_request = review_request
        state.human_review_history.append(
            HumanReviewDecision(
                request_id=review_request.request_id,
                decision_type=HumanReviewDecisionType.APPROVE_CONTINUE,
                reviewer_id="reviewer-1",
                approved_operation_digest=authorized_digest,
            )
        )
    state.current_state = RuntimeState.READY_EXTERNAL_EXECUTION
    return state


# Build the safe semantic judge required by executable governed proposals.
def _safe_judge() -> StaticJudge:
    return StaticJudge(
        JudgeAssessment(
            invoked=True,
            recommendation=JudgeRecommendedDirective.CONTINUE,
            confidence=0.99,
            summary="The exact fixture proposal is safe.",
            rationale="The target and content match the controlled test.",
            flags=(),
            evaluated_target="fixture",
            status=JudgeAssessmentStatus.ACTIVE,
            verdict=JudgeVerdict.SAFE,
            severity=JudgeSeverity.LOW,
        )
    )


class ExternalExecutionServiceTests(unittest.TestCase):
    # Default intent remains conservative for legacy Layer 1 payloads.
    def test_intent_defaults_to_proposal_only(self) -> None:
        self.assertEqual(TaskSpec(goal="Draft an email.").execution_intent, ExecutionIntent.PROPOSAL_ONLY)

    # Explicit execution intent survives the Layer 1 to TaskState projection.
    def test_execute_after_governance_intent_is_canonical(self) -> None:
        state = TaskState.from_intake(_intake(ExecutionIntent.EXECUTE_AFTER_GOVERNANCE))
        self.assertEqual(state.execution_intent, ExecutionIntent.EXECUTE_AFTER_GOVERNANCE)

    # An exact approved digest reaches the injected executor and completes.
    def test_exact_digest_executes_and_persists_receipt(self) -> None:
        state = _authorized_state()
        executor = RecordingExternalExecutor()
        result = external_execution(
            state,
            executor_service=executor,
            capability_registry=_registry(),
            thread_id="service-success",
        )
        self.assertEqual(result.current_state, RuntimeState.COMPLETED)
        self.assertEqual(result.last_external_execution_receipt.status, ExternalExecutionStatus.EXECUTED)
        self.assertEqual(len(executor.calls), 1)

    # Digest drift is rejected before a connector observes the request.
    def test_digest_mismatch_is_rejected_before_execution(self) -> None:
        state = _authorized_state(approved_digest="0" * 64)
        executor = RecordingExternalExecutor()
        result = external_execution(
            state,
            executor_service=executor,
            capability_registry=_registry(),
            thread_id="service-digest-mismatch",
        )
        self.assertEqual(result.last_external_execution_receipt.error_code, ExternalExecutionErrorCode.DIGEST_MISMATCH)
        self.assertEqual(executor.calls, [])

    # A proposal cannot reach the connector with an unsealed completion candidate.
    def test_unbound_completion_candidate_is_rejected_before_execution(self) -> None:
        state = _authorized_state()
        state.pending_completion_candidate = replace(
            state.pending_completion_candidate,
            external_operation_id=None,
            external_operation_digest=None,
        )
        executor = RecordingExternalExecutor()

        result = external_execution(
            state,
            executor_service=executor,
            capability_registry=_registry(),
            thread_id="service-unbound-candidate",
        )

        self.assertEqual(
            result.last_external_execution_receipt.error_code,
            ExternalExecutionErrorCode.DIGEST_MISMATCH,
        )
        self.assertEqual(executor.calls, [])

    # A malformed post-call receipt becomes indeterminate instead of crashing state commit.
    def test_mismatched_connector_receipt_routes_to_reconciliation(self) -> None:
        state = _authorized_state()
        executor = MismatchedReceiptExecutor()

        result = external_execution(
            state,
            executor_service=executor,
            capability_registry=_registry(),
            thread_id="service-mismatched-receipt",
        )

        self.assertEqual(
            result.last_external_execution_receipt.status,
            ExternalExecutionStatus.INDETERMINATE,
        )
        self.assertEqual(
            result.current_state,
            RuntimeState.WAITING_HUMAN_REVIEW,
        )
        self.assertIn(
            RuntimeFlag.EXTERNAL_EXECUTION_RECONCILIATION_REQUIRED,
            result.runtime_flags,
        )

    # A connector exception after invocation is an unknown outcome, never a lost checkpoint.
    def test_connector_exception_routes_to_reconciliation(self) -> None:
        state = _authorized_state()
        executor = RaisingExternalExecutor()

        result = external_execution(
            state,
            executor_service=executor,
            capability_registry=_registry(),
            thread_id="service-connector-exception",
        )

        self.assertEqual(
            result.last_external_execution_receipt.status,
            ExternalExecutionStatus.INDETERMINATE,
        )
        self.assertEqual(result.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertEqual(len(executor.calls), 1)

    # A request without an approve decision never invokes the adapter.
    def test_missing_approval_is_rejected(self) -> None:
        state = _authorized_state(approved=False)
        executor = RecordingExternalExecutor()
        result = external_execution(
            state,
            executor_service=executor,
            capability_registry=_registry(),
            thread_id="service-no-approval",
        )
        self.assertEqual(result.last_external_execution_receipt.error_code, ExternalExecutionErrorCode.AUTHORIZATION_MISSING)
        self.assertEqual(executor.calls, [])

    # Unknown and disabled capabilities remain fail-closed.
    def test_unknown_and_disabled_capabilities_are_rejected(self) -> None:
        unknown_state = _authorized_state(capability_id="communication.unknown.send")
        unknown_executor = RecordingExternalExecutor()
        unknown = external_execution(
            unknown_state,
            executor_service=unknown_executor,
            capability_registry=_registry(),
            thread_id="service-unknown",
        )
        self.assertEqual(unknown.last_external_execution_receipt.error_code, ExternalExecutionErrorCode.CAPABILITY_UNKNOWN)
        disabled_state = _authorized_state()
        disabled_executor = RecordingExternalExecutor()
        disabled = external_execution(
            disabled_state,
            executor_service=disabled_executor,
            capability_registry=_registry(enabled=False),
            thread_id="service-disabled",
        )
        self.assertEqual(disabled.last_external_execution_receipt.error_code, ExternalExecutionErrorCode.CAPABILITY_DISABLED)
        self.assertFalse(unknown_executor.calls or disabled_executor.calls)

    # The read-only envelope blocks a send even after a digest approval.
    def test_read_only_violation_is_rejected(self) -> None:
        state = _authorized_state(read_only=True)
        executor = RecordingExternalExecutor()
        result = external_execution(
            state,
            executor_service=executor,
            capability_registry=_registry(),
            thread_id="service-read-only",
        )
        self.assertEqual(result.last_external_execution_receipt.error_code, ExternalExecutionErrorCode.READ_ONLY_VIOLATION)
        self.assertEqual(executor.calls, [])

    # Non-allowlisted MCP tools are blocked at application preflight.
    def test_non_allowlisted_tool_is_rejected(self) -> None:
        state = _authorized_state()
        executor = RecordingExternalExecutor()
        result = external_execution(
            state,
            executor_service=executor,
            capability_registry=_registry(allowlisted=False),
            thread_id="service-tool-not-allowlisted",
        )
        self.assertEqual(result.last_external_execution_receipt.error_code, ExternalExecutionErrorCode.TOOL_NOT_ALLOWLISTED)
        self.assertEqual(executor.calls, [])

    # Deterministic failure terminates without an uncontrolled retry loop.
    def test_failed_receipt_aborts(self) -> None:
        state = _authorized_state()
        executor = RecordingExternalExecutor(ExternalExecutionStatus.FAILED)
        result = external_execution(
            state,
            executor_service=executor,
            capability_registry=_registry(),
            thread_id="service-failed",
        )
        self.assertEqual(result.current_state, RuntimeState.ABORTED)
        self.assertEqual(len(executor.calls), 1)

    # Retryable failure creates at most one fresh stable attempt scope.
    def test_retryable_failure_has_a_bounded_idempotent_retry(self) -> None:
        state = _authorized_state()
        executor = RecordingExternalExecutor(
            ExternalExecutionStatus.FAILED,
            retryable=True,
        )
        first = external_execution(
            state,
            executor_service=executor,
            capability_registry=_registry(max_attempts=2),
            thread_id="service-retry",
        )
        first_key = executor.calls[0].idempotency_key
        self.assertEqual(first.current_state, RuntimeState.READY_EXTERNAL_EXECUTION)
        second = external_execution(
            first,
            executor_service=executor,
            capability_registry=_registry(max_attempts=2),
            thread_id="service-retry",
        )
        self.assertEqual(second.current_state, RuntimeState.ABORTED)
        self.assertEqual(len(executor.calls), 2)
        self.assertNotEqual(first_key, executor.calls[1].idempotency_key)

    # Indeterminate outcomes pause for reconciliation and never auto-retry.
    def test_indeterminate_routes_to_human_reconciliation_without_retry(self) -> None:
        state = _authorized_state()
        executor = RecordingExternalExecutor(ExternalExecutionStatus.INDETERMINATE)
        result = external_execution(
            state,
            executor_service=executor,
            capability_registry=_registry(max_attempts=2),
            thread_id="service-indeterminate",
        )
        self.assertEqual(result.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertIsNotNone(result.pending_human_review_request)
        self.assertEqual(len(executor.calls), 1)

    # A persisted executed receipt suppresses repeated connector invocation after resume.
    def test_duplicate_executed_idempotency_key_reuses_receipt(self) -> None:
        state = _authorized_state()
        prior_executor = RecordingExternalExecutor()
        completed = external_execution(
            state,
            executor_service=prior_executor,
            capability_registry=_registry(),
            thread_id="service-duplicate",
        )
        request = prior_executor.calls[0]
        completed.current_external_execution_request = request
        completed.current_state = RuntimeState.READY_EXTERNAL_EXECUTION
        replay_executor = RecordingExternalExecutor()
        replay = external_execution(
            completed,
            executor_service=replay_executor,
            capability_registry=_registry(),
            thread_id="service-duplicate",
        )
        self.assertEqual(replay.current_state, RuntimeState.COMPLETED)
        self.assertEqual(replay_executor.calls, [])


class ExternalExecutionGraphTests(unittest.TestCase):
    # Build a graph using only deterministic local ports.
    def _graph(self, *, executor, policy_engine=None, registry=None):
        return compose_layer2_runtime(
            Layer2RuntimeDependencies(
                planner=OneStepExternalPlanner(),
                actor=ExternalEmailActor(),
                policy_engine=policy_engine or StubPolicyEngine(),
                judge=_safe_judge(),
                external_operation_executor=executor,
                external_capability_registry=registry or _registry(),
                checkpointer=InMemorySaver(serde=build_runtime_serializer()),
            )
        )

    # Proposal-only approval is terminal and cannot reach the executor.
    def test_draft_only_completes_as_approved_draft_without_executor(self) -> None:
        executor = RecordingExternalExecutor()
        result = self._graph(executor=executor).invoke(
            {"intake_result": _intake(ExecutionIntent.PROPOSAL_ONLY)},
            config={"configurable": {"thread_id": f"draft-only-{uuid4()}"}},
        )
        state = result["task_state"]
        self.assertEqual(state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(state.external_communication.phase, ExternalCommunicationPhase.APPROVED_DRAFT)
        self.assertEqual(executor.calls, [])

    # A safe OPA-authorized exact digest routes through external_execution.
    def test_safe_external_candidate_executes(self) -> None:
        executor = RecordingExternalExecutor()
        result = self._graph(executor=executor).invoke(
            {"intake_result": _intake(ExecutionIntent.EXECUTE_AFTER_GOVERNANCE)},
            config={"configurable": {"thread_id": f"safe-external-{uuid4()}"}},
        )
        state = result["task_state"]
        self.assertEqual(state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(state.last_external_execution_receipt.status, ExternalExecutionStatus.EXECUTED)
        self.assertEqual(len(executor.calls), 1)

    # A review directive pauses, then exact human approval executes once.
    def test_hitl_approve_routes_to_external_execution(self) -> None:
        executor = RecordingExternalExecutor()
        graph = self._graph(
            executor=executor,
            policy_engine=ExternalReviewPolicy(),
        )
        thread_id = f"review-external-{uuid4()}"
        paused = graph.invoke(
            {"intake_result": _intake(ExecutionIntent.EXECUTE_AFTER_GOVERNANCE)},
            config={"configurable": {"thread_id": thread_id}},
        )
        pending = paused["task_state"].pending_human_review_request
        self.assertIsNotNone(pending)
        resumed = graph.invoke(
            Command(
                resume={
                    "request_id": pending.request_id,
                    "decision_type": "approve_continue",
                    "reviewer_id": "operator-1",
                    "approved_operation_digest": pending.operation_payload_digest,
                }
            ),
            config={"configurable": {"thread_id": thread_id}},
        )
        self.assertEqual(resumed["task_state"].current_state, RuntimeState.COMPLETED)
        self.assertEqual(
            resumed["task_state"].last_external_execution_receipt.status,
            ExternalExecutionStatus.EXECUTED,
        )
        self.assertEqual(len(executor.calls), 1)

    # A human edit creates a new digest and forces a second governance pass.
    def test_hitl_modify_creates_new_digest_before_later_execution(self) -> None:
        executor = RecordingExternalExecutor()
        graph = self._graph(
            executor=executor,
            policy_engine=ExternalReviewPolicy(),
        )
        thread_id = f"modify-external-{uuid4()}"
        first = graph.invoke(
            {"intake_result": _intake(ExecutionIntent.EXECUTE_AFTER_GOVERNANCE)},
            config={"configurable": {"thread_id": thread_id}},
        )
        first_request = first["task_state"].pending_human_review_request
        first_digest = first_request.operation_payload_digest
        first_candidate = first["task_state"].pending_completion_candidate
        self.assertIsNotNone(first_candidate.evidence_snapshot_digest)
        self.assertIsNotNone(first_candidate.evidence_selection_digest)
        second = graph.invoke(
            Command(
                resume={
                    "request_id": first_request.request_id,
                    "decision_type": "modify_and_continue",
                    "reviewer_id": "operator-1",
                    "external_communication_edit": {
                        "note": "Use the reviewed subject.",
                        "draft": {
                            "channel": "email",
                            "recipient": "capture@example.test",
                            "subject": "Reviewed controlled integration",
                            "body": "This edited message remains in the capture transport.",
                            "metadata": {},
                        },
                    },
                }
            ),
            config={"configurable": {"thread_id": thread_id}},
        )
        second_request = second["task_state"].pending_human_review_request
        second_candidate = second["task_state"].pending_completion_candidate
        self.assertNotEqual(first_digest, second_request.operation_payload_digest)
        self.assertTrue(second["task_state"].last_judge_assessment.invoked)
        self.assertNotIn(
            GovernanceReasonCode.REPLAN_REQUEST_STALLED,
            second["task_state"].last_governance_decision.reason_codes,
        )
        self.assertEqual(
            first_candidate.evidence_snapshot_digest,
            second_candidate.evidence_snapshot_digest,
        )
        self.assertNotEqual(
            first_candidate.evidence_selection_digest,
            second_candidate.evidence_selection_digest,
        )
        self.assertEqual(
            second_candidate.external_operation_digest,
            second_request.operation_payload_digest,
        )
        final = graph.invoke(
            Command(
                resume={
                    "request_id": second_request.request_id,
                    "decision_type": "approve_continue",
                    "approved_operation_digest": second_request.operation_payload_digest,
                    "reviewer_id": "operator-2",
                }
            ),
            config={"configurable": {"thread_id": thread_id}},
        )
        self.assertEqual(final["task_state"].current_state, RuntimeState.COMPLETED)
        self.assertEqual(executor.calls[0].current_payload_digest, second_request.operation_payload_digest)

    # An indeterminate graph receipt pauses and approval only reconciles it.
    def test_indeterminate_graph_outcome_is_not_retried(self) -> None:
        executor = RecordingExternalExecutor(ExternalExecutionStatus.INDETERMINATE)
        graph = self._graph(executor=executor)
        thread_id = f"indeterminate-external-{uuid4()}"
        paused = graph.invoke(
            {"intake_result": _intake(ExecutionIntent.EXECUTE_AFTER_GOVERNANCE)},
            config={"configurable": {"thread_id": thread_id}},
        )
        state = paused["task_state"]
        self.assertEqual(state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertEqual(len(executor.calls), 1)
        pending = state.pending_human_review_request
        reconciled = graph.invoke(
            Command(
                resume={
                    "request_id": pending.request_id,
                    "decision_type": "approve_continue",
                    "approved_operation_digest": pending.operation_payload_digest,
                    "reviewer_id": "reconciler",
                }
            ),
            config={"configurable": {"thread_id": thread_id}},
        )
        self.assertEqual(reconciled["task_state"].current_state, RuntimeState.COMPLETED)
        self.assertEqual(
            reconciled["task_state"].last_external_execution_receipt.status,
            ExternalExecutionStatus.INDETERMINATE,
        )
        self.assertEqual(len(executor.calls), 1)

    # A graph with a disabled capability records rejection without side effects.
    def test_unsupported_graph_capability_has_no_effect(self) -> None:
        executor = RecordingExternalExecutor()
        result = self._graph(
            executor=executor,
            registry=_registry(enabled=False),
        ).invoke(
            {"intake_result": _intake(ExecutionIntent.EXECUTE_AFTER_GOVERNANCE)},
            config={"configurable": {"thread_id": f"unsupported-{uuid4()}"}},
        )
        state = result["task_state"]
        self.assertEqual(state.current_state, RuntimeState.ABORTED)
        self.assertEqual(
            state.last_external_execution_receipt.status,
            ExternalExecutionStatus.REJECTED_BEFORE_EXECUTION,
        )
        self.assertEqual(executor.calls, [])

    # Static read-only denial prevents both HITL approval and connector invocation.
    def test_static_deny_never_invokes_executor(self) -> None:
        executor = RecordingExternalExecutor()
        result = self._graph(executor=executor).invoke(
            {
                "intake_result": _intake(
                    ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
                    read_only=True,
                )
            },
            config={"configurable": {"thread_id": f"deny-external-{uuid4()}"}},
        )
        self.assertEqual(result["task_state"].current_state, RuntimeState.ABORTED)
        self.assertEqual(executor.calls, [])


if __name__ == "__main__":
    unittest.main()
