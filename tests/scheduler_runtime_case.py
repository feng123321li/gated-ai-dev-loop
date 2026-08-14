from __future__ import annotations

from .scheduler_runtime_support import (
    TemporaryDirectory,
    at,
    delivery_task_hierarchy,
    dispatch_loop,
    freeze_hierarchy,
    loop_node_id,
    prepare_hierarchy,
    record_loop_result,
    record_user_confirmation,
    review_node_id,
    success_for_node,
    task_review_node_id,
)


class SchedulerRuntimeTestsSupport:
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = self.temporary.name

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare_and_freeze(self, hierarchy: dict) -> dict:
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        return prepared

    def complete_task_delivery(
        self,
        delivery_id: str = "d-archive",
        *,
        requirement_key: str | None = None,
    ) -> dict:
        task_id = "t-archive"
        hierarchy = delivery_task_hierarchy(delivery_id, task_id)
        if requirement_key is not None:
            hierarchy["delivery"]["requirementKey"] = requirement_key
        prepared = self.prepare_and_freeze(
            hierarchy
        )
        root_id = prepared["rootId"]
        for index, node_id in enumerate(
            (
                loop_node_id(task_id),
                task_review_node_id(task_id),
                review_node_id(root_id),
            ),
            start=1,
        ):
            operation_id = f"op-archive-{index}"
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                owner=f"archive-agent-{index}",
                operation_id=operation_id,
                now=at(index * 2),
            )
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id=operation_id,
                outcome=success_for_node(node_id, f"{node_id} completed."),
                now=at(index * 2 + 1),
            )
        return record_user_confirmation(
            root=self.root,
            root_id=root_id,
            confirmed=True,
            confirmed_by="archive-user",
            summary="Accepted before archival.",
            now=at(8),
        )
