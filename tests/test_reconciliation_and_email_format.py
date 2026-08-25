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
from layer3_tool_bus_external_capabilities.external_operations import (
    CommunicationBodyFormat,
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


if __name__ == "__main__":
    unittest.main()
