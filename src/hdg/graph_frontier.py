from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from .dispatch_contracts import receiver_skill_prompt
from .graph_model import LOOP_NODE_KINDS, graph_assurance_profile
from .graph_runtime import advance_graph
from .loop_contracts import (
    loop_execution_policy,
    resource_claims_overlap,
)
from .repository import SchedulerRepository, timestamp
from .progress_reporting import attach_progress_monitor


_PASSIVE_FRONTIER_ACTIONS = frozenset(
    {
        "CONTINUE_OR_HEARTBEAT_LOOP",
        "WAIT_FOR_DISPATCH_RECEIVER",
    }
)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _render_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )


def _minimum_timestamp(values: list[object]) -> str | None:
    parsed = [
        (value, _parse_timestamp(value))
        for value in values
        if isinstance(value, str)
    ]
    valid = [(value, instant) for value, instant in parsed if instant is not None]
    if not valid:
        return None
    return min(valid, key=lambda item: item[1])[0]


def _attach_frontier_wait_directive(
    result: dict[str, Any],
) -> dict[str, Any]:
    monitor = result.get("progressMonitor")
    if not isinstance(monitor, dict):
        return result
    enriched = dict(result)
    enriched_monitor = deepcopy(monitor)
    directive = deepcopy(enriched_monitor.get("waitDirective") or {})
    actions = result.get("actions")
    action_names = [
        action["action"]
        for action in actions
        if isinstance(action, dict) and isinstance(action.get("action"), str)
    ] if isinstance(actions, list) else []
    immediate_actions = [
        action for action in action_names if action not in _PASSIVE_FRONTIER_ACTIONS
    ]
    capacity_wait_actions = [
        action
        for action in actions
        if isinstance(action, dict)
        and action.get("action")
        in {"WAIT_FOR_EXECUTOR_CAPACITY", "WAIT_FOR_HOST_CAPACITY"}
    ] if isinstance(actions, list) else []
    advance_required = directive.get("mode") == "ADVANCE_REQUIRED"
    active_receiver = bool(result.get("activeLoops"))
    waiting_for_receiver = "WAIT_FOR_DISPATCH_RECEIVER" in action_names
    active_or_reserved = active_receiver or waiting_for_receiver
    next_wake_at = result.get("nextWakeAt")
    if advance_required:
        mode = "ADVANCE_REQUIRED"
    elif immediate_actions and active_or_reserved:
        mode = "CONSUME_ACTIONS_THEN_HOST_NATIVE_EVENT_OR_DEADLINE"
    elif immediate_actions:
        mode = "CONSUME_ACTIONS_FIRST"
    elif active_or_reserved:
        mode = "HOST_NATIVE_EVENT_OR_DEADLINE"
    elif isinstance(next_wake_at, str):
        mode = "DEADLINE_ONLY"
    else:
        mode = "NO_AUTOMATIC_WAIT"
    observed = _parse_timestamp(enriched_monitor.get("observedAt"))
    poll_not_before = _parse_timestamp(directive.get("pollNotBefore"))
    if observed is not None and advance_required:
        poll_not_before = observed
    elif (
        poll_not_before is None
        and observed is not None
        and active_receiver
    ):
        poll_not_before = observed + timedelta(
            seconds=int(enriched_monitor.get("recommendedPollSeconds", 90))
        )
    next_wake = _parse_timestamp(next_wake_at)
    if (
        poll_not_before is None
        and waiting_for_receiver
        and next_wake is not None
    ):
        poll_not_before = next_wake
    poll_tool = (
        "graph_frontier"
        if advance_required
        else "graph_status"
        if active_receiver
        else "graph_frontier"
        if waiting_for_receiver
        else None
    )
    directive.update(
        {
            "mode": mode,
            "pollNotBefore": (
                _render_timestamp(poll_not_before)
                if poll_not_before is not None
                else None
            ),
            "pollTool": poll_tool,
            "advanceTool": "graph_frontier",
            "interruptOn": (
                [
                    "NATIVE_RECEIVER_COMPLETED",
                    "NATIVE_RECEIVER_NEEDS_ATTENTION",
                ]
                if active_or_reserved
                else []
            ),
            "onInterrupt": (
                "CALL_GRAPH_FRONTIER_ONCE" if active_or_reserved else "NONE"
            ),
            "onTimeout": (
                "CALL_GRAPH_STATUS_ONCE"
                if poll_tool == "graph_status"
                else "CALL_GRAPH_FRONTIER_ONCE"
                if poll_tool == "graph_frontier"
                else "NONE"
            ),
            "consumeActionsBeforeWaiting": bool(immediate_actions),
            "immediateActions": immediate_actions,
            "nextWakeAt": next_wake_at,
            "onNextWakeAt": (
                "CALL_GRAPH_FRONTIER_ONCE"
                if isinstance(next_wake_at, str)
                else "NONE"
            ),
            "suppressUnchangedCommentary": True,
        }
    )
    capacity_wake_times = [
        wake_at
        for action in capacity_wait_actions
        for wake_at in (action.get("resumeAt") or action.get("resetAt"),)
        if isinstance(wake_at, str)
    ]
    if capacity_wait_actions and capacity_wake_times:
        directive["nativeWakeDirective"] = {
            "mode": "HOST_NATIVE_ONE_SHOT",
            "scheduleAfter": _minimum_timestamp(capacity_wake_times),
            "applySafetyMargin": True,
            "cancelRecurringMonitors": any(
                action.get("cancelRecurringMonitors") is True
                for action in capacity_wait_actions
            ),
            "capacityActions": [
                str(action["action"])
                for action in capacity_wait_actions
            ],
        }
    else:
        directive.pop("nativeWakeDirective", None)
    enriched_monitor["waitDirective"] = directive
    enriched["progressMonitor"] = enriched_monitor
    return enriched


def build_graph_frontier(
    graph: dict[str, Any],
    run: dict[str, Any],
    *,
    external_reservations: list[dict[str, Any]] | None = None,
    dispatch_reservations: list[dict[str, Any]] | None = None,
    skill_hints: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    if run["status"] in {"COMPLETED", "CANCELLED", "SUPERSEDED"}:
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
            "progressMonitor": run.get("progressMonitor"),
        }
        if run.get("gitBinding") is not None:
            result["gitBinding"] = run["gitBinding"]
        return _attach_frontier_wait_directive(result)
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
    claimed_node_ids = {state["nodeId"] for state in claimed}
    actions: list[dict[str, Any]] = []
    ready_loops: list[dict[str, Any]] = []
    active_loops: list[dict[str, Any]] = []
    paused_loops: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    conflict_wake_times: list[str] = []
    reserved_claims = [
        (
            state["nodeId"],
            definitions[state["nodeId"]]["loop"]["resourceClaims"],
            state.get("leaseExpiresAt"),
        )
        for state in claimed
    ]
    reserved_claims.extend(
        (
            f"{reservation['rootId']}/{reservation['nodeId']}",
            reservation["resourceClaims"],
            reservation.get("leaseExpiresAt"),
        )
        for reservation in (external_reservations or [])
    )
    reserved_claims.extend(
        (
            f"{reservation['rootId']}/{reservation['nodeId']}",
            reservation["resourceClaims"],
            (
                reservation.get("reservationExpiresAt")
                if reservation.get("reservationStatus") == "RESERVED"
                else reservation.get("leaseExpiresAt")
            ),
        )
        for reservation in (dispatch_reservations or [])
        if not (
            reservation["rootId"] == run["rootId"]
            and reservation["nodeId"] in claimed_node_ids
        )
    )
    local_dispatch_reservations = {
        reservation["nodeId"]: reservation
        for reservation in (dispatch_reservations or [])
        if reservation["rootId"] == run["rootId"]
        and reservation.get("reservationStatus") == "RESERVED"
    }

    for state in sorted(run["nodes"], key=lambda item: item["nodeId"]):
        definition = definitions[state["nodeId"]]
        kind = definition["kind"]
        execution_policy = (
            loop_execution_policy(graph_assurance_profile(graph))
            if kind in LOOP_NODE_KINDS
            else None
        )
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
            dispatch_reservation = local_dispatch_reservations.get(
                state["nodeId"]
            )
            if dispatch_reservation is not None:
                ready_loops.append(
                    {
                        "nodeId": state["nodeId"],
                        "kind": kind,
                        "workItemId": definition["workItemId"],
                        "attempt": state["attempt"],
                        "loop": definition["loop"],
                        "resourceConflicts": [],
                        "dispatchReservation": {
                            "dispatchReservationId": (
                                dispatch_reservation[
                                    "dispatchReservationId"
                                ]
                            ),
                            "reservationExpiresAt": (
                                dispatch_reservation[
                                    "reservationExpiresAt"
                                ]
                            ),
                        },
                    }
                )
                actions.append(
                    {
                        "action": "WAIT_FOR_DISPATCH_RECEIVER",
                        "nodeId": state["nodeId"],
                        "dispatchReservationId": (
                            dispatch_reservation[
                                "dispatchReservationId"
                            ]
                        ),
                        "reservationExpiresAt": (
                            dispatch_reservation[
                                "reservationExpiresAt"
                            ]
                        ),
                    }
                )
                continue
            conflicting_reservations = [
                (reserved_node_id, wake_at)
                for reserved_node_id, claims, wake_at in reserved_claims
                if resource_claims_overlap(
                    definition["loop"]["resourceClaims"],
                    claims,
                )
            ]
            conflicts = sorted({
                reserved_node_id
                for reserved_node_id, _ in conflicting_reservations
            })
            conflict_wake_times.extend(
                wake_at
                for _, wake_at in conflicting_reservations
                if isinstance(wake_at, str)
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
                        None,
                    )
                )
                manual_task = (
                    kind == "TASK_LOOP"
                    and (
                        run.get("executionMode") == "manual"
                        or state.get("manualHandoffEnabled") is True
                    )
                )
                manual_fields: dict[str, Any] = {}
                if manual_task:
                    manual_fields["dispatchMode"] = "MANUAL"
                    receiver_prompt = receiver_skill_prompt(
                        kind,
                        skill_hints or [],
                    )
                    manual_fields["receiverPrompt"] = receiver_prompt
                    if skill_hints:
                        manual_fields["skillHints"] = [
                            dict(item)
                            for item in (skill_hints or [])
                        ]
                actions.append(
                    {
                        "action": (
                            "CLAIM_MANUAL_TASK"
                            if manual_task
                            else "DISPATCH_LOOP"
                        ),
                        "nodeId": state["nodeId"],
                        "loopRef": definition["loop"]["ref"],
                        "executionPolicy": execution_policy,
                        **manual_fields,
                    }
                )
        elif state["status"] == "CLAIMED":
            record = {
                "nodeId": state["nodeId"],
                "kind": kind,
                "workItemId": definition["workItemId"],
                "attempt": state["attempt"],
                "owner": state["owner"],
                "agentId": state.get("agentId"),
                "actualModelId": state.get("actualModelId"),
                "actualModelSource": state.get("actualModelSource"),
                "operationId": state["operationId"],
                "leaseExpiresAt": state["leaseExpiresAt"],
                "progress": state.get("progress"),
                "monitor": state.get("monitor"),
            }
            active_loops.append(record)
            actions.append(
                {
                    "action": "CONTINUE_OR_HEARTBEAT_LOOP",
                    "nodeId": state["nodeId"],
                    "operationId": state["operationId"],
                    "executionPolicy": execution_policy,
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
                            "executionPolicy": execution_policy,
                        }
                    )
                else:
                    actions.append(
                        {
                            "action": "WAIT_FOR_EXECUTOR_CAPACITY",
                            "nodeId": state["nodeId"],
                            "resumeAt": resume_at,
                            "executionPolicy": execution_policy,
                        }
                    )
            else:
                actions.append(
                    {
                        "action": "RESUME_LOOP_IN_INDEPENDENT_CONTEXT",
                        "nodeId": state["nodeId"],
                        "executionPolicy": execution_policy,
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
    host_capacity = run.get("hostCapacity")
    if host_capacity is not None:
        affected_node_ids = sorted(
            item["nodeId"]
            for item in paused_loops
            if item.get("capacityScope") == "HOST"
        )
        actions = [
            action
            for action in actions
            if action["action"]
            not in {
                "DISPATCH_LOOP",
                "CONTINUE_OR_HEARTBEAT_LOOP",
                "WAIT_FOR_HOST_CAPACITY",
            }
        ]
        actions.append(
            {
                "action": "WAIT_FOR_HOST_CAPACITY",
                "resetAt": host_capacity["resetAt"],
                "capacityKey": host_capacity["capacityKey"],
                "affectedNodeIds": affected_node_ids,
                "cancelRecurringMonitors": True,
                "wakeMode": "HOST_NATIVE_ONE_SHOT",
            }
        )
    result = {
        "rootId": run["rootId"],
        "runId": run["runId"],
        "status": run["status"],
        "readyLoops": ready_loops,
        "activeLoops": active_loops,
        "pausedLoops": paused_loops,
        "blockedLoops": blocked,
        "nextWakeAt": _minimum_timestamp(
            [
                item["resumeAt"]
                for item in paused_loops
                if "resumeAt" in item
            ]
            + [
                item["leaseExpiresAt"]
                for item in active_loops
                if isinstance(item.get("leaseExpiresAt"), str)
            ]
            + [
                reservation["reservationExpiresAt"]
                for reservation in (dispatch_reservations or [])
                if reservation["rootId"] == run["rootId"]
                and reservation.get("reservationStatus") == "RESERVED"
            ]
            + conflict_wake_times,
        ),
        "workspaceIsolation": run.get("workspaceIsolation"),
        "actions": actions,
        "progressMonitor": run.get("progressMonitor"),
    }
    if host_capacity is not None:
        result["hostCapacity"] = host_capacity
        result["nextWakeAt"] = _minimum_timestamp(
            [result["nextWakeAt"], host_capacity["resetAt"]]
        )
    if run.get("gitBinding") is not None:
        result["gitBinding"] = run["gitBinding"]
    return _attach_frontier_wait_directive(result)


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
    repository = SchedulerRepository(root, now=now)
    stored = repository.hierarchy(root_id)
    graph = stored["graph"]
    observed_now = timestamp(now)
    observation_at = (
        observed_now
        if _parse_timestamp(observed_now)
        >= _parse_timestamp(run["updatedAt"])
        else run["updatedAt"]
    )
    run = attach_progress_monitor(
        run,
        graph,
        observed_at=observation_at,
    )
    with repository.read() as connection:
        external_reservations = (
            repository.claimed_resource_reservations(
                connection,
                at=observation_at,
                exclude_root_id=root_id,
            )
        )
        dispatch_reservations = (
            repository.active_dispatch_reservations(
                connection,
                at=observation_at,
            )
        )
    return build_graph_frontier(
        graph,
        run,
        external_reservations=external_reservations,
        dispatch_reservations=dispatch_reservations,
        skill_hints=stored["hierarchy"]["root"]["skillHints"],
    )


__all__ = (
    "build_graph_frontier",
    "get_graph_frontier",
)
