from __future__ import annotations

from typing import Any

from .graph_model import LOOP_NODE_KINDS, graph_assurance_profile
from .graph_runtime import advance_graph
from .loop_contracts import (
    loop_execution_policy,
    resource_claims_overlap,
)
from .repository import SchedulerRepository, timestamp
from .progress_reporting import attach_progress_monitor


def build_graph_frontier(
    graph: dict[str, Any],
    run: dict[str, Any],
    *,
    external_reservations: list[dict[str, Any]] | None = None,
    dispatch_reservations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if run["status"] in {"COMPLETED", "CANCELLED", "SUPERSEDED"}:
        result = {
            "rootId": run["rootId"],
            "runId": run["runId"],
            "status": run["status"],
            "readyLoops": [],
            "activeLoops": [],
            "pausedLoops": [],
            "blockedLoops": [],
            "nextWakeAt": None,
            "workspaceIsolation": run.get("workspaceIsolation"),
            "actions": [],
            "progressMonitor": run.get("progressMonitor"),
        }
        if run.get("gitBinding") is not None:
            result["gitBinding"] = run["gitBinding"]
        return result
    definitions = {
        node["id"]: node
        for node in graph["nodes"]
    }
    task_requirements = {
        item["taskId"]: item
        for item in run.get("taskRequirements", [])
    }
    claimed = [
        state
        for state in run["nodes"]
        if state["status"] == "CLAIMED"
    ]
    claimed_node_ids = {state["nodeId"] for state in claimed}
    actions: list[dict[str, Any]] = []
    ready_loops: list[dict[str, Any]] = []
    active_loops: list[dict[str, Any]] = []
    paused_loops: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    reserved_claims = [
        (
            state["nodeId"],
            definitions[state["nodeId"]]["loop"]["resourceClaims"],
        )
        for state in claimed
    ]
    reserved_claims.extend(
        (
            f"{reservation['rootId']}/{reservation['nodeId']}",
            reservation["resourceClaims"],
        )
        for reservation in (external_reservations or [])
    )
    reserved_claims.extend(
        (
            f"{reservation['rootId']}/{reservation['nodeId']}",
            reservation["resourceClaims"],
        )
        for reservation in (dispatch_reservations or [])
        if not (
            reservation["rootId"] == run["rootId"]
            and reservation["nodeId"] in claimed_node_ids
        )
    )
    local_dispatch_reservations = {
        reservation["nodeId"]: reservation
        for reservation in (dispatch_reservations or [])
        if reservation["rootId"] == run["rootId"]
    }

    for state in sorted(run["nodes"], key=lambda item: item["nodeId"]):
        definition = definitions[state["nodeId"]]
        kind = definition["kind"]
        execution_policy = (
            loop_execution_policy(graph_assurance_profile(graph))
            if kind in LOOP_NODE_KINDS
            else None
        )
        if state["status"] == "READY" and kind in LOOP_NODE_KINDS:
            task_requirement = (
                task_requirements.get(definition["workItemId"])
                if kind == "TASK_LOOP"
                else None
            )
            if (
                task_requirement is not None
                and task_requirement["status"] == "UNFROZEN"
            ):
                ready_loops.append(
                    {
                        "nodeId": state["nodeId"],
                        "kind": kind,
                        "workItemId": definition["workItemId"],
                        "attempt": state["attempt"],
                        "loop": definition["loop"],
                        "resourceConflicts": [],
                        "taskRequirement": task_requirement,
                    }
                )
                actions.append(
                    {
                        "action": "REFREEZE_TASK_REQUIREMENT",
                        "nodeId": state["nodeId"],
                        "taskId": definition["workItemId"],
                        "revision": task_requirement["revision"],
                    }
                )
                continue
            dispatch_reservation = local_dispatch_reservations.get(
                state["nodeId"]
            )
            if dispatch_reservation is not None:
                ready_loops.append(
                    {
                        "nodeId": state["nodeId"],
                        "kind": kind,
                        "workItemId": definition["workItemId"],
                        "attempt": state["attempt"],
                        "loop": definition["loop"],
                        "resourceConflicts": [],
                        "dispatchReservation": {
                            "dispatchReservationId": (
                                dispatch_reservation[
                                    "dispatchReservationId"
                                ]
                            ),
                            "reservationExpiresAt": (
                                dispatch_reservation[
                                    "reservationExpiresAt"
                                ]
                            ),
                        },
                    }
                )
                actions.append(
                    {
                        "action": "WAIT_FOR_DISPATCH_RECEIVER",
                        "nodeId": state["nodeId"],
                        "dispatchReservationId": (
                            dispatch_reservation[
                                "dispatchReservationId"
                            ]
                        ),
                        "reservationExpiresAt": (
                            dispatch_reservation[
                                "reservationExpiresAt"
                            ]
                        ),
                    }
                )
                continue
            conflicts = sorted({
                reserved_node_id
                for reserved_node_id, claims in reserved_claims
                if resource_claims_overlap(
                    definition["loop"]["resourceClaims"],
                    claims,
                )
            })
            record = {
                "nodeId": state["nodeId"],
                "kind": kind,
                "workItemId": definition["workItemId"],
                "attempt": state["attempt"],
                "loop": definition["loop"],
                "resourceConflicts": conflicts,
            }
            if task_requirement is not None:
                record["taskRequirement"] = task_requirement
            ready_loops.append(record)
            if not conflicts:
                reserved_claims.append(
                    (
                        state["nodeId"],
                        definition["loop"]["resourceClaims"],
                    )
                )
                manual_task = (
                    run.get("executionMode") == "manual"
                    and kind == "TASK_LOOP"
                )
                actions.append(
                    {
                        "action": (
                            "CLAIM_MANUAL_TASK"
                            if manual_task
                            else "DISPATCH_LOOP"
                        ),
                        "nodeId": state["nodeId"],
                        "loopRef": definition["loop"]["ref"],
                        "executionPolicy": execution_policy,
                        **(
                            {"dispatchMode": "MANUAL"}
                            if manual_task
                            else {}
                        ),
                    }
                )
        elif state["status"] == "CLAIMED":
            record = {
                "nodeId": state["nodeId"],
                "kind": kind,
                "workItemId": definition["workItemId"],
                "attempt": state["attempt"],
                "owner": state["owner"],
                "agentId": state.get("agentId"),
                "modelId": state.get("modelId"),
                "actualModelId": state.get("actualModelId"),
                "actualModelSource": state.get("actualModelSource"),
                "operationId": state["operationId"],
                "leaseExpiresAt": state["leaseExpiresAt"],
                "progress": state.get("progress"),
                "monitor": state.get("monitor"),
            }
            active_loops.append(record)
            actions.append(
                {
                    "action": "CONTINUE_OR_HEARTBEAT_LOOP",
                    "nodeId": state["nodeId"],
                    "operationId": state["operationId"],
                    "executionPolicy": execution_policy,
                }
            )
        elif state["status"] == "PAUSED" and kind in LOOP_NODE_KINDS:
            resume_at = state.get("resumeAt")
            capacity_scope = state.get("capacityScope")
            record = {
                "nodeId": state["nodeId"],
                "kind": kind,
                "workItemId": definition["workItemId"],
                "attempt": state["attempt"],
                "previousOwner": state["owner"],
                "previousOperationId": state["operationId"],
            }
            if isinstance(resume_at, str):
                record["resumeAt"] = resume_at
            if capacity_scope in {"EXECUTOR", "HOST"}:
                record["capacityScope"] = capacity_scope
            paused_loops.append(record)
            if isinstance(resume_at, str):
                if capacity_scope == "HOST":
                    actions.append(
                        {
                            "action": "WAIT_FOR_HOST_CAPACITY",
                            "nodeId": state["nodeId"],
                            "resumeAt": resume_at,
                            "executionPolicy": execution_policy,
                        }
                    )
                else:
                    actions.append(
                        {
                            "action": "WAIT_FOR_EXECUTOR_CAPACITY",
                            "nodeId": state["nodeId"],
                            "resumeAt": resume_at,
                            "executionPolicy": execution_policy,
                        }
                    )
            else:
                actions.append(
                    {
                        "action": "RESUME_LOOP_IN_INDEPENDENT_CONTEXT",
                        "nodeId": state["nodeId"],
                        "executionPolicy": execution_policy,
                    }
                )
        elif state["status"] in {"BLOCKED", "CANCELLED"}:
            record = {
                "nodeId": state["nodeId"],
                "kind": kind,
                "workItemId": definition["workItemId"],
                "attempt": state["attempt"],
                "failureClass": state["failureClass"],
                "outcome": state["outcome"],
            }
            blocked.append(record)
            actions.append(
                {
                    "action": (
                        "REPLAN_HIERARCHY"
                        if state["failureClass"] == "REPLAN_REQUIRED"
                        else "RESOLVE_LOOP_CANCELLATION"
                        if state["status"] == "CANCELLED"
                        else "RESOLVE_LOOP_BLOCK"
                    ),
                    "nodeId": state["nodeId"],
                }
            )
        elif (
            state["status"] == "READY"
            and kind == "USER_CONFIRMATION"
        ):
            actions.append(
                {
                    "action": "RECORD_USER_CONFIRMATION",
                    "nodeId": state["nodeId"],
                }
            )
    replan_nodes = sorted(
        item["nodeId"]
        for item in blocked
        if item["failureClass"] == "REPLAN_REQUIRED"
    )
    if replan_nodes:
        actions = [
            {
                "action": "REPLAN_HIERARCHY",
                "nodeId": node_id,
            }
            for node_id in replan_nodes
        ]
    host_capacity = run.get("hostCapacity")
    if host_capacity is not None:
        affected_node_ids = sorted(
            item["nodeId"]
            for item in paused_loops
            if item.get("capacityScope") == "HOST"
        )
        actions = [
            action
            for action in actions
            if action["action"]
            not in {
                "DISPATCH_LOOP",
                "CONTINUE_OR_HEARTBEAT_LOOP",
                "WAIT_FOR_HOST_CAPACITY",
            }
        ]
        actions.append(
            {
                "action": "WAIT_FOR_HOST_CAPACITY",
                "resetAt": host_capacity["resetAt"],
                "capacityKey": host_capacity["capacityKey"],
                "affectedNodeIds": affected_node_ids,
                "cancelRecurringMonitors": True,
                "wakeMode": "HOST_NATIVE_ONE_SHOT",
            }
        )
    result = {
        "rootId": run["rootId"],
        "runId": run["runId"],
        "status": run["status"],
        "readyLoops": ready_loops,
        "activeLoops": active_loops,
        "pausedLoops": paused_loops,
        "blockedLoops": blocked,
        "nextWakeAt": min(
            [
                item["resumeAt"]
                for item in paused_loops
                if "resumeAt" in item
            ]
            + [
                reservation["reservationExpiresAt"]
                for reservation in (dispatch_reservations or [])
                if reservation["rootId"] == run["rootId"]
            ],
            default=None,
        ),
        "workspaceIsolation": run.get("workspaceIsolation"),
        "actions": actions,
        "progressMonitor": run.get("progressMonitor"),
    }
    if host_capacity is not None:
        result["hostCapacity"] = host_capacity
        result["nextWakeAt"] = min(
            filter(
                None,
                [result["nextWakeAt"], host_capacity["resetAt"]],
            )
        )
    if run.get("gitBinding") is not None:
        result["gitBinding"] = run["gitBinding"]
    return result


def get_graph_frontier(
    *,
    root: str,
    root_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    run = advance_graph(
        root=root,
        root_id=root_id,
        explicit_dogfood=explicit_dogfood,
        now=now,
    )
    repository = SchedulerRepository(root, now=now)
    graph = repository.hierarchy(root_id)["graph"]
    observation_at = max(timestamp(now), run["updatedAt"])
    run = attach_progress_monitor(
        run,
        graph,
        observed_at=observation_at,
    )
    with repository.read() as connection:
        external_reservations = (
            repository.claimed_resource_reservations(
                connection,
                at=observation_at,
                exclude_root_id=root_id,
            )
        )
        dispatch_reservations = (
            repository.active_dispatch_reservations(
                connection,
                at=observation_at,
            )
        )
    return build_graph_frontier(
        graph,
        run,
        external_reservations=external_reservations,
        dispatch_reservations=dispatch_reservations,
    )


__all__ = (
    "build_graph_frontier",
    "get_graph_frontier",
)
