from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from layer2_runtime_governance import GovernanceDirective, RuntimeState

from tests.b6_positive_fixtures import (
    B6_EVIDENCE_THREAD_ID,
    B6_TRAJECTORY_THREAD_ID,
    run_b6_evidence_run,
    run_b6_trajectory_run,
)


class B6PositiveRunsTests(unittest.TestCase):
    # Verify the Evidence burden activates the request-more-evidence recovery path.
    def test_evidence_burden_runs_through_runtime_and_preserves_deficit_code(self) -> None:
        with TemporaryDirectory(prefix="b6-evidence-") as temp_dir:
            artifact_root = Path(temp_dir)
            trace = run_b6_evidence_run(artifact_root=artifact_root)
            task_state = trace["final_result"]["task_state"]

            self.assertEqual(task_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
            self.assertEqual(task_state.evidence_request_count, 1)
            self.assertEqual(len(task_state.plan_steps), 1)
            self.assertEqual(task_state.plan_steps[0].evidence_burden.min_independent_sources, 2)
            self.assertEqual(
                [decision.directive for decision in task_state.governance_history],
                [
                    GovernanceDirective.CONTINUE,
                    GovernanceDirective.REQUEST_MORE_EVIDENCE,
                    GovernanceDirective.ASK_USER_CLARIFICATION,
                ],
            )

            evidence_decision = task_state.governance_history[1]
            evidence_facts = evidence_decision.pillar_results["evidence"]["raw_facts"]
            self.assertTrue(evidence_facts["coverage_evaluated"])
            self.assertEqual(evidence_facts["min_independent_sources_required"], 2)
            self.assertEqual(evidence_facts["distinct_independent_sources_count"], 1)
            self.assertFalse(evidence_facts["burden_satisfied"])
            self.assertIn("insufficient_independent_sources", evidence_facts["deficit_codes"])
            self.assertIn(
                "insufficient_independent_sources",
                task_state.governance_action_feedback[0].finding_codes,
            )

            paths = artifact_root / B6_EVIDENCE_THREAD_ID
            self.assertTrue((paths / "full-trace-2.json").exists())
            self.assertTrue((paths / "trace_summary.md").exists())
            persisted = json.loads((paths / "full-trace-2.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["runtime"]["current_state"], "waiting_human_review")

    # Verify three observable replans reach the configured human-review ceiling.
    def test_replan_ceiling_runs_through_runtime_and_routes_to_human_review(self) -> None:
        with TemporaryDirectory(prefix="b6-trajectory-") as temp_dir:
            artifact_root = Path(temp_dir)
            trace = run_b6_trajectory_run(artifact_root=artifact_root)
            task_state = trace["final_result"]["task_state"]

            self.assertEqual(task_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
            self.assertEqual(len(task_state.replan_checkpoints), 3)
            self.assertEqual(
                sum(item.node_name == "replanner" for item in task_state.step_history),
                3,
            )
            self.assertEqual(task_state.tool_call_count, 4)
            self.assertEqual(task_state.governance_history[-1].directive, GovernanceDirective.WAIT_HUMAN_REVIEW)
            self.assertIn("replan_ceiling_reached", task_state.governance_history[-1].reason_codes)

            trajectory_facts = task_state.governance_history[-1].pillar_results["trajectory"]["raw_facts"]
            self.assertEqual(trajectory_facts["replan_count"], 3)
            self.assertTrue(trajectory_facts["observable_change_since_last_replan"])
            self.assertTrue(
                task_state.governance_history[-1].pillar_results["trajectory"][
                    "replan_ceiling_reached"
                ]
            )

            paths = artifact_root / B6_TRAJECTORY_THREAD_ID
            self.assertTrue((paths / "full-trace-2.json").exists())
            self.assertTrue((paths / "trace_summary.md").exists())
            persisted = json.loads((paths / "full-trace-2.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["runtime"]["current_state"], "waiting_human_review")
