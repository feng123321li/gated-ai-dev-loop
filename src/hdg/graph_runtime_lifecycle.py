from __future__ import annotations

from .graph_runtime_common import (
    Any,
    HOST_ADAPTER_AGENTS,
    HOST_CAPACITY_KEYS,
    LOOP_NODE_KINDS,
    MAX_HOST_CAPACITY_RESET,
    PROGRESS_PHASE_TEXT,
    SchedulerRepository,
    _active_claim,
    _after,
    _assert_graph_not_replanning,
    _capacity_scope,
    _future_timestamp,
    _identity,
    _loaded,
    _locked_timestamp,
    _node,
    _parse_timestamp,
    _validated_stored_definition,
    fail,
    graph_assurance_profile,
    json,
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
    explicit_dogfood: bool = False,
    now: object = None,
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
                "Loop claim is missing, mismatched, or expired",
            )
        claim_policy = graph["runtime"]["claimPolicy"]
        current_expires = state["leaseExpiresAt"]
        renewal_boundary = _after(
            at,
            claim_policy["renewBeforeSeconds"],
        )
        lease_renewed = (
            _parse_timestamp(current_expires)
            <= _parse_timestamp(renewal_boundary)
        )
        expires = (
            _after(at, claim_policy["leaseSeconds"])
            if lease_renewed
            else current_expires
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
    }

def pause_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    operation_id: str,
    resume_at: str | None = None,
    capacity_scope: str | None = None,
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
        resume_at=resume_at,
        capacity_scope=capacity_scope,
        explicit_dogfood=explicit_dogfood,
        now=now,
    )

def report_host_capacity_exhausted(
    *,
    root: str,
    root_id: str,
    node_id: str,
    reset_at: str,
    host_adapter_id: str,
    receiver_context_id: str,
    report_id: str,
    reason: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Trip a host-side hard-quota breaker without a live model call."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if host_adapter_id not in HOST_ADAPTER_AGENTS:
        fail(
            "SCHEDULER_HOST_ADAPTER_UNTRUSTED",
            "Hard quota reports require an exact trusted host adapter",
        )
    receiver_context_id = _identity(
        receiver_context_id,
        "receiver_context_id",
    )
    report_id = _identity(report_id, "report_id")
    capacity_key = HOST_CAPACITY_KEYS[host_adapter_id]
    affected_agent_id = HOST_ADAPTER_AGENTS[host_adapter_id]
    if not isinstance(reason, str) or not reason.strip():
        fail(
            "SCHEDULER_HOST_CAPACITY_REPORT_INVALID",
            "reason must describe the observed host capacity failure",
        )
    normalized_reason = reason.strip()
    if len(normalized_reason) > 1024:
        fail(
            "SCHEDULER_HOST_CAPACITY_REPORT_INVALID",
            "reason must not exceed 1024 characters",
        )
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        normalized_reset_at = _future_timestamp(reset_at, at=at)
        if (
            _parse_timestamp(normalized_reset_at) - _parse_timestamp(at)
            > MAX_HOST_CAPACITY_RESET
        ):
            fail(
                "SCHEDULER_HOST_CAPACITY_REPORT_INVALID",
                "Host capacity reset time cannot exceed 24 hours",
            )
        replay = connection.execute(
            "SELECT * FROM host_capacity_breakers WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        if replay is not None:
            return {
                "rootId": root_id,
                "status": replay["status"],
                "capacityKey": replay["capacity_key"],
                "resetAt": replay["reset_at"],
                "affectedNodeIds": [],
                "cancelRecurringMonitors": True,
                "wakeMode": "HOST_NATIVE_ONE_SHOT",
                "idempotentReplay": True,
            }
        existing = connection.execute(
            "SELECT * FROM host_capacity_breakers "
            "WHERE capacity_key = ? AND status = 'OPEN'",
            (capacity_key,),
        ).fetchone()
        if existing is not None:
            if _parse_timestamp(normalized_reset_at) > _parse_timestamp(
                existing["reset_at"]
            ):
                fail(
                    "SCHEDULER_HOST_CAPACITY_REPORT_INVALID",
                    "A later report cannot extend an open capacity breaker",
                )
            return {
                "rootId": root_id,
                "status": "OPEN",
                "capacityKey": capacity_key,
                "resetAt": existing["reset_at"],
                "affectedNodeIds": [],
                "cancelRecurringMonitors": True,
                "wakeMode": "HOST_NATIVE_ONE_SHOT",
                "idempotentReplay": True,
            }
        _, target = _node(graph, nodes, node_id)
        if (
            target["status"] != "CLAIMED"
            or target.get("agentId") != affected_agent_id
            or target.get("receiverContextId") != receiver_context_id
        ):
            fail(
                "SCHEDULER_HOST_CAPACITY_REPORT_INVALID",
                "Hard quota evidence must match the claimed host receiver",
                nodeId=node_id,
                status=target["status"],
            )
        connection.execute(
            "INSERT INTO host_capacity_breakers("
            "capacity_key, host_adapter_id, agent_id, reset_at, report_id, "
            "status, reported_at, reason) "
            "VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?) "
            "ON CONFLICT(capacity_key) DO UPDATE SET "
            "host_adapter_id = excluded.host_adapter_id, "
            "agent_id = excluded.agent_id, reset_at = excluded.reset_at, "
            "report_id = excluded.report_id, status = 'OPEN', "
            "reported_at = excluded.reported_at, restored_at = NULL, "
            "reason = excluded.reason",
            (
                capacity_key,
                host_adapter_id,
                affected_agent_id,
                normalized_reset_at,
                report_id,
                at,
                normalized_reason,
            ),
        )
        pause_metadata = json.dumps(
            {"schedulerPause": {"capacityScope": "HOST"}},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        affected_by_run: dict[str, list[str]] = {}
        active_runs = connection.execute(
            "SELECT * FROM runs WHERE status NOT IN "
            "('COMPLETED', 'CANCELLED', 'SUPERSEDED')"
        ).fetchall()
        for affected_run in active_runs:
            affected_nodes = repository.latest_nodes(
                connection,
                affected_run["run_id"],
            )
            matching_nodes = [
                state
                for state in affected_nodes
                if state["status"] == "CLAIMED"
                and state.get("agentId") == affected_agent_id
            ]
            if not matching_nodes:
                continue
            revision = connection.execute(
                "SELECT * FROM delivery_revisions "
                "WHERE root_id = ? AND revision = ?",
                (affected_run["root_id"], affected_run["revision"]),
            ).fetchone()
            if revision is None:
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "Capacity breaker found a run without its revision",
                )
            _, affected_graph = _validated_stored_definition(revision)
            repository.append_event(
                connection,
                run_id=affected_run["run_id"],
                node_id=None,
                attempt=None,
                event_type="HOST_CAPACITY_EXHAUSTED",
                actor=host_adapter_id,
                operation_id=None,
                payload={
                    "capacityKey": capacity_key,
                    "resetAt": normalized_reset_at,
                    "reportedAt": at,
                    "reason": normalized_reason,
                    "reportId": report_id,
                    "affectedNodeIds": sorted(
                        state["nodeId"] for state in matching_nodes
                    ),
                },
                at=at,
            )
            for state in matching_nodes:
                connection.execute(
                    "UPDATE node_runs SET status = 'PAUSED', "
                    "finished_at = ?, lease_expires_at = NULL, "
                    "outcome_json = ? WHERE run_id = ? AND node_id = ? "
                    "AND attempt = ?",
                    (
                        normalized_reset_at,
                        pause_metadata,
                        affected_run["run_id"],
                        state["nodeId"],
                        state["attempt"],
                    ),
                )
                repository.append_event(
                    connection,
                    run_id=affected_run["run_id"],
                    node_id=state["nodeId"],
                    attempt=state["attempt"],
                    event_type="NODE_PAUSED",
                    actor=host_adapter_id,
                    operation_id=state["operationId"],
                    payload={
                        "resumeAt": normalized_reset_at,
                        "capacityScope": "HOST",
                        "hard429": True,
                        "capacityKey": capacity_key,
                    },
                    at=at,
                )
            connection.execute(
                "UPDATE runs SET host_capacity_key = ?, "
                "host_capacity_reset_at = ?, host_capacity_reported_at = ?, "
                "host_capacity_reason = ?, updated_at = ? WHERE run_id = ?",
                (
                    capacity_key,
                    normalized_reset_at,
                    at,
                    normalized_reason,
                    at,
                    affected_run["run_id"],
                ),
            )
            repository.refresh_ready(
                connection,
                affected_graph,
                affected_run["run_id"],
                at=at,
            )
            affected_by_run[affected_run["root_id"]] = sorted(
                state["nodeId"] for state in matching_nodes
            )
        affected = affected_by_run.get(root_id, [])
    for affected_root_id in affected_by_run:
        repository.write_projections(affected_root_id)
    return {
        "rootId": root_id,
        "status": "OPEN",
        "capacityKey": capacity_key,
        "resetAt": normalized_reset_at,
        "affectedNodeIds": affected,
        "cancelRecurringMonitors": True,
        "wakeMode": "HOST_NATIVE_ONE_SHOT",
    }

def _change_claimed_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    operation_id: str,
    target_status: str,
    event_type: str,
    resume_at: str | None,
    capacity_scope: str | None,
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
        normalized_resume_at = (
            _future_timestamp(resume_at, at=at)
            if resume_at is not None
            else None
        )
        normalized_capacity_scope = _capacity_scope(
            capacity_scope,
            has_resume_at=normalized_resume_at is not None,
        )
        pause_metadata = (
            json.dumps(
                {
                    "schedulerPause": {
                        "capacityScope": normalized_capacity_scope,
                    }
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if normalized_capacity_scope is not None
            else None
        )
        connection.execute(
            "UPDATE node_runs SET status = ?, finished_at = ?, "
            "lease_expires_at = NULL, outcome_json = ? "
            "WHERE run_id = ? AND node_id = ? AND attempt = ?",
            (
                target_status,
                normalized_resume_at,
                pause_metadata,
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
            payload=(
                {
                    "resumeAt": normalized_resume_at,
                    "capacityScope": normalized_capacity_scope,
                }
                if normalized_resume_at is not None
                else {}
            ),
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
        if normalized_resume_at is not None:
            result["handoff"]["resumeSequence"] = [
                "workspace_status",
                "graph_frontier",
                "loop_context",
                "dispatch_loop",
            ]
            result.update(
                {
                    "resumeAt": normalized_resume_at,
                    "capacityScope": normalized_capacity_scope,
                    "nextAction": (
                        "WAIT_FOR_HOST_CAPACITY"
                        if normalized_capacity_scope == "HOST"
                        else "WAIT_FOR_EXECUTOR_CAPACITY"
                    ),
                }
            )
    return result

def resume_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
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
    return {
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
