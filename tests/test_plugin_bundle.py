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
import threading
import types
import unittest
from unittest import mock

import hdg
from hdg.dispatch_planning import plan_dispatch_batch
from hdg.errors import GatedLoopError
from hdg.graph_model import loop_node_id, task_review_node_id
from hdg.graph_runtime import (
    dispatch_loop as runtime_dispatch_loop,
    graph_status,
    record_loop_result,
)
from hdg.mcp_tools import call_tool, tool_definitions
from hdg.mcp_adapter import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    PROTOCOL_VERSION_META_KEY,
)
from hdg.mcp_apps import DASHBOARD_RESOURCE_URI, MCP_APP_MIME_TYPE
from hdg.model_core import validate_hierarchy_definition
from hdg.planning import (
    create_manual_handoff,
    freeze_hierarchy,
    prepare_hierarchy,
    preview_hierarchy,
    start_manual_handoff,
)
from hdg.repository import SchedulerRepository

from .test_loop_architecture import group_hierarchy, task_hierarchy
from .automatic_dispatch import dispatch_loop, reserve_loop


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "hdg"
SKILL = ROOT / "skills" / "delivery-graph"
SKILL_RUNTIME = SKILL / "scripts" / "hdg"
PLUGIN = ROOT / "plugins" / "delivery-graph"
PLUGIN_SKILL = PLUGIN / "skills" / "delivery-graph"
CODEX_HOOKS = PLUGIN / "hooks" / "hooks.json"
CLAUDE_HOOKS = PLUGIN / "hooks" / "claude-hooks.json"


class PluginBundleTests(unittest.TestCase):
    def test_claude_plugin_registers_background_delivery_coordinator(
        self,
    ) -> None:
        agent = (
            PLUGIN / "agents" / "delivery-coordinator.md"
        ).read_text(encoding="utf-8")
        hooks = json.loads(
            CLAUDE_HOOKS.read_text(encoding="utf-8")
        )
        self.assertIn("name: delivery-coordinator", agent)
        self.assertIn("background: true", agent)
        self.assertIn("tools: Agent", agent)
        self.assertNotIn("isolation: worktree", agent)
        commands = [
            hook["command"]
            for group in hooks["hooks"]["PreToolUse"]
            for hook in group["hooks"]
        ]
        self.assertTrue(
            any("attest_claude_workspace.py" in item for item in commands)
        )

    def test_claude_workspace_hook_attests_linked_worktree_cwd(self) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            worktree = Path(root, "delivery-worktree")
            repository.mkdir()

            def git(*arguments: str, cwd: Path = repository) -> None:
                completed = subprocess.run(
                    ["git", "-C", str(cwd), *arguments],
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stderr,
                )

            git("init", "--initial-branch=main")
            git("config", "user.name", "Plugin Tests")
            git("config", "user.email", "plugin-tests@example.invalid")
            Path(repository, "tracked.txt").write_text(
                "baseline\n",
                encoding="utf-8",
            )
            git("add", "tracked.txt")
            git("commit", "-m", "baseline")
            git(
                "worktree",
                "add",
                "-b",
                "feature/delivery-hook",
                str(worktree),
                "main",
            )
            scheduler = SchedulerRepository(str(repository))
            with scheduler.transaction():
                pass
            transcript = Path(root, "claude-transcript.jsonl")
            transcript.write_text("{}\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(
                        PLUGIN
                        / "hooks"
                        / "attest_claude_workspace.py"
                    ),
                ],
                input=json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": (
                            "mcp__plugin_delivery-graph_"
                            "delivery-graph__workspace_status"
                        ),
                        "tool_input": {"root_id": "d-service"},
                        "tool_use_id": "claude-tool-use",
                        "session_id": "claude-session",
                        "transcript_path": str(transcript),
                        "cwd": str(worktree),
                    }
                ),
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            updated = json.loads(completed.stdout)["hookSpecificOutput"][
                "updatedInput"
            ]
            self.assertEqual(updated["root_id"], "d-service")
            attestation = updated["_host_workspace_attestation"]
            resolved = scheduler.consume_host_workspace_attestation(
                attestation,
                host_adapter_id="claude-code",
                tool_name="workspace_status",
            )
            self.assertEqual(resolved, str(worktree.resolve()))

    @staticmethod
    def codex_subagent_event(
        root: str,
        *,
        agent_id: str,
        model_id: str | None,
        task_name: str,
        cwd: str,
        parent_session_id: str = "codex-parent-session",
        agent_type: str = "default",
        start_transcript_is_parent: bool = False,
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
        start_transcript = transcript
        if start_transcript_is_parent:
            start_transcript = transcript.with_name(
                f"rollout-{parent_session_id}.jsonl"
            )
            start_transcript.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": parent_session_id,
                            "source": "cli",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        event = {
            "hook_event_name": "SubagentStart",
            "session_id": (
                parent_session_id
                if start_transcript_is_parent
                else agent_id
            ),
                "turn_id": "codex-turn-1",
                "agent_id": agent_id,
                "agent_type": agent_type,
                "permission_mode": "default",
                "cwd": cwd,
                "transcript_path": str(start_transcript),
            }
        if model_id is not None:
            event["model"] = model_id
        return event, codex_home

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
        hook_main.__globals__["_trusted_codex_sessions_root"] = lambda _path=None: (
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
        trusted_sessions = lambda _path=None: (
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

    @staticmethod
    def run_codex_manual_dispatch_hook(
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
        trusted_sessions = lambda _path=None: (
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
                / "attest_codex_dispatch_receiver.py"
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
            args=["in-process-codex-manual-dispatch-hook"],
            returncode=returncode,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
        )

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
        self.assertIn("后台方用原双 fingerprint 调用 `resume_execution_mode`", text)
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
        allowed_tools = (
            "allowed-tools:\n"
            "  - mcp__plugin_delivery-graph_delivery-graph__*"
        )
        self.assertIn(allowed_tools, main)
        self.assertIn(allowed_tools, plugin_main)
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
        self.assertIn("`environment=worktree`", planning)
        self.assertIn("`${CLAUDE_PROJECT_DIR}`", planning)
        self.assertIn("`hostDispatch`", main + planning)
        self.assertNotIn("`EXCLUSIVE_PRIMARY_CHECKOUT`", main + planning)
        self.assertIn("`HOST_NATIVE_LINKED_WORKTREE`", main + planning)
        self.assertIn("启动后台 coordinator", main + planning)
        self.assertIn("禁止要求用户启动第二个顶层 Claude 会话", planning)
        self.assertIn(
            "`manualDirectoryChangeRequired=false`",
            main + planning,
        )
        self.assertIn(
            "`coordinatorCheckoutPolicy=PRESERVE_CURRENT_CHECKOUT`",
            main + planning,
        )
        self.assertIn(
            "`requiresNewTopLevelSession=false`",
            main + planning,
        )
        self.assertIn("主任务不切换目录或分支", planning)
        self.assertIn("`NEW_FROM_CURRENT_BRANCH`", planning)
        self.assertIn("`CREATE_DELIVERY_FEATURE_BRANCH`", planning)
        self.assertIn("重新调用 `workspace_status`", planning)
        self.assertIn("`HOST_NATIVE_LINKED_WORKTREE`", planning)
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

    def test_skill_runs_current_codex_task_and_dispatches_independent_reviews(self) -> None:
        main = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        execution = (
            SKILL / "references" / "execution-quickstart.md"
        ).read_text(encoding="utf-8")
        recommendations = (
            SKILL / "references" / "agent-execution-boundary.md"
        ).read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("`plan_dispatch_batch`", main)
        self.assertIn(
            "modelPolicy=CURRENT_HOST_INHERIT",
            main + execution + recommendations,
        )
        self.assertIn("`claim_current_task`", main + execution)
        self.assertIn("当前 Delivery 会话", main + execution)
        self.assertIn(
            "互不冲突的 TASK 实现可按 frontier 并行执行",
            execution,
        )
        self.assertIn(
            "Codex 由 `SubagentStart` Hook 在 child 可见前完成 host-side claim",
            execution,
        )
        self.assertIn("WAIT_FOR_DISPATCH_RECEIVER", main + execution)
        self.assertIn("非空 reservation", main + execution)
        self.assertIn("决策指纹", execution)
        self.assertIn("始终继承当前宿主模型", execution)
        self.assertIn("不提供路由调整窗口", recommendations)
        self.assertIn("不接收", recommendations)
        self.assertIn("model inventory", recommendations)
        self.assertIn("Plugin 内置", execution)
        self.assertIn("不读取用户级编排配置", execution)
        self.assertNotIn("打开中央编排器设置", readme)
        self.assertIn("RECEIVER_ROOT_ROTATED", execution)
        self.assertIn("恢复无需重新 prepare/freeze", execution)

    def test_skill_uses_native_soft_stop_and_hard_429_breaker(
        self,
    ) -> None:
        execution = (
            SKILL / "references" / "execution-quickstart.md"
        ).read_text(encoding="utf-8")
        self.assertIn("剩余额度不高于 5%", execution)
        self.assertIn("Claude `StopFailure`", execution)
        self.assertIn("一次性恢复提示", execution)
        self.assertIn("宿主直接观察到硬 429", execution)
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
        self.assertIn("宿主显式选择", planning)
        self.assertIn("`origin/HEAD`", planning)
        self.assertIn("本地 `main`、本地 `master`", planning)
        self.assertIn("不得隐式从当前 feature HEAD 分叉", planning)
        self.assertIn("显式 stacked Delivery 授权", planning)
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

    def test_sensitive_hook_references_only_existing_tools(self) -> None:
        hooks = json.loads(
            CLAUDE_HOOKS.read_text(
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
                "archive_delivery",
                "rebuild_graph_run",
                "cancel_graph_run",
                "unfreeze_task_requirement",
                "refreeze_task_requirement",
                "handoff_ready_automatic_task",
            },
        )
        self.assertLessEqual(matchers, names)

    def test_sensitive_claude_tools_skip_expiring_workspace_evidence(
        self,
    ) -> None:
        namespace = runpy.run_path(
            str(PLUGIN / "hooks" / "attest_claude_workspace.py")
        )
        self.assertEqual(
            namespace["SENSITIVE_ADMINISTRATIVE_TOOLS"],
            frozenset(
                {
                    "archive_delivery",
                    "cancel_graph_run",
                    "rebuild_graph_run",
                    "refreeze_task_requirement",
                    "unfreeze_task_requirement",
                    "handoff_ready_automatic_task",
                }
            ),
        )

    def test_every_sensitive_claude_hook_returns_an_approval_prompt(
        self,
    ) -> None:
        hooks = json.loads(
            CLAUDE_HOOKS.read_text(
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
            CLAUDE_HOOKS.read_text(
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
                "mcp__plugin_delivery-graph_delivery-graph"
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
                "mcp__plugin_delivery-graph_delivery-graph"
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
        self.assertNotIn("modelId", claimed)
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

    def test_claude_hooks_bind_and_authorize_manual_task_child(self) -> None:
        with TemporaryDirectory() as root:
            hierarchy = task_hierarchy()
            preview = preview_hierarchy(root=root, hierarchy=hierarchy)
            handoff = create_manual_handoff(
                root=root,
                hierarchy=hierarchy,
                expected_hierarchy_fingerprint=(
                    preview["hierarchyFingerprint"]
                ),
                expected_graph_fingerprint=preview["graphFingerprint"],
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
            )
            start_manual_handoff(
                root=root,
                root_id=handoff["rootId"],
                expected_hierarchy_fingerprint=(
                    handoff["hierarchyFingerprint"]
                ),
                expected_graph_fingerprint=handoff["graphFingerprint"],
                started_by="claude-parent-session",
                workspace_root=root,
            )
            dispatch_tool = (
                "mcp__plugin_delivery-graph_delivery-graph"
                "__dispatch_loop"
            )
            tool_input = {
                "root_id": handoff["rootId"],
                "node_id": loop_node_id("t-service"),
                "owner": "claude-agent-child-manual",
                "agent_id": "untrusted-model-claim",
                "receiver_context_id": "untrusted-context",
                "receiver_attestation_id": "untrusted-attestation",
                "dispatch_mode": "MANUAL",
                "operation_id": "op-claude-manual-task",
            }
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
                        "tool_use_id": "claude-manual-dispatch-tool",
                        "tool_input": tool_input,
                        "agent_id": "claude-agent-child-manual",
                        "session_id": "claude-parent-session",
                        "cwd": root,
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
            mutation = self.run_loop_operation_hook(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "claude-parent-session",
                    "agent_id": "claude-agent-child-manual",
                    "tool_name": (
                        "mcp__plugin_delivery-graph_delivery-graph"
                        "__heartbeat_loop"
                    ),
                    "tool_input": {
                        "root_id": handoff["rootId"],
                        "node_id": loop_node_id("t-service"),
                    },
                    "tool_use_id": "claude-manual-heartbeat-tool",
                    "cwd": root,
                },
                Path(root, "codex-home"),
            )
            mutation_output = json.loads(mutation.stdout)[
                "hookSpecificOutput"
            ]

        self.assertEqual(updated["agent_id"], "claude-code")
        self.assertEqual(
            updated["receiver_context_id"],
            "claude-agent-child-manual",
        )
        self.assertIn("receiver_attestation_id", updated)
        self.assertTrue(claimed["receiverAttested"])
        self.assertEqual(claimed["dispatchMode"], "MANUAL")
        self.assertEqual(mutation_output["permissionDecision"], "allow")
        self.assertEqual(
            mutation_output["updatedInput"]["operation_id"],
            claimed["operationId"],
        )

    def test_codex_plugin_registers_native_subagent_attestation(self) -> None:
        manifest = json.loads(
            (PLUGIN / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["hooks"], "./hooks/hooks.json")
        self.assertEqual(
            manifest["mcpServers"]["delivery-graph"]["env"][
                "HDG_HOST_ADAPTER"
            ],
            "codex",
        )
        hooks = json.loads(
            CODEX_HOOKS.read_text(
                encoding="utf-8"
            )
        )
        session_start = hooks["hooks"]["SessionStart"]
        self.assertEqual(len(session_start), 1)
        session_command = session_start[0]["hooks"][0]
        self.assertIn(
            "attest_codex_session_receiver.py",
            session_command["command"],
        )
        self.assertEqual(
            session_start[0]["matcher"],
            "startup|resume|compact",
        )
        subagent_start = hooks["hooks"]["SubagentStart"]
        self.assertEqual(len(subagent_start), 1)
        command = subagent_start[0]["hooks"][0]
        self.assertIn("attest_codex_subagent_receiver.py", command["command"])
        self.assertEqual(command["timeout"], 30)
        self.assertIn("commandWindows", command)
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
        preflight_hooks = [
            entry
            for entry in hooks["hooks"]["PreToolUse"]
            if (
                "attest_codex_dispatch_preflight.py"
                in entry["hooks"][0]["command"]
            )
        ]
        manual_dispatch_hooks = [
            entry
            for entry in hooks["hooks"]["PreToolUse"]
            if (
                "attest_codex_dispatch_receiver.py"
                in entry["hooks"][0]["command"]
            )
        ]
        self.assertEqual(len(preflight_hooks), 1)
        self.assertEqual(
            preflight_hooks[0]["matcher"],
            "^mcp__.*__(plan_dispatch_batch|claim_current_task)$",
        )
        self.assertEqual(len(manual_dispatch_hooks), 1)
        self.assertEqual(
            manual_dispatch_hooks[0]["matcher"],
            "^mcp__.*__dispatch_loop$",
        )
        environment = dict(os.environ)
        environment["PLUGIN_ROOT"] = str(PLUGIN)
        launched = subprocess.run(
            command["commandWindows"],
            input=json.dumps({"hook_event_name": "unmatched"}),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            shell=True,
            env=environment,
        )
        self.assertEqual(launched.returncode, 0, launched.stderr)

    def test_codex_session_hook_claims_current_automatic_task(self) -> None:
        with TemporaryDirectory() as root:
            hierarchy = task_hierarchy()
            preview = preview_hierarchy(
                root=root,
                hierarchy=hierarchy,
            )
            SchedulerRepository(root).record_automatic_selection(
                preview["rootId"],
                expected_hierarchy_fingerprint=(
                    preview["hierarchyFingerprint"]
                ),
                expected_graph_fingerprint=preview["graphFingerprint"],
                authorized_project_ids=[],
                confirmed_by="human",
            )
            fake_lifecycle = types.ModuleType(
                "attest_codex_subagent_receiver"
            )
            fake_lifecycle._runtime_path = lambda: ROOT / "src"
            fake_lifecycle._workspace_start = lambda _cwd: root
            fake_lifecycle._session_meta_from_transcript = (
                lambda _path, session_id: {
                    "id": session_id,
                    "source": "cli",
                }
            )
            hook_module = runpy.run_path(
                str(
                    PLUGIN
                    / "hooks"
                    / "attest_codex_session_receiver.py"
                )
            )
            stdout = io.StringIO()
            event = {
                "hook_event_name": "SessionStart",
                "source": "resume",
                "session_id": "codex-current-session",
                "transcript_path": str(Path(root, "session.jsonl")),
                "cwd": root,
            }
            with (
                mock.patch.dict(
                    sys.modules,
                    {
                        "attest_codex_subagent_receiver": (
                            fake_lifecycle
                        )
                    },
                ),
                mock.patch.object(
                    sys,
                    "stdin",
                    io.StringIO(json.dumps(event)),
                ),
                redirect_stdout(stdout),
            ):
                returncode = hook_module["main"]()
            output = json.loads(stdout.getvalue())[
                "hookSpecificOutput"
            ]["additionalContext"]
            marker = "DELIVERY_GRAPH_SESSION_AUTH="
            session_auth = json.loads(output.split(marker, 1)[1])
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=hierarchy,
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
            resolved = SchedulerRepository(
                root
            ).validate_host_workspace_attestation(
                session_auth["session_attestation"],
                host_adapter_id="codex",
                context_id=session_auth["session_context_id"],
                tool_name="delivery_session",
            )
            claimed = call_tool(
                "claim_current_task",
                {
                    "root_id": prepared["rootId"],
                    "node_id": loop_node_id("t-service"),
                    "expected_graph_fingerprint": prepared[
                        "graphFingerprint"
                    ],
                },
                root=root,
                workspace_root=root,
                trusted_host_adapter="codex",
                host_session_attested=True,
                host_session_context_id=session_auth[
                    "session_context_id"
                ],
                host_session_role="DELIVERY_COORDINATOR",
            )
            heartbeat = call_tool(
                "heartbeat_loop",
                {
                    "root_id": prepared["rootId"],
                    "node_id": loop_node_id("t-service"),
                },
                root=root,
                workspace_root=root,
                trusted_host_adapter="codex",
                host_session_attested=True,
                host_session_context_id=session_auth[
                    "session_context_id"
                ],
                host_session_role="DELIVERY_COORDINATOR",
            )

        self.assertEqual(returncode, 0)
        self.assertEqual(resolved, str(Path(root).resolve()))
        self.assertEqual(claimed["dispatchMode"], "INLINE_AUTO")
        self.assertEqual(claimed["dispatchTransport"], "HOST_SESSION")
        self.assertEqual(heartbeat["status"], "CLAIMED")

    def test_codex_desktop_claim_is_attested_at_tool_time(self) -> None:
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
            fake_lifecycle = types.ModuleType(
                "attest_codex_subagent_receiver"
            )
            fake_lifecycle._runtime_path = lambda: ROOT / "src"
            fake_lifecycle._workspace_start = lambda _cwd: root
            fake_lifecycle._session_meta_from_transcript = (
                lambda _path, session_id: {
                    "id": session_id,
                    "source": "cli",
                }
            )
            fake_lifecycle._subagent_claim_metadata = (
                lambda *_args, **_kwargs: None
            )
            claim_hook = runpy.run_path(
                str(
                    PLUGIN
                    / "hooks"
                    / "attest_codex_dispatch_preflight.py"
                )
            )
            claim_stdout = io.StringIO()
            claim_event = {
                "hook_event_name": "PreToolUse",
                "tool_name": (
                    "mcp__plugin_delivery-graph_delivery-graph"
                    "__claim_current_task"
                ),
                "tool_use_id": "codex-desktop-claim",
                "session_id": "codex-desktop-session",
                "transcript_path": str(Path(root, "session.jsonl")),
                "cwd": root,
                "tool_input": {
                    "root_id": prepared["rootId"],
                    "node_id": loop_node_id("t-service"),
                    "expected_graph_fingerprint": prepared[
                        "graphFingerprint"
                    ],
                },
            }
            with (
                mock.patch.dict(
                    sys.modules,
                    {
                        "attest_codex_subagent_receiver": (
                            fake_lifecycle
                        )
                    },
                ),
                mock.patch.object(
                    sys,
                    "stdin",
                    io.StringIO(json.dumps(claim_event)),
                ),
                redirect_stdout(claim_stdout),
            ):
                claim_returncode = claim_hook["main"]()
            updated_claim = json.loads(claim_stdout.getvalue())[
                "hookSpecificOutput"
            ]["updatedInput"]
            session_token = updated_claim.pop(
                "_host_session_attestation"
            )
            session_context_id = updated_claim.pop(
                "_host_session_context_id"
            )
            SchedulerRepository(
                root
            ).validate_host_workspace_attestation(
                session_token,
                host_adapter_id="codex",
                context_id=session_context_id,
                tool_name="delivery_session",
            )
            claimed = call_tool(
                "claim_current_task",
                updated_claim,
                root=root,
                workspace_root=root,
                trusted_host_adapter="codex",
                host_session_attested=True,
                host_session_context_id=session_context_id,
                host_session_role="DELIVERY_COORDINATOR",
            )

            mutation_hook = runpy.run_path(
                str(
                    PLUGIN
                    / "hooks"
                    / "authorize_loop_operation.py"
                )
            )
            heartbeat_stdout = io.StringIO()
            heartbeat_event = {
                "hook_event_name": "PreToolUse",
                "tool_name": (
                    "mcp__plugin_delivery-graph_delivery-graph"
                    "__heartbeat_loop"
                ),
                "tool_use_id": "codex-desktop-heartbeat",
                "session_id": "codex-desktop-session",
                "transcript_path": str(Path(root, "session.jsonl")),
                "cwd": root,
                "tool_input": {
                    "root_id": prepared["rootId"],
                    "node_id": loop_node_id("t-service"),
                },
            }
            with (
                mock.patch.dict(
                    sys.modules,
                    {
                        "attest_codex_subagent_receiver": (
                            fake_lifecycle
                        )
                    },
                ),
                mock.patch.object(
                    sys,
                    "stdin",
                    io.StringIO(json.dumps(heartbeat_event)),
                ),
                redirect_stdout(heartbeat_stdout),
            ):
                heartbeat_returncode = mutation_hook["main"]()
            updated_heartbeat = json.loads(
                heartbeat_stdout.getvalue()
            )["hookSpecificOutput"]["updatedInput"]
            operation_token = updated_heartbeat.pop(
                "_host_receiver_operation_attestation"
            )
            SchedulerRepository(
                root
            ).consume_host_workspace_attestation(
                operation_token,
                host_adapter_id="codex",
                tool_name="receiver_operation:heartbeat_loop",
            )
            heartbeat = call_tool(
                "heartbeat_loop",
                updated_heartbeat,
                root=root,
                workspace_root=root,
                trusted_host_adapter="codex",
                host_receiver_operation_attested=True,
            )

        self.assertEqual(claim_returncode, 0)
        self.assertEqual(heartbeat_returncode, 0)
        self.assertEqual(claimed["dispatchMode"], "INLINE_AUTO")
        self.assertEqual(heartbeat["status"], "CLAIMED")

    def test_codex_desktop_claim_hook_rejects_subagent(self) -> None:
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
            fake_lifecycle = types.ModuleType(
                "attest_codex_subagent_receiver"
            )
            fake_lifecycle._runtime_path = lambda: ROOT / "src"
            fake_lifecycle._workspace_start = lambda _cwd: root
            fake_lifecycle._session_meta_from_transcript = (
                lambda _path, session_id: {
                    "id": session_id,
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": "codex-parent"
                            }
                        }
                    },
                }
            )
            hook_module = runpy.run_path(
                str(
                    PLUGIN
                    / "hooks"
                    / "attest_codex_dispatch_preflight.py"
                )
            )
            stdout = io.StringIO()
            event = {
                "hook_event_name": "PreToolUse",
                "tool_name": (
                    "mcp__plugin_delivery-graph_delivery-graph"
                    "__claim_current_task"
                ),
                "tool_use_id": "codex-child-claim",
                "session_id": "codex-child",
                "transcript_path": str(Path(root, "session.jsonl")),
                "cwd": root,
                "tool_input": {
                    "root_id": prepared["rootId"],
                    "node_id": loop_node_id("t-service"),
                    "expected_graph_fingerprint": prepared[
                        "graphFingerprint"
                    ],
                },
            }
            with (
                mock.patch.dict(
                    sys.modules,
                    {
                        "attest_codex_subagent_receiver": (
                            fake_lifecycle
                        )
                    },
                ),
                mock.patch.object(
                    sys,
                    "stdin",
                    io.StringIO(json.dumps(event)),
                ),
                redirect_stdout(stdout),
            ):
                returncode = hook_module["main"]()
            output = json.loads(stdout.getvalue())["hookSpecificOutput"]

        self.assertEqual(returncode, 0)
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertNotIn("updatedInput", output)

    def test_codex_session_hook_does_not_attest_subagent_as_coordinator(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            fake_lifecycle = types.ModuleType(
                "attest_codex_subagent_receiver"
            )
            fake_lifecycle._runtime_path = lambda: ROOT / "src"
            fake_lifecycle._workspace_start = lambda _cwd: root
            fake_lifecycle._session_meta_from_transcript = (
                lambda _path, session_id: {
                    "id": session_id,
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": "codex-parent",
                            }
                        }
                    },
                }
            )
            hook_module = runpy.run_path(
                str(
                    PLUGIN
                    / "hooks"
                    / "attest_codex_session_receiver.py"
                )
            )
            stdout = io.StringIO()
            with (
                mock.patch.dict(
                    sys.modules,
                    {
                        "attest_codex_subagent_receiver": (
                            fake_lifecycle
                        )
                    },
                ),
                mock.patch.object(
                    sys,
                    "stdin",
                    io.StringIO(
                        json.dumps(
                            {
                                "hook_event_name": "SessionStart",
                                "session_id": "codex-review-child",
                                "transcript_path": str(
                                    Path(root, "child.jsonl")
                                ),
                                "cwd": root,
                            }
                        )
                    ),
                ),
                redirect_stdout(stdout),
            ):
                returncode = hook_module["main"]()

        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.getvalue(), "")

    def test_codex_manual_dispatch_hook_attests_native_child(self) -> None:
        with TemporaryDirectory() as root:
            hierarchy = task_hierarchy()
            preview = preview_hierarchy(root=root, hierarchy=hierarchy)
            handoff = create_manual_handoff(
                root=root,
                hierarchy=hierarchy,
                expected_hierarchy_fingerprint=(
                    preview["hierarchyFingerprint"]
                ),
                expected_graph_fingerprint=preview["graphFingerprint"],
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
            )
            start_manual_handoff(
                root=root,
                root_id=handoff["rootId"],
                expected_hierarchy_fingerprint=(
                    handoff["hierarchyFingerprint"]
                ),
                expected_graph_fingerprint=handoff["graphFingerprint"],
                started_by="codex-parent-manual",
                workspace_root=root,
            )
            child_event, codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-manual-child",
                model_id="host-observed-manual-model",
                task_name="manual_delivery_receiver",
                cwd=root,
                parent_session_id="codex-parent-manual",
            )
            dispatch_tool = (
                "mcp__plugin_delivery-graph_delivery-graph"
                "__dispatch_loop"
            )
            dispatch_event = {
                **child_event,
                "hook_event_name": "PreToolUse",
                "tool_name": dispatch_tool,
                "tool_use_id": "codex-manual-dispatch",
                "tool_input": {
                    "root_id": handoff["rootId"],
                    "node_id": loop_node_id("t-service"),
                    "owner": "untrusted-owner",
                    "agent_id": "untrusted-agent",
                    "receiver_context_id": "untrusted-context",
                    "receiver_attestation_id": "untrusted-attestation",
                    "dispatch_mode": "MANUAL",
                    "operation_id": "op-codex-manual",
                },
            }

            completed = self.run_codex_manual_dispatch_hook(
                dispatch_event,
                codex_home,
            )
            output = json.loads(completed.stdout)["hookSpecificOutput"]
            updated = output["updatedInput"]
            claimed = call_tool(
                "dispatch_loop",
                updated,
                root=root,
                trusted_host_adapter="codex",
            )
            heartbeat = self.run_loop_operation_hook(
                {
                    **dispatch_event,
                    "tool_name": (
                        "mcp__plugin_delivery-graph_delivery-graph"
                        "__heartbeat_loop"
                    ),
                    "tool_use_id": "codex-manual-heartbeat",
                    "tool_input": {
                        "root_id": handoff["rootId"],
                        "node_id": loop_node_id("t-service"),
                    },
                },
                codex_home,
            )
            heartbeat_output = json.loads(heartbeat.stdout)[
                "hookSpecificOutput"
            ]
            heartbeat_arguments = dict(heartbeat_output["updatedInput"])
            operation_attestation = heartbeat_arguments.pop(
                "_host_receiver_operation_attestation"
            )
            attested_workspace = SchedulerRepository(
                root
            ).consume_host_workspace_attestation(
                operation_attestation,
                host_adapter_id="codex",
                tool_name="receiver_operation:heartbeat_loop",
            )
            heartbeat_result = call_tool(
                "heartbeat_loop",
                heartbeat_arguments,
                root=root,
                trusted_host_adapter="codex",
                host_receiver_operation_attested=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(output["permissionDecision"], "allow")
        self.assertEqual(updated["agent_id"], "codex")
        self.assertEqual(updated["owner"], "codex-manual-child")
        self.assertEqual(
            updated["receiver_context_id"],
            "codex-manual-child",
        )
        self.assertIn("receiver_attestation_id", updated)
        self.assertTrue(claimed["receiverAttested"])
        self.assertEqual(claimed["dispatchMode"], "MANUAL")
        self.assertEqual(
            claimed["actualModelId"],
            "host-observed-manual-model",
        )
        self.assertEqual(heartbeat_output["permissionDecision"], "allow")
        self.assertEqual(
            heartbeat_output["updatedInput"]["operation_id"],
            claimed["operationId"],
        )
        self.assertIn(
            "_host_receiver_operation_attestation",
            heartbeat_output["updatedInput"],
        )
        self.assertEqual(attested_workspace, str(Path(root).resolve()))
        self.assertEqual(heartbeat_result["nodeId"], loop_node_id("t-service"))

    def test_codex_auto_preflight_fails_before_reservation_without_hook(
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
            arguments = {
                "root_id": prepared["rootId"],
                "expected_graph_fingerprint": prepared[
                    "graphFingerprint"
                ],
            }
            with self.assertRaises(GatedLoopError) as missing_hook:
                call_tool(
                    "plan_dispatch_batch",
                    arguments,
                    root=root,
                    trusted_host_adapter="codex",
                )
            database = Path(root, ".layered-delivery", "scheduler.db")
            connection = sqlite3.connect(database)
            try:
                before = connection.execute(
                    "SELECT COUNT(*) FROM dispatch_reservations"
                ).fetchone()[0]
            finally:
                connection.close()

            hook = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(
                        PLUGIN
                        / "hooks"
                        / "attest_codex_dispatch_preflight.py"
                    ),
                ],
                input=json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": (
                            "mcp__plugin_delivery-graph_delivery-graph"
                            "__plan_dispatch_batch"
                        ),
                        "tool_use_id": "codex-plan-preflight",
                        "session_id": "codex-coordinator",
                        "cwd": root,
                        "tool_input": arguments,
                    }
                ),
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            updated = json.loads(hook.stdout)["hookSpecificOutput"][
                "updatedInput"
            ]
            token = updated.pop("_host_workspace_attestation")
            resolved = SchedulerRepository(
                root
            ).consume_host_workspace_attestation(
                token,
                host_adapter_id="codex",
                tool_name="plan_dispatch_batch",
            )
            planned = call_tool(
                "plan_dispatch_batch",
                updated,
                root=root,
                trusted_host_adapter="codex",
                host_hook_attested=True,
            )

        self.assertEqual(
            missing_hook.exception.code,
            "SCHEDULER_HOST_HOOK_NOT_READY",
        )
        self.assertEqual(before, 0)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(resolved, str(Path(root).resolve()))
        self.assertEqual(planned["assignments"], [])
        self.assertEqual(
            planned["currentSessionTaskNodeIds"],
            ["loop:t-service"],
        )
        self.assertEqual(planned["nextAction"], "CLAIM_CURRENT_TASK")

    def test_codex_start_failure_releases_exact_reservation(self) -> None:
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
                host_adapter_id="codex",
                host_native_agent_ids=("codex",),
            )["assignments"][0]
            wrong_adapter_release = SchedulerRepository(
                root
            ).expire_dispatch_reservation_now(
                assignment["dispatchReservationId"],
                root_id=prepared["rootId"],
                host_adapter_id="claude-code",
                failure_code="WRONG_ADAPTER",
            )
            database = Path(root, ".layered-delivery", "scheduler.db")
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE dispatch_reservations "
                    "SET decision_fingerprint = ? "
                    "WHERE reservation_id = ?",
                    ("0" * 64, assignment["dispatchReservationId"]),
                )
                connection.commit()
            finally:
                connection.close()
            event, codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-failed-start-child",
                model_id="host-model",
                task_name=assignment["hostTaskName"],
                cwd=root,
            )

            completed = self.run_codex_hook(event, codex_home)
            connection = sqlite3.connect(database)
            try:
                status = connection.execute(
                    "SELECT status FROM dispatch_reservations "
                    "WHERE reservation_id = ?",
                    (assignment["dispatchReservationId"],),
                ).fetchone()[0]
                failure_events = connection.execute(
                    "SELECT COUNT(*) FROM graph_events "
                    "WHERE event_type = 'DISPATCH_RECEIVER_START_FAILED'"
                ).fetchone()[0]
            finally:
                connection.close()
            state = graph_status(root=root, root_id=prepared["rootId"])

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("DELIVERY_GRAPH_STARTUP_ERROR=", completed.stdout)
        self.assertFalse(wrong_adapter_release)
        self.assertEqual(status, "EXPIRED")
        self.assertEqual(failure_events, 1)
        task_state = next(
            item
            for item in state["nodes"]
            if item["nodeId"] == assignment["nodeId"]
        )
        self.assertEqual(task_state["status"], "READY")

    def test_codex_reserved_child_role_mismatch_fails_closed(self) -> None:
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
                host_adapter_id="codex",
                host_native_agent_ids=("codex",),
            )["assignments"][0]
            event, codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-role-mismatch-child",
                model_id="host-model",
                task_name=assignment["hostTaskName"],
                cwd=root,
            )
            event["agent_type"] = "reviewer"

            completed = self.run_codex_hook(event, codex_home)
            database = Path(root, ".layered-delivery", "scheduler.db")
            connection = sqlite3.connect(database)
            try:
                reservation_status = connection.execute(
                    "SELECT status FROM dispatch_reservations "
                    "WHERE reservation_id = ?",
                    (assignment["dispatchReservationId"],),
                ).fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "SCHEDULER_CODEX_SUBAGENT_CONTEXT_MISMATCH",
            completed.stdout,
        )
        self.assertIn("Do not inspect or modify", completed.stdout)
        self.assertEqual(reservation_status, "EXPIRED")

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
                host_adapter_id="codex",
                host_native_agent_ids=("codex",),
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
            marker = "DELIVERY_GRAPH_ASSIGNMENT="
            assignment_context = json.loads(
                additional_context.split(marker, maxsplit=1)[1].splitlines()[0]
            )
            self.assertNotIn("receiver_attestation_id", completed.stdout)
            self.assertNotIn("DELIVERY_GRAPH_RECEIVER=", completed.stdout)
            self.assertNotIn("operation_id", assignment_context)
            operation_event = {
                "hook_event_name": "PreToolUse",
                "session_id": hook_event["session_id"],
                "agent_id": hook_event["agent_id"],
                "turn_id": "codex-child-turn",
                "tool_use_id": "codex-child-tool",
                "tool_name": (
                    "mcp__plugin_delivery-graph_delivery-graph"
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
                model_id="helper-observed-model",
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
        self.assertNotIn("modelId", claimed)
        self.assertEqual(
            claimed["actualModelId"],
            "effective-model-from-local-forwarder",
        )
        self.assertEqual(
            claimed["operationId"],
            injected_operation,
        )
        self.assertIn("DELIVERY_GRAPH_ASSIGNMENT=", replayed.stdout)
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

    def test_codex_subagent_start_resolves_child_from_parent_transcript(
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
            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                host_adapter_id="codex",
                host_native_agent_ids=("codex",),
            )["assignments"][0]
            hook_event, hook_codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-child-parent-transcript",
                model_id="host-observed-model",
                task_name=assignment["hostTaskName"],
                cwd=root,
                start_transcript_is_parent=True,
            )

            completed = self.run_codex_hook(
                hook_event,
                hook_codex_home,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("DELIVERY_GRAPH_ASSIGNMENT=", completed.stdout)

    def test_codex_subagent_start_waits_for_direct_child_transcript(
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
            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                host_adapter_id="codex",
                host_native_agent_ids=("codex",),
            )["assignments"][0]
            hook_event, hook_codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-delayed-child-transcript",
                model_id="host-observed-model",
                task_name=assignment["hostTaskName"],
                cwd=root,
            )
            transcript = Path(hook_event["transcript_path"])
            session_meta = transcript.read_text(encoding="utf-8")
            transcript.write_text("", encoding="utf-8")
            writer = threading.Timer(
                0.1,
                transcript.write_text,
                args=(session_meta,),
                kwargs={"encoding": "utf-8"},
            )
            writer.start()
            try:
                completed = self.run_codex_hook(
                    hook_event,
                    hook_codex_home,
                )
            finally:
                writer.join()
            state = graph_status(root=root, root_id=prepared["rootId"])

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("DELIVERY_GRAPH_ASSIGNMENT=", completed.stdout)
        claimed = next(
            item
            for item in state["nodes"]
            if item["nodeId"] == assignment["nodeId"]
        )
        self.assertEqual(claimed["status"], "CLAIMED")

    def test_codex_subagent_start_does_not_require_model_for_identity(
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
            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                host_adapter_id="codex",
                host_native_agent_ids=("codex",),
            )["assignments"][0]
            hook_event, hook_codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-child-without-model",
                model_id=None,
                task_name=assignment["hostTaskName"],
                cwd=root,
            )

            completed = self.run_codex_hook(
                hook_event,
                hook_codex_home,
            )
            state = graph_status(root=root, root_id=prepared["rootId"])

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("DELIVERY_GRAPH_ASSIGNMENT=", completed.stdout)
        claimed = next(
            item
            for item in state["nodes"]
            if item["nodeId"] == loop_node_id("t-service")
        )
        self.assertEqual(claimed["status"], "CLAIMED")
        self.assertIsNone(claimed["actualModelId"])

    def test_codex_hook_trusts_host_profile_across_desktop_sandbox(self) -> None:
        hook_module = runpy.run_path(
            str(
                PLUGIN
                / "hooks"
                / "attest_codex_subagent_receiver.py"
            )
        )
        resolver = hook_module["_trusted_codex_sessions_root"]
        with TemporaryDirectory() as root:
            host_profile = Path(root, "host-profile")
            sessions = host_profile / ".codex" / "sessions"
            sessions.mkdir(parents=True)
            transcript = sessions / "rollout-child.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            sandbox_profile = Path(root, "sandbox-profile")
            with (
                mock.patch.dict(
                    os.environ,
                    {"USERPROFILE": str(host_profile)},
                    clear=False,
                ),
                mock.patch.dict(os.environ, {"CODEX_HOME": ""}),
                mock.patch.object(
                    resolver.__globals__["sys"],
                    "platform",
                    "win32",
                ),
                mock.patch.object(
                    resolver.__globals__["os"],
                    "name",
                    "nt",
                ),
                mock.patch.dict(
                    resolver.__globals__,
                    {"_account_home": lambda: sandbox_profile},
                ),
            ):
                resolved = resolver(str(transcript))

        self.assertEqual(resolved, sessions.resolve())

    def test_codex_hook_rejects_custom_home_even_for_matching_transcript(
        self,
    ) -> None:
        hook_module = runpy.run_path(
            str(
                PLUGIN
                / "hooks"
                / "attest_codex_subagent_receiver.py"
            )
        )
        resolver = hook_module["_trusted_codex_sessions_root"]
        with TemporaryDirectory() as root:
            host_profile = Path(root, "host-profile")
            sessions = host_profile / ".codex" / "sessions"
            sessions.mkdir(parents=True)
            transcript = sessions / "rollout-child.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            sandbox_profile = Path(root, "sandbox-profile")
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "USERPROFILE": str(host_profile),
                        "CODEX_HOME": str(Path(root, "attacker-home")),
                    },
                    clear=False,
                ),
                mock.patch.object(
                    resolver.__globals__["sys"],
                    "platform",
                    "win32",
                ),
                mock.patch.object(
                    resolver.__globals__["os"],
                    "name",
                    "nt",
                ),
                mock.patch.dict(
                    resolver.__globals__,
                    {"_account_home": lambda: sandbox_profile},
                ),
            ):
                with self.assertRaises(OSError):
                    resolver(str(transcript))

    def test_codex_hook_claims_review_for_manual_graph(self) -> None:
        with TemporaryDirectory() as root:
            preview = preview_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
            handoff = create_manual_handoff(
                root=root,
                hierarchy=task_hierarchy(),
                expected_hierarchy_fingerprint=(
                    preview["hierarchyFingerprint"]
                ),
                expected_graph_fingerprint=preview["graphFingerprint"],
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
            )
            start_manual_handoff(
                root=root,
                root_id=handoff["rootId"],
                expected_hierarchy_fingerprint=(
                    handoff["hierarchyFingerprint"]
                ),
                expected_graph_fingerprint=handoff["graphFingerprint"],
                started_by="codex-parent",
                workspace_root=root,
            )
            task_node_id = loop_node_id("t-service")
            runtime_dispatch_loop(
                root=root,
                root_id=handoff["rootId"],
                node_id=task_node_id,
                owner="manual-codex-task",
                agent_id="codex",
                receiver_context_id="manual-codex-task",
                dispatch_mode="MANUAL",
                host_adapter_id="codex",
                operation_id="op-manual-codex-task",
                require_receiver_attestation=False,
            )
            record_loop_result(
                root=root,
                root_id=handoff["rootId"],
                node_id=task_node_id,
                operation_id="op-manual-codex-task",
                outcome={
                    "status": "SUCCEEDED",
                    "summary": "Manual TASK completed.",
                    "result": {},
                },
            )
            assignment = plan_dispatch_batch(
                root=root,
                root_id=handoff["rootId"],
                expected_graph_fingerprint=handoff["graphFingerprint"],
                host_adapter_id="codex",
                host_native_agent_ids=("codex",),
            )["assignments"][0]
            hook_event, hook_codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-manual-review-child",
                model_id="host-observed-review-model",
                task_name=assignment["hostTaskName"],
                cwd=root,
            )

            completed = self.run_codex_hook(
                hook_event,
                hook_codex_home,
            )
            state = graph_status(root=root, root_id=handoff["rootId"])

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("DELIVERY_GRAPH_ASSIGNMENT=", completed.stdout)
        claimed = next(
            item
            for item in state["nodes"]
            if item["nodeId"] == task_review_node_id("t-service")
        )
        self.assertEqual(claimed["status"], "CLAIMED")

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
                host_adapter_id="codex",
                host_native_agent_ids=("codex",),
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
        self.assertIn("DELIVERY_GRAPH_ASSIGNMENT=", completed.stdout)
        claimed = next(
            item
            for item in state["nodes"]
            if item["nodeId"] == loop_node_id("t-service")
        )
        self.assertNotIn("modelId", claimed)
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
                host_adapter_id="codex",
                host_native_agent_ids=("codex",),
            )["assignments"][0]
            helper_event, helper_codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-explorer-child",
                model_id="helper-observed-model",
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
                    model_id="receiver-observed-model",
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
        self.assertIn("DELIVERY_GRAPH_ASSIGNMENT=", intended.stdout)
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
                host_adapter_id="codex",
                host_native_agent_ids=("codex",),
            )["assignments"][0]
            event, fake_codex_home = self.codex_subagent_event(
                root,
                agent_id="codex-forged-child",
                model_id="host-observed-model",
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
        self.assertEqual(tool_count, 34)
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
            34,
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
        self.assertEqual(len(tools), 34)
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
            34,
        )


if __name__ == "__main__":
    unittest.main()
