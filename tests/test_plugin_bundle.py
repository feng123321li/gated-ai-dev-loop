from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import hdg
from hdg.mcp_tools import tool_definitions
from hdg.mcp_adapter import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    PROTOCOL_VERSION_META_KEY,
)
from hdg.model_core import validate_hierarchy_definition

from .test_loop_architecture import group_hierarchy


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "hdg"
SKILL = ROOT / "skills" / "layered-delivery"
SKILL_RUNTIME = SKILL / "scripts" / "hdg"
PLUGIN = ROOT / "plugins" / "layered-delivery"
PLUGIN_SKILL = PLUGIN / "skills" / "layered-delivery"


class PluginBundleTests(unittest.TestCase):
    def test_freeze_confirmation_copy_has_only_two_options(self) -> None:
        text = (
            SKILL / "references" / "planning-quickstart.md"
        ).read_text(encoding="utf-8")
        expected_copy = (
            "请选择下一步：",
            "**自动执行**：立即冻结并开始实现、测试和独立审查",
            "**手动交接**：冻结后生成交接信息",
            "如需继续调整需求，请直接回复修改意见；当前方案不会冻结。",
        )
        for line in expected_copy:
            with self.subTest(line=line):
                self.assertIn(line, text)
        self.assertNotIn("**其他内容**", text)
        self.assertNotIn("**其他反馈**", text)
        self.assertNotIn("**调整需求", text)

    def test_skill_routes_prepared_and_replan_safely(self) -> None:
        main = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        planning = (
            SKILL / "references" / "planning-quickstart.md"
        ).read_text(encoding="utf-8")
        execution = (
            SKILL / "references" / "execution-quickstart.md"
        ).read_text(encoding="utf-8")
        acceptance = (
            SKILL / "references" / "acceptance.md"
        ).read_text(encoding="utf-8")
        transport = (
            SKILL / "references" / "mcp-transport.md"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "需求未变时保留当前准备结果，不重复 prepare",
            main,
        )
        self.assertIn(
            "只有需求实际变化时才重新 prepare",
            main,
        )
        self.assertIn(
            "回答后保留当前 fingerprint",
            planning,
        )
        self.assertIn("`cancel_graph_run`", main)
        self.assertIn("新的 `delivery.id`", main)
        self.assertIn("不要自动取消当前 run", execution)
        self.assertIn(
            "不要把“Review 未通过”提交成 `BLOCKED`",
            execution,
        )
        self.assertIn(
            "payload 只提供目标、明确约束和已知验收点",
            acceptance,
        )
        self.assertIn(
            "独立发现和重新验证",
            acceptance,
        )
        self.assertIn("重连后先调用 `workspace_status`", transport)
        self.assertNotIn(
            "未明确选择这两项时继续需求交互并重新 prepare",
            main,
        )
        for projection in (
            "baseline.md",
            "progress.md",
            "acceptance.md",
            "interfaces.md",
        ):
            with self.subTest(projection=projection):
                self.assertIn(projection, planning)
        self.assertIn(
            "`payload.interfaces`",
            planning,
        )
        self.assertIn("HTTP", planning)
        self.assertIn("Dubbo", planning)
        self.assertIn("before", planning)
        self.assertIn("after", planning)
        self.assertIn("入参", planning)
        self.assertIn("出参", planning)
        self.assertIn("humanArtifacts.workItems", planning)
        self.assertIn("work-items/<node-id>/", main)
        self.assertIn(
            "不生成 `hierarchy.json`、`graph.json` 或 `state.json` 副本",
            main,
        )

    def test_skill_keeps_agent_recommendations_advisory(self) -> None:
        main = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        planning = (
            SKILL / "references" / "planning-quickstart.md"
        ).read_text(encoding="utf-8")
        execution = (
            SKILL / "references" / "execution-quickstart.md"
        ).read_text(encoding="utf-8")
        recommendations = (
            SKILL / "references" / "agent-recommendations.md"
        ).read_text(encoding="utf-8")

        for tool_name in ("available_agents", "recommend_executors"):
            with self.subTest(tool_name=tool_name):
                self.assertIn(f"`{tool_name}`", main)
                self.assertIn(f"`{tool_name}`", recommendations)
        self.assertIn("不会启动、切换或派遣", planning)
        self.assertIn("不得据此启动外部 CLI", execution)
        self.assertIn("dispatchAllowed=false", recommendations)
        self.assertIn("不解析 `loop.payload`", recommendations)
        self.assertIn(
            "LAYERED_DELIVERY_AGENT_PROFILES",
            recommendations,
        )
        metadata = (
            SKILL / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("Agent + Model", metadata)

    def test_documented_hierarchy_examples_are_valid(self) -> None:
        documents = (
            ROOT / "README.md",
            SKILL / "references" / "planning-quickstart.md",
        )
        examples = 0
        for document in documents:
            text = document.read_text(encoding="utf-8")
            for block in re.findall(
                r"```json\s*\n(.*?)\n```",
                text,
                flags=re.DOTALL,
            ):
                value = json.loads(block)
                if not (
                    isinstance(value, dict)
                    and set(value) == {"delivery", "root"}
                ):
                    continue
                validate_hierarchy_definition(value)
                examples += 1
        self.assertGreaterEqual(examples, 3)

    def test_runtime_is_an_exact_source_copy_without_cli(self) -> None:
        source_files = {
            path.name: path.read_bytes()
            for path in SOURCE.glob("*.py")
        }
        runtime_files = {
            path.name: path.read_bytes()
            for path in SKILL_RUNTIME.glob("*.py")
        }
        self.assertEqual(runtime_files, source_files)
        self.assertNotIn("cli.py", runtime_files)
        self.assertNotIn("__main__.py", runtime_files)
        self.assertNotIn("acceptance.py", runtime_files)
        self.assertNotIn("execution.py", runtime_files)
        self.assertNotIn("skill_execution.py", runtime_files)

    def test_plugin_skill_matches_canonical_skill(self) -> None:
        canonical = {
            path.relative_to(SKILL): path.read_bytes()
            for path in SKILL.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        plugin = {
            path.relative_to(PLUGIN_SKILL): path.read_bytes()
            for path in PLUGIN_SKILL.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        self.assertEqual(plugin, canonical)

    def test_dual_host_manifests_match_runtime_version(self) -> None:
        for relative in (
            ".codex-plugin/plugin.json",
            ".claude-plugin/plugin.json",
        ):
            with self.subTest(relative=relative):
                manifest = json.loads(
                    (PLUGIN / relative).read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["version"], hdg.__version__)
                self.assertIn("GROUP", manifest["description"])
                self.assertIn("TASK", manifest["description"])

    def test_sensitive_hook_references_only_existing_tools(self) -> None:
        hooks = json.loads(
            (PLUGIN / "hooks" / "hooks.json").read_text(
                encoding="utf-8"
            )
        )
        matchers = {
            entry["matcher"].rsplit("__", 1)[-1]
            for entry in hooks["hooks"]["PreToolUse"]
        }
        names = {tool["name"] for tool in tool_definitions()}
        self.assertEqual(
            matchers,
            {
                "rebuild_graph_run",
                "cancel_graph_run",
            },
        )
        self.assertLessEqual(matchers, names)

    def test_explicit_user_choices_do_not_trigger_host_reapproval(
        self,
    ) -> None:
        manifest = json.loads(
            (PLUGIN / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        server = manifest["mcpServers"]["layered-delivery"]
        self.assertEqual(
            server["default_tools_approval_mode"],
            "approve",
        )
        approvals = server["tools"]
        self.assertNotIn("freeze_hierarchy", approvals)
        self.assertNotIn("record_user_confirmation", approvals)

    def test_tool_count_is_the_scheduler_surface(self) -> None:
        self.assertEqual(len(tool_definitions()), 19)

    def test_bundled_mcp_prefers_modern_stdio_discovery(self) -> None:
        entry = SKILL / "scripts" / "hdg_mcp.py"
        request_meta = {
            PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION,
            CLIENT_CAPABILITIES_META_KEY: {},
            CLIENT_INFO_META_KEY: {
                "name": "bundle-test",
                "version": "1.0.0",
            },
        }
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {"_meta": request_meta},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {"_meta": request_meta},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "available_agents",
                    "arguments": {},
                    "_meta": request_meta,
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "prepare_hierarchy",
                    "arguments": {"hierarchy": group_hierarchy()},
                    "_meta": request_meta,
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "recommend_executors",
                    "arguments": {"root_id": "d-service"},
                    "_meta": request_meta,
                },
            },
        ]
        request = "".join(
            json.dumps(message, separators=(",", ":")) + "\n"
            for message in messages
        )
        with TemporaryDirectory() as project_root:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(entry),
                    "--project-root",
                    project_root,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            stdout, stderr = process.communicate(
                request,
                timeout=10,
            )
        self.assertEqual(process.returncode, 0, stderr)
        responses = [
            json.loads(line)
            for line in stdout.splitlines()
            if line
        ]
        self.assertEqual(len(responses), 5)
        self.assertEqual(
            responses[0]["result"]["supportedVersions"],
            [
                MODERN_PROTOCOL_VERSION,
                LEGACY_PREFERRED_PROTOCOL_VERSION,
            ],
        )
        self.assertEqual(
            responses[0]["result"]["resultType"],
            "complete",
        )
        self.assertEqual(
            len(responses[1]["result"]["tools"]),
            19,
        )
        discovery = responses[2]["result"]["structuredContent"]["result"]
        self.assertFalse(
            discovery["rules"]["developmentCommandsStarted"]
        )
        advice = responses[4]["result"]["structuredContent"]["result"]
        self.assertTrue(advice["recommendations"])
        self.assertTrue(
            all(
                not item["dispatchAllowed"]
                for item in advice["recommendations"]
            )
        )

    def test_bundled_mcp_keeps_legacy_initialize_fallback(self) -> None:
        entry = SKILL / "scripts" / "hdg_mcp.py"
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "legacy-bundle-test",
                        "version": "1.0.0",
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
        ]
        request = "".join(
            json.dumps(message, separators=(",", ":")) + "\n"
            for message in messages
        )
        with TemporaryDirectory() as project_root:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(entry),
                    "--project-root",
                    project_root,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            stdout, stderr = process.communicate(
                request,
                timeout=10,
            )
        self.assertEqual(process.returncode, 0, stderr)
        responses = [
            json.loads(line)
            for line in stdout.splitlines()
            if line
        ]
        self.assertEqual(len(responses), 2)
        self.assertEqual(
            responses[0]["result"]["protocolVersion"],
            "2025-11-25",
        )
        self.assertNotIn("resultType", responses[0]["result"])
        self.assertEqual(
            len(responses[1]["result"]["tools"]),
            19,
        )


if __name__ == "__main__":
    unittest.main()
