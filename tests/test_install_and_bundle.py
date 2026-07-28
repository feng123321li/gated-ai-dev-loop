from __future__ import annotations

import ast
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.build_skill import (
    PLUGIN_SKILL,
    REPOSITORY_ROOT,
    SOURCE_PACKAGE,
    TARGET_ENTRY,
    TARGET_PACKAGE,
    build_skill,
    main as build_main,
)
from hdg.mcp_tools import tool_definitions

TARGET_MCP_ENTRY = TARGET_ENTRY.with_name("hdg_mcp.py")
SENSITIVE_MCP_TOOLS = {
    "freeze_hierarchy",
    "rebuild_graph_run",
    "cancel_graph_run",
    "record_human_review_acceptance",
    "record_user_confirmation",
}
CLAUDE_PLUGIN_MCP_PREFIX = (
    "mcp__plugin_layered-delivery_layered-delivery__"
)


def file_map(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)).replace("\\", "/"): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


class InstallAndBundleTests(unittest.TestCase):
    def test_runtime_control_directory_is_git_ignored(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        gitignore = (repository_root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".layered-delivery/", gitignore.splitlines())

    def test_canonical_skill_name_is_layered_delivery(self) -> None:
        skill_root = TARGET_PACKAGE.parent.parent
        self.assertEqual(skill_root.name, "layered-delivery")
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        agent_metadata = (skill_root / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("name: layered-delivery", skill)
        self.assertIn("当工作区存在 `.layered-delivery/` 时接管现有 SQLite/Graph 运行", skill)
        frontmatter = skill.split("---", 2)[1]
        self.assertNotIn(f"{CLAUDE_PLUGIN_MCP_PREFIX}*", frontmatter)
        for tool in tool_definitions():
            permission_name = f"{CLAUDE_PLUGIN_MCP_PREFIX}{tool['name']}"
            with self.subTest(tool=tool["name"]):
                if tool["name"] in SENSITIVE_MCP_TOOLS:
                    self.assertNotIn(permission_name, frontmatter)
                else:
                    self.assertIn(permission_name, frontmatter)
        self.assertIn("$layered-delivery", agent_metadata)
        self.assertIn("allow_implicit_invocation: true", agent_metadata)

    def test_skill_entry_stays_lean_and_routes_details_on_demand(self) -> None:
        skill_root = TARGET_PACKAGE.parent.parent
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        self.assertLess(len(skill), 7000)
        self.assertIn("首次只读取本文件", skill)
        self.assertIn("不得预读全部 references", skill)
        self.assertIn("按动作读取", skill)
        self.assertIn("evidenceContractRef", skill)
        self.assertIn("不得读取控制器源码或 memory 文件反推格式", skill)
        self.assertIn("`ADVANCE_GRAPH` 是租约硬过期后的确定性自动恢复动作", skill)
        self.assertIn("不得以“代码和测试已完成”代替 Graph 收尾", skill)

    def test_manual_contract_details_live_in_routed_references(self) -> None:
        skill_root = TARGET_PACKAGE.parent.parent
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        workflow = (skill_root / "references" / "workflow.md").read_text(encoding="utf-8")
        development_plan = (skill_root / "references" / "development-plan.md").read_text(encoding="utf-8")
        acceptance = (skill_root / "references" / "acceptance.md").read_text(encoding="utf-8")
        tracking = (skill_root / "references" / "tracking.md").read_text(encoding="utf-8")
        claude_automation = (skill_root / "references" / "claude-automation.md").read_text(encoding="utf-8")
        development = (skill_root / "references" / "development.md").read_text(encoding="utf-8")
        self.assertIn("必须同时展示 `active` 和 `manual` 两种开发方式", skill)
        self.assertIn("每个 requirement 都必须有独立 acceptance", skill)
        self.assertIn("`requiredSkills`（可空）", skill)
        self.assertIn("不能只用一个跨需求 acceptance", development_plan)
        self.assertIn("同时展示 requirement 文本、R/A 映射和 expectedResult", acceptance)
        self.assertIn("可以使用 `handoffCommand`", workflow)
        self.assertIn("不要求逐字一致", workflow)
        self.assertIn("不能只给文件链接", workflow)
        self.assertIn(
            "所有面向人的状态报告同样必须把 SQLite 和控制器 JSON 中的 UTC 时间转换为当前运行环境的本机时区",
            tracking,
        )
        self.assertIn("claude-automation.md", skill)
        self.assertIn("不能由聊天提示、Skill 或仓库内容自行切换", claude_automation)
        self.assertIn("`acceptEdits` 只自动接受文件编辑", claude_automation)
        self.assertIn("claudeCodeAutoHandoff", claude_automation)
        self.assertIn("claude -p --permission-mode auto", claude_automation)
        self.assertIn("项目级 `.claude/settings.json`", claude_automation)
        self.assertIn("不默认使用 `bypassPermissions`", claude_automation)
        self.assertIn("逐项预批准 30 个中段自治工具", claude_automation)
        self.assertIn("Claude Code 至少使用 2.1.199", claude_automation)
        self.assertIn("finalize 不执行业务动作", claude_automation)
        self.assertIn("不使用 `__*` 通配符", claude_automation)
        self.assertIn("优先通过已连接的 Plugin MCP 调用 `graph_frontier`", development)
        self.assertIn("以结构化参数调用 `evidence_contract`", acceptance)
        self.assertIn("实际开发 Skill 调用", acceptance)
        self.assertIn("全部后代 Task", acceptance)
        self.assertIn("Skill 使用审计", acceptance)
        self.assertIn("skillUsage", acceptance)
        self.assertIn(
            "只有 baseline 没有 `FINAL_REVIEW` required Skill",
            acceptance,
        )
        self.assertIn(
            "最终验收阶段才可由人触发 `record_human_review_acceptance`",
            acceptance,
        )
        self.assertIn("宿主才可调用 `record_user_confirmation`", acceptance)

    def test_skill_contract_keeps_controller_invocation_host_portable(self) -> None:
        skill_root = TARGET_PACKAGE.parent.parent
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        transport = (skill_root / "references" / "stdin-transport.md").read_text(encoding="utf-8")
        self.assertIn("从当前 Skill 元数据解析 `<skill-root>`", skill)
        self.assertIn("不得固化用户目录、Skill 安装位置或操作系统路径", skill)
        self.assertIn("宿主无关调用契约", transport)
        self.assertIn("必须直接消费 stdout", transport)
        self.assertIn("必须保留 stderr", transport)
        self.assertIn("不得使用临时 JSON 中转只读查询结果", transport)
        self.assertIn("恢复入口是 `graph-frontier`，不是 `task-context`", transport)
        self.assertIn("分块解决的是单条 MCP 消息上限", transport)
        self.assertIn('"generationId":"..."', transport)

    def test_build_is_reproducible_and_bundle_matches_source(self) -> None:
        build_skill()
        self.assertEqual(file_map(SOURCE_PACKAGE), file_map(TARGET_PACKAGE))
        self.assertTrue(TARGET_ENTRY.is_file())
        self.assertIn("from hdg.cli import main", TARGET_ENTRY.read_text(encoding="utf-8"))
        self.assertTrue(TARGET_MCP_ENTRY.is_file())
        self.assertIn(
            "from hdg.mcp_server import main",
            TARGET_MCP_ENTRY.read_text(encoding="utf-8"),
        )
        self.assertEqual(file_map(TARGET_PACKAGE.parent.parent), file_map(PLUGIN_SKILL))

    def test_build_cli_reports_success_and_failure(self) -> None:
        standard_output = io.StringIO()
        with redirect_stdout(standard_output):
            self.assertEqual(build_main(), 0)
        self.assertIn("Built dual-host plugin Skill payload", standard_output.getvalue())

        standard_error = io.StringIO()
        with patch("scripts.build_skill.build_skill", side_effect=RuntimeError("broken")):
            with redirect_stderr(standard_error):
                self.assertEqual(build_main(), 1)
        self.assertIn("Build failed: broken", standard_error.getvalue())

    def test_dual_host_plugin_manifests_share_identity_and_version(self) -> None:
        plugin_root = PLUGIN_SKILL.parent.parent
        codex_manifest = json.loads(
            (plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        claude_manifest = json.loads(
            (plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        project_version = next(
            line.split("=", 1)[1].strip().strip('"')
            for line in (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
            if line.startswith("version = ")
        )

        self.assertEqual(codex_manifest["name"], "layered-delivery")
        self.assertEqual(claude_manifest["name"], codex_manifest["name"])
        self.assertEqual(claude_manifest["version"], codex_manifest["version"])
        self.assertEqual(codex_manifest["version"], project_version)
        self.assertEqual(
            codex_manifest["description"],
            "面向 AI 辅助开发、可人工评审的分层交付治理插件",
        )
        self.assertEqual(claude_manifest["description"], codex_manifest["description"])
        self.assertEqual(
            codex_manifest["interface"]["shortDescription"],
            "治理可评审、可恢复的 AI 辅助软件交付",
        )
        self.assertEqual(codex_manifest["skills"], "./skills/")
        self.assertIsInstance(codex_manifest.get("mcpServers"), dict)

    def test_claude_plugin_auto_discovers_portable_mcp_server(self) -> None:
        plugin_root = PLUGIN_SKILL.parent.parent
        mcp_path = plugin_root / ".mcp.json"
        self.assertTrue(mcp_path.is_file(), f"missing Claude MCP config: {mcp_path}")
        mcp_config = json.loads(mcp_path.read_text(encoding="utf-8"))
        self.assertEqual(set(mcp_config), {"mcpServers"})
        servers = mcp_config["mcpServers"]
        self.assertIsInstance(servers, dict)
        self.assertEqual(len(servers), 1)
        server = next(iter(servers.values()))
        self.assertEqual(server["command"], "python")
        self.assertIn(
            "${CLAUDE_PLUGIN_ROOT}/skills/layered-delivery/scripts/hdg_mcp.py",
            server["args"],
        )
        self.assertEqual(
            server["env"]["HDG_PROJECT_ROOT"],
            "${CLAUDE_PROJECT_DIR}",
        )

    def test_codex_plugin_declares_an_inline_compatible_mcp_server_map(self) -> None:
        plugin_root = PLUGIN_SKILL.parent.parent
        manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        servers = manifest["mcpServers"]
        self.assertIsInstance(servers, dict)
        self.assertEqual(len(servers), 1)
        server = next(iter(servers.values()))
        self.assertEqual(server["command"], "python")
        self.assertEqual(
            server["args"],
            [
                "-X",
                "utf8",
                "skills/layered-delivery/scripts/hdg_mcp.py",
                "--project-root-from-meta",
            ],
        )
        self.assertEqual(server["cwd"], ".")
        self.assertEqual(server["default_tools_approval_mode"], "approve")
        self.assertIs(server["supports_parallel_tool_calls"], False)
        self.assertEqual(
            set(server["tools"]),
            SENSITIVE_MCP_TOOLS,
        )
        for tool_config in server["tools"].values():
            self.assertEqual(tool_config, {"approval_mode": "prompt"})
        self.assertNotIn(
            "${CLAUDE_",
            json.dumps(servers, ensure_ascii=False),
        )
        self.assertFalse((plugin_root / ".codex-mcp.json").exists())

    def test_python_distribution_exposes_cli_and_mcp_entrypoints_without_dependencies(self) -> None:
        pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('dependencies = []', pyproject)
        self.assertIn('hdg = "hdg.cli:main"', pyproject)
        self.assertIn('hdg-mcp = "hdg.mcp_server:main"', pyproject)

    def test_repository_is_plugin_source_not_a_marketplace(self) -> None:
        self.assertFalse(
            (REPOSITORY_ROOT / ".agents" / "plugins" / "marketplace.json").exists()
        )
        self.assertFalse(
            (REPOSITORY_ROOT / ".claude-plugin" / "marketplace.json").exists()
        )
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "Marketplace\n只维护指向 `plugins/layered-delivery` 的 Git 版本映射",
            readme,
        )

    def test_readme_documents_mcp_first_plugin_and_cli_fallback_installation(self) -> None:
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLess(readme.index("## 安装"), readme.index("## 核心契约"))
        skill_install = (
            "npx skills add feng123321li/layered-delivery --skill layered-delivery "
            "--global --agent codex --agent claude-code --yes"
        )
        self.assertIn("MCP-first", readme)
        self.assertIn("CLI fallback", readme)
        mcp_first = readme.index("MCP-first")
        cli_fallback = readme.index("CLI fallback")
        skill_install_location = readme.index(skill_install)
        self.assertLess(mcp_first, cli_fallback)
        self.assertLess(cli_fallback, skill_install_location)
        mcp_installation = readme[mcp_first:cli_fallback]
        self.assertIn("插件", mcp_installation)
        self.assertIn("Codex", mcp_installation)
        self.assertIn("Claude", mcp_installation)
        for retired in (
            "codex plugin marketplace add feng123321li/layered-delivery",
            "claude plugin marketplace add feng123321li/layered-delivery",
            "git@git.i-sanger.com",
            "majorbio-skills",
            "git@git.i-sanger.com:ai/skill/layered-delivery.git",
            "https://git.i-sanger.com/ai/skill/layered-delivery.git",
        ):
            self.assertNotIn(retired, readme)

    def test_repository_changelog_tracks_the_current_version(self) -> None:
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (REPOSITORY_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        project_version = next(
            line.split("=", 1)[1].strip().strip('"')
            for line in (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
            if line.startswith("version = ")
        )

        self.assertIn("[版本更新记录](CHANGELOG.md)", readme)
        self.assertIn(f"## {project_version} — ", changelog)
        self.assertIn("## 0.1.0 — ", changelog)
        self.assertFalse((TARGET_PACKAGE.parent.parent / "CHANGELOG.md").exists())

    def test_legacy_python_installer_is_retired(self) -> None:
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertFalse((REPOSITORY_ROOT / "scripts" / "install_skill.py").exists())
        self.assertNotIn("python scripts/install_skill.py", readme)

    def test_runtime_imports_only_standard_library_or_local_modules(self) -> None:
        allowed_roots = {
            "hdg", "__future__", "abc", "argparse", "ast", "base64", "collections", "contextlib",
            "contextvars",
            "copy", "dataclasses", "datetime", "enum", "errno", "fcntl", "functools", "hashlib",
            "io", "json", "math", "msvcrt", "os", "pathlib", "posixpath", "re", "secrets", "shutil", "sqlite3",
            "stat", "sys", "tempfile", "threading", "time", "typing", "unittest", "urllib", "uuid",
        }
        repository_root = Path(__file__).resolve().parents[1]
        runtime_paths = [
            *SOURCE_PACKAGE.glob("*.py"),
            repository_root / "bin" / "hdg.py",
            repository_root / "scripts" / "build_skill.py",
            TARGET_MCP_ENTRY,
        ]
        for path in runtime_paths:
            self.assertTrue(path.is_file(), f"missing runtime path: {path}")
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots = [node.module.split(".")[0]]
                else:
                    continue
                self.assertTrue(set(roots) <= allowed_roots, f"non-stdlib import in {path}: {roots}")


if __name__ == "__main__":
    unittest.main()
