from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from langgraph.checkpoint.memory import InMemorySaver

from layer2_runtime_governance import GovernanceDirective, RuntimeState
from layer4_state_evidence_observability.infrastructure.checkpointing import (
    build_runtime_serializer,
)
from layer4_state_evidence_observability.infrastructure.runtime_artifacts import (
    RuntimeArtifactStore,
)
from scripts.generate_b6_positive_runs import (
    B6_EVIDENCE_OFFICIAL_THREAD_ID,
    B6_STEP_EXPLORATION_CEILING_OFFICIAL_THREAD_ID,
    B6_TRAJECTORY_OFFICIAL_THREAD_ID,
    generate_official_b6_runs,
)
from scripts.run_b6_live_evidence import LIVE_TASK, LIVE_THREAD_ID

from tests.b6_positive_fixtures import (
    B6_ANCHORING_CONCERN_THREAD_ID,
    B6_EVIDENCE_THREAD_ID,
    B6_STEP_EXPLORATION_CEILING_THREAD_ID,
    B6_TRAJECTORY_THREAD_ID,
    approve_b6_anchoring_concern_review,
    run_b6_anchoring_concern_run,
    run_b6_evidence_run,
    run_b6_step_exploration_ceiling_run,
    run_b6_trajectory_run,
)


class B6PositiveRunsTests(unittest.TestCase):
    # Keep official artifact generation on versioned ids and the injected OPA adapter.
    @patch("scripts.generate_b6_positive_runs.run_b6_step_exploration_ceiling_run")
    @patch("scripts.generate_b6_positive_runs.run_b6_trajectory_run")
    @patch("scripts.generate_b6_positive_runs.run_b6_evidence_run")
    @patch("scripts.generate_b6_positive_runs.build_default_policy_engine")
    def test_official_generator_uses_real_policy_boundary_and_versioned_ids(
        self,
        build_policy_engine: Mock,
        run_evidence: Mock,
        run_trajectory: Mock,
        run_step_exploration_ceiling: Mock,
    ) -> None:
        policy_engine = object()
        build_policy_engine.return_value = policy_engine
        run_evidence.return_value = {"thread_id": B6_EVIDENCE_OFFICIAL_THREAD_ID}
        run_trajectory.return_value = {"thread_id": B6_TRAJECTORY_OFFICIAL_THREAD_ID}
        run_step_exploration_ceiling.return_value = {"thread_id": B6_STEP_EXPLORATION_CEILING_OFFICIAL_THREAD_ID}

        with TemporaryDirectory(prefix="b6-official-") as temp_dir:
            artifact_root = Path(temp_dir)
            generated = generate_official_b6_runs(artifact_root=artifact_root)

        self.assertEqual(
            set(generated),
            {
                B6_EVIDENCE_OFFICIAL_THREAD_ID,
                B6_TRAJECTORY_OFFICIAL_THREAD_ID,
                B6_STEP_EXPLORATION_CEILING_OFFICIAL_THREAD_ID,
            },
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
        run_step_exploration_ceiling.assert_called_once_with(
            artifact_root=artifact_root,
            thread_id=B6_STEP_EXPLORATION_CEILING_OFFICIAL_THREAD_ID,
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
            # The ceiling is unconditional on the actor's current proposal (mirroring
            # trajectory.rego), so it blocks the 4th observation request itself rather
            # than waiting for a 4th NEEDS_REPLAN turn that never gets the chance to run.
            self.assertEqual(task_state.tool_call_count, 3)
            self.assertEqual(task_state.governance_history[-1].directive, GovernanceDirective.WAIT_HUMAN_REVIEW)
            self.assertIn("replan_ceiling_reached", task_state.governance_history[-1].reason_codes)

            trajectory_facts = task_state.governance_history[-1].pillar_results["trajectory"]["raw_facts"]
            self.assertEqual(trajectory_facts["replan_count"], 3)
            # The catching pass is the actor's 4th observation proposal, before it ever
            # executes - there is deliberately no new observable basis yet at this point.
            self.assertFalse(trajectory_facts["observable_change_since_last_replan"])
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

    # Verify a target that never settles - but always reaches a real backend, on a
    # fresh-looking request every turn - still reaches a terminating decision, and that
    # the terminating decision is OPA's step_exploration_budget_exhausted, never a
    # replan the tool gateway invents on its own. The actor's own liveness fuse stays
    # silent throughout: it exists to rescue a step spinning on locally-settled repeats
    # (see b6-trajectory-replan-ceiling-v2 for that path), not broad real exploration
    # that simply never completes, which is this pillar's actual domain.
    def test_step_exploration_ceiling_runs_through_runtime_without_gateway_replan(self) -> None:
        with TemporaryDirectory(prefix="b6-step-exploration-ceiling-") as temp_dir:
            artifact_root = Path(temp_dir)
            trace = run_b6_step_exploration_ceiling_run(artifact_root=artifact_root)
            task_state = trace["final_result"]["task_state"]

            self.assertEqual(task_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
            self.assertEqual(task_state.governance_history[-1].directive, GovernanceDirective.WAIT_HUMAN_REVIEW)
            self.assertIn("step_exploration_budget_exhausted", task_state.governance_history[-1].reason_codes)

            trajectory_facts = task_state.governance_history[-1].pillar_results["trajectory"]["raw_facts"]
            self.assertEqual(trajectory_facts["current_step_tool_attempt_count"], 30)
            self.assertFalse(trajectory_facts["completion_signal"])
            self.assertTrue(
                task_state.governance_history[-1].pillar_results["trajectory"][
                    "step_exploration_budget_exhausted"
                ]
            )

            # No replan happened at all: every request reached the backend, so neither
            # the liveness fuse nor the tool gateway ever had a reason to intervene - the
            # only plan step in this run is the original one, and every attempt on it
            # became a real backend call.
            self.assertEqual(len(task_state.replan_checkpoints), 0)
            self.assertEqual(
                sum(item.node_name == "replanner" for item in task_state.step_history),
                0,
            )
            self.assertEqual(task_state.tool_call_count, 30)

            paths = artifact_root / B6_STEP_EXPLORATION_CEILING_THREAD_ID
            self.assertTrue((paths / "full-trace-2.json").exists())
            self.assertTrue((paths / "trace_summary.md").exists())
            persisted = json.loads((paths / "full-trace-2.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["runtime"]["current_state"], "waiting_human_review")
            trajectory_trace = persisted["governance_passes"][-1]["pillar_results"]["trajectory"]
            self.assertTrue(trajectory_trace["step_exploration_budget_exhausted"])
            self.assertEqual(trajectory_trace["raw_facts"]["current_step_tool_attempt_count"], 30)

    # Verify a human approve_continue on an anchoring concern is remembered: resuming
    # the same thread does not re-escalate the identical, already-reviewed completion.
    def test_anchoring_concern_pauses_then_adjudication_memory_prevents_recurrence(self) -> None:
        with TemporaryDirectory(prefix="b6-anchoring-concern-") as temp_dir:
            artifact_root = Path(temp_dir)
            checkpointer = InMemorySaver(serde=build_runtime_serializer())
            artifact_store = RuntimeArtifactStore(root_path=artifact_root)

            paused_trace = run_b6_anchoring_concern_run(
                artifact_root=artifact_root,
                checkpointer=checkpointer,
                artifact_store=artifact_store,
            )
            paused_state = paused_trace["final_result"]["task_state"]

            self.assertEqual(paused_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
            self.assertEqual(
                paused_state.governance_history[-1].directive,
                GovernanceDirective.WAIT_HUMAN_REVIEW,
            )
            self.assertIn(
                "evidence_anchoring_requires_review",
                paused_state.governance_history[-1].reason_codes,
            )
            evidence_facts = paused_state.governance_history[-1].pillar_results["evidence"]["raw_facts"]
            self.assertTrue(evidence_facts["has_unanchored_literals_in_selection"])
            self.assertFalse(evidence_facts["anchoring_concern_adjudicated"])
            self.assertIsNone(paused_state.adjudicated_evidence_anchoring_digest)

            pending_request = paused_state.pending_human_review_request
            self.assertIsNotNone(pending_request)
            self.assertIn("approve_continue", [d.value for d in pending_request.allowed_decisions])

            resumed_trace = approve_b6_anchoring_concern_review(
                thread_id=B6_ANCHORING_CONCERN_THREAD_ID,
                request_id=pending_request.request_id,
                checkpointer=checkpointer,
                artifact_store=artifact_store,
            )
            resumed_state = resumed_trace["final_result"]["task_state"]

            # The same claim, resubmitted after approval, must not re-open a review: the
            # ruling is bound to the exact evidence-selection digest a human already saw.
            self.assertNotEqual(resumed_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
            self.assertIsNotNone(resumed_state.adjudicated_evidence_anchoring_digest)
            final_evidence_facts = resumed_state.governance_history[-1].pillar_results["evidence"]["raw_facts"]
            self.assertTrue(final_evidence_facts["anchoring_concern_adjudicated"])
            self.assertNotIn(
                "evidence_anchoring_requires_review",
                resumed_state.governance_history[-1].reason_codes,
            )
