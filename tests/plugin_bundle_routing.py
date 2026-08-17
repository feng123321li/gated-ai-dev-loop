from __future__ import annotations

from .plugin_bundle_support import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    DASHBOARD_RESOURCE_URI,
    DISPATCH_SKILL,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    MCP_APP_MIME_TYPE,
    MODERN_PROTOCOL_VERSION,
    McpConnection,
    PLANNING_TOOL_PROFILE,
    PLUGIN,
    PLUGIN_SKILL,
    PROTOCOL_VERSION_META_KEY,
    Path,
    ProjectRootBinding,
    REVIEW_SKILL,
    ROOT,
    SKILL,
    SKILL_RUNTIME,
    SOURCE,
    TASK_SKILL,
    TemporaryDirectory,
    _allowed_tools,
    group_hierarchy,
    handle_message,
    hdg,
    io,
    json,
    os,
    patch,
    preview_hierarchy,
    re,
    redirect_stderr,
    runpy,
    subprocess,
    sys,
    tool_definitions,
    tool_names_for_profile,
    unittest,
    validate_hierarchy_definition,
)


class PluginBundleTestsPart1:
    def test_codex_mcp_catalog_and_server_instructions_stay_bounded(
        self,
    ) -> None:
        catalog = json.dumps(
            {"tools": tool_definitions()},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertLessEqual(len(catalog), 144 * 1024)

        with TemporaryDirectory() as root:
            legacy = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": (
                            LEGACY_PREFERRED_PROTOCOL_VERSION
                        ),
                        "capabilities": {},
                        "clientInfo": {
                            "name": "codex-size-test",
                            "version": "1.0.0",
                        },
                    },
                },
                connection=McpConnection(
                    project_root=ProjectRootBinding.from_startup(root),
                    trusted_host_adapter="codex",
                ),
            )
            modern = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "server/discover",
                    "params": {
                        "_meta": {
                            PROTOCOL_VERSION_META_KEY: (
                                MODERN_PROTOCOL_VERSION
                            ),
                            CLIENT_CAPABILITIES_META_KEY: {},
                            CLIENT_INFO_META_KEY: {
                                "name": "codex-size-test",
                                "version": "1.0.0",
                            },
                        }
                    },
                },
                connection=McpConnection(
                    project_root=ProjectRootBinding.from_startup(root),
                    trusted_host_adapter="codex",
                ),
            )

        for response in (legacy, modern):
            instructions = response["result"]["instructions"]
            self.assertLessEqual(
                len(instructions.encode("utf-8")),
                1024,
            )
            self.assertIn("delivery-graph Skill", instructions)

    def test_plugin_omits_legacy_coordinator_and_lifecycle_hooks(
        self,
    ) -> None:
        manifests = [
            json.loads(
                (PLUGIN / relative).read_text(encoding="utf-8")
            )
            for relative in (
                ".codex-plugin/plugin.json",
                ".claude-plugin/plugin.json",
            )
        ]

        self.assertFalse(
            (PLUGIN / "agents" / "delivery-coordinator.md").exists()
        )
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
        self.assertIn("规划阶段 Skill 预触发", planning)
        self.assertIn("方向、边界和验收足够清楚", planning)
        task = (TASK_SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("实现类 Skill 多数应由 `$delivery-graph-task`", main)
        self.assertIn("普通文件名、实现类、内部方法", main)
        self.assertIn("用户明确要求", main)
        self.assertIn("不得把 Skill 默认示例", task)
        self.assertIn("只有阶段不适用或宿主不可用才跳过", task)
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
        protected_tools = {
            "archive_delivery",
            "cancel_graph_run",
            "handoff_ready_automatic_task",
            "rebuild_graph_run",
            "record_user_confirmation",
            "refreeze_task_requirement",
            "unfreeze_task_requirement",
        }
        safe_tools = (
            tool_names_for_profile(PLANNING_TOOL_PROFILE)
            - protected_tools
        )
        expected_allowed_tools = {
            *(
                "mcp__plugin_delivery-graph_delivery-graph__" + name
                for name in safe_tools
            ),
        }
        for document, path in (
            (main, SKILL / "SKILL.md"),
            (plugin_main, PLUGIN_SKILL / "SKILL.md"),
        ):
            self.assertIn("allowed-tools:", document)
            self.assertEqual(
                set(_allowed_tools(path)),
                expected_allowed_tools,
            )
            self.assertNotIn("delivery-graph__*", document)
        self.assertNotIn("`recommend_executors`", main + recommendations)
        self.assertIn("默认使用 `STANDARD`", planning)
        self.assertIn("Controller 不分析 Loop", recommendations)
        self.assertIn("手动开发生成完整冻结内容包", planning)
        self.assertIn(
            ".layered-delivery/<delivery-id>/handoff-<fingerprint>.md",
            planning,
        )
        self.assertIn(
            "不得创建跨需求共享的 `.layered-delivery/handoffs/`",
            planning,
        )
        self.assertIn("不指定 Agent 或接收任务", planning)
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
        self.assertIn("`workspaceProvenance`", planning)
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
        self.assertIn("内部 helper", recommendations)
        self.assertIn("不是 Graph receiver", recommendations)
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
        self.assertIn("固定并发槽位", execution)
        self.assertIn("Controller 不判断供应商额度", recommendations)
        for removed_contract in (
            "recommend_assurance_profile",
            "modelPolicy",
            "reasoningEffort",
            "workerTelemetry",
            "quotaExhaustionPolicy",
        ):
            self.assertNotIn(removed_contract, contract)
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
            "taskSplitIntegrityPreflight",
            "SCHEDULER_TASK_REQUIREMENT_RESERVATION_ACTIVE",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, main + planning + execution)
        self.assertIn("跨 Delivery", main + planning + execution)
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
        dispatch = (DISPATCH_SKILL / "SKILL.md").read_text(encoding="utf-8")
        task = (TASK_SKILL / "SKILL.md").read_text(encoding="utf-8")
        review = (REVIEW_SKILL / "SKILL.md").read_text(encoding="utf-8")

        self.assertLessEqual(len(readme.splitlines()), 200)
        self.assertLessEqual(len(main.splitlines()), 160)
        self.assertNotIn("```json", readme)
        for reference, document in (
            ("planning-quickstart.md", main),
            ("mcp-transport.md", main),
            ("dispatch-and-recovery.md", dispatch),
            ("task-execution.md", task),
            ("acceptance.md", review),
        ):
            with self.subTest(reference=reference):
                self.assertIn(reference, document)

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
