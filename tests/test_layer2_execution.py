from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from layer2_runtime_governance.execution.action_schemas import (
    build_actor_runtime_tools,
    build_json_actor_output_shape,
)


class ActorActionSchemaTests(unittest.TestCase):
    # Runtime actions must remain unique, closed, and provider-neutral.
    def test_runtime_action_schemas_are_unique_closed_functions(self) -> None:
        actions = build_actor_runtime_tools()
        names = [str(action["name"]) for action in actions]

        self.assertEqual(len(names), len(set(names)))
        self.assertIn("request_web_search", names)
        self.assertIn("emit_final_output", names)
        self.assertIn("emit_needs_replan", names)
        for action in actions:
            self.assertEqual(action["type"], "function")
            parameters = action["parameters"]
            self.assertEqual(parameters["type"], "object")
            self.assertFalse(parameters["additionalProperties"])
            self.assertTrue(set(parameters["required"]).issubset(parameters["properties"]))

    # JSON-only providers must receive exactly the same action vocabulary.
    def test_json_output_shape_lists_all_runtime_actions(self) -> None:
        actions = build_actor_runtime_tools()
        output_shape = json.loads(build_json_actor_output_shape())

        self.assertEqual(
            output_shape["allowed_tool_names"],
            [action["name"] for action in actions],
        )


if __name__ == "__main__":
    unittest.main()
