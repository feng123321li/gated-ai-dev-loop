from __future__ import annotations

from typing import Any

from .graph_model import LOOP_NODE_KINDS
from .graph_runtime import advance_graph
from .loop_contracts import (
    loop_execution_policy,
    resource_claims_overlap,
)
from .repository import SchedulerRepository


def build_graph_frontier(
    graph: dict[str, Any],
    run: dict[str, Any],
    *,
    external_reservations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if run["status"] in {"COMPLETED", "CANCELLED"}:
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

    for state in sorted(run["nodes"], key=lambda item: item["nodeId"]):
        definition = definitions[state["nodeId"]]
        kind = definition["kind"]
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
            conflicts = sorted(
                reserved_node_id
                for reserved_node_id, claims in reserved_claims
                if resource_claims_overlap(
                    definition["loop"]["resourceClaims"],
                    claims,
                )
            )
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
                actions.append(
                    {
                        "action": "DISPATCH_LOOP",
                        "nodeId": state["nodeId"],
                        "loopRef": definition["loop"]["ref"],
                        "executionPolicy": loop_execution_policy(),
                    }
                )
        elif state["status"] == "CLAIMED":
            record = {
                "nodeId": state["nodeId"],
                "kind": kind,
                "workItemId": definition["workItemId"],
                "attempt": state["attempt"],
                "owner": state["owner"],
                "operationId": state["operationId"],
                "leaseExpiresAt": state["leaseExpiresAt"],
            }
            active_loops.append(record)
            actions.append(
                {
                    "action": "CONTINUE_OR_HEARTBEAT_LOOP",
                    "nodeId": state["nodeId"],
                    "operationId": state["operationId"],
                    "executionPolicy": loop_execution_policy(),
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
                            "executionPolicy": loop_execution_policy(),
                        }
                    )
                else:
                    actions.append(
                        {
                            "action": "WAIT_FOR_EXECUTOR_CAPACITY",
                            "nodeId": state["nodeId"],
                            "resumeAt": resume_at,
                            "executionPolicy": loop_execution_policy(),
                        }
                    )
            else:
                actions.append(
                    {
                        "action": "RESUME_LOOP_IN_INDEPENDENT_CONTEXT",
                        "nodeId": state["nodeId"],
                        "executionPolicy": loop_execution_policy(),
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
    result = {
        "rootId": run["rootId"],
        "runId": run["runId"],
        "status": run["status"],
        "readyLoops": ready_loops,
        "activeLoops": active_loops,
        "pausedLoops": paused_loops,
        "blockedLoops": blocked,
        "nextWakeAt": min(
            (
                item["resumeAt"]
                for item in paused_loops
                if "resumeAt" in item
            ),
            default=None,
        ),
        "workspaceIsolation": run.get("workspaceIsolation"),
        "actions": actions,
    }
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
    repository = SchedulerRepository(root)
    graph = repository.hierarchy(root_id)["graph"]
    with repository.read() as connection:
        external_reservations = (
            repository.claimed_resource_reservations(
                connection,
                at=run["updatedAt"],
                exclude_root_id=root_id,
            )
        )
    return build_graph_frontier(
        graph,
        run,
        external_reservations=external_reservations,
    )


__all__ = (
    "build_graph_frontier",
    "get_graph_frontier",
)
