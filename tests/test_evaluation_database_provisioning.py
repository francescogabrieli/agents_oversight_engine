from __future__ import annotations

import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from layer3_tool_bus_external_capabilities.external_operations import (
    load_external_capability_registry,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_evaluation_database.py"
SOURCE_CSV = REPO_ROOT / "evaluation_corpus" / "datasets" / "evaluation_incidents_v1.csv"
EVALUATION_OVERLAY = REPO_ROOT / "configs" / "layer2_external_capabilities.evaluation.json"


class EvaluationDatabaseProvisioningTests(unittest.TestCase):
    # Create a test-owned database target outside the runtime artifact directory.
    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        self.database_path = Path(self._temporary_directory.name) / "evaluation.sqlite3"

    # Remove only the temporary directory created by this test case.
    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    # The evaluation overlay enables the registered email and database connectors together.
    def test_evaluation_overlay_enables_email_and_database(self) -> None:
        registry = load_external_capability_registry(config_path=EVALUATION_OVERLAY)

        self.assertIsNotNone(registry.resolve("communication.email.send"))
        self.assertIsNotNone(registry.resolve("database.statement.execute"))
        self.assertTrue(registry.mcp_servers["communication"].enabled)
        self.assertTrue(registry.mcp_servers["database"].enabled)

    # The provisioning script derives a deterministic SQLite fixture from the canonical CSV.
    def test_script_builds_expected_read_only_fixture(self) -> None:
        result = self._run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.database_path.is_file())
        connection = sqlite3.connect(self.database_path)
        try:
            count = connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            columns = [
                row[1]
                for row in connection.execute("PRAGMA table_info(incidents)").fetchall()
            ]
            incident = connection.execute(
                "SELECT severity, status FROM incidents WHERE incident_id = 'INC-005'"
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(count, 8)
        self.assertEqual(
            columns,
            [
                "incident_id",
                "detected_at",
                "service",
                "severity",
                "status",
                "customer_impact",
                "owner",
                "mitigation_status",
                "next_update_at",
            ],
        )
        self.assertEqual(incident, ("critical", "unresolved"))

    # Existing fixtures are protected from accidental overwrite unless replacement is explicit.
    def test_script_refuses_existing_fixture_without_replace(self) -> None:
        self.assertEqual(self._run_script().returncode, 0)
        repeated = self._run_script()

        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn("Refusing to overwrite", repeated.stderr)

    # Invoke the tracked provisioning script through the same command users run locally.
    def _run_script(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(BUILD_SCRIPT),
                "--source",
                str(SOURCE_CSV),
                "--database",
                str(self.database_path),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
