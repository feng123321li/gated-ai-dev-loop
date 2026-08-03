from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import runpy
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import types
import unittest
from unittest import mock

import hdg
from hdg.dispatch_planning import plan_dispatch_batch
from hdg.errors import GatedLoopError
from hdg.graph_model import loop_node_id
from hdg.graph_runtime import graph_status
from hdg.mcp_tools import call_tool, tool_definitions
from hdg.mcp_adapter import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    PROTOCOL_VERSION_META_KEY,
)
from hdg.model_core import validate_hierarchy_definition
from hdg.planning import (
    freeze_hierarchy,
    prepare_hierarchy,
    preview_hierarchy,
)

from .test_loop_architecture import group_hierarchy, task_hierarchy
from .automatic_dispatch import dispatch_loop, reserve_loop


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "hdg"
SKILL = ROOT / "skills" / "layered-delivery"
SKILL_RUNTIME = SKILL / "scripts" / "hdg"
PLUGIN = ROOT / "plugins" / "layered-delivery"
PLUGIN_SKILL = PLUGIN / "skills" / "layered-delivery"


class PluginBundleTests(unittest.TestCase):
    @staticmethod
    def codex_executor_inventory() -> list[dict]:
        return [
            {
                "agentId": "codex",
                "displayName": "Codex",
                "dispatchTransport": "HOST_NATIVE",
                "capabilities": ["development", "review"],
                "availableSlots": 2,
                "priority": 20,
                "nativeModelSelectionSupported": True,
                "models": [
                    {
                        "id": "gpt-5.6-sol",
                        "family": "gpt-5.6",
                        "tier": "FRONTIER",
                        "reasoningEffort": "high",
                        "priority": 10,
                    },
                    {
                        "id": "gpt-5.6-terra",
                        "family": "gpt-5.6",
                        "tier": "BALANCED",
                        "reasoningEffort": "medium",
                        "priority": 20,
                    },
                ],
            }
        ]

    @staticmethod
    def codex_subagent_event(
        root: str,
        *,
        agent_id: str,
        model_id: str,
        task_name: str,
        cwd: str,
        parent_session_id: str = "codex-parent-session",
        agent_type: str = "default",
    ) -> tuple[dict, Path]:
        codex_home = Path(root, "codex-home")
        transcript = (
            codex_home
            / "sessions"
            / "2026"
            / "07"
            / "31"
            / f"rollout-{agent_id}.jsonl"
        )
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "session_id": parent_session_id,
                        "id": agent_id,
                        "source": {
                            "subagent": {
                                "thread_spawn": {
                                    "parent_thread_id": parent_session_id,
                                    "agent_path": f"/root/{task_name}",
                                    "agent_role": (
                                        None
                                        if agent_type == "default"
                                        else agent_type
                                    ),
                                }
                            }
                        },
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return (
            {
                "hook_event_name": "SubagentStart",
                "session_id": parent_session_id,
                "turn_id": "codex-turn-1",
                "agent_id": agent_id,
                "agent_type": agent_type,
                "permission_mode": "default",
                "model": model_id,
                "cwd": cwd,
                "transcript_path": str(transcript),
            },
            codex_home,
        )

    @staticmethod
    def run_codex_hook(
        hook_event: dict,
        codex_home: Path,
    ) -> subprocess.CompletedProcess:
        hook_module = runpy.run_path(
            str(
                PLUGIN
                / "hooks"
                / "attest_codex_subagent_receiver.py"
            )
        )
        hook_main = hook_module["main"]
        hook_main.__globals__["_trusted_codex_sessions_root"] = lambda: (
            codex_home / "sessions"
        ).resolve()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                sys,
                "stdin",
                io.StringIO(json.dumps(hook_event)),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            returncode = hook_main()
        return subprocess.CompletedProcess(
            args=["in-process-codex-hook"],
            returncode=returncode,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
        )

    @staticmethod
    def run_loop_operation_hook(
        hook_event: dict,
        codex_home: Path,
    ) -> subprocess.CompletedProcess:
        support_globals = runpy.run_path(
            str(
                PLUGIN
                / "hooks"
                / "attest_codex_subagent_receiver.py"
            )
        )
        support = types.ModuleType("attest_codex_subagent_receiver")
        support.__dict__.update(support_globals)
        trusted_sessions = lambda: (
            codex_home / "sessions"
        ).resolve()
        support._trusted_codex_sessions_root = trusted_sessions
        support._session_meta_from_transcript.__globals__[
            "_trusted_codex_sessions_root"
        ] = trusted_sessions
        hook_module = runpy.run_path(
            str(
                PLUGIN
                / "hooks"
                / "authorize_loop_operation.py"
            )
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.dict(
                sys.modules,
                {"attest_codex_subagent_receiver": support},
            ),
            mock.patch.object(
                sys,
                "stdin",
                io.StringIO(json.dumps(hook_event)),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            returncode = hook_module["main"]()
        return subprocess.CompletedProcess(
            args=["in-process-loop-operation-hook"],
            returncode=returncode,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
        )

    def test_freeze_confirmation_copy_has_only_two_options(self) -> None:
        text = (
            SKILL / "references" / "planning-quickstart.md"
        ).read_text(encoding="utf-8")
        expected_copy = (
            "请选择下一步：",
            (
                "**自动执行**：创建或选择实际开发工作区，准备并冻结后"
                "开始实现、测试和独立审查"
            ),
            "**手动交接**：只生成开发内容交接文件，不创建任务或工作区",
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

        self.assertIn("`PREPARED`", main)
        self.assertIn("需求未变时不要重复 preview", main + planning)
        self.assertIn("初次开发前用户修改需求时", planning)
        self.assertIn(
            "回答后保留当前 fingerprint",
            planning,
        )
        self.assertIn("`prepare_delivery_revision`", main)
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
        self.assertIn("work-items/", transport)
        self.assertIn("<root-id>/", transport)
        self.assertIn(
            "不生成 hierarchy、Graph 或运行状态 JSON 副本",
            transport,
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
        self.assertIn("不再询问", planning)
        self.assertIn("30 秒调整窗口", planning)
        self.assertIn("到期自动重调", planning)
        self.assertIn("只生成一个自包含交接文件", planning)
        self.assertIn("交接前不指定 Agent", planning)
        self.assertIn("开始实际开发时才创建", planning)
        self.assertNotIn("创建新 Codex 任务", planning)
        self.assertNotIn("创建新 Claude 会话", planning)
        self.assertIn("推荐工具不得启动外部 CLI", execution)
        self.assertIn("dispatchAllowed=false", recommendations)
        self.assertIn("不解析 `loop.payload`", recommendations)
        self.assertIn(
            "LAYERED_DELIVERY_AGENT_PROFILES",
            recommendations,
        )
        metadata = (
            SKILL / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("开发内容交接文件", metadata)

    def test_skill_auto_dispatches_planned_models_in_parallel(self) -> None:
        main = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        execution = (
            SKILL / "references" / "execution-quickstart.md"
        ).read_text(encoding="utf-8")
        recommendations = (
            SKILL / "references" / "agent-recommendations.md"
        ).read_text(encoding="utf-8")
        orchestrator = (
            SKILL
            / "references"
            / "orchestrator-configuration.md"
        ).read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("`plan_dispatch_batch`", main)
        self.assertIn("显式模型 assignment 必须覆盖", execution)
        self.assertIn("并发创建", main)
        self.assertIn(
            "Codex 由 `SubagentStart` Hook 在 child 上下文可见前完成 host-side claim",
            execution,
        )
        self.assertIn("WAIT_FOR_DISPATCH_RECEIVER", main + execution)
        self.assertIn("dispatchReservationId", main + execution)
        self.assertIn("decisionFingerprint", execution)
        self.assertIn("不能继承总调度 Agent 的模型", execution)
        self.assertIn("派遣前自动判级", execution)
        self.assertIn("`ROUTINE`/`STANDARD`/`HIGH`", execution)
        self.assertIn("`ROUTINE` 目标为 `EFFICIENT`", recommendations)
        self.assertIn("luna 标为 `EFFICIENT`", recommendations)
        self.assertIn("不确定时使用 `HIGH`", execution)
        self.assertIn("只用于路由判级", execution)
        self.assertIn("HOST_NATIVE_DISPATCH_PLAN", recommendations)
        self.assertIn("HOST_NATIVE_ROUTE_REVIEW", recommendations)
        self.assertIn("30 秒", main + execution + recommendations)
        self.assertIn("dispatchTransport=HOST_NATIVE", main + execution)
        self.assertIn("EXTERNAL_PROCESS", main + execution + recommendations)
        self.assertIn("codex-companion", recommendations)
        self.assertIn("UNSAFE_EXECUTOR_TRANSPORT", recommendations)
        self.assertIn("`diversityLevel`", main + recommendations)
        self.assertIn('"autoSelectModel": true', orchestrator)
        self.assertIn('"allowCrossAdapterDispatch": false', orchestrator)
        self.assertIn('"maxConcurrentExecutors": 4', orchestrator)
        self.assertIn("%APPDATA%", orchestrator)
        self.assertIn("XDG_CONFIG_HOME", orchestrator)
        self.assertIn(
            "ORCHESTRATOR_CROSS_ADAPTER_UNAVAILABLE",
            orchestrator,
        )
        self.assertIn("设置工具不依赖 Delivery 工作区", orchestrator)
        self.assertIn("打开中央编排器设置", readme)
        self.assertIn("当前跨 Adapter 限制", readme)
        self.assertIn("RECEIVER_ROOT_ROTATED", execution)
        self.assertIn("无需重冻", readme)

    def test_skill_uses_native_soft_stop_and_hard_429_breaker(
        self,
    ) -> None:
        execution = (
            SKILL / "references" / "execution-quickstart.md"
        ).read_text(encoding="utf-8")
        self.assertIn("剩余额度不高于 5%", execution)
        self.assertIn("Claude Code", execution)
        self.assertIn("一次性 Cron", execution)
        self.assertIn("Codex Desktop", execution)
        self.assertIn("当前任务计划", execution)
        self.assertIn("直接收到 429", execution)
        self.assertIn("模型外宿主适配器私有回调", execution)
        self.assertIn("cancelRecurringMonitors=true", execution)
        self.assertIn("HOST_NATIVE_ONE_SHOT", execution)
        self.assertNotIn("RECOMMEND_ALTERNATE_OR_WAIT", execution)

    def test_skill_isolates_deliveries_and_versions_task_requirements(
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
            "workspaceKey",
            "linked worktree",
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
        self.assertIn("优先选择本地 `main`", planning)
        self.assertIn("回退 `master`", planning)
        self.assertIn(
            "不得从当前 feature HEAD 分叉",
            planning,
        )
        self.assertIn("新用户需求默认属于新 Delivery", planning)
        self.assertIn(
            "不得仅因 `workspace_status` 返回旧 Delivery 就进入 Revision",
            planning,
        )
        self.assertIn("`WORKTREE_SETUP_QUEUED`", planning)
        self.assertIn(
            "排队期间不重试创建",
            planning,
        )
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
        self.assertLessEqual(len(main.splitlines()), 120)
        self.assertNotIn("```json", readme)
        for reference in (
            "planning-quickstart.md",
            "execution-quickstart.md",
            "agent-recommendations.md",
            "acceptance.md",
            "mcp-transport.md",
            "orchestrator-configuration.md",
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

    def test_sensitive_hook_references_only_existing_tools(self) -> None:
        hooks = json.loads(
            (PLUGIN / "hooks" / "hooks.json").read_text(
                encoding="utf-8"
            )
        )
        matchers = {
            entry["matcher"].rsplit("__", 1)[-1]
            for entry in hooks["hooks"]["PreToolUse"]
            if (
                "require_sensitive_tool_approval.py"
                in entry["hooks"][0]["command"]
            )
        }
        names = {tool["name"] for tool in tool_definitions()}
        self.assertEqual(
            matchers,
            {
                "rebuild_graph_run",
                "cancel_graph_run",
                "unfreeze_task_requirement",
                "refreeze_task_requirement",
                "update_orchestrator_settings",
            },
        )
        self.assertLessEqual(matchers, names)

    def test_every_sensitive_claude_hook_returns_an_approval_prompt(
        self,
    ) -> None:
        hooks = json.loads(
            (PLUGIN / "hooks" / "hooks.json").read_text(
                encoding="utf-8"
            )
        )
        script = PLUGIN / "hooks" / "require_sensitive_tool_approval.py"
        for entry in hooks["hooks"]["PreToolUse"]:
            if (
                "require_sensitive_tool_approval.py"
                not in entry["hooks"][0]["command"]
            ):
                continue
            with self.subTest(matcher=entry["matcher"]):
                completed = subprocess.run(
                    [sys.executable, "-X", "utf8", str(script)],
                    input=json.dumps(
                        {
                            "hook_event_name": "PreToolUse",
                            "tool_name": entry["matcher"],
                        }
                    ),
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                output = json.loads(completed.stdout)
                decision = output["hookSpecificOutput"]
                self.assertEqual(decision["permissionDecision"], "ask")

    def test_claude_rate_limit_hook_uses_stop_failure(self) -> None:
        hooks = json.loads(
            (PLUGIN / "hooks" / "hooks.json").read_text(
                encoding="utf-8"
            )
        )
        stop_failure = hooks["hooks"]["StopFailure"]
        self.assertEqual(len(stop_failure), 1)
        self.assertEqual(stop_failure[0]["matcher"], "rate_limit")
        command = stop_failure[0]["hooks"][0]
        self.assertIn(
            "handle_claude_rate_limit.py",
            command["command"],
        )

    def test_claude_dispatch_hook_attests_spawned_subagent(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
            )
            reservation = reserve_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=loop_node_id("t-service"),
                agent_id="claude-code",
                model_id="claude-sonnet",
            )
            tool_input = {
                "root_id": prepared["rootId"],
                "node_id": loop_node_id("t-service"),
                "owner": "claude-child",
                "agent_id": "untrusted-model-claim",
                "model_id": "claude-sonnet",
                "dispatch_mode": reservation["dispatchMode"],
                "dispatch_transport": reservation[
                    "dispatchTransport"
                ],
                "dispatch_reservation_id": reservation[
                    "dispatchReservationId"
                ],
                "dispatch_reasoning_class": reservation[
                    "dispatchReasoningClass"
                ],
                "dispatch_decision_fingerprint": reservation[
                    "dispatchDecisionFingerprint"
                ],
                "receiver_context_id": "untrusted-context",
                "operation_id": "op-claude-hook-attested",
            }
            claude_transcript = Path(
                root,
                "claude-parent-session.jsonl",
            )
            claude_transcript.write_text("", encoding="utf-8")
            claude_sidechain = Path(
                root,
                "claude-parent-session",
                "subagents",
                "agent-claude-agent-child-1.jsonl",
            )
            claude_sidechain.parent.mkdir(parents=True)
            dispatch_tool = (
                "mcp__plugin_layered-delivery_layered-delivery"
                "__dispatch_loop"
            )
            claude_sidechain.write_text(
                json.dumps(
                    {
                        "agentId": "claude-agent-child-1",
                        "sessionId": "claude-parent-session",
                        "message": {
                            "role": "assistant",
                            "model": "glm-5.2",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "claude-dispatch-tool",
                                    "name": dispatch_tool,
                                    "input": tool_input,
                                }
                            ],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(
                        PLUGIN
                        / "hooks"
                        / "attest_claude_dispatch_receiver.py"
                    ),
                ],
                input=json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": dispatch_tool,
                        "tool_use_id": "claude-dispatch-tool",
                        "tool_input": tool_input,
                        "agent_id": "claude-agent-child-1",
                        "session_id": "claude-parent-session",
                        "cwd": root,
                        "transcript_path": str(claude_transcript),
                    }
                ),
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            updated = json.loads(completed.stdout)[
                "hookSpecificOutput"
            ]["updatedInput"]
            claimed = call_tool(
                "dispatch_loop",
                updated,
                root=root,
                trusted_host_adapter="claude-code",
            )
            heartbeat_tool = (
                "mcp__plugin_layered-delivery_layered-delivery"
                "__heartbeat_loop"
            )
            heartbeat_input = {
                "root_id": prepared["rootId"],
                "node_id": loop_node_id("t-service"),
            }
            mutation = self.run_loop_operation_hook(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "claude-parent-session",
                    "agent_id": "claude-agent-child-1",
                    "tool_name": heartbeat_tool,
                    "tool_input": heartbeat_input,
                    "tool_use_id": "claude-heartbeat-tool",
                    "cwd": root,
                    "transcript_path": str(claude_transcript),
                },
                Path(root, "codex-home"),
            )
            mutation_output = json.loads(mutation.stdout)[
                "hookSpecificOutput"
            ]
            unassigned_mutation = self.run_loop_operation_hook(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "claude-parent-session",
                    "agent_id": "claude-agent-unassigned",
                    "tool_name": heartbeat_tool,
                    "tool_input": heartbeat_input,
                    "tool_use_id": "claude-unassigned-heartbeat",
                    "cwd": root,
                    "transcript_path": str(claude_transcript),
                },
                Path(root, "codex-home"),
            )
            unassigned_output = json.loads(unassigned_mutation.stdout)[
                "hookSpecificOutput"
            ]
            with claude_sidechain.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "agentId": "claude-agent-child-1",
                            "sessionId": "claude-parent-session",
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "model": "claude-opus",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "forged-model-tool",
                                        "name": heartbeat_tool,
                                        "input": heartbeat_input,
                                    }
                                ],
                            },
                        }
                    )
                    + "\n"
                )
            forwarded_mutation = self.run_loop_operation_hook(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "claude-parent-session",
                    "agent_id": "claude-agent-child-1",
                    "tool_name": heartbeat_tool,
                    "tool_input": heartbeat_input,
                    "tool_use_id": "forged-model-tool",
                    "model": "claude-sonnet",
                    "cwd": root,
                    "transcript_path": str(claude_transcript),
                },
                Path(root, "codex-home"),
            )
            forwarded_output = json.loads(forwarded_mutation.stdout)[
                "hookSpecificOutput"
            ]

        self.assertEqual(updated["agent_id"], "claude-code")
        self.assertEqual(
            updated["receiver_context_id"],
            "claude-agent-child-1",
        )
        self.assertTrue(claimed["receiverAttested"])
        self.assertEqual(claimed["modelId"], "claude-sonnet")
        self.assertEqual(claimed["actualModelId"], "glm-5.2")
        self.assertEqual(mutation_output["permissionDecision"], "allow")
        self.assertEqual(
            mutation_output["updatedInput"]["operation_id"],
            claimed["operationId"],
        )
        self.assertEqual(
            unassigned_output["permissionDecision"],
            "deny",
        )
        self.assertEqual(
            forwarded_output["permissionDecision"],
            "allow",
        )

    def test_claude_dispatch_hook_blocks_parent_context_claim(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(
                    PLUGIN
                    / "hooks"
                    / "attest_claude_dispatch_receiver.py"
                ),
            ],
            input=json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_input": {},
                    "session_id": "claude-parent-session",
                    "cwd": str(ROOT),
                }
            ),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)

    def test_codex_plugin_registers_native_subagent_attestation(self) -> None:
        manifest = json.loads(
            (PLUGIN / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("hooks", manifest)
        self.assertEqual(
            manifest["mcpServers"]["layered-delivery"]["env"][
                "HDG_HOST_ADAPTER"
            ],
            "codex",
        )
        hooks = json.loads(
            (PLUGIN / "hooks" / "hooks.json").read_text(
                encoding="utf-8"
            )
        )
        subagent_start = hooks["hooks"]["SubagentStart"]
        self.assertEqual(len(subagent_start), 1)
        command = subagent_start[0]["hooks"][0]
        self.assertIn("attest_codex_subagent_receiver.py", command["command"])
        self.assertEqual(command["timeout"], 30)
        self.assertNotIn("commandWindows", command)
        operation_hooks = [
            entry
            for entry in hooks["hooks"]["PreToolUse"]
            if (
                "authorize_loop_operation.py"
                in entry["hooks"][0]["command"]
            )
        ]
        self.assertEqual(len(operation_hooks), 1)
        self.assertEqual(
            operation_hooks[0]["matcher"],
            "^mcp__.*__(heartbeat_loop|report_loop_progress|pause_loop|record_loop_result)$",
        )
        environment = dict(os.environ)
        environment["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN)
        launched = subprocess.run(
            command["command"],
            input=json.dumps({"hook_event_name": "unmatched"}),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            shell=True,
            env=environment,
        )
        self.assertEqual(launched.returncode, 0, launched.stderr)

    def test_codex_subagent_hook_attests_automatic_receiver(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
            )
            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=self.codex_executor_inventory(),
                node_requirements=[
                    {
                        "nodeId": loop_node_id("t-service"),
                        "reasoningClass": "STANDARD",
                        "source": "PLANNING",
                        "reason": "The host analyzed this development Loop.",
                    }
                ],
            )["assignments"][0]
            nested_cwd = Path(root, "modules", "service")
            nested_cwd.mkdir(parents=True)
            hook_event, hook_codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-child-1",
                model_id="effective-model-from-local-forwarder",
                task_name=assignment["hostTaskName"],
                cwd=str(nested_cwd),
            )
            completed = self.run_codex_hook(
                hook_event,
                hook_codex_home,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            additional_context = json.loads(completed.stdout)[
                "hookSpecificOutput"
            ]["additionalContext"]
            self.assertIn(
                "immediately call heartbeat_loop once before any other tool",
                additional_context,
            )
            marker = "LAYERED_DELIVERY_ASSIGNMENT="
            assignment_context = json.loads(
                additional_context.split(marker, maxsplit=1)[1].splitlines()[0]
            )
            self.assertNotIn("receiver_attestation_id", completed.stdout)
            self.assertNotIn("LAYERED_DELIVERY_RECEIVER=", completed.stdout)
            self.assertNotIn("operation_id", assignment_context)
            operation_event = {
                "hook_event_name": "PreToolUse",
                "session_id": hook_event["session_id"],
                "agent_id": hook_event["agent_id"],
                "turn_id": "codex-child-turn",
                "tool_use_id": "codex-child-tool",
                "tool_name": (
                    "mcp__plugin_layered-delivery_layered-delivery"
                    "__record_loop_result"
                ),
                "tool_input": {
                    "root_id": assignment_context["root_id"],
                    "node_id": assignment_context["node_id"],
                    "outcome": {
                        "status": "SUCCEEDED",
                        "summary": "verified",
                    },
                },
                "model": hook_event["model"],
                "cwd": hook_event["cwd"],
                "transcript_path": hook_event["transcript_path"],
            }
            authorized = self.run_loop_operation_hook(
                operation_event,
                hook_codex_home,
            )
            rewritten = json.loads(authorized.stdout)[
                "hookSpecificOutput"
            ]
            self.assertEqual(rewritten["permissionDecision"], "allow")
            injected_operation = rewritten["updatedInput"]["operation_id"]
            connection = sqlite3.connect(
                Path(root, ".layered-delivery", "scheduler.db")
            )
            try:
                identity = connection.execute(
                    "SELECT attestation_digest, status, operation_id "
                    "FROM host_receiver_identities"
                ).fetchone()
            finally:
                connection.close()
            self.assertRegex(identity[0], r"^[0-9a-f]{64}$")
            self.assertEqual(identity[1], "CONSUMED")
            self.assertEqual(
                identity[2],
                injected_operation,
            )
            state = graph_status(
                root=root,
                root_id=prepared["rootId"],
            )
            claimed = next(
                item
                for item in state["nodes"]
                if item["nodeId"] == assignment_context["node_id"]
            )
            replayed = self.run_codex_hook(
                hook_event,
                hook_codex_home,
            )
            helper_event, helper_codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-unassigned-helper",
                model_id=assignment["model"]["id"],
                task_name="unrelated_helper",
                cwd=str(nested_cwd),
            )
            denied_event = {
                **operation_event,
                "session_id": helper_event["session_id"],
                "agent_id": helper_event["agent_id"],
                "transcript_path": helper_event["transcript_path"],
                "tool_input": {
                    **operation_event["tool_input"],
                    "operation_id": injected_operation,
                },
            }
            denied = self.run_loop_operation_hook(
                denied_event,
                helper_codex_home,
            )
            denied_output = json.loads(denied.stdout)[
                "hookSpecificOutput"
            ]
            missing_transcript_event = {
                **operation_event,
                "session_id": "codex-root-without-transcript",
                "transcript_path": None,
                "tool_input": {
                    **operation_event["tool_input"],
                    "operation_id": injected_operation,
                },
            }
            missing_transcript = self.run_loop_operation_hook(
                missing_transcript_event,
                hook_codex_home,
            )
            missing_output = json.loads(missing_transcript.stdout)[
                "hookSpecificOutput"
            ]

        self.assertEqual(assignment_context["agent_id"], "codex")
        self.assertEqual(
            assignment_context["receiver_context_id"],
            "codex-child-1",
        )
        self.assertEqual(
            assignment_context["dispatch_reservation_id"],
            assignment["dispatchReservationId"],
        )
        self.assertEqual(assignment_context["node_id"], assignment["nodeId"])
        self.assertEqual(claimed["status"], "CLAIMED")
        self.assertEqual(claimed["modelId"], assignment["model"]["id"])
        self.assertEqual(
            claimed["actualModelId"],
            "effective-model-from-local-forwarder",
        )
        self.assertEqual(
            claimed["operationId"],
            injected_operation,
        )
        self.assertIn("LAYERED_DELIVERY_ASSIGNMENT=", replayed.stdout)
        self.assertNotIn("receiver_attestation_id", replayed.stdout)
        replayed_context = json.loads(replayed.stdout)[
            "hookSpecificOutput"
        ]["additionalContext"]
        replayed_assignment = json.loads(
            replayed_context.split(marker, maxsplit=1)[1].splitlines()[0]
        )
        self.assertNotIn("operation_id", replayed_assignment)
        self.assertEqual(denied_output["permissionDecision"], "deny")
        self.assertEqual(missing_output["permissionDecision"], "deny")

    def test_codex_subagent_hook_treats_observed_model_as_display_only(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
            )
            plan = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=self.codex_executor_inventory(),
                node_requirements=[
                    {
                        "nodeId": loop_node_id("t-service"),
                        "reasoningClass": "STANDARD",
                        "source": "PLANNING",
                        "reason": "The host analyzed this development Loop.",
                    }
                ],
            )
            hook_event, hook_codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-child-forwarded-model",
                model_id="deepseek-v4-pro",
                task_name=plan["assignments"][0]["hostTaskName"],
                cwd=root,
            )
            completed = self.run_codex_hook(
                hook_event,
                hook_codex_home,
            )
            state = graph_status(root=root, root_id=prepared["rootId"])

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("LAYERED_DELIVERY_ASSIGNMENT=", completed.stdout)
        claimed = next(
            item
            for item in state["nodes"]
            if item["nodeId"] == loop_node_id("t-service")
        )
        self.assertEqual(claimed["modelId"], "gpt-5.6-terra")
        self.assertEqual(claimed["actualModelId"], "deepseek-v4-pro")
        self.assertEqual(
            claimed["actualModelSource"],
            "HOST_REPORTED",
        )

    def test_codex_subagent_hook_requires_reserved_task_name(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
            )
            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared[
                    "graphFingerprint"
                ],
                executor_inventory=self.codex_executor_inventory(),
                node_requirements=[
                    {
                        "nodeId": loop_node_id("t-service"),
                        "reasoningClass": "STANDARD",
                        "source": "PLANNING",
                        "reason": "The host analyzed this development Loop.",
                    }
                ],
            )["assignments"][0]
            helper_event, helper_codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-explorer-child",
                model_id=assignment["model"]["id"],
                task_name="unrelated_explorer",
                cwd=root,
            )
            helper = self.run_codex_hook(
                helper_event,
                helper_codex_home,
            )
            intended_event, intended_codex_home = (
                self.codex_subagent_event(
                    root,
                    agent_id="codex-intended-child",
                    model_id=assignment["model"]["id"],
                    task_name=assignment["hostTaskName"],
                    cwd=root,
                )
            )
            intended = self.run_codex_hook(
                intended_event,
                intended_codex_home,
            )

        self.assertEqual(helper.returncode, 0, helper.stderr)
        self.assertEqual(helper.stdout, "")
        self.assertEqual(intended.returncode, 0, intended.stderr)
        self.assertIn("LAYERED_DELIVERY_ASSIGNMENT=", intended.stdout)
        self.assertNotIn("receiver_attestation_id", intended.stdout)

    def test_codex_subagent_hook_rejects_overridden_codex_home(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
            )
            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared[
                    "graphFingerprint"
                ],
                executor_inventory=self.codex_executor_inventory(),
                node_requirements=[
                    {
                        "nodeId": loop_node_id("t-service"),
                        "reasoningClass": "STANDARD",
                        "source": "PLANNING",
                        "reason": "The host analyzed this development Loop.",
                    }
                ],
            )["assignments"][0]
            event, fake_codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-forged-child",
                model_id=assignment["model"]["id"],
                task_name=assignment["hostTaskName"],
                cwd=root,
            )
            environment = dict(os.environ)
            environment["CODEX_HOME"] = str(fake_codex_home)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(
                        PLUGIN
                        / "hooks"
                        / "attest_codex_subagent_receiver.py"
                    ),
                ],
                input=json.dumps(event),
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                env=environment,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_codex_hook_stops_at_nearest_linked_worktree(self) -> None:
        with TemporaryDirectory() as root:
            parent = Path(root, "primary")
            linked = parent / "nested" / "linked-worktree"
            nested_cwd = linked / "module" / "service"
            Path(parent, ".layered-delivery").mkdir(parents=True)
            Path(parent, ".layered-delivery", "scheduler.db").touch()
            Path(parent, ".git").mkdir()
            linked.mkdir(parents=True)
            Path(linked, ".git").write_text(
                "gitdir: ../.git/worktrees/linked-worktree\n",
                encoding="utf-8",
            )
            nested_cwd.mkdir(parents=True)
            hook_module = runpy.run_path(
                str(
                    PLUGIN
                    / "hooks"
                    / "attest_codex_subagent_receiver.py"
                )
            )

            workspace_start = hook_module["_workspace_start"](
                str(nested_cwd)
            )

        self.assertEqual(workspace_start, str(linked.resolve()))

    def test_codex_subagent_hook_ignores_unbound_workspace(self) -> None:
        with TemporaryDirectory() as root:
            hook_event, hook_codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-child-1",
                model_id="gpt-5.6-terra",
                task_name="ld_00000000000000000000000000000000",
                cwd=root,
            )
            completed = self.run_codex_hook(
                hook_event,
                hook_codex_home,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "")
            self.assertFalse(Path(root, ".layered-delivery").exists())

    def test_claude_rate_limit_hook_pauses_without_agent_feedback(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
            )
            node_id = loop_node_id("t-service")
            dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=node_id,
                owner="claude-worker",
                agent_id="claude-code",
                model_id="claude-opus",
                receiver_context_id="claude-session",
                operation_id="op-claude-rate-limit-hook",
            )
            reset_at = (
                datetime.now(timezone.utc) + timedelta(hours=2)
            ).isoformat().replace("+00:00", "Z")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(
                        PLUGIN
                        / "hooks"
                        / "handle_claude_rate_limit.py"
                    ),
                ],
                input=json.dumps(
                    {
                        "hook_event_name": "StopFailure",
                        "error": "rate_limit",
                        "error_details": f"429 resetAt={reset_at}",
                        "session_id": "claude-session",
                        "cwd": root,
                    }
                ),
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            state = graph_status(
                root=root,
                root_id=prepared["rootId"],
            )
            current = next(
                item for item in state["nodes"] if item["nodeId"] == node_id
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(current["status"], "PAUSED")
        self.assertEqual(state["hostCapacity"]["resetAt"], reset_at)

    def test_claude_rate_limit_hook_ignores_rendered_message_reset(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
            )
            node_id = loop_node_id("t-service")
            dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=node_id,
                owner="claude-worker",
                agent_id="claude-code",
                model_id="claude-opus",
                receiver_context_id="claude-session",
                operation_id="op-ignore-rendered-rate-limit",
            )
            rendered_reset = (
                datetime.now(timezone.utc) + timedelta(hours=2)
            ).isoformat().replace("+00:00", "Z")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(
                        PLUGIN
                        / "hooks"
                        / "handle_claude_rate_limit.py"
                    ),
                ],
                input=json.dumps(
                    {
                        "hook_event_name": "StopFailure",
                        "error": "rate_limit",
                        "error_details": {
                            "requestLoggedAt": rendered_reset,
                            "message": "429 without structured reset",
                        },
                        "last_assistant_message": rendered_reset,
                        "session_id": "claude-session",
                        "cwd": root,
                    }
                ),
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            state = graph_status(
                root=root,
                root_id=prepared["rootId"],
            )
            current = next(
                item for item in state["nodes"] if item["nodeId"] == node_id
            )

        self.assertEqual(completed.returncode, 0)
        self.assertNotIn("hostCapacity", state)
        self.assertEqual(current["status"], "PAUSED")

    def test_explicit_user_choices_do_not_trigger_host_reapproval(
        self,
    ) -> None:
        manifest = json.loads(
            (PLUGIN / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        server = manifest["mcpServers"]["layered-delivery"]
        self.assertEqual(server["env"]["HDG_HOST_ADAPTER"], "codex")
        claude_server = json.loads(
            (PLUGIN / ".mcp.json").read_text(encoding="utf-8")
        )["mcpServers"]["layered-delivery"]
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
            approvals["unfreeze_task_requirement"]["approval_mode"],
            "prompt",
        )
        self.assertEqual(
            approvals["refreeze_task_requirement"]["approval_mode"],
            "prompt",
        )
        self.assertEqual(
            approvals["update_orchestrator_settings"]["approval_mode"],
            "prompt",
        )

    def test_tool_count_is_the_scheduler_surface(self) -> None:
        tool_count = len(tool_definitions())
        self.assertEqual(tool_count, 29)
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
                    "name": "preview_hierarchy",
                    "arguments": {"hierarchy": hierarchy},
                    "_meta": request_meta,
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
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
            29,
        )
        discovery = responses[2]["result"]["structuredContent"]["result"]
        self.assertFalse(
            discovery["rules"]["developmentCommandsStarted"]
        )
        preview_result = responses[3]["result"]["structuredContent"][
            "result"
        ]
        self.assertEqual(preview_result["status"], "PREVIEW")
        handoff = responses[4]["result"]["structuredContent"]["result"]
        self.assertEqual(handoff["status"], "HANDOFF_READY")
        self.assertFalse(handoff["graphRunCreated"])

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
            29,
        )


if __name__ == "__main__":
    unittest.main()
