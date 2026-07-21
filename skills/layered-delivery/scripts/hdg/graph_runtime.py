from __future__ import annotations

from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .graph_model import graph_summary
from .model import scope_patterns_overlap


SUCCESS_STATES = {"SUCCEEDED", "COMPLETED"}
TERMINAL_STATES = SUCCESS_STATES | {"BLOCKED"}


def hierarchy_root_entry(
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    by_id = {item["id"]: item for item in registry["workItems"]}
    current = entry
    visited: set[str] = set()
    while current["parentId"] is not None:
        if current["id"] in visited or current["parentId"] not in by_id:
            fail("WORK_ITEM_HIERARCHY_INVALID", "Work item hierarchy is invalid")
        visited.add(current["id"])
        current = by_id[current["parentId"]]
    return current


def is_descendant(
    registry: dict[str, Any],
    entry: dict[str, Any],
    ancestor_id: str,
) -> bool:
    by_id = {item["id"]: item for item in registry["workItems"]}
    current: dict[str, Any] | None = entry
    visited: set[str] = set()
    while current is not None:
        if current["id"] == ancestor_id:
            return True
        if current["parentId"] is None or current["id"] in visited:
            return False
        visited.add(current["id"])
        current = by_id.get(current["parentId"])
    return False


def _base_node_state(
    node: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    children_by_parent: dict[str, list[dict[str, Any]]],
) -> tuple[str, list[str]]:
    entry = by_id[node["workItemId"]]
    kind = node["kind"]
    if kind == "TASK_EXECUTION":
        if entry["status"] == "CLAIMED":
            return "CLAIMED", []
        latest_result = entry.get("latestResult") or {}
        latest_artifact = latest_result.get("artifact") or {}
        if latest_artifact.get("status") == "IMPLEMENTED":
            return "SUCCEEDED", []
        if latest_artifact.get("status") == "BLOCKED" and entry["status"] == "BLOCKED":
            return "BLOCKED", ["task-execution-blocked"]
        if entry["status"] in {"IMPLEMENTED", "VERIFIED"}:
            return "SUCCEEDED", []
        if entry["status"] == "BLOCKED":
            return "BLOCKED", ["work-item-blocked"]
        root = entry
        visited: set[str] = set()
        while root["parentId"] is not None:
            if root["id"] in visited:
                return "PENDING", ["invalid-hierarchy"]
            visited.add(root["id"])
            root = by_id[root["parentId"]]
        if entry["status"] == "FROZEN" and root.get("developmentMode") is not None:
            return "READY", []
        return "PENDING", ["requirement-not-frozen"]
    if kind.endswith("_GATE"):
        gate_status = entry["gate"]["status"]
        if gate_status == "PASS":
            return "SUCCEEDED", []
        if gate_status == "FAIL":
            return "BLOCKED", ["gate-failed"]
        if kind == "TASK_GATE":
            if entry["status"] == "IMPLEMENTED":
                return "READY", []
            return "PENDING", ["task-not-implemented"]
        children = children_by_parent.get(entry["id"], [])
        if children and all(child["status"] == "VERIFIED" for child in children):
            return "READY", []
        return "PENDING", ["children-not-verified"]
    acceptance = entry.get("acceptance") or {}
    if kind == "ROOT_REVIEW":
        if acceptance.get("status") == "WAITING_FOR_INDEPENDENT_REVIEW":
            return "READY", []
        if acceptance.get("status") in {"WAITING_FOR_USER_CONFIRMATION", "COMPLETED"}:
            return "SUCCEEDED", []
        return "PENDING", ["root-gate-not-passed"]
    if acceptance.get("status") == "COMPLETED":
        return "COMPLETED", []
    if acceptance.get("status") == "WAITING_FOR_USER_CONFIRMATION":
        return "READY", []
    return "PENDING", ["review-not-passed"]


def derive_node_states(
    graph: dict[str, Any],
    registry: dict[str, Any],
    run: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in registry["workItems"]}
    missing = sorted({node["workItemId"] for node in graph["nodes"]} - set(by_id))
    if missing:
        fail("DELIVERY_GRAPH_WORK_ITEM_MISSING", "Delivery graph references missing work items", ids=missing)
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for entry in registry["workItems"]:
        if entry["parentId"] is not None:
            children_by_parent.setdefault(entry["parentId"], []).append(entry)
    incoming: dict[str, list[dict[str, Any]]] = {node["id"]: [] for node in graph["nodes"]}
    for edge in graph["edges"]:
        incoming[edge["target"]].append(edge)
    persisted_by_node = {
        node["nodeId"]: node for node in (run or {}).get("nodes", [])
    }
    states: dict[str, dict[str, Any]] = {}
    for node in graph["nodes"]:
        status, blocked_by = _base_node_state(node, by_id, children_by_parent)
        persisted = persisted_by_node.get(node["id"])
        if (
            persisted is not None
            and persisted["attempt"] > 1
            and persisted["status"] in {"PENDING", "READY"}
            and node["kind"] == "TASK_EXECUTION"
            and by_id[node["workItemId"]]["status"] == "FROZEN"
        ):
            status, blocked_by = "READY", []
        states[node["id"]] = {
            "id": node["id"],
            "kind": node["kind"],
            "planes": node["planes"],
            "workItemId": node["workItemId"],
            "status": status,
            "blockedBy": blocked_by,
            "readyBecause": [],
        }
    for node in graph["nodes"]:
        state = states[node["id"]]
        if state["status"] != "READY":
            continue
        predecessors = incoming[node["id"]]
        unmet = sorted(
            edge["source"]
            for edge in predecessors
            if states[edge["source"]]["status"] not in SUCCESS_STATES
        )
        if unmet:
            state["status"] = "PENDING"
            state["blockedBy"] = unmet
        else:
            state["readyBecause"] = (
                [f"passed:{edge['source']}" for edge in sorted(predecessors, key=lambda item: item["id"])]
                or ["no-unmet-predecessors"]
            )
    return [states[node["id"]] for node in graph["nodes"]]


def _task_write_scope(repository: Any, definition: dict[str, Any]) -> list[str]:
    scope = list(definition["scope"])
    scope.extend(
        item["path"]
        for item in repository.effective_task_file_changes(definition)
    )
    return sorted(set(scope))


def _root_for_requested_item(
    registry: dict[str, Any],
    work_item_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    by_id = {item["id"]: item for item in registry["workItems"]}
    entry = by_id.get(work_item_id)
    if entry is None:
        fail("WORK_ITEM_NOT_FOUND", f"Unknown work item: {work_item_id}", id=work_item_id)
    return entry, hierarchy_root_entry(registry, entry)


def _load_graph_view(*, root: str, work_item_id: str) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    from .repository import GovernanceRepository

    repository = GovernanceRepository(root)
    registry = repository.read_operational_registry()
    requested, root_entry = _root_for_requested_item(registry, work_item_id)
    stored = repository.read_graph_definition(root_entry["id"])
    run = repository.read_graph_run(root_entry["id"], allow_missing=True)
    return repository, registry, requested, stored, run


def get_graph_status(*, root: str, work_item_id: str) -> dict[str, Any]:
    repository, registry, requested, stored, run = _load_graph_view(
        root=root,
        work_item_id=work_item_id,
    )
    graph = stored["graph"]
    current_runs = {node["nodeId"]: node for node in (run or {}).get("nodes", [])}
    nodes = []
    for state in derive_node_states(graph, registry, run):
        persisted = current_runs.get(state["id"])
        nodes.append({
            **state,
            "attempt": persisted["attempt"] if persisted else None,
            "owner": persisted["owner"] if persisted else None,
            "operationId": persisted["operationId"] if persisted else None,
            "recordRevision": persisted["recordRevision"] if persisted else None,
        })
    return {
        "schemaVersion": SCHEMA_VERSION,
        "rootId": graph["rootId"],
        "requestedItemId": requested["id"],
        "hierarchyFingerprint": graph["hierarchyFingerprint"],
        "graphFingerprint": stored["graphFingerprint"],
        "graphSummary": graph_summary(graph),
        "run": None if run is None else {
            key: run[key]
            for key in (
                "runId", "status", "startedAt", "updatedAt", "completedAt", "recordRevision"
            )
        },
        "nodes": nodes,
        "edges": graph["edges"],
    }


def get_graph_frontier(*, root: str, work_item_id: str) -> dict[str, Any]:
    repository, registry, requested, stored, run = _load_graph_view(
        root=root,
        work_item_id=work_item_id,
    )
    graph = stored["graph"]
    if run is None:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "rootId": graph["rootId"],
            "requestedItemId": requested["id"],
            "runId": None,
            "graphFingerprint": stored["graphFingerprint"],
            "actions": [],
            "blocked": [{"nodeId": None, "blockedBy": ["requirement-not-frozen"]}],
        }
    current_runs = {node["nodeId"]: node for node in run["nodes"]}
    states = derive_node_states(graph, registry, run)
    by_item = {item["id"]: item for item in registry["workItems"]}

    active_scopes: list[tuple[str, list[str]]] = []
    for entry in registry["workItems"]:
        if entry.get("claim") and entry["kind"] == "TASK":
            definition = repository.read_package(registry, entry)[0]
            active_scopes.append((entry["id"], _task_write_scope(repository, definition)))
    selected_scopes: list[tuple[str, list[str]]] = []
    requested_states = [
        state
        for state in states
        if is_descendant(registry, by_item[state["workItemId"]], requested["id"])
    ]
    actions: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for state in requested_states:
        persisted = current_runs.get(state["id"])
        state["attempt"] = persisted["attempt"] if persisted else 1
        if state["status"] == "READY" and state["kind"] == "TASK_EXECUTION":
            entry = by_item[state["workItemId"]]
            if repository.is_item_isolated(entry["id"]):
                blocked.append({"nodeId": state["id"], "blockedBy": ["read-only-isolated"]})
                continue
            definition = repository.assert_current_lineage(registry, entry)[0]
            scope = _task_write_scope(repository, definition)
            conflicts = [
                task_id
                for task_id, other_scope in active_scopes + selected_scopes
                if task_id != entry["id"] and scope_patterns_overlap(scope, other_scope)
            ]
            if conflicts:
                blocked.append({
                    "nodeId": state["id"],
                    "blockedBy": [f"scope-conflict:{task_id}" for task_id in sorted(conflicts)],
                })
                continue
            selected_scopes.append((entry["id"], scope))
            actions.append({
                "nodeId": state["id"],
                "nodeKind": state["kind"],
                "action": "DISPATCH_TASK",
                "workItemId": state["workItemId"],
                "attempt": state["attempt"],
                "parallelGroup": f"frontier-{run['recordRevision']}",
                "readyBecause": state["readyBecause"] + ["scope-available"],
            })
        elif state["status"] == "READY":
            action = (
                "RUN_GATE"
                if state["kind"].endswith("_GATE")
                else "REQUEST_REVIEW"
                if state["kind"] == "ROOT_REVIEW"
                else "REQUEST_USER_CONFIRMATION"
            )
            actions.append({
                "nodeId": state["id"],
                "nodeKind": state["kind"],
                "action": action,
                "workItemId": state["workItemId"],
                "attempt": state["attempt"],
                "parallelGroup": None,
                "readyBecause": state["readyBecause"],
            })
        elif state["status"] in {"PENDING", "BLOCKED"}:
            blocked.append({"nodeId": state["id"], "blockedBy": state["blockedBy"]})
    return {
        "schemaVersion": SCHEMA_VERSION,
        "rootId": graph["rootId"],
        "requestedItemId": requested["id"],
        "runId": run["runId"],
        "graphFingerprint": stored["graphFingerprint"],
        "actions": actions,
        "blocked": blocked,
    }


def list_graph_events(*, root: str, work_item_id: str) -> list[dict[str, Any]]:
    repository, registry, _, _, _ = _load_graph_view(root=root, work_item_id=work_item_id)
    root_entry = hierarchy_root_entry(
        registry,
        next(item for item in registry["workItems"] if item["id"] == work_item_id),
    )
    return repository.read_graph_events(root_entry["id"])
