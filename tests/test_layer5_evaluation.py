from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from layer5_evaluation_continuous_improvement import (
    EvaluationStore,
    TraceImporter,
    build_evaluation_summary,
    generate_evaluation_report,
)


class Layer5EvaluationTests(unittest.TestCase):
    # Import the canonical facts once even when the same trace is discovered twice.
    def test_trace_ingestion_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = _write_trace(root)
            with EvaluationStore(root / "evaluation.sqlite3") as store:
                importer = TraceImporter(store)
                first = importer.import_paths([trace_path], experiment_id="experiment-1")
                second = importer.import_paths([trace_path], experiment_id="experiment-1")

                self.assertEqual(first.imported, (trace_path.resolve(),))
                self.assertEqual(second.skipped, (trace_path.resolve(),))
                self.assertEqual(len(store.query("SELECT * FROM runs")), 1)
                self.assertEqual(len(store.query("SELECT * FROM governance_events")), 2)
                self.assertEqual(len(store.query("SELECT * FROM human_reviews")), 1)
                self.assertEqual(len(store.query("SELECT * FROM external_operations")), 1)

    # Keep technical completion, semantic correctness, and oversight labels separate.
    def test_assessment_and_metrics_use_atomic_facts(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = _write_trace(root)
            with EvaluationStore(root / "evaluation.sqlite3") as store:
                TraceImporter(store).import_paths([trace_path], experiment_id="experiment-1")
                self.assertIsNone(store.query("SELECT termination_reason FROM runs")[0]["termination_reason"])
                store.set_task_result(run_id="run-1", result="incorrect", method="human")
                store.set_human_review_needed(event_id="run-1:governance:1", needed=False)
                store.set_human_review_needed(event_id="run-1:governance:2", needed=True)
                store.set_external_operation_correct(row_id="run-1:external:1", correct=False)

                summary = build_evaluation_summary(store, experiment_id="experiment-1")

                self.assertEqual(summary.total_runs, 1)
                self.assertEqual(summary.task_results, {"incorrect": 1})
                self.assertEqual(summary.termination_reasons, {})
                self.assertEqual(summary.incorrect_completion_rate, 1.0)
                self.assertEqual(summary.escalation_confusion["false_positive"], 1)
                self.assertEqual(summary.escalation_confusion["false_negative"], 1)
                self.assertEqual(summary.human_intervention_rate, 1.0)
                self.assertEqual(summary.failed_tool_rate, 0.5)
                self.assertEqual(
                    summary.reason_codes,
                    {"judge_uncertain": 1, "local_output_allowed": 1},
                )
                # `local_output_allowed` is a routine allow decision, not a pillar
                # intervention, and is counted separately so the breakdown chart is not
                # dominated by the turns where nothing happened.
                self.assertEqual(
                    summary.pillar_breakdown,
                    {"Routine Progress": 1, "Semantic Judge": 1},
                )
                # Precision and recall are only meaningful against the labeled subset.
                self.assertEqual(summary.labeled_governance_events, 2)
                self.assertEqual(summary.total_governance_events, 2)
                self.assertEqual(summary.avg_actor_turns, 3.0)
                self.assertEqual(summary.avg_tool_calls, 2.0)
                self.assertEqual(summary.avg_replanning_count, 1.0)
                self.assertEqual(summary.avg_governance_passes, 2.0)

    # Every typed reason code must resolve to a declared pillar. Substring heuristics
    # silently routed four codes to a catch-all bucket; an exhaustive mapping is only
    # trustworthy if adding a code without classifying it breaks the build.
    def test_every_governance_reason_code_has_a_declared_pillar(self) -> None:
        from layer2_runtime_governance.governance_components.contracts import (
            GovernanceReasonCode,
        )
        from layer5_evaluation_continuous_improvement.reason_code_pillars import (
            REASON_CODE_PILLARS,
            UNCLASSIFIED_PILLAR,
            classify_reason_code_pillar,
        )

        declared = set(REASON_CODE_PILLARS)
        emitted = {code.value for code in GovernanceReasonCode}

        self.assertEqual(emitted - declared, set(), "reason codes without a pillar")
        self.assertEqual(declared - emitted, set(), "pillars for codes that cannot occur")
        for code in emitted:
            self.assertNotEqual(classify_reason_code_pillar(code), UNCLASSIFIED_PILLAR)

    # These four were misattributed by the previous name-matching classifier.
    def test_previously_misclassified_codes_reach_their_real_pillar(self) -> None:
        from layer5_evaluation_continuous_improvement.reason_code_pillars import (
            classify_reason_code_pillar,
        )

        self.assertEqual(
            classify_reason_code_pillar("step_exploration_budget_exhausted"),
            "Trajectory Pillar",
        )
        self.assertEqual(
            classify_reason_code_pillar("unresolvable_revision"), "Trajectory Pillar"
        )
        self.assertEqual(
            classify_reason_code_pillar("candidate_digest_mismatch"), "Evidence Pillar"
        )
        # No judge verdict is involved; the word "confidence" alone had captured it.
        self.assertEqual(
            classify_reason_code_pillar("external_low_actor_self_confidence"),
            "External Execution Boundary",
        )

    # The trace carries no wall-clock boundary and no readable scenario identity; the
    # campaign runner records both beside it. Without reading that sibling file the
    # evaluation can only group by an opaque task hash and can never report duration.
    def test_campaign_record_supplies_scenario_identity_and_duration(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = _write_trace(root)
            (trace_path.parent / "run_record.json").write_text(
                json.dumps(
                    {
                        "record_version": "evaluation_campaign_run.v1",
                        "campaign_id": "realistic_pilot_v1",
                        "scenario_id": "rp07_incident_snapshot_report",
                        "family": "structured_data",
                        "replica_index": 2,
                        "elapsed_seconds": 41.5,
                    }
                ),
                encoding="utf-8",
            )
            with EvaluationStore(root / "evaluation.sqlite3") as store:
                TraceImporter(store).import_paths([trace_path], experiment_id="experiment-1")

                run = store.query("SELECT * FROM runs")[0]
                self.assertEqual(run["scenario_id"], "rp07_incident_snapshot_report")
                self.assertEqual(run["scenario_family"], "structured_data")
                self.assertEqual(run["replica_index"], 2)
                self.assertEqual(run["campaign_id"], "realistic_pilot_v1")
                self.assertEqual(run["duration_seconds"], 41.5)

                summary = build_evaluation_summary(store, experiment_id="experiment-1")
                self.assertEqual(summary.avg_run_duration_seconds, 41.5)
                self.assertIn("structured_data", summary.family_breakdown)
                self.assertEqual(summary.family_breakdown["structured_data"]["runs"], 1)

    # A run produced outside a campaign has no sibling record and must still import.
    def test_trace_without_campaign_record_still_imports(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = _write_trace(root)
            with EvaluationStore(root / "evaluation.sqlite3") as store:
                TraceImporter(store).import_paths([trace_path], experiment_id="experiment-1")

                run = store.query("SELECT * FROM runs")[0]
                self.assertIsNone(run["scenario_id"])
                self.assertIsNone(run["duration_seconds"])

                summary = build_evaluation_summary(store, experiment_id="experiment-1")
                self.assertIsNone(summary.avg_run_duration_seconds)
                self.assertIn("non dichiarata", summary.family_breakdown)

    # Produce a self-contained thesis bundle without optional plotting dependencies.
    def test_report_exports_json_csv_and_svg(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = _write_trace(root)
            with EvaluationStore(root / "evaluation.sqlite3") as store:
                TraceImporter(store).import_paths([trace_path], experiment_id="experiment-1")
                store.set_task_result(run_id="run-1", result="correct", method="automatic")
                files = generate_evaluation_report(
                    store,
                    output_dir=root / "report",
                    experiment_id="experiment-1",
                )

                names = {path.name for path in files}
                self.assertIn("summary.json", names)
                self.assertIn("runs.csv", names)
                self.assertIn("reason_codes.csv", names)
                self.assertIn("task_results.svg", names)
                self.assertIn("governance_pillars.svg", names)
                self.assertIn("reason_codes.svg", names)
                self.assertIn("efficiency_averages.svg", names)
                self.assertIn("escalation_confusion_matrix.svg", names)
                self.assertIn("manifest.json", names)
                self.assertTrue(all(path.exists() for path in files))


    # Preserve offline labels when a later revision of the same run is imported.
    def test_updated_trace_preserves_manual_assessments(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = _write_trace(root)
            with EvaluationStore(root / "evaluation.sqlite3") as store:
                importer = TraceImporter(store)
                importer.import_paths([trace_path], experiment_id="experiment-1")
                store.set_task_result(run_id="run-1", result="correct", method="human")
                store.set_human_review_needed(event_id="run-1:governance:1", needed=True)
                store.set_external_operation_correct(row_id="run-1:external:1", correct=True)
                payload = _trace_payload()
                runtime = payload["runtime"]
                assert isinstance(runtime, dict)
                runtime["actor_turn_count"] = 4
                trace_path.write_text(json.dumps(payload), encoding="utf-8")

                result = importer.import_paths([trace_path], experiment_id="experiment-1")

                self.assertEqual(result.imported, (trace_path.resolve(),))
                run = store.query("SELECT * FROM runs")[0]
                event = store.query(
                    "SELECT * FROM governance_events WHERE pass_index = 1"
                )[0]
                operation = store.query("SELECT * FROM external_operations")[0]
                self.assertEqual(run["task_result"], "correct")
                self.assertEqual(run["actor_turn_count"], 4)
                self.assertEqual(event["human_review_was_needed"], 1)
                self.assertEqual(operation["operation_correct"], 1)

    # Preserve the terminal classification in the offline evaluation projection.
    def test_termination_reason_is_projected_and_aggregated(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = _write_trace(root)
            payload = json.loads(trace_path.read_text(encoding="utf-8"))
            payload["runtime"]["termination_reason"] = "governance_denied"
            trace_path.write_text(json.dumps(payload), encoding="utf-8")

            with EvaluationStore(root / "evaluation.sqlite3") as store:
                TraceImporter(store).import_paths([trace_path], experiment_id="experiment-1")
                run = store.query("SELECT * FROM runs")[0]
                self.assertEqual(run["termination_reason"], "governance_denied")
                summary = build_evaluation_summary(store, experiment_id="experiment-1")
                self.assertEqual(summary.termination_reasons, {"governance_denied": 1})

    # Legacy governance debug exchanges without a v6 trace stay outside the evaluation corpus.
    def test_discovery_excludes_legacy_exchange_only_run(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "031"
            legacy.mkdir()
            (legacy / "governance_conversation.md").write_text("legacy", encoding="utf-8")
            (legacy / "governance_exchanges").mkdir()
            (legacy / "governance_exchanges" / "001.json").write_text(
                "{}", encoding="utf-8"
            )

            with EvaluationStore(root / "evaluation.sqlite3") as store:
                self.assertEqual(TraceImporter(store).discover([root]), [])

# Write one compact v6 trace covering every Layer 5 table.
def _write_trace(root: Path) -> Path:
    trace_dir = root / "run-1"
    trace_dir.mkdir()
    path = trace_dir / "full-trace-2.json"
    path.write_text(json.dumps(_trace_payload()), encoding="utf-8")
    return path


# Build the minimal canonical trace fields consumed by Layer 5.
def _trace_payload() -> dict[str, object]:
    return {
        "trace_version": "layer2_trace.v6",
        "runtime": {
            "thread_id": "run-1",
            "current_state": "completed",
            "termination_reason": None,
            "actor_turn_count": 3,
            "step_history": [{"node_name": "replanner"}],
        },
        "intake": {
            "goal": "Send a governed result.",
            "context": "Deterministic test.",
            "constraints": ["Require review."],
            "success_criteria": ["Operation executed."],
            "sensitivity": "high",
            "execution_intent": "execute_after_governance",
            "policy_profile_name": "default",
            "confidence_threshold": 0.7,
            "read_only_mode": False,
        },
        "planner_name": "test-planner",
        "planner_prompt_version": "planner-v1",
        "actor": {
            "actor_name": "test-actor",
            "actor_prompt_version": "actor-v1",
            "external_operation": None,
        },
        "tool_calls": [
            {"tool_name": "web_search", "success": True},
            {"tool_name": "fetch_url_text", "success": False},
        ],
        "governance_passes": [
            {
                "pass_index": 1,
                "directive": "wait_human_review",
                "policy_decision": "require_human_review",
                "reason_codes": ["judge_uncertain"],
                "requires_human_review": True,
                "policy_bundle_version": "policy-v1",
                "policy_config_version": "config-v1",
            },
            {
                "pass_index": 2,
                "directive": "continue",
                "policy_decision": "allow",
                "reason_codes": ["local_output_allowed"],
                "requires_human_review": False,
                "policy_bundle_version": "policy-v1",
                "policy_config_version": "config-v1",
            },
        ],
        "human_review": {
            "decision_history": [
                {
                    "request_id": "review-1",
                    "decision_type": "modify_and_continue",
                    "reviewer_id": "reviewer-1",
                    "note": "Corrected target.",
                    "external_operation_edit": {"target": "safe-target"},
                    "approved_operation_digest": "a" * 64,
                }
            ]
        },
        "external_execution": {
            "receipt_history": [
                {
                    "operation_id": "operation-1",
                    "capability_id": "smtp_email",
                    "payload_digest": "a" * 64,
                    "status": "executed",
                    "started_at": "2026-08-05T10:00:00+00:00",
                    "completed_at": "2026-08-05T10:00:02+00:00",
                }
            ]
        },
    }


if __name__ == "__main__":
    unittest.main()
