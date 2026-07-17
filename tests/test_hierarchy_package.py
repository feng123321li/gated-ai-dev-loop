from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hdg.errors import GatedLoopError
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import GovernanceRepository

from .fixtures import (
    capability_definition,
    delivery_hierarchy,
    hierarchy_definition,
    hierarchy_node,
    task_definition,
    task_hierarchy,
)


class HierarchyPackageTests(unittest.TestCase):
    @staticmethod
    def _capability_hierarchy() -> dict:
        capability = capability_definition()
        task = task_definition(parentId=capability["id"], gateLevel="FULL")
        return hierarchy_definition(capability, [hierarchy_node(task)])

    def test_prepare_materializes_one_nested_directory_for_the_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=self._capability_hierarchy(),
                host_runtime="codex",
            )

            work_items = Path(temporary, ".hierarchical-delivery-governance", "work-items")
            self.assertEqual([item.name for item in work_items.iterdir()], ["c-python-runtime"])
            root = work_items / "c-python-runtime"
            child = root / "children" / "t-python-controller"
            self.assertTrue((root / "hierarchy.json").is_file())
            self.assertTrue((root / "development-plan.md").is_file())
            self.assertTrue((child / "baseline.json").is_file())
            self.assertEqual(prepared["artifactDir"], str(root))

            registry = GovernanceRepository(temporary).read_registry()
            by_id = {item["id"]: item for item in registry["workItems"]}
            self.assertEqual(by_id["c-python-runtime"]["packagePath"], "work-items/c-python-runtime")
            self.assertEqual(
                by_id["t-python-controller"]["packagePath"],
                "work-items/c-python-runtime/children/t-python-controller",
            )

    def test_all_legal_depths_use_exactly_one_requirement_root_directory(self) -> None:
        cases = (
            (task_hierarchy(), "t-python-controller", "work-items/t-python-controller"),
            (
                self._capability_hierarchy(),
                "c-python-runtime",
                "work-items/c-python-runtime/children/t-python-controller",
            ),
            (
                delivery_hierarchy(),
                "d-python-governance",
                "work-items/d-python-governance/children/c-python-runtime/children/t-python-controller",
            ),
        )
        for hierarchy, root_id, deepest_path in cases:
            with self.subTest(root_id=root_id), tempfile.TemporaryDirectory() as temporary:
                prepare_hierarchy(root=temporary, hierarchy=hierarchy, host_runtime="codex")
                work_items = Path(temporary, ".hierarchical-delivery-governance", "work-items")
                self.assertEqual([item.name for item in work_items.iterdir()], [root_id])
                registry = GovernanceRepository(temporary).read_registry()
                self.assertIn(deepest_path, {item["packagePath"] for item in registry["workItems"]})

    def test_prepare_writes_a_single_plan_for_the_complete_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=self._capability_hierarchy(),
                host_runtime="claude-code",
            )
            plan = Path(prepared["artifactDir"], "development-plan.md").read_text(encoding="utf-8")
            self.assertIn("需求层级开发方案", plan)
            self.assertIn("c-python-runtime", plan)
            self.assertIn("t-python-controller", plan)
            self.assertIn(prepared["hierarchyFingerprint"], plan)
            self.assertIn("无需复制或复述指纹", plan)
            self.assertIn("无需复述指纹", prepared["nextAction"])

    def test_one_confirmation_freezes_every_node_in_the_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=self._capability_hierarchy(),
                host_runtime="codex",
            )
            frozen = freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                confirmed=True,
            )

            self.assertEqual(frozen["frozenItemIds"], ["c-python-runtime", "t-python-controller"])
            registry = GovernanceRepository(temporary).read_registry()
            by_id = {item["id"]: item for item in registry["workItems"]}
            self.assertEqual(by_id["c-python-runtime"]["status"], "FROZEN")
            self.assertEqual(by_id["t-python-controller"]["status"], "WAITING_FOR_DEVELOPMENT_MODE_SELECTION")
            self.assertTrue(all(item["stage"] == "BASELINE_FROZEN" for item in by_id.values()))
            hierarchy_state = json.loads(
                Path(prepared["artifactDir"], "hierarchy.json").read_text(encoding="utf-8")
            )
            self.assertEqual(hierarchy_state["review"]["status"], "APPROVED")

    def test_hierarchy_plan_tampering_blocks_the_single_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=self._capability_hierarchy(),
                host_runtime="codex",
            )
            plan = Path(prepared["artifactDir"], "development-plan.md")
            plan.write_text(plan.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

            with self.assertRaises(GatedLoopError) as raised:
                freeze_hierarchy(
                    root=temporary,
                    root_id=prepared["rootId"],
                    expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                    confirmed=True,
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_HIERARCHY_PLAN_CHANGED")

    def test_missing_planned_child_is_rejected_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hierarchy = hierarchy_definition(capability_definition())
            with self.assertRaises(GatedLoopError) as raised:
                prepare_hierarchy(root=temporary, hierarchy=hierarchy, host_runtime="codex")
            self.assertEqual(raised.exception.code, "WORK_ITEM_HIERARCHY_INCOMPLETE")
            self.assertFalse(Path(temporary, ".hierarchical-delivery-governance").exists())

    def test_workspace_overview_projects_the_parent_child_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepare_hierarchy(
                root=temporary,
                hierarchy=self._capability_hierarchy(),
                host_runtime="codex",
            )
            overview = Path(
                temporary,
                ".hierarchical-delivery-governance",
                "workspace-overview.md",
            ).read_text(encoding="utf-8")
            self.assertIn("## 需求：c-python-runtime", overview)
            self.assertIn("└─ 任务 `t-python-controller`", overview)


if __name__ == "__main__":
    unittest.main()
