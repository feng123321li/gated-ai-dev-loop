from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .evidence import (
    confirmation_evidence_contract,
    gate_evidence_contract,
    review_evidence_contract,
    task_result_evidence_contract,
    validation_remediation_evidence_contract,
)
from .graph_model import graph_summary, runtime_transition
from .jsonio import fingerprint
from .model import required_skill_policy, scope_patterns_overlap


SUCCESS_STATES = {"SUCCEEDED", "COMPLETED"}
TERMINAL_STATES = SUCCESS_STATES | {"BLOCKED", "CANCELLED"}


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


def _task_write_scope(repository: Any, definition: dict[str, Any]) -> list[str]:
    scope = list(definition["scope"])
    scope.extend(
        item["path"]
        for item in repository.effective_task_file_changes(definition)
    )
    return sorted(set(scope))


def mcp_call(tool: str, **arguments: Any) -> dict[str, Any]:
    """Describe one host MCP invocation without exposing a shell command."""
    return {
        "tool": tool,
        "arguments": arguments,
    }


def evidence_contract_ref(work_item_id: str, contract_kind: str) -> dict[str, Any]:
    artifact_kinds = {
        "result": "TASK_RESULT",
        "gate": "WORK_ITEM_GATE",
        "remediation": "VALIDATION_REMEDIATION",
        "review": "ROOT_REVIEW",
        "confirmation": "USER_CONFIRMATION",
    }
    return {
        "artifactKind": artifact_kinds[contract_kind],
        "mcpCall": mcp_call(
            "evidence_contract",
            item_id=work_item_id,
            contract_kind=contract_kind,
        ),
    }


def _remediation_contract(
    repository: Any,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    definition = repository.assert_current_lineage(registry, entry)[0]
    return validation_remediation_evidence_contract(
        entry,
        definition,
        authorized_file_changes=repository.effective_task_file_changes(definition),
    )


def _result_contract(
    repository: Any,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    if entry["kind"] != "TASK":
        fail(
            "WORK_ITEM_RESULT_CONTRACT_TASK_REQUIRED",
            "Task result evidence contracts require a Task",
        )
    if entry["status"] != "CLAIMED" or not entry.get("claim"):
        fail(
            "WORK_ITEM_RESULT_CONTRACT_NOT_READY",
            "Task result evidence contracts require an active claim",
            mcpCall=mcp_call(
                "dispatch_task",
                item_id=entry["id"],
                owner="<owner>",
                operation_id="<operation-id>",
            ),
        )
    definition = repository.assert_current_lineage(registry, entry)[0]
    return task_result_evidence_contract(
        entry,
        definition,
        authorized_file_changes=repository.effective_task_file_changes(
            definition
        ),
        required_skills=repository.effective_required_skills(
            registry,
            entry,
            stage="DEVELOPMENT",
        ),
    )


def _gate_contract(
    repository: Any,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    definition = repository.assert_current_lineage(registry, entry)[0]
    additional_planned_files: set[str] = set()
    if entry["kind"] == "TASK":
        frozen_files = {
            item["path"]
            for item in definition["developmentPlan"].get("fileChanges", [])
        }
        effective_files = {
            item["path"]
            for item in repository.effective_task_file_changes(definition)
        }
        additional_planned_files = effective_files - frozen_files
    return gate_evidence_contract(
        entry,
        definition,
        additional_planned_files=additional_planned_files,
        required_skills=repository.effective_required_skills(
            registry,
            entry,
            stage="GATE",
        ),
    )


def _root_for_requested_item(
    registry: dict[str, Any],
    work_item_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    by_id = {item["id"]: item for item in registry["workItems"]}
    entry = by_id.get(work_item_id)
    if entry is None:
        fail("WORK_ITEM_NOT_FOUND", f"Unknown work item: {work_item_id}", id=work_item_id)
    return entry, hierarchy_root_entry(registry, entry)


def _load_graph_view(
    *,
    root: str,
    work_item_id: str,
    now: object = None,
) -> tuple[
    Any,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any] | None,
]:
    from .repository import GovernanceRepository

    repository = GovernanceRepository(root, now=now)
    registry = repository.read_operational_registry()
    requested, root_entry = _root_for_requested_item(registry, work_item_id)
    stored = repository.read_graph_definition(root_entry["id"])
    run = repository.read_graph_run(root_entry["id"], allow_missing=True)
    return repository, registry, requested, stored, run


def get_evidence_contract(
    *,
    root: str,
    work_item_id: str,
    contract_kind: str,
) -> dict[str, Any]:
    """Read the current compact evidence contract from SQLite on demand."""
    from .repository import GovernanceRepository

    if contract_kind not in {
        "result", "gate", "remediation", "review", "confirmation",
    }:
        fail(
            "WORK_ITEM_EVIDENCE_CONTRACT_KIND_INVALID",
            "Evidence contract kind must be result, gate, remediation, review, or confirmation",
        )
    repository = GovernanceRepository(root)
    registry = repository.read_operational_registry()
    entry = repository.item_by_id(registry, work_item_id)
    if contract_kind == "result":
        contract = _result_contract(repository, registry, entry)
        operation_id = entry["claim"]["operationId"]
        submit_mcp_calls = [
            mcp_call(
                "task_result",
                item_id=work_item_id,
                operation_id=operation_id,
                status="<IMPLEMENTED_OR_BLOCKED>",
                evidence="<evidence>",
            )
        ]
    elif contract_kind == "gate":
        contract = _gate_contract(repository, registry, entry)
        submit_mcp_calls = [
            mcp_call(
                "accept_item",
                item_id=work_item_id,
                evidence="<evidence>",
            )
        ]
    elif contract_kind == "remediation":
        if entry["kind"] != "TASK":
            fail(
                "WORK_ITEM_REMEDIATION_TASK_REQUIRED",
                "Validation remediation evidence contracts require a frozen Task",
            )
        contract = _remediation_contract(repository, registry, entry)
        submit_mcp_calls = [
            mcp_call(
                "remediate_task",
                item_id=work_item_id,
                expected_baseline_fingerprint=entry["baselineFingerprint"],
                evidence="<evidence>",
            )
        ]
    else:
        if entry["parentId"] is not None:
            fail(
                "WORK_ITEM_ACCEPTANCE_ROOT_REQUIRED",
                "Review and confirmation evidence contracts require a root work item",
            )
        if contract_kind == "review":
            contract = review_evidence_contract(
                repository.effective_required_skills(
                    registry,
                    entry,
                    stage="FINAL_REVIEW",
                )
            )
            review_tools = {
                "INDEPENDENT_REVIEW_PASS": "record_independent_review_pass",
                "REVIEW_BLOCKED": "record_independent_review_blocked",
                "HUMAN_REVIEW_ACCEPTED": "record_human_review_acceptance",
            }
            submit_mcp_calls = [
                mcp_call(
                    review_tools[action],
                    item_id=work_item_id,
                    evidence=f"<{action.lower()}-evidence>",
                )
                for action in contract["actionOptions"]
            ]
        else:
            contract = confirmation_evidence_contract()
            submit_mcp_calls = [
                mcp_call(
                    "record_user_confirmation",
                    item_id=work_item_id,
                    evidence="<evidence>",
                )
            ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": "governance.sqlite3",
        "itemId": work_item_id,
        "contractKind": contract_kind,
        "submitMcpCalls": submit_mcp_calls,
        "evidenceContract": contract,
    }


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
                "leaseExpiresAt": None,
                "lastHeartbeatAt": None,
                "failureClass": None,
                "lastTransition": None,
                "retryExhausted": False,
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
                "runId", "status", "startedAt", "updatedAt", "completedAt", "cancelledAt",
                "recordRevision",
            )
        },
        "nodes": nodes,
        "edges": graph["edges"],
        "runtime": graph["runtime"],
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
    *,
    at: str | None = None,
) -> dict[str, Any]:
    from .repository import timestamp

    graph = stored["graph"]
    at = at or timestamp(repository.now)
    if run is None:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "rootId": graph["rootId"],
            "requestedItemId": requested["id"],
            "runId": None,
            "graphFingerprint": stored["graphFingerprint"],
            "dispatchPlan": {
                "authority": "GRAPH_CONTROLLER",
                "strategy": "AUTO_DISPATCH_ALL_SAFE",
                "parallelGroup": None,
                "dispatchTaskIds": [],
                "desiredNewAgentCount": 0,
                "activeAgentCount": 0,
                "desiredTotalAgentCount": 0,
                "hostSelectionAllowed": False,
                "capacityPolicy": "QUEUE_REMAINDER_STABLE",
                "claimPolicy": "JUST_IN_TIME_ON_WORKER_START",
                "queuedTasksRemainUnclaimed": True,
                "recalculateAfterEveryTransition": True,
            },
            "actions": [],
            "inFlight": [],
            "nextWakeAt": None,
            "blocked": [{
                "nodeId": None,
                "nodeKind": None,
                "workItemId": requested["id"],
                "attempt": None,
                "status": "PENDING",
                "blockedBy": ["requirement-not-frozen"],
                "failureClass": None,
                "remainingAttempts": None,
                "retryExhausted": False,
                "recommendedAction": "FREEZE_REQUIREMENT",
            }],
            "criticalPath": critical_path(graph, states),
            "summary": {
                "actionable": 0,
                "blocked": 1,
                "claimed": 0,
                "inFlight": 0,
            },
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
    in_flight: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    wake_times: list[str] = []
    path = critical_path(graph, states)
    critical_nodes = set(path["nodeIds"])
    for state in requested_states:
        budget = retry_budget(graph, state["attempt"])
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
                    "failureClass": state.get("failureClass"),
                    "remainingAttempts": budget["remainingAttempts"],
                    "retryExhausted": state.get("retryExhausted", False),
                    "recommendedAction": "REQUEST_INTERVENTION",
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
                    "failureClass": state.get("failureClass"),
                    "remainingAttempts": budget["remainingAttempts"],
                    "retryExhausted": state.get("retryExhausted", False),
                    "recommendedAction": "WAIT_FOR_SCOPE",
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
                "autoDispatch": True,
                "dispatchOrdinal": len(selected_scopes),
                "readyBecause": state["readyBecause"] + ["scope-available"],
                "critical": state["id"] in critical_nodes,
                "mcpCall": mcp_call(
                    "dispatch_task",
                    item_id=state["workItemId"],
                    owner="<owner>",
                    operation_id="<operation-id>",
                ),
                "transition": "TASK_CLAIMED",
                "routeCondition": "ON_DISPATCH",
                "requiredSkills": repository.effective_required_skills(
                    registry,
                    entry,
                    stage="DEVELOPMENT",
                ),
                "requiredSkillPolicy": required_skill_policy(),
                **budget,
            })
        elif state["status"] == "READY":
            action = (
                "RUN_GATE"
                if state["kind"].endswith("_GATE")
                else "REQUEST_REVIEW"
                if state["kind"] == "ROOT_REVIEW"
                else "REQUEST_USER_CONFIRMATION"
            )
            action_record = {
                "nodeId": state["id"],
                "nodeKind": state["kind"],
                "action": action,
                "workItemId": state["workItemId"],
                "attempt": state["attempt"],
                "operationId": state.get("operationId"),
                "parallelGroup": None,
                "readyBecause": state["readyBecause"],
                "critical": state["id"] in critical_nodes,
                "transition": (
                    "GATE_PASSED"
                    if action == "RUN_GATE"
                    else "REVIEW_PASSED"
                    if action == "REQUEST_REVIEW"
                    else "USER_CONFIRMED"
                ),
                "routeCondition": "ON_PASS" if action != "REQUEST_USER_CONFIRMATION" else "ON_CONFIRMATION",
                **budget,
            }
            if action == "RUN_GATE":
                action_record["mcpCall"] = mcp_call(
                    "accept_item",
                    item_id=state["workItemId"],
                    evidence="<evidence>",
                )
                action_record["requiredSkills"] = (
                    repository.effective_required_skills(
                        registry,
                        by_item[state["workItemId"]],
                        stage="GATE",
                    )
                )
                action_record["requiredSkillPolicy"] = (
                    required_skill_policy()
                )
                action_record["evidenceContractRef"] = evidence_contract_ref(
                    state["workItemId"],
                    "gate",
                )
            elif action == "REQUEST_REVIEW":
                required_skills = repository.effective_required_skills(
                    registry,
                    by_item[state["workItemId"]],
                    stage="FINAL_REVIEW",
                )
                action_record["requiredSkills"] = required_skills
                action_record["mcpCallOptions"] = [
                    mcp_call(
                        "record_independent_review_pass",
                        item_id=state["workItemId"],
                        evidence="<independent-review-evidence>",
                    ),
                    *(
                        [
                            mcp_call(
                                "record_independent_review_blocked",
                                item_id=state["workItemId"],
                                evidence="<blocked-review-evidence>",
                            )
                        ]
                        if required_skills
                        else []
                    ),
                    mcp_call(
                        "record_human_review_acceptance",
                        item_id=state["workItemId"],
                        evidence="<human-review-evidence>",
                    ),
                ]
                action_record["requiredSkillPolicy"] = (
                    required_skill_policy()
                )
                action_record["evidenceContractRef"] = evidence_contract_ref(
                    state["workItemId"],
                    "review",
                )
                action_record["remediationContractRef"] = {
                    "artifactKind": "VALIDATION_REMEDIATION",
                    "mcpCall": mcp_call(
                        "evidence_contract",
                        item_id="<original-task-id>",
                        contract_kind="remediation",
                    ),
                }
            else:
                action_record["mcpCall"] = mcp_call(
                    "record_user_confirmation",
                    item_id=state["workItemId"],
                    evidence="<evidence>",
                )
                action_record["evidenceContractRef"] = evidence_contract_ref(
                    state["workItemId"],
                    "confirmation",
                )
            actions.append(action_record)
        elif state["status"] == "CLAIMED" and state["kind"] == "TASK_EXECUTION":
            claim_policy = graph["runtime"]["claimPolicy"]
            heartbeat_due_at = _runtime_timestamp_after(
                state["lastHeartbeatAt"],
                claim_policy["heartbeatSeconds"],
            )
            hard_expires_at = _runtime_timestamp_after(
                state["leaseExpiresAt"],
                claim_policy["graceSeconds"],
            )
            heartbeat_record = {
                "nodeId": state["id"],
                "nodeKind": state["kind"],
                "workItemId": state["workItemId"],
                "attempt": state["attempt"],
                "parallelGroup": None,
                "readyBecause": [f"claimed:{state.get('operationId') or 'unknown'}"],
                "critical": state["id"] in critical_nodes,
                "mcpCall": mcp_call(
                    "heartbeat_task",
                    item_id=state["workItemId"],
                    operation_id=state.get("operationId") or "<operation-id>",
                ),
                "transition": "TASK_HEARTBEAT",
                "routeCondition": "ON_HEARTBEAT",
                "heartbeatDueAt": heartbeat_due_at,
                "leaseExpiresAt": state.get("leaseExpiresAt"),
                "hardExpiresAt": hard_expires_at,
                **budget,
            }
            if _runtime_time(at) >= _runtime_time(hard_expires_at):
                actions.append({
                    "nodeId": state["id"],
                    "nodeKind": state["kind"],
                    "action": "ADVANCE_GRAPH",
                    "workItemId": state["workItemId"],
                    "attempt": state["attempt"],
                    "operationId": state.get("operationId"),
                    "parallelGroup": None,
                    "readyBecause": ["claim-hard-expired"],
                    "critical": state["id"] in critical_nodes,
                    "mcpCall": mcp_call(
                        "advance_graph",
                        item_id=state["workItemId"],
                    ),
                    "transition": "CLAIM_LEASE_EXPIRED",
                    "routeCondition": "ON_WORKER_LOST",
                    "failureClass": "WORKER_LOST",
                    "hardExpiresAt": hard_expires_at,
                    **budget,
                })
            elif _runtime_time(at) >= _runtime_time(heartbeat_due_at):
                remaining_seconds = int(
                    (
                        _runtime_time(state["leaseExpiresAt"])
                        - _runtime_time(at)
                    ).total_seconds()
                )
                if remaining_seconds <= 0:
                    urgency = "OVERDUE"
                elif remaining_seconds <= claim_policy["heartbeatSeconds"]:
                    urgency = "CRITICAL"
                else:
                    urgency = "NORMAL"
                actions.append({
                    **heartbeat_record,
                    "action": "HEARTBEAT_TASK",
                    "urgency": urgency,
                    "secondsUntilLeaseExpiry": remaining_seconds,
                })
            else:
                in_flight.append({
                    **heartbeat_record,
                    "status": "CLAIMED",
                    "scheduledAction": "HEARTBEAT_TASK",
                })
                wake_times.append(heartbeat_due_at)
        elif state["status"] == "PAUSED" and state["kind"] == "TASK_EXECUTION":
            actions.append({
                "nodeId": state["id"],
                "nodeKind": state["kind"],
                "action": "RESUME_TASK",
                "workItemId": state["workItemId"],
                "attempt": state["attempt"],
                "parallelGroup": None,
                "readyBecause": ["explicitly-paused"],
                "critical": state["id"] in critical_nodes,
                "mcpCall": mcp_call(
                    "resume_task",
                    item_id=state["workItemId"],
                ),
                "transition": "NODE_RESUMED",
                "routeCondition": "ON_RESUME",
                **budget,
            })
        elif state["status"] in {"PENDING", "BLOCKED", "CLAIMED", "CANCELLED"}:
            reasons = state["blockedBy"]
            if state["status"] == "CLAIMED":
                reasons = [f"claimed:{state.get('operationId') or 'unknown'}"]
            failure_class = state.get("failureClass")
            if state.get("retryExhausted"):
                recommended = "REQUEST_INTERVENTION"
            elif state["status"] == "CANCELLED":
                recommended = "NONE"
            elif state["status"] == "BLOCKED" and failure_class:
                recommended = (
                    failure_routing_decision(
                        graph,
                        attempt=state["attempt"],
                        failure_class=failure_class,
                    )["action"]
                    if failure_class != "GATE_FAILURE"
                    else (
                        "RETRY_NODE"
                        if budget["remainingAttempts"]
                        else "REQUEST_INTERVENTION"
                    )
                )
            else:
                recommended = "WAIT_FOR_PREDECESSORS"
            blocked_record = {
                "nodeId": state["id"],
                "nodeKind": state["kind"],
                "workItemId": state["workItemId"],
                "attempt": state["attempt"],
                "status": state["status"],
                "blockedBy": reasons,
                "failureClass": failure_class,
                "remainingAttempts": budget["remainingAttempts"],
                "retryExhausted": state.get("retryExhausted", False),
                "recommendedAction": recommended,
                "lastTransition": state.get("lastTransition"),
            }
            if state.get("blockedSkillUsage"):
                blocked_record["blockedSkillUsage"] = list(
                    state["blockedSkillUsage"]
                )
            if (
                recommended == "SUBMIT_REMEDIATION"
                and by_item[state["workItemId"]]["kind"] == "TASK"
            ):
                blocked_record["mcpCall"] = mcp_call(
                    "remediate_task",
                    item_id=state["workItemId"],
                    expected_baseline_fingerprint=(
                        by_item[state["workItemId"]]["baselineFingerprint"]
                    ),
                    evidence="<evidence>",
                )
                blocked_record["evidenceContractRef"] = evidence_contract_ref(
                    state["workItemId"],
                    "remediation",
                )
            if (
                state["kind"] == "ROOT_REVIEW"
                and state.get("lastTransition") == "REVIEW_BLOCKED"
            ):
                review_entry = by_item[state["workItemId"]]
                review_artifact = (
                    (
                        (review_entry.get("acceptance") or {})
                        .get("review") or {}
                    ).get("artifact") or {}
                )
                blocked_record.update({
                    "recoveryAction": (
                        "RETRY_ITEM_AFTER_SKILL_AVAILABLE"
                    ),
                    "mcpCall": mcp_call(
                        "retry_item",
                        item_id=state["workItemId"],
                        expected_baseline_fingerprint=(
                            by_item[state["workItemId"]]["baselineFingerprint"]
                        ),
                    ),
                    "evidenceContractRef": evidence_contract_ref(
                        state["workItemId"],
                        "review",
                    ),
                    "requiredSkills": (
                        repository.effective_required_skills(
                            registry,
                            review_entry,
                            stage="FINAL_REVIEW",
                        )
                    ),
                    "requiredSkillPolicy": required_skill_policy(),
                    "blockedSkillUsage": [
                        dict(usage)
                        for usage in review_artifact.get(
                            "skillUsage",
                            [],
                        )
                        if (
                            isinstance(usage, dict)
                            and usage.get("status") == "BLOCKED"
                        )
                    ],
                })
            blocked.append(blocked_record)
    dispatch_actions = [
        action for action in actions if action["action"] == "DISPATCH_TASK"
    ]
    active_agent_count = sum(
        state["status"] == "CLAIMED" and state["kind"] == "TASK_EXECUTION"
        for state in requested_states
    )
    desired_new_agent_count = len(dispatch_actions)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "rootId": graph["rootId"],
        "requestedItemId": requested["id"],
        "runId": run["runId"],
        "graphFingerprint": stored["graphFingerprint"],
        "dispatchPlan": {
            "authority": "GRAPH_CONTROLLER",
            "strategy": "AUTO_DISPATCH_ALL_SAFE",
            "parallelGroup": (
                dispatch_actions[0]["parallelGroup"] if dispatch_actions else None
            ),
            "dispatchTaskIds": [
                action["workItemId"] for action in dispatch_actions
            ],
            "desiredNewAgentCount": desired_new_agent_count,
            "activeAgentCount": active_agent_count,
            "desiredTotalAgentCount": active_agent_count + desired_new_agent_count,
            "hostSelectionAllowed": False,
            "capacityPolicy": "QUEUE_REMAINDER_STABLE",
            "claimPolicy": "JUST_IN_TIME_ON_WORKER_START",
            "queuedTasksRemainUnclaimed": True,
            "recalculateAfterEveryTransition": True,
        },
        "actions": actions,
        "inFlight": in_flight,
        "nextWakeAt": min(wake_times) if wake_times else None,
        "blocked": blocked,
        "criticalPath": path,
        "summary": {
            "actionable": len(actions),
            "blocked": len(blocked),
            "claimed": active_agent_count,
            "inFlight": len(in_flight),
        },
    }


def get_graph_frontier(
    *,
    root: str,
    work_item_id: str,
    now: object = None,
) -> dict[str, Any]:
    repository, registry, requested, stored, run = _load_graph_view(
        root=root,
        work_item_id=work_item_id,
        now=now,
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
    from .repository import timestamp

    return build_graph_frontier(
        repository,
        registry,
        requested,
        stored,
        run,
        states,
        at=timestamp(now),
    )


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
        repository.write_registry(registry, changed_item_ids=set())
    return get_graph_replay(root=root, work_item_id=work_item_id)


def list_graph_events(
    *,
    root: str,
    work_item_id: str,
    after_event_id: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    repository, registry, _, _, _ = _load_graph_view(root=root, work_item_id=work_item_id)
    root_entry = hierarchy_root_entry(
        registry,
        next(item for item in registry["workItems"] if item["id"] == work_item_id),
    )
    return repository.read_graph_events(
        root_entry["id"],
        after_event_id=after_event_id,
        limit=limit,
    )


def _runtime_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _runtime_timestamp_after(value: str, seconds: int) -> str:
    return (
        (_runtime_time(value) + timedelta(seconds=seconds))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def advance_graph(
    *,
    root: str,
    work_item_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Apply deterministic controller routes such as expired-claim recovery."""
    from .repository import GovernanceRepository, timestamp

    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    decisions: list[dict[str, Any]] = []
    with repository.transaction() as registry:
        requested, root_entry = _root_for_requested_item(registry, work_item_id)
        stored = repository.read_graph_definition(root_entry["id"])
        run = repository.read_graph_run(root_entry["id"])
        replay = replay_graph_events(
            stored["graph"],
            run,
            repository.read_graph_events(root_entry["id"]),
        )
        if replay["status"] in {"CANCELLED", "COMPLETED"}:
            return {
                "rootId": root_entry["id"],
                "runId": run["runId"],
                "status": replay["status"],
                "decisions": [],
            }
        by_item = {item["id"]: item for item in registry["workItems"]}
        for state in replay["nodes"]:
            entry = by_item[state["workItemId"]]
            if (
                state["kind"] != "TASK_EXECUTION"
                or state["status"] != "CLAIMED"
                or not is_descendant(registry, entry, requested["id"])
                or not state.get("leaseExpiresAt")
                or _runtime_time(at) < _runtime_time(
                    _runtime_timestamp_after(
                        state["leaseExpiresAt"],
                        stored["graph"]["runtime"]["claimPolicy"][
                            "graceSeconds"
                        ],
                    )
                )
            ):
                continue
            claim = entry.get("claim") or {}
            if claim.get("operationId") != state.get("operationId"):
                fail(
                    "DELIVERY_GRAPH_REPLAY_MISMATCH",
                    "Graph claim and work item claim disagree during automatic recovery",
                )
            repository.append_graph_event(
                root_id=root_entry["id"],
                node_id=state["nodeId"],
                event_type="CLAIM_LEASE_EXPIRED",
                actor="CONTROLLER",
                operation_id=state["operationId"],
                payload={
                    "leaseExpiresAt": state["leaseExpiresAt"],
                    "hardExpiresAt": _runtime_timestamp_after(
                        state["leaseExpiresAt"],
                        stored["graph"]["runtime"]["claimPolicy"][
                            "graceSeconds"
                        ],
                    ),
                    "failureClass": "WORKER_LOST",
                },
                recorded_at=at,
            )
            entry["claim"] = None
            entry["status"] = "BLOCKED"
            decision = failure_routing_decision(
                stored["graph"],
                attempt=state["attempt"],
                failure_class="WORKER_LOST",
            )
            if decision["action"] == "RETRY_NODE":
                attempts = repository.begin_graph_attempts(
                    root_entry["id"], [state["nodeId"]], at=at
                )
                repository.append_graph_event(
                    root_id=root_entry["id"],
                    node_id=state["nodeId"],
                    event_type="NODE_RETRY_SCHEDULED",
                    actor="CONTROLLER",
                    operation_id=None,
                    payload={
                        "attempts": attempts,
                        "failureClass": "WORKER_LOST",
                        "routeCondition": decision["routeCondition"],
                    },
                    recorded_at=at,
                )
                entry["status"] = "FROZEN"
            else:
                repository.append_graph_event(
                    root_id=root_entry["id"],
                    node_id=state["nodeId"],
                    event_type="RETRY_EXHAUSTED",
                    actor="CONTROLLER",
                    operation_id=None,
                    payload={
                        "failureClass": "WORKER_LOST",
                        "routeCondition": decision["routeCondition"],
                        "maxAttempts": decision["maxAttempts"],
                    },
                    recorded_at=at,
                )
            entry["recordRevision"] += 1
            entry["updatedAt"] = at
            decisions.append({"nodeId": state["nodeId"], **decision})
        if decisions:
            registry["currentFocus"] = {
                "workItemId": requested["id"],
                "purpose": "GRAPH_ADVANCED",
            }
            registry["revision"] += 1
            registry["updatedAt"] = at
            repository.write_registry(registry)
    status = get_graph_status(root=root, work_item_id=work_item_id)
    return {
        "rootId": status["rootId"],
        "runId": status["run"]["runId"],
        "status": status["run"]["status"],
        "decisions": decisions,
    }


def cancel_graph_run(
    *,
    root: str,
    work_item_id: str,
    confirmed: bool = False,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    if not confirmed:
        fail("CONFIRMATION_REQUIRED", "Graph run cancellation requires explicit confirmation")
    from .repository import GovernanceRepository, timestamp

    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    with repository.transaction() as registry:
        requested, root_entry = _root_for_requested_item(registry, work_item_id)
        stored = repository.read_graph_definition(root_entry["id"])
        run = repository.read_graph_run(root_entry["id"])
        replay = replay_graph_events(
            stored["graph"],
            run,
            repository.read_graph_events(root_entry["id"]),
        )
        if replay["status"] == "CANCELLED":
            return {
                "rootId": root_entry["id"],
                "runId": run["runId"],
                "status": "CANCELLED",
                "cancelledAt": replay["cancelledAt"],
            }
        if replay["status"] == "COMPLETED":
            fail("DELIVERY_GRAPH_ALREADY_COMPLETED", "A completed graph run cannot be cancelled")
        for entry in registry["workItems"]:
            if is_descendant(registry, entry, root_entry["id"]) and entry.get("claim"):
                entry["claim"] = None
                entry["status"] = "FROZEN"
                entry["recordRevision"] += 1
                entry["updatedAt"] = at
        repository.append_graph_event(
            root_id=root_entry["id"],
            node_id=None,
            event_type="GRAPH_RUN_CANCELLED",
            actor="USER",
            operation_id=None,
            payload={"confirmed": True, "requestedItemId": requested["id"]},
            recorded_at=at,
        )
        registry["currentFocus"] = {
            "workItemId": requested["id"],
            "purpose": "GRAPH_RUN_CANCELLED",
        }
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_registry(registry)
        return {
            "rootId": root_entry["id"],
            "runId": run["runId"],
            "status": "CANCELLED",
            "cancelledAt": at,
        }
