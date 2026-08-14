from __future__ import annotations

from .scheduler_runtime_support import (
    GatedLoopError,
    Path,
    SchedulerRepository,
    at,
    call_tool,
    create_manual_handoff,
    disjoint_parallel_hierarchy,
    dispatch_loop,
    get_graph_frontier,
    graph_runtime,
    graph_status,
    loop_context,
    loop_execution_policy,
    loop_node_id,
    parallel_hierarchy,
    plan_dispatch_batch,
    preview_hierarchy,
    rebuild_graph_run,
    record_loop_result,
    runtime_dispatch_loop,
    start_manual_handoff,
    task_hierarchy,
)


class SchedulerRuntimeTestsPart7:
    def test_unstarted_task_requirement_can_be_unfrozen_and_refrozen(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        task_id = "t-service"
        initial = graph_status(root=self.root, root_id=root_id)
        self.assertEqual(
            initial["taskRequirements"],
            [
                {
                    "taskId": task_id,
                    "revision": 1,
                    "status": "FROZEN",
                    "updatedAt": at(1).isoformat().replace(
                        "+00:00",
                        "Z",
                    ),
                }
            ],
        )

        unfrozen = call_tool(
            "unfreeze_task_requirement",
            {
                "root_id": root_id,
                "task_id": task_id,
                "expected_revision": 1,
                "authorized_by": "human",
                "reason": "Clarify the acceptance boundary.",
            },
            root=self.root,
        )
        self.assertEqual(
            unfrozen["taskRequirement"]["status"],
            "UNFROZEN",
        )
        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(2),
        )
        self.assertNotIn(
            loop_node_id(task_id),
            [
                action.get("nodeId")
                for action in frontier["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )
        self.assertIn(
            {
                "action": "REFREEZE_TASK_REQUIREMENT",
                "nodeId": loop_node_id(task_id),
                "taskId": task_id,
                "revision": 1,
            },
            frontier["actions"],
        )
        with self.assertRaises(GatedLoopError) as caught:
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=loop_node_id(task_id),
                owner="agent",
                operation_id="op-unfrozen",
                now=at(2),
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_TASK_REQUIREMENT_UNFROZEN",
        )

        requirement = unfrozen["taskRequirement"]["requirement"]
        requirement["title"] = "Run clarified service task"
        requirement["summary"] = "Implement the clarified requirement."
        requirement["payload"] = {
            "goal": "Deliver the revised result.",
            "acceptance": ["The revised acceptance boundary is verified."],
        }
        refrozen = call_tool(
            "refreeze_task_requirement",
            {
                "root_id": root_id,
                "task_id": task_id,
                "expected_revision": 1,
                "requirement": requirement,
                "confirmed_by": "human",
            },
            root=self.root,
        )
        self.assertEqual(refrozen["deliveryRevision"], 2)
        self.assertEqual(refrozen["previousRevision"], 1)
        self.assertEqual(
            refrozen["taskRequirement"]["revision"],
            2,
        )
        self.assertEqual(
            refrozen["taskRequirement"]["status"],
            "FROZEN",
        )
        history = SchedulerRepository(self.root).revision_history(root_id)
        self.assertEqual(history["currentRevision"], 2)
        self.assertEqual(
            [item["revision"] for item in history["revisions"]],
            [1, 2],
        )
        self.assertEqual(
            history["revisions"][0]["graphFingerprint"],
            prepared["graphFingerprint"],
        )
        self.assertEqual(
            history["revisions"][1]["graphFingerprint"],
            refrozen["graphFingerprint"],
        )
        self.assertNotEqual(
            history["revisions"][0]["graphFingerprint"],
            history["revisions"][1]["graphFingerprint"],
        )
        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id(task_id),
        )
        self.assertEqual(
            context["loop"]["payload"],
            requirement["payload"],
        )
        self.assertEqual(
            context["taskRequirement"]["revision"],
            2,
        )
        claimed = dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id(task_id),
            owner="agent-refrozen-revision",
            operation_id="op-refrozen-revision",
            now=at(3),
        )
        self.assertEqual(claimed["status"], "CLAIMED")
        self.assertEqual(claimed["deliveryRevision"], 2)
        resumed = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(4),
        )
        self.assertNotIn(
            loop_node_id(task_id),
            [item["nodeId"] for item in resumed["readyLoops"]],
        )
        baseline = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / "work-items"
            / task_id
            / "baseline.md"
        ).read_text(encoding="utf-8")
        self.assertIn("需求版本：2", baseline)
        self.assertIn("需求状态：已冻结", baseline)
        self.assertIn(requirement["title"], baseline)
        rebuilt = rebuild_graph_run(
            root=self.root,
            root_id=root_id,
        )
        self.assertEqual(
            rebuilt["taskRequirements"][0]["revision"],
            2,
        )
        self.assertEqual(
            rebuilt["taskRequirements"][0]["status"],
            "FROZEN",
        )

    def test_pending_dispatch_reservation_blocks_requirement_unfreeze(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        assignment = plan_dispatch_batch(
            root=self.root,
            root_id=root_id,
            expected_graph_fingerprint=prepared["graphFingerprint"],
            host_adapter_id="codex",
            host_native_agent_ids=("codex",),
            now=at(2),
        )["assignments"][0]

        with self.assertRaises(GatedLoopError) as caught:
            graph_runtime.unfreeze_task_requirement(
                root=self.root,
                root_id=root_id,
                task_id="t-service",
                expected_revision=1,
                authorized_by="human",
                reason="Clarify the acceptance boundary.",
                now=at(3),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_TASK_REQUIREMENT_RESERVATION_ACTIVE",
        )
        self.assertEqual(
            caught.exception.details["retryAfter"],
            assignment["reservationExpiresAt"],
        )
        self.assertEqual(
            caught.exception.details["dispatchReservations"],
            [
                {
                    "dispatchReservationId": assignment[
                        "dispatchReservationId"
                    ],
                    "nodeId": loop_node_id("t-service"),
                    "reservationExpiresAt": assignment[
                        "reservationExpiresAt"
                    ],
                }
            ],
        )

        unfrozen = graph_runtime.unfreeze_task_requirement(
            root=self.root,
            root_id=root_id,
            task_id="t-service",
            expected_revision=1,
            authorized_by="human",
            reason="Clarify the acceptance boundary.",
            now=at(8),
        )
        self.assertEqual(
            unfrozen["taskRequirement"]["status"],
            "UNFROZEN",
        )

    def test_repeated_task_refreezes_create_consecutive_delivery_revisions(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        graph_fingerprints = [prepared["graphFingerprint"]]

        for requirement_revision, minute in ((1, 2), (2, 4)):
            unfrozen = graph_runtime.unfreeze_task_requirement(
                root=self.root,
                root_id=root_id,
                task_id="t-service",
                expected_revision=requirement_revision,
                authorized_by="human",
                reason=f"Clarify requirement revision {requirement_revision + 1}.",
                now=at(minute),
            )
            requirement = unfrozen["taskRequirement"]["requirement"]
            requirement["summary"] = (
                f"Implement requirement revision {requirement_revision + 1}."
            )
            refrozen = graph_runtime.refreeze_task_requirement(
                root=self.root,
                root_id=root_id,
                task_id="t-service",
                expected_revision=requirement_revision,
                requirement=requirement,
                confirmed_by="human",
                now=at(minute + 1),
            )
            self.assertEqual(
                refrozen["deliveryRevision"],
                requirement_revision + 1,
            )
            self.assertEqual(
                refrozen["taskRequirement"]["revision"],
                requirement_revision + 1,
            )
            graph_fingerprints.append(refrozen["graphFingerprint"])

        history = SchedulerRepository(self.root).revision_history(root_id)
        self.assertEqual(history["currentRevision"], 3)
        self.assertEqual(
            [item["revision"] for item in history["revisions"]],
            [1, 2, 3],
        )
        self.assertEqual(
            [item["graphFingerprint"] for item in history["revisions"]],
            graph_fingerprints,
        )
        claimed = dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-service"),
            owner="agent-third-delivery-revision",
            operation_id="op-third-delivery-revision",
            now=at(6),
        )
        self.assertEqual(claimed["status"], "CLAIMED")
        self.assertEqual(claimed["deliveryRevision"], 3)
        self.assertEqual(claimed["taskRequirement"]["revision"], 3)

    def test_unchanged_task_refreeze_does_not_create_a_revision(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        unfrozen = graph_runtime.unfreeze_task_requirement(
            root=self.root,
            root_id=root_id,
            task_id="t-service",
            expected_revision=1,
            authorized_by="human",
            reason="Review the requirement without changing it.",
            now=at(2),
        )

        with self.assertRaises(GatedLoopError) as caught:
            graph_runtime.refreeze_task_requirement(
                root=self.root,
                root_id=root_id,
                task_id="t-service",
                expected_revision=1,
                requirement=unfrozen["taskRequirement"]["requirement"],
                confirmed_by="human",
                now=at(3),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_TASK_REQUIREMENT_CHANGE_INVALID",
        )
        history = SchedulerRepository(self.root).revision_history(root_id)
        self.assertEqual(history["currentRevision"], 1)
        self.assertEqual(len(history["revisions"]), 1)

    def test_manual_task_refreeze_preserves_manual_execution_mode(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        handoff = create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=preview[
                "hierarchyFingerprint"
            ],
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        start_manual_handoff(
            root=self.root,
            root_id=handoff["rootId"],
            expected_hierarchy_fingerprint=handoff[
                "hierarchyFingerprint"
            ],
            expected_graph_fingerprint=handoff["graphFingerprint"],
            started_by="manual-orchestrator",
            workspace_root=self.root,
            now=at(2),
        )
        unfrozen = graph_runtime.unfreeze_task_requirement(
            root=self.root,
            root_id=handoff["rootId"],
            task_id="t-service",
            expected_revision=1,
            authorized_by="human",
            reason="Clarify the manual TASK requirement.",
            now=at(3),
        )
        requirement = unfrozen["taskRequirement"]["requirement"]
        requirement["summary"] = "Implement the clarified manual TASK."

        refrozen = graph_runtime.refreeze_task_requirement(
            root=self.root,
            root_id=handoff["rootId"],
            task_id="t-service",
            expected_revision=1,
            requirement=requirement,
            confirmed_by="human",
            now=at(4),
        )

        self.assertEqual(refrozen["deliveryRevision"], 2)
        self.assertEqual(refrozen["executionMode"], "manual")
        claimed = runtime_dispatch_loop(
            root=self.root,
            root_id=handoff["rootId"],
            node_id=loop_node_id("t-service"),
            owner="manual-task-receiver",
            operation_id="op-manual-refrozen-revision",
            agent_id="codex",
            receiver_context_id="manual-task-receiver",
            dispatch_mode="MANUAL",
            now=at(5),
        )
        self.assertEqual(claimed["status"], "CLAIMED")
        self.assertEqual(claimed["dispatchMode"], "MANUAL")
        self.assertEqual(claimed["taskRequirement"]["revision"], 2)

    def test_sibling_dispatch_reservation_blocks_requirement_refreeze(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(disjoint_parallel_hierarchy())
        root_id = prepared["rootId"]
        unfrozen = graph_runtime.unfreeze_task_requirement(
            root=self.root,
            root_id=root_id,
            task_id="t-api",
            expected_revision=1,
            authorized_by="human",
            reason="Clarify the API acceptance boundary.",
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
        self.assertEqual(assignment["nodeId"], loop_node_id("t-core"))
        requirement = unfrozen["taskRequirement"]["requirement"]
        requirement["summary"] = "Implement the clarified API requirement."

        with self.assertRaises(GatedLoopError) as caught:
            graph_runtime.refreeze_task_requirement(
                root=self.root,
                root_id=root_id,
                task_id="t-api",
                expected_revision=1,
                requirement=requirement,
                confirmed_by="human",
                now=at(4),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_TASK_REQUIREMENT_RESERVATION_ACTIVE",
        )
        self.assertEqual(
            caught.exception.details["retryAfter"],
            assignment["reservationExpiresAt"],
        )

        refrozen = graph_runtime.refreeze_task_requirement(
            root=self.root,
            root_id=root_id,
            task_id="t-api",
            expected_revision=1,
            requirement=requirement,
            confirmed_by="human",
            now=at(9),
        )
        self.assertEqual(refrozen["taskRequirement"]["revision"], 2)
        self.assertEqual(refrozen["taskRequirement"]["status"], "FROZEN")

    def test_started_task_requirement_cannot_be_unfrozen(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-service"),
            owner="agent",
            operation_id="op-started-requirement",
            now=at(2),
        )
        with self.assertRaises(GatedLoopError) as caught:
            call_tool(
                "unfreeze_task_requirement",
                {
                    "root_id": root_id,
                    "task_id": "t-service",
                    "expected_revision": 1,
                    "authorized_by": "human",
                    "reason": "Too late.",
                },
                root=self.root,
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_TASK_ALREADY_STARTED",
        )

    def test_retried_task_requirement_cannot_be_unfrozen(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent",
            operation_id="op-started-before-retry",
            now=at(2),
        )
        retried = record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-started-before-retry",
            outcome={
                "status": "BLOCKED",
                "summary": "Worker transport failed.",
                "result": {},
            },
            failure_class="RETRYABLE_INFRA",
            now=at(3),
        )
        self.assertTrue(retried["retried"])
        self.assertEqual(retried["schedulerStatus"], "READY")

        with self.assertRaises(GatedLoopError) as caught:
            call_tool(
                "unfreeze_task_requirement",
                {
                    "root_id": root_id,
                    "task_id": "t-service",
                    "expected_revision": 1,
                    "authorized_by": "human",
                    "reason": "This task already entered development.",
                },
                root=self.root,
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_TASK_ALREADY_STARTED",
        )

    def test_initial_frontier_reserves_shared_resources_deterministically(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(parallel_hierarchy())
        frontier = get_graph_frontier(
            root=self.root,
            root_id=prepared["rootId"],
            now=at(2),
        )

        dispatch_actions = [
            item
            for item in frontier["actions"]
            if item["action"] == "DISPATCH_LOOP"
        ]
        self.assertEqual(
            dispatch_actions,
            [
                {
                    "action": "DISPATCH_LOOP",
                    "nodeId": loop_node_id("t-api"),
                    "loopRef": "project/java-service-loop@1",
                    "executionPolicy": loop_execution_policy(),
                }
            ],
        )
        ready = {
            item["nodeId"]: item
            for item in frontier["readyLoops"]
        }
        self.assertEqual(
            ready[loop_node_id("t-api")]["resourceConflicts"],
            [],
        )
        self.assertEqual(
            ready[loop_node_id("t-core")]["resourceConflicts"],
            [loop_node_id("t-api")],
        )

    def test_initial_frontier_dispatches_disjoint_ready_loops(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(
            disjoint_parallel_hierarchy()
        )
        frontier = get_graph_frontier(
            root=self.root,
            root_id=prepared["rootId"],
            now=at(2),
        )

        self.assertEqual(
            [
                item["nodeId"]
                for item in frontier["actions"]
                if item["action"] == "DISPATCH_LOOP"
            ],
            [
                loop_node_id("t-api"),
                loop_node_id("t-core"),
            ],
        )
        self.assertTrue(
            all(
                not item["resourceConflicts"]
                for item in frontier["readyLoops"]
            )
        )

    def test_replan_required_suppresses_new_dispatches(self) -> None:
        prepared = self.prepare_and_freeze(
            disjoint_parallel_hierarchy()
        )
        root_id = prepared["rootId"]
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-api"),
            owner="agent-api",
            operation_id="op-api-replan",
            now=at(2),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-api"),
            operation_id="op-api-replan",
            outcome={
                "status": "REPLAN_REQUIRED",
                "summary": "The frozen topology must change.",
                "result": {"reason": "new dependency"},
            },
            now=at(3),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(4),
        )

        self.assertEqual(
            frontier["actions"],
            [
                {
                    "action": "REPLAN_HIERARCHY",
                    "nodeId": loop_node_id("t-api"),
                }
            ],
        )
        self.assertIn(
            loop_node_id("t-core"),
            [item["nodeId"] for item in frontier["readyLoops"]],
        )
        with self.assertRaises(GatedLoopError) as caught:
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=loop_node_id("t-core"),
                owner="agent-core",
                operation_id="op-stale-frontier",
                now=at(5),
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_REPLAN_REQUIRED",
        )
