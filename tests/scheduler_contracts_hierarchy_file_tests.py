from __future__ import annotations

from .scheduler_contracts_support import (
    GatedLoopError,
    Path,
    SchedulerRepository,
    TemporaryDirectory,
    call_tool,
    json,
    unittest,
    validate_tool_arguments,
)


class HierarchyFileTests(unittest.TestCase):
    """hierarchy_file loads a large hierarchy from a workspace file."""

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _valid_hierarchy(self) -> dict:
        with (self._repo_root() / "examples" / "team-loops" / "light-change.json").open(
            encoding="utf-8"
        ) as handle:
            return json.load(handle)

    @staticmethod
    def _write(workspace: Path, name: str, payload: object) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        (workspace / name).write_text(text, encoding="utf-8")

    def test_preview_loads_hierarchy_from_file(self) -> None:
        hierarchy = self._valid_hierarchy()
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self._write(workspace, "h.json", hierarchy)
            call_tool(
                "preview_hierarchy",
                {"hierarchy_file": "h.json"},
                root=str(workspace),
                workspace_root=str(workspace),
            )
            # hierarchy_file was consumed and substituted; artifacts written
            self.assertTrue(
                (workspace / ".layered-delivery" / "scheduler.db").is_file()
            )
            repository = SchedulerRepository(str(workspace))
            stored = repository.hierarchy(hierarchy["delivery"]["id"])
            self.assertEqual(
                stored["hierarchy"]["delivery"]["id"],
                hierarchy["delivery"]["id"],
            )

    def test_inline_hierarchy_still_works(self) -> None:
        hierarchy = self._valid_hierarchy()
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(workspace),
                workspace_root=str(workspace),
            )
            self.assertTrue(
                (workspace / ".layered-delivery" / "scheduler.db").is_file()
            )

    def test_inline_and_file_are_mutually_exclusive(self) -> None:
        hierarchy = self._valid_hierarchy()
        with TemporaryDirectory() as temporary:
            self._write(Path(temporary), "h.json", hierarchy)
            with self.assertRaises(GatedLoopError) as caught:
                validate_tool_arguments(
                    "preview_hierarchy",
                    {"hierarchy": hierarchy, "hierarchy_file": "h.json"},
                )
            self.assertEqual(
                caught.exception.code, "SCHEDULER_HIERARCHY_INPUT_CONFLICT"
            )

    def test_neither_inline_nor_file_is_rejected(self) -> None:
        with self.assertRaises(GatedLoopError) as caught:
            validate_tool_arguments("preview_hierarchy", {})
        self.assertEqual(
            caught.exception.code, "SCHEDULER_HIERARCHY_INPUT_REQUIRED"
        )

    def test_file_outside_workspace_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "preview_hierarchy",
                    {"hierarchy_file": "../outside.json"},
                    root=str(workspace),
                    workspace_root=str(workspace),
                )
            self.assertEqual(caught.exception.code, "PATH_OUTSIDE_ROOT")

    def test_invalid_json_file_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self._write(workspace, "bad.json", "{not json")
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "preview_hierarchy",
                    {"hierarchy_file": "bad.json"},
                    root=str(workspace),
                    workspace_root=str(workspace),
                )
            self.assertEqual(
                caught.exception.code, "SCHEDULER_HIERARCHY_FILE_INVALID"
            )

    def test_non_object_json_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self._write(workspace, "arr.json", "[1, 2, 3]")
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "preview_hierarchy",
                    {"hierarchy_file": "arr.json"},
                    root=str(workspace),
                    workspace_root=str(workspace),
                )
            self.assertEqual(
                caught.exception.code, "SCHEDULER_HIERARCHY_FILE_INVALID"
            )
