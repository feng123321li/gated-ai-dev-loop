from __future__ import annotations

import json
import os
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
from hdg.mcp_apps import DASHBOARD_RESOURCE_URI, MCP_APP_MIME_TYPE
from hdg.model_core import validate_hierarchy_definition
from hdg.planning import preview_hierarchy

from .test_loop_architecture import group_hierarchy
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "hdg"
SKILL = ROOT / "skills" / "delivery-graph"
SKILL_RUNTIME = SKILL / "scripts" / "hdg"
PLUGIN = ROOT / "plugins" / "delivery-graph"
PLUGIN_SKILL = PLUGIN / "skills" / "delivery-graph"


class PluginBundleTests(unittest.TestCase):
    def test_plugin_registers_coordinator_without_lifecycle_hooks(
        self,
    ) -> None:
        agent = (
            PLUGIN / "agents" / "delivery-coordinator.md"
        ).read_text(encoding="utf-8")
        manifests = [
            json.loads(
                (PLUGIN / relative).read_text(encoding="utf-8")
            )
            for relative in (
                ".codex-plugin/plugin.json",
                ".claude-plugin/plugin.json",
            )
        ]

        self.assertIn("name: delivery-coordinator", agent)
        self.assertIn("background: true", agent)
        self.assertIn("tools: Agent", agent)
        self.assertNotIn("isolation: worktree", agent)
        self.assertFalse((PLUGIN / "hooks").exists())
        for manifest in manifests:
            self.assertNotIn("hooks", manifest)


    def test_execution_choice_copy_is_owned_by_controller(self) -> None:
        text = (
            SKILL / "references" / "planning-quickstart.md"
        ).read_text(encoding="utf-8")
        self.assertIn("`pendingInteraction`", text)
        self.assertIn("该对象的 `markdown`", text)
        self.assertIn("机械映射到 `AskUserQuestion`", text)
        self.assertIn("`request_user_input`（Codex）", text)
        self.assertIn("优先把其 `options` 机械映射", text)
        self.assertIn("只有映射工具在当前上下文不可调用", text)
        self.assertIn("不为它创建“其他”选项", text)
        self.assertIn("Controller 是交互文案的唯一所有者", text)
        self.assertIn("`AUTOMATIC`", text)
        self.assertIn("`MANUAL`", text)
        self.assertIn(
            "`freeformInput.nextAction=CONTINUE_REQUIREMENT_DISCUSSION`",
            text,
        )
        self.assertIn("先记录业务确认", text)
        self.assertIn(
            "再用明确 `rootId` 和原双 fingerprint 调用 "
            "`resume_execution_mode`",
            text,
        )
        self.assertIn("展示 `manualHandoff.receiverPrompt`", text)
        self.assertIn("不把同一 Delivery 限制为单仓库", text)
        self.assertIn("不得为第二仓库另起 Delivery", text)

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

        self.assertIn("`PREPARED`", main)
        self.assertIn(
            "需求未变且尚无 `executionSelection` 时不要重复 preview",
            main + planning,
        )
        self.assertIn("初次开发前用户修改需求时", planning)
        self.assertIn(
            "回答后保留当前 fingerprint",
            planning,
        )
        self.assertIn("`prepare_delivery_revision`", main + planning)
        self.assertIn("保持相同 `delivery.id`", planning + execution)
        self.assertIn("不要创建新的 Delivery ID", execution)
        self.assertIn("旧 run 自动成为 `SUPERSEDED`", execution)
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
            "revisions.md",
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
        self.assertIn("`controlStateCreated=true`", planning)
        self.assertIn("共享 `.layered-delivery/scheduler.db`", planning)
        self.assertIn("`HANDOFF_READY`", main + planning + transport)
        self.assertIn("work-items/", transport)
        self.assertIn("<root-id>/", transport)
        self.assertIn(
            "不生成 hierarchy、Graph 或运行状态 JSON 副本",
            transport,
        )

    def test_skill_keeps_receiver_and_worker_boundaries_explicit(self) -> None:
        main = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        plugin_main = (PLUGIN_SKILL / "SKILL.md").read_text(
            encoding="utf-8"
        )
        planning = (
            SKILL / "references" / "planning-quickstart.md"
        ).read_text(encoding="utf-8")
        execution = (
            SKILL / "references" / "execution-quickstart.md"
        ).read_text(encoding="utf-8")
        recommendations = (
            SKILL / "references" / "agent-execution-boundary.md"
        ).read_text(encoding="utf-8")
        for document in (main, plugin_main):
            self.assertIn("allowed-tools:", document)
            self.assertIn(
                "mcp__plugin_delivery-graph_delivery-graph__plan_dispatch_batch",
                document,
            )
            self.assertNotIn("delivery-graph__*", document)
        self.assertNotIn("`recommend_executors`", main + recommendations)
        self.assertIn("不推荐派遣模型", planning)
        self.assertIn("不提供路由调整窗口", recommendations)
        self.assertIn("手动开发生成完整冻结内容包", planning)
        self.assertIn(
            ".layered-delivery/<delivery-id>/handoff-<fingerprint>.md",
            planning,
        )
        self.assertIn(
            "不得创建跨需求共享的 `.layered-delivery/handoffs/`",
            planning,
        )
        self.assertIn("不指定 Agent、模型或接收任务", planning)
        self.assertIn("start_manual_handoff", planning)
        public_execution_contract = main + plugin_main + planning
        for removed_contract in (
            "`environment=worktree`",
            "`hostDispatch`",
            "`EXCLUSIVE_PRIMARY_CHECKOUT`",
            "`HOST_NATIVE_LINKED_WORKTREE`",
            "启动后台 coordinator",
            "`manualDirectoryChangeRequired=false`",
            "`coordinatorCheckoutPolicy=PRESERVE_CURRENT_CHECKOUT`",
            "`requiresNewTopLevelSession=false`",
        ):
            self.assertNotIn(
                removed_contract,
                public_execution_contract,
            )
        for document in (main, plugin_main, planning, execution):
            self.assertIn("`CURRENT_WORKSPACE_SERIAL`", document)
        self.assertIn(
            "`PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION`",
            main + planning + execution,
        )
        self.assertIn("`resume_execution_mode`", main + planning + execution)
        self.assertIn("不得重试选择", main + planning)
        self.assertIn(
            "同一物理 checkout 一次只运行一个 Delivery",
            main + planning + execution,
        )
        self.assertIn("`NEW_FROM_CURRENT_BRANCH`", planning)
        self.assertIn("`worktreeProvenance`", planning)
        self.assertIn("`baseHeadCommit`", planning)
        self.assertIn("`selectionSource`", planning)
        self.assertIn("`DIRTY_CONFIRMATION_REQUIRED`", planning)
        self.assertIn(
            "`confirmed_dirty_state_fingerprint`",
            planning,
        )
        self.assertIn(
            "`BRANCH_BOUND_TO_OTHER_DELIVERY`",
            planning,
        )
        self.assertIn("不能仅凭 feature 分支名", planning)
        self.assertIn("内部 Worker 不是 Graph receiver", recommendations)
        self.assertIn("不得调用 `dispatch_loop`", recommendations)
        self.assertIn("MANUAL claim", recommendations)
        metadata = (
            SKILL / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("冻结", metadata)

    def test_skill_dispatches_tasks_and_reviews_with_explicit_operations(
        self,
    ) -> None:
        main = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        execution = (
            SKILL / "references" / "execution-quickstart.md"
        ).read_text(encoding="utf-8")
        recommendations = (
            SKILL / "references" / "agent-execution-boundary.md"
        ).read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        contract = main + execution + recommendations

        self.assertIn("`plan_dispatch_batch`", contract)
        self.assertIn("Ready TASK/Review", execution)
        self.assertIn("reservation", contract)
        self.assertIn("decision fingerprint", execution)
        self.assertIn("显式 `operation_id`", execution)
        self.assertIn("独立 child", contract)
        self.assertIn("Plugin 不安装生命周期 Hook", execution)
        self.assertNotIn("`claim_current_task`", contract)
        self.assertNotIn("receiver_attestation_id", contract)
        self.assertNotIn("SessionStart", contract)
        self.assertNotIn("SubagentStart", contract)
        self.assertIn("WAIT_FOR_DISPATCH_RECEIVER", contract)
        self.assertIn("始终继承当前宿主模型", execution)
        self.assertIn("不提供路由调整窗口", recommendations)
        self.assertNotIn("打开中央编排器设置", readme)

    def test_skill_serializes_deliveries_and_versions_task_requirements(
        self,
    ) -> None:
        main = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        planning = (
            SKILL / "references" / "planning-quickstart.md"
        ).read_text(encoding="utf-8")
        execution = (
            SKILL / "references" / "execution-quickstart.md"
        ).read_text(encoding="utf-8")
        for marker in (
            "CURRENT_WORKSPACE_SERIAL",
            "rootId",
            "suggestedGitBinding",
            "delivery.gitBinding",
            "unfreeze_task_requirement",
            "refreeze_task_requirement",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, main + planning + execution)
        self.assertIn("跨 Delivery", main)
        self.assertIn("REFREEZE_TASK_REQUIREMENT", execution)
        self.assertIn("requirement revision 1", planning)
        self.assertIn("不得修改依赖", execution)
        self.assertIn("宿主显式选择", planning)
        self.assertIn("`origin/HEAD`", planning)
        self.assertIn("本地 `main`、本地 `master`", planning)
        self.assertIn("不得未经确认从当前 Delivery feature HEAD 分叉", execution)
        self.assertIn("显式 stacked Delivery 授权", planning)
        self.assertIn("新用户需求默认属于新 Delivery", planning)
        self.assertIn(
            "不得仅因 `workspace_status` 返回旧 Delivery 就进入 Revision",
            planning,
        )
        self.assertIn("不自动创建新 worktree", main + planning + execution)
        self.assertIn("后启动或后发现者等待", main + planning + execution)
        self.assertNotIn("`WORKTREE_SETUP_QUEUED`", planning)
        self.assertNotIn("`AUTOMATIC_PARALLEL`", main + planning + execution)
        self.assertIn(
            "不应触发宿主通用确认弹窗",
            planning + execution,
        )
        self.assertIn(
            "所有 TASK 共享该 Delivery",
            execution,
        )
        self.assertIn("projectScopes", main + planning + execution)
        self.assertIn("同名", main + planning + execution)
        self.assertIn(
            "TASK 可按各自 scope 单独执行 `git add` 和 `git commit`",
            main + execution,
        )
        self.assertNotIn("临时 task branch", main + execution)

    def test_entry_docs_use_progressive_disclosure(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        main = (SKILL / "SKILL.md").read_text(encoding="utf-8")

        self.assertLessEqual(len(readme.splitlines()), 200)
        self.assertLessEqual(len(main.splitlines()), 160)
        self.assertNotIn("```json", readme)
        for reference in (
            "planning-quickstart.md",
            "execution-quickstart.md",
            "agent-execution-boundary.md",
            "acceptance.md",
            "mcp-transport.md",
        ):
            with self.subTest(reference=reference):
                self.assertIn(reference, main)

    def test_documented_hierarchy_examples_are_valid(self) -> None:
        documents = (
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
        self.assertGreaterEqual(examples, 2)

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
                self.assertIn("冻结开发包", manifest["description"])


    def test_explicit_user_choices_do_not_trigger_host_reapproval(
        self,
    ) -> None:
        manifest = json.loads(
            (PLUGIN / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        server = manifest["mcpServers"]["delivery-graph"]
        self.assertEqual(server["env"]["HDG_HOST_ADAPTER"], "codex")
        claude_server = json.loads(
            (PLUGIN / ".mcp.json").read_text(encoding="utf-8")
        )["mcpServers"]["delivery-graph"]
        self.assertEqual(
            claude_server["env"]["HDG_HOST_ADAPTER"],
            "claude-code",
        )
        self.assertEqual(
            server["default_tools_approval_mode"],
            "approve",
        )
        approvals = server["tools"]
        self.assertNotIn("freeze_hierarchy", approvals)
        self.assertNotIn("record_user_confirmation", approvals)
        self.assertEqual(
            approvals["archive_delivery"]["approval_mode"],
            "prompt",
        )
        self.assertEqual(
            approvals["unfreeze_task_requirement"]["approval_mode"],
            "prompt",
        )
        self.assertEqual(
            approvals["refreeze_task_requirement"]["approval_mode"],
            "prompt",
        )
        self.assertEqual(
            approvals["handoff_ready_automatic_task"]["approval_mode"],
            "prompt",
        )
        self.assertNotIn("update_orchestrator_settings", approvals)

    def test_tool_count_is_the_scheduler_surface(self) -> None:
        tool_count = len(tool_definitions())
        self.assertEqual(tool_count, 32)
        self.assertIn(
            "start_manual_handoff",
            {tool["name"] for tool in tool_definitions()},
        )
        self.assertIn(
            "report_loop_progress",
            {tool["name"] for tool in tool_definitions()},
        )
        self.assertNotIn(
            "report_host_capacity_exhausted",
            {tool["name"] for tool in tool_definitions()},
        )
        engineering = (ROOT / "docs" / "project-engineering.md").read_text(
            encoding="utf-8"
        )
        documented = re.search(
            r"`mcp_tools\.py` 把 (\d+) 个模型可调用工具映射到 Controller",
            engineering,
        )
        self.assertIsNotNone(documented)
        self.assertEqual(int(documented.group(1)), tool_count)

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
        hierarchy = group_hierarchy()
        with TemporaryDirectory() as project_root:
            preview = preview_hierarchy(
                root=project_root,
                hierarchy=hierarchy,
            )
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
                    "name": "preview_hierarchy",
                    "arguments": {"hierarchy": hierarchy},
                    "_meta": request_meta,
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "create_manual_handoff",
                    "arguments": {
                        "hierarchy": hierarchy,
                        "expected_hierarchy_fingerprint": (
                            preview["hierarchyFingerprint"]
                        ),
                        "expected_graph_fingerprint": (
                            preview["graphFingerprint"]
                        ),
                        "authorized_project_ids": [],
                        "confirmed_by": "human",
                    },
                    "_meta": request_meta,
                },
            },
            ]
            request = "".join(
                json.dumps(message, separators=(",", ":")) + "\n"
                for message in messages
            )
            environment = dict(os.environ)
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
                env=environment,
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
        self.assertEqual(len(responses), 4)
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
            32,
        )
        preview_result = responses[2]["result"]["structuredContent"][
            "result"
        ]
        self.assertEqual(preview_result["status"], "CHOICE_READY")
        self.assertTrue(preview_result["artifactsReady"])
        handoff = responses[3]["result"]["structuredContent"]["result"]
        self.assertEqual(handoff["status"], "HANDOFF_READY")
        self.assertEqual(handoff["requirementSnapshotStatus"], "FROZEN")
        self.assertFalse(handoff["graphRunCreated"])

    def test_canonical_and_plugin_bundled_mcp_serve_dashboard_resource(
        self,
    ) -> None:
        request_meta = {
            PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION,
            CLIENT_CAPABILITIES_META_KEY: {},
            CLIENT_INFO_META_KEY: {
                "name": "bundle-resource-test",
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
                "method": "resources/list",
                "params": {"_meta": request_meta},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "resources/read",
                "params": {
                    "uri": DASHBOARD_RESOURCE_URI,
                    "_meta": request_meta,
                },
            },
        ]
        request = "".join(
            json.dumps(message, separators=(",", ":")) + "\n"
            for message in messages
        )
        entries = {
            "canonical-skill": SKILL / "scripts" / "hdg_mcp.py",
            "plugin-copy": PLUGIN_SKILL / "scripts" / "hdg_mcp.py",
        }

        for bundle, entry in entries.items():
            with self.subTest(bundle=bundle):
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
                        env=dict(os.environ),
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
                self.assertEqual(len(responses), 3)
                self.assertIn(
                    "resources",
                    responses[0]["result"]["capabilities"],
                )
                resources = responses[1]["result"]["resources"]
                self.assertEqual(len(resources), 1)
                self.assertEqual(resources[0]["uri"], DASHBOARD_RESOURCE_URI)
                self.assertEqual(resources[0]["mimeType"], MCP_APP_MIME_TYPE)
                content = responses[2]["result"]["contents"][0]
                self.assertEqual(content["uri"], DASHBOARD_RESOURCE_URI)
                self.assertEqual(content["mimeType"], MCP_APP_MIME_TYPE)
                self.assertIn("<html", content["text"].lower())
                self.assertIn("open_delivery_dashboard", content["text"])

    def test_bundled_mcp_ignores_retired_orchestrator_config(self) -> None:
        entry = SKILL / "scripts" / "hdg_mcp.py"
        request_meta = {
            PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION,
            CLIENT_CAPABILITIES_META_KEY: {},
            CLIENT_INFO_META_KEY: {
                "name": "retired-config-bundle-test",
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
        ]
        request = "".join(
            json.dumps(message, separators=(",", ":")) + "\n"
            for message in messages
        )
        with TemporaryDirectory() as project_root:
            config = Path(project_root, "user-config", "orchestrator.json")
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "automaticOrchestration": True,
                        "autoSelectModel": True,
                        "allowCrossAdapterDispatch": False,
                        "allowedAdapters": ["codex", "claude-code"],
                        "maxConcurrentExecutors": 4,
                        "quotaExhaustionPolicy": "PAUSE_AND_RESUME",
                        "preferDifferentAdapterForReview": True,
                    }
                ),
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment["DELIVERY_GRAPH_ORCHESTRATOR_CONFIG"] = str(config)
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
                env=environment,
            )
            stdout, stderr = process.communicate(request, timeout=10)

        self.assertEqual(process.returncode, 0, stderr)
        responses = [
            json.loads(line)
            for line in stdout.splitlines()
            if line
        ]
        self.assertEqual(len(responses), 2)
        tools = responses[1]["result"]["tools"]
        self.assertEqual(len(tools), 32)
        self.assertNotIn(
            "open_orchestrator_settings",
            {tool["name"] for tool in tools},
        )
        self.assertNotIn(
            "update_orchestrator_settings",
            {tool["name"] for tool in tools},
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
            environment = dict(os.environ)
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
                env=environment,
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
            32,
        )


if __name__ == "__main__":
    unittest.main()
