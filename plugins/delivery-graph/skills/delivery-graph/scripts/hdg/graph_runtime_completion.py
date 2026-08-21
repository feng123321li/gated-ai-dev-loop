from __future__ import annotations

from .graph_runtime_common import (
    Any,
    FAILURE_CLASSES,
    LOOP_NODE_KINDS,
    SchedulerRepository,
    _active_claim,
    _compact_loop_outcome_for_transport,
    _compact_run_for_transport,
    _identity,
    _loaded,
    _locked_timestamp,
    _node,
    _minimize_loop_outcome_for_graph,
    _retry_if_allowed,
    _validate_reused_evidence_refs,
    capture_verified_evidence_scope_state,
    capture_verified_workspace_changes,
    capture_verified_workspace_state,
    fail,
    json,
    validate_loop_outcome,
    validate_review_result_contract,
)


def record_loop_result(
    *,
    root: str,
    root_id: str,
    node_id: str,
    operation_id: str,
    outcome: object,
    failure_class: str | None = None,
    verified_project_scopes: list[dict[str, Any]] | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    normalized = validate_loop_outcome(outcome)
    normalized = _minimize_loop_outcome_for_graph(normalized)
    if normalized["status"] == "BLOCKED":
        if failure_class is None:
            fail(
                "SCHEDULER_FAILURE_CLASS_REQUIRED",
                "BLOCKED is reserved for a concrete condition that leaves "
                "no in-scope path to progress; provide failure_class only "
                "after internal correction and reevaluation are exhausted",
            )
        if failure_class not in FAILURE_CLASSES:
            fail(
                "SCHEDULER_FAILURE_CLASS_INVALID",
                "failure_class is not supported",
            )
    elif failure_class is not None:
        fail(
            "SCHEDULER_FAILURE_CLASS_INVALID",
            "failure_class is only valid for BLOCKED outcomes",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if verified_project_scopes is not None:
        # Reject an invalid bearer before reading any workspace content. The
        # mutation transaction below repeats this check after the read-only
        # Git capture, so lease or operation races still fail closed.
        with repository.read() as connection:
            graph, run, nodes = _loaded(connection, root_id)
            at = _locked_timestamp(now, run["updated_at"])
            definition, state = _node(graph, nodes, node_id)
            if (
                definition["kind"] not in LOOP_NODE_KINDS
                or not _active_claim(
                    state,
                    operation_id=operation_id,
                    at=at,
                )
            ):
                fail(
                    "SCHEDULER_OPERATION_INVALID",
                    "Loop does not have the supplied active operation",
                )
        result_payload = dict(normalized["result"])
        # This key is controller-owned on the MCP path. Overwrite any
        # self-reported value so acceptance evidence always comes from the
        # Adapter workspace and the Controller-verified Git scopes.
        workspace_changes = capture_verified_workspace_changes(
            verified_project_scopes,
        )
        result_payload["workspaceChanges"] = workspace_changes
        if isinstance(result_payload.get("verificationEvidence"), list):
            result_payload["evidenceWorkspaceSnapshots"] = [
                {
                    "projectId": item["projectId"],
                    "bindingState": "BOUND",
                    "headCommit": item["headCommit"],
                    "workingTreeStateFingerprint": item[
                        "workingTreeStateFingerprint"
                    ],
                }
                for item in workspace_changes
            ]
            evidence_scope_snapshots = (
                capture_verified_evidence_scope_state(
                    verified_project_scopes,
                    result_payload.get("affectedScopes"),
                )
            )
            confirmed_workspace_state = capture_verified_workspace_state(
                verified_project_scopes
            )
            recorded_binding = sorted(
                (
                    item["projectId"],
                    item["headCommit"],
                    item["workingTreeStateFingerprint"],
                )
                for item in workspace_changes
            )
            confirmed_binding = sorted(
                (
                    item["projectId"],
                    item["headCommit"],
                    item["workingTreeStateFingerprint"],
                )
                for item in confirmed_workspace_state
                if item.get("bindingState") == "BOUND"
            )
            if recorded_binding != confirmed_binding:
                fail(
                    "SCHEDULER_GIT_DIFF_CHANGED",
                    "The Delivery workspace changed while verification "
                    "scope evidence was captured",
                )
            result_payload["evidenceScopeSnapshots"] = (
                evidence_scope_snapshots
            )
        else:
            result_payload.pop("evidenceWorkspaceSnapshots", None)
            result_payload.pop("evidenceScopeSnapshots", None)
        _validate_reused_evidence_refs(
            graph=graph,
            nodes=nodes,
            node_id=node_id,
            result_payload=result_payload,
            verified_project_scopes=verified_project_scopes,
        )
        normalized["result"] = result_payload
    event_by_status = {
        "SUCCEEDED": "LOOP_SUCCEEDED",
        "BLOCKED": "LOOP_BLOCKED",
        "REPLAN_REQUIRED": "LOOP_REPLAN_REQUIRED",
        "CANCELLED": "LOOP_CANCELLED",
    }
    state_by_status = {
        "SUCCEEDED": "SUCCEEDED",
        "BLOCKED": "BLOCKED",
        "REPLAN_REQUIRED": "BLOCKED",
        "CANCELLED": "CANCELLED",
    }
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        definition, state = _node(graph, nodes, node_id)
        if (
            definition["kind"] not in LOOP_NODE_KINDS
            or not _active_claim(
                state,
                operation_id=operation_id,
                at=at,
            )
        ):
            fail(
                "SCHEDULER_OPERATION_INVALID",
                "Loop does not have the supplied active operation",
            )
        if (
            normalized["status"] == "SUCCEEDED"
            and definition["kind"].endswith("_REVIEW_LOOP")
        ):
            # The Controller checks only the receiver-declared result contract;
            # the independent receiver owns the technical acceptance judgment.
            normalized["result"] = validate_review_result_contract(
                definition["kind"],
                normalized["result"],
            )
        scheduler_status = state_by_status[normalized["status"]]
        effective_failure = (
            "REPLAN_REQUIRED"
            if normalized["status"] == "REPLAN_REQUIRED"
            else failure_class
        )
        connection.execute(
            "UPDATE node_runs SET status = ?, finished_at = ?, "
            "outcome_json = ?, failure_class = ? WHERE run_id = ? "
            "AND node_id = ? AND attempt = ?",
            (
                scheduler_status,
                at,
                json.dumps(
                    normalized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                effective_failure,
                run["run_id"],
                node_id,
                state["attempt"],
            ),
        )
        repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=node_id,
            attempt=state["attempt"],
            event_type=event_by_status[normalized["status"]],
            actor=state["owner"],
            operation_id=operation_id,
            payload={
                "outcome": normalized,
                "failureClass": effective_failure,
            },
            at=at,
        )
        retried = False
        if normalized["status"] == "BLOCKED":
            retried = _retry_if_allowed(
                repository,
                connection,
                graph=graph,
                run_id=run["run_id"],
                node=state,
                failure_class=str(effective_failure),
                at=at,
            )
        repository.refresh_ready(
            connection,
            graph,
            run["run_id"],
            at=at,
        )
    repository.write_projections(root_id)
    latest = next(
        node
        for node in repository.run(root_id)["nodes"]
        if node["nodeId"] == node_id
    )
    return {
        "rootId": root_id,
        "nodeId": node_id,
        "outcome": _compact_loop_outcome_for_transport(normalized),
        "schedulerStatus": latest["status"],
        "retried": retried,
        "nextAttempt": latest["attempt"] if retried else None,
    }

def record_user_confirmation(
    *,
    root: str,
    root_id: str,
    confirmed: bool,
    confirmed_by: str,
    summary: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    if confirmed is not True:
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "Revision completion requires explicit user confirmation",
        )
    confirmed_by = _identity(confirmed_by, "confirmed_by")
    if not isinstance(summary, str) or not summary.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "summary must be non-empty",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        definition = next(
            node
            for node in graph["nodes"]
            if node["kind"] == "USER_CONFIRMATION"
        )
        _, state = _node(graph, nodes, definition["id"])
        if state["status"] != "READY":
            fail(
                "SCHEDULER_CONFIRMATION_NOT_READY",
                "Current Revision completion confirmation is not ready",
            )
        connection.execute(
            "UPDATE node_runs SET status = 'COMPLETED', "
            "finished_at = ?, outcome_json = ? WHERE run_id = ? "
            "AND node_id = ? AND attempt = ?",
            (
                at,
                json.dumps(
                    {
                        "confirmedBy": confirmed_by,
                        "summary": summary.strip(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                run["run_id"],
                definition["id"],
                state["attempt"],
            ),
        )
        repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=definition["id"],
            attempt=state["attempt"],
            event_type="USER_CONFIRMED",
            actor=confirmed_by,
            operation_id=None,
            payload={"summary": summary.strip()},
            at=at,
        )
        repository.refresh_ready(
            connection,
            graph,
            run["run_id"],
            at=at,
        )
    repository.write_projections(root_id)
    result = _compact_run_for_transport(repository.run(root_id))
    closure = repository.delivery_closure(root_id)
    return {
        **result,
        "deliveryClosure": closure["state"],
        "deliveryStateLabel": closure["label"],
        "archiveState": "ACTIVE",
        "canPrepareRevision": True,
        "canCloseDelivery": True,
        "nextAction": "PREPARE_REVISION_OR_CLOSE_DELIVERY",
    }


def close_delivery(
    *,
    root: str,
    root_id: str,
    confirmed: bool,
    closed_by: str,
    summary: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Close a completed Delivery after production delivery."""

    if confirmed is not True:
        fail(
            "SCHEDULER_DELIVERY_CLOSE_CONFIRMATION_REQUIRED",
            "Delivery closure requires explicit confirmation",
        )
    closed_by = _identity(closed_by, "closed_by")
    if not isinstance(summary, str) or not summary.strip():
        fail(
            "SCHEDULER_DELIVERY_CLOSE_CONFIRMATION_REQUIRED",
            "summary must describe the accepted production delivery",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    already_closed = False
    archive_state = "ACTIVE"
    with repository.transaction() as connection:
        hierarchy = connection.execute(
            "SELECT revision, status, updated_at FROM hierarchies "
            "WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        if hierarchy is None:
            fail(
                "SCHEDULER_HIERARCHY_MISSING",
                f"Scheduler hierarchy is missing: {root_id}",
            )
        run = connection.execute(
            "SELECT * FROM runs WHERE root_id = ? AND revision = ?",
            (root_id, hierarchy["revision"]),
        ).fetchone()
        if run is None or run["status"] != "COMPLETED":
            fail(
                "SCHEDULER_DELIVERY_NOT_COMPLETED",
                "Only a completed Delivery revision can be closed",
                runStatus=run["status"] if run is not None else None,
            )
        closure = repository.delivery_closure_from_connection(
            connection,
            root_id,
        )
        archive_state = (
            "ARCHIVED" if hierarchy["status"] == "ARCHIVED" else "ACTIVE"
        )
        if closure["state"] == "CLOSED":
            already_closed = True
        else:
            pending_revision = connection.execute(
                "SELECT revision FROM delivery_revisions "
                "WHERE root_id = ? AND revision > ? "
                "AND status = 'PREPARED' ORDER BY revision LIMIT 1",
                (root_id, hierarchy["revision"]),
            ).fetchone()
            if pending_revision is not None:
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "A prepared Delivery revision must be resolved before "
                    "the Delivery can be closed",
                    preparedRevision=pending_revision["revision"],
                )
            at = _locked_timestamp(
                now,
                max(hierarchy["updated_at"], run["updated_at"]),
            )
            repository.append_event(
                connection,
                run_id=run["run_id"],
                node_id=None,
                attempt=None,
                event_type="DELIVERY_CLOSED",
                actor=closed_by,
                operation_id=None,
                payload={
                    "summary": summary.strip(),
                    "revision": hierarchy["revision"],
                },
                at=at,
            )
            closure = repository.delivery_closure_from_connection(
                connection,
                root_id,
            )
    repository.write_projections(root_id)
    archived = archive_state == "ARCHIVED"
    return {
        "rootId": root_id,
        "status": "ARCHIVED" if archived else "COMPLETED",
        "runStatus": "COMPLETED",
        "deliveryRevision": hierarchy["revision"],
        "deliveryClosure": closure["state"],
        "deliveryStateLabel": closure["label"],
        "archiveState": archive_state,
        "closedAt": closure["closedAt"],
        "closedBy": closure["closedBy"],
        "summary": closure["summary"],
        "alreadyClosed": already_closed,
        "canPrepareRevision": False,
        "canCloseDelivery": False,
        "nextAction": "NONE" if archived else "ARCHIVE_DELIVERY_OPTIONAL",
    }

def cancel_graph_run(
    *,
    root: str,
    root_id: str,
    cancelled_by: str,
    reason: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    cancelled_by = _identity(cancelled_by, "cancelled_by")
    if not isinstance(reason, str) or not reason.strip():
        fail("SCHEDULER_CANCEL_INVALID", "reason must be non-empty")
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as connection:
        hierarchy_row = connection.execute(
            "SELECT status, updated_at FROM hierarchies WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        if hierarchy_row is None:
            fail(
                "SCHEDULER_RUN_MISSING",
                f"No Delivery to cancel: {root_id}",
            )
        if hierarchy_row["status"] == "ARCHIVED":
            fail(
                "SCHEDULER_RUN_TERMINAL",
                "An archived Delivery cannot be cancelled",
            )
        if hierarchy_row["status"] != "FROZEN":
            at = _locked_timestamp(now, hierarchy_row["updated_at"])
            connection.execute(
                "UPDATE hierarchies SET status = 'ABANDONED', "
                "updated_at = ? WHERE root_id = ?",
                (at, root_id),
            )
            connection.execute(
                "UPDATE delivery_revisions SET status = 'ABANDONED', "
                "updated_at = ? WHERE root_id = ?",
                (at, root_id),
            )
            abandoned = True
        else:
            graph, run, nodes = _loaded(connection, root_id)
            at = _locked_timestamp(now, run["updated_at"])
            if run["status"] in {
                "COMPLETED",
                "CANCELLED",
                "SUPERSEDED",
            }:
                fail(
                    "SCHEDULER_RUN_TERMINAL",
                    "A terminal scheduler run cannot be cancelled",
                )
            for node in nodes:
                if node["status"] in {
                    "SUCCEEDED",
                    "COMPLETED",
                    "CANCELLED",
                }:
                    continue
                connection.execute(
                    "UPDATE node_runs SET status = 'CANCELLED', "
                    "finished_at = ? WHERE run_id = ? AND node_id = ? "
                    "AND attempt = ?",
                    (
                        at,
                        run["run_id"],
                        node["nodeId"],
                        node["attempt"],
                    ),
                )
            repository.append_event(
                connection,
                run_id=run["run_id"],
                node_id=None,
                attempt=None,
                event_type="GRAPH_RUN_CANCELLED",
                actor=cancelled_by,
                operation_id=None,
                payload={"reason": reason.strip()},
                at=at,
            )
            connection.execute(
                "UPDATE runs SET status = 'CANCELLED', updated_at = ?, "
                "cancelled_at = ? WHERE run_id = ?",
                (at, at, run["run_id"]),
            )
            abandoned = False
    repository.write_projections(root_id)
    if abandoned:
        return {
            "rootId": root_id,
            "runId": None,
            "runStatus": "ABSENT",
            "deliveryStatus": "ABANDONED",
            "cancelledBy": cancelled_by,
            "reason": reason.strip(),
        }
    return _compact_run_for_transport(repository.run(root_id))
