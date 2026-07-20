from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
LAYER2_ROOT = SRC_ROOT / "layer2_runtime_governance"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class Layer2ArchitectureTests(unittest.TestCase):
    # Tool capability modules must not depend on runtime orchestration or governance state.
    def test_tools_package_respects_inward_dependency_boundary(self) -> None:
        forbidden_modules = {
            "layer2_runtime_governance.graph",
            "layer2_runtime_governance.governance",
            "layer2_runtime_governance.nodes",
            "layer2_runtime_governance.state",
        }
        for path in (LAYER2_ROOT / "tools").rglob("*.py"):
            imported_modules = _absolute_imports(path)
            self.assertTrue(
                forbidden_modules.isdisjoint(imported_modules),
                f"{path} crosses the tool capability boundary: "
                f"{sorted(forbidden_modules.intersection(imported_modules))}",
            )

    # Actor orchestration modules must not depend back on graph or governance wiring.
    def test_execution_package_respects_runtime_dependency_boundary(self) -> None:
        forbidden_modules = {
            "layer2_runtime_governance.graph",
            "layer2_runtime_governance.governance",
            "layer2_runtime_governance.nodes",
        }
        for path in (LAYER2_ROOT / "execution").rglob("*.py"):
            imported_modules = _absolute_imports(path)
            self.assertTrue(
                forbidden_modules.isdisjoint(imported_modules),
                f"{path} crosses the actor execution boundary: "
                f"{sorted(forbidden_modules.intersection(imported_modules))}",
            )

    # Non-persistent facades must not obscure the root capability layout.
    def test_non_persistent_compatibility_facades_are_removed_from_root(self) -> None:
        retired_root_facades = (
            "acting.py",
            "bedrock_client.py",
            "hitl_dashboard.py",
            "persistence.py",
            "tool_gateway.py",
        )
        for filename in retired_root_facades:
            self.assertFalse((LAYER2_ROOT / filename).exists(), filename)
        self.assertTrue((LAYER2_ROOT / "compat" / "README.md").exists())

    # Graph wiring must remain readable without serializer or node business logic.
    def test_graph_module_stays_a_small_composition_surface(self) -> None:
        line_count = len((LAYER2_ROOT / "graph.py").read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(line_count, 180)

    # Runtime nodes are now capability services and no legacy facade should return.
    def test_nodes_compatibility_facade_is_removed(self) -> None:
        self.assertFalse((LAYER2_ROOT / "nodes.py").exists())

    # Governance contracts and OPA processing must stay in governance_components.
    def test_governance_compatibility_facade_stays_small(self) -> None:
        line_count = len((LAYER2_ROOT / "governance.py").read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(line_count, 50)

    # Canonical state types must be owned by domain modules, not redefined in the facade.
    def test_state_compatibility_facade_has_no_class_definitions(self) -> None:
        path = LAYER2_ROOT / "state.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, ast.ClassDef) for node in tree.body))
        self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 100)

    # Trace construction is owned directly by observability without a legacy facade.
    def test_trace_compatibility_facade_is_removed(self) -> None:
        self.assertFalse((LAYER2_ROOT / "trace.py").exists())

    # Production modules must consume canonical packages instead of legacy import anchors.
    def test_production_code_does_not_import_legacy_facades(self) -> None:
        forbidden_modules = {
            "layer2_runtime_governance.governance",
            "layer2_runtime_governance.replanning",
            "layer2_runtime_governance.state",
            "layer2_runtime_governance.compat",
        }
        excluded_paths = {
            LAYER2_ROOT / "governance.py",
            LAYER2_ROOT / "replanning.py",
            LAYER2_ROOT / "state.py",
            *(LAYER2_ROOT / "compat").glob("*.py"),
        }
        for path in LAYER2_ROOT.rglob("*.py"):
            if path in excluded_paths:
                continue
            imported_modules = _absolute_imports(path)
            self.assertTrue(
                forbidden_modules.isdisjoint(imported_modules),
                f"{path} imports legacy facade: "
                f"{sorted(forbidden_modules.intersection(imported_modules))}",
            )

    # Compatibility anchors need an explicit public surface, never a wildcard re-export.
    def test_legacy_facades_do_not_use_wildcard_imports(self) -> None:
        legacy_facades = (
            "governance.py",
            "replanning.py",
            "state.py",
            "compat/acting.py",
            "compat/bedrock_client.py",
            "compat/hitl_dashboard.py",
            "compat/persistence.py",
            "compat/tool_gateway.py",
        )
        for filename in legacy_facades:
            tree = ast.parse((LAYER2_ROOT / filename).read_text(encoding="utf-8"))
            wildcard_imports = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and any(alias.name == "*" for alias in node.names)
            ]
            self.assertFalse(wildcard_imports, f"{filename} has a wildcard re-export")

    # OPA is the sole final decision authority; the retired Python combiner stays absent.
    def test_legacy_python_decision_combiner_is_removed(self) -> None:
        self.assertFalse((LAYER2_ROOT / "decision_combiner.py").exists())
        self.assertFalse(
            (LAYER2_ROOT / "governance_components" / "legacy_combiner.py").exists()
        )

    # Runtime characterization tests remain grouped by capability, not one giant module.
    def test_runtime_test_monolith_is_removed(self) -> None:
        self.assertFalse((REPO_ROOT / "tests" / "test_layer2_runtime_governance.py").exists())

    # Large capability implementations belong to packages rather than the Layer 2 root.
    def test_root_keeps_capability_implementations_out_of_flat_modules(self) -> None:
        package_names = {
            "behavioral",
            "evaluation",
            "hitl",
            "judge",
            "oversight",
            "planning",
        }
        for package_name in package_names:
            self.assertTrue((LAYER2_ROOT / package_name / "__init__.py").exists())
            self.assertFalse((LAYER2_ROOT / f"{package_name}.py").exists())
        self.assertTrue(
            (LAYER2_ROOT / "governance_components" / "input_document.py").exists()
        )
        self.assertTrue(
            (LAYER2_ROOT / "infrastructure" / "runtime_artifacts.py").exists()
        )

    # Entrypoints must use the explicit dependency-injection composition root.
    def test_live_entrypoints_use_explicit_composition_root(self) -> None:
        for path in (SRC_ROOT / "main_layer2.py", SRC_ROOT / "main_layer2_dashboard.py"):
            source = path.read_text(encoding="utf-8")
            self.assertIn("compose_layer2_runtime", source)
            self.assertNotIn("build_runtime_graph(", source)

    # Persisted threads must accept both pre-refactor and canonical domain type paths.
    def test_checkpoint_allowlist_preserves_legacy_state_and_governance_paths(self) -> None:
        from layer2_runtime_governance.infrastructure.checkpointing import (
            _legacy_persisted_type_paths,
        )

        legacy_paths = _legacy_persisted_type_paths()
        self.assertIn(("layer2_runtime_governance.state", "TaskState"), legacy_paths)
        self.assertIn(
            ("layer2_runtime_governance.governance", "GovernanceDecisionTrace"),
            legacy_paths,
        )
        self.assertIn(
            ("layer2_runtime_governance.replanning", "ReplanDecision"),
            legacy_paths,
        )

    # Governance components must not depend on graph wiring or runtime node facades.
    def test_governance_components_respect_policy_boundary(self) -> None:
        forbidden_modules = {
            "layer2_runtime_governance.graph",
            "layer2_runtime_governance.nodes",
            "layer2_runtime_governance.runtime",
            "layer2_runtime_governance.tool_gateway",
        }
        for path in (LAYER2_ROOT / "governance_components").rglob("*.py"):
            imported_modules = _absolute_imports(path)
            self.assertTrue(
                forbidden_modules.isdisjoint(imported_modules),
                f"{path} crosses the governance boundary: "
                f"{sorted(forbidden_modules.intersection(imported_modules))}",
            )

    # Public domain views must never import framework or infrastructure packages directly.
    def test_domain_package_has_no_framework_or_infrastructure_imports(self) -> None:
        forbidden_roots = {
            "boto3",
            "langchain_core",
            "langgraph",
            "openai",
            "sqlite3",
            "subprocess",
            "urllib",
        }
        for path in (LAYER2_ROOT / "domain").rglob("*.py"):
            imported_roots = {
                module.split(".", 1)[0]
                for module in _absolute_imports(path)
            }
            self.assertTrue(
                forbidden_roots.isdisjoint(imported_roots),
                f"{path} imports forbidden domain dependency: "
                f"{sorted(forbidden_roots.intersection(imported_roots))}",
            )


# Resolve absolute imports, including relative imports inside the Layer 2 package.
def _absolute_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package_parts = list(path.relative_to(SRC_ROOT).with_suffix("").parts[:-1])
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if node.level == 0:
            modules.add(node.module)
            continue
        prefix = package_parts[: len(package_parts) - node.level + 1]
        modules.add(".".join([*prefix, node.module]))
    return modules


if __name__ == "__main__":
    unittest.main()
