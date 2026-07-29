from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import tempfile
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
EXPECTED_SENSITIVE_MCP_TOOLS = {
    "rebuild_graph_run",
    "cancel_graph_run",
    "record_human_review_acceptance",
    "record_user_confirmation",
}
SENSITIVE_MCP_TOOLS = {
    tool["name"]
    for tool in tool_definitions()
    if tool.get("_meta", {}).get("anthropic/requiresUserInteraction") is True
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
        self.assertIn(f"{CLAUDE_PLUGIN_MCP_PREFIX}*", frontmatter)
        for tool in tool_definitions():
            permission_name = f"{CLAUDE_PLUGIN_MCP_PREFIX}{tool['name']}"
            with self.subTest(tool=tool["name"]):
                self.assertNotIn(permission_name, frontmatter)
        self.assertIn("$layered-delivery", agent_metadata)
        self.assertIn("allow_implicit_invocation: true", agent_metadata)

    def test_skill_entry_stays_lean_and_routes_details_on_demand(self) -> None:
        skill_root = TARGET_PACKAGE.parent.parent
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        self.assertLess(len(skill), 4000)
        self.assertIn("首次只读取本文件", skill)
        self.assertIn("不得预读全部 references", skill)
        self.assertIn("按需读取", skill)
        self.assertIn("execution-quickstart.md", skill)
        self.assertIn("planning-quickstart.md", skill)
        self.assertIn(
            "不得编辑业务代码、启动 Shell/CLI 控制器、直接写 SQLite 或从源码/Markdown 猜状态",
            skill,
        )
        markdown_references = sorted(
            path.name
            for path in (skill_root / "references").glob("*.md")
        )
        self.assertEqual(
            markdown_references,
            [
                "acceptance.md",
                "execution-quickstart.md",
                "mcp-transport.md",
                "planning-quickstart.md",
            ],
        )
        execution_quickstart = (
            skill_root / "references" / "execution-quickstart.md"
        ).read_text(encoding="utf-8")
        self.assertIn("`ADVANCE_GRAPH`", execution_quickstart)
        self.assertIn(
            "不得用聊天总结代替 Graph 收尾",
            skill,
        )

    def test_manual_contract_details_live_in_routed_references(self) -> None:
        skill_root = TARGET_PACKAGE.parent.parent
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        acceptance = (skill_root / "references" / "acceptance.md").read_text(encoding="utf-8")
        planning_quickstart = (
            skill_root / "references" / "planning-quickstart.md"
        ).read_text(encoding="utf-8")
        execution_quickstart = (
            skill_root / "references" / "execution-quickstart.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "同时展示 `active` / `manual` 两个选项",
            planning_quickstart,
        )
        self.assertIn("每个 requirement 都有独立、可观察的 acceptance", skill)
        self.assertIn(
            "不预读、不递归展开、不自动加入 `GATE`",
            skill,
        )
        self.assertIn("不存在或疑似拼错", skill)
        self.assertIn("宿主级 `root` 与项目级 `project` catalog", skill)
        self.assertIn("优先展示人类友好的 `userPrompt`", skill)
        self.assertIn(
            '登记 `requiredSkills=[{"name":"...","stages":["DEVELOPMENT"],"purpose":"..."}]`',
            planning_quickstart,
        )
        self.assertIn(
            "不预读或分析 Skill 内容，不递归其内部 Skill",
            planning_quickstart,
        )
        self.assertIn(
            'available_skills={"root":[...],"project":[...]}',
            planning_quickstart,
        )
        self.assertIn(
            "按最小可用模块适当放宽",
            planning_quickstart,
        )
        self.assertIn(
            "`developmentPlan.fileChanges`",
            planning_quickstart,
        )
        self.assertIn("hierarchy_contract", planning_quickstart)
        self.assertIn("compactTask", planning_quickstart)
        self.assertIn("generatedFileRoots", planning_quickstart)
        self.assertIn("evidenceDelta", execution_quickstart)
        self.assertIn("nextFrontier", execution_quickstart)
        self.assertIn(
            "`task_context` 只作诊断预览，不能授权开工",
            execution_quickstart,
        )
        self.assertIn("同一回复就是冻结确认", planning_quickstart)
        self.assertIn(
            "当前宿主原生入口分别调用",
            execution_quickstart,
        )
        self.assertIn("`INVOKED + PASS`", acceptance)
        self.assertIn("USER_ACCEPTANCE", acceptance)
        self.assertIn("按此增量处理，只刷新受影响需求树", acceptance)
        self.assertIn(
            "只有没有 FINAL_REVIEW Skill 且无法隔离时",
            acceptance,
        )
        self.assertIn("宿主才可调用 `record_user_confirmation`", acceptance)

    def test_skill_contract_keeps_controller_invocation_host_portable(self) -> None:
        skill_root = TARGET_PACKAGE.parent.parent
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        transport = (skill_root / "references" / "mcp-transport.md").read_text(encoding="utf-8")
        self.assertIn("MCP 未安装、未连接或工具未注册时", skill)
        self.assertNotIn("CLI fallback", skill)
        self.assertIn("不得启动 `hdg.py`", transport)
        self.assertIn("暂存解决消息上限，不解决上下文成本", transport)
        self.assertIn("保存返回的 `generationId`", transport)
        self.assertIn(
            '{"payloadRef":{"uploadId":"...","generationId":"...","sha256":"...","sizeBytes":123}}',
            transport,
        )
        self.assertFalse(
            (skill_root / "references" / "stdin-transport.md").exists()
        )

    def test_build_is_reproducible_and_bundle_matches_source(self) -> None:
        build_skill()
        expected_package = file_map(SOURCE_PACKAGE)
        expected_package.pop("cli.py", None)
        expected_package.pop("__main__.py", None)
        self.assertEqual(expected_package, file_map(TARGET_PACKAGE))
        self.assertFalse(TARGET_ENTRY.exists())
        self.assertFalse((TARGET_PACKAGE / "cli.py").exists())
        self.assertFalse((TARGET_PACKAGE / "__main__.py").exists())
        self.assertTrue(TARGET_MCP_ENTRY.is_file())
        self.assertIn(
            "from hdg.mcp_server import main",
            TARGET_MCP_ENTRY.read_text(encoding="utf-8"),
        )
        self.assertEqual(file_map(TARGET_PACKAGE.parent.parent), file_map(PLUGIN_SKILL))

    def test_plugin_build_reports_success_and_failure(self) -> None:
        standard_output = io.StringIO()
        with redirect_stdout(standard_output):
            self.assertEqual(build_main(), 0)
        self.assertIn("Built dual-host Plugin payload", standard_output.getvalue())

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

    def test_claude_plugin_hooks_prompt_for_sensitive_tools_and_fail_closed(self) -> None:
        plugin_root = PLUGIN_SKILL.parent.parent
        hook_config_path = plugin_root / "hooks" / "hooks.json"
        hook_script = plugin_root / "hooks" / "require_sensitive_tool_approval.py"
        self.assertTrue(hook_config_path.is_file())
        self.assertTrue(hook_script.is_file())
        self.assertEqual(
            SENSITIVE_MCP_TOOLS,
            EXPECTED_SENSITIVE_MCP_TOOLS,
        )

        hook_config = json.loads(hook_config_path.read_text(encoding="utf-8"))
        hook_groups = hook_config["hooks"]["PreToolUse"]
        self.assertEqual(
            {group["matcher"] for group in hook_groups},
            {
                f"{CLAUDE_PLUGIN_MCP_PREFIX}{tool_name}"
                for tool_name in SENSITIVE_MCP_TOOLS
            },
        )
        self.assertNotIn(
            f"{CLAUDE_PLUGIN_MCP_PREFIX}freeze_hierarchy",
            {group["matcher"] for group in hook_groups},
        )
        for group in hook_groups:
            self.assertEqual(len(group["hooks"]), 1)
            handler = group["hooks"][0]
            self.assertEqual(handler["type"], "command")
            self.assertEqual(handler["command"], "python")
            self.assertEqual(handler["timeout"], 10)
            self.assertEqual(
                handler["args"],
                [
                    "-X",
                    "utf8",
                    "${CLAUDE_PLUGIN_ROOT}/hooks/require_sensitive_tool_approval.py",
                ],
            )

        for tool_name in SENSITIVE_MCP_TOOLS:
            with self.subTest(tool=tool_name):
                completed = subprocess.run(
                    [sys.executable, "-X", "utf8", str(hook_script)],
                    input=json.dumps(
                        {
                            "hook_event_name": "PreToolUse",
                            "tool_name": f"{CLAUDE_PLUGIN_MCP_PREFIX}{tool_name}",
                            "tool_input": {},
                        }
                    ),
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                output = json.loads(completed.stdout)
                decision = output["hookSpecificOutput"]
                self.assertEqual(decision["hookEventName"], "PreToolUse")
                self.assertEqual(decision["permissionDecision"], "ask")
                self.assertTrue(decision["permissionDecisionReason"])

        for invalid_input in (
            "not-json",
            json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": f"{CLAUDE_PLUGIN_MCP_PREFIX}workspace_status",
                }
            ),
            json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": [],
                }
            ),
            json.dumps(
                {
                    "hook_event_name": [],
                    "tool_name": (
                        f"{CLAUDE_PLUGIN_MCP_PREFIX}freeze_hierarchy"
                    ),
                }
            ),
        ):
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", str(hook_script)],
                input=invalid_input,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertIn("blocked", completed.stderr)

    def test_bundled_mcp_entry_completes_real_dual_host_stdio_handshakes(self) -> None:
        plugin_root = PLUGIN_SKILL.parent.parent
        entry = plugin_root / "skills" / "layered-delivery" / "scripts" / "hdg_mcp.py"
        self.assertTrue(entry.is_file())

        def run_handshake(
            *,
            client_name: str,
            client_version: str,
            command_arguments: list[str],
            environment: dict[str, str],
            request_meta: dict[str, object] | None = None,
        ) -> list[dict[str, object]]:
            requests = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {
                            "name": client_name,
                            "version": client_version,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "workspace_status",
                        "arguments": {},
                        **({"_meta": request_meta} if request_meta else {}),
                    },
                },
            ]
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", str(entry), *command_arguments],
                cwd=plugin_root,
                env=environment,
                input="\n".join(
                    json.dumps(request, separators=(",", ":"))
                    for request in requests
                )
                + "\n",
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            return [
                json.loads(line)
                for line in completed.stdout.splitlines()
                if line.strip()
            ]

        with tempfile.TemporaryDirectory() as project_root:
            claude_environment = os.environ.copy()
            claude_environment["HDG_PROJECT_ROOT"] = project_root
            claude_responses = run_handshake(
                client_name="Claude Code",
                client_version="2.1.199",
                command_arguments=[],
                environment=claude_environment,
            )

            codex_environment = os.environ.copy()
            codex_environment.pop("HDG_PROJECT_ROOT", None)
            codex_responses = run_handshake(
                client_name="codex-mcp-client",
                client_version="1.0.0",
                command_arguments=["--project-root-from-meta"],
                environment=codex_environment,
                request_meta={
                    "codex/sandbox-state-meta": {
                        "sandboxCwd": Path(project_root).resolve().as_uri(),
                    }
                },
            )

        for responses in (claude_responses, codex_responses):
            with self.subTest(host=responses[0]["result"]["serverInfo"]):
                self.assertEqual(len(responses), 3)
                self.assertEqual(
                    responses[0]["result"]["serverInfo"],
                    {"name": "layered-delivery", "version": "0.16.5"},
                )
                tools = responses[1]["result"]["tools"]
                self.assertEqual(len(tools), 38)
                self.assertEqual(
                    {
                        tool["name"]
                        for tool in tools
                        if tool.get("_meta", {}).get(
                            "anthropic/requiresUserInteraction"
                        )
                        is True
                    },
                    SENSITIVE_MCP_TOOLS,
                )
                self.assertEqual(
                    responses[2]["result"]["structuredContent"],
                    {
                        "ok": True,
                        "result": {
                            "activePayloadUploads": 0,
                            "databaseExists": False,
                            "stagedPayloadBytes": 0,
                            "state": "ABSENT",
                        },
                    },
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

    def test_plugin_runtime_has_no_python_console_entrypoints_or_dependencies(self) -> None:
        pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('dependencies = []', pyproject)
        self.assertNotIn("[project.scripts]", pyproject)
        self.assertNotIn('hdg-mcp = "hdg.mcp_server:main"', pyproject)
        self.assertNotIn('hdg = "hdg.cli:main"', pyproject)
        self.assertFalse((REPOSITORY_ROOT / "bin" / "hdg.py").exists())
        self.assertFalse((SOURCE_PACKAGE / "cli.py").exists())
        self.assertFalse((SOURCE_PACKAGE / "__main__.py").exists())

    def test_repository_is_plugin_source_not_a_marketplace(self) -> None:
        self.assertFalse(
            (REPOSITORY_ROOT / ".agents" / "plugins" / "marketplace.json").exists()
        )
        self.assertFalse(
            (REPOSITORY_ROOT / ".claude-plugin" / "marketplace.json").exists()
        )

    def test_readme_documents_current_purpose_and_usage(self) -> None:
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLess(readme.index("## 能做什么"), readme.index("## 怎么用"))
        self.assertIn("当前版本：**0.16.5**", readme)
        self.assertIn("选择 `active` 或 `manual`", readme)
        self.assertIn("直接在对话中说明即可", readme)
        self.assertIn("用户不需要填写 `requiredSkills` 字段", readme)
        self.assertIn(
            "不预分析、不递归展开，也不自动加入 `GATE`",
            readme,
        )
        self.assertIn(
            "给出带来源的近似 Skill 选项",
            readme,
        )
        self.assertIn(
            "Scope 按最小可用模块边界适当放宽",
            readme,
        )
        self.assertIn("Codex", readme)
        self.assertIn("Claude Code", readme)
        self.assertNotIn("Plugin-only", readme)
        self.assertNotIn("Skill-only", readme)
        self.assertNotIn("CLI fallback", readme)
        self.assertNotIn("npx skills add", readme)
        self.assertIn("MCP 未连接或工具注册失败时不能开始开发", readme)
        for retired in (
            "codex plugin marketplace add feng123321li/layered-delivery",
            "claude plugin marketplace add feng123321li/layered-delivery",
            "git@git.i-sanger.com",
            "majorbio-skills",
            "git@git.i-sanger.com:ai/skill/layered-delivery.git",
            "https://git.i-sanger.com/ai/skill/layered-delivery.git",
            "python -X utf8 <skill-root>/scripts/hdg.py",
        ):
            self.assertNotIn(retired, readme)

    def test_plugin_payload_has_no_cli_escape_hatch(self) -> None:
        skill_root = TARGET_PACKAGE.parent.parent
        retired_cli_tokens = (
            "dispatch-task",
            "task-result",
            "evidence-contract",
            "accept-item",
            "acceptance-item",
            "heartbeat-task",
            "advance-graph",
            "resume-task",
            "remediate-task",
            "retry-item",
        )
        self.assertFalse((skill_root / "scripts" / "hdg.py").exists())
        self.assertFalse((skill_root / "scripts" / "hdg" / "cli.py").exists())
        self.assertFalse(
            (skill_root / "scripts" / "hdg" / "__main__.py").exists()
        )
        for path in skill_root.rglob("*.md"):
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("CLI fallback", text)
                self.assertNotIn("scripts/hdg.py", text)
                for token in retired_cli_tokens:
                    self.assertNotIn(token, text)
        for runtime_root in (
            SOURCE_PACKAGE,
            skill_root / "scripts" / "hdg",
        ):
            for path in runtime_root.rglob("*.py"):
                with self.subTest(path=path):
                    text = path.read_text(encoding="utf-8")
                    self.assertNotIn("commandHint", text)
                    self.assertNotIn("submitCommandHint", text)
                    for token in retired_cli_tokens:
                        self.assertNotIn(token, text)

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
            "copy", "dataclasses", "datetime", "difflib", "enum", "errno", "fcntl", "functools", "hashlib",
            "io", "json", "math", "msvcrt", "os", "pathlib", "posixpath", "re", "secrets", "shutil", "sqlite3",
            "stat", "sys", "tempfile", "threading", "time", "typing", "unicodedata", "unittest",
            "urllib", "uuid",
        }
        repository_root = Path(__file__).resolve().parents[1]
        runtime_paths = [
            *(
                path
                for path in SOURCE_PACKAGE.glob("*.py")
                if path.name not in {"cli.py", "__main__.py"}
            ),
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
