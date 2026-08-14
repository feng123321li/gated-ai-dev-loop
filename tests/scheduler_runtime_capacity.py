from __future__ import annotations

from .scheduler_runtime_support import (
    GatedLoopError,
    Path,
    SchedulerRepository,
    WORK_ITEM_DIRECTORY,
    at,
    delivery_task_hierarchy,
    dispatch_loop,
    freeze_hierarchy,
    get_graph_frontier,
    graph_events,
    graph_runtime,
    graph_status,
    group_hierarchy,
    group_review_node_id,
    loop_context,
    loop_execution_policy,
    loop_node_id,
    node,
    pause_loop,
    prepare_hierarchy,
    rebuild_graph_run,
    record_loop_result,
    record_user_confirmation,
    report_host_capacity_exhausted,
    report_loop_progress,
    resume_loop,
    success,
    task_hierarchy,
)


class SchedulerRuntimeTestsPart5:
    def test_light_delivery_completes_without_independent_review_loops(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["delivery"].update(
            {
                "assuranceProfile": "LIGHT",
                "assuranceRationale": (
                    "The actual diff changes one internal helper, keeps all "
                    "interfaces stable, and has a focused passing test."
                ),
                "reviewLoop": None,
            }
        )
        hierarchy["root"]["reviewLoop"] = None
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="light-agent",
            operation_id="op-light",
            now=at(2),
        )
        report_loop_progress(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-light",
            phase="VERIFYING",
            summary_zh="Focused verification passed for the local change.",
            tests={"passed": 1, "failed": 0, "skipped": 0, "total": 1},
            now=at(3),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-light",
            outcome=success("Light change and focused verification completed."),
            now=at(4),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(5),
        )
        self.assertEqual(frontier["readyLoops"], [])
        self.assertIn(
            "RECORD_USER_CONFIRMATION",
            [action["action"] for action in frontier["actions"]],
        )
        confirmation_fingerprint = frontier["progressMonitor"][
            "changeFingerprint"
        ]
        completed = record_user_confirmation(
            root=self.root,
            root_id=root_id,
            confirmed=True,
            confirmed_by="human",
            summary="Accepted the focused change.",
            now=at(6),
        )
        self.assertEqual(completed["status"], "COMPLETED")
        completed_fingerprint = graph_status(
            root=self.root,
            root_id=root_id,
            now=at(6),
        )["progressMonitor"]["changeFingerprint"]
        self.assertNotEqual(
            completed_fingerprint,
            confirmation_fingerprint,
        )
        event_types = [
            event["eventType"]
            for event in graph_events(
                root=self.root,
                root_id=root_id,
            )["events"]
        ]
        self.assertEqual(event_types.count("LOOP_SUCCEEDED"), 1)
        self.assertNotIn("review:task:t-service", repr(frontier))
        acceptance = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / "acceptance.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "LIGHT 保障档不创建 Delivery Acceptance/Readiness",
            acceptance,
        )

    def test_rate_limited_loop_waits_until_reset_then_redispatches(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        reset_at = at(20).isoformat().replace("+00:00", "Z")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-rate-limited",
            operation_id="op-rate-limited",
            now=at(3),
        )

        paused = pause_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-rate-limited",
            resume_at=reset_at,
            capacity_scope="EXECUTOR",
            now=at(4),
        )

        self.assertEqual(paused["status"], "PAUSED")
        self.assertEqual(paused["resumeAt"], reset_at)
        self.assertEqual(
            paused["nextAction"],
            "WAIT_FOR_EXECUTOR_CAPACITY",
        )
        self.assertEqual(
            paused["handoff"]["resumeSequence"],
            [
                "workspace_status",
                "graph_frontier",
                "loop_context",
                "dispatch_loop",
            ],
        )
        progress = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / WORK_ITEM_DIRECTORY
            / "t-service"
            / "progress.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "等待至 2026-07-29 16:20:00 由 Agent 恢复派遣",
            progress,
        )
        rebuilt = rebuild_graph_run(
            root=self.root,
            root_id=root_id,
        )
        rebuilt_node = next(
            item
            for item in rebuilt["nodes"]
            if item["nodeId"] == node_id
        )
        self.assertEqual(rebuilt_node["status"], "PAUSED")
        self.assertEqual(rebuilt_node["resumeAt"], reset_at)
        self.assertIsNone(rebuilt_node["leaseExpiresAt"])
        self.assertIsNone(rebuilt_node["finishedAt"])

        waiting = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(10),
        )
        self.assertEqual(waiting["nextWakeAt"], reset_at)
        self.assertEqual(
            waiting["pausedLoops"],
            [
                {
                    "nodeId": node_id,
                    "kind": "TASK_LOOP",
                    "workItemId": "t-service",
                    "attempt": 1,
                    "previousOwner": "agent-rate-limited",
                    "previousOperationId": "op-rate-limited",
                    "resumeAt": reset_at,
                    "capacityScope": "EXECUTOR",
                }
            ],
        )
        self.assertEqual(
            waiting["actions"],
            [
                {
                    "action": "WAIT_FOR_EXECUTOR_CAPACITY",
                    "nodeId": node_id,
                    "resumeAt": reset_at,
                    "executionPolicy": loop_execution_policy(),
                }
            ],
        )
        wait_directive = waiting["progressMonitor"]["waitDirective"]
        self.assertEqual(wait_directive["mode"], "CONSUME_ACTIONS_FIRST")
        self.assertEqual(
            wait_directive["immediateActions"],
            ["WAIT_FOR_EXECUTOR_CAPACITY"],
        )
        self.assertEqual(
            wait_directive["nativeWakeDirective"],
            {
                "mode": "HOST_NATIVE_ONE_SHOT",
                "scheduleAfter": reset_at,
                "applySafetyMargin": True,
                "cancelRecurringMonitors": False,
                "capacityActions": ["WAIT_FOR_EXECUTOR_CAPACITY"],
            },
        )

        ready = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(20),
        )
        self.assertIsNone(ready["nextWakeAt"])
        self.assertEqual(ready["pausedLoops"], [])
        self.assertEqual(ready["readyLoops"][0]["attempt"], 1)
        self.assertIn(
            node_id,
            [
                action["nodeId"]
                for action in ready["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )
        auto_resumed = [
            event
            for event in graph_events(
                root=self.root,
                root_id=root_id,
            )["events"]
            if event["eventType"] == "NODE_AUTO_RESUMED"
        ]
        self.assertEqual(len(auto_resumed), 1)
        self.assertEqual(
            auto_resumed[0]["payload"],
            {"resumeAt": reset_at},
        )

    def test_rate_limited_loop_can_resume_early_with_alternate_agent(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        reset_at = at(20).isoformat().replace("+00:00", "Z")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-rate-limited",
            operation_id="op-rate-limited-alternate",
            now=at(3),
        )
        pause_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-rate-limited-alternate",
            resume_at=reset_at,
            capacity_scope="EXECUTOR",
            now=at(4),
        )

        resumed = resume_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            now=at(6),
        )
        self.assertEqual(resumed["status"], "READY")
        alternate = dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-independent-alternate",
            operation_id="op-independent-alternate",
            now=at(7),
        )
        self.assertEqual(alternate["owner"], "agent-independent-alternate")
        state = graph_status(root=self.root, root_id=root_id)
        current = next(
            item
            for item in state["nodes"]
            if item["nodeId"] == node_id
        )
        self.assertEqual(current["attempt"], 1)
        self.assertIsNone(current["resumeAt"])

    def test_rate_limit_pause_requires_a_future_reset_time(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-rate-limited",
            operation_id="op-invalid-reset",
            now=at(3),
        )

        for invalid in (
            "not-a-timestamp",
            at(4).isoformat().replace("+00:00", "Z"),
        ):
            with self.subTest(resume_at=invalid):
                with self.assertRaises(GatedLoopError) as caught:
                    pause_loop(
                        root=self.root,
                        root_id=root_id,
                        node_id=node_id,
                        operation_id="op-invalid-reset",
                        resume_at=invalid,
                        capacity_scope="EXECUTOR",
                        now=at(4),
                    )
                self.assertEqual(
                    caught.exception.code,
                    "SCHEDULER_RESUME_TIME_INVALID",
                )

        state = graph_status(root=self.root, root_id=root_id)
        current = next(
            item
            for item in state["nodes"]
            if item["nodeId"] == node_id
        )
        self.assertEqual(current["status"], "CLAIMED")

    def test_host_rate_limit_waits_for_host_native_wake(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        reset_at = at(20).isoformat().replace("+00:00", "Z")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="host-native-agent",
            operation_id="op-host-rate-limit",
            now=at(3),
        )

        paused = pause_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-host-rate-limit",
            resume_at=reset_at,
            capacity_scope="HOST",
            now=at(4),
        )
        self.assertEqual(paused["nextAction"], "WAIT_FOR_HOST_CAPACITY")
        waiting = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(10),
        )
        self.assertEqual(
            waiting["actions"],
            [
                {
                    "action": "WAIT_FOR_HOST_CAPACITY",
                    "nodeId": node_id,
                    "resumeAt": reset_at,
                    "executionPolicy": loop_execution_policy(),
                }
            ],
        )
        self.assertEqual(
            waiting["pausedLoops"][0]["capacityScope"],
            "HOST",
        )
        wait_directive = waiting["progressMonitor"]["waitDirective"]
        self.assertEqual(wait_directive["mode"], "CONSUME_ACTIONS_FIRST")
        self.assertEqual(
            wait_directive["immediateActions"],
            ["WAIT_FOR_HOST_CAPACITY"],
        )
        self.assertFalse(
            wait_directive["nativeWakeDirective"][
                "cancelRecurringMonitors"
            ]
        )
        ready = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(20),
        )
        self.assertIn(
            node_id,
            [
                action["nodeId"]
                for action in ready["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )

    def test_hard_429_trips_host_breaker_after_worker_stops(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        reset_at = at(40).isoformat().replace("+00:00", "Z")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="claude-worker",
            agent_id="claude-code",
            operation_id="op-hard-429",
            now=at(3),
        )

        tripped = report_host_capacity_exhausted(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            reset_at=reset_at,
            host_adapter_id="claude-code",
            receiver_context_id="claude-worker",
            report_id="report-hard-429",
            reason="HTTP 429 quota exhausted",
            now=at(30),
        )
        waiting = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(35),
        )

        self.assertEqual(tripped["status"], "OPEN")
        self.assertTrue(tripped["cancelRecurringMonitors"])
        self.assertEqual(tripped["wakeMode"], "HOST_NATIVE_ONE_SHOT")
        self.assertEqual(waiting["nextWakeAt"], reset_at)
        self.assertEqual(
            waiting["actions"],
            [
                {
                    "action": "WAIT_FOR_HOST_CAPACITY",
                    "resetAt": reset_at,
                    "capacityKey": "claude-code:default",
                    "affectedNodeIds": [node_id],
                    "cancelRecurringMonitors": True,
                    "wakeMode": "HOST_NATIVE_ONE_SHOT",
                }
            ],
        )
        hard_wait = waiting["progressMonitor"]["waitDirective"]
        self.assertEqual(
            hard_wait["nativeWakeDirective"]["scheduleAfter"],
            reset_at,
        )
        self.assertTrue(
            hard_wait["nativeWakeDirective"]["cancelRecurringMonitors"]
        )
        current = graph_status(root=self.root, root_id=root_id)
        current_node = next(
            item for item in current["nodes"] if item["nodeId"] == node_id
        )
        self.assertEqual(current_node["status"], "PAUSED")
        replayed = report_host_capacity_exhausted(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            reset_at=reset_at,
            host_adapter_id="claude-code",
            receiver_context_id="claude-worker",
            report_id="report-hard-429",
            reason="HTTP 429 quota exhausted",
            now=at(31),
        )
        self.assertTrue(replayed["idempotentReplay"])
        repository = SchedulerRepository(self.root)
        with repository.transaction() as connection:
            connection.execute("DELETE FROM host_capacity_breakers")
        rebuilt_open = rebuild_graph_run(
            root=self.root,
            root_id=root_id,
        )
        self.assertEqual(rebuilt_open["executionMode"], "active")
        self.assertEqual(
            rebuilt_open["hostCapacity"]["capacityKey"],
            "claude-code:default",
        )
        with repository.read() as connection:
            rebuilt_breaker = repository.open_host_capacity_breaker(
                connection,
                agent_id="claude-code",
                at=at(35).isoformat().replace("+00:00", "Z"),
            )
        self.assertIsNotNone(rebuilt_breaker)

        ready = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(40),
        )
        self.assertNotIn("hostCapacity", ready)
        self.assertIn(
            node_id,
            [
                action["nodeId"]
                for action in ready["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )
        rebuilt_restored = rebuild_graph_run(
            root=self.root,
            root_id=root_id,
        )
        self.assertNotIn("hostCapacity", rebuilt_restored)

    def test_every_trusted_host_adapter_has_a_capacity_key(self) -> None:
        self.assertEqual(
            set(graph_runtime.HOST_CAPACITY_KEYS),
            set(graph_runtime.HOST_ADAPTER_AGENTS),
        )

    def test_hard_quota_report_rejects_unbounded_reset_horizon(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=prepared["rootId"],
            node_id=node_id,
            owner="claude-worker",
            agent_id="claude-code",
            receiver_context_id="claude-context",
            operation_id="op-hard-quota-horizon",
            now=at(3),
        )
        with self.assertRaises(GatedLoopError) as caught:
            report_host_capacity_exhausted(
                root=self.root,
                root_id=prepared["rootId"],
                node_id=node_id,
                reset_at=at(30 + 25 * 60).isoformat().replace(
                    "+00:00",
                    "Z",
                ),
                host_adapter_id="claude-code",
                receiver_context_id="claude-context",
                report_id="report-too-far",
                reason="HTTP 429 quota exhausted",
                now=at(30),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_HOST_CAPACITY_REPORT_INVALID",
        )

    def test_rebuild_does_not_overwrite_newer_global_capacity_report(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="claude-worker",
            agent_id="claude-code",
            receiver_context_id="claude-worker",
            operation_id="op-stale-open",
            now=at(3),
        )
        report_host_capacity_exhausted(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            reset_at=at(40).isoformat().replace("+00:00", "Z"),
            host_adapter_id="claude-code",
            receiver_context_id="claude-worker",
            report_id="report-stale-open",
            reason="HTTP 429 quota exhausted",
            now=at(30),
        )
        repository = SchedulerRepository(self.root)
        newer_reset = at(60).isoformat().replace("+00:00", "Z")
        newer_reported = at(50).isoformat().replace("+00:00", "Z")
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE host_capacity_breakers SET reset_at = ?, "
                "report_id = 'report-newer-open', status = 'OPEN', "
                "reported_at = ?, restored_at = NULL, "
                "reason = 'newer host report' "
                "WHERE capacity_key = 'claude-code:default'",
                (newer_reset, newer_reported),
            )

        rebuild_graph_run(root=self.root, root_id=root_id)

        with repository.read() as connection:
            breaker = connection.execute(
                "SELECT * FROM host_capacity_breakers WHERE "
                "capacity_key = 'claude-code:default'"
            ).fetchone()
        self.assertEqual(breaker["report_id"], "report-newer-open")
        self.assertEqual(breaker["reset_at"], newer_reset)
        self.assertEqual(breaker["status"], "OPEN")

    def test_rebuild_old_restore_does_not_clear_newer_global_breaker(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="claude-worker",
            agent_id="claude-code",
            receiver_context_id="claude-worker",
            operation_id="op-stale-restore",
            now=at(3),
        )
        report_host_capacity_exhausted(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            reset_at=at(40).isoformat().replace("+00:00", "Z"),
            host_adapter_id="claude-code",
            receiver_context_id="claude-worker",
            report_id="report-stale-restore",
            reason="HTTP 429 quota exhausted",
            now=at(30),
        )
        get_graph_frontier(root=self.root, root_id=root_id, now=at(40))
        repository = SchedulerRepository(self.root)
        newer_reset = at(60).isoformat().replace("+00:00", "Z")
        newer_reported = at(50).isoformat().replace("+00:00", "Z")
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE host_capacity_breakers SET reset_at = ?, "
                "report_id = 'report-newer-after-restore', "
                "status = 'OPEN', reported_at = ?, restored_at = NULL, "
                "reason = 'newer host report' "
                "WHERE capacity_key = 'claude-code:default'",
                (newer_reset, newer_reported),
            )

        rebuild_graph_run(root=self.root, root_id=root_id)

        with repository.read() as connection:
            breaker = connection.execute(
                "SELECT * FROM host_capacity_breakers WHERE "
                "capacity_key = 'claude-code:default'"
            ).fetchone()
        self.assertEqual(
            breaker["report_id"],
            "report-newer-after-restore",
        )
        self.assertEqual(breaker["reset_at"], newer_reset)
        self.assertEqual(breaker["status"], "OPEN")

    def test_hard_quota_breaker_pauses_same_agent_across_deliveries(
        self,
    ) -> None:
        deliveries = []
        for delivery_id, task_id in (
            ("d-first", "t-first"),
            ("d-second", "t-second"),
        ):
            workspace = Path(self.root, delivery_id)
            workspace.mkdir()
            prepared = prepare_hierarchy(
                root=self.root,
                hierarchy=delivery_task_hierarchy(delivery_id, task_id),
                workspace_root=str(workspace),
                now=at(0),
            )
            freeze_hierarchy(
                root=self.root,
                root_id=delivery_id,
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
                root_id=delivery_id,
                node_id=loop_node_id(task_id),
                owner=f"claude-{task_id}",
                agent_id="claude-code",
                receiver_context_id=f"context-{task_id}",
                operation_id=f"op-{task_id}",
                now=at(3),
            )
            deliveries.append((delivery_id, task_id))

        report_host_capacity_exhausted(
            root=self.root,
            root_id="d-first",
            node_id=loop_node_id("t-first"),
            reset_at=at(40).isoformat().replace("+00:00", "Z"),
            host_adapter_id="claude-code",
            receiver_context_id="context-t-first",
            report_id="report-cross-delivery",
            reason="HTTP 429 quota exhausted",
            now=at(30),
        )

        for delivery_id, task_id in deliveries:
            node = next(
                item
                for item in graph_status(
                    root=self.root,
                    root_id=delivery_id,
                )["nodes"]
                if item["nodeId"] == loop_node_id(task_id)
            )
            self.assertEqual(node["status"], "PAUSED")

    def test_timed_pause_requires_explicit_capacity_scope(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="host-native-agent",
            operation_id="op-missing-capacity-scope",
            now=at(3),
        )

        with self.assertRaises(GatedLoopError) as caught:
            pause_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-missing-capacity-scope",
                resume_at=at(20).isoformat().replace("+00:00", "Z"),
                now=at(4),
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_CAPACITY_SCOPE_INVALID",
        )

    def test_group_review_context_links_group_work_item_projections(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(group_hierarchy())
        root_id = prepared["rootId"]

        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=group_review_node_id("g-service"),
        )

        item_prefix = (
            f".layered-delivery/{root_id}/"
            f"{WORK_ITEM_DIRECTORY}/g-service"
        )
        self.assertEqual(
            context["humanArtifacts"],
            {
                "workItem": {
                    "kind": "GROUP",
                    "baseline": f"{item_prefix}/baseline.md",
                    "progress": f"{item_prefix}/progress.md",
                    "acceptance": f"{item_prefix}/acceptance.md",
                }
            },
        )
