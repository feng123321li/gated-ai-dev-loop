from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Callable
import uuid

from .errors import fail
from .graph_model import loop_node_id, task_review_node_id
from .jsonio import canonical_json
from .model_core import (
    iter_hierarchy_nodes,
    validate_hierarchy_definition,
)


class DeliveryHierarchyStore:
    """Own Delivery hierarchy revisions, freezing, and run history."""

    def __init__(
        self,
        repository: Any,
        *,
        validate_stored_definition: Callable[..., Any],
        commit_timestamp_fn: Callable[..., str],
        timestamp_fn: Callable[[object], str],
    ) -> None:
        self.repository = repository
        self.validate_stored_definition = validate_stored_definition
        self.commit_timestamp_fn = commit_timestamp_fn
        self.timestamp_fn = timestamp_fn

    def __getattr__(self, name: str) -> Any:
        return getattr(self.repository, name)

    def record_manual_handoff(
        self,
        hierarchy: dict[str, Any],
        graph: dict[str, Any],
        *,
        hierarchy_fingerprint: str,
        graph_fingerprint: str,
        authorized_project_ids: list[str],
        expected_current_revision: int | None,
        continuity_basis: str | None,
        revision_reason: str | None,
        confirmed_by: str,
    ) -> dict[str, Any]:
        """Register a frozen manual snapshot without creating a Graph run."""

        root_id = graph["rootId"]
        hierarchy_json = canonical_json(hierarchy)
        graph_json = canonical_json(graph)
        previous_revision: int | None = None
        preserve_manual_updates = True
        with self.transaction() as connection:
            self._assert_delivery_requirement_available(
                connection,
                hierarchy,
            )
            existing = connection.execute(
                "SELECT * FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if existing is not None:
                self.validate_stored_definition(existing)
                if existing["status"] == "ARCHIVED":
                    fail(
                        "SCHEDULER_DELIVERY_ARCHIVED",
                        "An archived Delivery cannot become a manual handoff",
                        rootId=root_id,
                    )
                content_changed = (
                    existing["hierarchy_fingerprint"]
                    != hierarchy_fingerprint
                    or existing["graph_fingerprint"]
                    != graph_fingerprint
                )
                if content_changed:
                    if existing["status"] != "HANDOFF_READY":
                        fail(
                            "SCHEDULER_HANDOFF_CONTROL_STATE_CONFLICT",
                            "A manual revision requires an existing "
                            "HANDOFF_READY Delivery",
                            rootId=root_id,
                            status=existing["status"],
                        )
                    if (
                        not isinstance(expected_current_revision, int)
                        or isinstance(expected_current_revision, bool)
                        or continuity_basis
                        != "USER_EXPLICIT_SAME_DELIVERY"
                        or not isinstance(revision_reason, str)
                        or not revision_reason.strip()
                    ):
                        fail(
                            "SCHEDULER_MANUAL_REVISION_CONTINUITY_REQUIRED",
                            "Changed manual content must explicitly continue "
                            "the same Delivery revision",
                            rootId=root_id,
                            currentRevision=existing["revision"],
                            requiredContinuityBasis=(
                                "USER_EXPLICIT_SAME_DELIVERY"
                            ),
                            nextAction=(
                                "CREATE_MANUAL_REVISION_IN_EXISTING_DIRECTORY"
                            ),
                        )
                    if expected_current_revision != existing["revision"]:
                        fail(
                            "SCHEDULER_REVISION_CONFLICT",
                            "The expected manual Delivery revision is not "
                            "current",
                            rootId=root_id,
                            expectedRevision=expected_current_revision,
                            actualRevision=existing["revision"],
                        )
                    previous_revision = existing["revision"]
                    delivery_revision = previous_revision + 1
                    at = self.commit_timestamp_fn(
                        self.now,
                        existing["updated_at"],
                    )
                    connection.execute(
                        "UPDATE delivery_revisions SET status = "
                        "'SUPERSEDED', updated_at = ?, superseded_at = ? "
                        "WHERE root_id = ? AND revision = ?",
                        (at, at, root_id, previous_revision),
                    )
                    connection.execute(
                        "UPDATE hierarchies SET revision = ?, "
                        "hierarchy_fingerprint = ?, graph_fingerprint = ?, "
                        "hierarchy_json = ?, graph_json = ?, "
                        "status = 'HANDOFF_READY', updated_at = ? "
                        "WHERE root_id = ?",
                        (
                            delivery_revision,
                            hierarchy_fingerprint,
                            graph_fingerprint,
                            hierarchy_json,
                            graph_json,
                            at,
                            root_id,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO delivery_revisions(
                            root_id, revision, hierarchy_fingerprint,
                            graph_fingerprint, hierarchy_json, graph_json,
                            status, reason, continuity_basis, requested_by,
                            confirmed_by, authorized_project_ids_json,
                            created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, 'HANDOFF_READY', ?, ?, ?,
                            ?, ?, ?, ?
                        )
                        """,
                        (
                            root_id,
                            delivery_revision,
                            hierarchy_fingerprint,
                            graph_fingerprint,
                            hierarchy_json,
                            graph_json,
                            revision_reason.strip(),
                            continuity_basis,
                            confirmed_by,
                            confirmed_by,
                            canonical_json(authorized_project_ids),
                            at,
                            at,
                        ),
                    )
                else:
                    delivery_revision = existing["revision"]
                    previous_revision = (
                        delivery_revision - 1
                        if delivery_revision > 1
                        else None
                    )
                    if (
                        expected_current_revision is not None
                        and expected_current_revision != delivery_revision
                    ):
                        fail(
                            "SCHEDULER_REVISION_CONFLICT",
                            "The expected manual Delivery revision is not "
                            "current",
                            rootId=root_id,
                            expectedRevision=expected_current_revision,
                            actualRevision=delivery_revision,
                        )
                    if existing["status"] == "CHOICE_READY":
                        preserve_manual_updates = False
                        if any(
                            value is not None
                            for value in (
                                expected_current_revision,
                                continuity_basis,
                                revision_reason,
                            )
                        ):
                            fail(
                                "SCHEDULER_REVISION_CONFLICT",
                                "An initial staged choice cannot declare a "
                                "previous Delivery revision",
                                rootId=root_id,
                            )
                        at = self.commit_timestamp_fn(
                            self.now,
                            existing["updated_at"],
                        )
                        connection.execute(
                            "UPDATE hierarchies SET status = "
                            "'HANDOFF_READY', updated_at = ? "
                            "WHERE root_id = ?",
                            (at, root_id),
                        )
                        connection.execute(
                            "UPDATE delivery_revisions SET status = "
                            "'HANDOFF_READY', reason = ?, confirmed_by = ?, "
                            "authorized_project_ids_json = ?, "
                            "updated_at = ? WHERE root_id = ? "
                            "AND revision = ?",
                            (
                                "手动开发需求快照（已冻结，未创建 Graph Run）",
                                confirmed_by,
                                canonical_json(authorized_project_ids),
                                at,
                                root_id,
                                delivery_revision,
                            ),
                        )
                    elif existing["status"] == "HANDOFF_READY":
                        at = self.commit_timestamp_fn(
                            self.now,
                            existing["updated_at"],
                        )
                        connection.execute(
                            "UPDATE hierarchies SET updated_at = ? "
                            "WHERE root_id = ?",
                            (at, root_id),
                        )
                        connection.execute(
                            "UPDATE delivery_revisions SET confirmed_by = ?, "
                            "authorized_project_ids_json = ?, updated_at = ? "
                            "WHERE root_id = ? AND revision = ?",
                            (
                                confirmed_by,
                                canonical_json(authorized_project_ids),
                                at,
                                root_id,
                                delivery_revision,
                            ),
                        )
                    else:
                        at = self.timestamp_fn(self.now)
            else:
                if any(
                    value is not None
                    for value in (
                        expected_current_revision,
                        continuity_basis,
                        revision_reason,
                    )
                ):
                    fail(
                        "SCHEDULER_REVISION_CONFLICT",
                        "An initial manual handoff cannot declare a previous "
                        "Delivery revision",
                        rootId=root_id,
                    )
                delivery_revision = 1
                at = self.timestamp_fn(self.now)
                connection.execute(
                    """
                    INSERT INTO hierarchies(
                        root_id, revision, hierarchy_fingerprint,
                        graph_fingerprint, hierarchy_json, graph_json,
                        status, created_at, updated_at
                    ) VALUES (?, 1, ?, ?, ?, ?, 'HANDOFF_READY', ?, ?)
                    """,
                    (
                        root_id,
                        hierarchy_fingerprint,
                        graph_fingerprint,
                        hierarchy_json,
                        graph_json,
                        at,
                        at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO delivery_revisions(
                        root_id, revision, hierarchy_fingerprint,
                        graph_fingerprint, hierarchy_json, graph_json,
                        status, reason, confirmed_by,
                        authorized_project_ids_json, created_at, updated_at
                    ) VALUES (
                        ?, 1, ?, ?, ?, ?, 'HANDOFF_READY', ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        root_id,
                        hierarchy_fingerprint,
                        graph_fingerprint,
                        hierarchy_json,
                        graph_json,
                        "手动开发需求快照（已冻结，未创建 Graph Run）",
                        confirmed_by,
                        canonical_json(authorized_project_ids),
                        at,
                        at,
                    ),
                )
        self.write_projections(
            root_id,
            preserve_manual_updates=preserve_manual_updates,
        )
        return {
            "rootId": root_id,
            "status": "HANDOFF_READY",
            "deliveryRevision": delivery_revision,
            "previousRevision": previous_revision,
            "recordedAt": at,
        }

    def prepare(
        self,
        hierarchy: dict[str, Any],
        graph: dict[str, Any],
        *,
        hierarchy_fingerprint: str,
        graph_fingerprint: str,
        workspace_root: str | os.PathLike[str],
    ) -> dict[str, Any]:
        root_id = graph["rootId"]
        workspace_key = self.workspace_key(workspace_root)
        with self.transaction() as connection:
            self._assert_delivery_requirement_available(
                connection,
                hierarchy,
            )
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
                    "A prepared Delivery cannot move to another workspace",
                    rootId=root_id,
                )
            frozen = connection.execute(
                "SELECT status, revision, hierarchy_fingerprint, "
                "graph_fingerprint, updated_at FROM hierarchies "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            existing_run = connection.execute(
                "SELECT 1 FROM runs WHERE root_id = ? LIMIT 1",
                (root_id,),
            ).fetchone()
            if (
                frozen is not None
                and frozen["status"] == "HANDOFF_READY"
                and (
                    frozen["hierarchy_fingerprint"]
                    != hierarchy_fingerprint
                    or frozen["graph_fingerprint"]
                    != graph_fingerprint
                )
            ):
                fail(
                    "SCHEDULER_HANDOFF_CONTROL_STATE_CONFLICT",
                    "Prepared hierarchy differs from the frozen manual "
                    "snapshot",
                    rootId=root_id,
                    recovery=(
                        "Create an explicit manual revision under the same "
                        "Delivery ID, then prepare that exact snapshot"
                    ),
                )
            adopting_manual = (
                frozen is not None
                and frozen["status"] == "HANDOFF_READY"
                and frozen["hierarchy_fingerprint"]
                == hierarchy_fingerprint
                and frozen["graph_fingerprint"] == graph_fingerprint
                and existing_run is None
            )
            if (
                frozen is not None
                and (
                    frozen["status"] == "FROZEN"
                    or (
                        frozen["revision"] != 1
                        and not adopting_manual
                    )
                    or existing_run is not None
                )
            ):
                fail(
                    "SCHEDULER_HIERARCHY_FROZEN",
                    "Use prepare_delivery_revision to revise a Delivery "
                    "after its first freeze",
                )
            at = self.commit_timestamp_fn(
                self.now,
                frozen["updated_at"] if frozen is not None else None,
            )
            delivery_revision = frozen["revision"] if adopting_manual else 1
            if adopting_manual:
                connection.execute(
                    "UPDATE hierarchies SET status = 'PREPARED', "
                    "updated_at = ? WHERE root_id = ?",
                    (at, root_id),
                )
                connection.execute(
                    "UPDATE delivery_revisions SET status = 'PREPARED', "
                    "updated_at = ? WHERE root_id = ? AND revision = ?",
                    (at, root_id, delivery_revision),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO hierarchies(
                        root_id, revision, hierarchy_fingerprint,
                        graph_fingerprint, hierarchy_json, graph_json,
                        status, created_at, updated_at
                    ) VALUES (?, 1, ?, ?, ?, ?, 'PREPARED', ?, ?)
                    ON CONFLICT(root_id) DO UPDATE SET
                        revision = 1,
                        hierarchy_fingerprint = excluded.hierarchy_fingerprint,
                        graph_fingerprint = excluded.graph_fingerprint,
                        hierarchy_json = excluded.hierarchy_json,
                        graph_json = excluded.graph_json,
                        status = 'PREPARED',
                        updated_at = excluded.updated_at
                    """,
                    (
                        root_id,
                        hierarchy_fingerprint,
                        graph_fingerprint,
                        canonical_json(hierarchy),
                        canonical_json(graph),
                        at,
                        at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO delivery_revisions(
                        root_id, revision, hierarchy_fingerprint,
                        graph_fingerprint, hierarchy_json, graph_json, status,
                        created_at, updated_at
                    ) VALUES (?, 1, ?, ?, ?, ?, 'PREPARED', ?, ?)
                    ON CONFLICT(root_id, revision) DO UPDATE SET
                        hierarchy_fingerprint =
                            excluded.hierarchy_fingerprint,
                        graph_fingerprint = excluded.graph_fingerprint,
                        hierarchy_json = excluded.hierarchy_json,
                        graph_json = excluded.graph_json,
                        status = 'PREPARED',
                        reason = NULL,
                        continuity_basis = NULL,
                        requested_by = NULL,
                        confirmed_by = CASE WHEN
                            delivery_revisions.execution_mode =
                                'automatic_pending'
                            THEN delivery_revisions.confirmed_by
                            ELSE NULL END,
                        authorized_project_ids_json = CASE WHEN
                            delivery_revisions.execution_mode =
                                'automatic_pending'
                            THEN delivery_revisions.authorized_project_ids_json
                            ELSE NULL END,
                        execution_mode = CASE WHEN
                            delivery_revisions.execution_mode =
                                'automatic_pending'
                            THEN delivery_revisions.execution_mode
                            ELSE NULL END,
                        updated_at = excluded.updated_at,
                        frozen_at = NULL,
                        superseded_at = NULL
                    """,
                    (
                        root_id,
                        hierarchy_fingerprint,
                        graph_fingerprint,
                        canonical_json(hierarchy),
                        canonical_json(graph),
                        at,
                        at,
                    ),
                )
            connection.execute(
                "INSERT INTO delivery_workspaces("
                "root_id, workspace_key, created_at, updated_at"
                ") VALUES (?, ?, ?, ?) "
                "ON CONFLICT(root_id) DO UPDATE SET "
                "workspace_key = excluded.workspace_key, "
                "updated_at = excluded.updated_at",
                (root_id, workspace_key, at, at),
            )
        self.write_projections(root_id)
        result = {
            "rootId": root_id,
            "deliveryRevision": delivery_revision,
            "status": "PREPARED",
            "hierarchyFingerprint": hierarchy_fingerprint,
            "graphFingerprint": graph_fingerprint,
            "workspaceIsolation": {
                "mode": "MULTI_DELIVERY_WORKSPACE",
                "workspaceKey": workspace_key,
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
        return {
            "rootId": row["root_id"],
            "deliveryRevision": row["revision"],
            "status": row["status"],
            "hierarchyFingerprint": row["hierarchy_fingerprint"],
            "graphFingerprint": row["graph_fingerprint"],
            "hierarchy": hierarchy,
            "graph": graph,
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
            if previous_run["status"] in {
                "COMPLETED",
                "SUPERSEDED",
            }:
                fail(
                    "SCHEDULER_DELIVERY_TERMINAL",
                    "Only an unaccepted active Delivery can be revised",
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
                    "SELECT 1 FROM graph_events "
                    "WHERE run_id = ? "
                    "AND event_type = 'WORKSPACE_TURN_RELEASED' "
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

    def freeze(
        self,
        root_id: str,
        *,
        expected_delivery_revision: int,
        expected_hierarchy_fingerprint: str,
        authorized_project_ids: list[str],
        confirmed_by: str,
        workspace_turn_start: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._freeze(
            root_id,
            expected_delivery_revision=expected_delivery_revision,
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed_by=confirmed_by,
            execution_mode="active",
            graph_started_by=None,
            workspace_turn_start=workspace_turn_start,
        )

    def freeze_manual_handoff(
        self,
        root_id: str,
        *,
        expected_delivery_revision: int,
        expected_hierarchy_fingerprint: str,
        authorized_project_ids: list[str],
        confirmed_by: str,
        started_by: str,
        workspace_turn_start: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._freeze(
            root_id,
            expected_delivery_revision=expected_delivery_revision,
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed_by=confirmed_by,
            execution_mode="manual",
            graph_started_by=started_by,
            workspace_turn_start=workspace_turn_start,
        )

    @staticmethod
    def _project_workspace_keys(
        workspace_turn_start: object,
    ) -> tuple[str, ...]:
        if not isinstance(workspace_turn_start, dict):
            return ()
        projects = workspace_turn_start.get("projects")
        if not isinstance(projects, list):
            return ()
        return tuple(
            sorted(
                {
                    item["workspaceKey"]
                    for item in projects
                    if isinstance(item, dict)
                    and isinstance(item.get("workspaceKey"), str)
                    and item["workspaceKey"]
                }
            )
        )

    def _assert_project_workspace_turn_owned(
        self,
        connection: sqlite3.Connection,
        *,
        root_id: str,
        delivery_revision: int,
        hierarchy_status: str,
        workspace_turn_start: dict[str, Any] | None,
    ) -> None:
        """Serialize every Controller-captured READ_WRITE checkout."""

        started_rows = connection.execute(
            "SELECT r.root_id, r.revision, r.run_id, r.status, "
            "e.event_id, e.payload_json "
            "FROM runs r "
            "JOIN hierarchies h ON h.root_id = r.root_id "
            "AND h.revision = r.revision "
            "JOIN graph_events e ON e.run_id = r.run_id "
            "WHERE e.event_type = 'GRAPH_RUN_STARTED' "
            "AND NOT EXISTS ("
            "SELECT 1 FROM graph_events released "
            "WHERE released.run_id = r.run_id "
            "AND released.event_type = 'WORKSPACE_TURN_RELEASED'"
            ") "
            "ORDER BY e.event_id ASC"
        ).fetchall()
        started_turns: list[dict[str, Any]] = []
        for started in started_rows:
            try:
                payload = json.loads(started["payload_json"])
            except (TypeError, json.JSONDecodeError):
                fail(
                    "SCHEDULER_WORKSPACE_TURN_EVIDENCE_INVALID",
                    "Stored workspace turn evidence is not valid JSON",
                    ownerRootId=started["root_id"],
                    ownerRunId=started["run_id"],
                )
            turn_start = (
                payload.get("workspaceTurnStart")
                if isinstance(payload, dict)
                else None
            )
            started_turns.append(
                {
                    "rootId": started["root_id"],
                    "deliveryRevision": started["revision"],
                    "runId": started["run_id"],
                    "status": started["status"],
                    "eventId": started["event_id"],
                    "workspaceKeys": self._project_workspace_keys(
                        turn_start
                    ),
                }
            )

        candidate_event_id: int | None = None
        candidate_keys = self._project_workspace_keys(workspace_turn_start)
        if hierarchy_status == "FROZEN":
            persisted_candidate = next(
                (
                    item
                    for item in started_turns
                    if item["rootId"] == root_id
                    and item["deliveryRevision"] == delivery_revision
                ),
                None,
            )
            if persisted_candidate is not None:
                candidate_event_id = persisted_candidate["eventId"]
                candidate_keys = persisted_candidate["workspaceKeys"]
        if not candidate_keys:
            return

        candidate_key_set = set(candidate_keys)
        conflicts = [
            item
            for item in started_turns
            if item["rootId"] != root_id
            and (
                candidate_event_id is None
                or item["eventId"] < candidate_event_id
            )
            and candidate_key_set.intersection(item["workspaceKeys"])
        ]
        if not conflicts:
            return
        owner = conflicts[0]
        conflicting_keys = sorted(
            candidate_key_set.intersection(owner["workspaceKeys"])
        )
        fail(
            "SCHEDULER_WORKSPACE_TURN_NOT_OWNED",
            "Another Delivery owns a READ_WRITE project workspace turn",
            rootId=root_id,
            workspaceKey=conflicting_keys[0],
            conflictingWorkspaceKeys=conflicting_keys,
            ownerRootId=owner["rootId"],
            ownerStatus=owner["status"],
            ownerRunId=owner["runId"],
            ownerDeliveryRevision=owner["deliveryRevision"],
            workspaceScope="READ_WRITE_PROJECT_CHECKOUTS",
            queueOrder="GRAPH_RUN_STARTED_EVENT_ID",
        )

    def _freeze(
        self,
        root_id: str,
        *,
        expected_delivery_revision: int,
        expected_hierarchy_fingerprint: str,
        authorized_project_ids: list[str],
        confirmed_by: str,
        execution_mode: str = "active",
        graph_started_by: str | None = None,
        workspace_turn_start: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if execution_mode not in {"active", "manual"}:
            fail(
                "SCHEDULER_EXECUTION_MODE_INVALID",
                "Graph execution mode must be active or manual",
                executionMode=execution_mode,
            )
        carried_forward: list[str] = []
        with self.transaction() as connection:
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
                    "An archived Delivery cannot be frozen again",
                    rootId=root_id,
                )
            if (
                not isinstance(expected_delivery_revision, int)
                or isinstance(expected_delivery_revision, bool)
                or expected_delivery_revision < 1
            ):
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "Delivery revision must be a positive integer",
                    expectedRevision=expected_delivery_revision,
                )
            if expected_delivery_revision == row["revision"]:
                revision_row = row
            elif (
                row["status"] == "FROZEN"
                and expected_delivery_revision == row["revision"] + 1
            ):
                revision_row = connection.execute(
                    "SELECT * FROM delivery_revisions "
                    "WHERE root_id = ? AND revision = ?",
                    (root_id, expected_delivery_revision),
                ).fetchone()
                if (
                    revision_row is None
                    or revision_row["status"] != "PREPARED"
                ):
                    fail(
                        "SCHEDULER_REVISION_CONFLICT",
                        "The requested Delivery revision is not prepared",
                        expectedRevision=expected_delivery_revision,
                        actualRevision=row["revision"],
                    )
            else:
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "Delivery revision is not current or next prepared",
                    expectedRevision=expected_delivery_revision,
                    actualRevision=row["revision"],
                    status=row["status"],
                )
            binding = connection.execute(
                "SELECT workspace_key FROM delivery_workspaces "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if binding is None:
                fail(
                    "SCHEDULER_DELIVERY_WORKSPACE_MISSING",
                    f"Delivery workspace binding is missing: {root_id}",
                )
            if (
                revision_row["hierarchy_fingerprint"]
                != expected_hierarchy_fingerprint
            ):
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "Hierarchy fingerprint is not current",
                )
            hierarchy, graph = self.validate_stored_definition(revision_row)
            project_scopes = hierarchy["delivery"].get(
                "projectScopes",
                [],
            )
            required_project_ids = sorted(
                item["id"] for item in project_scopes
            )
            if (
                not isinstance(authorized_project_ids, list)
                or any(
                    not isinstance(item, str) or not item
                    for item in authorized_project_ids
                )
                or len(set(authorized_project_ids))
                != len(authorized_project_ids)
            ):
                fail(
                    "SCHEDULER_PROJECT_AUTHORIZATION_REQUIRED",
                    "authorized_project_ids must contain unique project IDs",
                )
            supplied_project_ids = sorted(authorized_project_ids)
            if supplied_project_ids != required_project_ids:
                fail(
                    "SCHEDULER_PROJECT_AUTHORIZATION_REQUIRED",
                    "Freeze requires exact authorization of every project "
                    "in this Delivery revision",
                    requiredProjectIds=required_project_ids,
                    suppliedProjectIds=supplied_project_ids,
                    missingProjectIds=sorted(
                        set(required_project_ids)
                        - set(supplied_project_ids)
                    ),
                    unknownProjectIds=sorted(
                        set(supplied_project_ids)
                        - set(required_project_ids)
                    ),
                )
            workspace_owner = connection.execute(
                "SELECT w.root_id, w.created_at, "
                "CASE WHEN r.status IS NOT NULL THEN r.status "
                "ELSE h.status END AS effective_status "
                "FROM delivery_workspaces w "
                "JOIN hierarchies h ON h.root_id = w.root_id "
                "LEFT JOIN runs r ON r.root_id = h.root_id "
                "AND r.revision = h.revision "
                "WHERE w.workspace_key = ? "
                "AND NOT EXISTS ("
                "SELECT 1 FROM graph_events e "
                "WHERE e.run_id = r.run_id "
                "AND e.event_type = 'WORKSPACE_TURN_RELEASED'"
                ") "
                "ORDER BY w.created_at ASC, w.root_id ASC LIMIT 1",
                (binding["workspace_key"],),
            ).fetchone()
            if (
                workspace_owner is not None
                and workspace_owner["root_id"] != root_id
            ):
                fail(
                    "SCHEDULER_WORKSPACE_TURN_NOT_OWNED",
                    "Another Delivery owns the serial workspace turn",
                    rootId=root_id,
                    workspaceKey=binding["workspace_key"],
                    ownerRootId=workspace_owner["root_id"],
                    ownerStatus=workspace_owner["effective_status"],
                    ownerCreatedAt=workspace_owner["created_at"],
                    queueOrder="WORKSPACE_BINDING_CREATED_AT_ROOT_ID",
                )
            self._assert_project_workspace_turn_owned(
                connection,
                root_id=root_id,
                delivery_revision=expected_delivery_revision,
                hierarchy_status=row["status"],
                workspace_turn_start=workspace_turn_start,
            )
            if (
                row["status"] == "FROZEN"
                and row["revision"] == expected_delivery_revision
            ):
                return self._run_from_connection(connection, root_id)
            at = self.commit_timestamp_fn(
                self.now,
                max(row["updated_at"], revision_row["updated_at"]),
            )
            previous_run = None
            previous_hierarchy: dict[str, Any] | None = None
            previous_nodes: dict[str, dict[str, Any]] = {}
            previous_requirement_revisions: dict[str, int] = {}
            if expected_delivery_revision > 1:
                previous_revision = expected_delivery_revision - 1
                previous_definition = connection.execute(
                    "SELECT * FROM delivery_revisions "
                    "WHERE root_id = ? AND revision = ?",
                    (root_id, previous_revision),
                ).fetchone()
                previous_run = connection.execute(
                    "SELECT * FROM runs "
                    "WHERE root_id = ? AND revision = ?",
                    (root_id, previous_revision),
                ).fetchone()
                if previous_definition is None:
                    fail(
                        "SCHEDULER_REVISION_CONFLICT",
                        "The previous Delivery revision is missing",
                    )
                previous_is_manual = (
                    previous_definition["status"] == "SUPERSEDED"
                    and previous_definition["confirmed_by"] is not None
                    and previous_definition[
                        "authorized_project_ids_json"
                    ]
                    is not None
                    and previous_definition["execution_mode"] is None
                )
                if previous_run is None and not previous_is_manual:
                    fail(
                        "SCHEDULER_REVISION_CONFLICT",
                        "The previous Delivery run is missing",
                    )
                if previous_run is not None:
                    previous_hierarchy = validate_hierarchy_definition(
                        json.loads(previous_definition["hierarchy_json"]),
                        enforce_resource_limits=False,
                    )
                    previous_node_values = self.latest_nodes(
                        connection,
                        previous_run["run_id"],
                    )
                    carried_forward = self._carriable_task_ids(
                        previous_hierarchy,
                        hierarchy,
                        previous_node_values,
                    )
                    previous_nodes = {
                        item["nodeId"]: item
                        for item in previous_node_values
                    }
                    previous_requirement_revisions = {
                        item["taskId"]: item["revision"]
                        for item in self.task_requirement_states(
                            connection,
                            previous_run["run_id"],
                        )
                    }
                    connection.execute(
                        "UPDATE node_runs SET status = 'CANCELLED', "
                        "finished_at = COALESCE(finished_at, ?) "
                        "WHERE run_id = ? AND status NOT IN "
                        "('SUCCEEDED', 'COMPLETED', 'CANCELLED')",
                        (at, previous_run["run_id"]),
                    )
                    self._append_event(
                        connection,
                        run_id=previous_run["run_id"],
                        node_id=None,
                        attempt=None,
                        event_type="GRAPH_RUN_SUPERSEDED",
                        actor="USER",
                        operation_id=None,
                        payload={
                            "fromRevision": previous_revision,
                            "toRevision": expected_delivery_revision,
                            "confirmedBy": confirmed_by,
                        },
                        at=at,
                    )
                    connection.execute(
                        "UPDATE runs SET status = 'SUPERSEDED', "
                        "updated_at = ?, superseded_at = ?, "
                        "superseded_by_revision = ? WHERE run_id = ?",
                        (
                            at,
                            at,
                            expected_delivery_revision,
                            previous_run["run_id"],
                        ),
                    )
                connection.execute(
                    "UPDATE delivery_revisions "
                    "SET status = 'SUPERSEDED', updated_at = ?, "
                    "superseded_at = ? "
                    "WHERE root_id = ? AND revision = ?",
                    (at, at, root_id, previous_revision),
                )
            requirement_revisions = (
                self._next_task_requirement_revisions(
                    previous_hierarchy,
                    hierarchy,
                    previous_requirement_revisions,
                )
            )
            run_id = f"run-{uuid.uuid4().hex}"
            connection.execute(
                "UPDATE hierarchies SET revision = ?, "
                "hierarchy_fingerprint = ?, graph_fingerprint = ?, "
                "hierarchy_json = ?, graph_json = ?, status = 'FROZEN', "
                "updated_at = ? WHERE root_id = ?",
                (
                    expected_delivery_revision,
                    revision_row["hierarchy_fingerprint"],
                    revision_row["graph_fingerprint"],
                    revision_row["hierarchy_json"],
                    revision_row["graph_json"],
                    at,
                    root_id,
                ),
            )
            connection.execute(
                "INSERT INTO runs(run_id, root_id, revision, "
                "execution_mode, status, "
                "started_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)",
                (
                    run_id,
                    root_id,
                    expected_delivery_revision,
                    execution_mode,
                    at,
                    at,
                ),
            )
            for node in graph["nodes"]:
                carried_task = (
                    node["workItemId"]
                    if (
                        node["kind"]
                        in {"TASK_LOOP", "TASK_REVIEW_LOOP"}
                        and node["workItemId"] in carried_forward
                    )
                    else None
                )
                previous_state = (
                    previous_nodes.get(node["id"])
                    if carried_task is not None
                    else None
                )
                status = (
                    "SUCCEEDED"
                    if previous_state is not None
                    else "PENDING"
                )
                connection.execute(
                    "INSERT INTO node_runs("
                    "run_id, node_id, attempt, status, finished_at, "
                    "outcome_json, failure_class"
                    ") VALUES (?, ?, 1, ?, ?, ?, ?)",
                    (
                        run_id,
                        node["id"],
                        status,
                        at if previous_state is not None else None,
                        (
                            canonical_json(previous_state["outcome"])
                            if (
                                previous_state is not None
                                and previous_state["outcome"] is not None
                            )
                            else None
                        ),
                        (
                            previous_state["failureClass"]
                            if previous_state is not None
                            else None
                        ),
                    ),
                )
                if node["kind"] == "TASK_LOOP":
                    connection.execute(
                        "INSERT INTO task_requirement_states("
                        "run_id, task_id, revision, status, updated_at"
                        ") VALUES (?, ?, ?, 'FROZEN', ?)",
                        (
                            run_id,
                            node["workItemId"],
                            requirement_revisions[node["workItemId"]],
                            at,
                        ),
                    )
            self._append_event(
                connection,
                run_id=run_id,
                node_id=None,
                attempt=None,
                event_type="GRAPH_RUN_STARTED",
                actor=graph_started_by or "USER",
                operation_id=None,
                payload={
                    "deliveryRevision": expected_delivery_revision,
                    "previousRevision": (
                        expected_delivery_revision - 1
                        if expected_delivery_revision > 1
                        else None
                    ),
                    "authorizedProjectIds": required_project_ids,
                    "executionMode": execution_mode,
                    "taskRequirementRevisions": requirement_revisions,
                    **(
                        {"startedBy": graph_started_by}
                        if graph_started_by is not None
                        else {}
                    ),
                    **(
                        {"workspaceTurnStart": workspace_turn_start}
                        if workspace_turn_start is not None
                        else {}
                    ),
                },
                at=at,
            )
            for task_id in carried_forward:
                for node_id in (
                    loop_node_id(task_id),
                    task_review_node_id(task_id),
                ):
                    self._append_event(
                        connection,
                        run_id=run_id,
                        node_id=node_id,
                        attempt=1,
                        event_type="NODE_RESULT_CARRIED_FORWARD",
                        actor="CONTROLLER",
                        operation_id=None,
                        payload={
                            "taskId": task_id,
                            "fromRevision": (
                                expected_delivery_revision - 1
                            ),
                            "outcome": previous_nodes[node_id][
                                "outcome"
                            ],
                            "failureClass": previous_nodes[node_id][
                                "failureClass"
                            ],
                            "requirementRevision": (
                                requirement_revisions[task_id]
                            ),
                        },
                        at=at,
                    )
            connection.execute(
                "UPDATE delivery_revisions SET status = 'FROZEN', "
                "confirmed_by = ?, authorized_project_ids_json = ?, "
                "execution_mode = ?, updated_at = ?, frozen_at = ? "
                "WHERE root_id = ? AND revision = ?",
                (
                    confirmed_by,
                    canonical_json(required_project_ids),
                    execution_mode,
                    at,
                    at,
                    root_id,
                    expected_delivery_revision,
                ),
            )
            self.refresh_ready(connection, graph, run_id, at=at)
        self.write_projections(root_id)
        result = self.run(root_id)
        result["carriedForwardTaskIds"] = carried_forward
        return result

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
        if row["host_capacity_reset_at"] is not None:
            result["hostCapacity"] = {
                "status": "OPEN",
                "capacityKey": row["host_capacity_key"],
                "resetAt": row["host_capacity_reset_at"],
                "reportedAt": row["host_capacity_reported_at"],
                "reason": row["host_capacity_reason"],
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
