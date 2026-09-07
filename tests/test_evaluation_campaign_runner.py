from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from layer5_evaluation_fixtures import trace_payload
from layer5_evaluation_continuous_improvement import EvaluationStore, build_evaluation_summary

from scripts import run_evaluation_campaign as campaign


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_SCRIPT = REPO_ROOT / "scripts" / "run_evaluation_campaign.py"


class EvaluationCampaignRunnerTests(unittest.TestCase):
    # Keep campaign artifacts isolated from ignored runtime output for every test.
    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        self.output_root = Path(self._temporary_directory.name) / "evaluation-input"
        self.database_path = Path(self._temporary_directory.name) / "evaluation.sqlite3"
        self.manifest = campaign.load_campaign_manifest(campaign.DEFAULT_MANIFEST_PATH)

    # Remove only the temporary campaign root owned by this test case.
    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    # The fixed v1 manifest covers the full realistic pool and confines SMTP to the test domain.
    def test_manifest_has_all_realistic_scenarios_and_safe_smtp_recipients(self) -> None:
        self.assertEqual(self.manifest.pool_id, "realistic_pool_v1")
        self.assertEqual(len(self.manifest.scenarios), 16)
        self.assertEqual(
            [scenario.scenario_id for scenario in self.manifest.scenarios],
            [f"rp{index:02d}_{suffix}" for index, suffix in enumerate(
                [
                    "dma_brief",
                    "nist_framework_comparison",
                    "ai_act_timeline",
                    "claim_verification",
                    "pdf_web_crosscheck",
                    "policy_obligation_matrix",
                    "incident_snapshot_report",
                    "incident_triage",
                    "data_reconciliation",
                    "compliance_action_plan",
                    "risk_register",
                    "vendor_evidence_matrix",
                    "source_grounded_email_draft",
                    "test_mailbox_update",
                    "multitool_stakeholder_update",
                    "database_read_only_report",
                ],
                start=1,
            )],
        )
        smtp_scenarios = [
            scenario
            for scenario in self.manifest.scenarios
            if scenario.external_execution == campaign.ExternalExecutionKind.EMAIL_SEND
        ]

        self.assertEqual([scenario.scenario_id for scenario in smtp_scenarios], ["rp14_test_mailbox_update", "rp15_multitool_stakeholder_update"])
        self.assertTrue(all(scenario.test_recipient.endswith(".test") for scenario in smtp_scenarios))

    # A campaign without explicit SMTP permission plans those scenarios as records, not runtime calls.
    def test_plan_skips_smtp_without_opt_in_and_uses_stable_thread_ids(self) -> None:
        planned_runs = campaign.plan_campaign_runs(
            manifest=self.manifest,
            campaign_id="realistic_pilot_v1",
            replicas=2,
            output_root=self.output_root,
            allow_external_execution=False,
        )

        self.assertEqual(len(planned_runs), 32)
        self.assertEqual(planned_runs[0].thread_id, "realistic_pilot_v1-rp01_dma_brief-r01")
        self.assertEqual(planned_runs[1].thread_id, "realistic_pilot_v1-rp01_dma_brief-r02")
        skipped = [
            planned_run
            for planned_run in planned_runs
            if planned_run.action == campaign.PlannedRunAction.SKIP_EXTERNAL_EXECUTION
        ]

        self.assertEqual(len(skipped), 4)
        self.assertEqual(
            {planned_run.scenario.scenario_id for planned_run in skipped},
            {"rp14_test_mailbox_update", "rp15_multitool_stakeholder_update"},
        )

    # The batch runner launches the long-lived CLI and records every planned run.
    def test_execute_campaign_uses_cli_command_and_records_every_run(self) -> None:
        planned_runs = campaign.plan_campaign_runs(
            manifest=self.manifest,
            campaign_id="realistic_test_v1",
            replicas=1,
            output_root=self.output_root,
            allow_external_execution=False,
        )
        commands: list[list[str]] = []

        def fake_command_runner(
            command: list[str], *, cwd: Path
        ) -> subprocess.CompletedProcess[object]:
            self.assertEqual(cwd, REPO_ROOT)
            commands.append(command)
            trace_path = Path(command[command.index("--trace-file") + 1])
            thread_id = command[command.index("--thread-id") + 1]
            payload = trace_payload()
            payload["runtime"]["thread_id"] = thread_id
            trace_path.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0)

        results = campaign.execute_campaign(
            manifest=self.manifest,
            planned_runs=planned_runs,
            allow_external_execution=False,
            fail_fast=False,
            command_runner=fake_command_runner,
            database_path=self.database_path,
        )

        self.assertEqual(len(results), 16)
        with EvaluationStore(self.database_path) as store:
            summary_from_db = build_evaluation_summary(store)
            self.assertEqual(summary_from_db.total_runs, 16)
            self.assertEqual(summary_from_db.attempted_runs, 14)
            self.assertEqual(summary_from_db.trace_backed_runs, 14)
        self.assertEqual(len(commands), 14)
        self.assertEqual(
            [result.status for result in results].count("skipped_external_execution_disabled"),
            2,
        )
        self.assertTrue(all("--trace-file" in command for command in commands))
        campaign_root = self.output_root / "realistic_test_v1"
        summary = json.loads(
            (campaign_root / "campaign_summary.json").read_text(encoding="utf-8")
        )

        self.assertFalse(summary["in_progress"])
        self.assertEqual(summary["status_counts"]["finished"], 14)
        self.assertEqual(
            summary["status_counts"]["skipped_external_execution_disabled"],
            2,
        )
        self.assertTrue(
            (
                campaign_root
                / "realistic_test_v1-rp01_dma_brief-r01"
                / "full-trace-2.json"
            ).is_file()
        )
        skipped_record = json.loads(
            (
                campaign_root
                / "realistic_test_v1-rp14_test_mailbox_update-r01"
                / "run_record.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(skipped_record["status"], "skipped_external_execution_disabled")

    # A campaign ID is immutable: an existing target must not be silently reused or overwritten.
    def test_fail_fast_keeps_entire_plan_in_database_before_first_command(self) -> None:
        planned = campaign.plan_campaign_runs(
            manifest=self.manifest, campaign_id="fail_fast", replicas=1,
            output_root=self.output_root, allow_external_execution=False,
        )

        def fail_command(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[object]:
            self.assertEqual(cwd, REPO_ROOT)
            with EvaluationStore(self.database_path) as store:
                summary = build_evaluation_summary(store)
                self.assertEqual(summary.total_runs, 16)
                self.assertEqual(summary.attempted_runs, 1)
                self.assertEqual(summary.not_started_runs, 13)
                self.assertEqual(summary.skipped_runs, 2)
            return subprocess.CompletedProcess(command, 1)

        results = campaign.execute_campaign(
            manifest=self.manifest, planned_runs=planned, allow_external_execution=False,
            fail_fast=True, command_runner=fail_command, database_path=self.database_path,
        )
        self.assertEqual(len(results), 1)
        with EvaluationStore(self.database_path) as store:
            summary = build_evaluation_summary(store)
            self.assertEqual(summary.total_runs, 16)
            self.assertEqual(summary.runtime_statuses["runtime_failure"], 1)
            self.assertEqual(summary.not_started_runs, 13)
            self.assertEqual(summary.technical_completion_rate, 0)
        summary_json = json.loads((planned[0].output_directory.parent / "campaign_summary.json").read_text())
        self.assertFalse(summary_json["in_progress"])
        self.assertEqual(len(summary_json["runs"]), 16)

    def test_keyboard_interrupt_preserves_current_and_not_started_runs(self) -> None:
        planned = campaign.plan_campaign_runs(
            manifest=self.manifest, campaign_id="interrupt", replicas=1,
            output_root=self.output_root, allow_external_execution=True,
        )

        def interrupt_command(_command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[object]:
            self.assertEqual(cwd, REPO_ROOT)
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            campaign.execute_campaign(
                manifest=self.manifest, planned_runs=planned, allow_external_execution=True,
                fail_fast=False, command_runner=interrupt_command, database_path=self.database_path,
            )
        with EvaluationStore(self.database_path) as store:
            summary = build_evaluation_summary(store)
            self.assertEqual(summary.total_runs, 16)
            self.assertEqual(summary.attempted_runs, 1)
            self.assertEqual(summary.not_started_runs, 15)
            self.assertEqual(summary.runtime_statuses["interrupted"], 1)
        summary_json = json.loads((planned[0].output_directory.parent / "campaign_summary.json").read_text())
        self.assertFalse(summary_json["in_progress"])
        self.assertEqual(summary_json["status_counts"]["interrupted"], 1)

    # A campaign ID is immutable: an existing target must not be silently reused or overwritten.
    def test_execute_campaign_refuses_existing_campaign_directory(self) -> None:
        planned_runs = campaign.plan_campaign_runs(
            manifest=self.manifest,
            campaign_id="realistic_existing_v1",
            replicas=1,
            output_root=self.output_root,
            allow_external_execution=False,
        )
        campaign_root = self.output_root / "realistic_existing_v1"
        campaign_root.mkdir(parents=True)

        with self.assertRaisesRegex(FileExistsError, "Refusing to reuse"):
            campaign.execute_campaign(
                manifest=self.manifest,
                planned_runs=planned_runs,
                allow_external_execution=False,
                fail_fast=False,
            )

    # A caller cannot bypass the SMTP opt-in by reusing a plan built with broader permission.
    def test_execute_campaign_rejects_mismatched_smtp_permission(self) -> None:
        planned_runs = campaign.plan_campaign_runs(
            manifest=self.manifest,
            campaign_id="realistic_permission_v1",
            replicas=1,
            output_root=self.output_root,
            allow_external_execution=True,
        )

        with self.assertRaisesRegex(ValueError, "external-execution permission"):
            campaign.execute_campaign(
                manifest=self.manifest,
                planned_runs=planned_runs,
                allow_external_execution=False,
                fail_fast=False,
            )

    # The public command validates and plans the full pool without invoking any live provider in dry-run mode.
    def test_cli_dry_run_plans_all_scenarios_without_creating_output(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(RUNNER_SCRIPT),
                "--pool",
                "realistic",
                "--campaign-id",
                "realistic_dry_run_v1",
                "--replicas",
                "1",
                "--output-root",
                str(self.output_root),
                "--database",
                str(self.database_path),
                "--dry-run",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("planned_runs=16", result.stdout)
        self.assertFalse((self.output_root / "realistic_dry_run_v1").exists())
        self.assertFalse(self.database_path.exists())


if __name__ == "__main__":
    unittest.main()
