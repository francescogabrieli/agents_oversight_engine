from __future__ import annotations

import json
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from tests.layer2_runtime_fixtures import *

from layer2_runtime_governance.external_operations import (
    DatabaseOperationPayload,
    DeferredExternalOperationExecutor,
    DestinationClass,
    ExternalOperationEffect,
    ExternalOperationPayloadEdit,
    OperationBoundary,
    SQLStatementClass,
    normalize_communication_operation,
    normalize_generic_external_operation,
    normalize_sql_operation,
)
from layer2_runtime_governance.external_operations.runtime_projection import (
    prepare_external_operation_proposal,
)
from layer2_runtime_governance.domain import normalize_task_state_serialization
from layer2_runtime_governance.hitl.dashboard import (
    build_governance_hitl_dashboard_snapshot,
)
from layer2_runtime_governance.judge import (
    DisabledJudge,
    JudgeAssessmentStatus,
    JudgeSeverity,
    JudgeVerdict,
)
from layer2_runtime_governance.judge.service import _JudgePayload
from layer2_runtime_governance.execution.result_builder import (
    convert_tool_call_to_actor_result,
)
from layer2_runtime_governance.runtime.actor_service import actor
from layer2_runtime_governance.tools import build_default_tool_gateway_config


class ExternalOperationNormalizerTests(unittest.TestCase):
    # OpenAI structured output must constrain semantic labels before service parsing.
    def test_judge_payload_schema_rejects_free_form_finding_categories(self) -> None:
        payload = {
            "verdict": "unsafe",
            "severity": "high",
            "confidence": 0.91,
            "findings": [
                {
                    "category": "Coercive / threatening tone",
                    "code": "coercive_tone",
                    "severity": "high",
                    "summary": "The message uses coercive language.",
                    "evidence_refs": [],
                }
            ],
            "summary": "Unsafe external communication.",
            "rationale": "The content is coercive.",
            "evaluated_operation_digest": "a" * 64,
        }

        with self.assertRaises(ValidationError):
            _JudgePayload.model_validate(payload)

        payload["findings"][0]["category"] = "deception_or_coercion"
        parsed = _JudgePayload.model_validate(payload)
        self.assertEqual(
            parsed.findings[0].category,
            JudgeFindingCategory.DECEPTION_OR_COERCION,
        )

    # Typed actor drafts preserve routing fields through runtime normalization.
    def test_typed_actor_email_draft_preserves_recipient_and_subject(self) -> None:
        state = TaskState.from_intake(_build_external_communication_intake_result())
        state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Produce the requested complete email draft.",
                rationale="The intake already contains the necessary facts.",
                expected_outcome="A typed external email draft.",
                step_kind=PlanStepKind.FINAL_SYNTHESIS,
                evidence_requirement=EvidenceSupportLevel.LOCAL_ONLY,
                claim_precision_ceiling=EvidenceSupportLevel.LOCAL_ONLY,
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        state.current_plan_step_index = 0
        actor_task = build_actor_task(task_state=state)
        result = convert_tool_call_to_actor_result(
            actor_task=actor_task,
            tool_call=SimpleNamespace(
                name="emit_external_communication_draft",
                arguments=json.dumps(
                    {
                        "summary": "Produced the requested email draft.",
                        "channel": "email",
                        "recipient": "vendor@example.com",
                        "subject": "Service issue",
                        "body": "Please address the service issue.",
                        "completion_rationale": "The local drafting objective is complete.",
                        "objective_satisfied": True,
                        "evidence_sufficient": True,
                        "confidence": 0.95,
                        "residual_uncertainty": "",
                    }
                ),
            ),
        )

        actor(state, actor_service=_SingleResultActor(result))
        proposal = prepare_external_operation_proposal(
            task_state=state,
            tool_gateway_config=build_default_tool_gateway_config(),
        )

        self.assertEqual(state.external_communication.draft.channel, "email")
        self.assertEqual(
            state.external_communication.draft.recipient,
            "vendor@example.com",
        )
        self.assertEqual(
            state.external_communication.draft.subject,
            "Service issue",
        )
        self.assertEqual(proposal.target.kind, "recipient")
        self.assertEqual(proposal.target.value, "vendor@example.com")
        self.assertEqual(
            proposal.target.destination_class,
            DestinationClass.EXTERNAL,
        )
    # SQL classification must follow the parsed root AST across comments, casing, and CTEs.
    def test_sql_select_variants_are_ast_classified_as_read(self) -> None:
        queries = (
            "/* audit */ SeLeCt id FROM accounts",
            "WITH active AS (SELECT id FROM accounts) SELECT id FROM active",
            "-- read only\nSELECT count(*) FROM accounts",
        )

        for index, query in enumerate(queries):
            proposal = _sql_proposal(query, suffix=str(index))
            self.assertEqual(proposal.payload.statement_class, SQLStatementClass.SELECT)
            self.assertEqual(proposal.normalized_effect, ExternalOperationEffect.READ)
            self.assertFalse(proposal.payload.ambiguous)

    # Mutating statements and multi-statement payloads retain conservative parser facts.
    def test_sql_mutations_and_multiple_statements_are_conservative(self) -> None:
        expected = {
            "UPDATE accounts SET enabled = false": (
                SQLStatementClass.UPDATE,
                ExternalOperationEffect.UPDATE,
            ),
            "DELETE FROM accounts WHERE id = 1": (
                SQLStatementClass.DELETE,
                ExternalOperationEffect.DELETE,
            ),
            "TRUNCATE TABLE accounts": (
                SQLStatementClass.TRUNCATE,
                ExternalOperationEffect.TRUNCATE,
            ),
            "SELECT id INTO archived_accounts FROM accounts": (
                SQLStatementClass.CREATE,
                ExternalOperationEffect.CREATE,
            ),
            "WITH changed AS (UPDATE accounts SET enabled = false RETURNING id) SELECT id FROM changed": (
                SQLStatementClass.UPDATE,
                ExternalOperationEffect.UPDATE,
            ),
            "WITH removed AS (DELETE FROM accounts RETURNING id) SELECT id FROM removed": (
                SQLStatementClass.DELETE,
                ExternalOperationEffect.DELETE,
            ),
        }
        for query, (statement_class, effect) in expected.items():
            proposal = _sql_proposal(query, suffix=statement_class.value)
            self.assertEqual(proposal.payload.statement_class, statement_class)
            self.assertEqual(proposal.normalized_effect, effect)

        multiple = _sql_proposal(
            "SELECT id FROM accounts; DELETE FROM accounts",
            suffix="multiple",
        )
        self.assertEqual(multiple.payload.statement_class, SQLStatementClass.MULTIPLE)
        self.assertEqual(multiple.normalized_effect, ExternalOperationEffect.UNKNOWN)
        self.assertTrue(multiple.payload.ambiguous)
        self.assertEqual(multiple.payload.statement_count, 2)

        locking_read = _sql_proposal(
            "SELECT id FROM accounts FOR UPDATE",
            suffix="locking-read",
        )
        self.assertEqual(locking_read.payload.statement_class, SQLStatementClass.COMMAND)
        self.assertEqual(locking_read.normalized_effect, ExternalOperationEffect.UNKNOWN)
        self.assertTrue(locking_read.payload.ambiguous)

    # Communication normalization handles routing facts but never labels tone or intent.
    def test_email_normalization_is_structural_not_semantic(self) -> None:
        draft = ExternalCommunicationDraft(
            channel=" EMAIL ",
            recipient="person@example.com ",
            subject="Aggressive wording",
            body="Do this immediately or else.",
        )

        proposal = normalize_communication_operation(
            operation_id="email-1",
            plan_step_id="step_1",
            capability_id="communication.email",
            draft=draft,
            execution_candidate=True,
        )

        self.assertEqual(proposal.boundary, OperationBoundary.GOVERNED_EXTERNAL)
        self.assertEqual(proposal.normalized_effect, ExternalOperationEffect.SEND)
        self.assertEqual(proposal.payload.recipient, "person@example.com")
        self.assertEqual(proposal.target.destination_class, DestinationClass.EXTERNAL)
        self.assertFalse(hasattr(proposal.payload, "tone"))

    # Display names and recipient lists cannot hide a prohibited destination.
    def test_email_destination_classification_uses_parsed_addresses(self) -> None:
        draft = ExternalCommunicationDraft(
            channel="email",
            recipient="Allowed <allowed@example.com>; Blocked <user@blocked.example>",
            subject="Status",
            body="Bounded update.",
        )

        proposal = normalize_communication_operation(
            operation_id="email-prohibited",
            plan_step_id="step_1",
            capability_id="communication.email",
            draft=draft,
            execution_candidate=True,
            prohibited_domains=("blocked.example",),
        )

        self.assertEqual(
            proposal.payload.recipient,
            "allowed@example.com, user@blocked.example",
        )
        self.assertEqual(
            proposal.target.destination_class,
            DestinationClass.PROHIBITED,
        )

    # Public evidence tools stay outside the mandatory semantic-judge boundary.
    def test_public_fetch_capabilities_are_registry_classified_as_evidence_read(self) -> None:
        config = build_default_tool_gateway_config()

        for tool_name in (ToolName.WEB_SEARCH, ToolName.FETCH_URL_TEXT):
            definition = config.tools[tool_name]
            self.assertEqual(definition.operation_boundary, OperationBoundary.EVIDENCE_READ)
            self.assertEqual(definition.normalized_effect, ExternalOperationEffect.READ)

    # The production seam remains deliberately incapable of performing side effects.
    def test_deferred_executor_never_performs_external_side_effect(self) -> None:
        proposal = normalize_generic_external_operation(
            operation_id="generic-1",
            plan_step_id="step_1",
            capability_id="generic.publish",
            action="publish",
            target="external-system",
            fields=(("body", "payload"),),
            effect=ExternalOperationEffect.PUBLISH,
        )

        result = DeferredExternalOperationExecutor().execute(
            proposal=proposal,
            approved_payload_digest=proposal.payload_digest,
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.payload_digest, proposal.payload_digest)
        self.assertIsNone(result.external_reference)

    # A later actor turn in the same semantic step must receive a fresh proposal.
    def test_new_actor_turn_invalidates_same_step_external_proposal(self) -> None:
        state = _build_actor_ready_state_for_judge()
        first_request = ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.WEB_SEARCH,
            reason="Collect the first source set.",
            query="first query",
        )
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.PROPOSE_ACTION,
            summary="Request the first evidence search.",
            tool_request=first_request,
        )
        state.current_tool_request = first_request
        first_proposal = prepare_external_operation_proposal(
            task_state=state,
            tool_gateway_config=build_default_tool_gateway_config(),
        )
        state.current_state = RuntimeState.ACTING
        state.current_tool_request = None

        actor(state, actor_service=_SecondSearchActor())
        second_proposal = prepare_external_operation_proposal(
            task_state=state,
            tool_gateway_config=build_default_tool_gateway_config(),
        )

        self.assertEqual(first_proposal.target.value, "first query")
        self.assertEqual(second_proposal.target.value, "second query")
        self.assertNotEqual(first_proposal.payload_digest, second_proposal.payload_digest)
        self.assertIn(first_proposal, state.external_operation_history)

    # Repeated identical proposals remain separate audit occurrences.
    def test_repeated_proposal_digest_is_preserved_in_history(self) -> None:
        state = _state_with_proposal(_generic_proposal())

        first = prepare_external_operation_proposal(
            task_state=state,
            tool_gateway_config=build_default_tool_gateway_config(),
        )
        second = prepare_external_operation_proposal(
            task_state=state,
            tool_gateway_config=build_default_tool_gateway_config(),
        )

        self.assertEqual(first.payload_digest, second.payload_digest)
        self.assertEqual(
            [item.payload_digest for item in state.external_operation_history],
            [first.payload_digest, second.payload_digest],
        )

    # Dashboard projection and checkpoint recovery retain the exact governed proposal.
    def test_hitl_projection_preserves_proposal_without_mutating_state(self) -> None:
        state = _state_with_proposal(_generic_proposal())
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        state.human_review_resume_state = RuntimeState.ACTING
        request = build_human_review_request(
            state=state,
            thread_id="proposal-serialization",
        )
        state.pending_human_review_request = request

        snapshot = build_governance_hitl_dashboard_snapshot(state)

        self.assertEqual(
            state.pending_human_review_request.external_operation_proposal,
            state.external_operation_proposal,
        )
        self.assertEqual(
            snapshot.human_review.pending_request.operation_payload_digest,
            state.external_operation_proposal.payload_digest,
        )

        state.pending_human_review_request = asdict(request)
        normalized = normalize_task_state_serialization(state)
        self.assertIsInstance(state.pending_human_review_request, dict)
        self.assertEqual(
            normalized.pending_human_review_request.external_operation_proposal,
            state.external_operation_proposal,
        )

    # Uniform HITL controls do not weaken a proposal's non-modifiable contract.
    def test_modify_rejects_non_modifiable_external_proposal(self) -> None:
        proposal = replace(_generic_proposal(), modifiable=False)
        state = _state_with_proposal(proposal)
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        state.human_review_resume_state = RuntimeState.ACTING
        request = build_human_review_request(
            state=state,
            thread_id="non-modifiable-proposal",
        )
        state.pending_human_review_request = request

        self.assertIn(
            HumanReviewDecisionType.MODIFY_AND_CONTINUE,
            request.allowed_decisions,
        )
        with self.assertRaisesRegex(ValueError, "not modifiable"):
            _apply_human_review_decision(
                state,
                HumanReviewDecision(
                    request_id=request.request_id,
                    decision_type=HumanReviewDecisionType.MODIFY_AND_CONTINUE,
                    external_operation_edit=ExternalOperationPayloadEdit(
                        payload=proposal.payload,
                    ),
                ),
            )


class ExternalOperationGovernanceTests(unittest.TestCase):
    # Python enums and the closed trace schema cover every active Rego reason code.
    def test_rego_python_and_trace_reason_codes_are_aligned(self) -> None:
        active_rego_codes = {
            "allowed_by_policy",
            "read_only_allowed",
            "ask_clarification_required",
            "evidence_support_below_step_need",
            "precision_mismatch",
            "trajectory_degraded",
            "evidence_gap_and_trajectory_degraded",
            "replan_limit_exceeded",
            "provider_failures_repeated",
            "repeated_recovery",
            "policy_version_mismatch",
            "required_pillar_unavailable",
            "static_policy_hard_deny",
            "read_only_mutation_blocked",
            "database_profile_mutation_blocked",
            "sql_ambiguous_or_multiple",
            "prohibited_destination",
            "judge_required_unavailable",
            "judge_needs_revision",
            "judge_unsafe",
            "judge_uncertain",
            "provider_runtime_action_missing",
            "repeated_action_revision",
        }
        python_codes = {code.value for code in GovernanceReasonCode}
        schema = json.loads(
            (REPO_ROOT / "schemas" / "layer2_trace.schema.json").read_text(
                encoding="utf-8"
            )
        )
        schema_codes = set(schema["$defs"]["governanceReasonCode"]["enum"])

        self.assertTrue(active_rego_codes.issubset(python_codes))
        self.assertEqual(schema_codes, python_codes)

    # A safe assessment for a governed external proposal can continue without forced HITL.
    def test_safe_external_operation_can_continue(self) -> None:
        state = _state_with_proposal(_sql_proposal("SELECT id FROM accounts"))

        result = governance(
            state,
            policy_engine_service=JudgeRequiredPolicyEngine(),
            judge_service=StaticJudge(_safe_judge_assessment()),
            thread_id="safe-external",
        )

        self.assertTrue(result.last_judge_assessment.invoked)
        self.assertEqual(
            result.last_judge_assessment.evaluated_operation_digest,
            result.external_operation_proposal.payload_digest,
        )
        self.assertEqual(result.last_governance_decision.directive, GovernanceDirective.CONTINUE)

    # Semantic revision is distinct from evidence recovery and invalidates the proposal.
    def test_needs_revision_returns_to_actor_with_typed_feedback(self) -> None:
        state = _state_with_proposal(_generic_proposal())

        result = governance(
            state,
            policy_engine_service=JudgeRequiredPolicyEngine(),
            judge_service=StaticJudge(_judge_more_evidence_assessment()),
            thread_id="revise-external",
        )

        self.assertEqual(
            result.last_governance_decision.directive,
            GovernanceDirective.REVISE_ACTION,
        )
        self.assertEqual(result.current_state, RuntimeState.ACTING)
        self.assertIsNone(result.external_operation_proposal)
        self.assertEqual(result.external_operation_revision_count, 1)
        self.assertEqual(len(result.governance_action_feedback), 1)

    # Unsafe semantic findings require full HITL and can never become a hard deny.
    def test_unsafe_external_operation_routes_to_full_hitl(self) -> None:
        state = _state_with_proposal(_generic_proposal())

        result = governance(
            state,
            policy_engine_service=JudgeRequiredPolicyEngine(),
            judge_service=StaticJudge(_judge_wait_review_assessment()),
            thread_id="unsafe-external",
        )

        self.assertEqual(
            result.last_governance_decision.directive,
            GovernanceDirective.WAIT_HUMAN_REVIEW,
        )
        self.assertNotEqual(result.last_governance_decision.policy_decision, PolicyDecision.DENY)
        self.assertEqual(
            set(result.pending_human_review_request.allowed_decisions),
            {
                HumanReviewDecisionType.APPROVE_CONTINUE,
                HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY,
                HumanReviewDecisionType.MODIFY_AND_CONTINUE,
                HumanReviewDecisionType.REQUEST_MORE_EVIDENCE,
                HumanReviewDecisionType.REPLAN,
                HumanReviewDecisionType.ASK_CLARIFICATION,
                HumanReviewDecisionType.ABORT,
            },
        )
        self.assertFalse(result.last_static_policy_assessment.hard_deny)
        self.assertEqual(
            result.last_static_policy_assessment.policy_directive,
            GovernanceDirective.CONTINUE,
        )
        self.assertNotIn(
            GovernanceReasonCode.JUDGE_UNSAFE,
            result.last_static_policy_assessment.reason_codes,
        )
        self.assertNotEqual(
            result.last_dynamic_policy_assessment.recommended_directive.value,
            GovernanceDirective.WAIT_HUMAN_REVIEW.value,
        )

    # A required disabled judge is unavailable and OPA fails closed to HITL.
    def test_required_disabled_judge_fails_closed(self) -> None:
        state = _state_with_proposal(_generic_proposal())

        result = governance(
            state,
            policy_engine_service=JudgeRequiredPolicyEngine(),
            judge_service=DisabledJudge(),
            thread_id="disabled-judge",
        )

        self.assertEqual(result.last_judge_assessment.status, JudgeAssessmentStatus.UNAVAILABLE)
        self.assertEqual(
            result.last_governance_decision.directive,
            GovernanceDirective.WAIT_HUMAN_REVIEW,
        )
        self.assertIn(
            GovernanceReasonCode.JUDGE_REQUIRED_UNAVAILABLE,
            result.last_governance_decision.reason_codes,
        )

    # Digest drift makes the persisted assessment invalid and forces HITL.
    def test_required_judge_digest_mismatch_is_invalid(self) -> None:
        state = _state_with_proposal(_generic_proposal())

        result = governance(
            state,
            policy_engine_service=JudgeRequiredPolicyEngine(),
            judge_service=_WrongDigestJudge(),
            thread_id="wrong-judge-digest",
        )

        self.assertEqual(result.last_judge_assessment.status, JudgeAssessmentStatus.INVALID)
        self.assertEqual(result.last_judge_assessment.verdict, JudgeVerdict.UNCERTAIN)
        self.assertEqual(
            result.last_governance_decision.directive,
            GovernanceDirective.WAIT_HUMAN_REVIEW,
        )

    # Contradictory judge fields are invalid even when the headline verdict says safe.
    def test_inconsistent_safe_judge_verdict_fails_closed(self) -> None:
        state = _state_with_proposal(_generic_proposal())
        inconsistent = replace(
            _safe_judge_assessment(),
            severity=JudgeSeverity.HIGH,
        )

        result = governance(
            state,
            policy_engine_service=JudgeRequiredPolicyEngine(),
            judge_service=StaticJudge(inconsistent),
            thread_id="inconsistent-safe-judge",
        )

        self.assertEqual(
            result.last_judge_assessment.status,
            JudgeAssessmentStatus.INVALID,
        )
        self.assertEqual(
            result.last_judge_assessment.verdict,
            JudgeVerdict.UNCERTAIN,
        )
        self.assertIn(
            "judge_inconsistent_safe_verdict",
            result.last_judge_assessment.flags,
        )
        self.assertEqual(
            result.last_governance_decision.directive,
            GovernanceDirective.WAIT_HUMAN_REVIEW,
        )

    # Static hard deny short-circuits semantic assessment for a read-only mutation.
    def test_static_deny_short_circuits_judge(self) -> None:
        state = _state_with_proposal(
            _sql_proposal("UPDATE accounts SET enabled = false"),
            read_only_mode=True,
        )
        judge = _ExplodingJudge()

        result = governance(
            state,
            policy_engine_service=StubPolicyEngine(),
            judge_service=judge,
            thread_id="static-short-circuit",
        )

        self.assertFalse(judge.called)
        self.assertFalse(result.last_judge_assessment.invoked)
        self.assertEqual(result.last_governance_decision.directive, GovernanceDirective.STOP)

    # Shadow risk failure remains observable without changing the active directive.
    def test_shadow_risk_failure_does_not_change_directive(self) -> None:
        baseline = governance(
            _build_actor_ready_state_for_judge(),
            policy_engine_service=StubPolicyEngine(),
            thread_id="risk-shadow-baseline",
        )

        with patch(
            "layer2_runtime_governance.signal_services.build_risk_signal",
            side_effect=RuntimeError("shadow risk unavailable"),
        ):
            result = governance(
                _build_actor_ready_state_for_judge(),
                policy_engine_service=StubPolicyEngine(),
                thread_id="risk-shadow-failure",
            )

        self.assertEqual(
            result.last_governance_decision.directive,
            baseline.last_governance_decision.directive,
        )
        self.assertNotEqual(
            result.last_governance_decision.directive,
            GovernanceDirective.WAIT_HUMAN_REVIEW,
        )
        self.assertIn("risk", result.last_governance_decision.pillar_errors)
        self.assertFalse(
            result.last_governance_decision.pillar_results["risk"]["decision_influence"]
        )

    # Human modification recomputes SQL facts and forces another governance pass.
    def test_modified_database_payload_changes_digest_and_is_not_executed(self) -> None:
        original = _sql_proposal("SELECT id FROM accounts")
        state = _state_with_proposal(original)
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        state.human_review_resume_state = RuntimeState.READY_TOOL_EXECUTION
        pending = build_human_review_request(state=state, thread_id="db-edit")
        state.pending_human_review_request = pending
        forged_payload = DatabaseOperationPayload(
            sql="UPDATE accounts SET enabled = false",
            normalized_sql="SELECT id FROM accounts",
            statement_class=SQLStatementClass.SELECT,
            statement_count=1,
            parse_valid=True,
            ambiguous=False,
            database_profile_id="enterprise_db.read_only",
        )

        _apply_human_review_decision(
            state,
            HumanReviewDecision(
                request_id=pending.request_id,
                decision_type=HumanReviewDecisionType.MODIFY_AND_CONTINUE,
                external_operation_edit=ExternalOperationPayloadEdit(
                    payload=forged_payload,
                    note="Change the requested operation.",
                ),
            ),
        )

        self.assertEqual(state.current_state, RuntimeState.READY_GOVERNANCE)
        self.assertIsNone(state.current_tool_request)
        self.assertNotEqual(state.external_operation_proposal.payload_digest, original.payload_digest)
        self.assertEqual(
            state.external_operation_proposal.payload.statement_class,
            SQLStatementClass.UPDATE,
        )
        self.assertEqual(
            state.external_operation_proposal.normalized_effect,
            ExternalOperationEffect.UPDATE,
        )
        self.assertIsNone(state.approved_external_operation_digest)

    # Read-only approval cannot authorize an unchanged mutating proposal.
    def test_read_only_approval_rejects_mutation(self) -> None:
        state = _state_with_proposal(_sql_proposal("DELETE FROM accounts"))
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        state.human_review_resume_state = RuntimeState.READY_TOOL_EXECUTION
        pending = build_human_review_request(state=state, thread_id="db-read-only")
        state.pending_human_review_request = pending

        with self.assertRaises(ValueError):
            _apply_human_review_decision(
                state,
                HumanReviewDecision(
                    request_id=pending.request_id,
                    decision_type=HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY,
                    approved_operation_digest=pending.operation_payload_digest,
                ),
            )


class _ExplodingJudge(JudgePort):
    judge_name = "exploding_judge"
    prompt_version = "exploding_judge_v1"

    def __init__(self) -> None:
        self.called = False

    # Record accidental invocation before raising to make short-circuit failures obvious.
    def evaluate(self, *, context):
        self.called = True
        raise AssertionError("Judge must not be invoked after static hard deny.")


# Return one prebuilt actor result so runtime projection can be tested in isolation.
class _SingleResultActor(ActorPort):
    actor_name = "single_result_actor"
    prompt_version = "single_result_actor_v1"

    def __init__(self, result: ActorResult) -> None:
        self._result = result

    # Preserve the prepared result while binding it to the current actor port.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        return self._result


# Produce a distinct request on the next actor turn without changing semantic step.
class _SecondSearchActor(ActorPort):
    actor_name = "second_search_actor"
    prompt_version = "second_search_actor_v1"

    # Return the second action candidate used by the proposal lifecycle regression.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.PROPOSE_ACTION,
            summary="Request the second evidence search.",
            tool_request=ToolRequest(
                plan_step_id=actor_task.current_plan_step.step_id,
                tool_name=ToolName.WEB_SEARCH,
                reason="Collect a different source set.",
                query="second query",
            ),
        )


class _WrongDigestJudge(JudgePort):
    judge_name = "wrong_digest_judge"
    prompt_version = "wrong_digest_judge_v1"

    # Return an otherwise safe assessment bound to the wrong immutable payload.
    def evaluate(self, *, context):
        assessment = _safe_judge_assessment()
        return replace(
            assessment,
            evaluated_target=context.external_operation.operation_id,
            evaluated_operation_digest="0" * 64,
        )


# Build one parser-owned database proposal.
def _sql_proposal(sql: str, *, suffix: str = "default"):
    return normalize_sql_operation(
        operation_id=f"db-{suffix}",
        plan_step_id="step_1",
        capability_id="database.query",
        sql=sql,
        database_target="enterprise-reporting",
    )


# Build one conservative generic governed operation.
def _generic_proposal():
    return normalize_generic_external_operation(
        operation_id="generic-default",
        plan_step_id="step_1",
        capability_id="generic.execute",
        action="execute",
        target="external-system",
        fields=(("payload", "bounded value"),),
        effect=ExternalOperationEffect.EXECUTE,
    )


# Attach a runtime-normalized proposal to one actor result without trusting legacy flags.
def _state_with_proposal(proposal, *, read_only_mode: bool = False):
    state = _build_actor_ready_state_for_judge()
    state.execution_policy = state.execution_policy.model_copy(
        update={"read_only_mode": read_only_mode}
    )
    state.actor_result = ActorResult(
        plan_step_id="step_1",
        result_type=ActorResultType.PROPOSE_ACTION,
        summary="Submit the runtime-normalized external operation for governance.",
        external_operation_proposal=proposal,
    )
    state.external_operation_proposal = proposal
    return state


# Build one positive semantic assessment; OPA alone decides continuation.
def _safe_judge_assessment() -> JudgeAssessment:
    return JudgeAssessment(
        invoked=True,
        recommendation=JudgeRecommendedDirective.CONTINUE,
        confidence=0.92,
        summary="The operation is aligned with the authorized goal.",
        rationale="Target, purpose, and bounded payload are semantically appropriate.",
        flags=(),
        evaluated_target="placeholder",
        status=JudgeAssessmentStatus.ACTIVE,
        verdict=JudgeVerdict.SAFE,
        severity=JudgeSeverity.LOW,
    )


if __name__ == "__main__":
    unittest.main()
