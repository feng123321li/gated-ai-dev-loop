from __future__ import annotations

from .scheduler_runtime_support import (
    GRAPH_COMPILER_CONTRACT,
    GatedLoopError,
    Path,
    SchedulerRepository,
    at,
    create_manual_handoff,
    deepcopy,
    delivery_task_hierarchy,
    dispatch_loop,
    freeze_hierarchy,
    get_graph_frontier,
    group_hierarchy,
    group_review_node_id,
    loop_node_id,
    prepare_hierarchy,
    preview_hierarchy,
    rebuild_graph_run,
    record_loop_result,
    review_node_id,
    runtime_dispatch_loop,
    skill_hint,
    sqlite3,
    start_manual_handoff,
    success,
    success_for_node,
    task_review_node_id,
    workspace_status,
)


class SchedulerRuntimeTestsPart2:
    def test_manual_handoff_materializes_development_bundle_without_starting(
        self,
    ) -> None:
        first = prepare_hierarchy(
            root=self.root,
            hierarchy=delivery_task_hierarchy("d-first", "t-first"),
            now=at(0),
        )
        freeze_hierarchy(
            root=self.root,
            root_id=first["rootId"],
            expected_hierarchy_fingerprint=(
                first["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        hierarchy = delivery_task_hierarchy("d-second", "t-second")
        hierarchy["root"]["definition"]["execution"]["loop"][
            "payload"
        ]["goal"] = "实现第二个独立需求并完成验证。"

        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(2),
        )
        handoff = create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(3),
        )

        self.assertEqual(preview["status"], "CHOICE_READY")
        self.assertEqual(
            preview["nextAction"],
            "PRESENT_HOST_NATIVE_EXECUTION_CHOICE",
        )
        self.assertEqual(handoff["status"], "QUEUED")
        self.assertEqual(handoff["deliveryStatus"], "HANDOFF_READY")
        self.assertEqual(
            handoff["deliveryQueue"]["continuation"]["tool"],
            "start_manual_handoff",
        )
        self.assertEqual(
            handoff["requirementSnapshotStatus"],
            "FROZEN",
        )
        self.assertEqual(
            handoff["nextAction"],
            "WAIT_FOR_MANUAL_QUEUE_TURN",
        )
        self.assertTrue(handoff["controlStateCreated"])
        self.assertFalse(handoff["graphRunCreated"])
        self.assertFalse(handoff["workspaceCreated"])
        self.assertTrue(handoff["workspaceBound"])
        self.assertEqual(
            set(handoff["manualHandoff"]),
            {"path", "format", "selfContained", "receiverPrompt"},
        )
        self.assertEqual(handoff["manualHandoff"]["format"], "MARKDOWN")
        self.assertTrue(handoff["manualHandoff"]["selfContained"])
        receiver_prompt = handoff["manualHandoff"]["receiverPrompt"]
        self.assertIn(handoff["manualHandoff"]["path"], receiver_prompt)
        self.assertIn("完整读取", receiver_prompt)
        self.assertIn("start_manual_handoff", receiver_prompt)
        self.assertIn(
            "GROUP seam Review 和 Delivery Acceptance/Readiness",
            receiver_prompt,
        )
        self.assertIn("不要重新规划", receiver_prompt)

        handoff_root = Path(
            self.root,
            ".layered-delivery",
            "d-second",
        )
        files = list(handoff_root.glob("handoff-*.md"))
        self.assertEqual(len(files), 1)
        self.assertEqual(
            {path.name for path in handoff_root.iterdir()},
            {
                "acceptance.md",
                "baseline.md",
                files[0].name,
                "overview.md",
                "progress.md",
                "revisions.md",
                "work-items",
            },
        )
        self.assertEqual(
            files[0].relative_to(Path(self.root)).as_posix(),
            handoff["manualHandoff"]["path"],
        )
        self.assertEqual(
            set(handoff["humanArtifacts"]),
            {
                "workspaceOverview",
                "overview",
                "baseline",
                "progress",
                "acceptance",
                "revisions",
                "taskBaselines",
                "workItems",
            },
        )
        for artifact_name in (
            "workspaceOverview",
            "overview",
            "baseline",
            "progress",
            "acceptance",
            "revisions",
        ):
            with self.subTest(artifact=artifact_name):
                self.assertTrue(
                    Path(
                        self.root,
                        handoff["humanArtifacts"][artifact_name],
                    ).is_file()
                )
        task_artifacts = handoff["humanArtifacts"]["workItems"][
            "t-second"
        ]
        self.assertEqual(task_artifacts["kind"], "TASK")
        for artifact_name in ("baseline", "progress", "acceptance"):
            with self.subTest(task_artifact=artifact_name):
                self.assertTrue(
                    Path(
                        self.root,
                        task_artifacts[artifact_name],
                    ).is_file()
                )
        self.assertFalse(
            Path(self.root, ".layered-delivery", "handoffs").exists()
        )
        content = files[0].read_text(encoding="utf-8")
        self.assertIn("## 接收 CLI 启动提示词", content)
        self.assertIn(receiver_prompt, content)
        for expected in (
            "# 开发内容交接",
            "d-second",
            "t-second",
            "实现第二个独立需求并完成验证。",
            preview["hierarchyFingerprint"],
            preview["graphFingerprint"],
            GRAPH_COMPILER_CONTRACT,
            "交接前不指定",
            "已绑定当前物理 workspace 的串行队列",
            "需求内容快照已冻结",
            "切换到任意 CLI",
            "start_manual_handoff",
            "TASK Review、已配置的 GROUP seam Review",
            '"id": "d-second"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, content)
        for forbidden in (
            "目标开发 Agent",
            "Codex",
            "Claude Code",
            "glm-5.2",
            "gpt-5.6",
            "prepare_hierarchy",
            "freeze_hierarchy",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, content)

        for projection_name in (
            "overview.md",
            "baseline.md",
            "progress.md",
            "acceptance.md",
        ):
            with self.subTest(projection=projection_name):
                projection = Path(
                    handoff_root,
                    projection_name,
                ).read_text(encoding="utf-8")
                self.assertIn(
                    "排队中（等待工作区串行调度）",
                    projection,
                )
        revisions = Path(
            handoff_root,
            "revisions.md",
        ).read_text(encoding="utf-8")
        self.assertIn("HANDOFF\\_READY", revisions)
        self.assertIn("已冻结，未创建 Graph Run", revisions)

        active = workspace_status(root=self.root)
        self.assertEqual(active["status"], "DELIVERY_SELECTION_REQUIRED")
        self.assertEqual(
            sorted(
                item["rootId"]
                for item in active["candidateDeliveries"]
            ),
            ["d-first", "d-second"],
        )
        stored_manual = SchedulerRepository(self.root).hierarchy(
            "d-second"
        )
        self.assertEqual(stored_manual["status"], "HANDOFF_READY")
        self.assertEqual(
            stored_manual["hierarchyFingerprint"],
            preview["hierarchyFingerprint"],
        )
        connection = sqlite3.connect(
            Path(self.root, ".layered-delivery", "scheduler.db")
        )
        try:
            run_count = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE root_id = ?",
                ("d-second",),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(run_count, 0)
        workspace_overview = Path(
            self.root,
            ".layered-delivery",
            "overview.md",
        ).read_text(encoding="utf-8")
        self.assertIn("未归档交付数量：2", workspace_overview)
        self.assertIn("d-first", workspace_overview)
        self.assertIn("d-second", workspace_overview)
        self.assertIn(
            "排队中（等待工作区串行调度）",
            workspace_overview,
        )

    def test_manual_handoff_runs_the_complete_standard_review_graph(
        self,
    ) -> None:
        hierarchy = group_hierarchy()
        hierarchy["root"]["skillHints"] = [
            skill_hint(
                "springboot-tdd",
                "Prefer TDD when the current manual TASK is Spring Boot work.",
            )
        ]
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        handoff = create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )

        started = start_manual_handoff(
            root=self.root,
            root_id=handoff["rootId"],
            expected_hierarchy_fingerprint=(
                handoff["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=handoff["graphFingerprint"],
            started_by="manual-orchestrator",
            workspace_root=self.root,
            now=at(2),
        )

        self.assertEqual(started["status"], "ACTIVE")
        self.assertEqual(started["executionMode"], "manual")
        self.assertTrue(started["graphRunCreated"])
        self.assertEqual(started["nextAction"], "READ_GRAPH_FRONTIER")
        repeated_start = start_manual_handoff(
            root=self.root,
            root_id=handoff["rootId"],
            expected_hierarchy_fingerprint=(
                handoff["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=handoff["graphFingerprint"],
            started_by="manual-orchestrator",
            workspace_root=self.root,
            now=at(2),
        )
        self.assertTrue(repeated_start["manualStartAlreadyApplied"])
        self.assertEqual(repeated_start["runId"], started["runId"])

        expected_steps = [
            ("CLAIM_MANUAL_TASK", loop_node_id("t-api")),
            ("DISPATCH_LOOP", task_review_node_id("t-api")),
            ("CLAIM_MANUAL_TASK", loop_node_id("t-core")),
            ("DISPATCH_LOOP", task_review_node_id("t-core")),
            ("DISPATCH_LOOP", group_review_node_id("g-service")),
            ("DISPATCH_LOOP", review_node_id("d-service")),
        ]
        for index, (action_name, node_id) in enumerate(
            expected_steps,
            start=3,
        ):
            with self.subTest(action=action_name, node=node_id):
                frontier = get_graph_frontier(
                    root=self.root,
                    root_id=handoff["rootId"],
                    now=at(index),
                )
                self.assertEqual(
                    [
                        (action["action"], action.get("nodeId"))
                        for action in frontier["actions"]
                    ],
                    [(action_name, node_id)],
                )
                operation_id = f"op-manual-graph-{index}"
                receiver_context_id = f"context-manual-graph-{index}"
                if action_name == "CLAIM_MANUAL_TASK":
                    self.assertEqual(
                        frontier["actions"][0]["skillHints"],
                        hierarchy["root"]["skillHints"],
                    )
                    self.assertIn(
                        "`$springboot-tdd`",
                        frontier["actions"][0]["receiverPrompt"],
                    )
                    runtime_dispatch_loop(
                        root=self.root,
                        root_id=handoff["rootId"],
                        node_id=node_id,
                        owner=f"manual-task-{index}",
                        agent_id="claude-code",
                        receiver_context_id=receiver_context_id,
                        dispatch_mode="MANUAL",
                        operation_id=operation_id,
                        now=at(index),
                    )
                    outcome = success("Manual TASK completed.")
                else:
                    dispatch_loop(
                        root=self.root,
                        root_id=handoff["rootId"],
                        node_id=node_id,
                        owner=f"review-agent-{index}",
                        receiver_context_id=receiver_context_id,
                        operation_id=operation_id,
                        now=at(index),
                    )
                    outcome = success_for_node(
                        node_id,
                        "Independent Review completed.",
                    )
                record_loop_result(
                    root=self.root,
                    root_id=handoff["rootId"],
                    node_id=node_id,
                    operation_id=operation_id,
                    outcome=outcome,
                    now=at(index),
                )

        rebuilt = rebuild_graph_run(
            root=self.root,
            root_id=handoff["rootId"],
        )
        self.assertEqual(rebuilt["executionMode"], "manual")
        frontier = get_graph_frontier(
            root=self.root,
            root_id=handoff["rootId"],
            now=at(10),
        )
        self.assertEqual(
            [action["action"] for action in frontier["actions"]],
            ["RECORD_USER_CONFIRMATION"],
        )
        self.assertEqual(
            SchedulerRepository(self.root).run(handoff["rootId"])[
                "executionMode"
            ],
            "manual",
        )

    def test_manual_handoff_start_recovers_interrupted_prepared_adoption(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy(
            "d-manual-interrupted",
            "t-manual-interrupted",
        )
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        handoff = create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            workspace_root=self.root,
            now=at(2),
        )
        self.assertEqual(prepared["status"], "PREPARED")

        started = start_manual_handoff(
            root=self.root,
            root_id=handoff["rootId"],
            expected_hierarchy_fingerprint=(
                handoff["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=handoff["graphFingerprint"],
            started_by="manual-orchestrator",
            workspace_root=self.root,
            now=at(3),
        )

        self.assertEqual(started["executionMode"], "manual")
        self.assertFalse(started["manualStartAlreadyApplied"])
        self.assertEqual(
            get_graph_frontier(
                root=self.root,
                root_id=handoff["rootId"],
                now=at(4),
            )["actions"][0]["action"],
            "CLAIM_MANUAL_TASK",
        )

    def test_manual_graph_rejects_manual_review_and_automatic_task_claims(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy("d-manual-run", "t-manual-run")
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        handoff = create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        start_manual_handoff(
            root=self.root,
            root_id=handoff["rootId"],
            expected_hierarchy_fingerprint=(
                handoff["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=handoff["graphFingerprint"],
            started_by="manual-orchestrator",
            workspace_root=self.root,
            now=at(2),
        )
        task_node_id = loop_node_id("t-manual-run")

        with self.assertRaises(GatedLoopError) as automatic_error:
            dispatch_loop(
                root=self.root,
                root_id=handoff["rootId"],
                node_id=task_node_id,
                owner="automatic-task",
                operation_id="op-automatic-task",
                now=at(3),
            )
        self.assertEqual(
            automatic_error.exception.code,
            "SCHEDULER_DISPATCH_MODE_INVALID",
        )

        runtime_dispatch_loop(
            root=self.root,
            root_id=handoff["rootId"],
            node_id=task_node_id,
            owner="manual-task",
            agent_id="claude-code",
            receiver_context_id="context-manual-task",
            dispatch_mode="MANUAL",
            operation_id="op-manual-task",
            now=at(3),
        )
        record_loop_result(
            root=self.root,
            root_id=handoff["rootId"],
            node_id=task_node_id,
            operation_id="op-manual-task",
            outcome=success(),
            now=at(4),
        )

        with self.assertRaises(GatedLoopError) as manual_review_error:
            runtime_dispatch_loop(
                root=self.root,
                root_id=handoff["rootId"],
                node_id=task_review_node_id("t-manual-run"),
                owner="manual-review",
                agent_id="claude-code",
                receiver_context_id="context-manual-review",
                dispatch_mode="MANUAL",
                operation_id="op-manual-review",
                now=at(5),
            )
        self.assertEqual(
            manual_review_error.exception.code,
            "SCHEDULER_DISPATCH_MODE_INVALID",
        )

    def test_preview_rejects_duplicate_external_requirement_under_new_id(
        self,
    ) -> None:
        original = delivery_task_hierarchy(
            "d-mprotein-443-original",
            "t-mprotein-443-original",
        )
        original["delivery"]["title"] = (
            "MPROTEIN-443 移除蛋白上机原样剩余信息"
        )
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=original,
            now=at(0),
        )
        create_manual_handoff(
            root=self.root,
            hierarchy=original,
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )

        duplicate = delivery_task_hierarchy(
            "mprotein-443-regenerated",
            "t-mprotein-443-regenerated",
        )
        duplicate["delivery"]["title"] = (
            "退役蛋白上机样品原样剩余量字段（MPROTEIN-443）"
        )
        with self.assertRaises(GatedLoopError) as caught:
            preview_hierarchy(
                root=self.root,
                hierarchy=duplicate,
                now=at(2),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DELIVERY_REQUIREMENT_CONFLICT",
        )
        self.assertEqual(
            caught.exception.details["requirementKey"],
            "MPROTEIN-443",
        )
        self.assertEqual(
            caught.exception.details["existingRootId"],
            "d-mprotein-443-original",
        )
        self.assertEqual(
            caught.exception.details["requestedRootId"],
            "mprotein-443-regenerated",
        )
        self.assertEqual(
            caught.exception.details["nextAction"],
            "REUSE_EXISTING_DELIVERY_ID_AND_CREATE_REVISION",
        )
        self.assertFalse(
            Path(
                self.root,
                ".layered-delivery",
                "mprotein-443-regenerated",
            ).exists()
        )

    def test_choice_ready_registration_prevents_stale_requirement_race(
        self,
    ) -> None:
        original = delivery_task_hierarchy("d-original", "t-original")
        original["delivery"]["requirementKey"] = "MPROTEIN-443"
        duplicate = delivery_task_hierarchy("d-duplicate", "t-duplicate")
        duplicate["delivery"]["requirementKey"] = "mprotein-443"
        duplicate_preview = preview_hierarchy(
            root=self.root,
            hierarchy=duplicate,
            now=at(0),
        )
        with self.assertRaises(GatedLoopError) as caught:
            preview_hierarchy(
                root=self.root,
                hierarchy=original,
                now=at(1),
            )

        self.assertEqual(duplicate_preview["status"], "CHOICE_READY")
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DELIVERY_REQUIREMENT_CONFLICT",
        )
        self.assertEqual(
            caught.exception.details["existingRootId"],
            "d-duplicate",
        )
        self.assertTrue(
            Path(
                self.root,
                ".layered-delivery",
                "d-duplicate",
            ).exists()
        )
        self.assertFalse(
            Path(
                self.root,
                ".layered-delivery",
                "d-original",
            ).exists()
        )

    def test_preview_rejects_requirement_key_change_for_same_delivery(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy("d-stable", "t-stable")
        hierarchy["delivery"]["requirementKey"] = "MPROTEIN-443"
        first_preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                first_preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=(
                first_preview["graphFingerprint"]
            ),
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        revised = deepcopy(hierarchy)
        revised["delivery"]["requirementKey"] = "MPROTEIN-444"

        with self.assertRaises(GatedLoopError) as caught:
            preview_hierarchy(
                root=self.root,
                hierarchy=revised,
                now=at(2),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DELIVERY_REQUIREMENT_KEY_IMMUTABLE",
        )
        self.assertEqual(
            caught.exception.details["existingRequirementKey"],
            "MPROTEIN-443",
        )
        self.assertEqual(
            caught.exception.details["requestedRequirementKey"],
            "MPROTEIN-444",
        )

    def test_manual_handoff_revision_reuses_the_original_directory(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy(
            "d-mprotein-445-reminder",
            "t-mprotein-445-reminder",
        )
        hierarchy["delivery"]["title"] = (
            "MPROTEIN-445 系统公共异常暂停提醒"
        )
        first_preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        first = create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                first_preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=(
                first_preview["graphFingerprint"]
            ),
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )

        revised = deepcopy(hierarchy)
        revised["delivery"]["title"] = (
            "MPROTEIN-445 当前菜单异常暂停提醒"
        )
        revised["delivery"]["summary"] = (
            "按当前菜单参数统计系统公共异常暂停数量。"
        )
        revised["root"]["definition"]["execution"]["loop"][
            "payload"
        ]["goal"] = "增加必填 taskCurrStep 参数并按当前菜单统计。"
        second_preview = preview_hierarchy(
            root=self.root,
            hierarchy=revised,
            now=at(2),
        )
        second = create_manual_handoff(
            root=self.root,
            hierarchy=revised,
            expected_hierarchy_fingerprint=(
                second_preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=(
                second_preview["graphFingerprint"]
            ),
            authorized_project_ids=[],
            expected_current_revision=1,
            continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
            revision_reason="用户把系统范围统计调整为当前菜单统计。",
            confirmed=True,
            confirmed_by="human",
            now=at(3),
        )

        self.assertEqual(first["deliveryRevision"], 1)
        self.assertEqual(second["deliveryRevision"], 2)
        self.assertEqual(second["previousRevision"], 1)
        self.assertEqual(
            second["rootId"],
            "d-mprotein-445-reminder",
        )
        history = SchedulerRepository(self.root).revision_history(
            second["rootId"]
        )
        self.assertEqual(history["currentRevision"], 2)
        self.assertEqual(
            [item["status"] for item in history["revisions"]],
            ["SUPERSEDED", "HANDOFF_READY"],
        )
        self.assertEqual(
            history["revisions"][1]["continuityBasis"],
            "USER_EXPLICIT_SAME_DELIVERY",
        )
        self.assertEqual(
            history["revisions"][1]["reason"],
            "用户把系统范围统计调整为当前菜单统计。",
        )
        handoff_root = Path(
            self.root,
            ".layered-delivery",
            second["rootId"],
        )
        self.assertEqual(len(list(handoff_root.glob("handoff-*.md"))), 2)
        self.assertFalse(
            Path(
                self.root,
                ".layered-delivery",
                "d-mprotein-445-current-menu-reminder",
            ).exists()
        )
        revisions = (handoff_root / "revisions.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "| 1 | 已被新修订取代（SUPERSEDED） |",
            revisions,
        )
        self.assertIn(
            "| 2 | 需求已冻结（手动开发，调度未启动）"
            "（HANDOFF\\_READY） |",
            revisions,
        )

    def test_changed_manual_handoff_requires_explicit_revision_continuity(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy(
            "d-mprotein-445",
            "t-mprotein-445",
        )
        first_preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                first_preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=(
                first_preview["graphFingerprint"]
            ),
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        revised = deepcopy(hierarchy)
        revised["delivery"]["summary"] = "修订后的需求范围。"
        second_preview = preview_hierarchy(
            root=self.root,
            hierarchy=revised,
            now=at(2),
        )

        with self.assertRaises(GatedLoopError) as caught:
            create_manual_handoff(
                root=self.root,
                hierarchy=revised,
                expected_hierarchy_fingerprint=(
                    second_preview["hierarchyFingerprint"]
                ),
                expected_graph_fingerprint=(
                    second_preview["graphFingerprint"]
                ),
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
                now=at(3),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_MANUAL_REVISION_CONTINUITY_REQUIRED",
        )
