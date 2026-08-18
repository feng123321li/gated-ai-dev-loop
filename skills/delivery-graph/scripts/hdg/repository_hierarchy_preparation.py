from __future__ import annotations

from .graph_model import (
    GRAPH_COMPILER_CONTRACT,
    compile_delivery_graph,
    graph_fingerprint,
)
from .repository_hierarchy_common import (
    Any,
    canonical_json,
    fail,
    os,
)


class HierarchyPreparationMixin:
    def refresh_manual_handoff_graph(
        self,
        root_id: str,
        *,
        expected_hierarchy_fingerprint: str,
        expected_graph_fingerprint: str,
    ) -> dict[str, Any]:
        """Refresh only an unstarted manual handoff's runtime policy."""

        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if row is None:
                fail(
                    "SCHEDULER_HIERARCHY_MISSING",
                    f"Unknown hierarchy: {root_id}",
                )
            if (
                row["hierarchy_fingerprint"]
                != expected_hierarchy_fingerprint
                or row["graph_fingerprint"]
                != expected_graph_fingerprint
            ):
                fail(
                    "SCHEDULER_MANUAL_HANDOFF_STALE",
                    "The manual handoff fingerprints are not current",
                    rootId=root_id,
                    actualHierarchyFingerprint=row[
                        "hierarchy_fingerprint"
                    ],
                    actualGraphFingerprint=row["graph_fingerprint"],
                )
            run = connection.execute(
                "SELECT 1 FROM runs WHERE root_id = ? AND revision = ?",
                (root_id, row["revision"]),
            ).fetchone()
            if row["status"] != "HANDOFF_READY" or run is not None:
                return {
                    "refreshed": False,
                    "previousGraphFingerprint": row["graph_fingerprint"],
                    "graphFingerprint": row["graph_fingerprint"],
                    "compilerContract": GRAPH_COMPILER_CONTRACT,
                }
            hierarchy, stored_graph = self.validate_stored_definition(row)
            current_graph = compile_delivery_graph(
                hierarchy,
                hierarchy_fingerprint=row["hierarchy_fingerprint"],
            )
            current_graph_fingerprint = graph_fingerprint(current_graph)
            if stored_graph == current_graph:
                return {
                    "refreshed": False,
                    "previousGraphFingerprint": row["graph_fingerprint"],
                    "graphFingerprint": current_graph_fingerprint,
                    "compilerContract": GRAPH_COMPILER_CONTRACT,
                }
            encoded_graph = canonical_json(current_graph)
            connection.execute(
                "UPDATE hierarchies SET graph_fingerprint = ?, "
                "graph_json = ? WHERE root_id = ?",
                (current_graph_fingerprint, encoded_graph, root_id),
            )
            connection.execute(
                "UPDATE delivery_revisions SET graph_fingerprint = ?, "
                "graph_json = ? WHERE root_id = ? AND revision = ?",
                (
                    current_graph_fingerprint,
                    encoded_graph,
                    root_id,
                    row["revision"],
                ),
            )
        self.write_projections(root_id)
        return {
            "refreshed": True,
            "previousGraphFingerprint": expected_graph_fingerprint,
            "graphFingerprint": current_graph_fingerprint,
            "compilerContract": GRAPH_COMPILER_CONTRACT,
        }

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
        workspace_key: str,
    ) -> dict[str, Any]:
        """Register a frozen manual snapshot in the serial execution queue."""

        root_id = graph["rootId"]
        if not isinstance(workspace_key, str) or not workspace_key:
            fail(
                "SCHEDULER_DELIVERY_WORKSPACE_MISSING",
                "Manual handoff queue registration requires a workspace key",
                rootId=root_id,
            )
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
                    "A queued manual handoff cannot move to another workspace",
                    rootId=root_id,
                )
            if existing is not None:
                self.validate_stored_definition(existing)
                if existing["status"] == "ARCHIVED":
                    fail(
                        "SCHEDULER_DELIVERY_ARCHIVED",
                        "An archived Delivery cannot become a manual handoff",
                        rootId=root_id,
                    )
                if existing["status"] in {
                    "CHOICE_READY",
                    "HANDOFF_READY",
                }:
                    current_revision = connection.execute(
                        "SELECT execution_mode FROM delivery_revisions "
                        "WHERE root_id = ? AND revision = ?",
                        (root_id, existing["revision"]),
                    ).fetchone()
                    if (
                        current_revision is not None
                        and current_revision["execution_mode"]
                        not in {None, "manual_pending"}
                    ):
                        fail(
                            "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
                            "The Delivery already has a non-manual execution "
                            "selection",
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
                            execution_mode, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, 'HANDOFF_READY', ?, ?, ?,
                            ?, ?, 'manual_pending', ?, ?
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
                            "execution_mode = 'manual_pending', "
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
                            "authorized_project_ids_json = ?, "
                            "execution_mode = 'manual_pending', updated_at = ? "
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
                        authorized_project_ids_json, execution_mode,
                        created_at, updated_at
                    ) VALUES (
                        ?, 1, ?, ?, ?, ?, 'HANDOFF_READY', ?, ?, ?,
                        'manual_pending', ?, ?
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
            connection.execute(
                "INSERT INTO delivery_workspaces("
                "root_id, workspace_key, created_at, updated_at"
                ") VALUES (?, ?, ?, ?) "
                "ON CONFLICT(root_id) DO UPDATE SET "
                "updated_at = excluded.updated_at",
                (root_id, workspace_key, at, at),
            )
        self.write_projections(
            root_id,
            preserve_manual_updates=preserve_manual_updates,
        )
        workspace_turn = self.serial_workspace_turn_state(root_id)
        return {
            "rootId": root_id,
            "status": "HANDOFF_READY",
            "deliveryRevision": delivery_revision,
            "previousRevision": previous_revision,
            "recordedAt": at,
            "selection": "MANUAL",
            "workspaceTurn": workspace_turn,
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
