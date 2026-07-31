from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from hdg.errors import GatedLoopError
from hdg.graph_frontier import get_graph_frontier
from hdg.graph_model import loop_node_id, task_review_node_id
from hdg.graph_runtime import (
    cancel_graph_run,
    dispatch_loop,
    rebuild_graph_run,
    record_loop_result,
)
from hdg.model_core import validate_hierarchy_definition
from hdg.planning import (
    delivery_revision_history,
    freeze_hierarchy,
    prepare_delivery_revision,
    prepare_hierarchy,
)
from hdg.repository import SchedulerRepository

from .test_loop_architecture import (
    group_hierarchy,
    node,
    task_definition,
    task_hierarchy,
)
from .test_scheduler_runtime import at, success


class DeliveryRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = self.temporary.name

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _freeze_initial(self, hierarchy: dict) -> dict:
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        frozen = freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_delivery_revision=1,
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        self.assertEqual(frozen["deliveryRevision"], 1)
        return prepared

    def _complete_task_and_review(
        self,
        root_id: str,
        task_id: str,
    ) -> None:
        task_node_id = loop_node_id(task_id)
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=task_node_id,
            owner="agent-task",
            operation_id=f"op-{task_id}",
            now=at(2),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=task_node_id,
            operation_id=f"op-{task_id}",
            outcome=success(f"{task_id} implemented."),
            now=at(3),
        )
        review_id = task_review_node_id(task_id)
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=review_id,
            owner="agent-review",
            operation_id=f"op-{task_id}-review",
            now=at(4),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=review_id,
            operation_id=f"op-{task_id}-review",
            outcome=success(f"{task_id} independently reviewed."),
            now=at(5),
        )

    def test_replan_creates_a_new_revision_of_the_same_delivery(self) -> None:
        initial = group_hierarchy()
        prepared = self._freeze_initial(initial)
        root_id = prepared["rootId"]
        self._complete_task_and_review(root_id, "t-api")

        revised = deepcopy(initial)
        repair = node(
            task_definition(
                item_id="t-supplier-repair",
                parent_id="g-service",
                depends_on=["t-core"],
                claims=["project:erp-supplier/module:api"],
            )
        )
        revised["root"]["children"].append(repair)
        revised["root"]["definition"]["children"].append(
            {
                "id": "t-supplier-repair",
                "kind": "TASK",
                "title": "Run t-supplier-repair",
            }
        )

        revision = prepare_delivery_revision(
            root=self.root,
            root_id=root_id,
            expected_current_revision=1,
            hierarchy=revised,
            reason="用户在最终验收前补充 supplier 合同修复。",
            requested_by="human",
            now=at(6),
        )

        self.assertEqual(revision["rootId"], root_id)
        self.assertEqual(revision["deliveryRevision"], 2)
        self.assertEqual(revision["previousRevision"], 1)
        self.assertEqual(
            revision["carryForwardTaskIds"],
            ["t-api"],
        )

        frozen = freeze_hierarchy(
            root=self.root,
            root_id=root_id,
            expected_delivery_revision=2,
            expected_hierarchy_fingerprint=(
                revision["hierarchyFingerprint"]
            ),
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(7),
        )

        self.assertEqual(frozen["rootId"], root_id)
        self.assertEqual(frozen["deliveryRevision"], 2)
        self.assertEqual(frozen["carriedForwardTaskIds"], ["t-api"])
        states = {
            item["nodeId"]: item["status"]
            for item in frozen["nodes"]
        }
        self.assertEqual(states[loop_node_id("t-api")], "SUCCEEDED")
        self.assertEqual(
            states[task_review_node_id("t-api")],
            "SUCCEEDED",
        )
        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(8),
        )
        self.assertEqual(
            [
                item["nodeId"]
                for item in frontier["readyLoops"]
            ],
            [loop_node_id("t-core")],
        )

        history = delivery_revision_history(
            root=self.root,
            root_id=root_id,
        )
        self.assertEqual(history["rootId"], root_id)
        self.assertEqual(history["currentRevision"], 2)
        self.assertEqual(
            [item["revision"] for item in history["revisions"]],
            [1, 2],
        )
        self.assertEqual(
            history["revisions"][0]["runStatus"],
            "SUPERSEDED",
        )
        rebuilt = rebuild_graph_run(
            root=self.root,
            root_id=root_id,
        )
        rebuilt_states = {
            item["nodeId"]: item["status"]
            for item in rebuilt["nodes"]
        }
        self.assertEqual(
            rebuilt_states[loop_node_id("t-api")],
            "SUCCEEDED",
        )
        self.assertEqual(
            rebuilt_states[task_review_node_id("t-api")],
            "SUCCEEDED",
        )
        revisions_projection = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / "revisions.md"
        ).read_text(encoding="utf-8")
        self.assertIn("当前修订：2", revisions_projection)
        self.assertIn("SUPERSEDED", revisions_projection)

    def test_cross_project_scope_requires_exact_freeze_authorization(
        self,
    ) -> None:
        with TemporaryDirectory() as order_root, TemporaryDirectory() as supplier_root:
            hierarchy = task_hierarchy()
            hierarchy["delivery"]["projectScopes"] = [
                {
                    "id": "erp-pm",
                    "workspaceRoot": self.root,
                    "access": "READ_WRITE",
                },
                {
                    "id": "erp-order",
                    "workspaceRoot": order_root,
                    "access": "READ_WRITE",
                },
                {
                    "id": "erp-supplier",
                    "workspaceRoot": supplier_root,
                    "access": "READ_WRITE",
                },
            ]
            prepared = prepare_hierarchy(
                root=self.root,
                hierarchy=hierarchy,
                now=at(0),
            )
            self.assertEqual(
                [
                    item["id"]
                    for item in prepared[
                        "requiredProjectAuthorizations"
                    ]
                ],
                ["erp-order", "erp-pm", "erp-supplier"],
            )

            with self.assertRaises(GatedLoopError) as caught:
                freeze_hierarchy(
                    root=self.root,
                    root_id=prepared["rootId"],
                    expected_delivery_revision=1,
                    expected_hierarchy_fingerprint=(
                        prepared["hierarchyFingerprint"]
                    ),
                    authorized_project_ids=["erp-pm", "erp-order"],
                    confirmed=True,
                    confirmed_by="human",
                    now=at(1),
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_PROJECT_AUTHORIZATION_REQUIRED",
            )
            self.assertEqual(
                caught.exception.details["missingProjectIds"],
                ["erp-supplier"],
            )

            frozen = freeze_hierarchy(
                root=self.root,
                root_id=prepared["rootId"],
                expected_delivery_revision=1,
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                authorized_project_ids=[
                    "erp-supplier",
                    "erp-pm",
                    "erp-order",
                ],
                confirmed=True,
                confirmed_by="human",
                now=at(2),
            )
            self.assertEqual(
                [item["id"] for item in frozen["projectScopes"]],
                ["erp-order", "erp-pm", "erp-supplier"],
            )

    def test_revision_rejects_a_different_delivery_identity(self) -> None:
        prepared = self._freeze_initial(task_hierarchy())
        replacement = task_hierarchy()
        replacement["delivery"]["id"] = "d-other"

        with self.assertRaises(GatedLoopError) as caught:
            prepare_delivery_revision(
                root=self.root,
                root_id=prepared["rootId"],
                expected_current_revision=1,
                hierarchy=replacement,
                reason="Invalid identity change.",
                requested_by="human",
                now=at(2),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DELIVERY_IDENTITY_IMMUTABLE",
        )

    def test_cancelled_unaccepted_run_can_continue_as_a_revision(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        prepared = self._freeze_initial(hierarchy)
        cancel_graph_run(
            root=self.root,
            root_id=prepared["rootId"],
            cancelled_by="human",
            reason="旧流程为重新规划而取消。",
            now=at(2),
        )
        revised = deepcopy(hierarchy)
        revised["root"]["definition"]["summary"] = (
            "Continue the same unaccepted Delivery with a revised scope."
        )

        revision = prepare_delivery_revision(
            root=self.root,
            root_id=prepared["rootId"],
            expected_current_revision=1,
            hierarchy=revised,
            reason="恢复尚未最终验收的同一需求。",
            requested_by="human",
            now=at(3),
        )
        frozen = freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_delivery_revision=2,
            expected_hierarchy_fingerprint=(
                revision["hierarchyFingerprint"]
            ),
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(4),
        )

        self.assertEqual(frozen["deliveryRevision"], 2)
        self.assertEqual(frozen["status"], "ACTIVE")

    def test_writable_git_projects_require_the_same_branch_name(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        binding = {
            "branchRef": "feature/shared-delivery",
            "baseRef": "main",
            "baseCommit": "a" * 40,
            "integrationTarget": "main",
        }
        hierarchy["delivery"]["gitBinding"] = binding
        hierarchy["delivery"]["projectScopes"] = [
            {
                "id": "erp-pm",
                "workspaceRoot": self.root,
                "access": "READ_WRITE",
                "gitBinding": binding,
            },
            {
                "id": "erp-order",
                "workspaceRoot": str(Path(self.root) / "erp-order"),
                "access": "READ_WRITE",
                "gitBinding": {
                    **binding,
                    "branchRef": "feature/other-delivery",
                    "baseCommit": "b" * 40,
                },
            },
        ]

        with self.assertRaises(GatedLoopError) as caught:
            validate_hierarchy_definition(hierarchy)

        self.assertEqual(
            caught.exception.code,
            "DELIVERY_PROJECT_BRANCH_MISMATCH",
        )

    def test_existing_scheduler_storage_is_upgraded_for_revisions(
        self,
    ) -> None:
        control = Path(self.root) / ".layered-delivery"
        control.mkdir()
        database = control / "scheduler.db"
        connection = sqlite3.connect(database)
        connection.executescript(
            """
            CREATE TABLE hierarchies (
                root_id TEXT PRIMARY KEY,
                hierarchy_fingerprint TEXT NOT NULL,
                graph_fingerprint TEXT NOT NULL,
                hierarchy_json TEXT NOT NULL,
                graph_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                root_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                cancelled_at TEXT,
                FOREIGN KEY(root_id) REFERENCES hierarchies(root_id)
            );
            CREATE TABLE node_runs (
                run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                status TEXT NOT NULL,
                owner TEXT,
                operation_id TEXT,
                claimed_at TEXT,
                last_heartbeat_at TEXT,
                lease_expires_at TEXT,
                finished_at TEXT,
                outcome_json TEXT,
                failure_class TEXT,
                PRIMARY KEY(run_id, node_id, attempt),
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            CREATE TABLE graph_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_uuid TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                node_id TEXT,
                attempt INTEGER,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                operation_id TEXT,
                payload_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                previous_hash TEXT,
                event_hash TEXT NOT NULL UNIQUE,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            CREATE TABLE delivery_workspaces (
                root_id TEXT PRIMARY KEY,
                workspace_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(root_id) REFERENCES hierarchies(root_id)
            );
            CREATE TABLE task_requirement_states (
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(run_id, task_id),
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            """
        )
        connection.close()

        repository = SchedulerRepository(self.root)
        with repository.transaction() as upgraded:
            hierarchy_columns = {
                row["name"]
                for row in upgraded.execute(
                    "PRAGMA table_info(hierarchies)"
                )
            }
            run_columns = {
                row["name"]
                for row in upgraded.execute(
                    "PRAGMA table_info(runs)"
                )
            }
            revisions_table = upgraded.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'delivery_revisions'"
            ).fetchone()

        self.assertIn("revision", hierarchy_columns)
        self.assertIn("revision", run_columns)
        self.assertIn("superseded_at", run_columns)
        self.assertIsNotNone(revisions_table)


if __name__ == "__main__":
    unittest.main()
