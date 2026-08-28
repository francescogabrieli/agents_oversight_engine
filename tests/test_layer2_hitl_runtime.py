from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from langgraph.types import Command

from tests.layer2_runtime_fixtures import (
    ClarificationActor,
    JudgeRequiredPolicyEngine,
    ReviewPolicyEngine,
    StaticJudge,
    StubPolicyEngine,
    _build_actor_ready_state_for_judge,
    _build_clarification_intake_result,
    _build_external_communication_intake_result,
    _build_external_communication_intake_result_for_governance,
    _build_policy_input_task_state,
    _build_research_intake_result,
    _build_test_graph,
    _judge_wait_review_assessment,
)

from layer2_runtime_governance.actor_contract import (
    ActorResult,
    ActorResultType,
    CapabilityAccessMode,
)
from layer2_runtime_governance.domain import (
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    ExternalCommunicationState,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    PlanStep,
    PlanStepStatus,
    ReplanCheckpoint,
    RuntimeFlag,
    RuntimeState,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepValidation,
    StepRecord,
    TaskState,
    TerminationReason,
)
from layer2_runtime_governance.governance_components import (
    GovernanceDecision,
    GovernanceDirective,
    GovernanceReasonCode,
    PolicyDecision,
    build_opa_input_document,
    build_policy_evaluation_input,
    evaluate_opa_first_governance,
)
from layer2_runtime_governance.signal_services import build_trajectory_signal
from layer2_runtime_governance.hitl import (
    _apply_human_review_decision,
    build_human_review_request,
    human_review,
)
from layer2_runtime_governance.runtime.actor_service import _record_actor_execution
from layer2_runtime_governance.runtime.governance_service import governance
from layer4_state_evidence_observability.infrastructure.checkpointing import (
    build_default_checkpointer,
)
REPO_ROOT = Path(__file__).resolve().parents[1]


class Layer2HumanReviewRuntimeTests(unittest.TestCase):
    # HITL actions are scoped to the exact reason that opened the gate.
    def test_clarification_review_exposes_only_clarify_or_abort(self) -> None:
        task_state = TaskState.from_intake(_build_clarification_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.human_review_resume_state = RuntimeState.ACTING

        request = build_human_review_request(
            state=task_state,
            thread_id="uniform-hitl-actions",
        )

        self.assertEqual(
            request.allowed_decisions,
            (
                HumanReviewDecisionType.ASK_CLARIFICATION,
                HumanReviewDecisionType.ABORT,
            ),
        )

    # A ceiling approval closes only the reviewed replan episode and releases normal progress.
    def test_replan_ceiling_approval_starts_a_fresh_trajectory_episode(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.human_review_resume_state = RuntimeState.REPLANNING
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Recover the evidence collection trajectory.",
                rationale="The current path cannot complete the task.",
                expected_outcome="A replacement evidence path.",
                status=PlanStepStatus.BLOCKED,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.NEEDS_REPLAN,
            summary="Another replacement trajectory is required.",
            capability_mode=CapabilityAccessMode.NONE,
            external_impact=False,
            blocking_reason="The current evidence source remains unavailable.",
        )
        for index in range(3):
            task_state.replan_checkpoints.append(
                ReplanCheckpoint(
                    actor_turn_count=index + 1,
                    tool_call_count=index,
                    observation_count=index,
                    completed_step_count=0,
                    human_review_count=0,
                    plan_signature=f"plan-{index}",
                    request_reason="Source unavailable.",
                )
            )
            task_state.step_history.append(
                StepRecord(
                    step_index=index,
                    runtime_state=RuntimeState.REPLANNING,
                    node_name="replanner",
                    summary="Installed a replacement trajectory.",
                )
            )
        task_state.last_governance_decision = GovernanceDecision(
            directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
            policy_decision=PolicyDecision.REQUIRE_HUMAN_REVIEW,
            reason_codes=(GovernanceReasonCode.REPLAN_CEILING_REACHED,),
            summary="The current replanning episode reached its ceiling.",
            constrains_capability_use=False,
            evaluated_actor_result_type=ActorResultType.NEEDS_REPLAN,
            evaluated_tool_name=None,
            policy_engine_name="opa",
            policy_bundle_version="layer2_governance_v5",
            policy_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
            oversight_recommended_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
            requires_human_review=True,
        )

        before_review = build_trajectory_signal(
            task_state=task_state,
            policy_input=build_policy_evaluation_input(task_state=task_state),
            thresholds_version="test",
        )
        self.assertEqual(before_review["raw_facts"]["replan_count"], 3)
        self.assertEqual(before_review["raw_facts"]["replan_episode_count"], 3)

        pending_request = build_human_review_request(
            state=task_state,
            thread_id="replan-ceiling-release",
        )
        self.assertEqual(
            pending_request.allowed_decisions,
            (
                HumanReviewDecisionType.APPROVE_CONTINUE,
                HumanReviewDecisionType.REPLAN,
                HumanReviewDecisionType.ABORT,
            ),
        )
        task_state.pending_human_review_request = pending_request
        with patch(
            "layer2_runtime_governance.hitl.interrupt",
            return_value={
                "request_id": pending_request.request_id,
                "decision_type": "approve_continue",
            },
        ):
            resumed_state = human_review(
                task_state,
                thread_id="replan-ceiling-release",
            )

        self.assertEqual(resumed_state.current_state, RuntimeState.REPLANNING)
        self.assertEqual(len(resumed_state.human_review_history), 1)
        resumed_state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary="Normal actor progress resumed.",
            capability_mode=CapabilityAccessMode.NONE,
            external_impact=False,
            output_text="Continuing after the approved boundary.",
        )
        after_review = build_trajectory_signal(
            task_state=resumed_state,
            policy_input=build_policy_evaluation_input(task_state=resumed_state),
            thresholds_version="test",
        )
        self.assertEqual(after_review["raw_facts"]["replan_count"], 3)
        self.assertEqual(after_review["raw_facts"]["replan_episode_count"], 0)
        self.assertFalse(after_review["raw_facts"]["needs_replan_signal"])

    # Evidence review opens a new remediation episode and never blindly approves the gap.
    def test_evidence_ceiling_review_exposes_recovery_choices_without_continue(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.human_review_resume_state = RuntimeState.ACTING
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Verify dated official sources.",
                rationale="The evidence burden remains unsatisfied.",
                expected_outcome="A valid evidence selection.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Repeated the invalid evidence selection.",
            output_text="The same unsupported completion.",
        )
        task_state.evidence_request_count = 7
        task_state.evidence_request_episode_count = 3
        task_state.evidence_request_episode_id = "step_1"
        task_state.last_governance_decision = GovernanceDecision(
            directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
            policy_decision=PolicyDecision.REQUIRE_HUMAN_REVIEW,
            reason_codes=(GovernanceReasonCode.EVIDENCE_REQUEST_LIMIT_REACHED,),
            summary="The Evidence remediation episode reached its ceiling.",
            constrains_capability_use=True,
            evaluated_actor_result_type=ActorResultType.STEP_COMPLETED,
            evaluated_tool_name=None,
            policy_engine_name="opa",
            policy_bundle_version="layer2_governance_v5",
            policy_directive=GovernanceDirective.CONTINUE,
            oversight_recommended_directive=GovernanceDirective.REQUEST_MORE_EVIDENCE,
            requires_human_review=True,
        )

        pending_request = build_human_review_request(
            state=task_state,
            thread_id="evidence-ceiling-release",
        )
        self.assertEqual(
            pending_request.allowed_decisions,
            (
                HumanReviewDecisionType.REQUEST_MORE_EVIDENCE,
                HumanReviewDecisionType.REPLAN,
                HumanReviewDecisionType.ABORT,
            ),
        )

        legacy_request = replace(
            pending_request,
            allowed_decisions=(
                HumanReviewDecisionType.APPROVE_CONTINUE,
                HumanReviewDecisionType.REPLAN,
                HumanReviewDecisionType.ABORT,
            ),
        )
        task_state.pending_human_review_request = legacy_request
        with self.assertRaisesRegex(ValueError, "cannot be bypassed"):
            _apply_human_review_decision(
                task_state,
                HumanReviewDecision(
                    request_id=legacy_request.request_id,
                    decision_type=HumanReviewDecisionType.APPROVE_CONTINUE,
                ),
            )

        task_state.pending_human_review_request = pending_request
        _apply_human_review_decision(
            task_state,
            HumanReviewDecision(
                request_id=pending_request.request_id,
                decision_type=HumanReviewDecisionType.REQUEST_MORE_EVIDENCE,
            ),
        )
        self.assertEqual(task_state.current_state, RuntimeState.ACTING)
        self.assertEqual(task_state.evidence_request_count, 7)
        self.assertEqual(task_state.evidence_request_episode_count, 0)
        self.assertIsNone(task_state.evidence_request_episode_id)

    # Scenarios remain grouped by the capability whose behavior they verify.
    def test_needs_clarification_skips_actor(self) -> None:
        graph = _build_test_graph()
        result = graph.invoke(
            {"intake_result": _build_clarification_intake_result()},
            config={"configurable": {"thread_id": "layer2-ready-to-execute-clarification"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertTrue(task_state.bootstrap_completed)
        self.assertIn(RuntimeFlag.CLARIFICATION_REQUIRED, task_state.runtime_flags)
        self.assertIn(RuntimeFlag.HIGH_SENSITIVITY, task_state.runtime_flags)
        self.assertEqual(task_state.step_history[0].runtime_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "governance",
                "finalize_bootstrap",
            ],
        )
        self.assertEqual(task_state.plan_steps, [])
        self.assertIsNone(task_state.current_plan_step_index)
        self.assertIsNone(task_state.planner_rationale)
        self.assertIsNone(task_state.actor_result)
        self.assertEqual(task_state.governance_history, [])
        self.assertIsNone(task_state.last_governance_decision)


    def test_provider_empty_block_routes_to_replan_without_human_review(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Draft the email header and opening.",
                rationale="The first external communication step should produce draft content.",
                expected_outcome="A markdown body fragment for the email.",
                status=PlanStepStatus.BLOCKED,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.BLOCKED,
            summary="Actor provider returned no usable runtime action.",
            capability_mode=CapabilityAccessMode.NONE,
            external_impact=False,
            provider_protocol_failure=True,
            blocking_reason=(
                "Actor provider returned no usable runtime action: OpenRouter returned no "
                "tool call and no assistant content. finish_reason='stop'."
            ),
            scratchpad_entry=ScratchpadEntry(
                turn_index=1,
                plan_step_id="step_1",
                entry_type=ScratchpadEntryType.UNCERTAINTY,
                summary="Actor provider returned no usable runtime action.",
            ),
        )
        task_state.scratchpad.append(task_state.actor_result.scratchpad_entry)

        result = governance(
            task_state,
            policy_engine_service=StubPolicyEngine(),
            judge_service=StaticJudge(_judge_wait_review_assessment()),
            thread_id="provider-empty-replan",
        )

        self.assertEqual(result.current_state, RuntimeState.REPLANNING)
        self.assertIsNone(result.pending_human_review_request)
        # The runtime recovery route is separate from the OPA directive; this
        # fixture's policy stub allows the turn while the typed failure replans.
        self.assertEqual(result.last_governance_decision.directive, GovernanceDirective.CONTINUE)
        self.assertIsNone(result.external_communication)


    def test_opa_policy_does_not_deny_write_compatibility_flag_alone(self) -> None:
        opa_path = shutil.which("opa")
        if opa_path is None:
            self.skipTest("OPA executable is not available.")

        policy_result = evaluate_opa_first_governance(
            task_state=_build_policy_input_task_state(),
            policy_engine_service=StubPolicyEngine(),
            thread_id="opa-write-policy-input",
        )
        opa_input = policy_result.decision_input
        opa_input["base_facts"]["actor"]["capability_mode"] = "write"
        opa_input["base_facts"]["actor"]["external_impact"] = False

        # Exercise the actual Rego bundle so the runtime policy and Python stub stay aligned.
        completed = subprocess.run(
            [
                opa_path,
                "eval",
                "--format",
                "json",
                "--data",
                "configs/opa/layer2_governance",
                "--stdin-input",
                "data.layer2.governance.decision",
            ],
            input=json.dumps(opa_input),
            text=True,
            capture_output=True,
            check=True,
            cwd=REPO_ROOT,
        )
        parsed = json.loads(completed.stdout)
        decision = parsed["result"][0]["expressions"][0]["value"]

        self.assertNotEqual(decision["policy_decision"], "deny")
        self.assertNotIn("write_capability_blocked", decision["reason_codes"])


    def test_opa_input_marks_external_communication_draft_phase(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.external_communication = ExternalCommunicationState(
            phase=ExternalCommunicationPhase.DRAFT_PROPOSAL
        )
        task_state.external_communication.phase = ExternalCommunicationPhase.DRAFT_PROPOSAL
        task_state.external_communication.review_ready = True
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Draft the cautious external email.",
                rationale="Keep the draft local before review.",
                expected_outcome="A local draft proposal.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.actor_turn_count = 1
        task_state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary="A local external communication draft is ready for review.",
            capability_mode=CapabilityAccessMode.NONE,
            external_impact=False,
            output_text="Draft email body.",
        )

        opa_input = build_opa_input_document(
            policy_input=build_policy_evaluation_input(task_state=task_state)
        )

        self.assertEqual(opa_input["external_communication"]["phase"], "draft_proposal")
        self.assertTrue(opa_input["external_communication"]["review_ready"])
        self.assertTrue(opa_input["actor"]["has_output_text"])


    def test_opa_policy_does_not_require_review_for_partial_external_draft(self) -> None:
        opa_path = shutil.which("opa")
        if opa_path is None:
            self.skipTest("OPA executable is not available.")

        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.external_communication = ExternalCommunicationState(
            phase=ExternalCommunicationPhase.DRAFT_IN_PROGRESS
        )
        task_state.external_communication.phase = ExternalCommunicationPhase.DRAFT_IN_PROGRESS
        task_state.external_communication.review_ready = False
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Identify the email audience and subject.",
                rationale="This is still partial draft work.",
                expected_outcome="A header concept only.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary="Partial draft metadata only.",
            capability_mode=CapabilityAccessMode.NONE,
            external_impact=False,
            output_text="Recipient: Ops Team Subject: Incident Update",
        )

        policy_result = evaluate_opa_first_governance(
            task_state=task_state,
            policy_engine_service=StubPolicyEngine(),
            thread_id="opa-partial-draft-input",
        )
        completed = subprocess.run(
            [
                opa_path,
                "eval",
                "--format",
                "json",
                "--data",
                "configs/opa/layer2_governance",
                "--stdin-input",
                "data.layer2.governance.decision",
            ],
            input=json.dumps(policy_result.decision_input),
            text=True,
            capture_output=True,
            check=True,
            cwd=REPO_ROOT,
        )
        parsed = json.loads(completed.stdout)
        decision = parsed["result"][0]["expressions"][0]["value"]

        self.assertEqual(decision["policy_decision"], "allow")
        self.assertNotIn("external_communication_review_required", decision["reason_codes"])


    def test_plain_intermediate_output_does_not_create_external_draft(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Identify the email audience, subject line, and intent.",
                rationale="Start with the draft header plan only.",
                expected_outcome="Recipient, subject, and purpose statement.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0

        _record_actor_execution(
            task_state,
            actor_result=ActorResult(
                plan_step_id="step_1",
                result_type=ActorResultType.INTERMEDIATE_OUTPUT,
                summary="Header plan prepared.",
                capability_mode=CapabilityAccessMode.NONE,
                external_impact=False,
                output_text=(
                    "Recipient: Acme Cloud Operations Team "
                    "Subject: Urgent incident verification required "
                    "Purpose/Intent: Request immediate manual verification."
                ),
                step_validation=StepValidation(
                    plan_step_id="step_1",
                    actor_self_confidence=0.35,
                    evidence_refs=(),
                    rationale_summary="This is only a partial draft fragment.",
                ),
            ),
        )

        self.assertIsNone(task_state.external_communication)


    def test_plain_markdown_outline_does_not_create_external_draft(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Identify the required email audience, purpose, and tone.",
                rationale="Start from a structured outline before the final compose step.",
                expected_outcome="A draft email outline with recipient, subject intent, and sections.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0

        _record_actor_execution(
            task_state,
            actor_result=ActorResult(
                plan_step_id="step_1",
                result_type=ActorResultType.INTERMEDIATE_OUTPUT,
                summary="Outline prepared.",
                capability_mode=CapabilityAccessMode.NONE,
                external_impact=False,
                output_text=(
                    "**Email Audience & Purpose** - **Recipient:** Acme Cloud Operations Team "
                    "(operations@acmecloud.com) - **Purpose:** Request immediate manual verification. "
                    "**Tone** - Professional and urgent. **Outline** 1. **Subject Line:** "
                    "\"Urgent: Manual Verification Required - EU Notification Pipeline Incident\" "
                    "2. **Greeting:** Direct address to the Operations Team. 3. **Incident Summary:** "
                    "Detection time and observed impact. 4. **Requested Actions:** Verify queues and backlog. "
                    "5. **Response Expectation:** Status update within 60 minutes."
                ),
                step_validation=StepValidation(
                    plan_step_id="step_1",
                    actor_self_confidence=0.35,
                    evidence_refs=(),
                    rationale_summary="This is still only a partial outline.",
                ),
            ),
        )

        self.assertIsNone(task_state.external_communication)


    def test_external_final_draft_becomes_review_ready_proposal(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.plan_steps = [
            PlanStep(
                step_id="step_6",
                objective="Ensure the overall tone and content are ready to send externally.",
                rationale="The final step should produce the complete governed email draft.",
                expected_outcome="A finalized email draft suitable for immediate sending.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0

        _record_actor_execution(
            task_state,
            actor_result=ActorResult(
                plan_step_id="step_6",
                result_type=ActorResultType.STEP_COMPLETED,
                summary="Final draft prepared.",
                capability_mode=CapabilityAccessMode.NONE,
                external_impact=False,
                output_text=(
                    "Subject: Incident verification request\n\n"
                    "Operations team,\n\n"
                    "We detected intermittent delivery failures at 09:12 CET on May 25, 2026. "
                    "Please verify delivery queues, inspect the retry backlog, confirm whether impact "
                    "extended beyond the notification pipeline, and report status back within 60 minutes."
                ),
                external_communication_draft=ExternalCommunicationDraft(
                    channel="email",
                    recipient="operations@example.com",
                    subject="Incident verification request",
                    body=(
                        "Operations team,\n\n"
                        "We detected intermittent delivery failures at 09:12 CET on May 25, 2026. "
                        "Please verify delivery queues, inspect the retry backlog, confirm whether "
                        "impact extended beyond the notification pipeline, and report status back "
                        "within 60 minutes."
                    ),
                ),
                step_validation=StepValidation(
                    plan_step_id="step_6",
                    actor_self_confidence=0.91,
                    evidence_refs=(),
                    rationale_summary="The final external draft is complete and reviewable.",
                ),
            ),
        )

        self.assertEqual(
            task_state.external_communication.phase,
            ExternalCommunicationPhase.DRAFT_PROPOSAL,
        )
        self.assertTrue(task_state.external_communication.review_ready)
        self.assertIn("Operations team", task_state.external_communication.draft.body)


    def test_actor_asks_for_clarification_routes_to_waiting_review(self) -> None:
        graph = _build_test_graph(actor_service=ClarificationActor())
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-clarification-governance"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertIsNotNone(task_state.pending_human_review_request)
        self.assertIsNone(task_state.last_human_review_decision)
        self.assertIn("__interrupt__", result)
        interrupt_payload = result["__interrupt__"][0].value
        self.assertEqual(interrupt_payload["current_state"], "waiting_human_review")
        self.assertEqual(interrupt_payload["resume_state"], "acting")
        self.assertIn("ask_clarification", interrupt_payload["allowed_decisions"])
        self.assertEqual(
            task_state.last_governance_decision.policy_decision,
            PolicyDecision.ASK_CLARIFICATION,
        )
        self.assertEqual(
            task_state.last_governance_decision.directive,
            GovernanceDirective.ASK_USER_CLARIFICATION,
        )
        self.assertEqual(len(task_state.governance_history), 1)
        self.assertEqual(
            task_state.governance_history[0].policy_decision,
            PolicyDecision.ASK_CLARIFICATION,
        )
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "governance",
            ],
        )


    def test_human_review_interrupts_and_resumes_with_approval(self) -> None:
        graph = _build_test_graph(policy_engine_service=ReviewPolicyEngine())
        thread_id = "layer2-hitl-approval"
        initial = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": thread_id}},
        )

        task_state = initial["task_state"]
        interrupt_payload = initial["__interrupt__"][0].value
        self.assertEqual(task_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertIsNotNone(task_state.pending_human_review_request)
        self.assertIsNone(task_state.last_human_review_decision)
        self.assertEqual(interrupt_payload["thread_id"], thread_id)
        self.assertEqual(interrupt_payload["current_state"], "waiting_human_review")
        self.assertEqual(interrupt_payload["resume_state"], "ready_tool_execution")
        self.assertIn("approve_continue", interrupt_payload["allowed_decisions"])

        resumed = graph.invoke(
            Command(
                resume={
                    "request_id": interrupt_payload["request_id"],
                    "decision_type": "approve_continue",
                    "note": "Approved to continue.",
                }
            ),
            config={"configurable": {"thread_id": thread_id}},
        )

        resumed_state = resumed["task_state"]
        self.assertEqual(resumed_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(
            resumed_state.last_human_review_decision.decision_type,
            HumanReviewDecisionType.APPROVE_CONTINUE,
        )
        self.assertEqual(
            resumed_state.last_human_review_request.request_id,
            interrupt_payload["request_id"],
        )
        self.assertEqual(len(resumed_state.human_review_history), 1)
        self.assertTrue(resumed_state.bootstrap_completed)

    # Verify an interrupt survives graph reconstruction through LangGraph's SQLite saver.
    def test_human_review_resumes_from_official_sqlite_checkpoint(self) -> None:
        temp_dir = REPO_ROOT / f"_sqlite_hitl_test_{uuid4().hex}"
        first_saver = None
        second_saver = None
        try:
            checkpoint_path = Path(temp_dir) / "hitl.sqlite3"
            first_saver = build_default_checkpointer(storage_path=checkpoint_path)
            first_graph = _build_test_graph(
                policy_engine_service=ReviewPolicyEngine(),
                checkpointer=first_saver,
            )
            thread_id = f"sqlite-hitl-{uuid4()}"
            initial = first_graph.invoke(
                {"intake_result": _build_research_intake_result()},
                config={"configurable": {"thread_id": thread_id}},
            )
            interrupt_payload = initial["__interrupt__"][0].value
            first_saver.conn.close()
            first_saver = None

            second_saver = build_default_checkpointer(storage_path=checkpoint_path)
            second_graph = _build_test_graph(
                policy_engine_service=ReviewPolicyEngine(),
                checkpointer=second_saver,
            )
            resumed = second_graph.invoke(
                Command(
                    resume={
                        "request_id": interrupt_payload["request_id"],
                        "decision_type": "approve_continue",
                    }
                ),
                config={"configurable": {"thread_id": thread_id}},
            )

            self.assertEqual(
                resumed["task_state"].current_state,
                RuntimeState.COMPLETED,
            )
            self.assertEqual(len(resumed["task_state"].human_review_history), 1)
        finally:
            if first_saver is not None:
                first_saver.conn.close()
            if second_saver is not None:
                second_saver.conn.close()
            shutil.rmtree(temp_dir, ignore_errors=True)


    def test_human_review_decision_routes_are_typed_and_request_id_is_checked(self) -> None:
        graph = _build_test_graph(policy_engine_service=ReviewPolicyEngine())
        thread_id = "layer2-hitl-routing"
        initial = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": thread_id}},
        )

        task_state = initial["task_state"]
        pending_request = task_state.pending_human_review_request
        assert pending_request is not None

        evidence_state = deepcopy(task_state)
        with self.assertRaisesRegex(ValueError, "cannot authorize"):
            _apply_human_review_decision(
                evidence_state,
                HumanReviewDecision(
                    request_id=pending_request.request_id,
                    decision_type=HumanReviewDecisionType.REQUEST_MORE_EVIDENCE,
                ),
            )
        self.assertEqual(evidence_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)

        replan_state = deepcopy(task_state)
        _apply_human_review_decision(
            replan_state,
            HumanReviewDecision(
                request_id=pending_request.request_id,
                decision_type=HumanReviewDecisionType.REPLAN,
            ),
        )
        self.assertEqual(replan_state.current_state, RuntimeState.REPLANNING)

        abort_state = deepcopy(task_state)
        _apply_human_review_decision(
            abort_state,
            HumanReviewDecision(
                request_id=pending_request.request_id,
                decision_type=HumanReviewDecisionType.ABORT,
            ),
        )
        self.assertEqual(abort_state.current_state, RuntimeState.ABORTED)
        self.assertEqual(abort_state.termination_reason, TerminationReason.HUMAN_ABORT)

        read_only_state = deepcopy(task_state)
        _apply_human_review_decision(
            read_only_state,
            HumanReviewDecision(
                request_id=pending_request.request_id,
                decision_type=HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY,
                approved_operation_digest=pending_request.operation_payload_digest,
            ),
        )
        self.assertEqual(read_only_state.current_state, RuntimeState.READY_TOOL_EXECUTION)
        self.assertTrue(read_only_state.execution_policy.read_only_mode)

        with self.assertRaises(ValueError):
            graph.invoke(
                Command(
                    resume={
                        "request_id": "wrong-request-id",
                        "decision_type": "approve_continue",
                    }
                ),
                config={"configurable": {"thread_id": thread_id}},
            )


    def test_human_review_modify_and_continue_applies_external_edit_and_resumes(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.human_review_resume_state = RuntimeState.ACTING
        task_state.external_communication = ExternalCommunicationState(
            phase=ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        )
        task_state.external_communication.phase = ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        task_state.external_communication.draft = ExternalCommunicationDraft(
            channel="email",
            recipient="ops@example.com",
            subject="Original subject",
            body="Original body",
            metadata={"priority": "high"},
        )
        pending_request = build_human_review_request(state=task_state, thread_id="external-edit")
        # Exercise edit application under an explicit correction-gate allowlist.
        pending_request = replace(
            pending_request,
            allowed_decisions=(
                HumanReviewDecisionType.MODIFY_AND_CONTINUE,
                HumanReviewDecisionType.REPLAN,
                HumanReviewDecisionType.ABORT,
            ),
        )
        task_state.pending_human_review_request = pending_request

        self.assertIn(
            HumanReviewDecisionType.MODIFY_AND_CONTINUE,
            pending_request.allowed_decisions,
        )

        _apply_human_review_decision(
            task_state,
            HumanReviewDecision(
                request_id=pending_request.request_id,
                decision_type=HumanReviewDecisionType.MODIFY_AND_CONTINUE,
                external_communication_edit=HumanReviewExternalEdit(
                    draft=ExternalCommunicationDraft(
                        channel="slack",
                        recipient="#ops",
                        subject="Updated subject",
                        body="Updated body",
                        metadata={"priority": "critical"},
                    ),
                    note="Clarified the target before submission.",
                ),
            ),
        )

        self.assertEqual(task_state.current_state, RuntimeState.READY_GOVERNANCE)
        self.assertEqual(
            task_state.external_communication.phase,
            ExternalCommunicationPhase.DRAFT_PROPOSAL,
        )
        self.assertEqual(task_state.external_communication.draft.channel, "slack")
        self.assertEqual(task_state.external_communication.draft.recipient, "#ops")
        self.assertEqual(task_state.external_communication.last_applied_edit.note, "Clarified the target before submission.")
        self.assertEqual(len(task_state.external_communication.edit_history), 1)
        self.assertEqual(len(task_state.human_review_history), 0)


    def test_human_review_modify_and_continue_rejects_invalid_request_id(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.human_review_resume_state = RuntimeState.ACTING
        task_state.external_communication = ExternalCommunicationState(
            phase=ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        )
        task_state.external_communication.phase = ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        task_state.external_communication.draft = ExternalCommunicationDraft(
            channel="email",
            recipient="ops@example.com",
            subject="Original subject",
            body="Original body",
        )
        pending_request = build_human_review_request(state=task_state, thread_id="external-edit")
        task_state.pending_human_review_request = pending_request

        with self.assertRaises(ValueError):
            _apply_human_review_decision(
                task_state,
                HumanReviewDecision(
                    request_id="wrong-request-id",
                    decision_type=HumanReviewDecisionType.MODIFY_AND_CONTINUE,
                    external_communication_edit=HumanReviewExternalEdit(
                        draft=ExternalCommunicationDraft(
                            channel="email",
                            recipient="ops@example.com",
                            subject="Updated subject",
                            body="Updated body",
                        ),
                    ),
                ),
            )


    def test_build_human_review_request_coerces_restored_external_draft_dict(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.human_review_resume_state = RuntimeState.ACTING
        task_state.external_communication = ExternalCommunicationState(
            phase=ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        )
        task_state.external_communication.phase = ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        task_state.external_communication.draft = {
            "channel": "email",
            "recipient": "ops@example.com",
            "subject": "Original subject",
            "body": "Original body",
            "metadata": {"priority": "high"},
        }

        pending_request = build_human_review_request(
            state=task_state,
            thread_id="external-edit-restored",
        )

        # Building an operator view must not mutate the canonical checkpoint state.
        self.assertIsInstance(task_state.external_communication.draft, dict)
        self.assertEqual(task_state.external_communication.draft["channel"], "email")
        self.assertIsInstance(pending_request.external_communication_draft, ExternalCommunicationDraft)
        self.assertEqual(pending_request.external_communication_draft.recipient, "ops@example.com")


    def test_human_review_node_accepts_modify_and_continue_and_resumes(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.human_review_resume_state = RuntimeState.ACTING
        task_state.external_communication = ExternalCommunicationState(
            phase=ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        )
        task_state.external_communication.phase = ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        task_state.external_communication.draft = ExternalCommunicationDraft(
            channel="email",
            recipient="ops@example.com",
            subject="Original subject",
            body="Original body",
            metadata={"priority": "high"},
        )
        pending_request = build_human_review_request(
            state=task_state,
            thread_id="external-edit-node",
        )
        # Exercise the node adapter with a request that explicitly authorizes edits.
        pending_request = replace(
            pending_request,
            allowed_decisions=(
                HumanReviewDecisionType.MODIFY_AND_CONTINUE,
                HumanReviewDecisionType.REPLAN,
                HumanReviewDecisionType.ABORT,
            ),
        )
        task_state.pending_human_review_request = pending_request

        resume_payload = {
            "request_id": pending_request.request_id,
            "thread_id": pending_request.thread_id,
            "decision_type": "modify_and_continue",
            "note": "Submit the updated draft.",
            "reviewer_id": "operator-1",
            "external_communication_edit": {
                "draft": {
                    "channel": "slack",
                    "recipient": "#ops",
                    "subject": "Updated subject",
                    "body": "Updated body",
                    "metadata": {"priority": "critical"},
                },
                "note": "Updated the target before submission.",
            },
        }

        with patch("layer2_runtime_governance.hitl.interrupt", return_value=resume_payload):
            result_state = human_review(task_state, thread_id="external-edit-node")

        self.assertEqual(result_state.current_state, RuntimeState.READY_GOVERNANCE)
        self.assertIsNone(result_state.pending_human_review_request)
        self.assertEqual(result_state.last_human_review_decision.decision_type, HumanReviewDecisionType.MODIFY_AND_CONTINUE)
        self.assertEqual(
            result_state.external_communication.phase,
            ExternalCommunicationPhase.DRAFT_PROPOSAL,
        )
        self.assertEqual(result_state.external_communication.draft.channel, "slack")
        self.assertEqual(result_state.external_communication.draft.recipient, "#ops")
        self.assertEqual(result_state.external_communication.last_applied_edit.note, "Updated the target before submission.")
        self.assertEqual(len(result_state.external_communication.edit_history), 1)


    def test_judge_is_invoked_for_external_draft(self) -> None:
        state = _build_actor_ready_state_for_judge()
        external_intake = _build_external_communication_intake_result_for_governance()
        state = TaskState.from_intake(external_intake)
        state.current_state = RuntimeState.ACTING
        state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Draft the external note.",
                rationale="The task is in the external branch.",
                expected_outcome="One local draft.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        state.current_plan_step_index = 0
        state.external_communication = ExternalCommunicationState(
            phase=ExternalCommunicationPhase.DRAFT_PROPOSAL
        )
        state.external_communication.phase = ExternalCommunicationPhase.DRAFT_PROPOSAL
        state.external_communication.review_ready = True
        state.external_communication.draft = ExternalCommunicationDraft(
            channel="email",
            recipient="ops@example.com",
            subject="Operational update",
            body="Draft email body.",
        )
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary="Draft prepared.",
            capability_mode=CapabilityAccessMode.NONE,
            output_text="Draft email body.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.72,
                evidence_refs=(),
                rationale_summary="Draft is complete.",
            ),
        )

        result = governance(
            state,
            policy_engine_service=JudgeRequiredPolicyEngine(),
            judge_service=StaticJudge(_judge_wait_review_assessment()),
            thread_id="judge-external-draft",
        )

        self.assertTrue(result.last_judge_assessment.invoked)
        self.assertEqual(
            result.last_judge_assessment.evaluated_operation_digest,
            result.external_operation_proposal.payload_digest,
        )


if __name__ == "__main__":
    unittest.main()
