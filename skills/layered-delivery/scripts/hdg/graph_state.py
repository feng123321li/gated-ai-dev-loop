from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .graph_model import runtime_transition
from .jsonio import fingerprint

SUCCESS_STATES = {"SUCCEEDED", "COMPLETED"}
TERMINAL_STATES = SUCCESS_STATES | {"BLOCKED", "CANCELLED"}

def materialized_graph_states(
    graph: dict[str, Any],
    run: dict[str, Any],
    registry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the validated latest node snapshots maintained by SQLite."""
    expected = {node["id"] for node in graph["nodes"]}
    snapshots = {
        node["nodeId"]: node
        for node in run["nodes"]
    }
    if set(snapshots) != expected:
        fail(
            "DELIVERY_GRAPH_RUN_INVALID",
            "Materialized graph snapshots do not match the frozen graph",
            missingNodes=sorted(expected - set(snapshots)),
            unknownNodes=sorted(set(snapshots) - expected),
        )
    graph_nodes = {node["id"]: node for node in graph["nodes"]}
    derived = {
        node["id"]: node
        for node in derive_node_states(graph, registry)
    }
    by_item = {item["id"]: item for item in registry["workItems"]}
    states = [
        {
            **derived[node_id],
            **graph_nodes[node_id],
            **{
                key: value
                for key, value in snapshots[node_id].items()
                if key != "nodeId"
            },
        }
        for node_id in sorted(expected)
    ]
    for state in states:
        entry = by_item[state["workItemId"]]
        if state["kind"].endswith("_GATE"):
            artifact = entry.get("gate", {}).get("artifact") or {}
        elif state["kind"] == "ROOT_REVIEW":
            artifact = (
                (entry.get("acceptance") or {}).get("review") or {}
            ).get("artifact") or {}
        else:
            artifact = {}
        blocked_skill_usage = [
            dict(usage)
            for usage in artifact.get("skillUsage", [])
            if usage.get("status") == "BLOCKED"
        ]
        if blocked_skill_usage:
            state["blockedSkillUsage"] = blocked_skill_usage
    return states


def retry_budget(graph: dict[str, Any], attempt: int) -> dict[str, int | bool]:
    maximum = graph["runtime"]["retryPolicy"]["maxAttempts"]
    remaining = max(0, maximum - attempt)
    return {
        "attempt": attempt,
        "maxAttempts": maximum,
        "remainingAttempts": remaining,
        "retryExhausted": remaining == 0,
    }


def failure_routing_decision(
    graph: dict[str, Any],
    *,
    attempt: int,
    failure_class: str,
) -> dict[str, Any]:
    budget = retry_budget(graph, attempt)
    automatic = failure_class in graph["runtime"]["retryPolicy"]["automaticFailureClasses"]
    if automatic and not budget["retryExhausted"]:
        action = "RETRY_NODE"
    elif automatic:
        action = graph["runtime"]["retryPolicy"]["onExhausted"]
    elif failure_class == "REMEDIATION_REQUIRED":
        action = "SUBMIT_REMEDIATION"
    elif failure_class == "CONTRACT_CHANGE":
        action = "REQUEST_REVIEW"
    elif failure_class == "EXTERNAL_AUTHORITY":
        action = "REQUEST_USER_AUTHORITY"
    else:
        action = "REQUEST_INTERVENTION"
    return {
        "failureClass": failure_class,
        "routeCondition": (
            "ON_RETRY_EXHAUSTED"
            if automatic and budget["retryExhausted"]
            else "ON_FAILURE"
        ),
        "action": action,
        "automatic": automatic,
        **budget,
        "nextAttempt": attempt + 1 if action == "RETRY_NODE" else None,
    }


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
        if acceptance.get("status") == "REVIEW_BLOCKED":
            return "BLOCKED", [
                "required-final-review-skill-unavailable",
            ]
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
        "latestEvidenceHash", "leaseExpiresAt", "lastHeartbeatAt", "failureClass",
        "lastTransition", "retryExhausted",
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
    graph: dict[str, Any],
    attempts: object,
    current: dict[str, dict[str, Any]],
    histories: dict[str, list[dict[str, Any]]],
    *,
    event_type: str,
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
        runtime_transition(
            graph,
            event_type=event_type,
            node_kind=previous["kind"],
            from_state=previous["status"],
        )
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
            "leaseExpiresAt": None,
            "lastHeartbeatAt": None,
            "failureClass": None,
            "lastTransition": event_type,
            "retryExhausted": False,
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
            "leaseExpiresAt": None,
            "lastHeartbeatAt": None,
            "failureClass": None,
            "lastTransition": None,
            "retryExhausted": False,
            "recordRevision": 1,
            "blockedBy": ["graph-run-not-started"],
            "readyBecause": [],
        }
        current[node["id"]] = initial
        histories[node["id"]] = [initial]

    started = False
    completed_at: str | None = None
    cancelled_at: str | None = None
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
            prior_revisions = {
                node_id: state["recordRevision"] for node_id, state in current.items()
            }
            for state in current.values():
                runtime_transition(
                    graph,
                    event_type=event_type,
                    node_kind=state["kind"],
                    from_state=state["status"],
                )
                state["lastTransition"] = event_type
            _refresh_replay_readiness(graph, current, started=True)
            for node_id, state in current.items():
                if state["recordRevision"] == prior_revisions[node_id]:
                    state["recordRevision"] += 1
            continue
        if not started:
            fail("DELIVERY_GRAPH_REPLAY_INVALID", "Graph event occurred before the run started")
        if event_type == "GRAPH_RUN_CANCELLED":
            if node_id is not None or event["attempt"] is not None or cancelled_at is not None:
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Graph cancellation event is invalid")
            for state in current.values():
                if state["status"] in SUCCESS_STATES or state["status"] == "CANCELLED":
                    continue
                runtime_transition(
                    graph,
                    event_type=event_type,
                    node_kind=state["kind"],
                    from_state=state["status"],
                )
                _set_replay_node(
                    state,
                    status="CANCELLED",
                    owner=None,
                    operationId=None,
                    finishedAt=event["recordedAt"],
                    lastTransition=event_type,
                    blockedBy=["graph-run-cancelled"],
                    readyBecause=[],
                )
            cancelled_at = event["recordedAt"]
            continue
        if cancelled_at is not None:
            fail("DELIVERY_GRAPH_REPLAY_INVALID", "Graph event occurred after cancellation")
        if node_id not in current:
            fail("DELIVERY_GRAPH_REPLAY_INVALID", "Graph event references an unknown node")

        if event_type in {"NODE_RETRY_SCHEDULED", "GRAPH_INVALIDATED"}:
            if event_type == "GRAPH_INVALIDATED" and not isinstance(
                payload.get("evidenceBinding"), dict
            ):
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Graph invalidation evidence is not bound")
            _new_replay_attempts(
                graph,
                payload.get("attempts"),
                current,
                histories,
                event_type=event_type,
                recorded_at=event["recordedAt"],
            )
            if current[node_id]["attempt"] != event["attempt"]:
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Retry event attempt is inconsistent")
            _refresh_replay_readiness(graph, current, started=True)
            continue

        node = current[node_id]
        if event["attempt"] != node["attempt"]:
            fail("DELIVERY_GRAPH_REPLAY_INVALID", "Graph event attempt is not current")
        if event_type in {
            "SKILL_ACTIVATED",
            "SKILL_CONFORMANCE_RECORDED",
        }:
            from .skill_execution import is_skill_lifecycle_event_valid

            if not is_skill_lifecycle_event_valid(event):
                fail(
                    "DELIVERY_GRAPH_REPLAY_INVALID",
                    "Required Skill lifecycle event is invalid",
                )
            continue
        binding = payload.get("evidenceBinding")
        evidence_hash = (
            binding.get("boundEvidenceSha256")
            if isinstance(binding, dict)
            else None
        )
        runtime_transition(
            graph,
            event_type=event_type,
            node_kind=node["kind"],
            from_state=node["status"],
        )
        if event_type == "TASK_CLAIMED":
            if node["kind"] != "TASK_EXECUTION" or node["status"] != "READY":
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Task claim transition is invalid")
            owner = payload.get("owner")
            lease_expires_at = payload.get("leaseExpiresAt")
            last_heartbeat_at = payload.get("lastHeartbeatAt")
            if (
                not isinstance(owner, str)
                or not owner
                or not event.get("operationId")
                or not isinstance(lease_expires_at, str)
                or not isinstance(last_heartbeat_at, str)
            ):
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Task claim identity is invalid")
            _set_replay_node(
                node,
                status="CLAIMED",
                owner=owner,
                operationId=event["operationId"],
                claimedAt=event["recordedAt"],
                leaseExpiresAt=lease_expires_at,
                lastHeartbeatAt=last_heartbeat_at,
                failureClass=None,
                lastTransition=event_type,
                retryExhausted=False,
                blockedBy=[],
                readyBecause=[],
            )
        elif event_type == "TASK_HEARTBEAT":
            if (
                node["kind"] != "TASK_EXECUTION"
                or node["operationId"] != event.get("operationId")
                or not isinstance(payload.get("leaseExpiresAt"), str)
                or payload.get("lastHeartbeatAt") != event["recordedAt"]
            ):
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Task heartbeat transition is invalid")
            _set_replay_node(
                node,
                leaseExpiresAt=payload["leaseExpiresAt"],
                lastHeartbeatAt=payload["lastHeartbeatAt"],
                lastTransition=event_type,
            )
        elif event_type == "NODE_PAUSED":
            if node["kind"] != "TASK_EXECUTION" or node["operationId"] != event.get("operationId"):
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Task pause transition is invalid")
            _set_replay_node(
                node,
                status="PAUSED",
                owner=None,
                operationId=None,
                lastTransition=event_type,
                blockedBy=["explicitly-paused"],
                readyBecause=[],
            )
        elif event_type == "NODE_RESUMED":
            if node["kind"] != "TASK_EXECUTION" or event.get("operationId") is not None:
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Task resume transition is invalid")
            incoming = [
                edge for edge in graph["edges"] if edge["target"] == node_id
            ]
            unmet = [
                edge["source"]
                for edge in incoming
                if current[edge["source"]]["status"] not in SUCCESS_STATES
            ]
            _set_replay_node(
                node,
                status="PENDING" if unmet else "READY",
                lastTransition=event_type,
                blockedBy=unmet,
                readyBecause=(
                    [] if unmet else [f"passed:{edge['source']}" for edge in sorted(incoming, key=lambda item: item["id"])]
                    or ["no-unmet-predecessors"]
                ),
            )
        elif event_type == "CLAIM_LEASE_EXPIRED":
            if node["kind"] != "TASK_EXECUTION" or node["operationId"] != event.get("operationId"):
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Claim lease expiration transition is invalid")
            _set_replay_node(
                node,
                status="BLOCKED",
                owner=None,
                operationId=None,
                finishedAt=event["recordedAt"],
                failureClass="WORKER_LOST",
                lastTransition=event_type,
                blockedBy=["claim-lease-expired"],
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
            failure_class = None
            if event_type == "TASK_BLOCKED":
                failure = payload.get("failure")
                if not isinstance(failure, dict) or not isinstance(failure.get("class"), str):
                    fail("DELIVERY_GRAPH_REPLAY_INVALID", "Task failure classification is invalid")
                failure_class = failure["class"]
            _set_replay_node(
                node,
                status="SUCCEEDED" if event_type == "TASK_IMPLEMENTED" else "BLOCKED",
                owner=None,
                operationId=None,
                finishedAt=event["recordedAt"],
                latestEvidenceHash=evidence_hash,
                failureClass=failure_class,
                lastTransition=event_type,
                blockedBy=[] if event_type == "TASK_IMPLEMENTED" else ["task-execution-blocked"],
                readyBecause=[],
            )
        elif event_type in {"GATE_PASSED", "GATE_FAILED"}:
            if not node["kind"].endswith("_GATE") or node["status"] != "READY" or evidence_hash is None:
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Gate transition is invalid")
            gate_passed = event_type == "GATE_PASSED"
            failure_class = None
            blocked_skill_usage: list[dict[str, str]] = []
            if not gate_passed:
                failure_class = payload.get("failureClass")
                raw_blocked_skill_usage = payload.get("blockedSkillUsage")
                if failure_class not in {"GATE_FAILURE", "NON_RETRYABLE"}:
                    fail(
                        "DELIVERY_GRAPH_REPLAY_INVALID",
                        "Gate failure classification is invalid",
                    )
                if failure_class == "NON_RETRYABLE":
                    if (
                        not isinstance(raw_blocked_skill_usage, list)
                        or not raw_blocked_skill_usage
                        or any(
                            not isinstance(usage, dict)
                            or set(usage)
                            != {"name", "stage", "status", "evidence"}
                            or usage.get("status") != "BLOCKED"
                            or not all(
                                isinstance(usage.get(field), str)
                                and bool(usage[field].strip())
                                for field in ("name", "stage", "evidence")
                            )
                            for usage in raw_blocked_skill_usage
                        )
                    ):
                        fail(
                            "DELIVERY_GRAPH_REPLAY_INVALID",
                            "Non-retryable gate failure must preserve blocked Skill usage",
                        )
                    blocked_skill_usage = [
                        dict(usage) for usage in raw_blocked_skill_usage
                    ]
                elif raw_blocked_skill_usage is not None:
                    fail(
                        "DELIVERY_GRAPH_REPLAY_INVALID",
                        "Retryable gate failure cannot contain blocked Skill usage",
                    )
            changes: dict[str, Any] = {
                "status": "SUCCEEDED" if gate_passed else "BLOCKED",
                "finishedAt": event["recordedAt"],
                "latestEvidenceHash": evidence_hash,
                "failureClass": failure_class,
                "lastTransition": event_type,
                "blockedBy": (
                    []
                    if gate_passed
                    else [
                        f"required-skill-blocked:{usage['name']}"
                        for usage in blocked_skill_usage
                    ]
                    or ["gate-failed"]
                ),
                "readyBecause": [],
            }
            if blocked_skill_usage:
                changes["blockedSkillUsage"] = blocked_skill_usage
            _set_replay_node(node, **changes)
        elif event_type == "REVIEW_PASSED":
            if node["kind"] != "ROOT_REVIEW" or node["status"] != "READY" or evidence_hash is None:
                fail("DELIVERY_GRAPH_REPLAY_INVALID", "Review transition is invalid")
            _set_replay_node(
                node,
                status="SUCCEEDED",
                finishedAt=event["recordedAt"],
                latestEvidenceHash=evidence_hash,
                lastTransition=event_type,
                blockedBy=[],
                readyBecause=[],
            )
        elif event_type == "REVIEW_BLOCKED":
            if (
                node["kind"] != "ROOT_REVIEW"
                or node["status"] != "READY"
                or evidence_hash is None
            ):
                fail(
                    "DELIVERY_GRAPH_REPLAY_INVALID",
                    "Blocked review transition is invalid",
                )
            _set_replay_node(
                node,
                status="BLOCKED",
                finishedAt=event["recordedAt"],
                latestEvidenceHash=evidence_hash,
                failureClass="EXTERNAL_AUTHORITY",
                lastTransition=event_type,
                blockedBy=[
                    "required-final-review-skill-unavailable",
                ],
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
                lastTransition=event_type,
                blockedBy=[],
                readyBecause=[],
            )
        elif event_type == "RETRY_EXHAUSTED":
            _set_replay_node(
                node,
                retryExhausted=True,
                lastTransition=event_type,
                blockedBy=["retry-exhausted"],
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
        "CANCELLED"
        if cancelled_at is not None
        else "COMPLETED"
        if any(node["kind"] == "USER_CONFIRMATION" and node["status"] == "COMPLETED" for node in nodes)
        else "PAUSED"
        if any(node["status"] == "PAUSED" for node in nodes)
        and not any(node["status"] in {"READY", "CLAIMED"} for node in nodes)
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
        "cancelledAt": cancelled_at,
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
        "leaseExpiresAt", "lastHeartbeatAt", "failureClass", "lastTransition",
        "retryExhausted", "recordRevision",
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
            "completedAt", "cancelledAt",
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
        unfinished = states[node_id]["status"] not in SUCCESS_STATES | {"CANCELLED"}
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
        "paused": any(states[node_id]["status"] == "PAUSED" for node_id in path),
    }


def _runtime_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _runtime_timestamp_after(value: str, seconds: int) -> str:
    return (
        (_runtime_time(value) + timedelta(seconds=seconds))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
