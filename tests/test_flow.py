from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hdg.execution import list_ready_tasks, select_development_mode
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import GovernanceRepository

from .fixtures import task_hierarchy


class WorkItemFlowTests(unittest.TestCase):
    def test_prepare_plan_freeze_and_select_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            self.assertTrue(result["created"])
            package = Path(result["artifactDir"])
            self.assertTrue((package / "development-plan.md").is_file())
            self.assertTrue((package / "development-plan.json").is_file())

            frozen = freeze_hierarchy(
                root=temporary,
                root_id=result["rootId"],
                expected_hierarchy_fingerprint=result["hierarchyFingerprint"],
                confirmed=True,
            )
            self.assertEqual(frozen["stage"], "BASELINE_FROZEN")

            selected = select_development_mode(
                root=temporary,
                item_id=result["rootId"],
                mode="manual",
                expected_baseline_fingerprint=result["baselineFingerprints"][result["rootId"]],
                confirmed=True,
            )
            self.assertEqual(selected["status"], "FROZEN")
            self.assertEqual(list_ready_tasks(root=temporary, work_item_id=result["rootId"]), [result["rootId"]])

            registry = GovernanceRepository(temporary).read_registry()
            self.assertEqual(registry["schemaVersion"], 3)
            self.assertTrue(registry["workItems"][0]["developmentPlan"])
            self.assertNotIn("developmentReview", registry["workItems"][0])
            self.assertEqual(
                set(registry),
                {"schemaVersion", "coordinationRoot", "revision", "currentFocus", "workItems", "updatedAt"},
            )


if __name__ == "__main__":
    unittest.main()
