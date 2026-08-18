from __future__ import annotations

from .graph_runtime_common import (
    Any,
    LOOP_NODE_KINDS,
    PROGRESS_PHASE_TEXT,
    SchedulerRepository,
    _active_claim,
    _after,
    _assert_graph_not_replanning,
    _heartbeat_directive,
    _loaded,
    _locked_timestamp,
    _node,
    _parse_timestamp,
    fail,
    graph_assurance_profile,
    loop_execution_policy,
    normalize_progress_payload,
)
from .graph_runtime_frontier import graph_status


def heartbeat_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    operation_id: str,
    expected_command_seconds: int | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    if expected_command_seconds is not None and (
        not isinstance(expected_command_seconds, int)
        or isinstance(expected_command_seconds, bool)
        or expected_command_seconds < 61
        or expected_command_seconds > 1800
    ):
        fail(
            "SCHEDULER_EXPECTED_COMMAND_SECONDS_INVALID",
            "expected_command_seconds must be an integer from 61 to 1800",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        _, state = _node(graph, nodes, node_id)
        if not _active_claim(
            state,
            operation_id=operation_id,
            at=at,
        ):
            fail(
                "SCHEDULER_OPERATION_INVALID",
                "Loop claim is missing, mismatched, or expired",
            )
        claim_policy = graph["runtime"]["claimPolicy"]
        current_expires = state["leaseExpiresAt"]
        renewal_boundary = _after(
            at,
            claim_policy["renewBeforeSeconds"],
        )
        if expected_command_seconds is not None:
            max_expected = claim_policy.get(
                "maxExpectedCommandSeconds",
                1800,
            )
            if expected_command_seconds > max_expected:
                fail(
                    "SCHEDULER_EXPECTED_COMMAND_SECONDS_INVALID",
                    "expected_command_seconds exceeds this Graph policy",
                    maxExpectedCommandSeconds=max_expected,
                )
            requested_expires = _after(
                at,
                expected_command_seconds
                + claim_policy.get("longCommandLeaseBufferSeconds", 120),
            )
            lease_renewed = _parse_timestamp(
                requested_expires
            ) > _parse_timestamp(current_expires)
            expires = requested_expires if lease_renewed else current_expires
            renewal_reason = "LONG_COMMAND"
        else:
            lease_renewed = (
                _parse_timestamp(current_expires)
                <= _parse_timestamp(renewal_boundary)
            )
            expires = (
                _after(at, claim_policy["leaseSeconds"])
                if lease_renewed
                else current_expires
            )
            renewal_reason = (
                "RENEWAL_THRESHOLD" if lease_renewed else "NOT_REQUIRED"
            )
        if lease_renewed:
            connection.execute(
                "UPDATE node_runs SET last_heartbeat_at = ?, "
                "lease_expires_at = ? WHERE run_id = ? AND node_id = ? "
                "AND attempt = ?",
                (
                    at,
                    expires,
                    run["run_id"],
                    node_id,
                    state["attempt"],
                ),
            )
        else:
            connection.execute(
                "UPDATE node_runs SET last_heartbeat_at = ? "
                "WHERE run_id = ? AND node_id = ? AND attempt = ?",
                (
                    at,
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
            event_type="LOOP_HEARTBEAT",
            actor=state["owner"],
            operation_id=operation_id,
            payload={
                "leaseExpiresAt": expires,
                "leaseRenewed": lease_renewed,
                "leaseRenewalReason": renewal_reason,
                **(
                    {
                        "expectedCommandSeconds": (
                            expected_command_seconds
                        )
                    }
                    if expected_command_seconds is not None
                    else {}
                ),
            },
            at=at,
        )
        connection.execute(
            "UPDATE runs SET updated_at = ? WHERE run_id = ?",
            (at, run["run_id"]),
        )
    status = graph_status(
        root=root,
        root_id=root_id,
        explicit_dogfood=explicit_dogfood,
        now=now,
    )
    return {
        "rootId": root_id,
        "nodeId": node_id,
        "status": "CLAIMED",
        "lastHeartbeatAt": at,
        "leaseExpiresAt": expires,
        "leaseRenewed": lease_renewed,
        "leaseRenewalReason": renewal_reason,
        "heartbeatDirective": _heartbeat_directive(
            claim_policy,
            observed_at=at,
            claimed_at=state["claimedAt"],
            last_heartbeat_at=at,
        ),
        **(
            {"expectedCommandSeconds": expected_command_seconds}
            if expected_command_seconds is not None
            else {}
        ),
        "progressMonitor": status["progressMonitor"],
    }
def report_loop_progress(
    *,
    root: str,
    root_id: str,
    node_id: str,
    operation_id: str,
    phase: str,
    summary_zh: str,
    completed_zh: list[str] | None = None,
    next_step_zh: str | None = None,
    progress_percent: int | None = None,
    tests: dict[str, int] | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Record bounded user-visible progress without renewing a lease."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    payload = normalize_progress_payload(
        phase=phase,
        summary_zh=summary_zh,
        completed_zh=completed_zh,
        next_step_zh=next_step_zh,
        progress_percent=progress_percent,
        tests=tests,
    )
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        definition, state = _node(graph, nodes, node_id)
        if definition["kind"] not in LOOP_NODE_KINDS or not _active_claim(
            state,
            operation_id=operation_id,
            at=at,
        ):
            fail(
                "SCHEDULER_OPERATION_INVALID",
                "Loop claim is missing, mismatched, or expired",
            )
        event = repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=node_id,
            attempt=state["attempt"],
            event_type="LOOP_PROGRESS_REPORTED",
            actor=state["owner"],
            operation_id=operation_id,
            payload=payload,
            at=at,
        )
        connection.execute(
            "UPDATE runs SET updated_at = ? WHERE run_id = ?",
            (at, run["run_id"]),
        )
        lease_expires_at = state["leaseExpiresAt"]
        heartbeat_directive = _heartbeat_directive(
            graph["runtime"]["claimPolicy"],
            observed_at=at,
            claimed_at=state["claimedAt"],
            last_heartbeat_at=state["lastHeartbeatAt"],
        )
    repository.write_projections(root_id)
    return {
        "rootId": root_id,
        "nodeId": node_id,
        "attempt": event["attempt"],
        "eventUuid": event["eventUuid"],
        "reportedAt": event["recordedAt"],
        "phase": payload["phase"],
        "phaseZh": PROGRESS_PHASE_TEXT[payload["phase"]],
        "summaryZh": payload["summaryZh"],
        "completedZh": payload["completedZh"],
        **(
            {"nextStepZh": payload["nextStepZh"]}
            if "nextStepZh" in payload
            else {}
        ),
        **(
            {"progressPercent": payload["progressPercent"]}
            if "progressPercent" in payload
            else {}
        ),
        **({"tests": payload["tests"]} if "tests" in payload else {}),
        "leaseExpiresAt": lease_expires_at,
        "leaseRenewed": False,
        "heartbeatDirective": heartbeat_directive,
    }

def pause_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    operation_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    return _change_claimed_loop(
        root=root,
        root_id=root_id,
        node_id=node_id,
        operation_id=operation_id,
        target_status="PAUSED",
        event_type="NODE_PAUSED",
        explicit_dogfood=explicit_dogfood,
        now=now,
    )

def _change_claimed_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    operation_id: str,
    target_status: str,
    event_type: str,
    explicit_dogfood: bool,
    now: object,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        _, state = _node(graph, nodes, node_id)
        if not _active_claim(
            state,
            operation_id=operation_id,
            at=at,
        ):
            fail(
                "SCHEDULER_OPERATION_INVALID",
                "Loop does not have the supplied active operation",
            )
        connection.execute(
            "UPDATE node_runs SET status = ?, finished_at = ?, "
            "lease_expires_at = NULL, outcome_json = ? "
            "WHERE run_id = ? AND node_id = ? AND attempt = ?",
            (
                target_status,
                at,
                None,
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
            event_type=event_type,
            actor=state["owner"],
            operation_id=operation_id,
            payload={},
            at=at,
        )
        repository.refresh_ready(
            connection,
            graph,
            run["run_id"],
            at=at,
        )
    repository.write_projections(root_id)
    result = {
        "rootId": root_id,
        "nodeId": node_id,
        "status": target_status,
    }
    if target_status == "PAUSED":
        result.update(
            {
                "executionPolicy": loop_execution_policy(
                    graph_assurance_profile(graph)
                ),
                "handoff": {
                    "rootId": root_id,
                    "nodeId": node_id,
                    "resumeSequence": [
                        "graph_frontier",
                        "resume_loop",
                        "graph_frontier",
                        "loop_context",
                        "dispatch_loop",
                    ],
                    "reuseFrozenGraph": True,
                    "reprepare": False,
                    "refreeze": False,
                },
            }
        )
    return result

def resume_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    workspace_turn: dict[str, Any] | None = None
    workspace_reacquisition: dict[str, Any] | None = None
    if workspace_root is not None:
        from .git_binding import (
            verify_delivery_git_binding,
            verify_runtime_delivery_project_scopes,
        )
        from .planning_gates import (
            _capture_workspace_turn_start,
            _serial_workspace_release_handshake,
        )
        from .planning_workspace import (
            _automatic_workspace_requests,
            _paused_serial_workspace_preparation,
        )

        release = repository.workspace_turn_release(root_id)
        if release is None and repository.run(root_id)["status"] == "PAUSED":
            handshake = _serial_workspace_release_handshake(
                repository,
                workspace_root,
                root_id,
            )
            if handshake["workspaceRelease"]["state"] != "RELEASED":
                return {
                    "rootId": root_id,
                    "nodeId": node_id,
                    "status": "PAUSED",
                    "deliveryStatus": "PAUSED",
                    **handshake,
                    "workspaceResume": {
                        "state": "WAITING_FOR_WORKSPACE_RELEASE",
                        "nextAction": handshake["nextAction"],
                    },
                }
            release = repository.workspace_turn_release(root_id)
        if release is not None:
            repository.requeue_paused_workspace_turn(
                root_id,
                node_id=node_id,
            )
        active_requeue = repository.paused_workspace_turn_requeue(root_id)
        if active_requeue is not None:
            workspace_turn = repository.serial_workspace_turn_state(root_id)
            if workspace_turn["state"] != "ACQUIRED":
                return {
                    "rootId": root_id,
                    "nodeId": node_id,
                    "status": "QUEUED",
                    "deliveryStatus": "PAUSED",
                    "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
                    "workspaceTurn": workspace_turn,
                    "workspaceRequeue": active_requeue,
                    "workspaceResume": {
                        "state": "WAITING_FOR_WORKSPACE_TURN",
                        "nextAction": (
                            "WAIT_FOR_PAUSED_LOOP_WORKSPACE_TURN"
                        ),
                    },
                    "nextAction": "WAIT_FOR_PAUSED_LOOP_WORKSPACE_TURN",
                }
            stored = repository.hierarchy(root_id)
            preparation = _paused_serial_workspace_preparation(
                control_root=root,
                workspace_root=workspace_root,
                hierarchy=stored["hierarchy"],
            )
            if preparation is not None:
                return {
                    "rootId": root_id,
                    "nodeId": node_id,
                    "status": "PAUSED",
                    "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
                    "workspaceTurn": workspace_turn,
                    "workspaceRequeue": active_requeue,
                    "workspacePreparation": preparation,
                    "workspaceResume": {
                        "state": "WORKSPACE_PREPARATION_REQUIRED",
                        "nextAction": preparation["nextAction"],
                    },
                    "nextAction": preparation["nextAction"],
                }
            turn_start = _capture_workspace_turn_start(
                _automatic_workspace_requests(
                    control_root=root,
                    workspace_root=workspace_root,
                    hierarchy=stored["hierarchy"],
                ),
                workspace_root,
            )
            workspace_reacquisition = (
                repository.reacquire_paused_workspace_turn(
                    root_id,
                    node_id=node_id,
                    workspace_turn_start=turn_start,
                )
            )
        stored = repository.hierarchy(root_id)
        repository.assert_delivery_workspace(root_id, workspace_root)
        verified_projects = verify_runtime_delivery_project_scopes(
            workspace_root,
            stored["hierarchy"]["delivery"],
            preparing=False,
        )
        if stored["hierarchy"]["delivery"].get("projectScopes") is None:
            verify_delivery_git_binding(
                workspace_root,
                stored["hierarchy"]["delivery"].get("gitBinding"),
                preparing=False,
            )
        elif not verified_projects:
            fail(
                "SCHEDULER_PROJECT_SCOPE_INVALID",
                "The paused Delivery has no verified project scope",
                rootId=root_id,
            )
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        _assert_graph_not_replanning(nodes)
        definition, state = _node(graph, nodes, node_id)
        if (
            definition["kind"] not in LOOP_NODE_KINDS
            or state["status"] != "PAUSED"
        ):
            fail(
                "SCHEDULER_LOOP_NOT_PAUSED",
                f"{node_id} is not paused",
            )
        connection.execute(
            "UPDATE node_runs SET status = 'PENDING', owner = NULL, "
            "operation_id = NULL, claimed_at = NULL, "
            "last_heartbeat_at = NULL, lease_expires_at = NULL, "
            "finished_at = NULL, outcome_json = NULL "
            "WHERE run_id = ? AND node_id = ? AND attempt = ?",
            (run["run_id"], node_id, state["attempt"]),
        )
        repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=node_id,
            attempt=state["attempt"],
            event_type="NODE_RESUMED",
            actor="CONTROLLER",
            operation_id=None,
            payload={},
            at=at,
        )
        repository.refresh_ready(
            connection,
            graph,
            run["run_id"],
            at=at,
        )
    repository.write_projections(root_id)
    result = {
        "rootId": root_id,
        "nodeId": node_id,
        "status": "READY",
        "executionPolicy": loop_execution_policy(
            graph_assurance_profile(graph)
        ),
        "nextAction": (
            "READ_GRAPH_FRONTIER_AND_REDISPATCH_"
            "IN_INDEPENDENT_CONTEXT"
        ),
    }
    if workspace_root is not None:
        result.update(
            {
                "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
                "workspaceTurn": (
                    repository.serial_workspace_turn_state(root_id)
                ),
                "workspaceResume": {
                    "state": "REACQUIRED",
                    "reacquisition": workspace_reacquisition,
                    "nextAction": result["nextAction"],
                },
            }
        )
    return result
