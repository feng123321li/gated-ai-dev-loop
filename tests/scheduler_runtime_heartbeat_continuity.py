from __future__ import annotations

from .scheduler_runtime_support import (
    at,
    dispatch_loop,
    get_graph_frontier,
    graph_events,
    heartbeat_loop,
    loop_node_id,
    record_loop_result,
    report_loop_progress,
    success,
    task_hierarchy,
    timedelta,
)


class SchedulerRuntimeTestsPart14:
    def test_initial_not_required_heartbeat_keeps_schedule_past_base_lease(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        operation_id = "op-long-inspection"
        claimed_at = at(2)
        claimed = dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="inspection-agent",
            operation_id=operation_id,
            now=claimed_at,
        )

        self.assertEqual(
            claimed["heartbeatDirective"],
            {
                "action": "HEARTBEAT_NOW",
                "dueAt": "2026-07-29T08:02:00Z",
                "intervalSeconds": 60,
                "continueUntil": "LOOP_RESULT_RECORDED_OR_CLAIM_RELEASED",
                "continueAfterLeaseRenewalNotRequired": True,
                "progressDoesNotAffectSchedule": True,
            },
        )

        initial = heartbeat_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id=operation_id,
            now=claimed_at + timedelta(seconds=10),
        )
        self.assertFalse(initial["leaseRenewed"])
        self.assertEqual(initial["leaseRenewalReason"], "NOT_REQUIRED")
        self.assertEqual(initial["leaseExpiresAt"], claimed["leaseExpiresAt"])
        self.assertEqual(
            initial["heartbeatDirective"]["dueAt"],
            "2026-07-29T08:03:10Z",
        )
        self.assertTrue(
            initial["heartbeatDirective"][
                "continueAfterLeaseRenewalNotRequired"
            ]
        )

        progress = report_loop_progress(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id=operation_id,
            phase="INSPECTING",
            summary_zh="正在检查代码与依赖。",
            progress_percent=5,
            now=claimed_at + timedelta(seconds=20),
        )
        self.assertFalse(progress["leaseRenewed"])
        self.assertEqual(progress["leaseExpiresAt"], claimed["leaseExpiresAt"])
        self.assertEqual(
            progress["heartbeatDirective"]["dueAt"],
            initial["heartbeatDirective"]["dueAt"],
        )

        periodic = []
        for seconds in (70, 130, 190):
            periodic.append(
                heartbeat_loop(
                    root=self.root,
                    root_id=root_id,
                    node_id=node_id,
                    operation_id=operation_id,
                    now=claimed_at + timedelta(seconds=seconds),
                )
            )
        self.assertEqual(
            [item["leaseRenewed"] for item in periodic],
            [False, False, True],
        )
        self.assertGreater(
            periodic[-1]["leaseExpiresAt"],
            claimed["leaseExpiresAt"],
        )

        after_base_lease = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=claimed_at + timedelta(seconds=301),
        )
        self.assertEqual(
            [item["nodeId"] for item in after_base_lease["activeLoops"]],
            [node_id],
        )
        heartbeat_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id=operation_id,
            now=claimed_at + timedelta(seconds=310),
        )
        completed = record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id=operation_id,
            outcome=success("Inspection completed without losing the worker."),
            now=claimed_at + timedelta(seconds=360),
        )
        self.assertEqual(completed["schedulerStatus"], "SUCCEEDED")
        self.assertEqual(completed["outcome"]["status"], "SUCCEEDED")

        events = graph_events(root=self.root, root_id=root_id)["events"]
        heartbeat_events = [
            event for event in events if event["eventType"] == "LOOP_HEARTBEAT"
        ]
        self.assertEqual(
            [event["payload"]["leaseRenewalReason"] for event in heartbeat_events],
            [
                "NOT_REQUIRED",
                "NOT_REQUIRED",
                "NOT_REQUIRED",
                "RENEWAL_THRESHOLD",
                "NOT_REQUIRED",
            ],
        )
        self.assertFalse(
            any(event["eventType"] == "CLAIM_LEASE_EXPIRED" for event in events)
        )
        self.assertFalse(
            any(
                event.get("payload", {}).get("failureClass") == "WORKER_LOST"
                for event in events
            )
        )
