from __future__ import annotations

import json
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
            self.assertIsNone(result["hostAutomation"])
            package = Path(result["artifactDir"])
            self.assertTrue((package / "development-plan.md").is_file())
            self.assertTrue(
                Path(temporary, ".layered-delivery", "governance.sqlite3").is_file()
            )
            self.assertEqual(list(Path(temporary, ".layered-delivery").rglob("*.json")), [])

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
                ".layered-delivery/work-items/t-python-controller/requirement-handoff.md",
            )
            self.assertIn("一次接管整棵需求树", frozen["handoffPrompt"])
            self.assertIn("不要要求用户逐 Task 回复启动", frozen["handoffPrompt"])
            self.assertIn(
                "在开始开发、认领 Task 或恢复 frozen graph 前",
                frozen["handoffPrompt"],
            )
            self.assertIn("恢复入口是 `graph_frontier`，不是 `task_context`", frozen["handoffPrompt"])
            self.assertIn("直接消费结构化 tool result", frozen["handoffPrompt"])
            self.assertIn(
                "MCP 未安装、未注册或未连接时立即阻断",
                frozen["handoffPrompt"],
            )
            self.assertIn(
                "`PLUGIN_MCP_DISCONNECTED`",
                frozen["handoffPrompt"],
            )
            self.assertNotIn("CLI fallback", frozen["handoffPrompt"])
            self.assertIn(
                "不得固化用户目录、Plugin 安装位置或操作系统路径",
                frozen["handoffPrompt"],
            )
            self.assertIn("不得创建临时 JSON", frozen["handoffPrompt"])
            self.assertIn(
                "硬过期时消费 frontier 的 `ADVANCE_GRAPH`",
                frozen["handoffPrompt"],
            )
            self.assertIn("这是自动恢复，不请求人工重置", frozen["handoffPrompt"])
            self.assertIn(
                "代码和测试完成后必须先提交 Task 结果",
                frozen["handoffPrompt"],
            )
            self.assertIn(
                "面向人的状态报告默认使用简体中文",
                frozen["handoffPrompt"],
            )
            self.assertIn("UTC+08:00", frozen["handoffPrompt"])
            self.assertIn("Claude Code 无人值守前置条件", frozen["handoffPrompt"])
            self.assertIn("不得启动 CLI 控制器", frozen["handoffPrompt"])
            self.assertIn("`acceptEdits` 仍不足以自动批准测试和构建命令", frozen["handoffPrompt"])
            machine_paths = (
                "C:\\Users\\", "/Users/", "/home/", "/tmp/", ".claude/skills", ".codex/skills",
            )
            for machine_path in machine_paths:
                self.assertNotIn(machine_path, frozen["handoffPrompt"])
                self.assertNotIn(machine_path, frozen["handoffCommand"])
            self.assertEqual(
                frozen["handoffCommand"],
                "继续执行治理需求 t-python-controller。用 layered-delivery Skill 从 MCP graph_frontier "
                "恢复已冻结运行，完整执行 Graph 计划，自动完成开发、测试、门禁和审查；"
                "勿重新准备、冻结或逐 Task 启动，停在最终确认。MCP 不可用时立即停止且不写治理状态；"
                "仅遇权限、契约或不可恢复阻断时返回用户。",
            )
            self.assertLess(
                len(frozen["handoffCommand"]) - len(frozen["rootId"]),
                170,
            )
            for implementation_detail in (
                "hdg.py Process",
                "acceptEdits",
                "bypassPermissions",
                "record_skill_activation",
                "UTC 时间",
            ):
                self.assertNotIn(implementation_detail, frozen["handoffCommand"])
            claude_handoff = frozen["claudeCodeAutoHandoff"]
            self.assertIsNone(frozen["hostAutomation"])
            self.assertIn("claudeCodeAutoHandoff", frozen["nextAction"])
            self.assertEqual(claude_handoff["permissionMode"], "auto")
            self.assertEqual(
                claude_handoff["interactiveArgv"],
                ["claude", "--permission-mode", "auto", frozen["handoffCommand"]],
            )
            self.assertEqual(
                claude_handoff["unattendedArgv"],
                ["claude", "-p", "--permission-mode", "auto", frozen["handoffCommand"]],
            )
            quoted_prompt = json.dumps(frozen["handoffCommand"], ensure_ascii=False)
            self.assertEqual(
                claude_handoff["interactiveCommand"],
                f"claude --permission-mode auto {quoted_prompt}",
            )
            self.assertEqual(
                claude_handoff["unattendedCommand"],
                f"claude -p --permission-mode auto {quoted_prompt}",
            )
            self.assertIn("模式选择器", claude_handoff["desktopInstruction"])
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
            self.assertIn("开发建议：手动", progress)
            self.assertIn("需求级交接：[requirement-handoff.md](requirement-handoff.md)", progress)
            self.assertIn("一次性交接整棵需求树", progress)
            self.assertNotIn("按需生成可复制的独立开发 handoff", progress)


if __name__ == "__main__":
    unittest.main()
