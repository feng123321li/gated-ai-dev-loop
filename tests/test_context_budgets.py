from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from hdg.mcp_tools import tool_definitions


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = REPOSITORY_ROOT / "src" / "hdg"


def _source_size(name: str) -> int:
    return len((SOURCE_PACKAGE / name).read_text(encoding="utf-8"))


def _function_span(name: str, function_name: str) -> int:
    path = SOURCE_PACKAGE / name
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return function.end_lineno - function.lineno + 1


class SourceContextBudgetTests(unittest.TestCase):
    def test_repository_facade_stays_within_targeted_reading_budget(self) -> None:
        self.assertLessEqual(_source_size("repository.py"), 50_000)

    def test_graph_runtime_facade_stays_within_targeted_reading_budget(self) -> None:
        self.assertLessEqual(_source_size("graph_runtime.py"), 15_000)

    def test_evidence_facade_stays_within_targeted_reading_budget(self) -> None:
        self.assertLessEqual(_source_size("evidence.py"), 10_000)

    def test_model_facade_stays_within_targeted_reading_budget(self) -> None:
        self.assertLessEqual(_source_size("model.py"), 10_000)

    def test_operation_dispatcher_stays_small(self) -> None:
        self.assertLessEqual(
            _function_span("operations.py", "execute_operation"),
            60,
        )


class McpContextBudgetTests(unittest.TestCase):
    def test_complete_tool_catalog_stays_below_context_budget(self) -> None:
        serialized = json.dumps(
            tool_definitions(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertLessEqual(len(serialized), 32_000)

    def test_tool_catalog_omits_duplicate_optional_display_metadata(self) -> None:
        for tool in tool_definitions():
            with self.subTest(tool=tool["name"]):
                self.assertNotIn("title", tool)
                self.assertNotIn("outputSchema", tool)
                self.assertIn("title", tool["annotations"])


if __name__ == "__main__":
    unittest.main()
