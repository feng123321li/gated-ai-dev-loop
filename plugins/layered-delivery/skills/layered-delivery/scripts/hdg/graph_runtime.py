from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .errors import fail
from .graph_model import FAILURE_CLASSES, LOOP_NODE_KINDS
from .loop_contracts import (
    loop_execution_policy,
    resource_claims_overlap,
    validate_loop_outcome,
)
from .model_rendering import task_baseline_relative_path
from .repository import (
    SchedulerRepository,
    _validated_stored_definition,
    timestamp,
)


IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,191}$")


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        fail(
            "SCHEDULER_IDENTITY_INVALID",
            f"{field} is not a portable scheduler identity",
            field=field,
        )
    return value


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _after(value: str, seconds: int) -> str:
    return (
        _parse_timestamp(value) + timedelta(seconds=seconds)
    ).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _locked_timestamp(now: object, current: str) -> str:
    """Resolve commit time under the scheduler lock without regression."""

    candidate = timestamp(now)
    if _parse_timestamp(candidate) < _parse_timestamp(current):
        return current
    return candidate


def _loaded(
    connection: Any,
    root_id: str,
) -> tuple[dict[str, Any], Any, list[dict[str, Any]]]:
    hierarchy = connection.execute(
        "SELECT * FROM hierarchies "
        "WHERE root_id = ? "
        "AND status = 'FROZEN'",
        (root_id,),
    ).fetchone()
    run = connection.execute(
        "SELECT * FROM runs WHERE root_id = ?",
        (root_id,),
    ).fetchone()
    if hierarchy is None or run is None:
        fail(
            "SCHEDULER_RUN_MISSING",
            f"Frozen scheduler run is missing: {root_id}",
        )
    _, graph = _validated_stored_definition(hierarchy)
    return (
        graph,
        run,
        SchedulerRepository.latest_nodes(connection, run["run_id"]),
    )


def _node(
    graph: dict[str, Any],
    nodes: list[dict[str, Any]],
    node_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    definition = next(
        (item for item in graph["nodes"] if item["id"] == node_id),
        None,
    )
    state = next(
        (item for item in nodes if item["nodeId"] == node_id),
        None,
    )
    if definition is None or state is None:
        fail(
            "SCHEDULER_NODE_MISSING",
            f"Scheduler node is missing: {node_id}",
        )
    return definition, state


def _assert_graph_not_replanning(
    nodes: list[dict[str, Any]],
) -> None:
    replan_nodes = sorted(
        item["nodeId"]
        for item in nodes
        if (
            item["status"] == "BLOCKED"
            and item["failureClass"] == "REPLAN_REQUIRED"
        )
    )
    if replan_nodes:
        fail(
            "SCHEDULER_REPLAN_REQUIRED",
            "The frozen Graph requires replanning and cannot start "
            "additional Loop work",
            nodeIds=replan_nodes,
        )


def _active_claim(
    state: dict[str, Any],
    *,
    operation_id: str,
    at: str,
) -> bool:
    lease_expires_at = state["leaseExpiresAt"]
    return (
        state["status"] == "CLAIMED"
        and state["operationId"] == operation_id
        and isinstance(lease_expires_at, str)
        and _parse_timestamp(lease_expires_at) >= _parse_timestamp(at)
    )


def _upstream_loop_results(
    graph: dict[str, Any],
    states: dict[str, dict[str, Any]],
    node_id: str,
) -> list[dict[str, Any]]:
    definitions = {
        node["id"]: node
        for node in graph["nodes"]
    }
    incoming: dict[str, list[str]] = {
        node["id"]: []
        for node in graph["nodes"]
    }
    for edge in graph["edges"]:
        incoming[edge["target"]].append(edge["source"])
    pending = list(incoming[node_id])
    visited: set[str] = set()
    results: list[dict[str, Any]] = []
    while pending:
        predecessor = pending.pop()
        if predecessor in visited:
            continue
        visited.add(predecessor)
        pending.extend(incoming[predecessor])
        definition = definitions[predecessor]
        if definition["kind"] not in LOOP_NODE_KINDS:
            continue
        state = states[predecessor]
        results.append(
            {
                "nodeId": predecessor,
                "kind": definition["kind"],
                "workItemId": definition["workItemId"],
                "attempt": state["attempt"],
                "status": state["status"],
                "outcome": state["outcome"],
            }
        )
    return sorted(results, key=lambda item: item["nodeId"])


def _retry_if_allowed(
    repository: SchedulerRepository,
    connection: Any,
    *,
    graph: dict[str, Any],
    run_id: str,
    node: dict[str, Any],
    failure_class: str,
    at: str,
) -> bool:
    policy = graph["runtime"]["retryPolicy"]
    if (
        failure_class
        not in policy["automaticFailureClasses"]
        or node["attempt"] >= policy["maxAttempts"]
    ):
        if (
            failure_class in policy["automaticFailureClasses"]
            and node["attempt"] >= policy["maxAttempts"]
        ):
            repository.append_event(
                connection,
                run_id=run_id,
                node_id=node["nodeId"],
                attempt=node["attempt"],
                event_type="RETRY_EXHAUSTED",
                actor="CONTROLLER",
                operation_id=None,
                payload={
                    "failureClass": failure_class,
                    "maxAttempts": policy["maxAttempts"],
                },
                at=at,
            )
        return False
    next_attempt = node["attempt"] + 1
    connection.execute(
        "INSERT INTO node_runs(run_id, node_id, attempt, status) "
        "VALUES (?, ?, ?, 'PENDING')",
        (run_id, node["nodeId"], next_attempt),
    )
    repository.append_event(
        connection,
        run_id=run_id,
        node_id=node["nodeId"],
        attempt=next_attempt,
        event_type="LOOP_RETRY_SCHEDULED",
        actor="CONTROLLER",
        operation_id=None,
        payload={
            "failureClass": failure_class,
            "previousAttempt": node["attempt"],
        },
        at=at,
    )
    return True


def advance_graph(
    *,
    root: str,
    root_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Advance leases, retries, joins, and dependency readiness."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        for node in nodes:
            if (
                node["status"] != "CLAIMED"
                or node["leaseExpiresAt"] is None
                or _parse_timestamp(node["leaseExpiresAt"])
                >= _parse_timestamp(at)
            ):
                continue
            connection.execute(
                "UPDATE node_runs SET status = 'BLOCKED', "
                "finished_at = ?, failure_class = 'WORKER_LOST' "
                "WHERE run_id = ? AND node_id = ? AND attempt = ?",
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
                node_id=node["nodeId"],
                attempt=node["attempt"],
                event_type="CLAIM_LEASE_EXPIRED",
                actor="CONTROLLER",
                operation_id=node["operationId"],
                payload={"failureClass": "WORKER_LOST"},
                at=at,
            )
            _retry_if_allowed(
                repository,
                connection,
                graph=graph,
                run_id=run["run_id"],
                node=node,
                failure_class="WORKER_LOST",
                at=at,
            )
        repository.refresh_ready(
            connection,
            graph,
            run["run_id"],
            at=at,
        )
    repository.write_projections(root_id)
    return repository.run(root_id)


def graph_status(
    *,
    root: str,
    root_id: str,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    definition = repository.hierarchy(root_id)
    run = repository.run(root_id)
    node_by_id = {
        node["id"]: node
        for node in definition["graph"]["nodes"]
    }
    return {
        **run,
        "nodes": [
            {
                **state,
                "kind": node_by_id[state["nodeId"]]["kind"],
                "workItemId": node_by_id[state["nodeId"]][
                    "workItemId"
                ],
            }
            for state in run["nodes"]
        ],
    }


def loop_context(
    *,
    root: str,
    root_id: str,
    node_id: str,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    run = repository.run(root_id)
    definition, state = _node(
        stored["graph"],
        run["nodes"],
        node_id,
    )
    if definition["kind"] not in LOOP_NODE_KINDS:
        fail(
            "SCHEDULER_LOOP_REQUIRED",
            f"{node_id} is not a Loop node",
        )
    predecessors = sorted(
        edge["source"]
        for edge in stored["graph"]["edges"]
        if edge["target"] == node_id
    )
    states = {
        node["nodeId"]: node
        for node in run["nodes"]
    }
    return {
        "rootId": root_id,
        "runId": run["runId"],
        "nodeId": node_id,
        "kind": definition["kind"],
        "workItemId": definition["workItemId"],
        "loop": definition["loop"],
        "skillHints": stored["hierarchy"]["root"]["skillHints"],
        "attempt": state["attempt"],
        "status": state["status"],
        "predecessors": [
            {
                "nodeId": predecessor,
                "status": states[predecessor]["status"],
                "outcome": states[predecessor]["outcome"],
            }
            for predecessor in predecessors
        ],
        "upstreamLoopResults": _upstream_loop_results(
            stored["graph"],
            states,
            node_id,
        ),
        "humanArtifacts": (
            {
                "taskBaseline": (
                    f".layered-delivery/{root_id}/"
                    f"{task_baseline_relative_path(definition['workItemId'])}"
                )
            }
            if definition["kind"] == "TASK_LOOP"
            else {}
        ),
        "executionPolicy": loop_execution_policy(),
        "rules": {
            "payloadIsOpaqueToScheduler": True,
            "internalGateAndSkillPolicyOwnedByLoop": True,
            "skillHintsAreAdvisory": True,
            "selectSkillsAtRuntime": True,
            "prioritizeApplicableSkillHints": True,
            "returnOnlyStandardLoopOutcome": True,
            "coordinatorMustNotExecuteLoopInline": True,
        },
    }


def dispatch_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    owner: str,
    operation_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    owner = _identity(owner, "owner")
    operation_id = _identity(operation_id, "operation_id")
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        _assert_graph_not_replanning(nodes)
        definition, state = _node(graph, nodes, node_id)
        if (
            definition["kind"] not in LOOP_NODE_KINDS
            or state["status"] != "READY"
        ):
            fail(
                "SCHEDULER_LOOP_NOT_READY",
                f"{node_id} is not ready for dispatch",
            )
        used = connection.execute(
            "SELECT 1 FROM graph_events WHERE operation_id = ? LIMIT 1",
            (operation_id,),
        ).fetchone()
        if used is not None:
            fail(
                "SCHEDULER_OPERATION_REUSED",
                "operation_id must be globally unique",
            )
        definitions = {
            item["id"]: item
            for item in graph["nodes"]
        }
        for active in nodes:
            if active["status"] != "CLAIMED":
                continue
            active_definition = definitions[active["nodeId"]]
            if resource_claims_overlap(
                definition["loop"]["resourceClaims"],
                active_definition["loop"]["resourceClaims"],
            ):
                fail(
                    "SCHEDULER_RESOURCE_CONFLICT",
                    f"{node_id} conflicts with active Loop "
                    f"{active['nodeId']}",
                    conflictingNodeId=active["nodeId"],
                )
        lease = graph["runtime"]["claimPolicy"]["leaseSeconds"]
        expires = _after(at, lease)
        connection.execute(
            "UPDATE node_runs SET status = 'CLAIMED', owner = ?, "
            "operation_id = ?, claimed_at = ?, last_heartbeat_at = ?, "
            "lease_expires_at = ? WHERE run_id = ? AND node_id = ? "
            "AND attempt = ?",
            (
                owner,
                operation_id,
                at,
                at,
                expires,
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
            event_type="LOOP_CLAIMED",
            actor=owner,
            operation_id=operation_id,
            payload={"leaseExpiresAt": expires},
            at=at,
        )
        connection.execute(
            "UPDATE runs SET status = 'ACTIVE', updated_at = ? "
            "WHERE run_id = ?",
            (at, run["run_id"]),
        )
    repository.write_projections(root_id)
    return {
        **loop_context(
            root=root,
            root_id=root_id,
            node_id=node_id,
            explicit_dogfood=explicit_dogfood,
        ),
        "owner": owner,
        "operationId": operation_id,
        "leaseExpiresAt": expires,
    }


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
        expires = _after(
            at,
            graph["runtime"]["claimPolicy"]["leaseSeconds"],
        )
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
        repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=node_id,
            attempt=state["attempt"],
            event_type="LOOP_HEARTBEAT",
            actor=state["owner"],
            operation_id=operation_id,
            payload={"leaseExpiresAt": expires},
            at=at,
        )
        connection.execute(
            "UPDATE runs SET updated_at = ? WHERE run_id = ?",
            (at, run["run_id"]),
        )
    repository.write_projections(root_id)
    return {
        "rootId": root_id,
        "nodeId": node_id,
        "status": "CLAIMED",
        "leaseExpiresAt": expires,
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
            "UPDATE node_runs SET status = ?, finished_at = ? "
            "WHERE run_id = ? AND node_id = ? AND attempt = ?",
            (
                target_status,
                at if target_status != "PAUSED" else None,
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
                "executionPolicy": loop_execution_policy(),
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
            "last_heartbeat_at = NULL, lease_expires_at = NULL "
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
        "executionPolicy": loop_execution_policy(),
        "nextAction": (
            "READ_GRAPH_FRONTIER_AND_REDISPATCH_"
            "IN_INDEPENDENT_CONTEXT"
        ),
    }


def record_loop_result(
    *,
    root: str,
    root_id: str,
    node_id: str,
    operation_id: str,
    outcome: object,
    failure_class: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    normalized = validate_loop_outcome(outcome)
    if normalized["status"] == "BLOCKED":
        failure_class = failure_class or "LOOP_BLOCKED"
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
        "outcome": normalized,
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
            "Final completion requires explicit user confirmation",
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
                "Final user confirmation is not ready",
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
    return repository.run(root_id)


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
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        if run["status"] in {"COMPLETED", "CANCELLED"}:
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
    repository.write_projections(root_id)
    return repository.run(root_id)


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
            "finishedAt": None,
            "outcome": None,
            "failureClass": None,
        }
        for node in graph["nodes"]
    }
    histories: dict[str, list[dict[str, Any]]] = {
        node_id: [state]
        for node_id, state in initial.items()
    }
    latest = dict(initial)
    completed_at: str | None = None
    cancelled_at: str | None = None

    for event in events:
        event_type = event["eventType"]
        node_id = event["nodeId"]
        at = event["recordedAt"]
        payload = event["payload"]
        if event_type == "GRAPH_RUN_STARTED":
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
                "finishedAt": None,
                "outcome": None,
                "failureClass": None,
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
        if event_type == "NODE_READY":
            state["status"] = "READY"
        elif event_type == "JOIN_COMPLETED":
            state["status"] = "SUCCEEDED"
            state["finishedAt"] = at
        elif event_type == "LOOP_CLAIMED":
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
        elif event_type == "NODE_PAUSED":
            state["status"] = "PAUSED"
        elif event_type == "NODE_RESUMED":
            state.update(
                {
                    "status": "PENDING",
                    "owner": None,
                    "operationId": None,
                    "claimedAt": None,
                    "lastHeartbeatAt": None,
                    "leaseExpiresAt": None,
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
        elif event_type == "RETRY_EXHAUSTED":
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
                    state["finishedAt"],
                    (
                        json.dumps(
                            state["outcome"],
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if state["outcome"] is not None
                        else None
                    ),
                    state["failureClass"],
                ),
            )
        connection.execute(
            "UPDATE runs SET status = ?, updated_at = ?, "
            "completed_at = ?, cancelled_at = ? WHERE run_id = ?",
            (
                run_status,
                updated_at,
                completed_at,
                cancelled_at,
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


__all__ = (
    "advance_graph",
    "cancel_graph_run",
    "dispatch_loop",
    "graph_events",
    "graph_status",
    "heartbeat_loop",
    "loop_context",
    "pause_loop",
    "record_loop_result",
    "record_user_confirmation",
    "rebuild_graph_run",
    "resume_loop",
)
