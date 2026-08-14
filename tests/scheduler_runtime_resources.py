from __future__ import annotations

from .scheduler_runtime_support import (
    GatedLoopError,
    Path,
    WORK_ITEM_DIRECTORY,
    at,
    call_tool,
    delivery_task_hierarchy,
    dispatch_loop,
    freeze_hierarchy,
    get_graph_frontier,
    graph_events,
    graph_status,
    group_hierarchy,
    group_review_node_id,
    loop_context,
    loop_node_id,
    parallel_hierarchy,
    plan_dispatch_batch,
    prepare_hierarchy,
    rebuild_graph_run,
    record_loop_result,
    reserve_loop,
    review_success,
    sqlite3,
    success,
    task_hierarchy,
)


class SchedulerRuntimeTestsPart6:
    def test_task_work_item_progress_and_acceptance_follow_run_state(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        item_root = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / WORK_ITEM_DIRECTORY
            / "t-service"
        )
        baseline_before = (item_root / "baseline.md").read_bytes()

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-task",
            agent_id="codex",
            operation_id="op-task-projection",
            now=at(2),
        )
        claimed = graph_status(root=self.root, root_id=root_id)
        claimed_node = next(
            item
            for item in claimed["nodes"]
            if item["nodeId"] == node_id
        )
        self.assertEqual(claimed_node["agentId"], "codex")
        self.assertNotIn("modelId", claimed_node)

        running_progress = (item_root / "progress.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            (
                "| TASK | 执行中 | codex | 未报告 | "
                "agent-task | 1 |"
            ),
            running_progress,
        )

        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-task-projection",
            outcome={
                "status": "SUCCEEDED",
                "summary": "任务实现与验证已完成。",
                "result": {"evidence": "全部自动化检查通过"},
            },
            now=at(3),
        )
        rebuild_graph_run(root=self.root, root_id=root_id)
        rebuilt = graph_status(root=self.root, root_id=root_id)
        rebuilt_node = next(
            item
            for item in rebuilt["nodes"]
            if item["nodeId"] == node_id
        )
        self.assertEqual(rebuilt_node["agentId"], "codex")
        self.assertNotIn("modelId", rebuilt_node)

        completed_progress = (item_root / "progress.md").read_text(
            encoding="utf-8"
        )
        acceptance = (item_root / "acceptance.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            (
                "| 阶段 | 当前进度 | 执行代理 | 宿主观测模型 | "
                "认领身份 | 执行轮次 | "
                "最近更新时间（UTC+8） | 结果摘要 |"
            ),
            completed_progress,
        )
        self.assertIn(
            (
                "| TASK | 已成功 | codex | 未报告 | "
                "agent-task | 1 |"
            ),
            completed_progress,
        )
        self.assertNotIn("\n- 当前进度：", completed_progress)
        self.assertIn("任务实现与验证已完成。", completed_progress)
        self.assertIn(
            (
                "| 当前进度 | 认领身份 | 执行轮次 | "
                "结束时间（UTC+8） | 结果摘要 |"
            ),
            acceptance,
        )
        self.assertIn("全部自动化检查通过", acceptance)
        self.assertEqual(
            (item_root / "baseline.md").read_bytes(),
            baseline_before,
        )

    def test_review_findings_are_classified_in_acceptance_reports(
        self,
    ) -> None:
        hierarchy = group_hierarchy()
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]
        for minute, item_id in ((2, "t-api"), (6, "t-core")):
            node_id = loop_node_id(item_id)
            operation_id = f"op-{item_id}-severity"
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                owner="task-agent",
                receiver_context_id=f"context-{item_id}-severity",
                operation_id=operation_id,
                now=at(minute),
            )
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id=operation_id,
                outcome=success(f"{item_id} completed."),
                now=at(minute + 1),
            )
            task_review_id = f"review:task:{item_id}"
            task_review_operation = f"op-{item_id}-task-review-severity"
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=task_review_id,
                owner="task-review-agent",
                receiver_context_id=(
                    f"context-{item_id}-task-review-severity"
                ),
                operation_id=task_review_operation,
                now=at(minute + 2),
            )
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=task_review_id,
                operation_id=task_review_operation,
                outcome=review_success(
                    "TASK_REVIEW_LOOP",
                    f"{item_id} task review completed.",
                ),
                now=at(minute + 3),
            )

        review_id = group_review_node_id("g-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=review_id,
            owner="review-agent",
            receiver_context_id="context-group-review-severity",
            operation_id="op-review-severity",
            now=at(10),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=review_id,
            operation_id="op-review-severity",
            outcome=review_success(
                "GROUP_REVIEW_LOOP",
                "P0/P1 已修复，P2 已记录。",
                findings=[
                    {
                        "severity": "P0",
                        "summary": "关键数据可能丢失",
                        "status": "RESOLVED",
                        "resolution": "修复字段映射并完成回归。",
                        "evidence": "数据链路测试通过",
                    },
                    {
                        "severity": "P1",
                        "summary": "异常分支缺少覆盖",
                        "status": "RESOLVED",
                        "resolution": "补充异常测试并复审。",
                        "evidence": "新增测试通过",
                    },
                    {
                        "severity": "P2",
                        "summary": "导出任务日志不足",
                        "status": "ACCEPTED",
                        "resolution": "作为非阻断改进项保留。",
                        "evidence": "不影响本次验收",
                    },
                ],
            ),
            now=at(11),
        )

        group_acceptance = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / WORK_ITEM_DIRECTORY
            / "g-service"
            / "acceptance.md"
        ).read_text(encoding="utf-8")
        delivery_acceptance = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / "acceptance.md"
        ).read_text(encoding="utf-8")

        self.assertIn("#### Review 问题分级", group_acceptance)
        self.assertIn("- P0：1 项，未关闭 0 项", group_acceptance)
        self.assertIn("- P1：1 项，未关闭 0 项", group_acceptance)
        self.assertIn(
            "- P2：1 项（必须逐项列示）",
            group_acceptance,
        )
        self.assertIn(
            "| 级别 | 问题 | 状态 | 处置 | 证据 |",
            group_acceptance,
        )
        self.assertIn("关键数据可能丢失", group_acceptance)
        self.assertIn("异常分支缺少覆盖", group_acceptance)
        self.assertIn("导出任务日志不足", group_acceptance)
        self.assertIn("已修复", group_acceptance)
        self.assertIn("已接受", group_acceptance)
        self.assertEqual(
            group_acceptance.count("导出任务日志不足"),
            1,
        )
        self.assertIn(
            "[查看](children/t-api/acceptance.md)",
            group_acceptance,
        )
        self.assertIn(
            "[查看](children/t-core/acceptance.md)",
            group_acceptance,
        )
        self.assertNotIn("opaque-to-scheduler", group_acceptance)

        self.assertIn("## 根工作项验收", delivery_acceptance)
        self.assertIn(
            "P0/P1 已修复，P2 已记录。",
            delivery_acceptance,
        )
        self.assertIn(
            f"[查看]({WORK_ITEM_DIRECTORY}/g-service/acceptance.md)",
            delivery_acceptance,
        )
        self.assertNotIn("关键数据可能丢失", delivery_acceptance)
        self.assertNotIn("异常分支缺少覆盖", delivery_acceptance)
        self.assertNotIn("导出任务日志不足", delivery_acceptance)

    def test_infrastructure_failure_retries_but_loop_block_does_not(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-1",
            operation_id="op-infra-1",
            now=at(2),
        )
        result = record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-infra-1",
            outcome={
                "status": "BLOCKED",
                "summary": "Worker transport failed.",
                "result": {},
            },
            failure_class="RETRYABLE_INFRA",
            now=at(3),
        )
        self.assertTrue(result["retried"])
        self.assertEqual(result["nextAttempt"], 2)
        self.assertEqual(result["schedulerStatus"], "READY")

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-2",
            operation_id="op-domain-2",
            now=at(4),
        )
        result = record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-domain-2",
            outcome={
                "status": "BLOCKED",
                "summary": "Loop needs external authority.",
                "result": {"request": "approve vendor contract"},
            },
            failure_class="EXTERNAL_AUTHORITY",
            now=at(5),
        )
        self.assertFalse(result["retried"])
        self.assertEqual(result["schedulerStatus"], "BLOCKED")
        self.assertEqual(
            graph_status(
                root=self.root,
                root_id=root_id,
            )["status"],
            "BLOCKED",
        )

    def test_retry_readiness_uses_latest_attempt_with_desc_index(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        database = Path(
            self.root,
            ".layered-delivery",
            "scheduler.db",
        )
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "CREATE INDEX test_node_runs_latest_attempt_desc "
                "ON node_runs(run_id, node_id, attempt DESC, status)"
            )
            run_id = connection.execute(
                "SELECT run_id FROM runs WHERE root_id = ?",
                (root_id,),
            ).fetchone()[0]
            query_plan = connection.execute(
                "EXPLAIN QUERY PLAN "
                "SELECT node_id, attempt, status FROM node_runs "
                "WHERE run_id = ? ORDER BY node_id",
                (run_id,),
            ).fetchall()
            connection.commit()
        finally:
            connection.close()
        self.assertTrue(
            any(
                "USING COVERING INDEX "
                "test_node_runs_latest_attempt_desc" in row[3]
                for row in query_plan
            ),
            query_plan,
        )

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-desc-index",
            operation_id="op-desc-index-attempt-1",
            now=at(2),
        )
        result = record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-desc-index-attempt-1",
            outcome={
                "status": "BLOCKED",
                "summary": "Worker transport failed.",
                "result": {},
            },
            failure_class="RETRYABLE_INFRA",
            now=at(3),
        )

        self.assertTrue(result["retried"])
        self.assertEqual(result["nextAttempt"], 2)
        self.assertEqual(result["schedulerStatus"], "READY")
        retry_ready_events = [
            event
            for event in graph_events(
                root=self.root,
                root_id=root_id,
            )["events"]
            if event["eventType"] == "NODE_READY"
            and event["nodeId"] == node_id
            and event["attempt"] == 2
        ]
        self.assertEqual(len(retry_ready_events), 1)

    def test_blocked_outcome_requires_explicit_failure_class(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="review-agent",
            operation_id="op-premature-block",
            now=at(2),
        )

        with self.assertRaises(GatedLoopError) as caught:
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-premature-block",
                outcome={
                    "status": "BLOCKED",
                    "summary": "A correctable Review finding remains.",
                    "result": {"finding": "implementation defect"},
                },
                now=at(3),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_FAILURE_CLASS_REQUIRED",
        )
        self.assertIn(
            "internal correction and reevaluation",
            caught.exception.message,
        )
        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
        )
        self.assertEqual(context["status"], "CLAIMED")

    def test_resource_claims_serialize_independent_loops(self) -> None:
        prepared = self.prepare_and_freeze(parallel_hierarchy())
        root_id = prepared["rootId"]
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-api"),
            owner="agent-api",
            operation_id="op-api",
            now=at(2),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(3),
        )
        core = next(
            item
            for item in frontier["readyLoops"]
            if item["nodeId"] == loop_node_id("t-core")
        )
        self.assertEqual(
            core["resourceConflicts"],
            [loop_node_id("t-api")],
        )
        self.assertNotIn(
            loop_node_id("t-core"),
            [
                item.get("nodeId")
                for item in frontier["actions"]
                if item["action"] == "DISPATCH_LOOP"
            ],
        )
        with self.assertRaises(GatedLoopError):
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=loop_node_id("t-core"),
                owner="agent-core",
                operation_id="op-core",
                now=at(3),
            )

    def test_resource_claims_serialize_loops_across_deliveries(self) -> None:
        first_workspace = Path(self.root, "worktree-first")
        second_workspace = Path(self.root, "worktree-second")
        first_workspace.mkdir()
        second_workspace.mkdir()
        claim = ["project:erp/environment:shared"]
        for delivery_id, task_id, workspace in (
            ("d-first", "t-first", first_workspace),
            ("d-second", "t-second", second_workspace),
        ):
            current = call_tool(
                "prepare_hierarchy",
                {
                    "hierarchy": delivery_task_hierarchy(
                        delivery_id,
                        task_id,
                        claims=claim,
                    )
                },
                root=self.root,
                workspace_root=str(workspace),
            )
            freeze_hierarchy(
                root=self.root,
                root_id=current["rootId"],
                workspace_root=str(workspace),
                expected_delivery_revision=1,
                expected_hierarchy_fingerprint=(
                    current["hierarchyFingerprint"]
                ),
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
            )

        first_reservation = reserve_loop(
            root=self.root,
            root_id="d-first",
            node_id=loop_node_id("t-first"),
        )
        first_claim = call_tool(
            "dispatch_loop",
            {
                "root_id": "d-first",
                "node_id": loop_node_id("t-first"),
                "owner": "agent-first",
                "agent_id": first_reservation["agentId"],
                "dispatch_mode": first_reservation["dispatchMode"],
                "dispatch_transport": first_reservation[
                    "dispatchTransport"
                ],
                "dispatch_reservation_id": first_reservation[
                    "dispatchReservationId"
                ],
                "dispatch_decision_fingerprint": first_reservation[
                    "dispatchDecisionFingerprint"
                ],
                "receiver_context_id": "context-first",
                "operation_id": "op-first",
            },
            root=self.root,
            workspace_root=str(first_workspace),
            trusted_host_adapter="codex",
        )
        frontier = call_tool(
            "graph_frontier",
            {"root_id": "d-second"},
            root=self.root,
            workspace_root=str(second_workspace),
        )
        second_ready = next(
            item
            for item in frontier["readyLoops"]
            if item["nodeId"] == loop_node_id("t-second")
        )
        self.assertEqual(
            second_ready["resourceConflicts"],
            [f"d-first/{loop_node_id('t-first')}"],
        )
        self.assertFalse(
            any(
                action["action"] == "DISPATCH_LOOP"
                for action in frontier["actions"]
            )
        )
        self.assertEqual(
            frontier["nextWakeAt"],
            first_claim["leaseExpiresAt"],
        )
        self.assertEqual(
            frontier["progressMonitor"]["waitDirective"]["mode"],
            "DEADLINE_ONLY",
        )
        self.assertEqual(
            frontier["progressMonitor"]["waitDirective"]["onNextWakeAt"],
            "CALL_GRAPH_FRONTIER_ONCE",
        )

        second_reservation = reserve_loop(
            root=self.root,
            root_id="d-second",
            node_id=loop_node_id("t-second"),
        )
        with self.assertRaises(GatedLoopError) as caught:
            call_tool(
                "dispatch_loop",
                {
                    "root_id": "d-second",
                    "node_id": loop_node_id("t-second"),
                    "owner": "agent-second",
                    "agent_id": second_reservation["agentId"],
                    "dispatch_mode": second_reservation["dispatchMode"],
                    "dispatch_transport": second_reservation[
                        "dispatchTransport"
                    ],
                    "dispatch_reservation_id": second_reservation[
                        "dispatchReservationId"
                    ],
                    "dispatch_decision_fingerprint": second_reservation[
                        "dispatchDecisionFingerprint"
                    ],
                    "receiver_context_id": "context-second",
                    "operation_id": "op-second",
                },
                root=self.root,
                workspace_root=str(second_workspace),
                trusted_host_adapter="codex",
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_RESOURCE_CONFLICT",
        )
        self.assertEqual(
            caught.exception.details["conflictingRootId"],
            "d-first",
        )

    def test_cross_delivery_dispatch_reservation_sets_conflict_deadline(
        self,
    ) -> None:
        claim = ["project:erp/environment:dispatch-reserved"]
        prepared_by_id: dict[str, dict] = {}
        first_workspace = Path(self.root, "worktree-reserved-first")
        second_workspace = Path(self.root, "worktree-reserved-second")
        first_workspace.mkdir()
        second_workspace.mkdir()
        for delivery_id, task_id, workspace in (
            (
                "d-reserved-first",
                "t-reserved-first",
                first_workspace,
            ),
            (
                "d-reserved-second",
                "t-reserved-second",
                second_workspace,
            ),
        ):
            prepared = prepare_hierarchy(
                root=self.root,
                hierarchy=delivery_task_hierarchy(
                    delivery_id,
                    task_id,
                    claims=claim,
                ),
                workspace_root=str(workspace),
                now=at(0),
            )
            freeze_hierarchy(
                root=self.root,
                root_id=prepared["rootId"],
                workspace_root=str(workspace),
                expected_hierarchy_fingerprint=prepared[
                    "hierarchyFingerprint"
                ],
                confirmed=True,
                confirmed_by="human",
                now=at(1),
            )
            prepared_by_id[delivery_id] = prepared

        first = prepared_by_id["d-reserved-first"]
        assignment = plan_dispatch_batch(
            root=self.root,
            root_id=first["rootId"],
            expected_graph_fingerprint=first["graphFingerprint"],
            host_adapter_id="codex",
            host_native_agent_ids=("codex",),
            now=at(2),
        )["assignments"][0]
        frontier = get_graph_frontier(
            root=self.root,
            root_id="d-reserved-second",
            now=at(3),
        )

        ready = next(
            item
            for item in frontier["readyLoops"]
            if item["nodeId"] == loop_node_id("t-reserved-second")
        )
        self.assertEqual(
            ready["resourceConflicts"],
            [f"d-reserved-first/{loop_node_id('t-reserved-first')}"],
        )
        self.assertEqual(
            frontier["nextWakeAt"],
            assignment["reservationExpiresAt"],
        )
        self.assertEqual(
            frontier["progressMonitor"]["waitDirective"]["mode"],
            "DEADLINE_ONLY",
        )

    def test_expired_cross_delivery_claim_does_not_block_dispatch(
        self,
    ) -> None:
        first_workspace = Path(self.root, "worktree-first")
        second_workspace = Path(self.root, "worktree-second")
        first_workspace.mkdir()
        second_workspace.mkdir()
        claim = ["project:erp/environment:shared"]
        for delivery_id, task_id, workspace in (
            ("d-first", "t-first", first_workspace),
            ("d-second", "t-second", second_workspace),
        ):
            prepared = prepare_hierarchy(
                root=self.root,
                hierarchy=delivery_task_hierarchy(
                    delivery_id,
                    task_id,
                    claims=claim,
                ),
                workspace_root=str(workspace),
                now=at(0),
            )
            freeze_hierarchy(
                root=self.root,
                root_id=prepared["rootId"],
                workspace_root=str(workspace),
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
                now=at(1),
            )

        dispatch_loop(
            root=self.root,
            root_id="d-first",
            node_id=loop_node_id("t-first"),
            owner="agent-first",
            operation_id="op-first-expiring",
            now=at(2),
        )
        frontier = get_graph_frontier(
            root=self.root,
            root_id="d-second",
            now=at(33),
        )
        self.assertIn(
            loop_node_id("t-second"),
            [
                action.get("nodeId")
                for action in frontier["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )
        dispatched = dispatch_loop(
            root=self.root,
            root_id="d-second",
            node_id=loop_node_id("t-second"),
            owner="agent-second",
            operation_id="op-second-after-expiry",
            now=at(33),
        )
        self.assertEqual(dispatched["status"], "CLAIMED")
