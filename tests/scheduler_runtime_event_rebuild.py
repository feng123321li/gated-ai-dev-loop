from __future__ import annotations

from .scheduler_runtime_support import (
    SchedulerRepository,
    at,
    dispatch_loop,
    fingerprint,
    loop_execution_policy,
    loop_node_id,
    patch,
    rebuild_graph_run,
    task_hierarchy,
)


class SchedulerRuntimeEventRebuildTests:
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

        rebuilt = rebuild_graph_run(root=self.root, root_id=root_id)

        state = next(
            item for item in rebuilt["nodes"] if item["nodeId"] == node_id
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
            "hdg.repository_events.fingerprint",
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
        self.assertLessEqual(hashed.call_count, len(expected_ids) + page_count)
