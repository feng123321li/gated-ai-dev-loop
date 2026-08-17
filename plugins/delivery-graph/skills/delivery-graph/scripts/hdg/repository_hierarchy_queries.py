from __future__ import annotations

from .repository_hierarchy_common import (
    Any,
    fail,
    json,
    sqlite3,
)


class HierarchyQueryMixin:
    def _run_from_connection(
        self,
        connection: sqlite3.Connection,
        root_id: str,
    ) -> dict[str, Any]:
        hierarchy_row = connection.execute(
            "SELECT * FROM hierarchies WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        if hierarchy_row is None:
            fail(
                "SCHEDULER_HIERARCHY_MISSING",
                f"Scheduler hierarchy is missing: {root_id}",
            )
        row = connection.execute(
            "SELECT * FROM runs WHERE root_id = ? AND revision = ?",
            (root_id, hierarchy_row["revision"]),
        ).fetchone()
        if row is None:
            fail(
                "SCHEDULER_RUN_MISSING",
                f"Scheduler run is missing: {root_id}",
            )
        nodes = self.latest_nodes(connection, row["run_id"])
        task_requirements = self.task_requirement_states(
            connection,
            row["run_id"],
        )
        workspace = connection.execute(
            "SELECT workspace_key FROM delivery_workspaces "
            "WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        if workspace is None:
            fail(
                "SCHEDULER_DELIVERY_WORKSPACE_MISSING",
                f"Delivery workspace binding is missing: {root_id}",
            )
        hierarchy, _ = self.validate_stored_definition(hierarchy_row)
        result = {
            "runId": row["run_id"],
            "rootId": row["root_id"],
            "deliveryRevision": row["revision"],
            "executionMode": row["execution_mode"],
            "status": row["status"],
            "startedAt": row["started_at"],
            "updatedAt": row["updated_at"],
            "completedAt": row["completed_at"],
            "cancelledAt": row["cancelled_at"],
            "supersededAt": row["superseded_at"],
            "supersededByRevision": row[
                "superseded_by_revision"
            ],
            "nodes": nodes,
            "taskRequirements": task_requirements,
            "workspaceIsolation": {
                "mode": "MULTI_DELIVERY_WORKSPACE",
                "workspaceKey": workspace["workspace_key"],
            },
        }
        git_binding = hierarchy["delivery"].get("gitBinding")
        if git_binding is not None:
            result["gitBinding"] = git_binding
        result["projectScopes"] = hierarchy["delivery"].get(
            "projectScopes",
            [],
        )
        return result

    def run(
        self,
        root_id: str,
    ) -> dict[str, Any]:
        with self.read() as connection:
            return self._run_from_connection(connection, root_id)

    def revision_history(self, root_id: str) -> dict[str, Any]:
        with self.read() as connection:
            hierarchy = connection.execute(
                "SELECT revision FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if hierarchy is None:
                fail(
                    "SCHEDULER_HIERARCHY_MISSING",
                    f"Scheduler hierarchy is missing: {root_id}",
                )
            rows = connection.execute(
                "SELECT d.*, r.run_id, r.status AS run_status, "
                "r.started_at, r.completed_at, r.cancelled_at, "
                "r.superseded_at AS run_superseded_at "
                "FROM delivery_revisions d "
                "LEFT JOIN runs r ON r.root_id = d.root_id "
                "AND r.revision = d.revision "
                "WHERE d.root_id = ? ORDER BY d.revision",
                (root_id,),
            ).fetchall()
        return {
            "rootId": root_id,
            "currentRevision": hierarchy["revision"],
            "revisions": [
                {
                    "revision": row["revision"],
                    "status": row["status"],
                    "runId": row["run_id"],
                    "runStatus": row["run_status"],
                    "hierarchyFingerprint": row[
                        "hierarchy_fingerprint"
                    ],
                    "graphFingerprint": row["graph_fingerprint"],
                    "reason": row["reason"],
                    "continuityBasis": row["continuity_basis"],
                    "requestedBy": row["requested_by"],
                    "confirmedBy": row["confirmed_by"],
                    "authorizedProjectIds": (
                        json.loads(
                            row["authorized_project_ids_json"]
                        )
                        if row["authorized_project_ids_json"]
                        else []
                    ),
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                    "frozenAt": row["frozen_at"],
                    "completedAt": row["completed_at"],
                    "cancelledAt": row["cancelled_at"],
                    "supersededAt": (
                        row["run_superseded_at"]
                        or row["superseded_at"]
                    ),
                }
                for row in rows
            ],
        }

    @staticmethod
    def task_requirement_states(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT task_id, revision, status, updated_at "
            "FROM task_requirement_states WHERE run_id = ? "
            "ORDER BY task_id",
            (run_id,),
        ).fetchall()
        return [
            {
                "taskId": row["task_id"],
                "revision": row["revision"],
                "status": row["status"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]
