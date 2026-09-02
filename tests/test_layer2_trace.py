from __future__ import annotations

import json
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError


REPO_ROOT = Path(__file__).resolve().parents[1]

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
from layer2_runtime_governance import (
    ActorResult,
    ActorResultType,
    BehavioralAssessment,
    BehavioralRecommendedDirective,
    CapabilityAccessMode,
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    ExternalCommunicationState,
    EvidenceQualityAssessment,
    EvidenceBurden,
    GovernanceDecision,
    GovernanceDirective,
    GovernanceReasonCode,
    JudgeAssessment,
    JudgeRecommendedDirective,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    HumanReviewRequest,
    OperationalRiskAssessment,
    OversightAssessment,
    OversightReasonCode,
    OversightRecommendedDirective,
    OversightSignalLevel,
    PlanStep,
    PlanStepStatus,
    PolicyDecision,
    RuntimeState,
    TerminationReason,
    ScratchpadEntry,
    ScratchpadEntryType,
    StaticPolicyAssessment,
    StepRecord,
    StepValidation,
    TaskState,
    ToolExecutionResult,
    ToolName,
    ToolRequest,
    ToolObservation,
    TrajectoryHealthAssessment,
    build_compact_trace_from_runtime_trace,
    build_layer2_runtime_trace,
    layer2_trace_to_dict,
    layer2_trace_to_json,
)
from layer2_runtime_governance.signal_services.evidence_service import build_evidence_signal
from layer4_state_evidence_observability.observability import (
    build_governance_narrative_markdown,
)


class Layer2TraceTests(unittest.TestCase):
    # A trace emitted directly by observability must satisfy the complete v6 schema.
    def test_real_runtime_trace_validates_against_v6_schema(self) -> None:
        state = _complete_task_state()
        payload = layer2_trace_to_dict(
            build_layer2_runtime_trace(
                thread_id="schema-valid-v5",
                intake_result=state.intake_payload,
                task_state=state,
            )
        )
        external_state = _external_communication_task_state()
        external_payload = layer2_trace_to_dict(
            build_layer2_runtime_trace(
                thread_id="schema-valid-external-v5",
                intake_result=external_state.intake_payload,
                task_state=external_state,
            )
        )

        _trace_schema_validator().validate(payload)
        _trace_schema_validator().validate(external_payload)

    # Fields added after v6 shipped stay optional, so artifacts already exported under
    # the same version label keep validating instead of silently splitting the contract.
    def test_v6_schema_accepts_traces_without_post_v6_optional_fields(self) -> None:
        state = _complete_task_state()
        payload = layer2_trace_to_dict(
            build_layer2_runtime_trace(
                thread_id="schema-valid-without-optional-fields",
                intake_result=state.intake_payload,
                task_state=state,
            )
        )
        legacy = deepcopy(payload)
        for observation in legacy["observations"]:
            observation.pop("retrieval_kind", None)
        for call in legacy["tool_calls"]:
            for observation in call.get("observations", []):
                observation.pop("retrieval_kind", None)
        for step in legacy["plan_steps"]:
            burden = step.get("evidence_burden")
            if isinstance(burden, dict):
                burden.pop("insufficiency_declaration_permitted", None)

        self.assertEqual(legacy["trace_version"], payload["trace_version"])
        _trace_schema_validator().validate(legacy)

    # The v6 schema must reject a v2 artifact before evaluation normalization.
    def test_v6_schema_rejects_v2_trace(self) -> None:
        payload = json.loads(
            (REPO_ROOT / "tests" / "fixtures" / "layer2_trace.v2.legacy.json").read_text(
                encoding="utf-8"
            )
        )

        with self.assertRaises(ValidationError):
            _trace_schema_validator().validate(payload)

    # Retired root governance histories must not coexist with the canonical v6 pass list.
    def test_v6_schema_rejects_legacy_root_governance_lists(self) -> None:
        payload = _valid_v3_trace_payload()
        payload["governance_decisions"] = deepcopy(payload["governance_passes"])

        with self.assertRaises(ValidationError):
            _trace_schema_validator().validate(payload)

    # Closed v6 sections must reject unknown properties and missing required fields.
    def test_v6_schema_rejects_unknown_and_missing_properties(self) -> None:
        unknown = _valid_v3_trace_payload()
        unknown["runtime"]["unexpected"] = True
        missing = _valid_v3_trace_payload()
        del missing["actor"]["actor_self_confidence"]

        with self.assertRaises(ValidationError):
            _trace_schema_validator().validate(unknown)
        with self.assertRaises(ValidationError):
            _trace_schema_validator().validate(missing)

    # Stable enum and scalar contracts must reject values outside the producer vocabulary.
    def test_v6_schema_rejects_wrong_enum_and_type(self) -> None:
        wrong_enum = _valid_v3_trace_payload()
        wrong_enum["runtime"]["current_state"] = "unknown_state"
        wrong_type = _valid_v3_trace_payload()
        wrong_type["runtime"]["actor_turn_count"] = "three"

        with self.assertRaises(ValidationError):
            _trace_schema_validator().validate(wrong_enum)
        with self.assertRaises(ValidationError):
            _trace_schema_validator().validate(wrong_type)

    # Every governance pass must explicitly carry a boolean human-review flag.
    def test_v6_schema_requires_boolean_human_review_flag(self) -> None:
        missing = _valid_v3_trace_payload()
        del missing["governance_passes"][0]["requires_human_review"]
        wrong_type = _valid_v3_trace_payload()
        wrong_type["governance_passes"][0]["requires_human_review"] = "false"

        with self.assertRaises(ValidationError):
            _trace_schema_validator().validate(missing)
        with self.assertRaises(ValidationError):
            _trace_schema_validator().validate(wrong_type)

    # governance_exchange.md is the complete audit stream and is unreadable by design;
    # this artifact exists so a person can follow the same run turn by turn. It must keep
    # the moments that required a decision and collapse the routine ones.
    def test_governance_narrative_keeps_decisions_and_collapses_routine(self) -> None:
        state = _complete_task_state()
        trace = build_layer2_runtime_trace(
            thread_id="trace-test",
            intake_result=state.intake_payload,
            task_state=state,
        )

        markdown = build_governance_narrative_markdown(trace)

        self.assertIn("Cosa si sono detti agente e governance", markdown)
        self.assertIn("trace-test", markdown)
        self.assertIn("Trace the governed runtime.", markdown)
        # Routine passes are summarized, never rendered one block each.
        self.assertIn("valutazioni di routine", markdown)
        # The artifact has to stay small enough to actually read.
        self.assertLess(len(markdown), 20000)

    # A clarification stop records an uncertainty entry, not a completion rationale: B22
    # rendered as "L'agente ha chiesto un chiarimento." with the question nowhere in the
    # artifact a reader opens to follow the run.
    def test_governance_narrative_quotes_a_clarification_question(self) -> None:
        state = _complete_task_state()
        state.scratchpad.append(
            ScratchpadEntry(
                turn_index=2,
                plan_step_id="step_2",
                entry_type=ScratchpadEntryType.UNCERTAINTY,
                summary="Devo accettare le fonti senza data esplicita?",
            )
        )
        clarification = replace(
            _governance_continue(),
            directive=GovernanceDirective.ASK_USER_CLARIFICATION,
            evaluated_actor_result_type=ActorResultType.ASK_CLARIFICATION,
            evaluated_plan_step_id="step_2",
            reason_codes=(GovernanceReasonCode.ASK_CLARIFICATION_REQUIRED,),
            summary="The actor requested clarification.",
        )
        state.governance_history = [*state.governance_history, clarification]
        state.last_governance_decision = clarification

        trace = build_layer2_runtime_trace(
            thread_id="trace-clarify",
            intake_result=state.intake_payload,
            task_state=state,
        )
        markdown = build_governance_narrative_markdown(trace)

        self.assertIn("ha chiesto un chiarimento", markdown)
        self.assertIn("Devo accettare le fonti senza data esplicita?", markdown)

    def test_runtime_trace_includes_core_sections_and_histories(self) -> None:
        state = _complete_task_state()

        trace = build_layer2_runtime_trace(
            thread_id="trace-test",
            intake_result=state.intake_payload,
            task_state=state,
        )

        self.assertEqual(trace.runtime.thread_id, "trace-test")
        self.assertEqual(trace.runtime.current_state, "completed")
        self.assertEqual(trace.intake.goal, "Trace the governed runtime.")
        self.assertEqual(len(trace.plan_steps), 2)
        self.assertEqual(trace.actor.result_type, "step_completed")
        self.assertEqual(trace.execution.final_output_text, "Final trace answer.")
        self.assertEqual(len(trace.tool_calls), 2)
        self.assertEqual(trace.tool_calls[0].tool_name, "mcp.local_test.text_stats")
        self.assertFalse(trace.tool_calls[1].success)
        self.assertEqual(len(trace.observations), 1)
        self.assertEqual(trace.observations[0].locator, "mcp://local_test/text_stats")
        self.assertEqual(trace.observations[0].content, '{"words": 3}')
        self.assertEqual(trace.trace_version, "layer2_trace.v6")
        self.assertEqual(len(trace.governance_passes), 2)
        self.assertEqual(trace.governance_passes[0].directive, "continue_read_only")
        self.assertFalse(trace.governance_passes[0].requires_human_review)
        self.assertFalse(trace.governance_passes[1].requires_human_review)

    # Preserve the proof obligation and source time in a replayable v6 trace.
    def test_trace_binds_factual_burden_and_source_timestamp(self) -> None:
        state = _complete_task_state()
        state.plan_steps[0].produces_factual_claims = True
        state.plan_steps[0].evidence_burden = EvidenceBurden(
            min_independent_sources=2,
            require_retrieved_content=True,
        )
        state.observations[0] = ToolObservation(
            observation_id=state.observations[0].observation_id,
            locator=state.observations[0].locator,
            content=state.observations[0].content,
            source_timestamp=datetime(2026, 8, 22, 10, 30, tzinfo=timezone.utc),
        )

        trace = build_layer2_runtime_trace(
            thread_id="trace-evidence-contract",
            intake_result=state.intake_payload,
            task_state=state,
        )
        payload = layer2_trace_to_dict(trace)

        self.assertTrue(trace.plan_steps[0].produces_factual_claims)
        self.assertEqual(trace.plan_steps[0].evidence_burden.min_independent_sources, 2)
        self.assertEqual(
            trace.observations[0].source_timestamp,
            "2026-08-22T10:30:00+00:00",
        )
        _trace_schema_validator().validate(payload)

    # Keep pending governed tool reviews aligned with the complete ToolRequest contract.
    def test_pending_tool_review_trace_matches_schema_with_document_fields(self) -> None:
        state = _complete_task_state()
        tool_request = ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.WEB_SEARCH,
            reason="Collect a governed source.",
            query="controlled source",
            document_id="document-1",
            dataset_id="dataset-1",
        )
        state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        state.current_plan_step_index = 0
        state.current_tool_request = tool_request
        pending_request = HumanReviewRequest(
            request_id="trace-tool-review:human_review",
            thread_id="trace-tool-review",
            current_state=RuntimeState.WAITING_HUMAN_REVIEW,
            resume_state=RuntimeState.READY_TOOL_EXECUTION,
            current_plan_step_id="step_1",
            current_plan_step_objective="Collect deterministic text stats.",
            actor_result_type=ActorResultType.PROPOSE_ACTION,
            current_tool_request=tool_request,
            governance_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
            governance_summary="The governed read requires review.",
            allowed_decisions=(HumanReviewDecisionType.APPROVE_CONTINUE,),
        )
        state.pending_human_review_request = pending_request
        state.last_human_review_request = pending_request

        payload = layer2_trace_to_dict(
            build_layer2_runtime_trace(
                thread_id="trace-tool-review",
                intake_result=state.intake_payload,
                task_state=state,
            )
        )

        self.assertEqual(payload["tool_calls"][-1]["document_id"], "document-1")
        self.assertEqual(
            payload["human_review"]["pending_request"]["current_tool_request"][
                "dataset_id"
            ],
            "dataset-1",
        )
        _trace_schema_validator().validate(payload)

    # Actor observability and governance facts must expose the same selected evidence.
    def test_actor_trace_and_governance_signal_share_selected_evidence(self) -> None:
        state = _complete_task_state()
        state.observations = [
            ToolObservation(
                observation_id="step_1:tool:1:source:1",
                locator="https://example.org/source",
                content="Discovered source metadata. Content has not been extracted.",
            )
        ]
        state.actor_result = replace(
            state.actor_result,
            result_type=ActorResultType.STEP_COMPLETED,
            step_validation=replace(
                state.actor_result.step_validation,
                evidence_refs=("step_1:tool:1:source:1",),
            ),
        )

        trace = build_layer2_runtime_trace(
            thread_id="precision-mismatch-consistency",
            intake_result=state.intake_payload,
            task_state=state,
        )
        evidence_signal = build_evidence_signal(
            task_state=state,
            thresholds_version="test_thresholds",
        )

        self.assertEqual(trace.actor.selected_evidence_refs, ("step_1:tool:1:source:1",))
        self.assertEqual(evidence_signal["raw_facts"]["selected_evidence_ref_count"], 1)
        self.assertTrue(evidence_signal["raw_facts"]["selected_evidence_refs_valid"])

    def test_trace_dict_and_json_are_json_safe(self) -> None:
        state = _complete_task_state()
        trace = build_layer2_runtime_trace(
            thread_id="json-safe",
            intake_result=state.intake_payload,
            task_state=state,
        )

        payload = layer2_trace_to_dict(trace)
        encoded = layer2_trace_to_json(trace)
        decoded = json.loads(encoded)

        self.assertEqual(payload["runtime"]["current_state"], "completed")
        self.assertEqual(decoded["trace_version"], "layer2_trace.v6")
        self.assertEqual(decoded["governance_passes"][0]["policy_decision"], "allow_read_only")
        self.assertFalse(decoded["governance_passes"][0]["requires_human_review"])
        self.assertNotIn("governance_decisions", decoded)
        self.assertNotIn("dynamic_policy_assessments", decoded)

        schema = json.loads(
            (REPO_ROOT / "schemas" / "layer2_trace.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["trace_version"]["const"], "layer2_trace.v6")
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["$defs"]["governancePass"]["additionalProperties"])

    def test_wait_human_review_trace_exports_canonical_review_flag(self) -> None:
        state = _complete_task_state()
        state.governance_history = [
            replace(
                _governance_continue(),
                directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                policy_decision=PolicyDecision.REQUIRE_HUMAN_REVIEW,
                requires_human_review=True,
            )
        ]

        payload = layer2_trace_to_dict(
            build_layer2_runtime_trace(
                thread_id="review-flag",
                intake_result=state.intake_payload,
                task_state=state,
            )
        )

        self.assertTrue(payload["governance_passes"][0]["requires_human_review"])

    def test_compact_trace_is_derived_from_runtime_trace(self) -> None:
        state = _complete_task_state()
        trace = build_layer2_runtime_trace(
            thread_id="compact",
            intake_result=state.intake_payload,
            task_state=state,
        )

        compact = build_compact_trace_from_runtime_trace(trace)

        self.assertEqual(compact["planning_snapshot"]["plan_step_count"], 2)
        self.assertEqual(compact["governance_snapshot"]["governance_pass_count"], 2)
        self.assertEqual(
            compact["governance_snapshot"]["last_governance_pass"]["directive"],
            "continue",
        )
        self.assertEqual(compact["tool_snapshot"]["observation_count"], 1)
        self.assertIsNone(compact["tool_snapshot"]["current_tool_request"])

    def test_waiting_review_and_aborted_states_are_explicit(self) -> None:
        review_state = _complete_task_state()
        review_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        aborted_state = _complete_task_state()
        aborted_state.current_state = RuntimeState.ABORTED
        aborted_state.termination_reason = TerminationReason.GOVERNANCE_DENIED

        review_trace = build_layer2_runtime_trace(
            thread_id="review",
            intake_result=review_state.intake_payload,
            task_state=review_state,
        )
        aborted_trace = build_layer2_runtime_trace(
            thread_id="aborted",
            intake_result=aborted_state.intake_payload,
            task_state=aborted_state,
        )

        self.assertEqual(review_trace.runtime.current_state, "waiting_human_review")
        self.assertEqual(aborted_trace.runtime.current_state, "aborted")
        self.assertEqual(aborted_trace.runtime.termination_reason, "governance_denied")

    def test_trace_includes_external_communication_edit_history(self) -> None:
        state = _external_communication_task_state()

        trace = build_layer2_runtime_trace(
            thread_id="external-trace",
            intake_result=state.intake_payload,
            task_state=state,
        )

        self.assertIsNotNone(trace.human_review)
        self.assertIsNotNone(trace.external_communication)
        self.assertTrue(
            trace.plan_steps[0].produces_governed_external_effect_proposal
        )
        self.assertEqual(trace.external_communication.phase, "approved_for_execution")
        self.assertEqual(trace.external_communication.draft.channel, "email")
        self.assertEqual(
            trace.external_communication.last_applied_edit.note,
            "Updated the target before submission.",
        )
        self.assertEqual(len(trace.external_communication.edit_history), 1)
        self.assertEqual(
            trace.human_review.pending_request.external_communication_target_type,
            "email",
        )
        self.assertEqual(
            trace.human_review.pending_request.external_communication_draft.channel,
            "email",
        )
        self.assertEqual(
            trace.human_review.decision_history[0].external_communication_edit.draft.channel,
            "slack",
        )


# Build the repository v3 schema validator used by positive and negative contract tests.
def _trace_schema_validator() -> Draft202012Validator:
    schema = json.loads(
        (REPO_ROOT / "schemas" / "layer2_trace.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


# Build one real producer payload so negative tests change only the intended field.
def _valid_v3_trace_payload() -> dict[str, Any]:
    state = _complete_task_state()
    return layer2_trace_to_dict(
        build_layer2_runtime_trace(
            thread_id="schema-negative-fixture",
            intake_result=state.intake_payload,
            task_state=state,
        )
    )


# Build a canonical IntakeResult for trace tests.
def _intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Trace the governed runtime.",
                context="Use deterministic fixtures.",
                constraints=["Stay read-only."],
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Produce an audit-safe trace."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                policy_profile_name="trace_profile",
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="trace_test_intake",
            prompt_version="trace_test_v1",
            schema_version="1.0.0",
            warnings=[],
            missing_information=[],
        ),
    )


# Build an IntakeResult for the external communication trace coverage.
def _external_communication_intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Prepare a governed external communication draft.",
                context="Use deterministic fixtures.",
                constraints=["Keep the draft local."],
                execution_intent=ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
                sensitivity=SensitivityLevel.HIGH,
                success_criteria=["Expose the typed external communication trace."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.7,
                policy_profile_name="trace_external_profile",
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="trace_external_intake",
            prompt_version="trace_external_v1",
            schema_version="1.0.0",
            warnings=[],
            missing_information=[],
        ),
    )


# Build a complete TaskState covering all major trace sections.
def _complete_task_state() -> TaskState:
    state = TaskState.from_intake(_intake_result())
    state.current_state = RuntimeState.COMPLETED
    state.bootstrap_completed = True
    state.step_history = [
        StepRecord(1, RuntimeState.PLANNING, "bootstrap_from_intake", "Bootstrapped."),
        StepRecord(2, RuntimeState.ACTING, "actor", "Actor executed."),
    ]
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Collect deterministic text stats.",
            rationale="The task needs evidence.",
            expected_outcome="Stats collected.",
            status=PlanStepStatus.COMPLETED,
        ),
        PlanStep(
            step_id="step_2",
            objective="Write final answer.",
            rationale="The task needs a final output.",
            expected_outcome="Answer written.",
            status=PlanStepStatus.COMPLETED,
        ),
    ]
    state.current_plan_step_index = None
    state.planner_name = "stub_planner"
    state.planner_prompt_version = "planner_v1"
    state.planner_rationale = "Two-step trace plan."
    state.actor_name = "stub_actor"
    state.actor_prompt_version = "actor_v1"
    state.actor_turn_count = 2
    state.tool_call_count = 1
    state.final_output_text = "Final trace answer."
    state.actor_result = ActorResult(
        plan_step_id="step_2",
        result_type=ActorResultType.STEP_COMPLETED,
        summary="Final output produced.",
        capability_mode=CapabilityAccessMode.NONE,
        external_impact=False,
        output_text="Final trace answer.",
        step_validation=_validation("step_2"),
    )
    state.executed_steps = [
        _executed_step("step_1", ActorResultType.INTERMEDIATE_OUTPUT),
        _executed_step("step_2", ActorResultType.STEP_COMPLETED),
    ]
    state.scratchpad = [
        ScratchpadEntry(
            turn_index=1,
            plan_step_id="step_1",
            entry_type=ScratchpadEntryType.OBSERVATION,
            summary="Observed deterministic stats.",
            evidence_refs=["step_1:tool:1:source:1"],
        )
    ]
    state.step_validations = [_validation("step_1"), _validation("step_2")]
    state.last_tool_result = _tool_result()
    state.tool_results_history = [_tool_result(), _second_tool_result()]
    state.observations = [_observation()]
    state.governance_history = [_governance_read_only(), _governance_continue()]
    state.last_governance_decision = state.governance_history[-1]
    state.static_policy_history = [_static_policy_read_only(), _static_policy_allow()]
    state.last_static_policy_assessment = state.static_policy_history[-1]
    state.dynamic_policy_history = [_oversight(), _oversight()]
    state.last_dynamic_policy_assessment = state.dynamic_policy_history[-1]
    state.oversight_history = [_oversight(), _oversight()]
    state.last_oversight_assessment = state.oversight_history[-1]
    state.behavioral_history = [_behavioral()]
    state.last_behavioral_assessment = state.behavioral_history[-1]
    state.judge_history = [_judge()]
    state.last_judge_assessment = state.judge_history[-1]
    return state


# Build a complete TaskState that exercises the typed external communication review history.
def _external_communication_task_state() -> TaskState:
    state = TaskState.from_intake(_external_communication_intake_result())
    state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
    state.bootstrap_completed = True
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Draft the external communication.",
            rationale="The draft must remain local until review is complete.",
            expected_outcome="A reviewable external draft.",
            status=PlanStepStatus.IN_PROGRESS,
            produces_governed_external_effect_proposal=True,
        )
    ]
    state.current_plan_step_index = 0
    state.external_communication = ExternalCommunicationState(
        phase=ExternalCommunicationPhase.DRAFT_IN_PROGRESS
    )
    state.external_communication.phase = ExternalCommunicationPhase.APPROVED_FOR_EXECUTION
    state.external_communication.draft = ExternalCommunicationDraft(
        channel="email",
        recipient="ops@example.com",
        subject="Draft update",
        body="Please review this draft before sending.",
        metadata={"priority": "high"},
    )
    applied_edit = HumanReviewExternalEdit(
        draft=ExternalCommunicationDraft(
            channel="slack",
            recipient="#ops",
            subject="Updated subject",
            body="Updated body text.",
            metadata={"priority": "critical"},
        ),
        note="Updated the target before submission.",
    )
    state.external_communication.last_applied_edit = applied_edit
    state.external_communication.edit_history = [applied_edit]
    pending_request = HumanReviewRequest(
        request_id="external-trace:human_review",
        thread_id="external-trace",
        current_state=RuntimeState.WAITING_HUMAN_REVIEW,
        resume_state=RuntimeState.ACTING,
        current_plan_step_id="step_1",
        current_plan_step_objective="Draft the external communication.",
        actor_result_type=ActorResultType.INTERMEDIATE_OUTPUT,
        current_tool_request=None,
        governance_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
        governance_summary="External communication requires review.",
        oversight_summary="Review is still required.",
        external_communication_target_type="email",
        external_communication_draft=state.external_communication.draft,
        reason_codes=("human_review_required",),
        allowed_decisions=(
            HumanReviewDecisionType.APPROVE_CONTINUE,
            HumanReviewDecisionType.MODIFY_AND_CONTINUE,
        ),
        review_summary="The external draft may be edited and resubmitted.",
    )
    state.pending_human_review_request = pending_request
    state.last_human_review_request = pending_request
    decision = HumanReviewDecision(
        request_id=pending_request.request_id,
        decision_type=HumanReviewDecisionType.MODIFY_AND_CONTINUE,
        external_communication_edit=applied_edit,
        reviewer_id="operator-1",
    )
    state.human_review_history = [decision]
    state.last_human_review_decision = decision
    return state


# Build one executed step for the trace execution section.
def _executed_step(step_id: str, result_type: ActorResultType) -> Any:
    from layer2_runtime_governance import ExecutedPlanStep

    return ExecutedPlanStep(
        step_id=step_id,
        objective=f"Objective for {step_id}.",
        result_type=result_type,
        summary=f"Summary for {step_id}.",
        output_text=f"Output for {step_id}.",
    )


# Build one positive step validation.
def _validation(step_id: str) -> StepValidation:
    return StepValidation(
        plan_step_id=step_id,
        actor_self_confidence=0.9,
        evidence_refs=(),
        rationale_summary="Validation passed.",
    )


# Build one retained tool result.
def _tool_result() -> ToolExecutionResult:
    request = ToolRequest(
        plan_step_id="step_1",
        tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
        reason="Collect text stats.",
        text="agent agent evidence",
        limit=5,
    )
    return ToolExecutionResult(
        request=request,
        success=True,
        summary="Stats collected.",
        content='{"words": 3}',
        observations=(
            ToolObservation(
                observation_id=f"{request.request_id}:1",
                locator="mcp://local_test/text_stats",
                content='{"words": 3}',
            ),
        ),
    )


# Build a second retained tool result so trace exports full tool history.
def _second_tool_result() -> ToolExecutionResult:
    request = ToolRequest(
        plan_step_id="step_2",
        tool_name=ToolName.FETCH_URL_TEXT,
        reason="Fetch source text.",
        url="https://example.org/source",
    )
    return ToolExecutionResult(
        request=request,
        success=False,
        summary="Duplicate tool request blocked before execution.",
        error_message="The same tool request was already executed for this plan step.",
    )


# Build one concrete tool observation.
def _observation() -> ToolObservation:
    return ToolObservation(
        observation_id="step_1:tool:1:source:1",
        locator="mcp://local_test/text_stats",
        content='{"words": 3}',
    )


# Build the read-only governance decision for tool use.
def _governance_read_only() -> GovernanceDecision:
    return GovernanceDecision(
        directive=GovernanceDirective.CONTINUE_READ_ONLY,
        policy_decision=PolicyDecision.ALLOW_READ_ONLY,
        reason_codes=(GovernanceReasonCode.READ_ONLY_ALLOWED,),
        summary="Read-only tool use allowed.",
        constrains_capability_use=True,
        evaluated_actor_result_type=ActorResultType.PROPOSE_ACTION,
        evaluated_tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
        policy_engine_name="test_policy",
        policy_bundle_version="test_v1",
        policy_directive=GovernanceDirective.CONTINUE_READ_ONLY,
        oversight_recommended_directive=GovernanceDirective.CONTINUE,
        oversight_summary="Oversight healthy.",
    )


# Build the local-output governance decision.
def _governance_continue() -> GovernanceDecision:
    return GovernanceDecision(
        directive=GovernanceDirective.CONTINUE,
        policy_decision=PolicyDecision.ALLOW,
        reason_codes=(GovernanceReasonCode.LOCAL_OUTPUT_ALLOWED,),
        summary="Local output allowed.",
        constrains_capability_use=False,
        evaluated_actor_result_type=ActorResultType.STEP_COMPLETED,
        evaluated_tool_name=None,
        policy_engine_name="test_policy",
        policy_bundle_version="test_v1",
        policy_directive=GovernanceDirective.CONTINUE,
        oversight_recommended_directive=GovernanceDirective.CONTINUE,
        oversight_summary="Oversight healthy.",
    )


# Build one strong oversight assessment.
def _oversight() -> OversightAssessment:
    return OversightAssessment(
        evidence_quality=EvidenceQualityAssessment(
            score=0.98,
            level=OversightSignalLevel.STRONG,
            reason_codes=(OversightReasonCode.EVIDENCE_MARKED_SUFFICIENT,),
        ),
        operational_risk=OperationalRiskAssessment(
            score=0.15,
            level=OversightSignalLevel.LOW,
            reason_codes=(OversightReasonCode.TOOL_RISK_LOW,),
        ),
        trajectory_health=TrajectoryHealthAssessment(
            score=1.0,
            level=OversightSignalLevel.HEALTHY,
            reason_codes=(OversightReasonCode.NO_REPLAN_NEEDED,),
        ),
        recommended_directive=OversightRecommendedDirective.CONTINUE,
        summary="Evidence strong, risk low, trajectory healthy.",
        reason_codes=(OversightReasonCode.RECOMMEND_CONTINUE,),
    )


# Build one static policy assessment.
def _static_policy_read_only() -> StaticPolicyAssessment:
    return StaticPolicyAssessment(
        policy_decision=PolicyDecision.ALLOW_READ_ONLY,
        reason_codes=(GovernanceReasonCode.READ_ONLY_ALLOWED,),
        summary="Read-only tool use allowed.",
        policy_engine_name="test_policy",
        policy_bundle_version="test_v1",
        policy_directive=GovernanceDirective.CONTINUE_READ_ONLY,
    )


# Build one local-output static policy assessment.
def _static_policy_allow() -> StaticPolicyAssessment:
    return StaticPolicyAssessment(
        policy_decision=PolicyDecision.ALLOW,
        reason_codes=(GovernanceReasonCode.LOCAL_OUTPUT_ALLOWED,),
        summary="Local output allowed.",
        policy_engine_name="test_policy",
        policy_bundle_version="test_v1",
        policy_directive=GovernanceDirective.CONTINUE,
    )


# Build one behavioural foundation assessment.
def _behavioral() -> BehavioralAssessment:
    return BehavioralAssessment(
        history_available=True,
        historical_run_count=3,
        feature_snapshot_version="behavioral_features_v2",
        summary="Behavioral analysis matched 3 comparable runs.",
        notes=("sqlite_history_available",),
        matched_run_count=3,
        historical_similarity_score=0.82,
        behavior_deviation_score=0.21,
        novelty_score=0.18,
        behavioral_recommended_directive=BehavioralRecommendedDirective.CONTINUE,
        behavioral_flags=("stable_history",),
        matched_run_ids=("run-1", "run-2", "run-3"),
    )


# Build one selective judge assessment for trace export.
def _judge() -> JudgeAssessment:
    return JudgeAssessment(
        invoked=True,
        recommendation=JudgeRecommendedDirective.WAIT_HUMAN_REVIEW,
        confidence=0.87,
        summary="Judge recommends human review.",
        rationale="The output is semantically risky.",
        flags=("semantic_risk_high",),
        evaluated_target="external_draft",
    )


if __name__ == "__main__":
    unittest.main()
