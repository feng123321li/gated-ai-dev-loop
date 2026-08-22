from __future__ import annotations

import json
from typing import Any

from .errors import fail
from .jsonio import canonical_json


class DeliveryExecutionSelectionMixin:
    """Persist and read the selected execution mode for one Revision."""

    def record_automatic_selection(
        self,
        root_id: str,
        *,
        expected_hierarchy_fingerprint: str,
        expected_graph_fingerprint: str,
        authorized_project_ids: list[str],
        confirmed_by: str,
        workspace_key: str | None = None,
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            hierarchy = connection.execute(
                "SELECT * FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if hierarchy is None:
                fail(
                    "SCHEDULER_HIERARCHY_MISSING",
                    f"Unknown hierarchy: {root_id}",
                )
            if (
                hierarchy["hierarchy_fingerprint"]
                != expected_hierarchy_fingerprint
                or hierarchy["graph_fingerprint"]
                != expected_graph_fingerprint
            ):
                fail(
                    "SCHEDULER_EXECUTION_CHOICE_STALE",
                    "The selected execution choice does not match the "
                    "generated baseline",
                    rootId=root_id,
                )
            if hierarchy["status"] not in {"CHOICE_READY", "PREPARED"}:
                fail(
                    "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
                    "The Delivery is not waiting for automatic execution",
                    rootId=root_id,
                    status=hierarchy["status"],
                )
            revision = connection.execute(
                "SELECT * FROM delivery_revisions WHERE root_id = ? "
                "AND revision = ?",
                (root_id, hierarchy["revision"]),
            ).fetchone()
            if revision is None:
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "The current Delivery revision is missing",
                    rootId=root_id,
                )
            encoded_projects = canonical_json(authorized_project_ids)
            selection_already_applied = (
                revision["execution_mode"] == "automatic_pending"
            )
            if selection_already_applied and (
                revision["confirmed_by"] != confirmed_by
                or revision["authorized_project_ids_json"] != encoded_projects
            ):
                fail(
                    "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
                    "The recorded automatic choice has different human "
                    "authorization",
                    rootId=root_id,
                )
            if revision["execution_mode"] not in {None, "automatic_pending"}:
                fail(
                    "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
                    "Another execution mode has already been selected",
                    rootId=root_id,
                    executionMode=revision["execution_mode"],
                )
            at = self.commit_timestamp_fn(self.now, hierarchy["updated_at"])
            workspace_turn = None
            if workspace_key is not None:
                existing_binding = connection.execute(
                    "SELECT workspace_key FROM delivery_workspaces "
                    "WHERE root_id = ?",
                    (root_id,),
                ).fetchone()
                if (
                    existing_binding is not None
                    and existing_binding["workspace_key"] != workspace_key
                ):
                    fail(
                        "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
                        "A selected Delivery cannot move to another workspace",
                        rootId=root_id,
                    )
                connection.execute(
                    "INSERT INTO delivery_workspaces("
                    "root_id, workspace_key, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(root_id) DO UPDATE SET "
                    "updated_at = excluded.updated_at",
                    (root_id, workspace_key, at, at),
                )
            connection.execute(
                "UPDATE delivery_revisions SET confirmed_by = ?, "
                "authorized_project_ids_json = ?, execution_mode = "
                "'automatic_pending', reason = ?, updated_at = ? "
                "WHERE root_id = ? AND revision = ?",
                (
                    confirmed_by,
                    encoded_projects,
                    "用户已选择自动执行，等待当前 workspace 串行调度",
                    at,
                    root_id,
                    hierarchy["revision"],
                ),
            )
            connection.execute(
                "UPDATE hierarchies SET updated_at = ? WHERE root_id = ?",
                (at, root_id),
            )
            if workspace_key is not None:
                workspace_turn = self._serial_workspace_turn_state_from_connection(
                    connection,
                    workspace_key=workspace_key,
                    requested_root_id=root_id,
                )
        self.write_projections(root_id)
        return {
            "selection": "AUTOMATIC",
            "state": (
                (
                    "QUEUED"
                    if workspace_turn["state"] == "WAITING_FOR_WORKSPACE_TURN"
                    else workspace_turn["state"]
                )
                if workspace_turn is not None
                else "RECORDED_PENDING_WORKSPACE"
            ),
            "confirmationRequired": False,
            "confirmedBy": confirmed_by,
            "authorizedProjectIds": list(authorized_project_ids),
            "selectionAlreadyApplied": selection_already_applied,
            **(
                {"workspaceTurn": workspace_turn}
                if workspace_turn is not None
                else {}
            ),
        }

    def execution_selection(
        self,
        root_id: str,
    ) -> dict[str, Any] | None:
        with self.read() as connection:
            row = connection.execute(
                "SELECT d.* FROM delivery_revisions d "
                "JOIN hierarchies h ON h.root_id = d.root_id "
                "AND h.revision = d.revision WHERE d.root_id = ?",
                (root_id,),
            ).fetchone()
            workspace_turn = None
            if (
                row is not None
                and row["execution_mode"]
                in {"automatic_pending", "manual_pending"}
            ):
                binding = connection.execute(
                    "SELECT workspace_key FROM delivery_workspaces "
                    "WHERE root_id = ?",
                    (root_id,),
                ).fetchone()
                if binding is not None:
                    workspace_turn = self._serial_workspace_turn_state_from_connection(
                        connection,
                        workspace_key=binding["workspace_key"],
                        requested_root_id=root_id,
                    )
        if (
            row is None
            or row["execution_mode"]
            not in {"automatic_pending", "manual_pending"}
        ):
            return None
        selection = (
            "AUTOMATIC"
            if row["execution_mode"] == "automatic_pending"
            else "MANUAL"
        )
        authorized = json.loads(row["authorized_project_ids_json"] or "[]")
        return {
            "selection": selection,
            "state": (
                (
                    "QUEUED"
                    if workspace_turn["state"] == "WAITING_FOR_WORKSPACE_TURN"
                    else workspace_turn["state"]
                )
                if workspace_turn is not None
                else "RECORDED_PENDING_WORKSPACE_TURN"
            ),
            "confirmationRequired": False,
            "confirmedBy": row["confirmed_by"],
            "authorizedProjectIds": authorized,
            **(
                {"workspaceTurn": workspace_turn}
                if workspace_turn is not None
                else {}
            ),
        }


__all__ = ("DeliveryExecutionSelectionMixin",)
