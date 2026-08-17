from __future__ import annotations

from copy import deepcopy
from typing import Any

from .graph_runtime import graph_status
from .repository import SchedulerRepository


_RUN_FIELDS = (
    "runId",
    "status",
    "executionMode",
    "startedAt",
    "updatedAt",
    "completedAt",
    "cancelledAt",
    "supersededAt",
)
_REVISION_FIELDS = (
    "revision",
    "status",
    "runId",
    "runStatus",
    "createdAt",
    "updatedAt",
    "frozenAt",
    "completedAt",
    "cancelledAt",
    "supersededAt",
)
_NODE_DISPLAY_FIELDS = (
    "agentId",
)
_PROGRESS_FIELDS = (
    "progressPercent",
)
_MONITOR_FIELDS = (
    "phaseZh",
    "summaryZh",
    "heartbeatZh",
    "healthZh",
)
_ALERT_FIELDS = (
    "nodeId",
    "code",
    "messageZh",
)
_PROGRESS_MONITOR_FIELDS = (
    "observedAt",
    "recommendedPollSeconds",
)
_LOOP_KINDS = frozenset(
    {
        "TASK_LOOP",
        "TASK_REVIEW_LOOP",
        "GROUP_REVIEW_LOOP",
        "DELIVERY_REVIEW_LOOP",
    }
)


def _allowlisted_mapping(
    value: object,
    fields: tuple[str, ...],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        field: deepcopy(value[field])
        for field in fields
        if field in value and value[field] is not None
    }


def _progress_monitor_snapshot(status: dict[str, Any]) -> dict[str, Any]:
    source = status.get("progressMonitor")
    if not isinstance(source, dict):
        source = {}
    snapshot = {
        field: deepcopy(source[field])
        for field in _PROGRESS_MONITOR_FIELDS
        if field in source and source[field] is not None
    }
    alerts = source.get("alerts")
    snapshot["alerts"] = [
        projected
        for alert in alerts if isinstance(alert, dict)
        if (
            projected := _allowlisted_mapping(alert, _ALERT_FIELDS)
        ) is not None
    ] if isinstance(alerts, list) else []
    return snapshot


def _display_nodes(
    graph: dict[str, Any],
    status: dict[str, Any],
) -> list[dict[str, Any]]:
    state_by_id = {
        item["nodeId"]: item
        for item in status.get("nodes", [])
        if isinstance(item, dict) and isinstance(item.get("nodeId"), str)
    }
    result = []
    for definition in graph.get("nodes", []):
        if not isinstance(definition, dict):
            continue
        node_id = definition.get("id")
        if not isinstance(node_id, str):
            continue
        state = state_by_id.get(node_id, {})
        node: dict[str, Any] = {
            "id": node_id,
            "kind": definition.get("kind"),
            "workItemId": definition.get("workItemId"),
            "status": state.get("status", "UNKNOWN"),
            "attempt": state.get("attempt", 0),
        }
        for field in _NODE_DISPLAY_FIELDS:
            value = state.get(field)
            if value is not None:
                node[field] = deepcopy(value)
        progress = _allowlisted_mapping(
            state.get("progress"),
            _PROGRESS_FIELDS,
        )
        if progress:
            node["progress"] = progress
        monitor = _allowlisted_mapping(
            state.get("monitor"),
            _MONITOR_FIELDS,
        )
        if monitor:
            node["monitor"] = monitor
        result.append(node)
    return result


def _frontier_snapshot(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    ready = [
        deepcopy(node)
        for node in nodes
        if node["status"] == "READY" and node.get("kind") in _LOOP_KINDS
    ]
    active = [
        deepcopy(node)
        for node in nodes
        if node["status"] == "CLAIMED"
    ]
    paused = [
        deepcopy(node)
        for node in nodes
        if node["status"] == "PAUSED"
    ]
    blocked = [
        deepcopy(node)
        for node in nodes
        if node["status"] in {"BLOCKED", "CANCELLED"}
    ]
    return {
        "readyLoops": ready,
        "activeLoops": active,
        "pausedLoops": paused,
        "blockedLoops": blocked,
    }


def open_delivery_dashboard(
    *,
    root: str,
    root_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Build a bounded, read-only dashboard projection for one Delivery."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.scheduler_lock():
        definition = repository.hierarchy(root_id)
        status = graph_status(
            root=root,
            root_id=root_id,
            explicit_dogfood=explicit_dogfood,
            now=now,
        )
        history = repository.revision_history(root_id)
    nodes = _display_nodes(definition["graph"], status)
    frontier = _frontier_snapshot(nodes)
    progress_monitor = _progress_monitor_snapshot(status)
    alerts = progress_monitor["alerts"]
    completed = sum(
        node["status"] in {"SUCCEEDED", "COMPLETED"}
        for node in nodes
    )
    total = len(nodes)
    delivery = definition["hierarchy"]["delivery"]
    revisions = [
        {
            field: deepcopy(revision.get(field))
            for field in _REVISION_FIELDS
            if field in revision
        }
        for revision in history.get("revisions", [])
        if isinstance(revision, dict)
    ]
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "observedAt": (
            progress_monitor.get("observedAt") or status.get("updatedAt")
        ),
        "delivery": {
            "id": definition["rootId"],
            "title": delivery.get("title", definition["rootId"]),
            "summary": delivery.get("summary", ""),
            "status": definition["status"],
            "revision": definition["deliveryRevision"],
            "updatedAt": definition["updatedAt"],
        },
        "run": {
            field: deepcopy(status.get(field))
            for field in _RUN_FIELDS
            if field in status
        },
        "summary": {
            "completedNodes": completed,
            "totalNodes": total,
            "activeLoops": len(frontier["activeLoops"]),
            "readyLoops": len(frontier["readyLoops"]),
            "pendingReviews": sum(
                node["status"] == "READY"
                and isinstance(node.get("kind"), str)
                and "REVIEW" in node["kind"]
                for node in nodes
            ),
            "alerts": len(alerts) + len(frontier["blockedLoops"]),
        },
        "graph": {
            "nodes": nodes,
            "edges": [
                {
                    "source": edge.get("source"),
                    "target": edge.get("target"),
                }
                for edge in definition["graph"].get("edges", [])
                if isinstance(edge, dict)
            ],
        },
        "frontier": frontier,
        "progressMonitor": progress_monitor,
        "currentRevision": history.get("currentRevision"),
        "revisions": revisions,
    }


__all__ = ("open_delivery_dashboard",)
