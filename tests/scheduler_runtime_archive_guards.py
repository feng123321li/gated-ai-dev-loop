from __future__ import annotations

from .scheduler_runtime_support import (
    GatedLoopError,
    SchedulerRepository,
    archive_delivery,
    at,
    cancel_graph_run,
    close_delivery,
    create_manual_handoff,
)


class SchedulerRuntimeArchiveGuardTests:
    def test_archived_delivery_cannot_become_a_manual_handoff(self) -> None:
        completed = self.complete_task_delivery("d-archived-manual")
        root_id = completed["rootId"]
        close_delivery(
            root=self.root,
            root_id=root_id,
            confirmed=True,
            closed_by="archive-user",
            summary="Production delivery completed.",
            now=at(9),
        )
        archive_delivery(root=self.root, root_id=root_id, now=at(9))
        stored = SchedulerRepository(self.root).hierarchy(root_id)

        with self.assertRaises(GatedLoopError) as caught:
            create_manual_handoff(
                root=self.root,
                hierarchy=stored["hierarchy"],
                expected_hierarchy_fingerprint=stored[
                    "hierarchyFingerprint"
                ],
                expected_graph_fingerprint=stored["graphFingerprint"],
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="archive-user",
                now=at(10),
            )

        self.assertEqual(caught.exception.code, "SCHEDULER_DELIVERY_ARCHIVED")
        self.assertEqual(
            SchedulerRepository(self.root).hierarchy(root_id)["status"],
            "ARCHIVED",
        )

    def test_archived_delivery_cannot_be_cancelled(self) -> None:
        completed = self.complete_task_delivery("d-archived-cancel")
        root_id = completed["rootId"]
        close_delivery(
            root=self.root,
            root_id=root_id,
            confirmed=True,
            closed_by="archive-user",
            summary="Production delivery completed.",
            now=at(9),
        )
        archive_delivery(root=self.root, root_id=root_id, now=at(9))

        with self.assertRaises(GatedLoopError) as caught:
            cancel_graph_run(
                root=self.root,
                root_id=root_id,
                cancelled_by="archive-user",
                reason="Must remain archived.",
                now=at(10),
            )

        self.assertEqual(caught.exception.code, "SCHEDULER_RUN_TERMINAL")
        self.assertEqual(
            SchedulerRepository(self.root).hierarchy(root_id)["status"],
            "ARCHIVED",
        )
