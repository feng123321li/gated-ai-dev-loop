from __future__ import annotations

import re
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .jsonio import fingerprint
from .model import safe_id


GRAPH_NODE_KINDS = (
    "TASK_EXECUTION",
    "TASK_GATE",
    "CAPABILITY_GATE",
    "DELIVERY_GATE",
    "ROOT_REVIEW",
    "USER_CONFIRMATION",
)
GRAPH_EDGE_KINDS = ("ON_SUCCESS", "REQUIRES_PASS", "ALL_OF")
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
    "RETRYABLE",
    "REMEDIATION_REQUIRED",
    "CONTRACT_CHANGE",
    "EXTERNAL_AUTHORITY",
    "NON_RETRYABLE",
    "WORKER_LOST",
    "GATE_FAILURE",
)
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_CLAIM_LEASE_SECONDS = 30 * 60
DEFAULT_HEARTBEAT_SECONDS = 5 * 60
GRAPH_FIELDS = {
    "schemaVersion", "rootId", "hierarchyFingerprint", "nodes", "edges", "runtime",
}
GRAPH_NODE_FIELDS = {"id", "kind", "planes", "workItemId"}
GRAPH_EDGE_FIELDS = {"id", "source", "target", "kind", "plane", "joinGroup"}
RUNTIME_TRANSITION_FIELDS = {
    "id", "eventType", "fromStates", "toStates", "routeCondition", "nodeKinds",
    "automatic", "createsAttempt",
}
GRAPH_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9:._-]*$")
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


def execution_node_id(task_id: str) -> str:
    return f"task:{safe_id(task_id)}:execute"


def gate_node_id(work_item_id: str) -> str:
    return f"gate:{safe_id(work_item_id)}"


def review_node_id(root_id: str) -> str:
    return f"review:{safe_id(root_id)}"


def confirmation_node_id(root_id: str) -> str:
    return f"confirm:{safe_id(root_id)}"


def _node(node_id: str, kind: str, work_item_id: str) -> dict[str, Any]:
    planes = (
        ["EXECUTION"]
        if kind == "TASK_EXECUTION"
        else ["GOVERNANCE"]
        if kind in {"ROOT_REVIEW", "USER_CONFIRMATION"}
        else ["EXECUTION", "GOVERNANCE"]
    )
    return {
        "id": node_id,
        "kind": kind,
        "planes": planes,
        "workItemId": work_item_id,
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
        "toStates": sorted([to_states] if isinstance(to_states, str) else to_states),
        "routeCondition": route_condition,
        "nodeKinds": sorted(node_kinds),
        "automatic": automatic,
        "createsAttempt": creates_attempt,
    }


def compile_runtime_policy() -> dict[str, Any]:
    """Return the controller-owned FSM and router policy frozen into every graph."""
    all_kinds = list(GRAPH_NODE_KINDS)
    gate_kinds = [kind for kind in GRAPH_NODE_KINDS if kind.endswith("_GATE")]
    retry_kinds = ["TASK_EXECUTION", *gate_kinds]
    transitions = [
        _transition(
            "GRAPH_RUN_STARTED", ["PENDING"], ["PENDING", "READY"], "ON_START", all_kinds,
            automatic=True,
        ),
        _transition(
            "TASK_CLAIMED", ["READY"], "CLAIMED", "ON_DISPATCH", ["TASK_EXECUTION"],
            automatic=False,
        ),
        _transition(
            "TASK_HEARTBEAT", ["CLAIMED"], "CLAIMED", "ON_HEARTBEAT", ["TASK_EXECUTION"],
            automatic=False,
        ),
        _transition(
            "TASK_IMPLEMENTED", ["CLAIMED"], "SUCCEEDED", "ON_SUCCESS", ["TASK_EXECUTION"],
            automatic=False,
        ),
        _transition(
            "TASK_BLOCKED", ["CLAIMED"], "BLOCKED", "ON_FAILURE", ["TASK_EXECUTION"],
            automatic=False,
        ),
        _transition(
            "CLAIM_LEASE_EXPIRED", ["CLAIMED"], "BLOCKED", "ON_LEASE_EXPIRED",
            ["TASK_EXECUTION"], automatic=True,
        ),
        _transition(
            "NODE_PAUSED", ["CLAIMED"], "PAUSED", "ON_PAUSE", ["TASK_EXECUTION"],
            automatic=False,
        ),
        _transition(
            "NODE_RESUMED", ["PAUSED"], ["PENDING", "READY"], "ON_RESUME", ["TASK_EXECUTION"],
            automatic=False,
        ),
        _transition(
            "GATE_PASSED", ["READY"], "SUCCEEDED", "ON_PASS", gate_kinds,
            automatic=False,
        ),
        _transition(
            "GATE_FAILED", ["READY"], "BLOCKED", "ON_FAILURE", gate_kinds,
            automatic=False,
        ),
        _transition(
            "REVIEW_PASSED", ["READY"], "SUCCEEDED", "ON_PASS", ["ROOT_REVIEW"],
            automatic=False,
        ),
        _transition(
            "USER_CONFIRMED", ["READY"], "COMPLETED", "ON_CONFIRMATION",
            ["USER_CONFIRMATION"], automatic=False,
        ),
        _transition(
            "NODE_RETRY_SCHEDULED", ["BLOCKED"], ["PENDING", "READY"], "ON_RETRY_ALLOWED",
            retry_kinds, automatic=True, creates_attempt=True,
        ),
        _transition(
            "GRAPH_INVALIDATED", ["BLOCKED", "COMPLETED", "SUCCEEDED"], ["PENDING", "READY"],
            "ON_REMEDIATION", all_kinds, automatic=False, creates_attempt=True,
        ),
        _transition(
            "RETRY_EXHAUSTED", ["BLOCKED"], "BLOCKED", "ON_RETRY_EXHAUSTED",
            retry_kinds, automatic=True,
        ),
        _transition(
            "GRAPH_RUN_CANCELLED", ["BLOCKED", "CLAIMED", "PAUSED", "PENDING", "READY"],
            "CANCELLED", "ON_CANCEL", all_kinds, automatic=False,
        ),
    ]
    return {
        "states": list(RUNTIME_STATES),
        "terminalStates": list(RUNTIME_TERMINAL_STATES),
        "retryPolicy": {
            "maxAttempts": DEFAULT_MAX_ATTEMPTS,
            "automaticFailureClasses": ["RETRYABLE", "WORKER_LOST"],
            "onExhausted": "BLOCK_RUN",
        },
        "claimPolicy": {
            "leaseSeconds": DEFAULT_CLAIM_LEASE_SECONDS,
            "heartbeatSeconds": DEFAULT_HEARTBEAT_SECONDS,
            "onExpired": "RETRY_NODE",
        },
        "transitions": sorted(transitions, key=lambda item: item["id"]),
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

    def visit(node: dict[str, Any]) -> None:
        result.append(node)
        for child in node["children"]:
            visit(child)

    visit(hierarchy["root"])
    return result


def compile_delivery_graph(
    hierarchy: dict[str, Any],
    *,
    hierarchy_fingerprint: str,
) -> dict[str, Any]:
    """Compile one validated Layered Delivery hierarchy into a deterministic graph."""
    if not FINGERPRINT.fullmatch(hierarchy_fingerprint):
        fail("DELIVERY_GRAPH_FINGERPRINT_INVALID", "Hierarchy fingerprint is invalid")
    root_id = hierarchy["root"]["definition"]["id"]
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    hierarchy_nodes = _walk_hierarchy(hierarchy)
    by_id = {node["definition"]["id"]: node for node in hierarchy_nodes}

    for hierarchy_node in hierarchy_nodes:
        definition = hierarchy_node["definition"]
        item_id = definition["id"]
        kind = definition["kind"]
        if kind == "TASK":
            nodes.append(_node(execution_node_id(item_id), "TASK_EXECUTION", item_id))
            nodes.append(_node(gate_node_id(item_id), "TASK_GATE", item_id))
            edges.append(
                _edge(
                    execution_node_id(item_id),
                    gate_node_id(item_id),
                    "ON_SUCCESS",
                    plane="EXECUTION",
                )
            )
            for dependency_id in definition["execution"]["dependsOn"]:
                edges.append(
                    _edge(
                        gate_node_id(dependency_id),
                        execution_node_id(item_id),
                        "REQUIRES_PASS",
                        plane="EXECUTION",
                    )
                )
        elif kind == "CAPABILITY":
            nodes.append(_node(gate_node_id(item_id), "CAPABILITY_GATE", item_id))
        else:
            nodes.append(_node(gate_node_id(item_id), "DELIVERY_GATE", item_id))

        if kind != "TASK":
            join_group = f"join:{item_id}:children"
            for child in hierarchy_node["children"]:
                edges.append(
                    _edge(
                        gate_node_id(child["definition"]["id"]),
                        gate_node_id(item_id),
                        "ALL_OF",
                        plane="EXECUTION",
                        join_group=join_group,
                    )
                )

    for hierarchy_node in hierarchy_nodes:
        definition = hierarchy_node["definition"]
        if definition["kind"] != "CAPABILITY":
            continue
        for dependency_id in definition["decomposition"]["dependsOn"]:
            if dependency_id not in by_id:
                fail("DELIVERY_GRAPH_DEPENDENCY_INVALID", "Capability dependency is missing from the graph")
            for child in hierarchy_node["children"]:
                edges.append(
                    _edge(
                        gate_node_id(dependency_id),
                        execution_node_id(child["definition"]["id"]),
                        "REQUIRES_PASS",
                        plane="EXECUTION",
                    )
                )

    nodes.extend(
        [
            _node(review_node_id(root_id), "ROOT_REVIEW", root_id),
            _node(confirmation_node_id(root_id), "USER_CONFIRMATION", root_id),
        ]
    )
    edges.extend(
        [
            _edge(
                gate_node_id(root_id),
                review_node_id(root_id),
                "REQUIRES_PASS",
                plane="GOVERNANCE",
            ),
            _edge(
                review_node_id(root_id),
                confirmation_node_id(root_id),
                "REQUIRES_PASS",
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


def _validate_acyclic(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    outgoing: dict[str, list[str]] = {node["id"]: [] for node in nodes}
    indegree = {node["id"]: 0 for node in nodes}
    for edge in edges:
        outgoing[edge["source"]].append(edge["target"])
        indegree[edge["target"]] += 1
    ready = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
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
        fail("DELIVERY_GRAPH_CYCLE", "Delivery graph must be acyclic")


def validate_delivery_graph(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != GRAPH_FIELDS:
        fail("DELIVERY_GRAPH_INVALID", "Delivery graph fields are invalid")
    if value.get("schemaVersion") != SCHEMA_VERSION:
        fail("DELIVERY_GRAPH_INVALID", "Delivery graph schemaVersion is invalid")
    root_id = safe_id(value.get("rootId"), "rootId")
    if not FINGERPRINT.fullmatch(str(value.get("hierarchyFingerprint", ""))):
        fail("DELIVERY_GRAPH_FINGERPRINT_INVALID", "Delivery graph hierarchy fingerprint is invalid")
    if not isinstance(value.get("nodes"), list) or not isinstance(value.get("edges"), list):
        fail("DELIVERY_GRAPH_INVALID", "Delivery graph nodes and edges must be arrays")
    if value.get("runtime") != compile_runtime_policy():
        fail("DELIVERY_GRAPH_RUNTIME_INVALID", "Delivery graph runtime FSM or router policy is invalid")

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
            or any(plane not in GRAPH_PLANES for plane in entry["planes"])
        ):
            fail("DELIVERY_GRAPH_NODE_INVALID", "Delivery graph node is invalid")
        work_item_id = safe_id(entry.get("workItemId"), "workItemId")
        expected_id = (
            execution_node_id(work_item_id)
            if entry["kind"] == "TASK_EXECUTION"
            else review_node_id(work_item_id)
            if entry["kind"] == "ROOT_REVIEW"
            else confirmation_node_id(work_item_id)
            if entry["kind"] == "USER_CONFIRMATION"
            else gate_node_id(work_item_id)
        )
        if entry["id"] != expected_id:
            fail("DELIVERY_GRAPH_NODE_INVALID", "Delivery graph node ID does not match its kind")
        node_ids.add(entry["id"])
        nodes.append(entry)
    if not nodes or nodes != sorted(nodes, key=lambda item: item["id"]):
        fail("DELIVERY_GRAPH_NODE_INVALID", "Delivery graph nodes must use stable ordering")

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
            and bool(GRAPH_IDENTIFIER.fullmatch(entry["joinGroup"]))
            if isinstance(entry, dict) and entry.get("kind") == "ALL_OF"
            else isinstance(entry, dict) and entry.get("joinGroup") is None
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
            fail("DELIVERY_GRAPH_EDGE_INVALID", "Delivery graph edge is invalid")
        edge_ids.add(entry["id"])
        semantic_edges.add(semantic)
        edges.append(entry)
    if edges != sorted(edges, key=lambda item: item["id"]):
        fail("DELIVERY_GRAPH_EDGE_INVALID", "Delivery graph edges must use stable ordering")
    if review_node_id(root_id) not in node_ids or confirmation_node_id(root_id) not in node_ids:
        fail("DELIVERY_GRAPH_INVALID", "Delivery graph root review and confirmation nodes are required")
    _validate_acyclic(nodes, edges)
    return value


def graph_fingerprint(graph: dict[str, Any]) -> str:
    return fingerprint(validate_delivery_graph(graph))


def graph_summary(graph: dict[str, Any]) -> dict[str, int]:
    normalized = validate_delivery_graph(graph)
    return {
        "nodes": len(normalized["nodes"]),
        "edges": len(normalized["edges"]),
        "taskExecutions": sum(node["kind"] == "TASK_EXECUTION" for node in normalized["nodes"]),
        "gateNodes": sum(node["kind"].endswith("_GATE") for node in normalized["nodes"]),
        "reviewNodes": sum(node["kind"] in {"ROOT_REVIEW", "USER_CONFIRMATION"} for node in normalized["nodes"]),
        "runtimeTransitions": len(normalized["runtime"]["transitions"]),
    }
