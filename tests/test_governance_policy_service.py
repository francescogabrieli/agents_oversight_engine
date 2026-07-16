from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path
from urllib.request import Request

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from governance_policy_service import OPAClientSettings, OPADecisionClient, PolicyServiceError


class _FakeResponse:
    # Store one JSON response body for the client contract tests.
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    # Match urllib's context-manager response surface.
    def __enter__(self) -> "_FakeResponse":
        return self

    # No resource cleanup is needed by the in-memory response.
    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    # Return the encoded response exactly as urllib would.
    def read(self) -> bytes:
        return self._body


class PolicyServiceClientTests(unittest.TestCase):
    # The transport package must remain independent from the runtime monolith.
    def test_package_has_no_runtime_imports(self) -> None:
        package_root = SRC_ROOT / "governance_policy_service"
        for path in package_root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name.split(".", 1)[0] for alias in node.names}
                    self.assertNotIn("layer2_runtime_governance", imported, path)
                if isinstance(node, ast.ImportFrom) and node.module is not None:
                    self.assertNotEqual(node.module.split(".", 1)[0], "layer2_runtime_governance", path)

    # The client should serialize only the stable OPA input envelope.
    def test_evaluate_posts_versioned_input_and_returns_result(self) -> None:
        captured: dict[str, object] = {}

        # Capture the outbound request without opening a network connection.
        def opener(request: Request, *, timeout: float) -> _FakeResponse:
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse({"result": {"directive": "continue"}})

        client = OPADecisionClient(
            settings=OPAClientSettings(base_url="http://policy.internal/", timeout_seconds=2.5),
            opener=opener,
        )
        result = client.evaluate(
            path="/v1/data/layer2/governance/decision",
            input_document={"schema_version": "governance_decision_input.v1"},
        )

        self.assertEqual(result, {"directive": "continue"})
        self.assertEqual(captured["url"], "http://policy.internal/v1/data/layer2/governance/decision")
        self.assertEqual(captured["timeout"], 2.5)
        self.assertEqual(
            captured["payload"],
            {"input": {"schema_version": "governance_decision_input.v1"}},
        )

    # A response without OPA's result envelope must fail at the transport boundary.
    def test_missing_result_envelope_raises_service_error(self) -> None:
        # Return a syntactically valid but contract-invalid OPA response.
        def opener(request: Request, *, timeout: float) -> _FakeResponse:
            return _FakeResponse({"unexpected": {}})

        client = OPADecisionClient(
            settings=OPAClientSettings(base_url="http://policy.internal"),
            opener=opener,
        )

        with self.assertRaisesRegex(PolicyServiceError, "result field"):
            client.evaluate(path="/decision", input_document={})


if __name__ == "__main__":
    unittest.main()
