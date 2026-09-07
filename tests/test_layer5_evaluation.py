from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from layer5_evaluation_fixtures import trace_payload as _trace_payload, write_trace as _write_trace, write_run_record

from layer5_evaluation_continuous_improvement import (
    EvaluationStore,
    TraceImporter,
    build_evaluation_summary,
    generate_evaluation_report,
)


class Layer5EvaluationTests(unittest.TestCase):
    def test_corrupt_trace_keeps_failed_run_and_reports_import_error(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            record = write_run_record(root, status="failed")
            trace = record.parent / "full-trace-2.json"
            trace.write_text("{truncated", encoding="utf-8")
            with EvaluationStore(root / "evaluation.sqlite3") as store:
                result = TraceImporter(store).import_paths([root], experiment_id="campaign-1")
                self.assertEqual(len(result.failures), 1)
                self.assertEqual(result.imported, (record.resolve(),))
                summary = build_evaluation_summary(store)
                self.assertEqual(summary.total_runs, 1)
                self.assertEqual(summary.runtime_statuses, {"runtime_failure": 1})

    def test_record_only_is_idempotent_and_later_trace_replaces_placeholder(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            record = write_run_record(root)
            with EvaluationStore(root / "evaluation.sqlite3") as store:
                importer = TraceImporter(store)
                importer.import_paths([record], experiment_id="campaign-1")
                repeated = importer.import_paths([record], experiment_id="campaign-1")
                self.assertEqual(repeated.skipped, (record.resolve(),))
                summary = build_evaluation_summary(store)
                self.assertEqual(summary.not_started_runs, 1)
                self.assertEqual(summary.attempted_runs, 0)
                self.assertIsNone(summary.technical_completion_rate)
                self.assertIsNone(summary.family_breakdown["research"]["technical_completion_rate"])
                trace = record.parent / "full-trace-2.json"
                trace.write_text(json.dumps(_trace_payload()), encoding="utf-8")
                write_run_record(root, status="finished", elapsed_seconds=12.5)
                importer.import_paths([record], experiment_id="campaign-1")
                self.assertEqual(len(store.query("SELECT * FROM runs")), 1)
                summary = build_evaluation_summary(store)
                self.assertEqual(summary.trace_backed_runs, 1)
                self.assertEqual(summary.not_started_runs, 0)
                self.assertEqual(summary.technical_completion_rate, 1)
                store.set_task_result(run_id="run-1", result="correct", method="human")
                write_run_record(root, status="finished", elapsed_seconds=20.0)
                importer.import_paths([record], experiment_id="campaign-1")
                updated = store.query("SELECT * FROM runs")[0]
                self.assertEqual(updated["duration_seconds"], 20.0)
                self.assertEqual(updated["task_result"], "correct")

    def test_empty_denominators_remain_unavailable_in_json_and_svg(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with EvaluationStore(root / "evaluation.sqlite3") as store:
                summary = build_evaluation_summary(store)
                self.assertIsNone(summary.technical_completion_rate)
                self.assertIsNone(summary.human_intervention_rate)
                self.assertIsNone(summary.failed_tool_rate)
                self.assertIsNone(summary.correct_rate)
                self.assertIsNone(summary.escalation_f1)
                generate_evaluation_report(store, output_dir=root / "report")
                exported = json.loads((root / "report" / "summary.json").read_text())
                self.assertIsNone(exported["technical_completion_rate"])
                self.assertIn("N/D", (root / "report" / "efficiency_averages.svg").read_text())

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

    # Failed and intentionally skipped campaign runs remain in the experimental corpus
    # even when no canonical trace could be produced.
    def test_trace_less_campaign_runs_are_imported_without_biasing_attempt_denominator(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            failed_dir = root / "failed-run"
            skipped_dir = root / "skipped-run"
            failed_dir.mkdir()
            skipped_dir.mkdir()
            common = {
                "record_version": "evaluation_campaign_run.v1",
                "campaign_id": "campaign-1",
                "family": "research",
                "replica_index": 1,
                "elapsed_seconds": 4.5,
                "required_tools": ["web_search"],
                "external_execution": "none",
                "task": "A campaign task",
            }
            (failed_dir / "run_record.json").write_text(
                json.dumps(
                    {
                        **common,
                        "thread_id": "failed-run",
                        "scenario_id": "rp01",
                        "status": "failed",
                        "error_type": "RuntimeError",
                    }
                ),
                encoding="utf-8",
            )
            (skipped_dir / "run_record.json").write_text(
                json.dumps(
                    {
                        **common,
                        "thread_id": "skipped-run",
                        "scenario_id": "rp14",
                        "status": "skipped_external_execution_disabled",
                        "elapsed_seconds": None,
                    }
                ),
                encoding="utf-8",
            )

            with EvaluationStore(root / "evaluation.sqlite3") as store:
                result = TraceImporter(store).import_paths([root], experiment_id="experiment-1")
                summary = build_evaluation_summary(store, experiment_id="experiment-1")

                self.assertEqual(len(result.imported), 2)
                self.assertEqual(summary.total_runs, 2)
                self.assertEqual(summary.attempted_runs, 1)
                self.assertEqual(summary.skipped_runs, 1)
                self.assertEqual(summary.trace_backed_runs, 0)
                self.assertEqual(summary.technical_completion_rate, 0.0)
                failed = store.query("SELECT * FROM runs WHERE run_id = 'failed-run'")[0]
                self.assertEqual(failed["runtime_status"], "runtime_failure")
                self.assertEqual(failed["termination_reason"], "runtime_failure")
                self.assertEqual(failed["trace_available"], 0)

    # A replan is a recovery attempt, not a guaranteed one - measure how often it
    # actually reaches completion instead of asserting recovery as the default outcome.
    def test_replan_recovery_rate_measures_completion_after_replan(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            recovered_path = root / "recovered" / "full-trace-2.json"
            recovered_path.parent.mkdir(parents=True)
            recovered_payload = _trace_payload()
            recovered_payload["runtime"]["thread_id"] = "recovered"
            recovered_path.write_text(json.dumps(recovered_payload), encoding="utf-8")

            stalled_path = root / "stalled" / "full-trace-2.json"
            stalled_path.parent.mkdir(parents=True)
            stalled_payload = _trace_payload()
            stalled_payload["runtime"]["thread_id"] = "stalled"
            stalled_payload["runtime"]["current_state"] = "waiting_human_review"
            stalled_path.write_text(json.dumps(stalled_payload), encoding="utf-8")

            never_replanned_path = root / "clean" / "full-trace-2.json"
            never_replanned_path.parent.mkdir(parents=True)
            clean_payload = _trace_payload()
            clean_payload["runtime"]["thread_id"] = "clean"
            clean_payload["runtime"]["step_history"] = []
            never_replanned_path.write_text(json.dumps(clean_payload), encoding="utf-8")

            with EvaluationStore(root / "evaluation.sqlite3") as store:
                TraceImporter(store).import_paths(
                    [recovered_path, stalled_path, never_replanned_path],
                    experiment_id="experiment-1",
                )
                summary = build_evaluation_summary(store, experiment_id="experiment-1")

                # Only the two runs that actually replanned enter the denominator.
                self.assertEqual(summary.runs_with_replan, 2)
                self.assertEqual(summary.replan_recovery_rate, 0.5)

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



if __name__ == "__main__":
    unittest.main()
