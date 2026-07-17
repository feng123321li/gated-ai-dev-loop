from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hdg.execution import list_ready_tasks, select_development_mode
from hdg.planning import freeze_work_item, prepare_work_item
from hdg.repository import GovernanceRepository

from .fixtures import task_definition


class WorkItemFlowTests(unittest.TestCase):
    def test_prepare_review_freeze_and_select_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = prepare_work_item(
                root=temporary,
                definition=task_definition(),
                host_runtime="codex",
            )
            self.assertTrue(result["created"])
            package = Path(result["artifactDir"])
            self.assertTrue((package / "development-review.md").is_file())
            self.assertTrue((package / "development-plan.json").is_file())

            frozen = freeze_work_item(
                root=temporary,
                item_id=result["id"],
                expected_baseline_fingerprint=result["baselineFingerprint"],
                confirmed=True,
            )
            self.assertEqual(frozen["stage"], "BASELINE_FROZEN")

            selected = select_development_mode(
                root=temporary,
                item_id=result["id"],
                mode="manual",
                expected_baseline_fingerprint=result["baselineFingerprint"],
                confirmed=True,
            )
            self.assertEqual(selected["status"], "FROZEN")
            self.assertEqual(list_ready_tasks(root=temporary, work_item_id=result["id"]), [result["id"]])

            registry = GovernanceRepository(temporary).read_registry()
            self.assertEqual(registry["schemaVersion"], 3)
            self.assertEqual(
                set(registry),
                {"schemaVersion", "coordinationRoot", "revision", "currentFocus", "workItems", "promotionHistory", "updatedAt"},
            )


if __name__ == "__main__":
    unittest.main()
