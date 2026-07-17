from __future__ import annotations

from tests.layer2_runtime_fixtures import *
from layer2_runtime_governance.infrastructure.checkpointing import (
    build_default_checkpointer,
)


class Layer2HumanReviewRuntimeTests(unittest.TestCase):
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
            blocking_reason=(
                "Actor provider returned no usable runtime action: OpenRouter returned no "
                "tool call and no assistant content. finish_reason='stop'."
            ),
        )

        result = governance(
            task_state,
            policy_engine_service=StubPolicyEngine(),
            judge_service=StaticJudge(_judge_wait_review_assessment()),
            thread_id="provider-empty-replan",
        )

        self.assertEqual(result.current_state, RuntimeState.REPLANNING)
        self.assertIsNone(result.pending_human_review_request)
        self.assertEqual(result.last_governance_decision.directive, GovernanceDirective.REPLAN)
        self.assertEqual(
            result.external_communication.phase,
            ExternalCommunicationPhase.EVIDENCE_COLLECTION,
        )


    def test_opa_policy_denies_write_capability_without_external_impact(self) -> None:
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

        self.assertEqual(decision["policy_decision"], "deny")
        self.assertIn("write_capability_blocked", decision["reason_codes"])


    def test_opa_input_marks_external_communication_draft_phase(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
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
        task_state.external_communication.phase = ExternalCommunicationPhase.DRAFT_IN_PROGRESS
        task_state.external_communication.review_ready = False
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Identify the email audience and subject.",
                rationale="This is still partial draft work.",
                expected_outcome="A header concept only.",
                status=PlanStepStatus.IN_PROGRESS,
                step_kind=PlanStepKind.LOCAL_TRANSFORMATION,
                evidence_requirement=EvidenceSupportLevel.LOCAL_ONLY,
                claim_precision_ceiling=EvidenceSupportLevel.LOCAL_ONLY,
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


    def test_external_partial_draft_progress_is_preserved_as_markdown_body(self) -> None:
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
                    objective_satisfied=False,
                    evidence_sufficient=False,
                    confidence=0.35,
                    next_action=StepValidationDecision.REQUEST_MORE_EVIDENCE,
                    rationale_summary="This is only a partial draft fragment.",
                ),
            ),
        )

        self.assertEqual(
            task_state.external_communication.phase,
            ExternalCommunicationPhase.DRAFT_IN_PROGRESS,
        )
        self.assertFalse(task_state.external_communication.review_ready)
        self.assertIsNone(task_state.external_communication.draft.recipient)
        self.assertIsNone(task_state.external_communication.draft.subject)
        self.assertIn("Recipient: Acme Cloud Operations Team", task_state.external_communication.draft.body)
        self.assertIn("Subject: Urgent incident verification required", task_state.external_communication.draft.body)


    def test_external_markdown_outline_partial_draft_stays_in_single_body(self) -> None:
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
                    objective_satisfied=False,
                    evidence_sufficient=False,
                    confidence=0.35,
                    next_action=StepValidationDecision.REQUEST_MORE_EVIDENCE,
                    rationale_summary="This is still only a partial outline.",
                ),
            ),
        )

        self.assertIsNone(task_state.external_communication.draft.recipient)
        self.assertIsNone(task_state.external_communication.draft.subject)
        self.assertIn("**Email Audience & Purpose**", task_state.external_communication.draft.body)
        self.assertIn("**Subject Line:**", task_state.external_communication.draft.body)
        self.assertIn("**Requested Actions:**", task_state.external_communication.draft.body)


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
                result_type=ActorResultType.INTERMEDIATE_OUTPUT,
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
                step_validation=StepValidation(
                    plan_step_id="step_6",
                    objective_satisfied=True,
                    evidence_sufficient=True,
                    confidence=0.91,
                    next_action=StepValidationDecision.COMPLETE_STEP,
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
        _apply_human_review_decision(
            evidence_state,
            HumanReviewDecision(
                request_id=pending_request.request_id,
                decision_type=HumanReviewDecisionType.REQUEST_MORE_EVIDENCE,
            ),
        )
        self.assertEqual(evidence_state.current_state, RuntimeState.ACTING)

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

        read_only_state = deepcopy(task_state)
        _apply_human_review_decision(
            read_only_state,
            HumanReviewDecision(
                request_id=pending_request.request_id,
                decision_type=HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY,
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
        task_state.external_communication.phase = ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        task_state.external_communication.draft = ExternalCommunicationDraft(
            channel="email",
            recipient="ops@example.com",
            subject="Original subject",
            body="Original body",
            metadata={"priority": "high"},
        )
        pending_request = build_human_review_request(state=task_state, thread_id="external-edit")
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

        self.assertEqual(task_state.current_state, RuntimeState.ACTING)
        self.assertEqual(
            task_state.external_communication.phase,
            ExternalCommunicationPhase.APPROVED_FOR_EXECUTION,
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

        self.assertIsInstance(task_state.external_communication.draft, ExternalCommunicationDraft)
        self.assertEqual(task_state.external_communication.draft.channel, "email")
        self.assertIsInstance(pending_request.external_communication_draft, ExternalCommunicationDraft)
        self.assertEqual(pending_request.external_communication_draft.recipient, "ops@example.com")


    def test_human_review_node_accepts_modify_and_continue_and_resumes(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.human_review_resume_state = RuntimeState.ACTING
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

        self.assertEqual(result_state.current_state, RuntimeState.ACTING)
        self.assertIsNone(result_state.pending_human_review_request)
        self.assertEqual(result_state.last_human_review_decision.decision_type, HumanReviewDecisionType.MODIFY_AND_CONTINUE)
        self.assertEqual(
            result_state.external_communication.phase,
            ExternalCommunicationPhase.APPROVED_FOR_EXECUTION,
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
        state.external_communication.phase = ExternalCommunicationPhase.DRAFT_PROPOSAL
        state.external_communication.review_ready = True
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary="Draft prepared.",
            capability_mode=CapabilityAccessMode.NONE,
            output_text="Draft email body.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                objective_satisfied=True,
                evidence_sufficient=True,
                confidence=0.72,
                next_action=StepValidationDecision.COMPLETE_STEP,
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
        self.assertEqual(result.last_judge_assessment.evaluated_target, "opa_required")


if __name__ == "__main__":
    unittest.main()
