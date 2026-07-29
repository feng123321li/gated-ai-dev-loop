from __future__ import annotations

from typing import Any

from .graph_model import LOOP_NODE_KINDS
from .graph_runtime import advance_graph
from .loop_contracts import resource_claims_overlap
from .repository import SchedulerRepository


def build_graph_frontier(
    graph: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    if run["status"] in {"COMPLETED", "CANCELLED"}:
        return {
            "rootId": run["rootId"],
            "runId": run["runId"],
            "status": run["status"],
            "readyLoops": [],
            "activeLoops": [],
            "blockedLoops": [],
            "actions": [],
        }
    definitions = {
        node["id"]: node
        for node in graph["nodes"]
    }
    claimed = [
        state
        for state in run["nodes"]
        if state["status"] == "CLAIMED"
    ]
    actions: list[dict[str, Any]] = []
    ready_loops: list[dict[str, Any]] = []
    active_loops: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    reserved_claims = [
        (
            state["nodeId"],
            definitions[state["nodeId"]]["loop"]["resourceClaims"],
        )
        for state in claimed
    ]

    for state in sorted(run["nodes"], key=lambda item: item["nodeId"]):
        definition = definitions[state["nodeId"]]
        kind = definition["kind"]
        if state["status"] == "READY" and kind in LOOP_NODE_KINDS:
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
    return {
        "rootId": run["rootId"],
        "runId": run["runId"],
        "status": run["status"],
        "readyLoops": ready_loops,
        "activeLoops": active_loops,
        "blockedLoops": blocked,
        "actions": actions,
    }


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
    return build_graph_frontier(graph, run)


__all__ = (
    "build_graph_frontier",
    "get_graph_frontier",
)
