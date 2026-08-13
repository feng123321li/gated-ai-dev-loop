from __future__ import annotations

import re
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .jsonio import fingerprint
from .loop_contracts import (
    validate_loop_descriptor,
)
from .model_core import safe_id, work_item_dependencies


GRAPH_NODE_KINDS = (
    "TASK_LOOP",
    "TASK_REVIEW_LOOP",
    "GROUP_JOIN",
    "GROUP_REVIEW_LOOP",
    "DELIVERY_REVIEW_LOOP",
    "USER_CONFIRMATION",
)
REVIEW_NODE_KINDS = (
    "TASK_REVIEW_LOOP",
    "GROUP_REVIEW_LOOP",
    "DELIVERY_REVIEW_LOOP",
)
LOOP_NODE_KINDS = ("TASK_LOOP", *REVIEW_NODE_KINDS)
JOIN_NODE_KINDS = ("GROUP_JOIN",)
GRAPH_EDGE_KINDS = ("REQUIRES_SUCCESS", "ALL_OF")
GRAPH_PLANES = ("EXECUTION", "GOVERNANCE")
RUNTIME_STATES = (
    "PENDING",
    "READY",
    "CLAIMED",
    "SUCCEEDED",
    "BLOCKED",
    "PAUSED",
    "CANCELLED",
    "COMPLETED",
)
RUNTIME_TERMINAL_STATES = ("CANCELLED", "COMPLETED")
FAILURE_CLASSES = (
    "RETRYABLE_INFRA",
    "WORKER_LOST",
    "LOOP_BLOCKED",
    "REPLAN_REQUIRED",
    "EXTERNAL_AUTHORITY",
    "NON_RETRYABLE",
)
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_CLAIM_LEASE_SECONDS = 30 * 60
DEFAULT_HEARTBEAT_SECONDS = 5 * 60
DEFAULT_CLAIM_GRACE_SECONDS = 2 * 60
GRAPH_FIELDS = {
    "schemaVersion",
    "rootId",
    "hierarchyFingerprint",
    "nodes",
    "edges",
    "runtime",
}
GRAPH_NODE_FIELDS = {"id", "kind", "planes", "workItemId", "loop"}
GRAPH_EDGE_FIELDS = {
    "id",
    "source",
    "target",
    "kind",
    "plane",
    "joinGroup",
}
RUNTIME_TRANSITION_FIELDS = {
    "id",
    "eventType",
    "fromStates",
    "toStates",
    "routeCondition",
    "nodeKinds",
    "automatic",
    "createsAttempt",
}
GRAPH_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9:._-]*$")
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


def loop_node_id(task_id: str) -> str:
    return f"loop:{safe_id(task_id)}"


def task_review_node_id(task_id: str) -> str:
    return f"review:task:{safe_id(task_id)}"


def join_node_id(work_item_id: str) -> str:
    return f"join:{safe_id(work_item_id)}"


def review_node_id(root_id: str) -> str:
    return f"review:delivery:{safe_id(root_id)}"


def group_review_node_id(group_id: str) -> str:
    return f"review:group:{safe_id(group_id)}"


def confirmation_node_id(root_id: str) -> str:
    return f"confirm:{safe_id(root_id)}"


def _node(
    node_id: str,
    kind: str,
    work_item_id: str,
    *,
    loop: dict[str, Any] | None = None,
) -> dict[str, Any]:
    planes = (
        ["GOVERNANCE"]
        if kind in {*REVIEW_NODE_KINDS, "USER_CONFIRMATION"}
        else ["EXECUTION"]
    )
    return {
        "id": node_id,
        "kind": kind,
        "planes": planes,
        "workItemId": work_item_id,
        "loop": validate_loop_descriptor(loop) if loop is not None else None,
    }


def _edge(
    source: str,
    target: str,
    kind: str,
    *,
    plane: str,
    join_group: str | None = None,
) -> dict[str, Any]:
    return {
        "id": f"edge:{source}:{kind.lower()}:{target}",
        "source": source,
        "target": target,
        "kind": kind,
        "plane": plane,
        "joinGroup": join_group,
    }


def _transition(
    event_type: str,
    from_states: list[str],
    to_states: str | list[str],
    route_condition: str,
    node_kinds: list[str],
    *,
    automatic: bool,
    creates_attempt: bool = False,
) -> dict[str, Any]:
    return {
        "id": f"transition:{event_type.lower().replace('_', '-')}",
        "eventType": event_type,
        "fromStates": sorted(from_states),
        "toStates": sorted(
            [to_states] if isinstance(to_states, str) else to_states
        ),
        "routeCondition": route_condition,
        "nodeKinds": sorted(node_kinds),
        "automatic": automatic,
        "createsAttempt": creates_attempt,
    }


def compile_runtime_policy() -> dict[str, Any]:
    """Return the scheduler-only FSM frozen into every delivery graph."""

    all_kinds = list(GRAPH_NODE_KINDS)
    loops = list(LOOP_NODE_KINDS)
    transitions = [
        _transition(
            "GRAPH_RUN_STARTED",
            ["PENDING"],
            ["PENDING", "READY"],
            "ON_START",
            all_kinds,
            automatic=True,
        ),
        _transition(
            "NODE_READY",
            ["PENDING"],
            "READY",
            "ON_PREDECESSORS_SUCCEEDED",
            [*loops, "USER_CONFIRMATION"],
            automatic=True,
        ),
        _transition(
            "LOOP_CLAIMED",
            ["READY"],
            "CLAIMED",
            "ON_DISPATCH",
            loops,
            automatic=False,
        ),
        _transition(
            "LOOP_HEARTBEAT",
            ["CLAIMED"],
            "CLAIMED",
            "ON_HEARTBEAT",
            loops,
            automatic=False,
        ),
        _transition(
            "LOOP_SUCCEEDED",
            ["CLAIMED"],
            "SUCCEEDED",
            "ON_SUCCESS",
            loops,
            automatic=False,
        ),
        _transition(
            "LOOP_BLOCKED",
            ["CLAIMED"],
            "BLOCKED",
            "ON_FAILURE",
            loops,
            automatic=False,
        ),
        _transition(
            "LOOP_REPLAN_REQUIRED",
            ["CLAIMED"],
            "BLOCKED",
            "ON_REPLAN_REQUIRED",
            loops,
            automatic=False,
        ),
        _transition(
            "LOOP_CANCELLED",
            ["CLAIMED"],
            "CANCELLED",
            "ON_LOOP_CANCELLED",
            loops,
            automatic=False,
        ),
        _transition(
            "CLAIM_LEASE_EXPIRED",
            ["CLAIMED"],
            "BLOCKED",
            "ON_LEASE_EXPIRED",
            loops,
            automatic=True,
        ),
        _transition(
            "NODE_PAUSED",
            ["CLAIMED"],
            "PAUSED",
            "ON_PAUSE",
            loops,
            automatic=False,
        ),
        _transition(
            "NODE_RESUMED",
            ["PAUSED"],
            "PENDING",
            "ON_RESUME",
            loops,
            automatic=False,
        ),
        _transition(
            "NODE_AUTO_RESUMED",
            ["PAUSED"],
            "PENDING",
            "ON_RESUME_AT_REACHED",
            loops,
            automatic=True,
        ),
        _transition(
            "JOIN_COMPLETED",
            ["PENDING"],
            "SUCCEEDED",
            "ON_ALL_SUCCESS",
            list(JOIN_NODE_KINDS),
            automatic=True,
        ),
        _transition(
            "USER_CONFIRMED",
            ["READY"],
            "COMPLETED",
            "ON_CONFIRMATION",
            ["USER_CONFIRMATION"],
            automatic=False,
        ),
        _transition(
            "LOOP_RETRY_SCHEDULED",
            ["BLOCKED"],
            ["PENDING", "READY"],
            "ON_INFRA_RETRY_ALLOWED",
            loops,
            automatic=True,
            creates_attempt=True,
        ),
        _transition(
            "RETRY_EXHAUSTED",
            ["BLOCKED"],
            "BLOCKED",
            "ON_RETRY_EXHAUSTED",
            loops,
            automatic=True,
        ),
        _transition(
            "GRAPH_RUN_CANCELLED",
            ["BLOCKED", "CLAIMED", "PAUSED", "PENDING", "READY"],
            "CANCELLED",
            "ON_CANCEL",
            all_kinds,
            automatic=False,
        ),
    ]
    return {
        "states": list(RUNTIME_STATES),
        "terminalStates": list(RUNTIME_TERMINAL_STATES),
        "retryPolicy": {
            "maxAttempts": DEFAULT_MAX_ATTEMPTS,
            "automaticFailureClasses": [
                "RETRYABLE_INFRA",
                "WORKER_LOST",
            ],
            "onExhausted": "BLOCK_RUN",
        },
        "claimPolicy": {
            "leaseSeconds": DEFAULT_CLAIM_LEASE_SECONDS,
            "heartbeatSeconds": DEFAULT_HEARTBEAT_SECONDS,
            "graceSeconds": DEFAULT_CLAIM_GRACE_SECONDS,
            "claimMode": "JUST_IN_TIME_ON_LOOP_START",
            "onExpired": "RETRY_LOOP",
        },
        "transitions": sorted(
            transitions,
            key=lambda item: item["id"],
        ),
    }


def runtime_transition(
    graph: dict[str, Any],
    *,
    event_type: str,
    node_kind: str,
    from_state: str,
) -> dict[str, Any]:
    for transition in graph["runtime"]["transitions"]:
        if (
            transition["eventType"] == event_type
            and node_kind in transition["nodeKinds"]
            and from_state in transition["fromStates"]
        ):
            return transition
    fail(
        "DELIVERY_GRAPH_TRANSITION_INVALID",
        f"{event_type} is not legal for {node_kind} from {from_state}",
    )


def _walk_hierarchy(hierarchy: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    pending = [hierarchy["root"]]
    while pending:
        node = pending.pop()
        result.append(node)
        pending.extend(reversed(node["children"]))
    return result


def _terminal_node_id(hierarchy_node: dict[str, Any]) -> str:
    definition = hierarchy_node["definition"]
    if definition["kind"] == "TASK":
        return (
            task_review_node_id(definition["id"])
            if hierarchy_node["reviewLoop"] is not None
            else loop_node_id(definition["id"])
        )
    return (
        group_review_node_id(definition["id"])
        if hierarchy_node["reviewLoop"] is not None
        else join_node_id(definition["id"])
    )


def _entry_node_ids(hierarchy_node: dict[str, Any]) -> list[str]:
    definition = hierarchy_node["definition"]
    if definition["kind"] == "TASK":
        return [loop_node_id(definition["id"])]
    entries: list[str] = []
    for child in hierarchy_node["children"]:
        if work_item_dependencies(child["definition"]):
            continue
        entries.extend(_entry_node_ids(child))
    return sorted(set(entries))


def compile_delivery_graph(
    hierarchy: dict[str, Any],
    *,
    hierarchy_fingerprint: str,
) -> dict[str, Any]:
    """Compile scheduling metadata without interpreting Loop payloads."""

    if not FINGERPRINT.fullmatch(hierarchy_fingerprint):
        fail(
            "DELIVERY_GRAPH_FINGERPRINT_INVALID",
            "Hierarchy fingerprint is invalid",
        )
    delivery = hierarchy["delivery"]
    root_id = delivery["id"]
    assurance_profile = delivery.get("assuranceProfile", "STANDARD")
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    hierarchy_nodes = _walk_hierarchy(hierarchy)
    if assurance_profile == "LIGHT":
        root_definition = hierarchy["root"]["definition"]
        task_node_id = loop_node_id(root_definition["id"])
        confirmation_id = confirmation_node_id(root_id)
        graph = {
            "schemaVersion": SCHEMA_VERSION,
            "rootId": root_id,
            "hierarchyFingerprint": hierarchy_fingerprint,
            "nodes": sorted(
                [
                    _node(
                        task_node_id,
                        "TASK_LOOP",
                        root_definition["id"],
                        loop=root_definition["execution"]["loop"],
                    ),
                    _node(
                        confirmation_id,
                        "USER_CONFIRMATION",
                        root_id,
                    ),
                ],
                key=lambda item: item["id"],
            ),
            "edges": [
                _edge(
                    task_node_id,
                    confirmation_id,
                    "REQUIRES_SUCCESS",
                    plane="GOVERNANCE",
                )
            ],
            "runtime": compile_runtime_policy(),
        }
        return validate_delivery_graph(graph)
    by_id = {
        item["definition"]["id"]: item
        for item in hierarchy_nodes
    }

    for hierarchy_node in hierarchy_nodes:
        definition = hierarchy_node["definition"]
        item_id = definition["id"]
        kind = definition["kind"]
        if kind == "TASK":
            nodes.append(
                _node(
                    loop_node_id(item_id),
                    "TASK_LOOP",
                    item_id,
                    loop=definition["execution"]["loop"],
                )
            )
            nodes.append(
                _node(
                    task_review_node_id(item_id),
                    "TASK_REVIEW_LOOP",
                    item_id,
                    loop=hierarchy_node["reviewLoop"],
                )
            )
            edges.append(
                _edge(
                    loop_node_id(item_id),
                    task_review_node_id(item_id),
                    "REQUIRES_SUCCESS",
                    plane="GOVERNANCE",
                )
            )
            continue

        nodes.append(
            _node(
                join_node_id(item_id),
                "GROUP_JOIN",
                item_id,
            )
        )
        join_group = f"join:{item_id}:children"
        for child in hierarchy_node["children"]:
            edges.append(
                _edge(
                    _terminal_node_id(child),
                    join_node_id(item_id),
                    "ALL_OF",
                    plane="EXECUTION",
                    join_group=join_group,
                )
            )
        if hierarchy_node["reviewLoop"] is not None:
            nodes.append(
                _node(
                    group_review_node_id(item_id),
                    "GROUP_REVIEW_LOOP",
                    item_id,
                    loop=hierarchy_node["reviewLoop"],
                )
            )
            edges.append(
                _edge(
                    join_node_id(item_id),
                    group_review_node_id(item_id),
                    "REQUIRES_SUCCESS",
                    plane="GOVERNANCE",
                )
            )

    for hierarchy_node in hierarchy_nodes:
        definition = hierarchy_node["definition"]
        for dependency_id in work_item_dependencies(definition):
            dependency = by_id.get(dependency_id)
            if dependency is None:
                fail(
                    "DELIVERY_GRAPH_DEPENDENCY_INVALID",
                    "Sibling dependency is missing from the graph",
                )
            for entry_node_id in _entry_node_ids(hierarchy_node):
                edges.append(
                    _edge(
                        _terminal_node_id(dependency),
                        entry_node_id,
                        "REQUIRES_SUCCESS",
                        plane="EXECUTION",
                    )
                )

    root_terminal = _terminal_node_id(hierarchy["root"])
    nodes.extend(
        [
            _node(
                review_node_id(root_id),
                "DELIVERY_REVIEW_LOOP",
                root_id,
                loop=delivery["reviewLoop"],
            ),
            _node(
                confirmation_node_id(root_id),
                "USER_CONFIRMATION",
                root_id,
            ),
        ]
    )
    edges.extend(
        [
            _edge(
                root_terminal,
                review_node_id(root_id),
                "REQUIRES_SUCCESS",
                plane="GOVERNANCE",
            ),
            _edge(
                review_node_id(root_id),
                confirmation_node_id(root_id),
                "REQUIRES_SUCCESS",
                plane="GOVERNANCE",
            ),
        ]
    )
    graph = {
        "schemaVersion": SCHEMA_VERSION,
        "rootId": root_id,
        "hierarchyFingerprint": hierarchy_fingerprint,
        "nodes": sorted(nodes, key=lambda item: item["id"]),
        "edges": sorted(edges, key=lambda item: item["id"]),
        "runtime": compile_runtime_policy(),
    }
    return validate_delivery_graph(graph)


def _validate_acyclic(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    outgoing: dict[str, list[str]] = {
        node["id"]: []
        for node in nodes
    }
    indegree = {node["id"]: 0 for node in nodes}
    for edge in edges:
        outgoing[edge["source"]].append(edge["target"])
        indegree[edge["target"]] += 1
    ready = sorted(
        node_id
        for node_id, degree in indegree.items()
        if degree == 0
    )
    visited = 0
    while ready:
        node_id = ready.pop(0)
        visited += 1
        for target in sorted(outgoing[node_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    if visited != len(nodes):
        fail(
            "DELIVERY_GRAPH_CYCLE",
            "Delivery graph must be acyclic",
        )


def _expected_node_id(kind: str, work_item_id: str) -> str:
    if kind == "TASK_LOOP":
        return loop_node_id(work_item_id)
    if kind == "TASK_REVIEW_LOOP":
        return task_review_node_id(work_item_id)
    if kind in JOIN_NODE_KINDS:
        return join_node_id(work_item_id)
    if kind == "GROUP_REVIEW_LOOP":
        return group_review_node_id(work_item_id)
    if kind == "DELIVERY_REVIEW_LOOP":
        return review_node_id(work_item_id)
    return confirmation_node_id(work_item_id)


def validate_delivery_graph(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != GRAPH_FIELDS:
        fail(
            "DELIVERY_GRAPH_INVALID",
            "Delivery graph fields are invalid",
        )
    if value.get("schemaVersion") != SCHEMA_VERSION:
        fail(
            "DELIVERY_GRAPH_INVALID",
            "Delivery graph schemaVersion is invalid",
        )
    root_id = safe_id(value.get("rootId"), "rootId")
    if not FINGERPRINT.fullmatch(
        str(value.get("hierarchyFingerprint", ""))
    ):
        fail(
            "DELIVERY_GRAPH_FINGERPRINT_INVALID",
            "Delivery graph hierarchy fingerprint is invalid",
        )
    if (
        not isinstance(value.get("nodes"), list)
        or not isinstance(value.get("edges"), list)
    ):
        fail(
            "DELIVERY_GRAPH_INVALID",
            "Delivery graph nodes and edges must be arrays",
        )
    if value.get("runtime") != compile_runtime_policy():
        fail(
            "DELIVERY_GRAPH_RUNTIME_INVALID",
            "Delivery graph runtime FSM or router policy is invalid",
        )

    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for entry in value["nodes"]:
        if (
            not isinstance(entry, dict)
            or set(entry) != GRAPH_NODE_FIELDS
            or not isinstance(entry.get("id"), str)
            or not GRAPH_IDENTIFIER.fullmatch(entry["id"])
            or entry["id"] in node_ids
            or entry.get("kind") not in GRAPH_NODE_KINDS
            or not isinstance(entry.get("planes"), list)
            or entry["planes"] != sorted(set(entry["planes"]))
            or not entry["planes"]
            or any(
                plane not in GRAPH_PLANES
                for plane in entry["planes"]
            )
        ):
            fail(
                "DELIVERY_GRAPH_NODE_INVALID",
                "Delivery graph node is invalid",
            )
        work_item_id = safe_id(
            entry.get("workItemId"),
            "workItemId",
        )
        if entry["id"] != _expected_node_id(
            entry["kind"],
            work_item_id,
        ):
            fail(
                "DELIVERY_GRAPH_NODE_INVALID",
                "Delivery graph node ID does not match its kind",
            )
        if entry["kind"] in LOOP_NODE_KINDS:
            normalized_loop = validate_loop_descriptor(entry.get("loop"))
            if normalized_loop != entry["loop"]:
                fail(
                    "DELIVERY_GRAPH_NODE_INVALID",
                    "Delivery graph Loop descriptor is not canonical",
                )
        elif entry.get("loop") is not None:
            fail(
                "DELIVERY_GRAPH_NODE_INVALID",
                "Only Loop nodes may carry Loop descriptors",
            )
        node_ids.add(entry["id"])
        nodes.append(entry)
    if (
        not nodes
        or nodes != sorted(nodes, key=lambda item: item["id"])
    ):
        fail(
            "DELIVERY_GRAPH_NODE_INVALID",
            "Delivery graph nodes must use stable ordering",
        )

    edges: list[dict[str, Any]] = []
    edge_ids: set[str] = set()
    semantic_edges: set[tuple[str, str, str]] = set()
    for entry in value["edges"]:
        semantic = (
            entry.get("source"),
            entry.get("target"),
            entry.get("kind"),
        ) if isinstance(entry, dict) else (None, None, None)
        valid_join = (
            isinstance(entry.get("joinGroup"), str)
            and bool(
                GRAPH_IDENTIFIER.fullmatch(entry["joinGroup"])
            )
            if (
                isinstance(entry, dict)
                and entry.get("kind") == "ALL_OF"
            )
            else (
                isinstance(entry, dict)
                and entry.get("joinGroup") is None
            )
        )
        if (
            not isinstance(entry, dict)
            or set(entry) != GRAPH_EDGE_FIELDS
            or not isinstance(entry.get("id"), str)
            or not GRAPH_IDENTIFIER.fullmatch(entry["id"])
            or entry["id"] in edge_ids
            or entry.get("source") not in node_ids
            or entry.get("target") not in node_ids
            or entry["source"] == entry["target"]
            or entry.get("kind") not in GRAPH_EDGE_KINDS
            or entry.get("plane") not in GRAPH_PLANES
            or semantic in semantic_edges
            or not valid_join
        ):
            fail(
                "DELIVERY_GRAPH_EDGE_INVALID",
                "Delivery graph edge is invalid",
            )
        edge_ids.add(entry["id"])
        semantic_edges.add(semantic)
        edges.append(entry)
    if edges != sorted(edges, key=lambda item: item["id"]):
        fail(
            "DELIVERY_GRAPH_EDGE_INVALID",
            "Delivery graph edges must use stable ordering",
        )
    confirmation_id = confirmation_node_id(root_id)
    assurance_profile = graph_assurance_profile(value)
    if assurance_profile == "LIGHT":
        kinds = [node["kind"] for node in nodes]
        if (
            kinds.count("TASK_LOOP") != 1
            or kinds.count("USER_CONFIRMATION") != 1
            or len(nodes) != 2
            or confirmation_id not in node_ids
            or len(edges) != 1
            or edges[0]["target"] != confirmation_id
            or edges[0]["source"] not in node_ids
        ):
            fail(
                "DELIVERY_GRAPH_INVALID",
                "LIGHT Delivery graph must contain one TASK Loop followed "
                "directly by user confirmation",
            )
    elif (
        review_node_id(root_id) not in node_ids
        or confirmation_id not in node_ids
    ):
        fail(
            "DELIVERY_GRAPH_INVALID",
            "STANDARD Delivery graph root review and confirmation nodes "
            "are required",
        )
    _validate_acyclic(nodes, edges)
    return value


def graph_assurance_profile(graph: dict[str, Any]) -> str:
    """Infer the frozen assurance profile from its immutable topology."""

    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if not isinstance(nodes, list):
        return "STANDARD"
    return (
        "STANDARD"
        if any(
            isinstance(node, dict)
            and node.get("kind") in REVIEW_NODE_KINDS
            for node in nodes
        )
        else "LIGHT"
    )


def graph_fingerprint(graph: dict[str, Any]) -> str:
    return fingerprint(validate_delivery_graph(graph))


def graph_summary(graph: dict[str, Any]) -> dict[str, int]:
    normalized = validate_delivery_graph(graph)
    return {
        "nodes": len(normalized["nodes"]),
        "edges": len(normalized["edges"]),
        "taskLoops": sum(
            node["kind"] == "TASK_LOOP"
            for node in normalized["nodes"]
        ),
        "joinNodes": sum(
            node["kind"] in JOIN_NODE_KINDS
            for node in normalized["nodes"]
        ),
        "reviewLoops": sum(
            node["kind"] in REVIEW_NODE_KINDS
            for node in normalized["nodes"]
        ),
        "confirmationNodes": sum(
            node["kind"] == "USER_CONFIRMATION"
            for node in normalized["nodes"]
        ),
        "runtimeTransitions": len(
            normalized["runtime"]["transitions"]
        ),
    }
