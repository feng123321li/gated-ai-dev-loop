from __future__ import annotations

from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .model import required_skill_policy, scope_patterns_overlap
from .graph_contracts import (
    evidence_contract_ref,
    mcp_call,
)

from .graph_state import (
    materialized_graph_states,
    retry_budget,
    failure_routing_decision,
    is_descendant,
    derive_node_states,
    critical_path,
    _runtime_time,
    _runtime_timestamp_after,
)

from .graph_queries import (
    _load_graph_view,
)

def _task_write_scope(repository: Any, definition: dict[str, Any]) -> list[str]:
    scope = list(definition["scope"])
    scope.extend(
        item["path"]
        for item in repository.effective_task_file_changes(definition)
    )
    return sorted(set(scope))


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
            "frontierRevision": 0,
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
        "frontierRevision": run["recordRevision"],
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


def compact_graph_frontier(
    frontier: dict[str, Any],
    *,
    include_blocked_details: bool = False,
    since_revision: int | None = None,
) -> dict[str, Any]:
    """Return the action-bearing subset needed by an execution adapter."""
    revision = frontier["frontierRevision"]
    time_sensitive_actions = {
        "HEARTBEAT_TASK",
        "ADVANCE_GRAPH",
    }
    if (
        since_revision is not None
        and since_revision == revision
        and not any(
            action["action"] in time_sensitive_actions
            for action in frontier["actions"]
        )
    ):
        return {
            "schemaVersion": frontier["schemaVersion"],
            "rootId": frontier["rootId"],
            "requestedItemId": frontier["requestedItemId"],
            "runId": frontier["runId"],
            "frontierRevision": revision,
            "frontierSource": frontier.get("frontierSource", "DERIVED"),
            "responseMode": "COMPACT",
            "unchanged": True,
            "nextWakeAt": frontier["nextWakeAt"],
            "summary": frontier["summary"],
        }

    action_fields = {
        "nodeId",
        "nodeKind",
        "action",
        "workItemId",
        "attempt",
        "operationId",
        "parallelGroup",
        "mcpCall",
        "mcpCallOptions",
        "evidenceContractRef",
        "remediationContractRef",
        "requiredSkills",
        "heartbeatDueAt",
        "leaseExpiresAt",
        "hardExpiresAt",
        "failureClass",
        "remainingAttempts",
        "retryExhausted",
        "blockedSkillUsage",
    }
    in_flight_fields = {
        "nodeId",
        "nodeKind",
        "workItemId",
        "attempt",
        "operationId",
        "owner",
        "leaseExpiresAt",
        "heartbeatDueAt",
        "hardExpiresAt",
    }
    result = {
        "schemaVersion": frontier["schemaVersion"],
        "rootId": frontier["rootId"],
        "requestedItemId": frontier["requestedItemId"],
        "runId": frontier["runId"],
        "frontierRevision": revision,
        "frontierSource": frontier.get("frontierSource", "DERIVED"),
        "responseMode": "COMPACT",
        "unchanged": False,
        "dispatchPlan": frontier["dispatchPlan"],
        "actions": [
            {
                key: value
                for key, value in action.items()
                if key in action_fields
            }
            for action in frontier["actions"]
        ],
        "inFlight": [
            {
                key: value
                for key, value in record.items()
                if key in in_flight_fields
            }
            for record in frontier["inFlight"]
        ],
        "nextWakeAt": frontier["nextWakeAt"],
        "summary": frontier["summary"],
        "detailRef": mcp_call(
            "graph_frontier",
            item_id=frontier["requestedItemId"],
            response_mode="full",
            include_blocked_details=True,
        ),
    }
    if include_blocked_details:
        result["blocked"] = frontier["blocked"]
    return result


def get_graph_frontier(
    *,
    root: str,
    work_item_id: str,
    now: object = None,
    response_mode: str = "full",
    since_revision: int | None = None,
    include_blocked_details: bool = True,
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
        frontier_source = "DERIVED"
    else:
        states = materialized_graph_states(stored["graph"], run, registry)
        frontier_source = "SNAPSHOT"
    from .repository import timestamp

    frontier = build_graph_frontier(
        repository,
        registry,
        requested,
        stored,
        run,
        states,
        at=timestamp(now),
    )
    frontier["frontierSource"] = frontier_source
    if response_mode == "full":
        return frontier
    if response_mode != "compact":
        fail(
            "DELIVERY_GRAPH_FRONTIER_MODE_INVALID",
            "Graph frontier response mode must be full or compact",
        )
    return compact_graph_frontier(
        frontier,
        include_blocked_details=include_blocked_details,
        since_revision=since_revision,
    )
