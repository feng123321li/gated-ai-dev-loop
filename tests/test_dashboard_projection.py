from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hdg.dashboard import open_delivery_dashboard
from hdg.planning import freeze_hierarchy, prepare_hierarchy

from .loop_architecture_support import task_hierarchy


class DashboardProjectionTests(unittest.TestCase):
    def test_repeated_dashboard_refresh_does_not_mutate_graph_state(
        self,
    ) -> None:
        def database_snapshot(database: Path) -> tuple[object, ...]:
            connection = sqlite3.connect(database)
            try:
                tables = [
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' ORDER BY name"
                    )
                ]
                return tuple(
                    (
                        table,
                        tuple(connection.execute(
                            f'SELECT * FROM "{table}" ORDER BY rowid'
                        )),
                    )
                    for table in tables
                )
            finally:
                connection.close()

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
                confirmed_by="dashboard-read-test",
            )
            database = Path(root, ".layered-delivery", "scheduler.db")
            before = database_snapshot(database)

            first = open_delivery_dashboard(
                root=root,
                root_id=prepared["rootId"],
            )
            second = open_delivery_dashboard(
                root=root,
                root_id=prepared["rootId"],
            )

            after = database_snapshot(database)

        self.assertTrue(first["readOnly"])
        self.assertTrue(second["readOnly"])
        self.assertEqual(before, after)

    def test_snapshot_is_locked_and_projection_is_minimized(self) -> None:
        definition = {
            "rootId": "d-dashboard",
            "deliveryRevision": 2,
            "status": "FROZEN",
            "updatedAt": "2026-08-08T08:00:00Z",
            "hierarchy": {
                "delivery": {
                    "id": "d-dashboard",
                    "title": "Dashboard delivery",
                    "summary": "Read-only dashboard projection.",
                }
            },
            "graph": {
                "nodes": [
                    {
                        "id": "loop:t-api",
                        "kind": "TASK_LOOP",
                        "workItemId": "t-api",
                    },
                    {
                        "id": "review:t-api",
                        "kind": "TASK_REVIEW_LOOP",
                        "workItemId": "t-api",
                    },
                    {
                        "id": "confirm:d-dashboard",
                        "kind": "USER_CONFIRMATION",
                        "workItemId": "d-dashboard",
                    },
                ],
                "edges": [
                    {
                        "source": "loop:t-api",
                        "target": "review:t-api",
                    },
                    {
                        "source": "review:t-api",
                        "target": "confirm:d-dashboard",
                    },
                ],
            },
        }
        status = {
            "rootId": "d-dashboard",
            "runId": "d-dashboard-r2",
            "status": "RUNNING",
            "executionMode": "automatic",
            "startedAt": "2026-08-08T07:00:00Z",
            "updatedAt": "2026-08-08T08:00:00Z",
            "nodes": [
                {
                    "nodeId": "loop:t-api",
                    "status": "SUCCEEDED",
                    "attempt": 1,
                    "agentId": "worker-1",
                    "leaseExpiresAt": "2026-08-08T09:00:00Z",
                    "failureClass": "private-failure",
                    "progress": {
                        "progressPercent": 100,
                        "summaryZh": "private-progress-summary",
                        "nextStepZh": "private-next-step",
                    },
                    "monitor": {
                        "phaseZh": "完成",
                        "summaryZh": "接口已完成",
                        "heartbeatZh": "不适用",
                        "healthZh": "已成功",
                        "diagnosis": {"private": True},
                    },
                },
                {
                    "nodeId": "review:t-api",
                    "status": "READY",
                    "attempt": 0,
                },
                {
                    "nodeId": "confirm:d-dashboard",
                    "status": "COMPLETED",
                    "attempt": 0,
                },
            ],
            "progressMonitor": {
                "observedAt": "2026-08-08T08:00:00Z",
                "recommendedPollSeconds": 10,
                "alerts": [
                    {
                        "nodeId": "review:t-api",
                        "code": "SUSPECT_LOST",
                        "messageZh": "等待复核执行器恢复。",
                        "diagnosis": {"private": True},
                    }
                ],
                "rows": [{"private": "row"}],
                "markdownTable": "| private |",
                "internalPolicy": {"private": True},
            },
        }
        history = {
            "rootId": "d-dashboard",
            "currentRevision": 2,
            "revisions": [],
        }
        lock_active = False

        @contextmanager
        def scheduler_lock():
            nonlocal lock_active
            self.assertFalse(lock_active)
            lock_active = True
            try:
                yield
            finally:
                lock_active = False

        def locked_definition(root_id: str) -> dict[str, object]:
            self.assertTrue(lock_active)
            self.assertEqual(root_id, "d-dashboard")
            return definition

        def locked_status(**arguments: object) -> dict[str, object]:
            self.assertTrue(lock_active)
            self.assertEqual(arguments["root_id"], "d-dashboard")
            return status

        def locked_history(root_id: str) -> dict[str, object]:
            self.assertTrue(lock_active)
            self.assertEqual(root_id, "d-dashboard")
            return history

        with (
            patch(
                "hdg.dashboard.SchedulerRepository",
                autospec=True,
            ) as repository_class,
            patch(
                "hdg.dashboard.graph_status",
                side_effect=locked_status,
            ),
        ):
            repository = repository_class.return_value
            repository.scheduler_lock.side_effect = scheduler_lock
            repository.hierarchy.side_effect = locked_definition
            repository.revision_history.side_effect = locked_history
            result = open_delivery_dashboard(
                root="C:/control",
                root_id="d-dashboard",
            )

        repository.scheduler_lock.assert_called_once_with()
        self.assertFalse(lock_active)
        self.assertEqual(result["summary"]["completedNodes"], 2)
        node = result["graph"]["nodes"][0]
        self.assertEqual(
            node["progress"],
            {"progressPercent": 100},
        )
        self.assertEqual(
            node["monitor"],
            {
                "phaseZh": "完成",
                "summaryZh": "接口已完成",
                "heartbeatZh": "不适用",
                "healthZh": "已成功",
            },
        )
        self.assertNotIn("leaseExpiresAt", node)
        self.assertNotIn("failureClass", node)
        self.assertEqual(
            result["progressMonitor"],
            {
                "observedAt": "2026-08-08T08:00:00Z",
                "recommendedPollSeconds": 10,
                "alerts": [
                    {
                        "nodeId": "review:t-api",
                        "code": "SUSPECT_LOST",
                        "messageZh": "等待复核执行器恢复。",
                    }
                ],
            },
        )
        self.assertNotIn("rows", result["progressMonitor"])
        self.assertNotIn("markdownTable", result["progressMonitor"])


if __name__ == "__main__":
    unittest.main()
