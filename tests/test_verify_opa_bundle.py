from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import verify_opa_bundle as vob  # noqa: E402


class VerifyOpaBundleTests(unittest.TestCase):
    # A version string can match while rule content silently diverges (the exact
    # Windows `--watch` bind-mount failure this check exists to catch); content
    # comparison must therefore never be satisfied by matching metadata alone.
    def test_stale_module_content_is_reported_even_with_matching_ids(self) -> None:
        local = {"main.rego": "package layer2.governance\n\nallowed := true\n"}
        remote = [{"id": "policy/main.rego", "raw": "package layer2.governance\n\nallowed := false\n"}]

        mismatches = vob._diff_policies(local=local, remote=remote)

        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0].kind, "content_differs")
        self.assertIn("main.rego", mismatches[0].detail)

    def test_identical_content_reports_no_mismatch_regardless_of_mount_prefix(self) -> None:
        local = {
            "main.rego": "package layer2.governance\n",
            "pillars/evidence.rego": "package layer2.governance.pillars.evidence\n",
        }
        remote = [
            {"id": "policy/main.rego", "raw": local["main.rego"]},
            {"id": "layer2_governance/pillars/evidence.rego", "raw": local["pillars/evidence.rego"]},
        ]

        self.assertEqual(vob._diff_policies(local=local, remote=remote), [])

    def test_module_present_only_on_disk_is_reported(self) -> None:
        local = {"pillars/risk.rego": "package layer2.governance.pillars.risk\n"}
        remote: list[dict] = []

        mismatches = vob._diff_policies(local=local, remote=remote)

        self.assertEqual([m.kind for m in mismatches], ["missing_remotely"])

    def test_module_present_only_on_pdp_is_reported(self) -> None:
        local: dict[str, str] = {}
        remote = [{"id": "policy/main.rego", "raw": "package layer2.governance\n"}]

        mismatches = vob._diff_policies(local=local, remote=remote)

        self.assertEqual([m.kind for m in mismatches], ["missing_locally"])

    def test_policy_config_mismatch_is_reported_with_both_sides(self) -> None:
        local = {"policy_bundle_version": "layer2_governance_v6"}
        remote = {"policy_bundle_version": "layer2_governance_v5"}

        mismatches = vob._diff_policy_config(local=local, remote=remote)

        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0].kind, "data_differs")
        self.assertIn("layer2_governance_v6", mismatches[0].detail)
        self.assertIn("layer2_governance_v5", mismatches[0].detail)

    def test_identical_policy_config_reports_no_mismatch(self) -> None:
        config = {"policy_bundle_version": "layer2_governance_v6", "thresholds": {"max_replans_before_review": 3}}

        self.assertEqual(vob._diff_policy_config(local=config, remote=dict(config)), [])

    # The check must enumerate every .rego file the repository actually ships, not a
    # hand-maintained subset that could silently stop covering a new pillar module.
    def test_local_rego_files_discovers_every_shipped_policy_module(self) -> None:
        discovered = set(vob._local_rego_files())
        on_disk = {
            str(path.relative_to(vob.POLICY_ROOT)).replace("\\", "/")
            for path in vob.POLICY_ROOT.rglob("*.rego")
        }

        self.assertEqual(discovered, on_disk)
        self.assertIn("main.rego", discovered)
        self.assertIn("pillars/evidence.rego", discovered)
        self.assertIn("pillars/trajectory.rego", discovered)

    def test_unreachable_pdp_is_reported_as_a_single_mismatch(self) -> None:
        mismatches = vob.verify_bundle(base_url="http://127.0.0.1:1", timeout_seconds=0.2)

        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0].kind, "unreachable")


if __name__ == "__main__":
    unittest.main()
