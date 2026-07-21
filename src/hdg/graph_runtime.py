from __future__ import annotations

from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .graph_model import graph_summary
from .jsonio import fingerprint
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


def _set_replay_node(node: dict[str, Any], **changes: Any) -> None:
    if all(node.get(key) == value for key, value in changes.items()):
        return
    persisted_fields = {
        "status", "owner", "operationId", "claimedAt", "finishedAt",
        "latestEvidenceHash",
    }
    persisted_changed = any(
        key in persisted_fields and node.get(key) != value
        for key, value in changes.items()
    )
    node.update(changes)
    if persisted_changed:
        node["recordRevision"] += 1


def _refresh_replay_readiness(
    graph: dict[str, Any],
    current: dict[str, dict[str, Any]],
    *,
    started: bool,
) -> None:
    incoming: dict[str, list[dict[str, Any]]] = {
        node["id"]: [] for node in graph["nodes"]
    }
    for edge in graph["edges"]:
        incoming[edge["target"]].append(edge)
    for node in graph["nodes"]:
        state = current[node["id"]]
        if state["status"] not in {"PENDING", "READY"}:
            continue
        predecessors = sorted(incoming[node["id"]], key=lambda item: item["id"])
        unmet = [
            edge["source"]
            for edge in predecessors
            if current[edge["source"]]["status"] not in SUCCESS_STATES
        ]
        if not started:
            desired = {
                "status": "PENDING",
                "blockedBy": ["graph-run-not-started"],
                "readyBecause": [],
            }
        elif unmet:
            desired = {
                "status": "PENDING",
                "blockedBy": unmet,
                "readyBecause": [],
            }
        else:
            desired = {
                "status": "READY",
                "blockedBy": [],
                "readyBecause": (
                    [f"passed:{edge['source']}" for edge in predecessors]
                    or ["no-unmet-predecessors"]
                ),
            }
        _set_replay_node(state, **desired)


def _new_replay_attempts(
    attempts: object,
    current: dict[str, dict[str, Any]],
    histories: dict[str, list[dict[str, Any]]],
    *,
    recorded_at: str,
) -> None:
    if not isinstance(attempts, list) or not attempts:
        fail("DELIVERY_GRAPH_REPLAY_INVALID", "Retry event does not contain node attempts")
    seen: set[str] = set()
    for item in attempts:
        if (
            not isinstance(item, dict)
            or set(item) != {"nodeId", "attempt", "startedAt"}
            or item.get("nodeId") not in current
            or not isinstance(item.get("attempt"), int)
            or isinstance(item.get("attempt"), bool)
            or item.get("startedAt") != recorded_at
            or item["nodeId"] in seen
        ):
            fail("DELIVERY_GRAPH_REPLAY_INVALID", "Retry event contains an invalid node attempt")
        previous = current[item["nodeId"]]
        if item["attempt"] != previous["attempt"] + 1:
            fail("DELIVERY_GRAPH_REPLAY_INVALID", "Retry attempt sequence is invalid")
        seen.add(item["nodeId"])
        next_attempt = {
            **{
                key: previous[key]
                for key in ("nodeId", "kind", "planes", "workItemId")
            },
            "attempt": item["attempt"],
            "status": "PENDING",
            "owner": None,
            "operationId": None,
            "claimedAt": None,
            "finishedAt": None,
            "latestEvidenceHash": None,
            "recordRevision": 1,
            "blockedBy": [],
            "readyBecause": [],
        }
        histories[item["nodeId"]].append(next_attempt)
        current[item["nodeId"]] = next_attempt


def replay_graph_events(
    graph: dict[str, Any],
    run: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reconstruct every graph node attempt exclusively from the immutable event stream."""
    current: dict[str, dict[str, Any]] = {}
    histories: dict[str, list[dict[str, Any]]] = {}
    for node in graph["nodes"]:
        initial = {
            "nodeId": node["id"],
            "kind": node["kind"],
            "planes": node["planes"],
            "workItemId": node["workItemId"],
            "attempt": 1,
            "status": "PENDING",
            "owner": None,
            "operationId": None,
            "claimedAt": None,
            "finishedAt": None,
            "latestEvidenceHash": None,
            "recordRevision": 1,
            "blockedBy": ["graph-run-not-started"],
            "readyBecause": [],
        }
        current[node["id"]] = initial
        histories[node["id"]] = [initial]

    started = False
    completed_at: str | None = None
    for index, event in enumerate(events):
        if (
            event.get("runId") != run["runId"]
            or event.get("graphFingerprint") != run["graphFingerprint"]
        ):
            fail("DELIVERY_GRAPH_REPLAY_INVALID", "Graph event belongs to a different run or graph")
        event_type = event["eventType"]
        node_id = event["nodeId"]
        payload = event["payload"]
        if event_type == "GRAPH_RUN_STARTED":
            if index != 0 or started or node_id is not None or event["attempt"] is not None:
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Graph start event is invalid")
            started = True
            _refresh_replay_readiness(graph, current, started=True)
            continue
        if not started:
            fail("DELIVERY_GRAPH_REPLAY_INVALID", "Graph event occurred before the run started")
        if node_id not in current:
            fail("DELIVERY_GRAPH_REPLAY_INVALID", "Graph event references an unknown node")

        if event_type in {"NODE_RETRY_SCHEDULED", "GRAPH_INVALIDATED"}:
            if event_type == "GRAPH_INVALIDATED" and not isinstance(
                payload.get("evidenceBinding"), dict
            ):
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Graph invalidation evidence is not bound")
            _new_replay_attempts(
                payload.get("attempts"),
                current,
                histories,
                recorded_at=event["recordedAt"],
            )
            if current[node_id]["attempt"] != event["attempt"]:
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Retry event attempt is inconsistent")
            _refresh_replay_readiness(graph, current, started=True)
            continue

        node = current[node_id]
        if event["attempt"] != node["attempt"]:
            fail("DELIVERY_GRAPH_REPLAY_INVALID", "Graph event attempt is not current")
        binding = payload.get("evidenceBinding")
        evidence_hash = (
            binding.get("boundEvidenceSha256")
            if isinstance(binding, dict)
            else None
        )
        if event_type == "TASK_CLAIMED":
            if node["kind"] != "TASK_EXECUTION" or node["status"] != "READY":
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Task claim transition is invalid")
            owner = payload.get("owner")
            if not isinstance(owner, str) or not owner or not event.get("operationId"):
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Task claim identity is invalid")
            _set_replay_node(
                node,
                status="CLAIMED",
                owner=owner,
                operationId=event["operationId"],
                claimedAt=event["recordedAt"],
                blockedBy=[],
                readyBecause=[],
            )
        elif event_type in {"TASK_IMPLEMENTED", "TASK_BLOCKED"}:
            if (
                node["kind"] != "TASK_EXECUTION"
                or node["status"] != "CLAIMED"
                or node["operationId"] != event.get("operationId")
                or evidence_hash is None
            ):
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Task result transition is invalid")
            _set_replay_node(
                node,
                status="SUCCEEDED" if event_type == "TASK_IMPLEMENTED" else "BLOCKED",
                owner=None,
                operationId=None,
                finishedAt=event["recordedAt"],
                latestEvidenceHash=evidence_hash,
                blockedBy=[] if event_type == "TASK_IMPLEMENTED" else ["task-execution-blocked"],
                readyBecause=[],
            )
        elif event_type in {"GATE_PASSED", "GATE_FAILED"}:
            if not node["kind"].endswith("_GATE") or node["status"] != "READY" or evidence_hash is None:
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Gate transition is invalid")
            _set_replay_node(
                node,
                status="SUCCEEDED" if event_type == "GATE_PASSED" else "BLOCKED",
                finishedAt=event["recordedAt"],
                latestEvidenceHash=evidence_hash,
                blockedBy=[] if event_type == "GATE_PASSED" else ["gate-failed"],
                readyBecause=[],
            )
        elif event_type == "REVIEW_PASSED":
            if node["kind"] != "ROOT_REVIEW" or node["status"] != "READY" or evidence_hash is None:
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Review transition is invalid")
            _set_replay_node(
                node,
                status="SUCCEEDED",
                finishedAt=event["recordedAt"],
                latestEvidenceHash=evidence_hash,
                blockedBy=[],
                readyBecause=[],
            )
        elif event_type == "USER_CONFIRMED":
            if node["kind"] != "USER_CONFIRMATION" or node["status"] != "READY" or evidence_hash is None:
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Confirmation transition is invalid")
            completed_at = event["recordedAt"]
            _set_replay_node(
                node,
                status="COMPLETED",
                finishedAt=completed_at,
                latestEvidenceHash=evidence_hash,
                blockedBy=[],
                readyBecause=[],
            )
        else:
            fail("DELIVERY_GRAPH_REPLAY_INVALID", f"Unknown graph event type: {event_type}")
        _refresh_replay_readiness(graph, current, started=True)

    if not started:
        fail("DELIVERY_GRAPH_REPLAY_INVALID", "Graph run has no start event")
    nodes = [current[node["id"]] for node in graph["nodes"]]
    attempts = [
        attempt
        for node in graph["nodes"]
        for attempt in histories[node["id"]]
    ]
    status = (
        "COMPLETED"
        if any(node["kind"] == "USER_CONFIRMATION" and node["status"] == "COMPLETED" for node in nodes)
        else "BLOCKED"
        if any(node["status"] == "BLOCKED" for node in nodes)
        else "ACTIVE"
    )
    material = {
        "schemaVersion": SCHEMA_VERSION,
        "rootId": graph["rootId"],
        "runId": run["runId"],
        "graphFingerprint": run["graphFingerprint"],
        "status": status,
        "startedAt": events[0]["recordedAt"],
        "updatedAt": events[-1]["recordedAt"],
        "completedAt": completed_at,
        "eventCount": len(events),
        "nodes": nodes,
        "attempts": attempts,
    }
    return {**material, "replayFingerprint": fingerprint(material)}


def replay_mismatches(
    replay: dict[str, Any],
    run: dict[str, Any],
) -> list[dict[str, Any]]:
    persisted = {
        (node["nodeId"], node["attempt"]): node
        for node in run.get("attempts", run["nodes"])
    }
    compared_fields = (
        "status", "owner", "operationId", "claimedAt", "finishedAt", "latestEvidenceHash",
        "recordRevision",
    )
    mismatches: list[dict[str, Any]] = []
    replay_attempts = {
        (node["nodeId"], node["attempt"]): node
        for node in replay["attempts"]
    }
    for key, expected in replay_attempts.items():
        actual = persisted.get(key)
        differences = {
            field: {"expected": expected[field], "actual": None if actual is None else actual[field]}
            for field in compared_fields
            if actual is None or expected[field] != actual[field]
        }
        if differences:
            mismatches.append({
                "nodeId": expected["nodeId"],
                "attempt": expected["attempt"],
                "differences": differences,
            })
    for node_id, attempt in sorted(set(persisted) - set(replay_attempts)):
        mismatches.append({
            "nodeId": node_id,
            "attempt": attempt,
            "differences": {"snapshot": {"expected": None, "actual": "extra"}},
        })
    run_differences = {
        field: {"expected": replay[field], "actual": run.get(field)}
        for field in (
            "rootId", "runId", "graphFingerprint", "status", "startedAt", "updatedAt",
            "completedAt",
        )
        if run.get(field) != replay[field]
    }
    if run_differences:
        mismatches.append({
            "nodeId": None,
            "attempt": None,
            "differences": run_differences,
        })
    return mismatches


def critical_path(
    graph: dict[str, Any],
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    states = {
        node.get("id", node.get("nodeId")): node for node in nodes
    }
    outgoing: dict[str, list[str]] = {node["id"]: [] for node in graph["nodes"]}
    incoming_count = {node["id"]: 0 for node in graph["nodes"]}
    indegree = {node["id"]: 0 for node in graph["nodes"]}
    for edge in graph["edges"]:
        outgoing[edge["source"]].append(edge["target"])
        incoming_count[edge["target"]] += 1
        indegree[edge["target"]] += 1
    ready = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for target in sorted(outgoing[node_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    best: dict[str, tuple[int, list[str]]] = {}
    for node_id in reversed(order):
        successors = [best[target] for target in outgoing[node_id]]
        successor_score, successor_path = max(
            successors,
            key=lambda item: (item[0], tuple(reversed(item[1]))),
            default=(0, []),
        )
        unfinished = states[node_id]["status"] not in SUCCESS_STATES
        best[node_id] = (
            successor_score + (1 if unfinished else 0),
            ([node_id] if unfinished else []) + successor_path,
        )
    remaining, path = max(
        best.values(),
        key=lambda item: (item[0], tuple(reversed(item[1]))),
        default=(0, []),
    )
    next_join = next((node_id for node_id in path if incoming_count[node_id] > 1), None)
    return {
        "nodeIds": path,
        "remainingNodes": remaining,
        "nextJoinNodeId": next_join,
        "blocked": any(states[node_id]["status"] == "BLOCKED" for node_id in path),
    }


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
    replay: dict[str, Any] | None = None
    if run is None:
        nodes = [
            {
                **state,
                "attempt": None,
                "owner": None,
                "operationId": None,
                "claimedAt": None,
                "finishedAt": None,
                "latestEvidenceHash": None,
                "recordRevision": None,
            }
            for state in derive_node_states(graph, registry)
        ]
    else:
        replay = replay_graph_events(
            graph,
            run,
            repository.read_graph_events(graph["rootId"]),
        )
        mismatches = replay_mismatches(replay, run)
        if mismatches:
            fail(
                "DELIVERY_GRAPH_REPLAY_MISMATCH",
                "Persisted graph snapshots do not match the immutable event replay",
                mismatches=mismatches,
            )
        nodes = [
            {"id": node["nodeId"], **{key: value for key, value in node.items() if key != "nodeId"}}
            for node in replay["nodes"]
        ]
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
        "criticalPath": critical_path(graph, nodes),
        "replay": None if replay is None else {
            "eventCount": replay["eventCount"],
            "replayFingerprint": replay["replayFingerprint"],
            "consistentWithSnapshots": True,
        },
    }


def build_graph_frontier(
    repository: Any,
    registry: dict[str, Any],
    requested: dict[str, Any],
    stored: dict[str, Any],
    run: dict[str, Any] | None,
    states: list[dict[str, Any]],
) -> dict[str, Any]:
    graph = stored["graph"]
    if run is None:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "rootId": graph["rootId"],
            "requestedItemId": requested["id"],
            "runId": None,
            "graphFingerprint": stored["graphFingerprint"],
            "actions": [],
            "blocked": [{
                "nodeId": None,
                "nodeKind": None,
                "workItemId": requested["id"],
                "attempt": None,
                "status": "PENDING",
                "blockedBy": ["requirement-not-frozen"],
            }],
            "criticalPath": critical_path(graph, states),
            "summary": {"actionable": 0, "blocked": 1, "claimed": 0},
        }
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
    path = critical_path(graph, states)
    critical_nodes = set(path["nodeIds"])
    for state in requested_states:
        if state["status"] == "READY" and state["kind"] == "TASK_EXECUTION":
            entry = by_item[state["workItemId"]]
            if repository.is_item_isolated(entry["id"]):
                blocked.append({
                    "nodeId": state["id"],
                    "nodeKind": state["kind"],
                    "workItemId": state["workItemId"],
                    "attempt": state["attempt"],
                    "status": state["status"],
                    "blockedBy": ["read-only-isolated"],
                })
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
                    "nodeKind": state["kind"],
                    "workItemId": state["workItemId"],
                    "attempt": state["attempt"],
                    "status": state["status"],
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
                "critical": state["id"] in critical_nodes,
                "commandHint": f"dispatch-task --item {state['workItemId']} --owner <owner> --operation <id>",
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
                "critical": state["id"] in critical_nodes,
                "commandHint": (
                    f"accept-item --item {state['workItemId']} --evidence -"
                    if action == "RUN_GATE"
                    else f"acceptance-item --item {state['workItemId']} --action <action> --evidence -"
                ),
            })
        elif state["status"] in {"PENDING", "BLOCKED", "CLAIMED"}:
            reasons = state["blockedBy"]
            if state["status"] == "CLAIMED":
                reasons = [f"claimed:{state.get('operationId') or 'unknown'}"]
            blocked.append({
                "nodeId": state["id"],
                "nodeKind": state["kind"],
                "workItemId": state["workItemId"],
                "attempt": state["attempt"],
                "status": state["status"],
                "blockedBy": reasons,
            })
    return {
        "schemaVersion": SCHEMA_VERSION,
        "rootId": graph["rootId"],
        "requestedItemId": requested["id"],
        "runId": run["runId"],
        "graphFingerprint": stored["graphFingerprint"],
        "actions": actions,
        "blocked": blocked,
        "criticalPath": path,
        "summary": {
            "actionable": len(actions),
            "blocked": len(blocked),
            "claimed": sum(state["status"] == "CLAIMED" for state in requested_states),
        },
    }


def get_graph_frontier(*, root: str, work_item_id: str) -> dict[str, Any]:
    repository, registry, requested, stored, run = _load_graph_view(
        root=root,
        work_item_id=work_item_id,
    )
    if run is None:
        states = [
            {**state, "attempt": None, "owner": None, "operationId": None}
            for state in derive_node_states(stored["graph"], registry)
        ]
    else:
        replay = replay_graph_events(
            stored["graph"],
            run,
            repository.read_graph_events(stored["graph"]["rootId"]),
        )
        mismatches = replay_mismatches(replay, run)
        if mismatches:
            fail(
                "DELIVERY_GRAPH_REPLAY_MISMATCH",
                "Persisted graph snapshots do not match the immutable event replay",
                mismatches=mismatches,
            )
        states = [
            {"id": node["nodeId"], **{key: value for key, value in node.items() if key != "nodeId"}}
            for node in replay["nodes"]
        ]
    return build_graph_frontier(repository, registry, requested, stored, run, states)


def get_graph_replay(*, root: str, work_item_id: str) -> dict[str, Any]:
    repository, _, _, stored, run = _load_graph_view(
        root=root,
        work_item_id=work_item_id,
    )
    if run is None:
        fail("DELIVERY_GRAPH_RUN_MISSING", "Delivery graph has not been frozen")
    replay = replay_graph_events(
        stored["graph"],
        run,
        repository.read_graph_events(stored["graph"]["rootId"]),
    )
    mismatches = replay_mismatches(replay, run)
    return {
        **replay,
        "consistentWithSnapshots": not mismatches,
        "mismatches": mismatches,
    }


def rebuild_graph_run(
    *,
    root: str,
    work_item_id: str,
    confirmed: bool = False,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    if not confirmed:
        fail("CONFIRMATION_REQUIRED", "Graph snapshot rebuild requires explicit confirmation")
    from .repository import GovernanceRepository, timestamp

    repository = GovernanceRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(repository.now)
    with repository.transaction() as registry:
        requested, root_entry = _root_for_requested_item(registry, work_item_id)
        repository.rebuild_graph_run_from_events(root_entry["id"])
        registry["currentFocus"] = {
            "workItemId": requested["id"],
            "purpose": "GRAPH_REPLAY_REBUILD",
        }
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_registry(registry)
    return get_graph_replay(root=root, work_item_id=work_item_id)


def list_graph_events(*, root: str, work_item_id: str) -> list[dict[str, Any]]:
    repository, registry, _, _, _ = _load_graph_view(root=root, work_item_id=work_item_id)
    root_entry = hierarchy_root_entry(
        registry,
        next(item for item in registry["workItems"] if item["id"] == work_item_id),
    )
    return repository.read_graph_events(root_entry["id"])
