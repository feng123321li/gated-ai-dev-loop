from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hdg.execution import list_ready_tasks
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import GovernanceRepository

from .fixtures import task_hierarchy


class WorkItemFlowTests(unittest.TestCase):
    def test_prepare_plan_and_single_freeze_make_task_ready(self) -> None:
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
                development_mode="manual",
                confirmed=True,
            )
            self.assertEqual(frozen["stage"], "BASELINE_FROZEN")
            self.assertEqual(
                frozen["humanArtifacts"]["requirementHandoff"],
                ".hierarchical-delivery-governance/work-items/t-python-controller/requirement-handoff.md",
            )
            self.assertIn("一次接管整棵需求树", frozen["handoffPrompt"])
            self.assertIn("不要要求用户逐 Task 回复启动", frozen["handoffPrompt"])
            self.assertEqual(
                (package / "requirement-handoff.md").read_text(encoding="utf-8"),
                frozen["handoffPrompt"],
            )

            self.assertEqual(list_ready_tasks(root=temporary, work_item_id=result["rootId"]), [result["rootId"]])

            registry = GovernanceRepository(temporary).read_registry()
            self.assertEqual(registry["schemaVersion"], 3)
            self.assertTrue(registry["workItems"][0]["developmentPlan"])
            self.assertNotIn("developmentReview", registry["workItems"][0])
            self.assertEqual(
                set(registry),
                {"schemaVersion", "coordinationRoot", "revision", "currentFocus", "workItems", "updatedAt"},
            )
            progress = (package / "progress.md").read_text(encoding="utf-8")
            self.assertIn("开发建议：manual", progress)
            self.assertIn("需求级交接：[requirement-handoff.md](requirement-handoff.md)", progress)
            self.assertIn("一次性交接整棵需求树", progress)
            self.assertNotIn("按需生成可复制的独立开发 handoff", progress)


if __name__ == "__main__":
    unittest.main()
