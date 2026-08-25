from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from tests.layer2_runtime_fixtures import _build_research_intake_result

from layer2_runtime_governance.actor_contract import build_actor_task
from layer2_runtime_governance.domain import PlanStep, PlanStepStatus, TaskState
from layer2_runtime_governance.execution.action_schemas import build_actor_runtime_tools
from layer2_runtime_governance.execution.result_builder import (
    convert_tool_call_to_actor_result,
)
from layer3_tool_bus_external_capabilities.tool_contract import (
    ToolErrorCode,
    ToolName,
    ToolRequest,
    canonical_tool_request_identity,
)
from layer3_tool_bus_external_capabilities.tools import (
    OpenAIToolGateway,
    load_tool_gateway_config,
)
from layer3_tool_bus_external_capabilities.tools.controlled_corpus import (
    load_controlled_corpus_config,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_CONFIG_PATH = REPO_ROOT / "configs" / "layer2_tool_gateway.json"


class ControlledCorpusToolTests(unittest.TestCase):
    # The default corpus remains a configuration-owned list of bounded resources.
    def test_default_registry_loads_documents_and_datasets(self) -> None:
        config = load_controlled_corpus_config()

        self.assertIn("information_security_policy_v1", config.documents)
        self.assertIn("evaluation_incidents_v1", config.datasets)
        self.assertEqual(config.datasets["evaluation_incidents_v1"].max_rows, 50)

    # The actor receives both local capability actions only because the registry enables them.
    def test_production_action_binding_exposes_controlled_corpus_actions(self) -> None:
        tool_config = load_tool_gateway_config(config_path=TOOL_CONFIG_PATH)
        names = {item["name"] for item in build_actor_runtime_tools(tool_config)}

        self.assertIn("request_controlled_document", names)
        self.assertIn("request_controlled_dataset", names)

    # A document request is resolved by stable ID and carries a non-public provenance label.
    def test_gateway_reads_registered_controlled_document(self) -> None:
        gateway = OpenAIToolGateway(
            config=load_tool_gateway_config(config_path=TOOL_CONFIG_PATH)
        )

        result = gateway.execute(
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.CONTROLLED_DOCUMENT_READ,
                reason="Extract obligations from the controlled incident policy.",
                document_id="information_security_policy_v1",
            )
        )

        self.assertTrue(result.success)
        self.assertIn("Controlled evaluation document", result.content or "")
        self.assertIn("Reliability Lead", result.content or "")
        self.assertEqual(
            result.observations[0].locator,
            "controlled-document://information_security_policy_v1",
        )

    # Dataset observations preserve raw row counts, schema version, and a bounded row subset.
    def test_gateway_reads_registered_controlled_dataset(self) -> None:
        gateway = OpenAIToolGateway(
            config=load_tool_gateway_config(config_path=TOOL_CONFIG_PATH)
        )

        result = gateway.execute(
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.CONTROLLED_DATASET_READ,
                reason="Review a bounded incident snapshot.",
                dataset_id="evaluation_incidents_v1",
                limit=3,
            )
        )

        self.assertTrue(result.success)
        payload = json.loads(result.content or "{}")
        self.assertEqual(payload["schema_version"], "1")
        self.assertEqual(payload["total_rows"], 8)
        self.assertEqual(payload["returned_rows"], 3)
        self.assertEqual(payload["rows_omitted"], 5)
        self.assertEqual(
            result.observations[0].locator,
            "controlled-dataset://evaluation_incidents_v1",
        )

    # IDs are the only selection surface; an actor-supplied path cannot escape the registry.
    def test_unknown_controlled_resource_is_rejected(self) -> None:
        gateway = OpenAIToolGateway(
            config=load_tool_gateway_config(config_path=TOOL_CONFIG_PATH)
        )

        result = gateway.execute(
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.CONTROLLED_DOCUMENT_READ,
                reason="Attempt an unregistered local read.",
                document_id="../../outside",
                path="C:\\untrusted.txt",
            )
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ToolErrorCode.FORBIDDEN)
        self.assertIn("not registered", result.error_message or "")

    # Administrative registry paths must resolve below the tracked controlled corpus root.
    def test_registry_rejects_resource_outside_controlled_corpus(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "corpus.json"
            config_path.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "document_id": "outside",
                                "title": "Outside",
                                "description": "Invalid test entry.",
                                "path": "README.md",
                            }
                        ],
                        "datasets": [],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "outside evaluation_corpus"):
                load_controlled_corpus_config(config_path=config_path)

    # The actor-side action is converted into the new typed ToolRequest without raw paths.
    def test_actor_converts_dataset_action_to_typed_request(self) -> None:
        state = TaskState.from_intake(_build_research_intake_result())
        state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Inspect the controlled incident snapshot.",
                rationale="The task names a bounded local dataset.",
                expected_outcome="Observed structured facts.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        state.current_plan_step_index = 0

        result = convert_tool_call_to_actor_result(
            actor_task=build_actor_task(task_state=state),
            tool_call=SimpleNamespace(
                name="request_controlled_dataset",
                arguments=json.dumps(
                    {
                        "summary": "Inspect the registered incident snapshot.",
                        "reason": "The current step requires the controlled rows.",
                        "dataset_id": "evaluation_incidents_v1",
                        "max_rows": 3,
                    }
                ),
            ),
        )

        self.assertEqual(result.tool_request.tool_name, ToolName.CONTROLLED_DATASET_READ)
        self.assertEqual(result.tool_request.dataset_id, "evaluation_incidents_v1")
        self.assertEqual(result.tool_request.limit, 3)
        self.assertIsNone(result.tool_request.path)

    # Equivalent controlled reads remain subject to the generic progress guard cache.
    def test_dataset_action_key_ignores_reason_but_respects_row_limit(self) -> None:
        first = ToolRequest(
            "step_1",
            ToolName.CONTROLLED_DATASET_READ,
            "Read the snapshot.",
            dataset_id="evaluation_incidents_v1",
            limit=3,
        )
        same = ToolRequest(
            "step_1",
            ToolName.CONTROLLED_DATASET_READ,
            "Use the registered local data.",
            dataset_id="EVALUATION_INCIDENTS_V1",
            limit=3,
        )
        different_limit = ToolRequest(
            "step_1",
            ToolName.CONTROLLED_DATASET_READ,
            "Read more rows.",
            dataset_id="evaluation_incidents_v1",
            limit=4,
        )

        self.assertEqual(
            canonical_tool_request_identity(first),
            canonical_tool_request_identity(same),
        )
        self.assertNotEqual(
            canonical_tool_request_identity(first),
            canonical_tool_request_identity(different_limit),
        )


if __name__ == "__main__":
    unittest.main()
