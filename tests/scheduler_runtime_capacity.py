from __future__ import annotations

from .scheduler_runtime_support import (
    Path,
    WORK_ITEM_DIRECTORY,
    at,
    dispatch_loop,
    get_graph_frontier,
    graph_events,
    graph_status,
    group_hierarchy,
    group_review_node_id,
    loop_context,
    loop_node_id,
    record_loop_result,
    record_user_confirmation,
    report_loop_progress,
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
