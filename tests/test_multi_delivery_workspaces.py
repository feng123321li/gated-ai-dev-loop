from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from hdg.mcp_tools import call_tool
from hdg.repository import SchedulerRepository
from hdg.workspace_identity import legacy_path_workspace_key

from .test_loop_architecture import task_hierarchy


def _isolated_task_hierarchy(delivery_id: str, task_id: str) -> dict:
    hierarchy = task_hierarchy()
    hierarchy["delivery"]["id"] = delivery_id
    hierarchy["delivery"]["title"] = f"Deliver {delivery_id}"
    definition = hierarchy["root"]["definition"]
    definition["id"] = task_id
    definition["title"] = f"Run {task_id}"
    return hierarchy


def _prepare(root: Path, workspace: Path, delivery_id: str) -> dict:
    return call_tool(
        "prepare_hierarchy",
        {
            "hierarchy": _isolated_task_hierarchy(
                delivery_id,
                f"t-{delivery_id}",
            )
        },
        root=str(root),
        workspace_root=str(workspace),
    )


def _freeze(root: Path, workspace: Path, prepared: dict) -> dict:
    return call_tool(
        "freeze_hierarchy",
        {
            "root_id": prepared["rootId"],
            "expected_delivery_revision": prepared["deliveryRevision"],
            "expected_hierarchy_fingerprint": prepared[
                "hierarchyFingerprint"
            ],
            "authorized_project_ids": [],
            "confirmed_by": "test-user",
        },
        root=str(root),
        workspace_root=str(workspace),
    )


class MultiDeliveryWorkspaceTests(unittest.TestCase):
    def test_active_and_prepared_require_explicit_delivery_selection(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()

            active = _prepare(root, workspace, "d-active")
            _freeze(root, workspace, active)
            _prepare(root, workspace, "d-prepared")

            status = call_tool(
                "workspace_status",
                {},
                root=str(root),
                workspace_root=str(workspace),
            )

            self.assertEqual(status["status"], "DELIVERY_SELECTION_REQUIRED")
            self.assertNotIn("rootId", status)
            self.assertEqual(
                {
                    item["rootId"]: item["status"]
                    for item in status["candidateDeliveries"]
                },
                {
                    "d-active": "ACTIVE",
                    "d-prepared": "PREPARED",
                },
            )

    def test_single_unfinished_delivery_wins_over_terminal_history(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()

            terminal = _prepare(root, workspace, "d-terminal")
            _freeze(root, workspace, terminal)
            call_tool(
                "cancel_graph_run",
                {
                    "root_id": terminal["rootId"],
                    "cancelled_by": "test-user",
                    "reason": "Create terminal workspace history.",
                },
                root=str(root),
                workspace_root=str(workspace),
            )
            unfinished = _prepare(root, workspace, "d-unfinished")

            status = call_tool(
                "workspace_status",
                {},
                root=str(root),
                workspace_root=str(workspace),
            )

            self.assertEqual(status["status"], "PREPARED")
            self.assertEqual(status["rootId"], unfinished["rootId"])
            self.assertNotIn("candidateDeliveries", status)

    def test_workspace_key_index_remains_non_unique(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            _prepare(root, workspace, "d-index-contract")

            connection = sqlite3.connect(
                root / ".layered-delivery" / "scheduler.db"
            )
            try:
                indexes = {
                    row[1]: row
                    for row in connection.execute(
                        "PRAGMA index_list(delivery_workspaces)"
                    ).fetchall()
                }
                columns = connection.execute(
                    "PRAGMA index_info(delivery_workspaces_by_key)"
                ).fetchall()
            finally:
                connection.close()

            self.assertIn("delivery_workspaces_by_key", indexes)
            self.assertEqual(indexes["delivery_workspaces_by_key"][2], 0)
            self.assertEqual([row[2] for row in columns], ["workspace_key"])

    def test_two_legacy_bindings_upgrade_to_the_same_stable_key(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            prepared_deliveries = []
            for delivery_id in ("d-legacy-first", "d-legacy-second"):
                prepared = _prepare(root, workspace, delivery_id)
                prepared_deliveries.append(prepared)

            legacy_key = legacy_path_workspace_key(workspace)
            stable_key = SchedulerRepository.workspace_key(workspace)
            database = root / ".layered-delivery" / "scheduler.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE delivery_workspaces SET workspace_key = ?",
                    (legacy_key,),
                )
                connection.commit()
            finally:
                connection.close()

            for prepared in prepared_deliveries:
                status = call_tool(
                    "workspace_status",
                    {"root_id": prepared["rootId"]},
                    root=str(root),
                    workspace_root=str(workspace),
                )
                self.assertEqual(status["status"], "PREPARED")
                self.assertEqual(
                    status["workspaceIsolation"]["identityVersion"],
                    "PATH_V1",
                )

            connection = sqlite3.connect(database)
            try:
                stored_keys = [
                    row[0]
                    for row in connection.execute(
                        "SELECT workspace_key FROM delivery_workspaces "
                        "ORDER BY root_id"
                    ).fetchall()
                ]
            finally:
                connection.close()

            self.assertEqual(stored_keys, [stable_key, stable_key])

    def test_serial_workspace_turns_keep_paused_and_blocked_occupants(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            blocked_workspace = root / "blocked-workspace"
            paused_workspace = root / "paused-workspace"
            terminal_workspace = root / "terminal-workspace"
            blocked_workspace.mkdir()
            paused_workspace.mkdir()
            terminal_workspace.mkdir()
            blocked = _prepare(root, blocked_workspace, "d-blocked")
            _freeze(root, blocked_workspace, blocked)
            paused = _prepare(root, paused_workspace, "d-paused")
            _freeze(root, paused_workspace, paused)
            prepared = _prepare(root, workspace, "d-prepared")
            terminal = _prepare(root, terminal_workspace, "d-terminal")
            _freeze(root, terminal_workspace, terminal)
            call_tool(
                "cancel_graph_run",
                {
                    "root_id": terminal["rootId"],
                    "cancelled_by": "test-user",
                    "reason": "Terminal turns do not occupy the workspace.",
                },
                root=str(root),
                workspace_root=str(terminal_workspace),
            )
            database = root / ".layered-delivery" / "scheduler.db"
            workspace_key = SchedulerRepository.workspace_key(workspace)
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE delivery_workspaces SET workspace_key = ?",
                    (workspace_key,),
                )
                connection.execute(
                    "UPDATE runs SET status = 'BLOCKED' "
                    "WHERE root_id = ?",
                    (blocked["rootId"],),
                )
                connection.execute(
                    "UPDATE runs SET status = 'PAUSED' WHERE root_id = ?",
                    (paused["rootId"],),
                )
                for root_id, created_at in (
                    ("d-paused", "2026-08-11T01:00:00Z"),
                    ("d-blocked", "2026-08-11T01:00:00Z"),
                    ("d-prepared", "2026-08-11T02:00:00Z"),
                    ("d-terminal", "2026-08-11T00:00:00Z"),
                ):
                    connection.execute(
                        "UPDATE delivery_workspaces SET created_at = ? "
                        "WHERE root_id = ?",
                        (created_at, root_id),
                    )
                connection.commit()
            finally:
                connection.close()

            repository = SchedulerRepository(str(root))
            self.assertEqual(
                repository.serial_workspace_turns(workspace),
                [
                    {
                        "rootId": "d-blocked",
                        "status": "BLOCKED",
                        "workspaceKey": workspace_key,
                        "createdAt": "2026-08-11T01:00:00Z",
                    },
                    {
                        "rootId": "d-paused",
                        "status": "PAUSED",
                        "workspaceKey": workspace_key,
                        "createdAt": "2026-08-11T01:00:00Z",
                    },
                    {
                        "rootId": "d-prepared",
                        "status": "PREPARED",
                        "workspaceKey": workspace_key,
                        "createdAt": "2026-08-11T02:00:00Z",
                    },
                ],
            )
            self.assertEqual(
                repository.serial_workspace_turns(
                    workspace,
                    exclude_root_id="d-blocked",
                ),
                [
                    {
                        "rootId": "d-paused",
                        "status": "PAUSED",
                        "workspaceKey": workspace_key,
                        "createdAt": "2026-08-11T01:00:00Z",
                    },
                    {
                        "rootId": "d-prepared",
                        "status": "PREPARED",
                        "workspaceKey": workspace_key,
                        "createdAt": "2026-08-11T02:00:00Z",
                    },
                ],
            )


if __name__ == "__main__":
    unittest.main()
