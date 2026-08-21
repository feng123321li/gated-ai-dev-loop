from __future__ import annotations

from .scheduler_runtime_support import (
    GatedLoopError,
    Path,
    SchedulerRepository,
    archive_delivery,
    at,
    call_tool,
    close_delivery,
    deepcopy,
    delivery_task_hierarchy,
    freeze_hierarchy,
    graph_events,
    prepare_delivery_revision,
    workspace_status,
)


class SchedulerRuntimeTestsPart15:
    def test_completed_open_delivery_can_continue_with_next_revision(
        self,
    ) -> None:
        completed = self.complete_task_delivery("d-completed-open")
        root_id = completed["rootId"]
        status = workspace_status(root=self.root, root_id=root_id)

        self.assertEqual(status["runStatus"], "COMPLETED")
        self.assertEqual(status["deliveryClosure"], "OPEN")
        self.assertEqual(status["deliveryStateLabel"], "未上线")
        self.assertTrue(status["canPrepareRevision"])
        self.assertTrue(status["canCloseDelivery"])
        self.assertEqual(
            status["nextAction"],
            "PREPARE_REVISION_OR_CLOSE_DELIVERY",
        )
        rootless = workspace_status(root=self.root)
        self.assertEqual(rootless["rootId"], root_id)
        self.assertEqual(rootless["deliveryClosure"], "OPEN")
        overview = Path(
            self.root,
            ".layered-delivery",
            root_id,
            "overview.md",
        ).read_text(encoding="utf-8")
        self.assertIn("| 已完成 | 未上线 |", overview)

        hierarchy = deepcopy(
            SchedulerRepository(self.root).hierarchy(root_id)["hierarchy"]
        )
        hierarchy["delivery"]["summary"] = "测试反馈后的第二轮优化。"
        prepared = prepare_delivery_revision(
            root=self.root,
            root_id=root_id,
            expected_current_revision=1,
            hierarchy=hierarchy,
            reason="测试反馈后继续优化。",
            continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
            requested_by="delivery-owner",
            now=at(9),
        )
        frozen = freeze_hierarchy(
            root=self.root,
            root_id=root_id,
            expected_delivery_revision=2,
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="delivery-owner",
            now=at(10),
        )

        self.assertEqual(frozen["deliveryRevision"], 2)
        history = SchedulerRepository(self.root).revision_history(root_id)
        self.assertEqual(
            [item["status"] for item in history["revisions"]],
            ["SUPERSEDED", "FROZEN"],
        )
        self.assertEqual(history["revisions"][0]["runStatus"], "COMPLETED")

    def test_closed_delivery_rejects_another_revision(self) -> None:
        completed = self.complete_task_delivery("d-closed")
        root_id = completed["rootId"]
        closed = call_tool(
            "close_delivery",
            {
                "root_id": root_id,
                "confirmed": True,
                "closed_by": "delivery-owner",
                "summary": "测试、业务验收和生产上线均已完成。",
            },
            root=self.root,
            workspace_root=self.root,
        )

        self.assertEqual(closed["deliveryClosure"], "CLOSED")
        self.assertEqual(closed["deliveryStateLabel"], "已上线交付")
        self.assertFalse(closed["canPrepareRevision"])
        self.assertFalse(closed["canCloseDelivery"])
        status = workspace_status(root=self.root, root_id=root_id)
        self.assertEqual(status["deliveryClosure"], "CLOSED")
        self.assertEqual(status["deliveryStateLabel"], "已上线交付")
        self.assertEqual(status["nextAction"], "ARCHIVE_DELIVERY_OPTIONAL")
        overview = Path(
            self.root,
            ".layered-delivery",
            root_id,
            "overview.md",
        ).read_text(encoding="utf-8")
        self.assertIn("| 已完成 | 已上线交付 |", overview)
        repeated = close_delivery(
            root=self.root,
            root_id=root_id,
            confirmed=True,
            closed_by="delivery-owner",
            summary="重复确认不产生新事件。",
            now=at(10),
        )
        self.assertTrue(repeated["alreadyClosed"])
        self.assertEqual(
            [
                event["eventType"]
                for event in graph_events(root=self.root, root_id=root_id)[
                    "events"
                ]
            ].count("DELIVERY_CLOSED"),
            1,
        )

        hierarchy = deepcopy(
            SchedulerRepository(self.root).hierarchy(root_id)["hierarchy"]
        )
        hierarchy["delivery"]["summary"] = "关闭后不允许继续追加。"
        with self.assertRaises(GatedLoopError) as caught:
            prepare_delivery_revision(
                root=self.root,
                root_id=root_id,
                expected_current_revision=1,
                hierarchy=hierarchy,
                reason="错误地尝试继续追加。",
                continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
                requested_by="delivery-owner",
                now=at(11),
            )
        self.assertEqual(caught.exception.code, "SCHEDULER_DELIVERY_CLOSED")

    def test_completed_open_delivery_must_close_before_archive(self) -> None:
        completed = self.complete_task_delivery("d-close-before-archive")
        root_id = completed["rootId"]

        with self.assertRaises(GatedLoopError) as caught:
            archive_delivery(root=self.root, root_id=root_id, now=at(9))

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DELIVERY_NOT_CLOSED",
        )

    def test_delivery_close_requires_completion_and_explicit_confirmation(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(
            delivery_task_hierarchy("d-close-guard", "t-close-guard")
        )
        root_id = prepared["rootId"]

        with self.assertRaises(GatedLoopError) as unconfirmed:
            close_delivery(
                root=self.root,
                root_id=root_id,
                confirmed=False,
                closed_by="delivery-owner",
                summary="Not authorized.",
                now=at(2),
            )
        self.assertEqual(
            unconfirmed.exception.code,
            "SCHEDULER_DELIVERY_CLOSE_CONFIRMATION_REQUIRED",
        )

        with self.assertRaises(GatedLoopError) as incomplete:
            close_delivery(
                root=self.root,
                root_id=root_id,
                confirmed=True,
                closed_by="delivery-owner",
                summary="Not completed.",
                now=at(2),
            )
        self.assertEqual(
            incomplete.exception.code,
            "SCHEDULER_DELIVERY_NOT_COMPLETED",
        )
