from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hdg.acceptance import record_work_item_gate
from hdg.errors import GatedLoopError
from hdg.execution import build_task_context, dispatch_task, list_ready_tasks, record_task_result
from hdg import execution
from hdg.planning import freeze_hierarchy, prepare_hierarchy, refresh_work_item_projections
from hdg.repository import GovernanceRepository

from .fixtures import (
    capability_definition,
    delivery_hierarchy,
    hierarchy_definition,
    hierarchy_node,
    task_definition,
    task_hierarchy,
    two_task_capability_hierarchy,
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
            self.assertTrue((root / "baseline.md").is_file())
            self.assertTrue((root / "development-plan.md").is_file())
            self.assertTrue((child / "baseline.md").is_file())
            self.assertEqual(list(work_items.rglob("*.json")), [])
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

    def test_prepare_writes_a_root_aggregate_plan_for_the_complete_tree(self) -> None:
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
                development_mode="active",
                confirmed=True,
            )

            self.assertEqual(frozen["frozenItemIds"], ["c-python-runtime", "t-python-controller"])
            self.assertEqual(
                frozen["rootBaselineFingerprint"],
                prepared["baselineFingerprints"][prepared["rootId"]],
            )
            registry = GovernanceRepository(temporary).read_registry()
            by_id = {item["id"]: item for item in registry["workItems"]}
            self.assertEqual(by_id["c-python-runtime"]["status"], "FROZEN")
            self.assertEqual(by_id["t-python-controller"]["status"], "FROZEN")
            self.assertTrue(all(item["stage"] == "BASELINE_FROZEN" for item in by_id.values()))
            self.assertEqual(
                list_ready_tasks(root=temporary, work_item_id=prepared["rootId"]),
                ["t-python-controller"],
            )
            hierarchy_state = GovernanceRepository(temporary).read_hierarchy_state(prepared["rootId"])
            self.assertEqual(hierarchy_state["review"]["status"], "APPROVED")

    def test_freeze_makes_every_independent_task_ready_without_a_second_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=two_task_capability_hierarchy(),
                host_runtime="codex",
            )
            frozen = freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )

            self.assertEqual(frozen["developmentMode"]["mode"], "active")
            registry = GovernanceRepository(temporary).read_registry()
            by_id = {item["id"]: item for item in registry["workItems"]}
            self.assertEqual(by_id[prepared["rootId"]]["developmentMode"]["mode"], "active")
            self.assertIsNone(by_id["t-python-controller"]["developmentMode"])
            self.assertIsNone(by_id["t-python-worker"]["developmentMode"])
            self.assertEqual(by_id["t-python-controller"]["status"], "FROZEN")
            self.assertEqual(by_id["t-python-worker"]["status"], "FROZEN")
            self.assertEqual(
                list_ready_tasks(root=temporary, work_item_id=prepared["rootId"]),
                ["t-python-controller", "t-python-worker"],
            )
            self.assertEqual(
                build_task_context(root=temporary, item_id="t-python-controller")["developmentMode"],
                "active",
            )
            root_path = Path(prepared["artifactDir"])
            self.assertFalse((root_path / "development-mode.json").exists())
            mode_record = by_id[prepared["rootId"]]["developmentMode"]
            self.assertEqual(
                set(mode_record),
                {"schemaVersion", "rootId", "baselineFingerprint", "mode", "confirmedBy", "confirmedAt"},
            )
            self.assertNotIn("agentCount", mode_record)
            self.assertNotIn("concurrency", mode_record)
            plan = (root_path / "development-plan.md").read_text(encoding="utf-8")
            self.assertNotIn("子 Agent", plan)
            self.assertNotIn("并发槽", plan)
            self.assertFalse((root_path / "children" / "t-python-controller" / "development-mode.json").exists())
            self.assertFalse((root_path / "children" / "t-python-worker" / "development-mode.json").exists())
            workspace_overview = Path(
                temporary,
                ".hierarchical-delivery-governance",
                "workspace-overview.md",
            ).read_text(encoding="utf-8")
            self.assertIn("开发建议：active（需求评审时选择）", workspace_overview)
            child_progress = (
                root_path / "children" / "t-python-controller" / "progress.md"
            ).read_text(encoding="utf-8")
            self.assertIn("开发建议：active", child_progress)

            repeated = freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )
            self.assertTrue(repeated["idempotent"])
            with self.assertRaises(GatedLoopError) as raised:
                freeze_hierarchy(
                    root=temporary,
                    root_id=prepared["rootId"],
                    expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                    development_mode="manual",
                    confirmed=True,
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_DEVELOPMENT_MODE_LOCKED")

    def test_dispatch_artifact_failure_does_not_leave_a_claimed_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )
            real_atomic_write = execution.atomic_write

            def fail_bound_context(target: Path, content: str) -> None:
                if target.name == "development-handoff.md" and "op-atomic" in content:
                    raise OSError("simulated context write failure")
                real_atomic_write(target, content)

            with patch("hdg.execution.atomic_write", side_effect=fail_bound_context):
                with self.assertRaises(OSError):
                    dispatch_task(
                        root=temporary,
                        item_id=prepared["rootId"],
                        owner="developer",
                        operation_id="op-atomic",
                    )

            registry = GovernanceRepository(temporary).read_registry()
            task = registry["workItems"][0]
            self.assertEqual(task["status"], "FROZEN")
            self.assertIsNone(task["claim"])
            self.assertEqual(list_ready_tasks(root=temporary, work_item_id=prepared["rootId"]), [prepared["rootId"]])

    def test_development_mode_does_not_change_the_frozen_requirement_contract(self) -> None:
        results = {}
        with tempfile.TemporaryDirectory() as active_root, tempfile.TemporaryDirectory() as manual_root:
            for mode, temporary in (("active", active_root), ("manual", manual_root)):
                prepared = prepare_hierarchy(
                    root=temporary,
                    hierarchy=two_task_capability_hierarchy(),
                    host_runtime="codex",
                    now="2026-07-17T10:00:00Z",
                )
                frozen = freeze_hierarchy(
                    root=temporary,
                    root_id=prepared["rootId"],
                    expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                    development_mode=mode,
                    confirmed=True,
                    now="2026-07-17T10:01:00Z",
                )
                root_path = Path(prepared["artifactDir"])
                results[mode] = {
                    "hierarchyFingerprint": prepared["hierarchyFingerprint"],
                    "baselineFingerprints": prepared["baselineFingerprints"],
                    "plan": (root_path / "development-plan.md").read_text(encoding="utf-8"),
                    "mode": next(
                        item["developmentMode"]
                        for item in GovernanceRepository(temporary).read_registry()["workItems"]
                        if item["id"] == prepared["rootId"]
                    ),
                    "handoff": frozen["humanArtifacts"]["requirementHandoff"],
                    "handoffPrompt": frozen["handoffPrompt"],
                    "handoffExists": (root_path / "requirement-handoff.md").exists(),
                }

            self.assertEqual(results["active"]["hierarchyFingerprint"], results["manual"]["hierarchyFingerprint"])
            self.assertEqual(results["active"]["baselineFingerprints"], results["manual"]["baselineFingerprints"])
            self.assertEqual(results["active"]["plan"], results["manual"]["plan"])
            self.assertEqual(results["active"]["mode"]["mode"], "active")
            self.assertEqual(results["manual"]["mode"]["mode"], "manual")
            self.assertIsNone(results["active"]["handoff"])
            self.assertIsNone(results["active"]["handoffPrompt"])
            self.assertFalse(results["active"]["handoffExists"])
            self.assertTrue(results["manual"]["handoffExists"])

    def test_manual_freeze_creates_one_handoff_for_the_complete_requirement_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=two_task_capability_hierarchy(),
                host_runtime="codex",
            )
            frozen = freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="manual",
                confirmed=True,
            )
            root = Path(prepared["artifactDir"])
            handoff_path = root / "requirement-handoff.md"
            handoff = handoff_path.read_text(encoding="utf-8")

            self.assertEqual(handoff, frozen["handoffPrompt"])
            self.assertIn("需求级一次性交接", handoff)
            self.assertIn("`c-python-runtime`", handoff)
            self.assertIn("`t-python-controller`", handoff)
            self.assertIn("`t-python-worker`", handoff)
            self.assertIn("按依赖动态计算 READY Task", handoff)
            self.assertIn("不要要求用户逐 Task 回复启动", handoff)
            self.assertFalse((root / "children" / "t-python-controller" / "development-handoff.md").exists())
            self.assertFalse((root / "children" / "t-python-worker" / "development-handoff.md").exists())

            child_progress = (
                root / "children" / "t-python-controller" / "progress.md"
            ).read_text(encoding="utf-8")
            self.assertIn("无需人工逐 Task 启动", child_progress)

            refreshed = refresh_work_item_projections(root=temporary)
            root_artifacts = next(
                item["humanArtifacts"]
                for item in refreshed["workItems"]
                if item["id"] == prepared["rootId"]
            )
            self.assertEqual(
                root_artifacts["requirementHandoff"],
                ".hierarchical-delivery-governance/work-items/c-python-runtime/requirement-handoff.md",
            )

            idempotent = freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="manual",
                confirmed=True,
            )
            self.assertTrue(idempotent["idempotent"])
            self.assertEqual(idempotent["handoffPrompt"], handoff)

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
                    development_mode="active",
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

    def test_root_progress_tracks_the_development_plan_tree_after_each_state_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=two_task_capability_hierarchy(),
                host_runtime="codex",
            )
            root = Path(prepared["artifactDir"])
            plan = (root / "development-plan.md").read_text(encoding="utf-8")
            prepared_progress = (root / "progress.md").read_text(encoding="utf-8")
            child = root / "children" / "t-python-controller"
            child_plan = (child / "development-plan.md").read_text(encoding="utf-8")
            child_progress = (child / "progress.md").read_text(encoding="utf-8")

            self.assertIn('<a id="work-item-c-python-runtime"></a>', plan)
            self.assertIn('<a id="work-item-t-python-controller"></a>', plan)
            self.assertIn("# 开发方案：Python controller", child_plan)
            self.assertIn("- 开发方案：[development-plan.md](development-plan.md)", child_progress)
            self.assertIn("- 当前执行：未认领", child_progress)
            self.assertIn("- 当前执行：不适用", prepared_progress)
            self.assertNotIn("- 认领：", prepared_progress)
            self.assertIn("## 整树进度明细", prepared_progress)
            self.assertIn(
                "| 层级工作项 | 阶段 | 状态 | 门禁 | 当前执行 | 节点文件 | 阶段产物 |",
                prepared_progress,
            )
            self.assertIn("[能力 `c-python-runtime`](development-plan.md#work-item-c-python-runtime)", prepared_progress)
            self.assertIn("| ├─ [任务 `t-python-controller`](development-plan.md#work-item-t-python-controller) |", prepared_progress)
            self.assertIn("| └─ [任务 `t-python-worker`](development-plan.md#work-item-t-python-worker) |", prepared_progress)
            self.assertIn(
                "[方案](children/t-python-controller/development-plan.md)、"
                "[进度](children/t-python-controller/progress.md)",
                prepared_progress,
            )
            self.assertNotIn("— 阶段", prepared_progress)
            refreshed = refresh_work_item_projections(root=temporary)
            task_artifacts = next(
                item["humanArtifacts"]
                for item in refreshed["workItems"]
                if item["id"] == "t-python-controller"
            )
            self.assertEqual(
                task_artifacts["developmentPlan"],
                ".hierarchical-delivery-governance/work-items/c-python-runtime/children/"
                "t-python-controller/development-plan.md",
            )
            self.assertEqual(
                task_artifacts["hierarchyDevelopmentPlan"],
                ".hierarchical-delivery-governance/work-items/c-python-runtime/development-plan.md",
            )
            self.assertIn("| 等待开发方案确认 | 等待开发方案评审 | 未运行 |", prepared_progress)
            self.assertRegex(prepared_progress, r"c-python-runtime.*\| 未运行 \| 不适用 \|")
            self.assertEqual(prepared_progress.count("| 未运行 | 未认领 |"), 2)

            freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )
            frozen_progress = (root / "progress.md").read_text(encoding="utf-8")
            self.assertEqual(frozen_progress.count("| 开发方案已冻结 | 已冻结 | 未运行 |"), 3)
            self.assertEqual(frozen_progress.count("| 未运行 | 未认领 |"), 2)
            self.assertRegex(frozen_progress, r"c-python-runtime.*\| 未运行 \| 不适用 \|")

            dispatch_task(
                root=temporary,
                item_id="t-python-controller",
                owner="developer",
                operation_id="op-progress",
            )
            claimed_progress = (root / "progress.md").read_text(encoding="utf-8")
            claimed_child_progress = (child / "progress.md").read_text(encoding="utf-8")
            self.assertIn("- 当前执行：developer / op-progress", claimed_child_progress)
            self.assertRegex(
                claimed_progress,
                r"t-python-controller.*\| 开发方案已冻结 \| 开发中 \| 未运行 \| developer / op-progress \|",
            )
            self.assertRegex(
                claimed_progress,
                r"t-python-worker.*\| 开发方案已冻结 \| 已冻结 \| 未运行 \| 未认领 \|",
            )

            record_task_result(
                root=temporary,
                item_id="t-python-controller",
                operation_id="op-progress",
                status="IMPLEMENTED",
                evidence={"path": "missing-progress-evidence.json", "sha256": "0" * 64},
            )
            implemented_progress = (root / "progress.md").read_text(encoding="utf-8")
            implemented_child_progress = (child / "progress.md").read_text(encoding="utf-8")
            self.assertIn("- 当前执行：已释放", implemented_child_progress)
            self.assertRegex(
                implemented_progress,
                r"t-python-controller.*\| 开发方案已冻结 \| 等待门禁验收 \| 未运行 \| 已释放 \|",
            )
            self.assertIn(
                "[开发复核](children/t-python-controller/development-review.md)",
                implemented_progress,
            )

            record_work_item_gate(
                root=temporary,
                item_id="t-python-controller",
                status="PASS",
                evidence={"path": "missing-progress-gate.json", "sha256": "1" * 64},
            )
            verified_progress = (root / "progress.md").read_text(encoding="utf-8")
            self.assertRegex(
                verified_progress,
                r"t-python-controller.*\| 开发方案已冻结 \| 门禁已通过 \| 通过 \| 已释放 \|",
            )
            self.assertIn(
                "[验收报告](children/t-python-controller/acceptance-report.md)",
                verified_progress,
            )

    def test_delivery_progress_preserves_the_three_level_plan_hierarchy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=delivery_hierarchy(),
                host_runtime="codex",
            )
            root = Path(prepared["artifactDir"])
            plan = (root / "development-plan.md").read_text(encoding="utf-8")
            progress = (root / "progress.md").read_text(encoding="utf-8")

            ids = ("d-python-governance", "c-python-runtime", "t-python-controller")
            self.assertEqual(
                sorted((plan.index(f'work-item-{item_id}'), item_id) for item_id in ids),
                [(plan.index(f"work-item-{item_id}"), item_id) for item_id in ids],
            )
            self.assertLess(progress.index(ids[0]), progress.index(ids[1]))
            self.assertLess(progress.index(ids[1]), progress.index(ids[2]))
            self.assertIn("\n| └─ [能力 `c-python-runtime`]", progress)
            self.assertIn("\n| 　└─ [任务 `t-python-controller`]", progress)
            self.assertIn("\n└─ 能力 [`c-python-runtime`]", plan)
            self.assertIn("\n   └─ 任务 [`t-python-controller`]", plan)


if __name__ == "__main__":
    unittest.main()
