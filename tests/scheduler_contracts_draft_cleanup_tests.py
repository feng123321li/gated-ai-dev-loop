from __future__ import annotations

from .scheduler_contracts_support import (
    Path,
    TemporaryDirectory,
    call_tool,
    json,
    unittest,
)


class DraftCleanupTests(unittest.TestCase):
    """cancel_graph_run abandons a pre-run draft and releases its requirementKey."""

    def _hierarchy(self) -> dict:
        repo_root = Path(__file__).resolve().parents[1]
        with (repo_root / "examples" / "team-loops" / "light-change.json").open(
            encoding="utf-8"
        ) as handle:
            hierarchy = json.load(handle)
        hierarchy["delivery"]["requirementKey"] = "MPROTEIN-CLEANUP-TEST"
        return hierarchy

    def test_abandon_prerun_draft_releases_requirement_key(self) -> None:
        hierarchy = self._hierarchy()
        root_id = hierarchy["delivery"]["id"]
        with TemporaryDirectory() as temporary:
            root = str(Path(temporary))
            call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=root,
                workspace_root=root,
            )
            result = call_tool(
                "cancel_graph_run",
                {
                    "root_id": root_id,
                    "cancelled_by": "tester",
                    "reason": "stuck pre-run draft",
                },
                root=root,
                workspace_root=root,
            )
            self.assertEqual(result["deliveryStatus"], "ABANDONED")
            self.assertIsNone(result["runId"])
            # requirementKey released: a new Delivery with the same key previews OK
            retry = json.loads(json.dumps(hierarchy))
            retry["delivery"]["id"] = root_id + "-retry"
            call_tool(
                "preview_hierarchy",
                {"hierarchy": retry},
                root=root,
                workspace_root=root,
            )
