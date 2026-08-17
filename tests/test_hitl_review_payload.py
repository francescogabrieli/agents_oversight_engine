from __future__ import annotations

import unittest
from unittest import mock

from layer2_runtime_governance.domain import (
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    ExternalCommunicationState,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewRequest,
    RuntimeState,
    TaskState,
    coerce_human_review_decision_payload,
    set_external_communication_phase,
)
from main_layer2_dashboard import DashboardSession, _parse_human_review_decision


class HitlReviewPayloadTests(unittest.TestCase):
    # Verify dashboard edits retain immutable copied-recipient fields omitted by its form.
    def test_dashboard_edit_preserves_bounded_cc_and_bcc(self) -> None:
        request = _review_request(
            draft=ExternalCommunicationDraft(
                channel="email",
                recipient="owner@example.com",
                subject="Review",
                body="Original body",
                cc=("audit@example.com",),
                bcc=("archive@example.com",),
            ),
            allowed_decisions=(HumanReviewDecisionType.MODIFY_AND_CONTINUE,),
        )
        session = _dashboard_session(request)

        decision = _parse_human_review_decision(
            {
                "thread_id": request.thread_id,
                "request_id": request.request_id,
                "decision_type": "modify_and_continue",
                "external_communication_edit": {
                    "draft": {
                        "channel": "email",
                        "recipient": "owner@example.com",
                        "subject": "Review",
                        "body": "  Revised body\r\n\r\nSecond line  ",
                        "metadata": {},
                    }
                },
            },
            session=session,
        )

        edit = decision.external_communication_edit
        self.assertIsNotNone(edit)
        assert edit is not None
        self.assertEqual(edit.draft.body, "  Revised body\n\nSecond line  ")
        self.assertEqual(edit.draft.cc, ("audit@example.com",))
        self.assertEqual(edit.draft.bcc, ("archive@example.com",))

    # Verify copied-recipient bounds are enforced before either live path stores an edit.
    def test_shared_coercer_rejects_unbounded_copy_recipients(self) -> None:
        request = _review_request(
            draft=ExternalCommunicationDraft(
                channel="email",
                body="Original body",
            ),
            allowed_decisions=(HumanReviewDecisionType.MODIFY_AND_CONTINUE,),
        )

        with self.assertRaisesRegex(ValueError, "recipient bound"):
            coerce_human_review_decision_payload(
                {
                    "request_id": request.request_id,
                    "decision_type": "modify_and_continue",
                    "external_communication_edit": {
                        "draft": {
                            "channel": "email",
                            "body": "Revised body",
                            "cc": [f"copy-{index}@example.com" for index in range(21)],
                        }
                    },
                },
                pending_request=request,
            )

    # Verify typed operation edits consume the same body and recipient normalization surface.
    def test_operation_edit_uses_canonical_communication_normalization(self) -> None:
        request = _review_request(
            allowed_decisions=(HumanReviewDecisionType.MODIFY_AND_CONTINUE,),
        )

        decision = coerce_human_review_decision_payload(
            {
                "request_id": request.request_id,
                "decision_type": "modify_and_continue",
                "external_operation_edit": {
                    "payload": {
                        "payload_type": "communication",
                        "channel": "email",
                        "recipient": "owner@example.com",
                        "subject": "Review",
                        "body": "  First line\r\nSecond line  ",
                        "destination_class": "external",
                        "cc": ["  audit@example.com  "],
                        "bcc": ["archive@example.com"],
                    }
                },
            },
            pending_request=request,
        )

        edit = decision.external_operation_edit
        self.assertIsNotNone(edit)
        assert edit is not None
        self.assertEqual(edit.payload.body, "  First line\nSecond line  ")
        self.assertEqual(edit.payload.cc, ("audit@example.com",))
        self.assertEqual(edit.payload.bcc, ("archive@example.com",))

    # Verify JSON and typed runtime decisions share the nonempty clarification invariant.
    def test_shared_coercer_requires_nonempty_clarification_for_all_live_shapes(self) -> None:
        request = _review_request(
            allowed_decisions=(HumanReviewDecisionType.ASK_CLARIFICATION,),
        )
        payloads = (
            {
                "request_id": request.request_id,
                "decision_type": "ask_clarification",
                "clarification_text": "   ",
            },
            HumanReviewDecision(
                request_id=request.request_id,
                decision_type=HumanReviewDecisionType.ASK_CLARIFICATION,
                clarification_text="   ",
            ),
        )

        for payload in payloads:
            with (
                self.subTest(payload_type=type(payload).__name__),
                self.assertRaisesRegex(ValueError, "Clarification text is required"),
            ):
                coerce_human_review_decision_payload(
                    payload,
                    pending_request=request,
                )

    # Verify every runtime caller observes the same canonical phase mutation semantics.
    def test_domain_phase_transition_updates_only_the_typed_branch(self) -> None:
        state = TaskState(
            intake_payload=mock.Mock(),
            current_state=RuntimeState.ACTING,
            external_communication=ExternalCommunicationState(
                phase=ExternalCommunicationPhase.DRAFT_IN_PROGRESS,
                review_ready=True,
            ),
        )

        set_external_communication_phase(
            state,
            phase=ExternalCommunicationPhase.BLOCKED,
            summary="The proposal was blocked.",
            review_ready=False,
        )

        branch = state.external_communication
        self.assertIsNotNone(branch)
        assert branch is not None
        self.assertEqual(branch.phase, ExternalCommunicationPhase.BLOCKED)
        self.assertFalse(branch.review_ready)
        self.assertEqual(branch.last_transition_summary, "The proposal was blocked.")


# Build the smallest immutable review request needed by the live coercion boundary.
def _review_request(
    *,
    draft: ExternalCommunicationDraft | None = None,
    allowed_decisions: tuple[HumanReviewDecisionType, ...],
) -> HumanReviewRequest:
    return HumanReviewRequest(
        request_id="thread-review:hitl",
        thread_id="thread-review",
        current_state=RuntimeState.WAITING_HUMAN_REVIEW,
        resume_state=RuntimeState.ACTING,
        current_plan_step_id="step_1",
        current_plan_step_objective="Prepare the governed proposal.",
        actor_result_type=None,
        current_tool_request=None,
        governance_directive=None,
        governance_summary="Human review is required.",
        external_communication_draft=draft,
        allowed_decisions=allowed_decisions,
    )


# Wrap one canonical pending request in the real dashboard session contract.
def _dashboard_session(request: HumanReviewRequest) -> DashboardSession:
    state = TaskState(
        intake_payload=mock.Mock(),
        current_state=RuntimeState.WAITING_HUMAN_REVIEW,
        pending_human_review_request=request,
    )
    return DashboardSession(
        thread_id=request.thread_id,
        intake_result=None,
        final_result={"task_state": state},
        checkpointer=object(),
        artifact_store=mock.Mock(),
    )


if __name__ == "__main__":
    unittest.main()
