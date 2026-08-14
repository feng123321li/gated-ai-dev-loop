from __future__ import annotations

from .mcp_apps_support import patch


class McpAppsContractTestsPart2:
    def test_dashboard_projection_is_read_only_and_data_minimized(
        self,
    ) -> None:
        from hdg.dashboard import open_delivery_dashboard

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
                        "loop": {"secretInput": "must-not-leak"},
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
                        "target": "confirm:d-dashboard",
                    }
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
            "completedAt": None,
            "nodes": [
                {
                    "nodeId": "loop:t-api",
                    "kind": "TASK_LOOP",
                    "workItemId": "t-api",
                    "status": "CLAIMED",
                    "attempt": 1,
                    "operationId": "must-not-leak",
                    "monitor": {"health": "HEALTHY"},
                },
                {
                    "nodeId": "confirm:d-dashboard",
                    "kind": "USER_CONFIRMATION",
                    "workItemId": "d-dashboard",
                    "status": "PENDING",
                    "attempt": 0,
                },
            ],
            "progressMonitor": {
                "observedAt": "2026-08-08T08:00:00Z",
                "recommendedPollSeconds": 10,
                "alerts": [],
                "rows": [],
                "markdownTable": "| node | status |",
            },
        }
        history = {
            "rootId": "d-dashboard",
            "currentRevision": 2,
            "revisions": [
                {
                    "revision": 1,
                    "status": "SUPERSEDED",
                    "runId": "d-dashboard-r1",
                    "runStatus": "SUPERSEDED",
                    "reason": "private reason",
                    "requestedBy": "private actor",
                    "createdAt": "2026-08-07T08:00:00Z",
                    "updatedAt": "2026-08-07T09:00:00Z",
                    "frozenAt": "2026-08-07T08:10:00Z",
                    "completedAt": None,
                    "cancelledAt": None,
                    "supersededAt": "2026-08-07T09:00:00Z",
                }
            ],
        }

        with (
            patch(
                "hdg.dashboard.SchedulerRepository",
                autospec=True,
            ) as repository_class,
            patch("hdg.dashboard.graph_status", return_value=status),
        ):
            repository = repository_class.return_value
            repository.hierarchy.return_value = definition
            repository.revision_history.return_value = history
            result = open_delivery_dashboard(
                root="C:/control",
                root_id="d-dashboard",
            )

        self.assertTrue(result["readOnly"])
        self.assertEqual(result["delivery"]["revision"], 2)
        self.assertEqual(result["run"]["status"], "RUNNING")
        self.assertEqual(result["summary"]["activeLoops"], 1)
        self.assertEqual(result["graph"]["edges"], definition["graph"]["edges"])
        self.assertNotIn("loop", result["graph"]["nodes"][0])
        self.assertNotIn("operationId", result["graph"]["nodes"][0])
        self.assertNotIn("reason", result["revisions"][0])
        self.assertNotIn("requestedBy", result["revisions"][0])
        repository.assert_self_hosting_dogfood.assert_called_once_with(False)
