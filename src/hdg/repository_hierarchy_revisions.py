from __future__ import annotations

from .graph_model import GRAPH_COMPILER_CONTRACT
from .repository_hierarchy_common import (
    Any,
    canonical_json,
    fail,
    iter_hierarchy_nodes,
    json,
    loop_node_id,
    os,
    task_review_node_id,
    validate_hierarchy_definition,
)


class HierarchyRevisionMixin:
    def hierarchy(
        self,
        root_id: str | None = None,
    ) -> dict[str, Any]:
        with self.read() as connection:
            if root_id is None:
                row = connection.execute(
                    "SELECT * FROM hierarchies "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM hierarchies WHERE root_id = ?",
                    (root_id,),
                ).fetchone()
        if row is None:
            fail(
                "SCHEDULER_HIERARCHY_MISSING",
                "Scheduler hierarchy is missing",
            )
        hierarchy, graph = self.validate_stored_definition(row)
        compiler_contract = (
            graph.get("runtime", {}).get("compilerContract")
            if isinstance(graph.get("runtime"), dict)
            else None
        )
        graph_compatibility = {
            "state": (
                "CURRENT"
                if compiler_contract == GRAPH_COMPILER_CONTRACT
                else "REFRESH_ON_MANUAL_START"
            ),
            "compilerContract": compiler_contract,
            "currentCompilerContract": GRAPH_COMPILER_CONTRACT,
        }
        return {
            "rootId": row["root_id"],
            "deliveryRevision": row["revision"],
            "status": row["status"],
            "hierarchyFingerprint": row["hierarchy_fingerprint"],
            "graphFingerprint": row["graph_fingerprint"],
            "hierarchy": hierarchy,
            "graph": graph,
            "graphCompatibility": graph_compatibility,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def revision_hierarchy(
        self,
        root_id: str,
        revision: int,
    ) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                "SELECT * FROM delivery_revisions "
                "WHERE root_id = ? AND revision = ?",
                (root_id, revision),
            ).fetchone()
        if row is None:
            fail(
                "SCHEDULER_REVISION_MISSING",
                "The requested Delivery revision is missing",
                rootId=root_id,
                deliveryRevision=revision,
            )
        hierarchy, _graph = self.validate_stored_definition(row)
        return hierarchy

    @staticmethod
    def _carriable_task_ids(
        previous_hierarchy: dict[str, Any],
        revised_hierarchy: dict[str, Any],
        previous_nodes: list[dict[str, Any]],
    ) -> list[str]:
        previous_tasks = {
            item["definition"]["id"]: item
            for item in iter_hierarchy_nodes(previous_hierarchy)
            if item["definition"]["kind"] == "TASK"
        }
        revised_tasks = {
            item["definition"]["id"]: item
            for item in iter_hierarchy_nodes(revised_hierarchy)
            if item["definition"]["kind"] == "TASK"
        }
        state = {
            item["nodeId"]: item["status"]
            for item in previous_nodes
        }
        result = []
        for task_id, revised in revised_tasks.items():
            previous = previous_tasks.get(task_id)
            if (
                previous is None
                or previous["definition"] != revised["definition"]
                or previous["reviewLoop"] != revised["reviewLoop"]
                or state.get(loop_node_id(task_id)) != "SUCCEEDED"
                or state.get(task_review_node_id(task_id)) != "SUCCEEDED"
            ):
                continue
            result.append(task_id)
        return sorted(result)

    @staticmethod
    def _task_requirement_material(
        hierarchy: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        return {
            item["definition"]["id"]: {
                "title": item["definition"]["title"],
                "summary": item["definition"]["summary"],
                "payload": item["definition"]["execution"]["loop"][
                    "payload"
                ],
            }
            for item in iter_hierarchy_nodes(hierarchy)
            if item["definition"]["kind"] == "TASK"
        }

    @classmethod
    def _next_task_requirement_revisions(
        cls,
        previous_hierarchy: dict[str, Any] | None,
        revised_hierarchy: dict[str, Any],
        previous_revisions: dict[str, int],
    ) -> dict[str, int]:
        revised = cls._task_requirement_material(revised_hierarchy)
        if previous_hierarchy is None:
            return {task_id: 1 for task_id in revised}
        previous = cls._task_requirement_material(previous_hierarchy)
        return {
            task_id: (
                1
                if task_id not in previous
                else previous_revisions.get(task_id, 1)
                + (previous[task_id] != material)
            )
            for task_id, material in revised.items()
        }

    def prepare_revision(
        self,
        hierarchy: dict[str, Any],
        graph: dict[str, Any],
        *,
        root_id: str,
        expected_current_revision: int,
        hierarchy_fingerprint: str,
        graph_fingerprint: str,
        reason: str,
        continuity_basis: str,
        requested_by: str,
        workspace_root: str | os.PathLike[str],
    ) -> dict[str, Any]:
        workspace_key = self.workspace_key(workspace_root)
        with self.transaction() as connection:
            self._assert_delivery_requirement_available(
                connection,
                hierarchy,
            )
            row = connection.execute(
                "SELECT * FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if row is None:
                fail(
                    "SCHEDULER_HIERARCHY_MISSING",
                    f"Scheduler hierarchy is missing: {root_id}",
                )
            if row["status"] == "ARCHIVED":
                fail(
                    "SCHEDULER_DELIVERY_ARCHIVED",
                    "An archived Delivery cannot be revised",
                    rootId=root_id,
                )
            closure = self.delivery_closure_from_connection(
                connection,
                root_id,
            )
            if closure["state"] == "CLOSED":
                fail(
                    "SCHEDULER_DELIVERY_CLOSED",
                    "An already delivered Delivery cannot accept another "
                    "revision; start a new Delivery instead",
                    rootId=root_id,
                )
            binding = connection.execute(
                "SELECT workspace_key FROM delivery_workspaces "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if (
                binding is None
                or binding["workspace_key"] != workspace_key
            ):
                fail(
                    "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
                    "A Delivery revision must stay in its bound workspace",
                    rootId=root_id,
                )
            if hierarchy["delivery"]["id"] != root_id:
                fail(
                    "SCHEDULER_DELIVERY_IDENTITY_IMMUTABLE",
                    "A Delivery revision must retain the original "
                    "Delivery ID",
                    rootId=root_id,
                )
            if (
                not isinstance(expected_current_revision, int)
                or isinstance(expected_current_revision, bool)
                or expected_current_revision < 1
            ):
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "expected_current_revision must be a positive integer",
                )
            preparing_revision = expected_current_revision + 1
            candidate_row = connection.execute(
                "SELECT * FROM delivery_revisions "
                "WHERE root_id = ? AND revision = ?",
                (root_id, preparing_revision),
            ).fetchone()
            is_reprepare = (
                candidate_row is not None
                and candidate_row["status"] == "PREPARED"
            )
            if (
                row["status"] != "FROZEN"
                or row["revision"] != expected_current_revision
            ):
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "The expected Delivery revision is not current",
                    expectedRevision=expected_current_revision,
                    actualRevision=row["revision"],
                    status=row["status"],
                )
            if candidate_row is not None and not is_reprepare:
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "The next Delivery revision is not a prepared candidate",
                    expectedRevision=preparing_revision,
                    status=candidate_row["status"],
                )
            previous_revision_row = connection.execute(
                "SELECT * FROM delivery_revisions "
                "WHERE root_id = ? AND revision = ?",
                (root_id, expected_current_revision),
            ).fetchone()
            previous_run = connection.execute(
                "SELECT * FROM runs WHERE root_id = ? AND revision = ?",
                (root_id, expected_current_revision),
            ).fetchone()
            if previous_revision_row is None or previous_run is None:
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "The previous frozen Delivery revision is missing",
                )
            if previous_run["status"] == "SUPERSEDED":
                fail(
                    "SCHEDULER_DELIVERY_TERMINAL",
                    "A superseded Delivery run cannot be revised",
                    runStatus=previous_run["status"],
                )
            previous_hierarchy = validate_hierarchy_definition(
                json.loads(previous_revision_row["hierarchy_json"]),
                enforce_resource_limits=False,
            )
            previous_nodes = self.latest_nodes(
                connection,
                previous_run["run_id"],
            )
            if continuity_basis == "ACTIVE_LOOP_REPLAN" and not any(
                item["failureClass"] == "REPLAN_REQUIRED"
                for item in previous_nodes
            ):
                fail(
                    "SCHEDULER_REVISION_CONTINUITY_REQUIRED",
                    "ACTIVE_LOOP_REPLAN requires a recorded replan outcome",
                )
            carry_forward = self._carriable_task_ids(
                previous_hierarchy,
                hierarchy,
                previous_nodes,
            )
            at = self.commit_timestamp_fn(
                self.now,
                max(
                    row["updated_at"],
                    (
                        candidate_row["updated_at"]
                        if candidate_row is not None
                        else row["updated_at"]
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO delivery_revisions(
                    root_id, revision, hierarchy_fingerprint,
                    graph_fingerprint, hierarchy_json, graph_json, status,
                    reason, continuity_basis, requested_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?, ?, ?, ?)
                ON CONFLICT(root_id, revision) DO UPDATE SET
                    hierarchy_fingerprint =
                        excluded.hierarchy_fingerprint,
                    graph_fingerprint = excluded.graph_fingerprint,
                    hierarchy_json = excluded.hierarchy_json,
                    graph_json = excluded.graph_json,
                    status = 'PREPARED',
                    reason = excluded.reason,
                    continuity_basis = excluded.continuity_basis,
                    requested_by = excluded.requested_by,
                    updated_at = excluded.updated_at
                """,
                (
                    root_id,
                    preparing_revision,
                    hierarchy_fingerprint,
                    graph_fingerprint,
                    canonical_json(hierarchy),
                    canonical_json(graph),
                    reason,
                    continuity_basis,
                    requested_by,
                    at,
                    at,
                ),
            )
            if not is_reprepare:
                released_turn = connection.execute(
                    "SELECT 1 FROM graph_events released "
                    "WHERE released.run_id = ? "
                    "AND released.event_type = 'WORKSPACE_TURN_RELEASED' "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM graph_events requeued "
                    "WHERE requeued.run_id = released.run_id "
                    "AND requeued.event_type = 'WORKSPACE_TURN_REQUEUED' "
                    "AND requeued.event_id > released.event_id"
                    ") "
                    "LIMIT 1",
                    (previous_run["run_id"],),
                ).fetchone()
                if released_turn is not None:
                    connection.execute(
                        "UPDATE delivery_workspaces SET created_at = ?, "
                        "updated_at = ? WHERE root_id = ?",
                        (at, at, root_id),
                    )
        self.write_projections(root_id)
        return {
            "rootId": root_id,
            "deliveryRevision": preparing_revision,
            "previousRevision": expected_current_revision,
            "status": "PREPARED",
            "hierarchyFingerprint": hierarchy_fingerprint,
            "graphFingerprint": graph_fingerprint,
            "carryForwardTaskIds": carry_forward,
            "projectScopes": hierarchy["delivery"].get(
                "projectScopes",
                [],
            ),
            "workspaceIsolation": {
                "mode": "MULTI_DELIVERY_WORKSPACE",
                "workspaceKey": workspace_key,
            },
        }
