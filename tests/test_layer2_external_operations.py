import json
import re
import sys
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from layer1_intake import ExecutionIntent
from layer2_runtime_governance.domain import (
    ExecutedPlanStep,
    ExternalCommunicationPhase,
    ExternalCommunicationState,
    StepCompletionCandidate,
    build_evidence_selection_digest,
    build_evidence_snapshot_manifest,
    digest_json,
    digest_text,
)
from layer2_runtime_governance.governance_components import (
    GovernanceDirective,
    GovernanceReasonCode,
    build_default_policy_engine,
    evaluate_opa_first_governance,
)
from layer2_runtime_governance.runtime.node_adapters import (
    build_governance_node,
    build_human_review_node,
)
from tests.layer2_runtime_fixtures import *

from layer2_runtime_governance.external_operations import (
    DatabaseOperationPayload,
    DeferredExternalOperationExecutor,
    DestinationClass,
    ExternalOperationEffect,
    ExternalOperationPayloadEdit,
    ExternalExecutionReceipt,
    ExternalExecutionStatus,
    GovernanceActionFeedback,
    OperationBoundary,
    SQLStatementClass,
    normalize_communication_operation,
    normalize_generic_external_operation,
    normalize_sql_operation,
)
from layer2_runtime_governance.runtime.external_execution_service import (
    commit_external_execution_completion,
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
    JudgeExecutionError,
    JudgeSeverity,
    JudgeVerdict,
    JudgeSettings,
    OpenAIJudge,
    judge_evidence_bundle_to_json,
)
from layer2_runtime_governance.judge.service import (
    _JudgePayload,
    _validate_judge_payload,
)
from layer2_runtime_governance.judge import (
    build_judge_system_prompt,
    build_judge_user_prompt,
)
from layer2_runtime_governance.signal_services import (
    build_judge_context,
    build_trajectory_signal,
)
from layer2_runtime_governance.infrastructure.runtime_artifacts import (
    RuntimeArtifactStore,
    _persist_current_judge_evidence_bundle,
)
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
            "evaluated_evidence_snapshot_digest": "b" * 64,
            "evaluated_evidence_selection_digest": "c" * 64,
            "claim_assessments": [],
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
                        "actor_self_confidence": 0.95,
                        "evidence_refs": [],
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

    # The governed payload must preserve the exact body even when its preview is bounded.
    def test_long_email_body_is_not_truncated_in_canonical_proposal_or_judge_prompt(self) -> None:
        body = f"{'A' * 12000}\nFULL-BODY-TAIL"
        proposal = normalize_communication_operation(
            operation_id="long-email",
            plan_step_id="step_1",
            capability_id="communication.email.send",
            draft=ExternalCommunicationDraft(
                channel="email",
                recipient="recipient@example.com",
                subject="Long governed payload",
                body=body,
            ),
            execution_candidate=True,
        )
        state = _state_with_proposal(proposal)
        context = build_judge_context(
            task_state=state,
            external_operation=proposal,
            invocation_reason="test_full_long_email_payload",
        )
        prompt = build_judge_user_prompt(context=context)

        self.assertEqual(proposal.payload.body, body)
        self.assertEqual(context.external_communication_body, body)
        self.assertEqual(len(proposal.payload_preview), 4096)
        self.assertIn("FULL-BODY-TAIL", prompt)
        self.assertEqual(prompt.count("FULL-BODY-TAIL"), 1)
        self.assertNotIn('"external_communication_body"', prompt)
        self.assertEqual(
            proposal.facts_provenance.normalizer_version,
            "external_operation_normalizers.v2",
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

    # Governance atomically seals a late-projected communication proposal into its candidate.
    def test_late_projected_communication_rebinds_completion_candidate_before_evidence(self) -> None:
        state = _build_actor_ready_state_for_judge()
        selected_refs = ("evidence-1",)
        snapshot_digest = digest_json(
            build_evidence_snapshot_manifest(task_state=state)
        )
        state.external_communication = ExternalCommunicationState(
            phase=ExternalCommunicationPhase.DRAFT_PROPOSAL,
            draft=ExternalCommunicationDraft(
                channel="email",
                recipient="ops@example.com",
                subject="Bound candidate",
                body="This exact draft must be governed and approved.",
            ),
            review_ready=True,
        )
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Prepared the complete external communication.",
            output_text=state.external_communication.draft.body,
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=selected_refs,
                rationale_summary="The selected observation supports the draft.",
            ),
        )
        state.pending_completion_candidate = StepCompletionCandidate(
            candidate_id="step_1:candidate:late-projection",
            step_id="step_1",
            summary=state.actor_result.summary,
            output_text=state.actor_result.output_text,
            evidence_refs=selected_refs,
            evidence_snapshot_digest=snapshot_digest,
            evidence_selection_digest=build_evidence_selection_digest(
                selected_refs=selected_refs,
                evidence_snapshot_digest=snapshot_digest,
                external_operation_digest=None,
            ),
        )

        result = evaluate_opa_first_governance(
            task_state=state,
            policy_engine_service=JudgeRequiredPolicyEngine(),
            judge_service=StaticJudge(_safe_judge_assessment()),
        )

        proposal = state.external_operation_proposal
        candidate = state.pending_completion_candidate
        self.assertIsNotNone(proposal)
        self.assertEqual(candidate.external_operation_id, proposal.operation_id)
        self.assertEqual(candidate.external_operation_digest, proposal.payload_digest)
        self.assertEqual(
            candidate.evidence_selection_digest,
            build_evidence_selection_digest(
                selected_refs=selected_refs,
                evidence_snapshot_digest=snapshot_digest,
                external_operation_digest=proposal.payload_digest,
            ),
        )
        evidence_facts = result.decision_input["pillars"]["evidence"]["raw_facts"]
        self.assertTrue(evidence_facts["candidate_evidence_selection_matches"])
        self.assertTrue(evidence_facts["candidate_external_operation_digest_matches"])
        self.assertTrue(result.judge_assessment.invoked)

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

    # A non-authorizing external pause permits correction but never implicit approval.
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
        rego_root = REPO_ROOT / "configs" / "opa" / "layer2_governance"
        active_rego_codes: set[str] = set()
        call_site_patterns = (
            re.compile(r'(?:hitl|replan|evidence)_decision\(\s*"([^"]+)"'),
            re.compile(
                r'base_decision\([^;\n]*?\[\s*"([^"]+)"\s*\]',
            ),
            re.compile(r'reason_codes\s+contains\s+"([^"]+)"'),
            re.compile(
                r'static_deny_reason_codes[^\n]*?else\s*:=\s*\[\s*"([^"]+)"\s*\]',
            ),
        )
        for rego_path in (
            rego_root / "main.rego",
            *sorted((rego_root / "pillars").glob("*.rego")),
        ):
            source = rego_path.read_text(encoding="utf-8")
            for pattern in call_site_patterns:
                active_rego_codes.update(pattern.findall(source))

        python_codes = {code.value for code in GovernanceReasonCode}
        schema = json.loads(
            (REPO_ROOT / "schemas" / "layer2_trace.schema.json").read_text(
                encoding="utf-8"
            )
        )
        schema_codes = set(schema["$defs"]["governanceReasonCode"]["enum"])

        self.assertTrue(active_rego_codes)
        self.assertEqual(active_rego_codes - python_codes, set())
        self.assertEqual(schema_codes, python_codes)

    # A successful digest-bound receipt commits its proposal step and skips stale successors.
    def test_external_receipt_commits_owner_and_skips_residual_plan(self) -> None:
        proposal = _generic_proposal()
        state = _state_with_proposal(proposal)
        state.plan_steps.append(
            PlanStep(
                step_id="step_2",
                objective="Send the already governed operation.",
                rationale="Legacy over-decomposed lifecycle step.",
                expected_outcome="A delivery receipt.",
                status=PlanStepStatus.PENDING,
            )
        )
        state.pending_completion_candidate = StepCompletionCandidate(
            candidate_id="step_1:candidate:1",
            step_id="step_1",
            summary="Prepared the complete governed proposal.",
            output_text="Governed proposal body.",
            external_operation_id=proposal.operation_id,
            external_operation_digest=proposal.payload_digest,
        )
        now = datetime.now(timezone.utc)
        receipt = ExternalExecutionReceipt(
            operation_id=proposal.operation_id,
            capability_id=proposal.capability_id,
            payload_digest=proposal.payload_digest,
            status=ExternalExecutionStatus.EXECUTED,
            idempotency_key="receipt-1",
            started_at=now,
            completed_at=now,
        )

        with self.assertRaisesRegex(ValueError, "does not match"):
            commit_external_execution_completion(
                state=state,
                receipt=replace(receipt, payload_digest="0" * 64),
            )
        self.assertEqual(state.plan_steps[0].status, PlanStepStatus.IN_PROGRESS)

        commit_external_execution_completion(state=state, receipt=receipt)

        self.assertEqual(state.plan_steps[0].status, PlanStepStatus.COMPLETED)
        self.assertEqual(state.plan_steps[1].status, PlanStepStatus.SKIPPED)
        self.assertEqual(
            [step.step_id for step in state.executed_steps],
            ["step_1"],
        )
        self.assertIsNone(state.pending_completion_candidate)
        self.assertIsNone(state.current_plan_step_index)
        self.assertIn("step_2", state.step_history[-1].summary)

    # Judge receives the complete semantic evidence base without process narration.
    def test_judge_context_contains_relevant_execution_context_and_exact_external(self) -> None:
        proposal = _generic_proposal()
        state = _state_with_proposal(proposal)
        state.observations = [
            ToolObservation(
                observation_id="observation-1",
                locator="https://example.org/source",
                content="Concrete observed source content.",
            )
        ] + [
            ToolObservation(
                observation_id=f"observation-{index}",
                locator=f"https://example.org/source/{index}",
                content=f"Concrete observed source content {index}.",
            )
            for index in range(2, 56)
        ]
        state.scratchpad = [
            ScratchpadEntry(
                turn_index=1,
                plan_step_id="step_1",
                entry_type=ScratchpadEntryType.PROGRESS_NOTE,
                summary="Compared the requested outcome with the observed source.",
                evidence_refs=["observation-1"],
            )
        ]
        state.tool_results_history = [
            ToolExecutionResult(
                request=ToolRequest(
                    plan_step_id="step_1",
                    tool_name=ToolName.WEB_SEARCH,
                    reason="Acquire a concrete source.",
                    query="concrete source",
                ),
                success=True,
                summary="Source acquired.",
                content="Provider result content.",
                observations=tuple(state.observations),
            )
        ]
        state.executed_steps = [
            ExecutedPlanStep(
                step_id="step_1",
                objective="Prepare the governed external result.",
                result_type=ActorResultType.STEP_COMPLETED,
                summary="Prepared the result.",
                output_text=proposal.payload_preview,
            )
        ]
        state.step_history.append(
            StepRecord(
                step_index=1,
                runtime_state=RuntimeState.ACTING,
                node_name="actor",
                summary="Actor produced the external candidate.",
            )
        )
        state.external_operation_history = [proposal]
        state.actor_result = replace(
            state.actor_result,
            result_type=ActorResultType.STEP_COMPLETED,
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.8,
                evidence_refs=("observation-1",),
                rationale_summary="The source supports the proposed result.",
            ),
        )

        context = build_judge_context(
            task_state=state,
            external_operation=proposal,
            invocation_reason="test_complete_external_candidate",
        )
        prompt = build_judge_user_prompt(context=context)

        self.assertEqual(context.external_operation.payload_digest, proposal.payload_digest)
        self.assertEqual(len(context.full_plan), 1)
        self.assertEqual(len(context.completed_steps), 1)
        self.assertEqual(len(context.scratchpad_entries), 0)
        self.assertEqual(len(context.tool_executions), 0)
        self.assertEqual(len(context.runtime_events), 0)
        self.assertEqual(len(context.observations), 55)
        self.assertEqual(context.selected_evidence_refs, ("observation-1",))
        self.assertEqual(sum(item.selected_by_actor for item in context.observations), 1)
        self.assertIsNotNone(context.evidence_bundle)
        self.assertEqual(
            context.evidence_bundle.allowed_evidence_ids,
            tuple(f"observation-{index}" for index in range(1, 56)),
        )
        self.assertTrue(context.evidence_base_complete)
        self.assertEqual(context.observation_count, 55)
        self.assertNotIn("scratchpad_entries", prompt)
        self.assertNotIn("tool_executions", prompt)
        self.assertNotIn("runtime_events", prompt)
        self.assertIn('"full_plan"', prompt)
        self.assertIn('"allowed_evidence_ids"', prompt)
        self.assertNotIn('"content_digest"', prompt)
        serialized_bundle = json.loads(
            judge_evidence_bundle_to_json(context.evidence_bundle)
        )
        self.assertEqual(
            serialized_bundle["allowed_evidence_ids"],
            list(context.evidence_bundle.allowed_evidence_ids),
        )
        self.assertIn(proposal.payload_digest, prompt)
        self.assertNotIn("release chronology", build_judge_system_prompt())

        invalid_payload = _JudgePayload.model_validate(
            {
                "verdict": "needs_revision",
                "severity": "medium",
                "confidence": 0.9,
                "findings": [],
                "claim_assessments": [
                    {
                        "claim_summary": "One externally verifiable claim.",
                        "status": "unsupported",
                        "selected_evidence_refs": [],
                        "supporting_evidence_refs": ["invented-observation"],
                        "contradicting_evidence_refs": [],
                        "explanation": "The model invented a reference.",
                    }
                ],
                "summary": "Revision required.",
                "rationale": "One claim is not supported.",
                "evaluated_operation_digest": proposal.payload_digest,
                "evaluated_evidence_snapshot_digest": context.evidence_snapshot_digest,
                "evaluated_evidence_selection_digest": context.evidence_selection_digest,
            }
        )
        with self.assertRaises(JudgeExecutionError):
            _validate_judge_payload(
                payload=invalid_payload,
                context=context,
            )

    # An invalid model reference receives one local correction using the same bundle.
    def test_judge_retries_once_when_model_uses_a_content_digest_as_evidence_ref(self) -> None:
        proposal = _generic_proposal()
        state = _state_with_proposal(proposal)
        state.observations = [
            ToolObservation(
                observation_id="observation-1",
                locator="https://example.org/source",
                content="Concrete observed source content.",
            )
        ]
        state.actor_result = replace(
            state.actor_result,
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.8,
                evidence_refs=("observation-1",),
                rationale_summary="The source supports the proposal.",
            ),
        )
        context = build_judge_context(
            task_state=state,
            external_operation=proposal,
            invocation_reason="test_protocol_retry",
        )

        # Produce one otherwise-valid response with a caller-selected reference.
        def payload_with_ref(reference: str) -> _JudgePayload:
            return _JudgePayload.model_validate(
                {
                    "verdict": "needs_revision",
                    "severity": "medium",
                    "confidence": 0.8,
                    "findings": [
                        {
                            "category": "factual_support",
                            "code": "support_check",
                            "severity": "medium",
                            "summary": "Review the cited support.",
                            "evidence_refs": [reference],
                        }
                    ],
                    "claim_assessments": [],
                    "summary": "Revision required.",
                    "rationale": "One claim needs correction.",
                    "evaluated_operation_digest": proposal.payload_digest,
                    "evaluated_evidence_snapshot_digest": context.evidence_snapshot_digest,
                    "evaluated_evidence_selection_digest": context.evidence_selection_digest,
                }
            )

        class RetryingJudge(OpenAIJudge):
            # Avoid a real provider client while exercising the adapter recovery path.
            # Keep construction local so no network client is created by the test.
            def __init__(self) -> None:
                self._settings = JudgeSettings(model="test-judge", api_key="test")
                self.responses = [
                    payload_with_ref(digest_text(state.observations[0].content)),
                    payload_with_ref("observation-1"),
                ]
                self.corrections: list[str | None] = []

            # Return the two deterministic structured provider responses in order.
            def _request_judge_response(
                self,
                *,
                context,
                protocol_correction: str | None = None,
            ):
                self.corrections.append(protocol_correction)
                return SimpleNamespace(output_parsed=self.responses.pop(0))

        judge = RetryingJudge()
        assessment = judge.evaluate(context=context)

        self.assertEqual(assessment.status, JudgeAssessmentStatus.ACTIVE)
        self.assertEqual(assessment.findings[0].evidence_refs, ("observation-1",))
        self.assertEqual(len(judge.corrections), 2)
        self.assertIsNone(judge.corrections[0])
        self.assertIn("unknown observation ids", judge.corrections[1])

    # Run artifacts preserve the exact content-addressed pool supplied to the Judge.
    def test_judge_evidence_bundle_is_persisted_as_exact_run_json(self) -> None:
        proposal = _generic_proposal()
        state = _state_with_proposal(proposal)
        context = build_judge_context(
            task_state=state,
            external_operation=proposal,
            invocation_reason="test_artifact_projection",
        )

        with TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            store = RuntimeArtifactStore(root_path=Path(temp_dir))
            paths = store.build_paths("judge-bundle-artifact")
            paths.thread_dir.mkdir(parents=True, exist_ok=True)
            artifact = _persist_current_judge_evidence_bundle(
                paths=paths,
                task_state=state,
            )

            self.assertIsNotNone(artifact)
            self.assertEqual(artifact[0], context.evidence_bundle.bundle_digest)
            current_payload = json.loads(
                (paths.thread_dir / "judge_evidence_bundle.json").read_text(
                    encoding="utf-8"
                )
            )
            immutable_payload = json.loads(
                (
                    paths.thread_dir
                    / "judge_evidence_bundles"
                    / f"{artifact[0]}.json"
                ).read_text(encoding="utf-8")
            )
            expected_payload = json.loads(
                judge_evidence_bundle_to_json(context.evidence_bundle)
            )
            self.assertEqual(current_payload, expected_payload)
            self.assertEqual(immutable_payload, expected_payload)

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
        if result.last_governance_decision.directive != GovernanceDirective.CONTINUE:
            print("SAFE TEST GOV DECISION:", result.last_governance_decision)
        self.assertEqual(result.last_governance_decision.directive, GovernanceDirective.CONTINUE)

    # Semantic revision is distinct from evidence recovery and invalidates the proposal.
    def test_needs_revision_returns_to_actor_with_typed_feedback(self) -> None:
        state = _state_with_proposal(_generic_proposal())
        rejected_selection_digest = (
            state.pending_completion_candidate.evidence_selection_digest
        )

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
        self.assertEqual(
            result.governance_action_feedback[0].invalidated_evidence_selection_digest,
            rejected_selection_digest,
        )

    # Trajectory must reject an exact payload-and-Evidence replay after semantic feedback.
    def test_trajectory_detects_exact_rejected_candidate_replay(self) -> None:
        state = _state_with_proposal(_generic_proposal())
        candidate = state.pending_completion_candidate
        proposal = state.external_operation_proposal
        state.governance_action_feedback.append(
            GovernanceActionFeedback(
                operation_id=proposal.operation_id,
                invalidated_payload_digest=proposal.payload_digest,
                invalidated_evidence_selection_digest=(
                    candidate.evidence_selection_digest
                ),
                finding_codes=("unsupported_claim",),
                summary="Revise the exact rejected candidate.",
                revision_index=1,
            )
        )

        signal = build_trajectory_signal(
            task_state=state,
            policy_input=build_policy_evaluation_input(task_state=state),
            thresholds_version="policy.v1",
        )

        self.assertTrue(
            signal["raw_facts"]["repeated_rejected_payload_digest"]
        )
        self.assertEqual(
            signal["raw_facts"]["external_action_revision_count"],
            1,
        )

    # Deterministic replay admission must stop before invoking the semantic Judge.
    def test_exact_rejected_candidate_replay_bypasses_judge_and_routes_to_hitl(self) -> None:
        state = _state_with_proposal(_generic_proposal())
        candidate = state.pending_completion_candidate
        proposal = state.external_operation_proposal
        state.governance_action_feedback.append(
            GovernanceActionFeedback(
                operation_id=proposal.operation_id,
                invalidated_payload_digest=proposal.payload_digest,
                invalidated_evidence_selection_digest=(
                    candidate.evidence_selection_digest
                ),
                finding_codes=("unsupported_claim",),
                summary="Revise the exact rejected candidate.",
                revision_index=1,
            )
        )

        result = evaluate_opa_first_governance(
            task_state=state,
            policy_engine_service=StubPolicyEngine(),
            judge_service=_ExplodingJudge(),
        )

        self.assertEqual(
            result.governance_decision.directive,
            GovernanceDirective.WAIT_HUMAN_REVIEW,
        )
        self.assertIn(
            GovernanceReasonCode.REJECTED_CANDIDATE_RESUBMITTED,
            result.governance_decision.reason_codes,
        )
        self.assertFalse(result.judge_assessment.invoked)

    # Governance history must identify an unchanged payload-and-Evidence submission.
    def test_trajectory_detects_exact_governance_binding_replay(self) -> None:
        state = _state_with_proposal(_generic_proposal())
        candidate = state.pending_completion_candidate
        proposal = state.external_operation_proposal
        state.governance_history.append(
            SimpleNamespace(
                evaluated_operation_digest=proposal.payload_digest,
                pillar_results={
                    "evidence": {
                        "raw_facts": {
                            "evidence_selection_digest": (
                                candidate.evidence_selection_digest
                            )
                        }
                    }
                },
            )
        )

        signal = build_trajectory_signal(
            task_state=state,
            policy_input=build_policy_evaluation_input(task_state=state),
            thresholds_version="policy.v1",
        )

        self.assertTrue(signal["raw_facts"]["unchanged_resubmission"])

    # One operation id with a substantively changed binding is a revision, not a replay.
    def test_trajectory_does_not_collapse_distinct_candidate_bindings(self) -> None:
        state = _state_with_proposal(_generic_proposal())
        proposal = state.external_operation_proposal
        state.governance_action_feedback.append(
            GovernanceActionFeedback(
                operation_id=proposal.operation_id,
                invalidated_payload_digest="f" * 64,
                invalidated_evidence_selection_digest="e" * 64,
                finding_codes=("unsupported_claim",),
                summary="A different candidate was rejected.",
                revision_index=1,
            )
        )

        signal = build_trajectory_signal(
            task_state=state,
            policy_input=build_policy_evaluation_input(task_state=state),
            thresholds_version="policy.v1",
        )

        self.assertFalse(
            signal["raw_facts"]["repeated_rejected_payload_digest"]
        )
        self.assertFalse(signal["raw_facts"]["unchanged_resubmission"])

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
            result.pending_human_review_request.allowed_decisions,
            (
                HumanReviewDecisionType.MODIFY_AND_CONTINUE,
                HumanReviewDecisionType.REPLAN,
                HumanReviewDecisionType.ABORT,
            ),
        )
        with self.assertRaisesRegex(ValueError, "cannot authorize"):
            _apply_human_review_decision(
                result,
                HumanReviewDecision(
                    request_id=result.pending_human_review_request.request_id,
                    decision_type=HumanReviewDecisionType.APPROVE_CONTINUE,
                    approved_operation_digest=(
                        result.pending_human_review_request.operation_payload_digest
                    ),
                ),
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
        baseline_state = _state_with_proposal(_generic_proposal())
        baseline = governance(
            baseline_state,
            policy_engine_service=StubPolicyEngine(),
            judge_service=StaticJudge(_safe_judge_assessment()),
            thread_id="risk-shadow-baseline",
        )

        with patch(
            "layer2_runtime_governance.signal_services.build_risk_signal",
            side_effect=RuntimeError("shadow risk unavailable"),
        ):
            failed_state = _state_with_proposal(_generic_proposal())
            result = governance(
                failed_state,
                policy_engine_service=StubPolicyEngine(),
                judge_service=StaticJudge(_safe_judge_assessment()),
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

    # Proposal-only dangerous drafts must trigger judge & HITL, then complete as approved_draft without external execution.
    def test_proposal_only_dangerous_draft_triggers_judge_and_pauses_for_hitl(self) -> None:
        state = TaskState.from_intake(_build_external_communication_intake_result())
        state.execution_intent = ExecutionIntent.PROPOSAL_ONLY
        state.external_communication = ExternalCommunicationState(
            phase=ExternalCommunicationPhase.DRAFT_PROPOSAL,
            last_transition_summary="Review ready draft",
            draft=ExternalCommunicationDraft(
                channel="email",
                recipient="user@example.com",
                subject="Dangerous Request",
                body="Please send money to unsafe target",
            ),
            review_ready=True,
        )
        state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Draft the email.",
                rationale="Required task step.",
                expected_outcome="Draft produced.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        state.current_plan_step_index = 0
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Drafted dangerous email.",
            output_text=state.external_communication.draft.body,
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.8,
                evidence_refs=(),
                rationale_summary="The review-ready draft is the exact completion candidate.",
            ),
            external_impact=True,
        )

        proposal = prepare_external_operation_proposal(
            task_state=state,
            tool_gateway_config=build_default_tool_gateway_config(),
        )
        self.assertIsNotNone(proposal)
        self.assertTrue(proposal.execution_candidate)
        evidence_snapshot_digest = digest_json(
            build_evidence_snapshot_manifest(task_state=state)
        )
        state.pending_completion_candidate = StepCompletionCandidate(
            candidate_id="step_1:candidate:1",
            step_id="step_1",
            summary="Drafted dangerous email.",
            output_text=state.external_communication.draft.body,
            external_operation_id=proposal.operation_id,
            external_operation_digest=proposal.payload_digest,
            evidence_snapshot_digest=evidence_snapshot_digest,
            evidence_selection_digest=build_evidence_selection_digest(
                selected_refs=(),
                evidence_snapshot_digest=evidence_snapshot_digest,
                external_operation_digest=proposal.payload_digest,
            ),
        )

        policy_engine = StubPolicyEngine()
        unsafe_judge = _UnsafeJudge()
        governance_node = build_governance_node(
            policy_engine_service=policy_engine,
            judge_service=unsafe_judge,
        )

        state_dict = {"task_state": state}
        res = governance_node(state_dict)
        res_state = res["task_state"]

        self.assertEqual(res_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertIsNotNone(res_state.pending_human_review_request)
        self.assertIn(GovernanceReasonCode.JUDGE_UNSAFE, res_state.last_governance_decision.reason_codes)

        # Judge-unsafe remains corrective even for proposal-only drafts; it cannot be approved.
        with self.assertRaisesRegex(ValueError, "cannot authorize"):
            _apply_human_review_decision(
                res_state,
                HumanReviewDecision(
                    request_id=res_state.pending_human_review_request.request_id,
                    decision_type=HumanReviewDecisionType.APPROVE_CONTINUE,
                    approved_operation_digest=(
                        res_state.pending_human_review_request.operation_payload_digest
                    ),
                ),
            )

        self.assertEqual(res_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertIsNone(res_state.current_external_execution_request)
        self.assertIsNone(res_state.last_external_execution_receipt)

    # CC/BCC recipients must be evaluated by destination classification and static policy.
    def test_bcc_prohibited_destination_classified_as_prohibited_and_denied_by_static(self) -> None:
        draft = ExternalCommunicationDraft(
            channel="email",
            recipient="allowed@internal.local",
            bcc=("prohibited@blocked.example",),
            subject="Test Subject",
            body="Test Body",
        )
        proposal = normalize_communication_operation(
            operation_id="test-op",
            plan_step_id="step_1",
            capability_id="communication.email.send",
            draft=draft,
            execution_candidate=True,
            prohibited_domains=("blocked.example",),
        )
        self.assertEqual(proposal.target.destination_class, DestinationClass.PROHIBITED)
        self.assertIn("Bcc: prohibited@blocked.example", proposal.payload_preview)

        state = _state_with_proposal(proposal)
        policy_engine = StubPolicyEngine()
        result = evaluate_opa_first_governance(task_state=state, policy_engine_service=policy_engine)
        self.assertEqual(result.governance_decision.directive, GovernanceDirective.STOP)
        self.assertIn(GovernanceReasonCode.PROHIBITED_DESTINATION, result.governance_decision.reason_codes)

    # Custom database profile marked as read-only must hard-deny mutation statements in static policy.
    def test_custom_read_only_database_profile_hard_denies_mutation_in_static_policy(self) -> None:
        proposal = normalize_sql_operation(
            operation_id="db-test",
            plan_step_id="step_1",
            capability_id="database.statement.execute",
            sql="UPDATE accounts SET balance = 0",
            database_target="custom_reporting",
            database_profile_id="custom_reporting_readonly",
            is_read_only_profile=True,
        )
        self.assertTrue(proposal.payload.is_read_only_profile)

        state = _state_with_proposal(proposal)
        policy_engine = StubPolicyEngine()
        result = evaluate_opa_first_governance(task_state=state, policy_engine_service=policy_engine)
        self.assertEqual(result.governance_decision.directive, GovernanceDirective.STOP)
        self.assertIn(GovernanceReasonCode.DATABASE_PROFILE_MUTATION_BLOCKED, result.governance_decision.reason_codes)


class _UnsafeJudge(JudgePort):
    judge_name = "unsafe_judge"
    prompt_version = "unsafe_judge_v1"

    def evaluate(self, *, context) -> JudgeAssessment:
        return JudgeAssessment(
            invoked=True,
            recommendation=JudgeRecommendedDirective.WAIT_HUMAN_REVIEW,
            confidence=0.88,
            summary="The draft asks for unsafe transfers.",
            rationale="Content contains dangerous external requests.",
            flags=("dangerous_content",),
            evaluated_target=context.external_operation.operation_id,
            status=JudgeAssessmentStatus.ACTIVE,
            verdict=JudgeVerdict.UNSAFE,
            severity=JudgeSeverity.HIGH,
            evaluated_operation_digest=context.external_operation.payload_digest,
            evaluated_evidence_snapshot_digest=context.evidence_snapshot_digest,
            evaluated_evidence_selection_digest=context.evidence_selection_digest,
            prompt_version=self.prompt_version,
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
            evaluated_evidence_snapshot_digest=context.evidence_snapshot_digest,
            evaluated_evidence_selection_digest=context.evidence_selection_digest,
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
    selected_refs = ("evidence-1",)
    state.actor_result = ActorResult(
        plan_step_id="step_1",
        result_type=ActorResultType.STEP_COMPLETED,
        summary="Submit the runtime-normalized external operation for governance.",
        output_text=proposal.payload_preview,
        step_validation=StepValidation(
            plan_step_id="step_1",
            actor_self_confidence=0.8,
            evidence_refs=selected_refs,
            rationale_summary="The concrete observation supports the completion.",
        ),
        external_operation_proposal=proposal,
    )
    state.external_operation_proposal = proposal
    evidence_snapshot_digest = digest_json(
        build_evidence_snapshot_manifest(task_state=state)
    )
    state.pending_completion_candidate = StepCompletionCandidate(
        candidate_id=f"{proposal.plan_step_id}:candidate:1",
        step_id=proposal.plan_step_id,
        summary="Prepared the governed external proposal.",
        output_text=proposal.payload_preview,
        evidence_refs=selected_refs,
        external_operation_id=proposal.operation_id,
        external_operation_digest=proposal.payload_digest,
        evidence_snapshot_digest=evidence_snapshot_digest,
        evidence_selection_digest=build_evidence_selection_digest(
            selected_refs=selected_refs,
            evidence_snapshot_digest=evidence_snapshot_digest,
            external_operation_digest=proposal.payload_digest,
        ),
    )
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
