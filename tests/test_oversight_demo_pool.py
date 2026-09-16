from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.run_oversight_demo_pool import (
    DEFAULT_MANIFEST,
    ExpectedOutcome,
    extract_provenance,
    load_manifest,
    select_scenarios,
    validate_trace,
)
from scripts.run_b6_live_evidence import (
    QualitativeDocumentLiveToolGateway,
    TimeoutFaultLiveToolGateway,
)
from layer3_tool_bus_external_capabilities.tool_contract import (
    RetrievalKind,
    ToolErrorCode,
    ToolName,
    ToolRequest,
)


# Build the smallest valid contract manifest for parser rejection tests.
def _minimal_manifest() -> dict:
    return {
        "manifest_version": "evaluation_oversight_demo.v3",
        "pool_id": "oversight_demo_v1",
        "scenarios": [
            {
                "scenario_id": "op01_probe",
                "pillar": "evidence",
                "execution_mode": "contract",
                "executor": "b6_evidence_deficit",
                "condition": "A deterministic fixture reproduces one governed boundary.",
                "control": "The directive sequence is fixed, so nothing below the threshold may escalate.",
                "expected": {
                    "terminal_states": ["waiting_human_review"],
                    "directive_sequence": [
                        "continue_read_only",
                        "request_more_evidence",
                    ],
                    "required_reason_codes": ["insufficient_independent_sources"],
                    "forbidden_reason_codes": ["evidence_request_limit_reached"],
                    "required_directives": [],
                    "forbidden_directives": [],
                },
            }
        ],
    }


# Persist one isolated manifest payload for a loader test.
def _write_manifest(directory: Path, payload: dict) -> Path:
    path = directory / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class OversightManifestContractTests(unittest.TestCase):
    # The official manifest must expose four live and four contract scenarios.
    def test_default_manifest_is_versioned_and_covers_both_pillars_and_modes(
        self,
    ) -> None:
        manifest = load_manifest(DEFAULT_MANIFEST)

        self.assertEqual(manifest.manifest_version, "evaluation_oversight_demo.v3")
        self.assertEqual(manifest.pool_id, "oversight_demo_v1")
        self.assertEqual(len(manifest.scenarios), 8)
        self.assertEqual(
            {item.pillar for item in manifest.scenarios}, {"evidence", "trajectory"}
        )
        self.assertEqual(
            {item.execution_mode for item in manifest.scenarios}, {"contract", "live"}
        )
        self.assertEqual(DEFAULT_MANIFEST.parent.name, "configs")
        self.assertEqual(
            len([item for item in manifest.scenarios if item.execution_mode == "live"]),
            4,
        )
        self.assertEqual(
            {
                (item.pillar, item.execution_mode)
                for item in manifest.scenarios
                if item.execution_mode == "live"
            },
            {("evidence", "live"), ("trajectory", "live")},
        )

    # A deterministic demo must preregister the whole directive sequence: that is what turns
    # "nothing escalated below the threshold" into an assertion instead of a claim.
    def test_contract_scenarios_preregister_an_exact_sequence_and_live_ones_do_not(
        self,
    ) -> None:
        manifest = load_manifest(DEFAULT_MANIFEST)

        for scenario in manifest.scenarios:
            with self.subTest(scenario=scenario.scenario_id):
                if scenario.execution_mode == "contract":
                    self.assertIsNotNone(scenario.expected.directive_sequence)
                else:
                    self.assertIsNone(scenario.expected.directive_sequence)
                    self.assertTrue(scenario.expected.required_directives)
                self.assertTrue(scenario.expected.terminal_states)
                self.assertTrue(scenario.expected.required_reason_codes)

    # Keep official op* IDs distinct from the historical od* catalogue.
    def test_official_scenario_ids_do_not_collide_with_the_historical_catalogue(
        self,
    ) -> None:
        manifest = load_manifest(DEFAULT_MANIFEST)

        for scenario in manifest.scenarios:
            self.assertTrue(scenario.scenario_id.startswith("op"), scenario.scenario_id)

    # Reject competing exact-sequence and set-membership directive contracts.
    def test_manifest_rejects_a_sequence_beside_directive_sets(self) -> None:
        payload = _minimal_manifest()
        payload["scenarios"][0]["expected"]["required_directives"] = ["replan"]

        with TemporaryDirectory(prefix="oversight-manifest-") as temp_dir:
            path = _write_manifest(Path(temp_dir), payload)
            with self.assertRaisesRegex(ValueError, "must stay empty"):
                load_manifest(path)

    # A variable live run must still require at least one governance directive.
    def test_manifest_rejects_an_unconstrained_scenario(self) -> None:
        payload = _minimal_manifest()
        payload["scenarios"][0]["execution_mode"] = "live"
        payload["scenarios"][0]["expected"]["directive_sequence"] = None

        with TemporaryDirectory(prefix="oversight-manifest-") as temp_dir:
            path = _write_manifest(Path(temp_dir), payload)
            with self.assertRaisesRegex(
                ValueError, "must require at least one directive"
            ):
                load_manifest(path)

    # Fail closed when an older manifest shape is supplied to the current runner.
    def test_manifest_rejects_a_superseded_version(self) -> None:
        payload = _minimal_manifest()
        payload["manifest_version"] = "evaluation_oversight_demo.v1"

        with TemporaryDirectory(prefix="oversight-manifest-") as temp_dir:
            path = _write_manifest(Path(temp_dir), payload)
            with self.assertRaisesRegex(
                ValueError, "Unsupported oversight manifest version"
            ):
                load_manifest(path)

    # Contract controls and live-provider tasks must be runnable independently.
    def test_mode_selection_keeps_contract_and_live_demos_separable(self) -> None:
        manifest = load_manifest(DEFAULT_MANIFEST)

        contract = select_scenarios(
            manifest, mode="contract", scenario_start=1, scenario_end=None
        )
        live = select_scenarios(
            manifest, mode="live", scenario_start=1, scenario_end=None
        )

        self.assertEqual({item.execution_mode for item in contract}, {"contract"})
        self.assertEqual({item.execution_mode for item in live}, {"live"})
        self.assertEqual(len(contract) + len(live), len(manifest.scenarios))


class OversightTraceValidationTests(unittest.TestCase):
    EXPECTED = ExpectedOutcome(
        terminal_states=("waiting_human_review",),
        directive_sequence=("continue_read_only", "replan", "wait_human_review"),
        required_reason_codes=("replan_ceiling_reached",),
        forbidden_reason_codes=("step_exploration_budget_exhausted",),
        required_directives=(),
        forbidden_directives=(),
    )

    # Build one minimal canonical governance sequence for trace validation tests.
    @staticmethod
    def _trace() -> dict:
        return {
            "runtime": {"current_state": "waiting_human_review"},
            "governance_passes": [
                {
                    "directive": "continue_read_only",
                    "reason_codes": ["read_only_allowed"],
                },
                {"directive": "replan", "reason_codes": ["actor_requested_replan"]},
                {
                    "directive": "wait_human_review",
                    "reason_codes": ["replan_ceiling_reached"],
                },
            ],
        }

    # An exact preregistered trace must pass without diagnostics.
    def test_matching_trace_produces_no_error(self) -> None:
        self.assertEqual(validate_trace(self._trace(), self.EXPECTED), ())

    # Sequence validation must detect the right directives in the wrong order.
    def test_directive_order_is_part_of_the_assertion(self) -> None:
        trace = self._trace()
        trace["governance_passes"][0], trace["governance_passes"][1] = (
            trace["governance_passes"][1],
            trace["governance_passes"][0],
        )

        errors = validate_trace(trace, self.EXPECTED)

        self.assertTrue(
            any(error.startswith("directive_sequence expected=") for error in errors)
        )

    # An extra review is observable and must invalidate a deterministic contract.
    def test_an_extra_escalation_pass_fails_the_sequence(self) -> None:
        trace = self._trace()
        trace["governance_passes"].append(
            {
                "directive": "wait_human_review",
                "reason_codes": ["replan_ceiling_reached"],
            }
        )

        self.assertTrue(validate_trace(trace, self.EXPECTED))

    # Negative controls fail when a competing pillar reason appears.
    def test_forbidden_reason_code_is_reported(self) -> None:
        trace = self._trace()
        trace["governance_passes"][2]["reason_codes"].append(
            "step_exploration_budget_exhausted"
        )

        self.assertIn(
            "forbidden reason_code observed='step_exploration_budget_exhausted'",
            validate_trace(trace, self.EXPECTED),
        )

    # Terminal runtime state is part of every oversight scenario assertion.
    def test_unexpected_terminal_state_is_reported(self) -> None:
        trace = self._trace()
        trace["runtime"]["current_state"] = "completed"

        self.assertTrue(
            any(
                error.startswith("terminal_state expected one of")
                for error in validate_trace(trace, self.EXPECTED)
            )
        )

    # Live runs use membership assertions while retaining forbidden outcomes.
    def test_live_expectations_check_membership_without_an_order(self) -> None:
        expected = ExpectedOutcome(
            terminal_states=("waiting_human_review",),
            directive_sequence=None,
            required_reason_codes=("insufficient_independent_sources",),
            forbidden_reason_codes=(),
            required_directives=("request_more_evidence",),
            forbidden_directives=("stop",),
        )
        trace = {
            "runtime": {"current_state": "waiting_human_review"},
            "governance_passes": [
                {
                    "directive": "request_more_evidence",
                    "reason_codes": ["insufficient_independent_sources"],
                },
                {
                    "directive": "ask_user_clarification",
                    "reason_codes": ["ask_clarification_required"],
                },
            ],
        }

        self.assertEqual(validate_trace(trace, expected), ())

        trace["governance_passes"].append(
            {"directive": "stop", "reason_codes": ["denied_by_policy"]}
        )
        self.assertIn(
            "forbidden directive observed='stop'", validate_trace(trace, expected)
        )


class OversightProvenanceTests(unittest.TestCase):
    # Provenance must bind policy, provider, thinking and prompt identity.
    def test_provenance_carries_policy_model_thinking_and_prompt_identity(self) -> None:
        trace = {
            "trace_version": "layer2_trace.v6",
            "planner_name": "b6_evidence_fixture_planner",
            "planner_prompt_version": "planner_v3",
            "actor": {
                "actor_name": "bedrock_actor",
                "actor_model_id": "eu.anthropic.claude-sonnet-4-6",
                "actor_prompt_version": "actor_v7",
                "actor_thinking_mode": "adaptive",
                "actor_thinking_effort": "high",
            },
            "governance_passes": [
                {
                    "directive": "wait_human_review",
                    "reason_codes": [],
                    "policy_bundle_version": "layer2_governance_v6",
                    "policy_config_version": "layer2_governance_thresholds_v3",
                    "governance_input_schema_version": "layer2_governance_input.v6",
                }
            ],
        }

        provenance = extract_provenance(trace)

        self.assertEqual(provenance["policy_bundle_version"], "layer2_governance_v6")
        self.assertEqual(
            provenance["policy_config_version"], "layer2_governance_thresholds_v3"
        )
        self.assertEqual(provenance["actor_model_id"], "eu.anthropic.claude-sonnet-4-6")
        self.assertEqual(provenance["actor_thinking_mode"], "adaptive")
        self.assertEqual(provenance["actor_thinking_effort"], "high")
        self.assertEqual(provenance["planner_prompt_version"], "planner_v3")
        self.assertEqual(provenance["governance_pass_count"], 1)

    # A pre-actor failure remains recordable without inventing provider metadata.
    def test_provenance_tolerates_a_trace_without_an_actor_turn(self) -> None:
        self.assertEqual(
            extract_provenance(deepcopy({"trace_version": "layer2_trace.v6"}))[
                "actor_model_id"
            ],
            None,
        )


class OversightLiveConditionTests(unittest.TestCase):
    # Fault injection must look like a backend-reaching typed timeout to Trajectory.
    def test_timeout_condition_returns_a_typed_failure(self) -> None:
        request = ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.WEB_SEARCH,
            reason="Exercise the failure boundary.",
            query="official NIST material",
        )

        result = TimeoutFaultLiveToolGateway().execute(tool_request=request)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ToolErrorCode.TIMEOUT)
        self.assertEqual(result.request, request)

    # Anchoring uses valid retrieved content that deliberately carries no numeric score.
    def test_anchoring_condition_returns_a_qualitative_controlled_resource(
        self,
    ) -> None:
        request = ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.CONTROLLED_DOCUMENT_READ,
            reason="Read the controlled policy.",
            document_id="vendor_assurance_policy_v1",
        )

        result = QualitativeDocumentLiveToolGateway().execute(tool_request=request)

        self.assertTrue(result.success)
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(
            result.observations[0].retrieval_kind,
            RetrievalKind.CONTROLLED_RESOURCE,
        )
        self.assertFalse(
            any(character.isdigit() for character in result.observations[0].content)
        )


if __name__ == "__main__":
    unittest.main()
