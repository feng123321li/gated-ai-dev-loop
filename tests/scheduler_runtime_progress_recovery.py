from __future__ import annotations

from .scheduler_runtime_support import (
    GatedLoopError,
    Path,
    STATUS_TEXT,
    SchedulerRepository,
    WORK_ITEM_DIRECTORY,
    at,
    auditable_recursive_hierarchy,
    datetime,
    disjoint_parallel_hierarchy,
    dispatch_loop,
    fingerprint,
    freeze_hierarchy,
    get_graph_frontier,
    graph_events,
    graph_status,
    heartbeat_loop,
    hierarchical_work_item_paths,
    loop_execution_policy,
    loop_node_id,
    patch,
    plan_dispatch_batch,
    prepare_hierarchy,
    rebuild_graph_run,
    report_loop_progress,
    task_hierarchy,
    timedelta,
    timezone,
)


class SchedulerRuntimeTestsPart10:
    def test_short_lease_renews_at_threshold_and_updates_live_monitor_only(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        operation_id = "op-threshold-renewal"
        claimed_at = at(2)
        claimed = dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="heartbeat-agent",
            operation_id=operation_id,
            now=claimed_at,
        )
        progress_path = Path(
            self.root, ".layered-delivery", root_id, "progress.md"
        )
        projection_after_claim = progress_path.read_bytes()

        self.assertEqual(
            claimed["leaseExpiresAt"],
            "2026-07-29T08:07:00Z",
        )
        self.assertIn("progressMonitor", claimed)
        self.assertIn("心跳与租约", claimed["progressMonitor"]["markdownTable"])

        early = heartbeat_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id=operation_id,
            now=at(3),
        )
        early_monitor = early["progressMonitor"]
        early_row = next(
            row for row in early_monitor["rows"] if row["nodeId"] == node_id
        )

        self.assertFalse(early["leaseRenewed"])
        self.assertEqual(early["leaseExpiresAt"], claimed["leaseExpiresAt"])
        self.assertEqual(early_row["lastHeartbeatAt"], "2026-07-29T08:03:00Z")
        self.assertFalse(early_row["lastHeartbeatLeaseRenewed"])
        self.assertIn("保活，未到续租阈值", early_row["heartbeatZh"])
        self.assertEqual(progress_path.read_bytes(), projection_after_claim)

        renewed = heartbeat_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id=operation_id,
            now=at(5),
        )
        renewed_monitor = renewed["progressMonitor"]
        renewed_row = next(
            row
            for row in renewed_monitor["rows"]
            if row["nodeId"] == node_id
        )

        self.assertTrue(renewed["leaseRenewed"])
        self.assertEqual(renewed["leaseExpiresAt"], "2026-07-29T08:10:00Z")
        self.assertEqual(renewed_row["lastHeartbeatAt"], "2026-07-29T08:05:00Z")
        self.assertTrue(renewed_row["lastHeartbeatLeaseRenewed"])
        self.assertIn("已续租", renewed_row["heartbeatZh"])
        self.assertNotEqual(
            early_monitor["changeFingerprint"],
            renewed_monitor["changeFingerprint"],
        )
        self.assertEqual(progress_path.read_bytes(), projection_after_claim)

        heartbeat_events = [
            event
            for event in graph_events(root=self.root, root_id=root_id)["events"]
            if event["eventType"] == "LOOP_HEARTBEAT"
        ]
        self.assertEqual(
            [event["payload"]["leaseRenewed"] for event in heartbeat_events],
            [False, True],
        )

        report_loop_progress(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id=operation_id,
            phase="TESTING",
            summary_zh="关键测试阶段已开始。",
            now=at(6),
        )
        self.assertNotEqual(progress_path.read_bytes(), projection_after_claim)

    def test_frozen_projection_contains_runtime_progress(self) -> None:
        hierarchy = auditable_recursive_hierarchy()
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        frozen = freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        projections = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
        )
        overview = (projections / "overview.md").read_text(
            encoding="utf-8"
        )
        progress = (projections / "progress.md").read_text(
            encoding="utf-8"
        )
        acceptance = (projections / "acceptance.md").read_text(
            encoding="utf-8"
        )

        self.assertEqual(frozen["status"], "ACTIVE")
        self.assertFalse((projections / "state.json").exists())
        self.assertIn(
            (
                f"| {prepared['rootId']} | "
                f"{hierarchy['delivery']['title']} | 运行中 |"
            ),
            overview,
        )
        self.assertNotIn("ACTIVE", overview)
        self.assertIn("运行状态：运行中", progress)
        statuses = {
            state["status"]
            for state in frozen["nodes"]
        }
        self.assertIn("READY", statuses)
        self.assertIn("PENDING", statuses)
        item_paths = hierarchical_work_item_paths(hierarchy)
        for state in frozen["nodes"]:
            with self.subTest(node_id=state["nodeId"]):
                node_id = state["nodeId"]
                status = STATUS_TEXT[state["status"]]
                if node_id.startswith("confirm:"):
                    self.assertIn(f"| {status} | 无 | 1 |", acceptance)
                    continue
                if node_id.startswith("loop:"):
                    item_id = node_id.removeprefix("loop:")
                    stage = "TASK"
                    path = item_paths[item_id].removeprefix(
                        f"{WORK_ITEM_DIRECTORY}/"
                    ).replace("/children/", "/")
                elif node_id.startswith("join:"):
                    item_id = node_id.removeprefix("join:")
                    stage = "GROUP 完成点"
                    path = item_paths[item_id].removeprefix(
                        f"{WORK_ITEM_DIRECTORY}/"
                    ).replace("/children/", "/")
                elif node_id.startswith("review:group:"):
                    item_id = node_id.removeprefix("review:group:")
                    stage = "GROUP seam Review"
                    path = item_paths[item_id].removeprefix(
                        f"{WORK_ITEM_DIRECTORY}/"
                    ).replace("/children/", "/")
                else:
                    path = hierarchy["delivery"]["id"]
                    stage = "Delivery Acceptance/Readiness"
                self.assertIn(
                    (
                        f"| {path} | {stage} | {status} | "
                        "无 | 无 | 1 |"
                    ),
                    progress,
                )

    def test_projection_labels_statuses_and_times_are_localized(
        self,
    ) -> None:
        prepared_at = datetime(
            2026,
            1,
            1,
            0,
            0,
            tzinfo=timezone.utc,
        )
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=task_hierarchy(),
            now=prepared_at,
        )
        overview_path = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
            / "overview.md"
        )
        baseline_path = overview_path.with_name("baseline.md")
        progress_path = overview_path.with_name("progress.md")
        prepared_overview = overview_path.read_text(encoding="utf-8")
        prepared_baseline = baseline_path.read_text(encoding="utf-8")

        self.assertIn(
            "2026-01-01 08:00:00",
            prepared_overview,
        )
        self.assertNotIn(
            "2026-01-01T08:00:00+08:00",
            prepared_overview,
        )
        self.assertIn(
            "| d-service | Deliver d-service | 待冻结 |",
            prepared_overview,
        )
        self.assertIn("| 任务 |", prepared_baseline)
        self.assertNotIn("PREPARED", prepared_overview)

        freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=prepared_at + timedelta(minutes=1),
        )
        dispatch_loop(
            root=self.root,
            root_id=prepared["rootId"],
            node_id=loop_node_id("t-service"),
            owner="agent-local-time",
            operation_id="op-local-time",
            now=prepared_at + timedelta(minutes=2),
        )
        active_overview = overview_path.read_text(encoding="utf-8")
        active_progress = progress_path.read_text(encoding="utf-8")

        self.assertIn(
            "| d-service | Deliver d-service | 运行中 |",
            active_overview,
        )
        self.assertIn(
            (
                "| t-service | TASK | 执行中 | codex | agent-local-time | 1 | "
                "2026-01-01 08:02:00 |"
            ),
            active_progress,
        )
        for machine_status in ("FROZEN", "ACTIVE", "CLAIMED"):
            self.assertNotIn(machine_status, active_overview)
            self.assertNotIn(machine_status, active_progress)
        self.assertNotRegex(
            active_progress,
            r"2026-01-01T\d{2}:\d{2}:\d{2}(?:Z|\+08:00)",
        )

    def test_loop_progress_is_audited_without_renewing_its_lease(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        claimed = dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="claude-reviewer",
            agent_id="claude-code",
            operation_id="op-progress",
            now=at(2),
        )

        reported = report_loop_progress(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-progress",
            phase="TESTING",
            summary_zh="正在运行测试，准备检查接口兼容性。",
            completed_zh=["已完成代码检查"],
            next_step_zh="检查接口兼容性",
            progress_percent=70,
            tests={
                "passed": 74,
                "failed": 0,
                "skipped": 0,
                "total": 74,
            },
            now=at(2) + timedelta(seconds=30),
        )

        self.assertEqual(reported["phaseZh"], "运行测试")
        self.assertEqual(reported["leaseExpiresAt"], claimed["leaseExpiresAt"])
        events = graph_events(root=self.root, root_id=root_id)["events"]
        progress_event = next(
            event
            for event in events
            if event["eventType"] == "LOOP_PROGRESS_REPORTED"
        )
        self.assertEqual(progress_event["payload"]["summaryZh"], "正在运行测试，准备检查接口兼容性。")
        self.assertFalse(
            any(event["eventType"] == "LOOP_HEARTBEAT" for event in events)
        )

        status = graph_status(
            root=self.root,
            root_id=root_id,
            now=at(2) + timedelta(seconds=48),
        )
        state = next(
            item for item in status["nodes"] if item["nodeId"] == node_id
        )
        self.assertEqual(state["progress"]["progressPercent"], 70)
        self.assertNotIn("modelId", state)
        table = status["progressMonitor"]["markdownTable"]
        self.assertIn("| 节点 | 执行器 | 当前阶段 |", table)
        self.assertIn("t-service · 任务执行", table)
        self.assertIn(
            "第 1 轮 · claude-code",
            table,
        )
        self.assertIn("运行测试", table)
        self.assertIn("74/74 通过", table)
        self.assertIn("准备检查接口兼容性", table)
        self.assertIn("尚无独立心跳", table)
        self.assertNotIn("LOOP_PROGRESS_REPORTED", table)
        self.assertNotIn("op-progress", table)

        heartbeat_missing = graph_status(
            root=self.root,
            root_id=root_id,
            now=at(2) + timedelta(seconds=91),
        )["progressMonitor"]
        self.assertEqual(
            heartbeat_missing["alerts"][0]["code"],
            "HEARTBEAT_MISSING",
        )
        self.assertIn("已开始但无独立心跳", heartbeat_missing["markdownTable"])

        projection = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / "progress.md"
        ).read_text(encoding="utf-8")
        self.assertIn("## 实时进度监控", projection)
        self.assertIn("74/74 通过", projection)
        self.assertNotIn("op-progress", projection)

        rebuilt = rebuild_graph_run(root=self.root, root_id=root_id)
        rebuilt_state = next(
            item for item in rebuilt["nodes"] if item["nodeId"] == node_id
        )
        self.assertEqual(
            rebuilt_state["progress"]["summaryZh"],
            "正在运行测试，准备检查接口兼容性。",
        )

    def test_light_loop_also_requires_an_immediate_first_heartbeat(self) -> None:
        hierarchy = task_hierarchy()
        hierarchy["delivery"].update(
            {
                "assuranceProfile": "LIGHT",
                "assuranceRationale": (
                    "One local helper changes with bounded targeted verification."
                ),
                "reviewLoop": None,
            }
        )
        hierarchy["root"]["reviewLoop"] = None
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        claimed_at = at(2)
        claimed = dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="light-agent",
            agent_id="claude-code",
            operation_id="op-light-no-heartbeat",
            now=claimed_at,
        )

        within_short_window = graph_status(
            root=self.root,
            root_id=root_id,
            now=claimed_at + timedelta(seconds=91),
        )["progressMonitor"]

        self.assertTrue(
            claimed["executionPolicy"]["progressReporting"][
                "initialHeartbeatRequiredBeforeWork"
            ]
        )
        self.assertEqual(
            claimed["heartbeatDirective"]["action"],
            "HEARTBEAT_NOW",
        )
        self.assertEqual(
            within_short_window["alerts"][0]["code"],
            "SUSPECT_NOT_STARTED",
        )
        self.assertIn("疑似未启动", within_short_window["markdownTable"])
        events = graph_events(root=self.root, root_id=root_id)["events"]
        self.assertFalse(
            any(event["eventType"] == "LOOP_HEARTBEAT" for event in events)
        )

        after_short_window = graph_status(
            root=self.root,
            root_id=root_id,
            now=claimed_at + timedelta(minutes=5, seconds=1),
        )["progressMonitor"]
        self.assertEqual(
            after_short_window["alerts"][0]["code"],
            "LEASE_EXPIRED_PENDING_RECOVERY",
        )

    def test_loop_progress_accepts_user_language_and_requires_live_claim(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-progress",
            operation_id="op-progress-validation",
            now=at(2),
        )

        reported = report_loop_progress(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-progress-validation",
            phase="INSPECTING",
            summary_zh="Inspecting source code.",
            completed_zh=["Loaded the relevant modules."],
            next_step_zh="Run the focused tests.",
            now=at(2) + timedelta(seconds=1),
        )
        self.assertEqual(reported["summaryZh"], "Inspecting source code.")
        self.assertEqual(
            reported["completedZh"],
            ["Loaded the relevant modules."],
        )
        self.assertEqual(reported["nextStepZh"], "Run the focused tests.")

        with self.assertRaises(GatedLoopError) as caught:
            report_loop_progress(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-progress-validation",
                phase="INSPECTING",
                summary_zh="Invalid\x01progress",
                now=at(2) + timedelta(seconds=1),
            )
        self.assertEqual(caught.exception.code, "SCHEDULER_PROGRESS_INVALID")

        with self.assertRaises(GatedLoopError) as caught:
            report_loop_progress(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-other",
                phase="INSPECTING",
                summary_zh="正在检查源代码。",
                now=at(2) + timedelta(seconds=2),
            )
        self.assertEqual(caught.exception.code, "SCHEDULER_OPERATION_INVALID")

        with self.assertRaises(GatedLoopError) as caught:
            report_loop_progress(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-progress-validation",
                phase="VERIFYING",
                summary_zh="正在执行最终验证。",
                now=at(33),
            )
        self.assertEqual(caught.exception.code, "SCHEDULER_OPERATION_INVALID")

    def test_progress_monitor_localizes_silence_and_recovers_expired_lease(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        operation_id = "op-progress-monitor"
        claimed_at = at(2)
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="background-agent",
            agent_id="claude-code",
            operation_id=operation_id,
            now=claimed_at,
        )

        not_started = graph_status(
            root=self.root,
            root_id=root_id,
            now=claimed_at + timedelta(seconds=91),
        )["progressMonitor"]
        self.assertEqual(not_started["alerts"][0]["code"], "SUSPECT_NOT_STARTED")
        self.assertIn("疑似未启动", not_started["markdownTable"])

        heartbeat_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id=operation_id,
            now=claimed_at + timedelta(minutes=3),
        )
        alive_without_progress = graph_status(
            root=self.root,
            root_id=root_id,
            now=claimed_at + timedelta(minutes=5, seconds=1),
        )["progressMonitor"]
        self.assertEqual(
            alive_without_progress["alerts"][0]["code"],
            "ALIVE_WITHOUT_PROGRESS",
        )
        self.assertIn("存活但无可见进展", alive_without_progress["markdownTable"])

        suspect_lost = graph_status(
            root=self.root,
            root_id=root_id,
            now=claimed_at + timedelta(minutes=6, seconds=1),
        )["progressMonitor"]
        self.assertEqual(suspect_lost["alerts"][0]["code"], "SUSPECT_LOST")
        self.assertEqual(
            suspect_lost["alerts"][0]["diagnosis"],
            {
                "claimMatched": True,
                "cause": "UNDETERMINED_CONTROL_PLANE_SILENCE",
                "hostProcessAlive": None,
                "safeRecovery": "WAIT_FOR_LEASE_EXPIRY",
            },
        )
        self.assertIn("疑似失联", suspect_lost["markdownTable"])

        expired_status = graph_status(
            root=self.root,
            root_id=root_id,
            now=claimed_at + timedelta(minutes=8, seconds=1),
        )["progressMonitor"]
        self.assertEqual(
            expired_status["alerts"][0]["code"],
            "LEASE_EXPIRED_PENDING_RECOVERY",
        )
        self.assertEqual(
            expired_status["waitDirective"]["mode"],
            "ADVANCE_REQUIRED",
        )
        self.assertEqual(
            expired_status["waitDirective"]["pollTool"],
            "graph_frontier",
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=claimed_at + timedelta(minutes=8, seconds=1),
        )
        self.assertEqual(frontier["progressMonitor"]["recommendedPollSeconds"], 90)
        events = graph_events(root=self.root, root_id=root_id)["events"]
        expired = next(
            event
            for event in events
            if event["eventType"] == "CLAIM_LEASE_EXPIRED"
        )
        self.assertEqual(expired["payload"]["failureClass"], "WORKER_LOST")

    def test_frontier_recovers_a_lease_at_the_exact_expiry_in_one_call(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        claimed = dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="background-agent",
            operation_id="op-expiry-equality",
            now=at(2),
        )
        expiry = datetime.fromisoformat(
            claimed["leaseExpiresAt"].replace("Z", "+00:00")
        )

        with self.assertRaises(GatedLoopError) as heartbeat_caught:
            heartbeat_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-expiry-equality",
                now=expiry,
            )

        recovered = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=expiry,
        )

        self.assertEqual(
            heartbeat_caught.exception.code,
            "SCHEDULER_OPERATION_INVALID",
        )

        self.assertNotEqual(
            recovered["progressMonitor"]["waitDirective"]["mode"],
            "ADVANCE_REQUIRED",
        )
        self.assertIn(
            "DISPATCH_LOOP",
            {action["action"] for action in recovered["actions"]},
        )

    def test_progress_monitor_waits_for_native_receiver_or_poll_deadline(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="background-agent",
            operation_id="op-native-wait",
            now=at(2),
        )

        first = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(3),
        )["progressMonitor"]
        second = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(3) + timedelta(seconds=5),
        )["progressMonitor"]

        self.assertEqual(first["recommendedPollSeconds"], 30)
        self.assertRegex(first["changeFingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            first["waitDirective"],
            {
                "mode": "HOST_NATIVE_EVENT_OR_DEADLINE",
                "pollNotBefore": "2026-07-29T08:03:30Z",
                "pollTool": "graph_status",
                "advanceTool": "graph_frontier",
                "interruptOn": [
                    "NATIVE_RECEIVER_COMPLETED",
                    "NATIVE_RECEIVER_NEEDS_ATTENTION",
                ],
                "onInterrupt": "CALL_GRAPH_FRONTIER_ONCE",
                "onTimeout": "CALL_GRAPH_STATUS_ONCE",
                "consumeActionsBeforeWaiting": False,
                "immediateActions": [],
                "nextWakeAt": "2026-07-29T08:07:00Z",
                "onNextWakeAt": "CALL_GRAPH_FRONTIER_ONCE",
                "suppressUnchangedCommentary": True,
            },
        )
        self.assertEqual(
            second["waitDirective"]["pollNotBefore"],
            "2026-07-29T08:03:30Z",
        )
        self.assertEqual(second["recommendedPollSeconds"], 25)
        self.assertEqual(
            first["changeFingerprint"],
            second["changeFingerprint"],
        )
        report_loop_progress(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-native-wait",
            phase="TESTING",
            summary_zh="正在运行受影响范围测试。",
            completed_zh=["代码修改完成"],
            next_step_zh="记录测试证据。",
            now=at(3) + timedelta(seconds=6),
        )
        heartbeat_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-native-wait",
            now=at(3) + timedelta(seconds=6),
        )
        changed = graph_status(
            root=self.root,
            root_id=root_id,
            now=at(3) + timedelta(seconds=6),
        )["progressMonitor"]
        self.assertNotEqual(
            first["changeFingerprint"],
            changed["changeFingerprint"],
        )
        self.assertEqual(
            changed["waitDirective"]["pollNotBefore"],
            "2026-07-29T08:06:06Z",
        )
        self.assertEqual(changed["recommendedPollSeconds"], 180)

    def test_frontier_consumes_ready_actions_before_waiting_on_receiver(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(disjoint_parallel_hierarchy())
        root_id = prepared["rootId"]
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-api"),
            owner="background-agent",
            operation_id="op-active-with-ready-peer",
            now=at(2),
        )

        status_directive = graph_status(
            root=self.root,
            root_id=root_id,
            now=at(3),
        )["progressMonitor"]["waitDirective"]
        self.assertEqual(
            status_directive["mode"],
            "FRONTIER_ACTIONS_AVAILABLE",
        )
        self.assertEqual(status_directive["pollTool"], "graph_frontier")

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(3),
        )

        self.assertIn(
            "DISPATCH_LOOP",
            {action["action"] for action in frontier["actions"]},
        )
        directive = frontier["progressMonitor"]["waitDirective"]
        self.assertEqual(
            directive["mode"],
            "CONSUME_ACTIONS_THEN_HOST_NATIVE_EVENT_OR_DEADLINE",
        )
        self.assertTrue(directive["consumeActionsBeforeWaiting"])
        self.assertEqual(directive["immediateActions"], ["DISPATCH_LOOP"])

    def test_status_uses_earliest_claim_deadline_while_peer_is_reserved(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(disjoint_parallel_hierarchy())
        root_id = prepared["rootId"]
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-api"),
            owner="background-agent",
            operation_id="op-active-peer",
            now=at(2),
        )
        assignment = plan_dispatch_batch(
            root=self.root,
            root_id=root_id,
            expected_graph_fingerprint=prepared["graphFingerprint"],
            host_adapter_id="codex",
            host_native_agent_ids=("codex",),
            now=at(3),
        )["assignments"][0]

        first = graph_status(
            root=self.root,
            root_id=root_id,
            now=at(3) + timedelta(seconds=5),
        )["progressMonitor"]["waitDirective"]
        second = graph_status(
            root=self.root,
            root_id=root_id,
            now=at(3) + timedelta(seconds=15),
        )["progressMonitor"]["waitDirective"]

        self.assertEqual(first["mode"], "HOST_NATIVE_EVENT_OR_DEADLINE")
        self.assertEqual(first["pollTool"], "graph_status")
        self.assertEqual(
            first["nextWakeAt"],
            "2026-07-29T08:07:00Z",
        )
        self.assertEqual(
            second["nextWakeAt"],
            "2026-07-29T08:07:00Z",
        )
        self.assertEqual(
            assignment["reservationExpiresAt"],
            "2026-07-29T08:08:00Z",
        )

    def test_repeated_noop_frontier_does_not_touch_run_or_projections(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-service"),
            owner="background-agent",
            operation_id="op-noop-frontier",
            now=at(2),
        )
        get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(3),
        )
        before = graph_status(root=self.root, root_id=root_id, now=at(3))
        progress_path = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / "progress.md"
        )
        before_bytes = progress_path.read_bytes()
        before_mtime = progress_path.stat().st_mtime_ns

        get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(3) + timedelta(seconds=1),
        )
        after = graph_status(
            root=self.root,
            root_id=root_id,
            now=at(3) + timedelta(seconds=1),
        )

        self.assertEqual(after["updatedAt"], before["updatedAt"])
        self.assertEqual(progress_path.read_bytes(), before_bytes)
        self.assertEqual(progress_path.stat().st_mtime_ns, before_mtime)

    def test_long_running_commands_keep_heartbeat_outside_blocking_call(
        self,
    ) -> None:
        policy = loop_execution_policy()

        self.assertEqual(
            policy["longRunningCommands"],
            {
                "execution": "NON_BLOCKING_OR_SEPARATE_MONITOR",
                "estimatedOverSecondsRequiresBackground": 60,
                "preferNarrowCommandScope": True,
                "heartbeatWhileRunning": True,
                "heartbeatIntervalSeconds": 60,
                "beforeStart": "REPORT_PROGRESS_AND_HEARTBEAT",
                "afterFinish": "HEARTBEAT_AND_REPORT_PROGRESS",
                "hostCompletionNotificationIsNotHeartbeat": True,
                "leaseRequestArgument": "expected_command_seconds",
                "maxExpectedCommandSeconds": 1800,
                "leaseBufferSeconds": 120,
            },
        )

    def test_materialized_state_can_be_rebuilt_from_events(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-1",
            operation_id="op-rebuild",
            now=at(2),
        )
        repository = SchedulerRepository(self.root)
        run_id = repository.run(root_id)["runId"]
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE node_runs SET status = 'BLOCKED' "
                "WHERE run_id = ? AND node_id = ?",
                (run_id, node_id),
            )

        rebuilt = rebuild_graph_run(
            root=self.root,
            root_id=root_id,
        )

        state = next(
            item
            for item in rebuilt["nodes"]
            if item["nodeId"] == node_id
        )
        self.assertEqual(state["status"], "CLAIMED")
        self.assertGreater(rebuilt["rebuiltFromEvents"], 0)

    def test_event_pagination_hashes_each_event_only_once_per_full_scan(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        repository = SchedulerRepository(self.root)
        run_id = repository.run(root_id)["runId"]
        with repository.transaction() as connection:
            for index in range(405):
                repository.append_event(
                    connection,
                    run_id=run_id,
                    node_id=None,
                    attempt=None,
                    event_type="PAGINATION_TEST_EVENT",
                    actor="TEST",
                    operation_id=None,
                    payload={"index": index},
                    at="2026-07-29T08:03:00Z",
                )
            expected_ids = [
                row["event_id"]
                for row in connection.execute(
                    "SELECT event_id FROM graph_events "
                    "WHERE run_id = ? ORDER BY event_id",
                    (run_id,),
                ).fetchall()
            ]

        collected_ids: list[int] = []
        cursor = 0
        page_count = 0
        with patch(
            "hdg.repository.fingerprint",
            wraps=fingerprint,
        ) as hashed:
            while True:
                page = repository.events(
                    root_id,
                    after_event_id=cursor,
                    limit=50,
                )
                page_count += 1
                collected_ids.extend(item["eventId"] for item in page)
                if len(page) < 50:
                    break
                cursor = page[-1]["eventId"]

        self.assertEqual(collected_ids, expected_ids)
        self.assertLessEqual(
            hashed.call_count,
            len(expected_ids) + page_count,
        )
