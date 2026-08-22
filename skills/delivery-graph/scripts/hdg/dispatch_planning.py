from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from .agent_profiles import (
    load_agent_profile_catalog,
    profile_for_loop,
    team_plan_for_profile,
)
from .dispatch_contracts import (
    DISPATCH_POLICY_VERSION,
    HOST_ADAPTER_RECEIVER_AGENTS,
    HOST_NATIVE_DISPATCH_TRANSPORT,
    automatic_dispatch_decision_fingerprint,
    receiver_skill_prompt,
)
from .errors import fail
from .graph_frontier import get_graph_frontier
from .jsonio import fingerprint
from .repository import SchedulerRepository


DISPATCH_RESERVATION_SECONDS = 300
MAX_CONCURRENT_EXECUTORS = 4
SAFE_HOST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$")


def _trusted_host_receiver(
    *,
    host_adapter_id: str | None,
    host_native_agent_ids: tuple[str, ...] | None,
) -> tuple[str, str]:
    """Resolve exactly one trusted native receiver identity."""

    if (
        not isinstance(host_adapter_id, str)
        or SAFE_HOST_ID.fullmatch(host_adapter_id) is None
    ):
        fail(
            "SCHEDULER_HOST_EXECUTOR_CONTEXT_REQUIRED",
            "Automatic dispatch requires the trusted current host Adapter",
        )
    if (
        not isinstance(host_native_agent_ids, tuple)
        or len(host_native_agent_ids) != 1
        or not isinstance(host_native_agent_ids[0], str)
        or SAFE_HOST_ID.fullmatch(host_native_agent_ids[0]) is None
    ):
        fail(
            "SCHEDULER_HOST_NATIVE_INVENTORY_MISMATCH",
            "Automatic dispatch requires exactly one trusted native receiver Agent",
        )
    receiver_agent_id = host_native_agent_ids[0]
    expected_receiver = HOST_ADAPTER_RECEIVER_AGENTS.get(host_adapter_id)
    if expected_receiver is None or receiver_agent_id != expected_receiver:
        fail(
            "SCHEDULER_HOST_NATIVE_INVENTORY_MISMATCH",
            "The trusted host Adapter cannot create the reported receiver Agent",
            hostAdapterId=host_adapter_id,
            expectedReceiverAgentId=expected_receiver,
            suppliedReceiverAgentId=receiver_agent_id,
        )
    return host_adapter_id, receiver_agent_id


def _role(node_kind: str) -> str:
    return (
        "DEVELOPMENT"
        if node_kind == "TASK_LOOP"
        else "INDEPENDENT_REVIEW"
    )


def _assignment(
    *,
    node: dict[str, Any],
    attempt: int,
    graph_fingerprint: str,
    host_adapter_id: str,
    receiver_agent_id: str,
    skill_hints: list[dict[str, str]],
    agent_catalog: dict[str, Any],
) -> dict[str, Any]:
    profile = profile_for_loop(agent_catalog, node["kind"])
    team_plan = team_plan_for_profile(agent_catalog, profile)
    assignment = {
        "nodeId": node["id"],
        "kind": node["kind"],
        "workItemId": node["workItemId"],
        "attempt": attempt,
        "role": _role(node["kind"]),
        "hostAdapterId": host_adapter_id,
        "receiverAgentId": receiver_agent_id,
        "dispatchTransport": HOST_NATIVE_DISPATCH_TRANSPORT,
        "hostDispatchAllowed": True,
        "agentProfileId": profile["id"],
        "agentCatalogVersion": agent_catalog["catalogVersion"],
        "agentCatalogFingerprint": agent_catalog["catalogFingerprint"],
        "requiredCapabilities": list(profile["capabilities"]),
        "outputContract": profile["outputContract"],
        "teamPlan": team_plan,
        "contextInput": {
            "rootId": None,
            "nodeId": node["id"],
        },
        "decisionFingerprint": automatic_dispatch_decision_fingerprint(
            graph_fingerprint=graph_fingerprint,
            node_id=node["id"],
            attempt=attempt,
            host_adapter_id=host_adapter_id,
            receiver_agent_id=receiver_agent_id,
            dispatch_transport=HOST_NATIVE_DISPATCH_TRANSPORT,
            agent_profile_id=profile["id"],
            agent_catalog_fingerprint=agent_catalog[
                "catalogFingerprint"
            ],
            team_plan_fingerprint=team_plan["teamPlanFingerprint"],
        ),
        "reasons": [
            {
                "code": "CURRENT_HOST_RECEIVER",
                "message": (
                    "Host orchestration routes the Loop to the trusted "
                    "native receiver for the current host Adapter."
                ),
            }
        ],
        "independence": {
            "required": True,
            "boundary": "INDEPENDENT_RECEIVER_CONTEXT",
        },
    }
    receiver_prompt = receiver_skill_prompt(
        node["kind"],
        skill_hints,
        host_adapter_id=host_adapter_id,
        agent_profile_id=profile["id"],
        team_plan=team_plan,
    )
    assignment["receiverPrompt"] = receiver_prompt
    if skill_hints:
        assignment["skillHints"] = [dict(item) for item in skill_hints]
    return assignment


def plan_dispatch_batch(
    *,
    root: str,
    root_id: str,
    expected_graph_fingerprint: str,
    host_native_agent_ids: tuple[str, ...] | None = None,
    host_adapter_id: str | None = None,
    workspace_root: str | None = None,
    max_concurrent_executors: int = MAX_CONCURRENT_EXECUTORS,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Reserve the current frontier for trusted native receivers."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    actual_host_adapter_id, receiver_agent_id = _trusted_host_receiver(
        host_adapter_id=host_adapter_id,
        host_native_agent_ids=host_native_agent_ids,
    )
    stored = repository.hierarchy(root_id)
    if expected_graph_fingerprint != stored["graphFingerprint"]:
        fail(
            "SCHEDULER_GRAPH_FINGERPRINT_MISMATCH",
            "The expected Graph fingerprint is stale",
            expectedGraphFingerprint=expected_graph_fingerprint,
            actualGraphFingerprint=stored["graphFingerprint"],
        )
    frontier = get_graph_frontier(
        root=root,
        root_id=root_id,
        explicit_dogfood=explicit_dogfood,
        now=now,
    )
    execution_mode = frontier.get("executionMode")
    if execution_mode not in {"active", "manual"}:
        fail(
            "SCHEDULER_AUTO_DISPATCH_DISABLED",
            "Host-native automatic dispatch requires a governed Graph run",
            executionMode=execution_mode,
        )
    graph = stored["graph"]
    agent_catalog = load_agent_profile_catalog(workspace_root or root)
    skill_hints = stored["hierarchy"]["root"]["skillHints"]
    definitions = {node["id"]: node for node in graph["nodes"]}
    ready_states = {
        node["nodeId"]: node
        for node in frontier["readyLoops"]
    }
    frontier_dispatch_node_ids = sorted(
        action["nodeId"]
        for action in frontier["actions"]
        if action["action"] == "DISPATCH_LOOP"
    )
    dispatch_node_ids = frontier_dispatch_node_ids
    reserved_actions = {
        action["nodeId"]: action
        for action in frontier["actions"]
        if action["action"] == "WAIT_FOR_DISPATCH_RECEIVER"
    }
    assignments = [
        _assignment(
            node=definitions[node_id],
            attempt=ready_states[node_id]["attempt"],
            graph_fingerprint=stored["graphFingerprint"],
            host_adapter_id=actual_host_adapter_id,
            receiver_agent_id=receiver_agent_id,
            skill_hints=skill_hints,
            agent_catalog=agent_catalog,
        )
        for node_id in dispatch_node_ids
    ]
    for assignment in assignments:
        assignment["contextInput"]["rootId"] = root_id

    reservations = repository.reserve_dispatch_assignments(
        root_id=root_id,
        graph_fingerprint=stored["graphFingerprint"],
        assignments=assignments,
        agent_slot_limits={
            receiver_agent_id: max_concurrent_executors
        },
        profile_slot_limits={
            profile["id"]: min(
                profile["maxConcurrent"],
                max_concurrent_executors,
            )
            for profile in agent_catalog["profiles"]
            if profile["kind"] == "RECEIVER"
        },
        orchestrator_slot_limit=max_concurrent_executors,
        reservation_seconds=DISPATCH_RESERVATION_SECONDS,
    )
    reserved_assignments: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = [
        {
            "nodeId": node_id,
            "code": "DISPATCH_ALREADY_RESERVED",
            "message": (
                "Another dispatcher already reserved this Loop for host "
                "Agent creation."
            ),
            "dispatchReservationId": action["dispatchReservationId"],
            "reservationExpiresAt": action["reservationExpiresAt"],
            "claimCreated": False,
        }
        for node_id, action in sorted(reserved_actions.items())
    ]
    by_node = {assignment["nodeId"]: assignment for assignment in assignments}
    for node_id in dispatch_node_ids:
        assignment = by_node[node_id]
        reservation = reservations["accepted"].get(node_id)
        if reservation is None:
            deferred.append(
                {
                    "nodeId": node_id,
                    "kind": assignment["kind"],
                    "workItemId": assignment["workItemId"],
                    **reservations["rejected"][node_id],
                    "claimCreated": False,
                }
            )
            continue
        assignment.update(reservation)
        assignment["hostTaskName"] = (
            "ld_" + reservation["dispatchReservationId"].replace("-", "")
        )
        assignment["contextInput"].update(
            {
                "dispatchReservationId": reservation[
                    "dispatchReservationId"
                ],
                "hostTaskName": assignment["hostTaskName"],
            }
        )
        reserved_assignments.append(assignment)

    plan_material = {
        "policyVersion": DISPATCH_POLICY_VERSION,
        "rootId": root_id,
        "graphFingerprint": stored["graphFingerprint"],
        "hostAdapterId": actual_host_adapter_id,
        "receiverAgentId": receiver_agent_id,
        "agentCatalogFingerprint": agent_catalog["catalogFingerprint"],
        "dispatchNodeIds": dispatch_node_ids,
        "assignments": reserved_assignments,
        "deferred": deferred,
        "nextAction": "CREATE_INDEPENDENT_RECEIVERS",
        "dispatchPolicy": {
            "maxConcurrentExecutors": max_concurrent_executors,
            "agentCatalogVersion": agent_catalog["catalogVersion"],
            "agentCatalogFingerprint": agent_catalog[
                "catalogFingerprint"
            ],
        },
    }
    reservation_deadlines = [
        item["reservationExpiresAt"]
        for item in reserved_assignments
        if isinstance(item.get("reservationExpiresAt"), str)
    ]
    post_action_wait = (
        {
            "mode": "HOST_NATIVE_EVENT_OR_RESERVATION_DEADLINE",
            "interruptOn": [
                "NATIVE_RECEIVER_CLAIMED",
                "NATIVE_RECEIVER_COMPLETED",
                "NATIVE_RECEIVER_NEEDS_ATTENTION",
                "NATIVE_RECEIVER_START_FAILED",
            ],
            "onInterrupt": "CALL_GRAPH_FRONTIER_ONCE",
            "deadline": min(
                reservation_deadlines,
                key=lambda value: datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                ),
            ),
            "onDeadline": "CALL_GRAPH_FRONTIER_ONCE",
            "doNotPollBackToBack": True,
        }
        if reservation_deadlines
        else None
    )
    return {
        "rootId": root_id,
        "graphFingerprint": stored["graphFingerprint"],
        "policyVersion": DISPATCH_POLICY_VERSION,
        "binding": "HOST_NATIVE_DISPATCH_PLAN",
        "planFingerprint": fingerprint(plan_material),
        "assignments": reserved_assignments,
        "deferred": deferred,
        "nextAction": "CREATE_INDEPENDENT_RECEIVERS",
        **(
            {"postActionWait": post_action_wait}
            if post_action_wait is not None
            else {}
        ),
        "concurrentDispatchGroups": (
            [[item["nodeId"] for item in reserved_assignments]]
            if reserved_assignments
            else []
        ),
        "summary": {
            "frontierDispatchLoops": len(frontier_dispatch_node_ids),
            "dispatchable": len(reserved_assignments),
            "deferred": len(deferred),
            "concurrent": len(reserved_assignments) > 1,
        },
        "dispatchPolicy": {
            "maxConcurrentExecutors": max_concurrent_executors,
            "configurationSource": "PLUGIN_BUILT_IN",
            "agentCatalogVersion": agent_catalog["catalogVersion"],
            "agentCatalogFingerprint": agent_catalog[
                "catalogFingerprint"
            ],
            "agentCatalogConfigurationSource": agent_catalog[
                "configurationSource"
            ],
            "stateSnapshotSource": "GRAPH_FRONTIER",
            "parallelizeDisjointFrontier": True,
            "reuseExactMatchEvidence": True,
            "hostNativeOnly": True,
            "controllerAnalyzesLoopPayload": False,
            "reserveBeforeSpawn": True,
            "reservationSeconds": DISPATCH_RESERVATION_SECONDS,
            "toolStartsAgents": False,
            "toolClaimsLoops": False,
        },
    }


__all__ = (
    "DISPATCH_POLICY_VERSION",
    "MAX_CONCURRENT_EXECUTORS",
    "plan_dispatch_batch",
)
