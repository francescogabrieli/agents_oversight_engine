from __future__ import annotations

from datetime import datetime, timedelta, timezone
import shutil
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

from layer4_state_evidence_observability.infrastructure.review_decision_inbox import (
    SQLiteReviewDecisionInbox,
)


class ReviewDecisionInboxTests(unittest.TestCase):
    # Create one isolated durable inbox and controllable UTC clock per test.
    def setUp(self) -> None:
        self.temp_root = REPO_ROOT / "_test_review_decision_inbox"
        shutil.rmtree(self.temp_root, ignore_errors=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.now = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
        self.inbox = SQLiteReviewDecisionInbox(
            storage_path=self.temp_root / "decisions.sqlite3",
            lease_timeout_seconds=10,
            clock=lambda: self.now,
        )

    # Close SQLite before removing its isolated files.
    def tearDown(self) -> None:
        self.inbox.close()
        shutil.rmtree(self.temp_root, ignore_errors=True)

    # Verify one live owner claims and consumes the exact immutable submission.
    def test_owner_claims_and_consumes_submitted_decision(self) -> None:
        self.inbox.acquire_owner(
            thread_id="thread-1",
            owner_id="owner-1",
            process_id=42,
            terminal_label="terminal-42",
            status="waiting_human_review",
        )
        submitted = self.inbox.submit(
            thread_id="thread-1",
            request_id="thread-1:review:1",
            decision_type="approve_continue",
            decision_payload={
                "request_id": "thread-1:review:1",
                "decision_type": "approve_continue",
            },
            reviewer_id="operator-local",
            task_goal="Send the governed result.",
            review_summary="Approval is required.",
        )

        claimed = self.inbox.claim_next(
            thread_id="thread-1",
            request_id="thread-1:review:1",
            owner_id="owner-1",
        )

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.submission_id, submitted.submission_id)
        self.assertEqual(claimed.status.value, "claimed")
        self.inbox.mark_consumed(
            submission_id=claimed.submission_id,
            owner_id="owner-1",
        )
        resolved = self.inbox.get_for_request(
            thread_id="thread-1",
            request_id="thread-1:review:1",
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.status.value, "consumed")

    # Verify duplicate clicks are idempotent while conflicting decisions are rejected.
    def test_submission_is_idempotent_per_review_request(self) -> None:
        payload = {
            "request_id": "thread-1:review:1",
            "decision_type": "replan",
        }
        first = self.inbox.submit(
            thread_id="thread-1",
            request_id="thread-1:review:1",
            decision_type="replan",
            decision_payload=payload,
            reviewer_id=None,
            task_goal=None,
            review_summary=None,
        )
        second = self.inbox.submit(
            thread_id="thread-1",
            request_id="thread-1:review:1",
            decision_type="replan",
            decision_payload=payload,
            reviewer_id=None,
            task_goal=None,
            review_summary=None,
        )

        self.assertEqual(first.submission_id, second.submission_id)
        with self.assertRaisesRegex(ValueError, "different decision"):
            self.inbox.submit(
                thread_id="thread-1",
                request_id="thread-1:review:1",
                decision_type="abort",
                decision_payload={
                    "request_id": "thread-1:review:1",
                    "decision_type": "abort",
                },
                reviewer_id=None,
                task_goal=None,
                review_summary=None,
            )

    # Verify owner fencing expires and recent review visibility lasts only its TTL.
    def test_owner_lease_and_recent_review_ttl_are_bounded(self) -> None:
        self.inbox.acquire_owner(
            thread_id="thread-1",
            owner_id="owner-1",
            process_id=1,
            terminal_label="terminal-1",
            status="waiting_human_review",
        )
        with self.assertRaisesRegex(ValueError, "live runtime owner"):
            self.inbox.acquire_owner(
                thread_id="thread-1",
                owner_id="owner-2",
                process_id=2,
                terminal_label="terminal-2",
                status="waiting_human_review",
            )
        self.inbox.submit(
            thread_id="thread-1",
            request_id="thread-1:review:1",
            decision_type="abort",
            decision_payload={
                "request_id": "thread-1:review:1",
                "decision_type": "abort",
            },
            reviewer_id=None,
            task_goal=None,
            review_summary=None,
        )

        self.assertEqual(len(self.inbox.list_recent(within_seconds=120)), 1)
        self.now += timedelta(seconds=121)
        self.assertIsNone(self.inbox.get_live_owner(thread_id="thread-1"))
        self.assertEqual(self.inbox.list_recent(within_seconds=120), ())
        lease = self.inbox.acquire_owner(
            thread_id="thread-1",
            owner_id="owner-2",
            process_id=2,
            terminal_label="terminal-2",
            status="waiting_human_review",
        )
        self.assertEqual(lease.owner_id, "owner-2")

    # Recover only the command that still matches the canonical pending checkpoint request.
    def test_failed_or_stale_claim_is_recoverable_by_exact_request(self) -> None:
        self.inbox.acquire_owner(
            thread_id="thread-1",
            owner_id="owner-1",
            process_id=1,
            terminal_label="terminal-1",
            status="waiting_human_review",
        )
        self.inbox.submit(
            thread_id="thread-1",
            request_id="review-old",
            decision_type="abort",
            decision_payload={"request_id": "review-old", "decision_type": "abort"},
            reviewer_id=None,
            task_goal=None,
            review_summary=None,
        )
        current = self.inbox.submit(
            thread_id="thread-1",
            request_id="review-current",
            decision_type="replan",
            decision_payload={"request_id": "review-current", "decision_type": "replan"},
            reviewer_id=None,
            task_goal=None,
            review_summary=None,
        )

        claimed = self.inbox.claim_next(
            thread_id="thread-1",
            request_id="review-current",
            owner_id="owner-1",
        )
        self.assertEqual(claimed.submission_id, current.submission_id)
        self.inbox.mark_failed(
            submission_id=claimed.submission_id,
            owner_id="owner-1",
            error="provider interrupted",
        )

        recovered = self.inbox.claim_next(
            thread_id="thread-1",
            request_id="review-current",
            owner_id="owner-1",
        )
        self.assertEqual(recovered.submission_id, current.submission_id)
        self.assertEqual(recovered.status.value, "claimed")
        self.assertIsNone(recovered.error)


if __name__ == "__main__":
    unittest.main()
