from __future__ import annotations

from .graph_runtime_common import (
    Any,
    GRAPH_EXECUTION_MODES,
    HOST_ADAPTER_AGENTS,
    SchedulerRepository,
    _dispatch_mode_allowed,
    _locked_timestamp,
    fail,
    json,
    validate_progress_event_payload,
)


def archive_delivery(
    *,
    root: str,
    root_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Hide one completed Delivery while retaining its audit history."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as connection:
        hierarchy = connection.execute(
            "SELECT status, revision, updated_at FROM hierarchies "
            "WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        if hierarchy is None:
            fail(
                "SCHEDULER_DELIVERY_MISSING",
                f"No Delivery to archive: {root_id}",
            )
        run = connection.execute(
            "SELECT status, updated_at FROM runs "
            "WHERE root_id = ? AND revision = ?",
            (root_id, hierarchy["revision"]),
        ).fetchone()
        revision = connection.execute(
            "SELECT status FROM delivery_revisions "
            "WHERE root_id = ? AND revision = ?",
            (root_id, hierarchy["revision"]),
        ).fetchone()
        if hierarchy["status"] == "ARCHIVED":
            if (
                run is None
                or run["status"] != "COMPLETED"
                or revision is None
                or revision["status"] != "ARCHIVED"
            ):
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "An archived Delivery must retain its completed run "
                    "and revision",
                    rootId=root_id,
                )
            archived_at = hierarchy["updated_at"]
            already_archived = True
        else:
            if run is None or run["status"] != "COMPLETED":
                fail(
                    "SCHEDULER_DELIVERY_NOT_COMPLETED",
                    "Only a completed Delivery can be archived",
                    rootId=root_id,
                    runStatus=(run["status"] if run is not None else None),
                )
            if hierarchy["status"] != "FROZEN":
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "A completed Delivery has an invalid hierarchy status",
                    rootId=root_id,
                    hierarchyStatus=hierarchy["status"],
                )
            if revision is None or revision["status"] != "FROZEN":
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "The completed Delivery revision is not frozen",
                    rootId=root_id,
                )
            archived_at = _locked_timestamp(
                now,
                max(hierarchy["updated_at"], run["updated_at"]),
            )
            connection.execute(
                "UPDATE hierarchies SET status = 'ARCHIVED', updated_at = ? "
                "WHERE root_id = ?",
                (archived_at, root_id),
            )
            updated_revision = connection.execute(
                "UPDATE delivery_revisions SET status = 'ARCHIVED', "
                "updated_at = ? WHERE root_id = ? AND revision = ?",
                (archived_at, root_id, hierarchy["revision"]),
            )
            if updated_revision.rowcount != 1:
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "The completed Delivery revision is missing",
                    rootId=root_id,
                )
            already_archived = False
    repository.write_projections(root_id)
    return {
        "rootId": root_id,
        "status": "ARCHIVED",
        "runStatus": "COMPLETED",
        "archivedAt": archived_at,
        "alreadyArchived": already_archived,
    }

def graph_events(
    *,
    root: str,
    root_id: str,
    after_event_id: int = 0,
    limit: int = 200,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    events = repository.events(
        root_id,
        after_event_id=after_event_id,
        limit=limit,
    )
    return {
        "rootId": root_id,
        "events": events,
        "nextCursor": (
            events[-1]["eventId"]
            if events
            else after_event_id
        ),
    }

def _rebuild_graph_run_locked(
    *,
    repository: SchedulerRepository,
    root_id: str,
) -> dict[str, Any]:
    stored = repository.hierarchy(root_id)
    current_run = repository.run(root_id)
    events: list[dict[str, Any]] = []
    cursor = 0
    while True:
        page = repository.events(
            root_id,
            after_event_id=cursor,
            limit=200,
        )
        events.extend(page)
        if len(page) < 200:
            break
        cursor = page[-1]["eventId"]

    graph = stored["graph"]
    definitions = {node["id"]: node for node in graph["nodes"]}
    initial = {
        node["id"]: {
            "nodeId": node["id"],
            "attempt": 1,
            "status": "PENDING",
            "owner": None,
            "operationId": None,
            "claimedAt": None,
            "lastHeartbeatAt": None,
            "leaseExpiresAt": None,
            "resumeAt": None,
            "capacityScope": None,
            "finishedAt": None,
            "outcome": None,
            "failureClass": None,
            "manualHandoffEnabled": False,
            "manualTaskHandoff": None,
        }
        for node in graph["nodes"]
    }
    histories: dict[str, list[dict[str, Any]]] = {
        node_id: [state]
        for node_id, state in initial.items()
    }
    latest = dict(initial)
    requirement_states = {
        node["workItemId"]: {
            "taskId": node["workItemId"],
            "revision": 1,
            "status": "FROZEN",
            "updatedAt": current_run["startedAt"],
        }
        for node in graph["nodes"]
        if node["kind"] == "TASK_LOOP"
    }
    completed_at: str | None = None
    cancelled_at: str | None = None
    execution_mode = current_run["executionMode"]
    if execution_mode not in GRAPH_EXECUTION_MODES:
        fail(
            "SCHEDULER_EVENT_REPLAY_INVALID",
            "Graph replay found an unsupported execution mode",
            executionMode=execution_mode,
        )
    host_capacity: dict[str, str] | None = None
    restored_capacity_events: dict[str, dict[str, str]] = {}

    for event in events:
        event_type = event["eventType"]
        node_id = event["nodeId"]
        at = event["recordedAt"]
        payload = event["payload"]
        if event_type == "GRAPH_RUN_STARTED":
            event_execution_mode = payload.get("executionMode")
            if event_execution_mode != execution_mode:
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Graph run event execution mode does not match the run",
                    executionMode=event_execution_mode,
                )
            requirement_revisions = payload.get(
                "taskRequirementRevisions"
            )
            if requirement_revisions is not None:
                if (
                    not isinstance(requirement_revisions, dict)
                    or set(requirement_revisions)
                    != set(requirement_states)
                    or any(
                        not isinstance(value, int)
                        or isinstance(value, bool)
                        or value < 1
                        for value in requirement_revisions.values()
                    )
                ):
                    fail(
                        "SCHEDULER_EVENT_REPLAY_INVALID",
                        "Graph run event has invalid TASK requirement revisions",
                    )
                for task_id, revision in requirement_revisions.items():
                    requirement_states[task_id]["revision"] = revision
                    requirement_states[task_id]["updatedAt"] = at
            continue
        if event_type == "HOST_CAPACITY_EXHAUSTED":
            required_capacity_fields = (
                "capacityKey",
                "resetAt",
                "reason",
            )
            if not all(
                isinstance(payload.get(field), str)
                for field in required_capacity_fields
            ):
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Host capacity event is missing breaker metadata",
                )
            host_adapter_id = event["actor"]
            if host_adapter_id not in HOST_ADAPTER_AGENTS:
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Host capacity event has an unknown host adapter",
                )
            host_capacity = {
                "capacityKey": payload["capacityKey"],
                "resetAt": payload["resetAt"],
                "reportedAt": (
                    payload.get("reportedAt")
                    if isinstance(payload.get("reportedAt"), str)
                    else at
                ),
                "reason": payload["reason"],
                "hostAdapterId": host_adapter_id,
                "agentId": HOST_ADAPTER_AGENTS[host_adapter_id],
                "reportId": (
                    payload.get("reportId")
                    if isinstance(payload.get("reportId"), str)
                    else event["eventUuid"]
                ),
            }
            continue
        if event_type == "HOST_CAPACITY_RESTORED":
            restored_key = payload.get("capacityKey")
            restored_reset_at = payload.get("resetAt")
            restored_report_id = payload.get("reportId")
            if (
                isinstance(restored_key, str)
                and isinstance(restored_reset_at, str)
                and isinstance(restored_report_id, str)
            ):
                restored_capacity_events[restored_key] = {
                    "capacityKey": restored_key,
                    "resetAt": restored_reset_at,
                    "reportId": restored_report_id,
                    "restoredAt": at,
                }
            host_capacity = None
            continue
        if event_type == "GRAPH_RUN_CANCELLED":
            cancelled_at = at
            for state in latest.values():
                if state["status"] not in {
                    "SUCCEEDED",
                    "COMPLETED",
                    "CANCELLED",
                }:
                    state["status"] = "CANCELLED"
                    state["finishedAt"] = at
            continue
        if node_id not in latest:
            fail(
                "SCHEDULER_EVENT_REPLAY_INVALID",
                "Event references an unknown scheduler node",
            )
        if event_type == "LOOP_RETRY_SCHEDULED":
            state = {
                "nodeId": node_id,
                "attempt": event["attempt"],
                "status": "PENDING",
                "owner": None,
                "operationId": None,
                "claimedAt": None,
                "lastHeartbeatAt": None,
                "leaseExpiresAt": None,
                "resumeAt": None,
                "capacityScope": None,
                "finishedAt": None,
                "outcome": None,
                "failureClass": None,
                "manualHandoffEnabled": latest[node_id].get(
                    "manualHandoffEnabled",
                    False,
                ),
                "manualTaskHandoff": latest[node_id].get(
                    "manualTaskHandoff"
                ),
            }
            if state["attempt"] != latest[node_id]["attempt"] + 1:
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Loop retry attempt sequence is invalid",
                )
            histories[node_id].append(state)
            latest[node_id] = state
            continue
        state = latest[node_id]
        if event["attempt"] != state["attempt"]:
            fail(
                "SCHEDULER_EVENT_REPLAY_INVALID",
                "Event does not reference the latest Loop attempt",
            )
        if event_type == "NODE_RESULT_CARRIED_FORWARD":
            if state["status"] != "PENDING":
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Only a pending node can receive a carried result",
                )
            state["status"] = "SUCCEEDED"
            state["finishedAt"] = at
            state["outcome"] = payload.get("outcome")
            state["failureClass"] = payload.get("failureClass")
            task_id = payload.get("taskId")
            requirement = requirement_states.get(task_id)
            if requirement is not None:
                requirement["revision"] = payload.get(
                    "requirementRevision",
                    1,
                )
                requirement["updatedAt"] = at
            continue
        if event_type == "TASK_REQUIREMENT_UNFROZEN":
            task_id = payload.get("taskId")
            requirement = requirement_states.get(task_id)
            if (
                requirement is None
                or requirement["revision"] != payload.get("revision")
                or requirement["status"] != "FROZEN"
            ):
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "TASK requirement unfreeze sequence is invalid",
                )
            requirement["status"] = "UNFROZEN"
            requirement["updatedAt"] = at
            continue
        if event_type == "TASK_REQUIREMENT_REFROZEN":
            task_id = payload.get("taskId")
            requirement = requirement_states.get(task_id)
            if (
                requirement is None
                or requirement["status"] != "UNFROZEN"
                or payload.get("revision")
                != requirement["revision"] + 1
            ):
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "TASK requirement refreeze sequence is invalid",
                )
            requirement["revision"] = payload["revision"]
            requirement["status"] = "FROZEN"
            requirement["updatedAt"] = at
            continue
        if event_type == "LOOP_MANUAL_HANDOFF_ENABLED":
            definition = definitions[node_id]
            if (
                execution_mode != "active"
                or definition["kind"] != "TASK_LOOP"
                or state["status"] != "READY"
                or state.get("manualHandoffEnabled")
                or payload.get("dispatchMode") != "MANUAL"
                or payload.get("confirmedNoCodeChanges") is not True
                or not isinstance(payload.get("reason"), str)
                or not payload["reason"].strip()
            ):
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Automatic TASK manual handoff event is invalid",
                )
            state["manualHandoffEnabled"] = True
            state["manualTaskHandoff"] = {
                "confirmedBy": event["actor"],
                "reason": payload["reason"],
                "handoffRequestId": event["operationId"],
                "enabledAt": at,
            }
            continue
        if event_type == "NODE_READY":
            state["status"] = "READY"
        elif event_type == "JOIN_COMPLETED":
            state["status"] = "SUCCEEDED"
            state["finishedAt"] = at
        elif event_type == "LOOP_CLAIMED":
            definition = next(
                node for node in graph["nodes"] if node["id"] == node_id
            )
            if not _dispatch_mode_allowed(
                execution_mode,
                definition["kind"],
                payload.get("dispatchMode"),
                manual_handoff_enabled=bool(
                    state.get("manualHandoffEnabled")
                ),
            ):
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Loop claim dispatch mode is inconsistent with the Graph run",
                    nodeId=node_id,
                )
            state.update(
                {
                    "status": "CLAIMED",
                    "owner": event["actor"],
                    "operationId": event["operationId"],
                    "claimedAt": at,
                    "lastHeartbeatAt": at,
                    "leaseExpiresAt": payload["leaseExpiresAt"],
                }
            )
        elif event_type == "LOOP_HEARTBEAT":
            state["lastHeartbeatAt"] = at
            state["leaseExpiresAt"] = payload["leaseExpiresAt"]
        elif event_type == "LOOP_PROGRESS_REPORTED":
            if (
                state["status"] != "CLAIMED"
                or event["operationId"] != state["operationId"]
                or event["actor"] != state["owner"]
            ):
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Loop progress event does not belong to the live claim",
                )
            validate_progress_event_payload(payload)
        elif event_type == "NODE_PAUSED":
            state.update(
                {
                    "status": "PAUSED",
                    "leaseExpiresAt": None,
                    "resumeAt": payload.get("resumeAt"),
                    "capacityScope": payload.get("capacityScope"),
                }
            )
        elif event_type in {"NODE_RESUMED", "NODE_AUTO_RESUMED"}:
            state.update(
                {
                    "status": "PENDING",
                    "owner": None,
                    "operationId": None,
                    "claimedAt": None,
                    "lastHeartbeatAt": None,
                    "leaseExpiresAt": None,
                    "resumeAt": None,
                    "capacityScope": None,
                }
            )
        elif event_type in {
            "LOOP_SUCCEEDED",
            "LOOP_BLOCKED",
            "LOOP_REPLAN_REQUIRED",
            "LOOP_CANCELLED",
        }:
            state["status"] = {
                "LOOP_SUCCEEDED": "SUCCEEDED",
                "LOOP_BLOCKED": "BLOCKED",
                "LOOP_REPLAN_REQUIRED": "BLOCKED",
                "LOOP_CANCELLED": "CANCELLED",
            }[event_type]
            state["finishedAt"] = at
            state["outcome"] = payload["outcome"]
            state["failureClass"] = payload.get("failureClass")
        elif event_type == "CLAIM_LEASE_EXPIRED":
            state["status"] = "BLOCKED"
            state["finishedAt"] = at
            state["failureClass"] = "WORKER_LOST"
        elif event_type == "USER_CONFIRMED":
            state["status"] = "COMPLETED"
            state["finishedAt"] = at
            state["outcome"] = {
                "confirmedBy": event["actor"],
                "summary": payload["summary"],
            }
            completed_at = at
        elif event_type in {
            "RETRY_EXHAUSTED",
            "WORKSPACE_TURN_RELEASED",
        }:
            continue
        else:
            fail(
                "SCHEDULER_EVENT_REPLAY_INVALID",
                f"Unsupported scheduler event: {event_type}",
            )

    states = [
        state
        for node_id in sorted(histories)
        for state in histories[node_id]
    ]
    latest_states = [
        histories[node_id][-1]
        for node_id in sorted(histories)
    ]
    if cancelled_at is not None:
        run_status = "CANCELLED"
    elif completed_at is not None:
        run_status = "COMPLETED"
    elif any(
        state["status"] in {"BLOCKED", "CANCELLED"}
        for state in latest_states
    ):
        run_status = "BLOCKED"
    elif any(
        state["status"] == "PAUSED"
        for state in latest_states
    ) and not any(
        state["status"] in {"READY", "CLAIMED"}
        for state in latest_states
    ):
        run_status = "PAUSED"
    else:
        run_status = "ACTIVE"
    updated_at = (
        events[-1]["recordedAt"]
        if events
        else current_run["startedAt"]
    )
    with repository.transaction() as connection:
        for restored_capacity in restored_capacity_events.values():
            connection.execute(
                "UPDATE host_capacity_breakers SET status = 'RESTORED', "
                "restored_at = ? WHERE capacity_key = ? "
                "AND report_id = ? AND reset_at = ? AND status = 'OPEN'",
                (
                    restored_capacity["restoredAt"],
                    restored_capacity["capacityKey"],
                    restored_capacity["reportId"],
                    restored_capacity["resetAt"],
                ),
            )
        if host_capacity is not None:
            connection.execute(
                "INSERT INTO host_capacity_breakers("
                "capacity_key, host_adapter_id, agent_id, reset_at, "
                "report_id, status, reported_at, reason) "
                "VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?) "
                "ON CONFLICT(capacity_key) DO UPDATE SET "
                "host_adapter_id = excluded.host_adapter_id, "
                "agent_id = excluded.agent_id, reset_at = excluded.reset_at, "
                "report_id = excluded.report_id, status = 'OPEN', "
                "reported_at = excluded.reported_at, restored_at = NULL, "
                "reason = excluded.reason "
                "WHERE (host_capacity_breakers.report_id = "
                "excluded.report_id AND host_capacity_breakers.status = "
                "'OPEN') OR host_capacity_breakers.reported_at < "
                "excluded.reported_at",
                (
                    host_capacity["capacityKey"],
                    host_capacity["hostAdapterId"],
                    host_capacity["agentId"],
                    host_capacity["resetAt"],
                    host_capacity["reportId"],
                    host_capacity["reportedAt"],
                    host_capacity["reason"],
                ),
            )
        connection.execute(
            "DELETE FROM node_runs WHERE run_id = ?",
            (current_run["runId"],),
        )
        for state in states:
            connection.execute(
                "INSERT INTO node_runs(run_id, node_id, attempt, status, "
                "owner, operation_id, claimed_at, last_heartbeat_at, "
                "lease_expires_at, finished_at, outcome_json, failure_class) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    current_run["runId"],
                    state["nodeId"],
                    state["attempt"],
                    state["status"],
                    state["owner"],
                    state["operationId"],
                    state["claimedAt"],
                    state["lastHeartbeatAt"],
                    state["leaseExpiresAt"],
                    (
                        state["resumeAt"]
                        if state["status"] == "PAUSED"
                        else state["finishedAt"]
                    ),
                    (
                        json.dumps(
                            {
                                "schedulerPause": {
                                    "capacityScope": state[
                                        "capacityScope"
                                    ],
                                }
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if state["status"] == "PAUSED"
                        and state["capacityScope"] is not None
                        else (
                            json.dumps(
                                state["outcome"],
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                            if state["outcome"] is not None
                            else None
                        )
                    ),
                    state["failureClass"],
                ),
            )
        connection.execute(
            "DELETE FROM task_requirement_states WHERE run_id = ?",
            (current_run["runId"],),
        )
        for requirement in requirement_states.values():
            connection.execute(
                "INSERT INTO task_requirement_states("
                "run_id, task_id, revision, status, updated_at"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    current_run["runId"],
                    requirement["taskId"],
                    requirement["revision"],
                    requirement["status"],
                    requirement["updatedAt"],
                ),
            )
        connection.execute(
            "UPDATE runs SET status = ?, execution_mode = ?, "
            "updated_at = ?, completed_at = ?, cancelled_at = ?, "
            "host_capacity_key = ?, host_capacity_reset_at = ?, "
            "host_capacity_reported_at = ?, host_capacity_reason = ? "
            "WHERE run_id = ?",
            (
                run_status,
                execution_mode,
                updated_at,
                completed_at,
                cancelled_at,
                (
                    host_capacity["capacityKey"]
                    if host_capacity is not None
                    else None
                ),
                (
                    host_capacity["resetAt"]
                    if host_capacity is not None
                    else None
                ),
                (
                    host_capacity["reportedAt"]
                    if host_capacity is not None
                    else None
                ),
                (
                    host_capacity["reason"]
                    if host_capacity is not None
                    else None
                ),
                current_run["runId"],
            ),
        )
    repository.write_projections(root_id)
    return {
        **repository.run(root_id),
        "rebuiltFromEvents": len(events),
    }

def rebuild_graph_run(
    *,
    root: str,
    root_id: str,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    """Rebuild materialized state from one locked event snapshot."""

    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.scheduler_lock():
        return _rebuild_graph_run_locked(
            repository=repository,
            root_id=root_id,
        )
