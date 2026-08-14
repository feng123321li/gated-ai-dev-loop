from __future__ import annotations

from .scheduler_runtime_support import (
    Event,
    SchedulerRepository,
    Thread,
    at,
    cancel_graph_run,
    current_thread,
    datetime,
    disjoint_parallel_hierarchy,
    dispatch_loop,
    get_graph_frontier,
    graph_events,
    graph_status,
    loop_node_id,
    patch,
    rebuild_graph_run,
    record_loop_result,
    task_hierarchy,
)


class SchedulerRuntimeTestsPart11:
    def test_rebuild_does_not_overwrite_a_concurrent_claim(self) -> None:
        prepared = self.prepare_and_freeze(
            disjoint_parallel_hierarchy()
        )
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-api")
        operation_id = "op-during-rebuild"
        snapshot_captured = Event()
        release_snapshot = Event()
        dispatch_finished = Event()
        errors: list[BaseException] = []
        original_events = SchedulerRepository.events

        def held_event_snapshot(
            repository: SchedulerRepository,
            *args: object,
            **kwargs: object,
        ) -> list[dict]:
            page = original_events(repository, *args, **kwargs)
            if (
                current_thread().name == "rebuild-thread"
                and not snapshot_captured.is_set()
            ):
                snapshot_captured.set()
                if not release_snapshot.wait(timeout=5):
                    raise AssertionError(
                        "Timed out releasing the rebuild event snapshot"
                    )
            return page

        def rebuild() -> None:
            try:
                rebuild_graph_run(root=self.root, root_id=root_id)
            except BaseException as error:
                errors.append(error)

        def claim() -> None:
            try:
                dispatch_loop(
                    root=self.root,
                    root_id=root_id,
                    node_id=node_id,
                    owner="concurrent-agent",
                    operation_id=operation_id,
                    now=at(2),
                )
            except BaseException as error:
                errors.append(error)
            finally:
                dispatch_finished.set()

        with patch.object(
            SchedulerRepository,
            "events",
            new=held_event_snapshot,
        ):
            rebuild_thread = Thread(
                target=rebuild,
                name="rebuild-thread",
            )
            dispatch_thread = Thread(
                target=claim,
                name="dispatch-during-rebuild",
            )
            rebuild_thread.start()
            self.assertTrue(snapshot_captured.wait(timeout=5))
            dispatch_thread.start()
            try:
                dispatch_finished.wait(timeout=1)
            finally:
                release_snapshot.set()
            rebuild_thread.join(timeout=5)
            dispatch_thread.join(timeout=5)

        self.assertFalse(rebuild_thread.is_alive())
        self.assertFalse(dispatch_thread.is_alive())
        self.assertEqual(errors, [])

        events = graph_events(root=self.root, root_id=root_id)["events"]
        claim_event = next(
            event
            for event in events
            if event["eventType"] == "LOOP_CLAIMED"
            and event["operationId"] == operation_id
        )
        run = graph_status(root=self.root, root_id=root_id)
        state = next(
            item
            for item in run["nodes"]
            if item["nodeId"] == node_id
        )

        self.assertEqual(state["status"], "CLAIMED")
        self.assertEqual(state["operationId"], operation_id)
        self.assertEqual(
            state["claimedAt"],
            claim_event["recordedAt"],
        )
        self.assertGreaterEqual(
            datetime.fromisoformat(
                run["updatedAt"].replace("Z", "+00:00")
            ),
            datetime.fromisoformat(
                claim_event["recordedAt"].replace("Z", "+00:00")
            ),
        )

    def test_loop_cancellation_blocks_the_run_with_a_frontier_action(
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
            operation_id="op-cancelled-loop",
            now=at(2),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-cancelled-loop",
            outcome={
                "status": "CANCELLED",
                "summary": "Internal Loop was cancelled.",
                "result": {},
            },
            now=at(3),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(4),
        )
        self.assertEqual(frontier["status"], "BLOCKED")
        self.assertIn(
            {
                "action": "RESOLVE_LOOP_CANCELLATION",
                "nodeId": node_id,
            },
            frontier["actions"],
        )

    def test_cancelled_graph_is_a_stable_terminal_frontier(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        cancelled = cancel_graph_run(
            root=self.root,
            root_id=root_id,
            cancelled_by="human",
            reason="Requirement withdrawn.",
            now=at(2),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(20),
        )
        after = graph_status(root=self.root, root_id=root_id)

        self.assertEqual(frontier["status"], "CANCELLED")
        self.assertEqual(frontier["actions"], [])
        self.assertEqual(frontier["blockedLoops"], [])
        self.assertEqual(after["status"], "CANCELLED")
        self.assertEqual(after["updatedAt"], cancelled["updatedAt"])
        self.assertEqual(after["cancelledAt"], cancelled["cancelledAt"])
