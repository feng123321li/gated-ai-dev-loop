from __future__ import annotations

from .repository_hierarchy_common import (
    Any,
    canonical_json,
    fail,
    json,
    loop_node_id,
    sqlite3,
    task_review_node_id,
    uuid,
    validate_hierarchy_definition,
)


class HierarchyFreezeMixin:
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
