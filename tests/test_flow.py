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
            self.assertIn("优先使用已连接的 Plugin MCP", frozen["handoffPrompt"])
            self.assertIn("恢复入口是 `graph_frontier`，不是 `task_context`", frozen["handoffPrompt"])
            self.assertIn("直接消费结构化 tool result", frozen["handoffPrompt"])
            self.assertIn("只有 MCP 不可用时", frozen["handoffPrompt"])
            self.assertIn("保留控制器 stderr", frozen["handoffPrompt"])
            self.assertIn("不得固化用户目录、Skill 安装位置或操作系统路径", frozen["handoffPrompt"])
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
                "面向人的状态报告必须把控制器 UTC 时间转换为当前运行环境的本机时区",
                frozen["handoffPrompt"],
            )
            self.assertIn("显式标注 UTC 偏移", frozen["handoffPrompt"])
            self.assertIn("Claude Code 无人值守前置条件", frozen["handoffPrompt"])
            self.assertIn("MCP 控制器不再触发 `hdg.py` Process 授权", frozen["handoffPrompt"])
            self.assertIn("`acceptEdits` 仍不足以自动批准测试和构建命令", frozen["handoffPrompt"])
            machine_paths = (
                "C:\\Users\\", "/Users/", "/home/", "/tmp/", ".claude/skills", ".codex/skills",
            )
            for machine_path in machine_paths:
                self.assertNotIn(machine_path, frozen["handoffPrompt"])
                self.assertNotIn(machine_path, frozen["handoffCommand"])
            self.assertEqual(
                frozen["handoffCommand"],
                "继续执行治理需求 t-python-controller。使用当前 layered-delivery Skill 和已连接的 Plugin MCP，"
                "从当前项目的治理数据库恢复已冻结方案，按 Graph 自动调度计划接管整棵需求树并完成开发、测试和门禁；"
                "以 MCP graph_frontier 为恢复入口并直接消费结构化 tool result，不固化用户目录、Skill 安装位置或操作系统路径，"
                "只有 MCP 不可用时才从 Skill 元数据解析 CLI fallback；不使用临时 JSON 中转，也不要重新准备、冻结需求或逐 Task 请求人工启动；"
                "面向人的状态报告须把控制器 UTC 时间转换为当前运行环境的本机时区并显式标注 UTC 偏移，机器字段保持不变；"
                "若接收宿主是 Claude Code，必须在 dispatch_task 认领前由用户级设置、模式选择器或启动参数启用 auto；"
                "MCP 控制器不再需要 hdg.py Process 授权，但 acceptEdits 仍不足以自动批准测试和构建命令；"
                "逐项执行 frontier action 中冻结的 requiredSkills，并在 result、gate 和独立审查 evidence 中记录具体使用情况；"
                "会话不得自行修改权限配置或启用 bypassPermissions。",
            )
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
            self.assertIn("开发建议：manual", progress)
            self.assertIn("需求级交接：[requirement-handoff.md](requirement-handoff.md)", progress)
            self.assertIn("一次性交接整棵需求树", progress)
            self.assertNotIn("按需生成可复制的独立开发 handoff", progress)


if __name__ == "__main__":
    unittest.main()
