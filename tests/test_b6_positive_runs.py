from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from layer2_runtime_governance import GovernanceDirective, RuntimeState
from scripts.generate_b6_positive_runs import (
    B6_EVIDENCE_OFFICIAL_THREAD_ID,
    B6_TRAJECTORY_OFFICIAL_THREAD_ID,
    generate_official_b6_runs,
)
from scripts.run_b6_live_evidence import LIVE_TASK, LIVE_THREAD_ID

from tests.b6_positive_fixtures import (
    B6_EVIDENCE_THREAD_ID,
    B6_TRAJECTORY_THREAD_ID,
    run_b6_evidence_run,
    run_b6_trajectory_run,
)


class B6PositiveRunsTests(unittest.TestCase):
    # Keep official artifact generation on versioned ids and the injected OPA adapter.
    @patch("scripts.generate_b6_positive_runs.run_b6_trajectory_run")
    @patch("scripts.generate_b6_positive_runs.run_b6_evidence_run")
    @patch("scripts.generate_b6_positive_runs.build_default_policy_engine")
    def test_official_generator_uses_real_policy_boundary_and_versioned_ids(
        self,
        build_policy_engine: Mock,
        run_evidence: Mock,
        run_trajectory: Mock,
    ) -> None:
        policy_engine = object()
        build_policy_engine.return_value = policy_engine
        run_evidence.return_value = {"thread_id": B6_EVIDENCE_OFFICIAL_THREAD_ID}
        run_trajectory.return_value = {"thread_id": B6_TRAJECTORY_OFFICIAL_THREAD_ID}

        with TemporaryDirectory(prefix="b6-official-") as temp_dir:
            artifact_root = Path(temp_dir)
            generated = generate_official_b6_runs(artifact_root=artifact_root)

        self.assertEqual(
            set(generated),
            {B6_EVIDENCE_OFFICIAL_THREAD_ID, B6_TRAJECTORY_OFFICIAL_THREAD_ID},
        )
        run_evidence.assert_called_once_with(
            artifact_root=artifact_root,
            thread_id=B6_EVIDENCE_OFFICIAL_THREAD_ID,
            policy_engine_service=policy_engine,
        )
        run_trajectory.assert_called_once_with(
            artifact_root=artifact_root,
            thread_id=B6_TRAJECTORY_OFFICIAL_THREAD_ID,
            policy_engine_service=policy_engine,
        )

    # Keep the live B6 isolated from historical artifacts and early literal gating.
    def test_live_b6_has_versioned_id_and_constrained_collection_completion(self) -> None:
        self.assertEqual(LIVE_THREAD_ID, "b6-live-evidence-burden-v2")
        self.assertIn("'Source collection complete'", LIVE_TASK)
        self.assertIn("do not include any numeric literal", LIVE_TASK)

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
            evidence_trace = persisted["governance_passes"][1]["pillar_results"]["evidence"]
            self.assertTrue(evidence_trace["evidence_gap"])
            self.assertIn(
                "insufficient_independent_sources",
                evidence_trace["raw_facts"]["deficit_codes"],
            )

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
            trajectory_trace = persisted["governance_passes"][-1]["pillar_results"]["trajectory"]
            self.assertTrue(trajectory_trace["replan_ceiling_reached"])
            self.assertEqual(trajectory_trace["raw_facts"]["replan_count"], 3)
