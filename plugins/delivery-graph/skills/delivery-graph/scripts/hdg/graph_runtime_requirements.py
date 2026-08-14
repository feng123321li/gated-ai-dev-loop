from __future__ import annotations

from .graph_runtime_common import (
    Any,
    SchedulerRepository,
    _identity,
    _loaded,
    _locked_timestamp,
    _node,
    _validated_stored_definition,
    compile_delivery_graph,
    deepcopy,
    fail,
    graph_fingerprint,
    hierarchy_fingerprint,
    iter_hierarchy_nodes,
    json,
    validate_hierarchy_definition,
    validate_loop_descriptor,
)
from .graph_runtime_frontier import loop_context


def _task_requirement_snapshot(
    hierarchy: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    task = next(
        (
            node["definition"]
            for node in iter_hierarchy_nodes(hierarchy)
            if (
                node["definition"]["kind"] == "TASK"
                and node["definition"]["id"] == task_id
            )
        ),
        None,
    )
    if task is None:
        fail(
            "SCHEDULER_TASK_MISSING",
            f"TASK is missing from the Delivery: {task_id}",
        )
    return {
        "title": task["title"],
        "summary": task["summary"],
        "payload": deepcopy(task["execution"]["loop"]["payload"]),
    }

def _task_requirement_row(
    connection: Any,
    *,
    run_id: str,
    task_id: str,
) -> Any:
    row = connection.execute(
        "SELECT * FROM task_requirement_states "
        "WHERE run_id = ? AND task_id = ?",
        (run_id, task_id),
    ).fetchone()
    if row is None:
        fail(
            "SCHEDULER_TASK_MISSING",
            f"TASK requirement is missing: {task_id}",
        )
    return row

def _expected_requirement_revision(
    value: object,
    current: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value != current
    ):
        fail(
            "SCHEDULER_TASK_REQUIREMENT_REVISION_CONFLICT",
            "TASK requirement revision is not current",
            currentRevision=current,
        )
    return value

def _assert_task_requirement_unstarted(
    connection: Any,
    *,
    run_id: str,
    node_id: str,
    task_id: str,
    state: dict[str, Any],
) -> None:
    previously_claimed = connection.execute(
        "SELECT 1 FROM graph_events "
        "WHERE run_id = ? AND node_id = ? "
        "AND event_type = 'LOOP_CLAIMED' LIMIT 1",
        (run_id, node_id),
    ).fetchone()
    if (
        state["status"] not in {"PENDING", "READY"}
        or previously_claimed is not None
    ):
        fail(
            "SCHEDULER_TASK_ALREADY_STARTED",
            "Only an unstarted TASK requirement can be changed",
            taskId=task_id,
            taskStatus=state["status"],
        )

def _assert_no_pending_dispatch_reservations(
    repository: SchedulerRepository,
    connection: Any,
    *,
    run_id: str,
    at: str,
) -> None:
    """Keep requirement edits from invalidating unclaimed assignments."""

    repository.expire_dispatch_reservations(connection, at=at)
    rows = connection.execute(
        "SELECT reservation_id, node_id, expires_at "
        "FROM dispatch_reservations "
        "WHERE run_id = ? AND status = 'RESERVED' "
        "AND julianday(expires_at) > julianday(?) "
        "ORDER BY expires_at, node_id, reservation_id",
        (run_id, at),
    ).fetchall()
    if not rows:
        return
    reservations = [
        {
            "dispatchReservationId": row["reservation_id"],
            "nodeId": row["node_id"],
            "reservationExpiresAt": row["expires_at"],
        }
        for row in rows
    ]
    fail(
        "SCHEDULER_TASK_REQUIREMENT_RESERVATION_ACTIVE",
        "Wait for every pending dispatch reservation in this Graph Run to "
        "expire before changing a TASK requirement",
        retryAfter=reservations[0]["reservationExpiresAt"],
        dispatchReservations=reservations,
    )

def unfreeze_task_requirement(
    *,
    root: str,
    root_id: str,
    task_id: str,
    expected_revision: int,
    authorized_by: str,
    reason: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    authorized_by = _identity(authorized_by, "authorized_by")
    if not isinstance(reason, str) or not reason.strip():
        fail(
            "SCHEDULER_TASK_REQUIREMENT_CHANGE_INVALID",
            "reason must be non-empty",
        )
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        definition = next(
            (
                node
                for node in graph["nodes"]
                if (
                    node["kind"] == "TASK_LOOP"
                    and node["workItemId"] == task_id
                )
            ),
            None,
        )
        if definition is None:
            fail(
                "SCHEDULER_TASK_MISSING",
                f"TASK is missing from the Delivery: {task_id}",
            )
        _, state = _node(graph, nodes, definition["id"])
        _assert_task_requirement_unstarted(
            connection,
            run_id=run["run_id"],
            node_id=definition["id"],
            task_id=task_id,
            state=state,
        )
        requirement = _task_requirement_row(
            connection,
            run_id=run["run_id"],
            task_id=task_id,
        )
        _expected_requirement_revision(
            expected_revision,
            requirement["revision"],
        )
        if requirement["status"] != "FROZEN":
            fail(
                "SCHEDULER_TASK_REQUIREMENT_ALREADY_UNFROZEN",
                "TASK requirement is already unfrozen",
                taskId=task_id,
            )
        _assert_no_pending_dispatch_reservations(
            repository,
            connection,
            run_id=run["run_id"],
            at=at,
        )
        connection.execute(
            "UPDATE task_requirement_states "
            "SET status = 'UNFROZEN', updated_at = ? "
            "WHERE run_id = ? AND task_id = ?",
            (at, run["run_id"], task_id),
        )
        repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=definition["id"],
            attempt=state["attempt"],
            event_type="TASK_REQUIREMENT_UNFROZEN",
            actor=authorized_by,
            operation_id=None,
            payload={
                "taskId": task_id,
                "revision": requirement["revision"],
                "reason": reason.strip(),
            },
            at=at,
        )
        connection.execute(
            "UPDATE runs SET updated_at = ? WHERE run_id = ?",
            (at, run["run_id"]),
        )
        hierarchy_row = connection.execute(
            "SELECT * FROM hierarchies WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        assert hierarchy_row is not None
        hierarchy, _ = _validated_stored_definition(hierarchy_row)
        result = {
            "rootId": root_id,
            "taskRequirement": {
                "taskId": task_id,
                "revision": requirement["revision"],
                "status": "UNFROZEN",
                "updatedAt": at,
                "requirement": _task_requirement_snapshot(
                    hierarchy,
                    task_id,
                ),
            },
            "nextAction": "REFREEZE_TASK_REQUIREMENT",
        }
    repository.write_projections(root_id)
    return result

def refreeze_task_requirement(
    *,
    root: str,
    root_id: str,
    task_id: str,
    expected_revision: int,
    requirement: object,
    confirmed_by: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    confirmed_by = _identity(confirmed_by, "confirmed_by")
    if not isinstance(requirement, dict) or set(requirement) != {
        "title",
        "summary",
        "payload",
    }:
        fail(
            "SCHEDULER_TASK_REQUIREMENT_CHANGE_INVALID",
            "requirement fields must be title, summary, and payload",
        )
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        definition = next(
            (
                node
                for node in graph["nodes"]
                if (
                    node["kind"] == "TASK_LOOP"
                    and node["workItemId"] == task_id
                )
            ),
            None,
        )
        if definition is None:
            fail(
                "SCHEDULER_TASK_MISSING",
                f"TASK is missing from the Delivery: {task_id}",
            )
        _, state = _node(graph, nodes, definition["id"])
        _assert_task_requirement_unstarted(
            connection,
            run_id=run["run_id"],
            node_id=definition["id"],
            task_id=task_id,
            state=state,
        )
        requirement_row = _task_requirement_row(
            connection,
            run_id=run["run_id"],
            task_id=task_id,
        )
        _expected_requirement_revision(
            expected_revision,
            requirement_row["revision"],
        )
        if requirement_row["status"] != "UNFROZEN":
            fail(
                "SCHEDULER_TASK_REQUIREMENT_NOT_UNFROZEN",
                "TASK requirement must be unfrozen before replacement",
                taskId=task_id,
            )
        _assert_no_pending_dispatch_reservations(
            repository,
            connection,
            run_id=run["run_id"],
            at=at,
        )
        current_delivery_revision = run["revision"]
        revision_row = connection.execute(
            "SELECT * FROM delivery_revisions "
            "WHERE root_id = ? AND revision = ?",
            (root_id, current_delivery_revision),
        ).fetchone()
        if revision_row is None or revision_row["status"] != "FROZEN":
            fail(
                "SCHEDULER_REVISION_CONFLICT",
                "The current immutable Delivery revision is missing",
                rootId=root_id,
                deliveryRevision=current_delivery_revision,
            )
        prepared_candidate = connection.execute(
            "SELECT status FROM delivery_revisions "
            "WHERE root_id = ? AND revision = ?",
            (root_id, current_delivery_revision + 1),
        ).fetchone()
        if prepared_candidate is not None:
            fail(
                "SCHEDULER_REVISION_CONFLICT",
                "The next Delivery revision is already reserved",
                rootId=root_id,
                deliveryRevision=current_delivery_revision + 1,
                status=prepared_candidate["status"],
            )
        immutable_hierarchy, _ = _validated_stored_definition(
            revision_row
        )
        replacement = deepcopy(immutable_hierarchy)
        task_definition = next(
            node["definition"]
            for node in iter_hierarchy_nodes(replacement)
            if (
                node["definition"]["kind"] == "TASK"
                and node["definition"]["id"] == task_id
            )
        )
        candidate_loop = {
            **task_definition["execution"]["loop"],
            "payload": requirement.get("payload"),
        }
        task_definition["title"] = requirement.get("title")
        task_definition["summary"] = requirement.get("summary")
        task_definition["execution"]["loop"] = (
            validate_loop_descriptor(candidate_loop)
        )
        normalized = validate_hierarchy_definition(replacement)
        hierarchy_value = hierarchy_fingerprint(normalized)
        revised_graph = compile_delivery_graph(
            normalized,
            hierarchy_fingerprint=hierarchy_value,
        )
        graph_value = graph_fingerprint(revised_graph)
        requirement_snapshot = _task_requirement_snapshot(
            normalized,
            task_id,
        )
        if requirement_snapshot == _task_requirement_snapshot(
            immutable_hierarchy,
            task_id,
        ):
            fail(
                "SCHEDULER_TASK_REQUIREMENT_CHANGE_INVALID",
                "The replacement TASK requirement must change title, summary, or payload",
                taskId=task_id,
            )
        unfreeze_event = connection.execute(
            "SELECT payload_json FROM graph_events "
            "WHERE run_id = ? AND node_id = ? "
            "AND event_type = 'TASK_REQUIREMENT_UNFROZEN' "
            "ORDER BY event_id DESC LIMIT 1",
            (run["run_id"], definition["id"]),
        ).fetchone()
        unfreeze_payload = (
            json.loads(unfreeze_event["payload_json"])
            if unfreeze_event is not None
            else {}
        )
        revision_reason = unfreeze_payload.get("reason")
        if not isinstance(revision_reason, str) or not revision_reason.strip():
            fail(
                "SCHEDULER_EVENT_REPLAY_INVALID",
                "TASK requirement unfreeze reason is missing",
            )
        authorized_project_ids = json.loads(
            revision_row["authorized_project_ids_json"] or "[]"
        )
        execution_mode = run["execution_mode"]
        task_requirement_revision = requirement_row["revision"] + 1

    prepared = repository.prepare_revision(
        normalized,
        revised_graph,
        root_id=root_id,
        expected_current_revision=current_delivery_revision,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
        reason=revision_reason.strip(),
        continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
        requested_by=confirmed_by,
        workspace_root=root,
    )
    from .planning import freeze_hierarchy

    frozen = freeze_hierarchy(
        root=root,
        root_id=root_id,
        expected_delivery_revision=prepared["deliveryRevision"],
        expected_hierarchy_fingerprint=hierarchy_value,
        authorized_project_ids=authorized_project_ids,
        confirmed=True,
        confirmed_by=confirmed_by,
        workspace_root=root,
        explicit_dogfood=explicit_dogfood,
        now=now,
        _execution_mode=execution_mode,
    )
    if frozen.get("status") == "QUEUED":
        return {
            **prepared,
            "taskRequirement": {
                "taskId": task_id,
                "revision": task_requirement_revision,
                "status": "REVISION_PREPARED",
                "updatedAt": at,
                "requirement": requirement_snapshot,
            },
            "nextAction": frozen["nextAction"],
        }
    context = loop_context(
        root=root,
        root_id=root_id,
        node_id=definition["id"],
        explicit_dogfood=explicit_dogfood,
    )
    task_requirement = {
        **context["taskRequirement"],
        "requirement": requirement_snapshot,
    }
    if task_requirement["revision"] != task_requirement_revision:
        fail(
            "SCHEDULER_STATE_INVALID",
            "The revised TASK requirement was not anchored to the new Delivery revision",
            taskId=task_id,
            expectedRevision=task_requirement_revision,
            actualRevision=task_requirement["revision"],
        )
    return {
        "rootId": root_id,
        "deliveryRevision": frozen["deliveryRevision"],
        "previousRevision": current_delivery_revision,
        "runId": frozen["runId"],
        "executionMode": frozen["executionMode"],
        "status": frozen["status"],
        "hierarchyFingerprint": hierarchy_value,
        "graphFingerprint": graph_value,
        "taskRequirement": task_requirement,
        "carriedForwardTaskIds": frozen.get(
            "carriedForwardTaskIds",
            [],
        ),
        "nextAction": "READ_GRAPH_FRONTIER",
    }
