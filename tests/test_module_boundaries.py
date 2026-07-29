from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = REPOSITORY_ROOT / "src" / "hdg"

PUBLIC_APIS = {
    "hdg.evidence": {
        "evidence_record",
        "gate_evidence_contract",
        "hydrate_gate_evidence",
        "valid_evidence_record",
        "valid_gate_artifact",
    },
    "hdg.model": {
        "hierarchy_fingerprint",
        "render_development_plan",
        "render_work_item_baseline",
        "resolve_self_hosting_policy",
        "safe_id",
        "scope_patterns_overlap",
        "validate_hierarchy_definition",
        "validate_work_item_definition",
        "work_item_baseline_fingerprint",
        "work_item_child_contract_fingerprint",
        "work_item_contract_fingerprint",
    },
    "hdg.graph_runtime": {
        "advance_graph",
        "cancel_graph_run",
        "critical_path",
        "get_evidence_contract",
        "get_graph_frontier",
        "get_graph_replay",
        "get_graph_status",
        "list_graph_events",
        "rebuild_graph_run",
    },
    "hdg.repository": {
        "GovernanceRepository",
        "timestamp",
    },
    "hdg.operations": {
        "OperationContext",
        "execute_operation",
    },
}

INTERNAL_FACADE_IMPORT_ALLOWLIST = {
    "evidence": set(),
    "model": set(),
    "graph_runtime": {
        "advance_graph",
        "cancel_graph_run",
        "list_graph_events",
        "rebuild_graph_run",
    },
    "repository": {
        "GovernanceRepository",
    },
    "operations": {
        "execute_operation",
    },
}


class StablePublicApiTests(unittest.TestCase):
    def test_facades_export_only_the_stable_public_api(self) -> None:
        for module_name, expected in PUBLIC_APIS.items():
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertEqual(set(module.__all__), expected)
                self.assertTrue(all(not name.startswith("_") for name in module.__all__))
                for name in expected:
                    self.assertTrue(hasattr(module, name), name)

    def test_internal_modules_import_implementations_from_owning_modules(self) -> None:
        violations: list[str] = []
        for path in sorted(SOURCE_PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                facade = node.module
                if facade not in INTERNAL_FACADE_IMPORT_ALLOWLIST:
                    continue
                allowed = INTERNAL_FACADE_IMPORT_ALLOWLIST[facade]
                disallowed = sorted(
                    alias.name
                    for alias in node.names
                    if alias.name not in allowed
                )
                if disallowed:
                    violations.append(
                        f"{path.name}:{node.lineno} imports "
                        f"{', '.join(disallowed)} from {facade}"
                    )
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
