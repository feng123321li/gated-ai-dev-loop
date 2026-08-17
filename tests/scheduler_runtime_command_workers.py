from __future__ import annotations

from .scheduler_runtime_support import (
    SchedulerRepository,
    at,
    dispatch_loop,
    get_graph_frontier,
    graph_status,
    heartbeat_loop,
    json,
    loop_execution_policy,
    loop_node_id,
    rebuild_graph_run,
    record_loop_result,
    success,
    task_hierarchy,
)


class SchedulerRuntimeTestsPart12:
    def test_execution_policy_routes_heavy_builds_to_scoped_workers(
        self,
    ) -> None:
        policy = loop_execution_policy()
        workers = policy["specializedCommandWorkers"]

        self.assertTrue(workers["parentRetainsControlPlaneCredentials"])
        self.assertFalse(workers["workerReceivesControlPlaneCredentials"])
        self.assertEqual(
            workers["fallback"],
            "NON_BLOCKING_PROCESS_OR_SEPARATE_MONITOR",
        )
        profiles = {item["id"]: item for item in workers["profiles"]}
        self.assertIn("JAVA_MAVEN", profiles)
        self.assertEqual(
            profiles["JAVA_MAVEN"]["projectSignals"],
            ["pom.xml", ".mvn/", "mvnw", "mvnw.cmd"],
        )
        self.assertIn(
            "DEPENDENCY_WARMUP",
            profiles["JAVA_MAVEN"]["triggers"],
        )
        self.assertIn("JAVA_GRADLE", profiles)
        self.assertIn("NODE_PACKAGE_MANAGER", profiles)
        self.assertEqual(
            workers["reviewPolicy"]["GROUP_REVIEW_LOOP"],
            "SEAM_GAP_ONLY_NEVER_DEFAULT_FULL_BUILD",
        )

    def test_long_command_heartbeat_grants_bounded_maven_warmup_lease(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        operation_id = "op-maven-warmup"
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="maven-agent",
            operation_id=operation_id,
            now=at(2),
        )

        granted = heartbeat_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id=operation_id,
            expected_command_seconds=600,
            now=at(3),
        )

        self.assertTrue(granted["leaseRenewed"])
        self.assertEqual(granted["leaseRenewalReason"], "LONG_COMMAND")
        self.assertEqual(granted["expectedCommandSeconds"], 600)
        self.assertEqual(
            granted["leaseExpiresAt"],
            "2026-07-29T08:15:00Z",
        )
        still_claimed = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(10),
        )
        self.assertEqual(still_claimed["activeLoops"][0]["nodeId"], node_id)

    def test_runtime_responses_omit_large_workspace_diffs(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        operation_id = "op-large-workspace-diff"
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="large-result-agent",
            operation_id=operation_id,
            now=at(2),
        )
        outcome = success("Large workspace evidence is stored.")
        outcome["result"]["workspaceChanges"] = [
            {
                "projectId": "project-a",
                "diff": "x" * 500_000,
                "diffByteCount": 500_000,
                "diffTruncated": False,
            }
        ]

        recorded = record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id=operation_id,
            outcome=outcome,
            now=at(3),
        )
        status = graph_status(root=self.root, root_id=root_id, now=at(4))
        rebuilt = rebuild_graph_run(root=self.root, root_id=root_id)

        for response in (recorded, status, rebuilt):
            raw = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.assertLess(len(raw), 100_000)
            snapshots = (
                response["outcome"]["result"]["workspaceChanges"]
                if "outcome" in response
                else next(
                    item["outcome"]["result"]["workspaceChanges"]
                    for item in response["nodes"]
                    if item["nodeId"] == node_id
                )
            )
            self.assertNotIn("diff", snapshots[0])
            self.assertTrue(snapshots[0]["diffOmittedFromGraph"])

        repository = SchedulerRepository(self.root)
        with repository.read() as connection:
            stored_event = connection.execute(
                "SELECT payload_json FROM graph_events "
                "WHERE event_type = 'LOOP_SUCCEEDED'"
            ).fetchone()["payload_json"]
        self.assertLess(len(stored_event.encode("utf-8")), 100_000)
        self.assertNotIn('"diff":', stored_event)
