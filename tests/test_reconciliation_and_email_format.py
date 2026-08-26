from __future__ import annotations

import unittest
from datetime import datetime, timezone

from layer2_runtime_governance.domain import (
    ExternalCommunicationDraft,
    RuntimeFlag,
)
from layer2_runtime_governance.hitl import (
    build_human_review_request,
    human_review_reasons_authorize_external_execution,
)
from layer3_tool_bus_external_capabilities.external_capability_mcp.markdown_email import (
    render_markdown_email_html,
)
from json_serialization import to_jsonable
from layer1_intake import ExecutionIntent
from layer2_runtime_governance.domain.serialization import (
    coerce_external_communication_draft,
)
from layer3_tool_bus_external_capabilities.external_operations.mcp.executor import (
    _communication_arguments,
)
from layer3_tool_bus_external_capabilities.external_operations import (
    CommunicationBodyFormat,
    CommunicationOperationPayload,
    DestinationClass,
    ExternalExecutionAuthorizationSource,
    build_external_execution_request,
    ExternalExecutionReceipt,
    ExternalExecutionStatus,
    normalize_communication_operation,
)

from tests.layer2_runtime_fixtures import _build_policy_input_task_state


class IndeterminateReconciliationReviewTests(unittest.TestCase):
    def _state_awaiting_reconciliation(self):
        state = _build_policy_input_task_state()
        now = datetime.now(timezone.utc)
        state.last_external_execution_receipt = ExternalExecutionReceipt(
            operation_id="op-1",
            capability_id="communication.email.send",
            payload_digest="a" * 64,
            status=ExternalExecutionStatus.INDETERMINATE,
            idempotency_key="key-1",
            started_at=now,
            completed_at=now,
            external_reference="<msg-id@example.test>",
            summary="The SMTP connection ended without a reliable delivery outcome.",
        )
        state.runtime_flags.append(
            RuntimeFlag.EXTERNAL_EXECUTION_RECONCILIATION_REQUIRED
        )
        return state

    # The reconciliation gate must ask its own question, not repeat the approval gate's.
    def test_reconciliation_review_uses_its_own_reason_code(self) -> None:
        state = self._state_awaiting_reconciliation()

        request = build_human_review_request(state=state, thread_id="t-1")

        self.assertIn("external_execution_reconciliation_required", request.reason_codes)
        self.assertNotIn("capability_approval_required", request.reason_codes)

    # The operator must read what is unresolved and see the connector evidence.
    def test_reconciliation_summary_describes_the_unresolved_outcome(self) -> None:
        state = self._state_awaiting_reconciliation()

        summary = build_human_review_request(state=state, thread_id="t-1").review_summary

        self.assertIsNotNone(summary)
        self.assertIn("could not be confirmed", summary)
        self.assertIn("<msg-id@example.test>", summary)
        self.assertIn("never authorizes a new execution", summary)
        self.assertNotIn("capability approval mode", summary.lower())

    # Reconciliation settles an outcome; it can never authorize another connector call.
    def test_reconciliation_reason_cannot_authorize_external_execution(self) -> None:
        self.assertFalse(
            human_review_reasons_authorize_external_execution(
                ("external_execution_reconciliation_required",)
            )
        )
        self.assertFalse(
            human_review_reasons_authorize_external_execution(
                (
                    "capability_approval_required",
                    "external_execution_reconciliation_required",
                )
            )
        )


class CommunicationBodyFormatTests(unittest.TestCase):
    def _proposal(self, body_format: str):
        return normalize_communication_operation(
            operation_id="op-1",
            plan_step_id="step_1",
            capability_id="communication.email.send",
            draft=ExternalCommunicationDraft(
                channel="email",
                recipient="someone@example.test",
                subject="Subject",
                body="# Title\n\n- point",
                body_format=body_format,
            ),
            execution_candidate=True,
        )

    def test_declared_format_reaches_the_canonical_payload(self) -> None:
        self.assertEqual(
            self._proposal("markdown").payload.body_format,
            CommunicationBodyFormat.MARKDOWN,
        )
        self.assertEqual(
            self._proposal("plain").payload.body_format,
            CommunicationBodyFormat.PLAIN,
        )

    # An unknown declaration must degrade to plain text rather than be trusted.
    def test_unknown_format_degrades_to_plain(self) -> None:
        self.assertEqual(
            self._proposal("fancy-html").payload.body_format,
            CommunicationBodyFormat.PLAIN,
        )

    # The format participates in the digest, so the reviewer approves the rendering too.
    def test_format_changes_the_payload_digest(self) -> None:
        self.assertNotEqual(
            self._proposal("markdown").payload_digest,
            self._proposal("plain").payload_digest,
        )


class MarkdownEmailRenderingTests(unittest.TestCase):
    def test_renders_the_briefing_subset(self) -> None:
        html = render_markdown_email_html(
            "# Briefing\n\n## Section\n**Bold** text\n\n- one\n- two\n\n"
            "1. first\n\n---\n\nSee [paper](https://example.test/a).\n"
        )

        self.assertIn("<h1", html)
        self.assertIn("<h2", html)
        self.assertIn("<strong>Bold</strong>", html)
        self.assertIn("<ul", html)
        self.assertIn("<ol", html)
        self.assertIn("<hr", html)
        self.assertIn('<a href="https://example.test/a">paper</a>', html)
        self.assertNotIn("##", html)

    # The rendering is a presentation transform and must never introduce active content.
    def test_rendering_neutralizes_embedded_markup_and_unsafe_links(self) -> None:
        html = render_markdown_email_html(
            "<script>alert(1)</script>\n\n[x](javascript:alert(1))\n"
        )

        self.assertNotIn("<script", html.lower())
        self.assertIn("&lt;script&gt;", html)
        # A non-http(s) target never becomes a link; it survives as inert text.
        self.assertNotIn("href=\"javascript:", html.lower())
        self.assertNotIn("<a ", html.lower())
        self.assertIn("[x](javascript:alert(1))", html)


class BodyFormatSurvivesTheRuntimeBoundariesTests(unittest.TestCase):
    # Regression for benchmark-web-email-11: checkpoint restore rebuilt the payload from
    # stored primitives, so the enum arrived as a bare string and the connector raised
    # AttributeError on `.value`. Coercion belongs to the canonical payload, once.
    def test_payload_coerces_a_restored_string_format(self) -> None:
        for raw, expected in (
            ("markdown", CommunicationBodyFormat.MARKDOWN),
            ("MARKDOWN", CommunicationBodyFormat.MARKDOWN),
            ("plain", CommunicationBodyFormat.PLAIN),
            ("unknown-format", CommunicationBodyFormat.PLAIN),
            (None, CommunicationBodyFormat.PLAIN),
        ):
            with self.subTest(raw=raw):
                payload = CommunicationOperationPayload(
                    channel="email",
                    recipient="a@b.test",
                    subject="S",
                    body="B",
                    destination_class=DestinationClass.EXTERNAL,
                    body_format=raw,
                )
                self.assertIs(payload.body_format, expected)
                self.assertEqual(payload.body_format.value, expected.value)

    # Regression for benchmark-web-email-11: the actor declared markdown on the draft but
    # the runtime rebuild dropped it, so the canonical payload silently said plain.
    def test_declared_format_survives_checkpoint_and_normalization(self) -> None:
        draft = ExternalCommunicationDraft(
            channel="email",
            recipient="a@b.test",
            subject="S",
            body="# Title",
            body_format="markdown",
        )

        restored = coerce_external_communication_draft(to_jsonable(draft))
        self.assertEqual(restored.body_format, "markdown")

        proposal = normalize_communication_operation(
            operation_id="op-1",
            plan_step_id="step_1",
            capability_id="communication.email.send",
            draft=restored,
            execution_candidate=True,
        )
        self.assertIs(proposal.payload.body_format, CommunicationBodyFormat.MARKDOWN)

    # The connector must receive the declared contract, not a default.
    def test_connector_arguments_carry_the_declared_format(self) -> None:
        proposal = normalize_communication_operation(
            operation_id="op-1",
            plan_step_id="step_1",
            capability_id="communication.email.send",
            draft=ExternalCommunicationDraft(
                channel="email",
                recipient="a@b.test",
                subject="S",
                body="# Title",
                body_format="markdown",
            ),
            execution_candidate=True,
        )
        request = build_external_execution_request(
            proposal=proposal,
            authorized_payload_digest=proposal.payload_digest,
            execution_intent=ExecutionIntent.EXECUTE_AFTER_GOVERNANCE,
            authorization_source=ExternalExecutionAuthorizationSource.HUMAN_REVIEW,
            reviewer_id="operator-local",
        )

        self.assertEqual(_communication_arguments(request)["body_format"], "markdown")


if __name__ == "__main__":
    unittest.main()
