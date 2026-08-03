from __future__ import annotations

from typing import Any

from .agent_discovery import discover_available_agents
from .dispatch_planning import preview_dispatch_routes
from .errors import fail
from .graph_model import LOOP_NODE_KINDS
from .orchestrator_config import OrchestratorConfig
from .repository import SchedulerRepository


def _role(node_kind: str) -> str:
    return (
        "DEVELOPMENT"
        if node_kind == "TASK_LOOP"
        else "INDEPENDENT_REVIEW"
    )


def _supports(agent: dict[str, Any], role: str) -> bool:
    capability = "development" if role == "DEVELOPMENT" else "review"
    return capability in agent["capabilities"]


def _candidate(
    agent: dict[str, Any],
) -> dict[str, Any]:
    return {
        "agentId": agent["id"],
        "agentDisplayName": agent["displayName"],
        "command": agent["command"],
        "model": dict(agent["model"]),
        "modelDisplayOnly": True,
        "modelAffectsRecommendation": False,
        "availabilityScope": "LOCAL_TERMINAL",
        "dispatchTransport": "EXTERNAL_PROCESS",
        "hostDispatchEligible": False,
    }


def _upstream_recommended_agents(
    *,
    node_id: str,
    incoming: dict[str, list[str]],
    recommendations_by_node: dict[str, dict[str, Any]],
) -> list[str]:
    pending = list(incoming.get(node_id, []))
    visited: set[str] = set()
    agent_ids: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        recommendation = recommendations_by_node.get(current)
        if (
            recommendation is not None
            and recommendation["role"] == "DEVELOPMENT"
        ):
            selected = recommendation.get("recommended")
            if selected is not None:
                agent_ids.add(selected["agentId"])
        pending.extend(incoming.get(current, []))
    return sorted(agent_ids)


def _ranked_agents(
    agents: list[dict[str, Any]],
    *,
    role: str,
    avoid: list[str],
) -> list[dict[str, Any]]:
    avoid_ids = set(avoid)
    eligible = [
        agent
        for agent in agents
        if _supports(agent, role)
    ]
    return sorted(
        eligible,
        key=lambda agent: (
            agent["id"] in avoid_ids,
            -agent["priority"],
            agent["model"]["id"] is None,
            agent["id"],
        ),
    )


def _reasons(
    *,
    selected: dict[str, Any] | None,
    role: str,
    avoid: list[str],
    independence_satisfied: bool | None,
) -> list[dict[str, str]]:
    if selected is None:
        return [
            {
                "code": "NO_ELIGIBLE_AGENT",
                "message": (
                    "No discovered local Agent advertises the required "
                    f"{role.lower()} capability."
                ),
            }
        ]
    reasons = [
        {
            "code": "LOCAL_AGENT_AVAILABLE",
            "message": (
                f"{selected['displayName']} is available through the local "
                f"{selected['command']} terminal command."
            ),
        },
        {
            "code": "ROLE_CAPABILITY_MATCH",
            "message": (
                f"The Agent advertises the capability required for {role}."
            ),
        },
    ]
    model_id = selected["model"]["id"]
    reasons.append(
        {
            "code": (
                "ACTIVE_MODEL_DISCOVERED"
                if model_id is not None
                else "ACTIVE_MODEL_UNRESOLVED"
            ),
            "message": (
                f"The currently configured model is {model_id}."
                if model_id is not None
                else (
                    "The terminal Agent is available, but its current model "
                    "ID cannot be discovered safely; current/default is shown."
                )
            ),
        }
    )
    if role == "INDEPENDENT_REVIEW":
        reasons.append(
            {
                "code": (
                    "REVIEW_AGENT_DIVERSE"
                    if independence_satisfied
                    else "REVIEW_AGENT_NOT_DIVERSE"
                ),
                "message": (
                    "The recommendation differs from the advisory upstream "
                    "executor recommendation."
                    if independence_satisfied
                    else (
                        "No different eligible local Agent is available; "
                        "context isolation is still required, but Agent "
                        "diversity is unsatisfied."
                    )
                ),
            }
        )
    if selected["priority"]:
        reasons.append(
            {
                "code": "USER_PROFILE_PRIORITY",
                "message": (
                    "The local user Profile priority contributed to ranking."
                ),
            }
        )
    elif not avoid:
        reasons.append(
            {
                "code": "STABLE_FALLBACK_ORDER",
                "message": (
                    "No explicit Profile priority distinguished candidates; "
                    "the deterministic Agent ID order was used."
                ),
            }
        )
    return reasons


def _confidence(
    *,
    selected: dict[str, Any] | None,
    independence_satisfied: bool | None,
    selection_ambiguous: bool,
) -> str:
    if selected is None:
        return "NONE"
    if independence_satisfied is False:
        return "LOW"
    if selected["model"]["id"] is None or selection_ambiguous:
        return "MEDIUM"
    return "HIGH"


def _selection_ambiguous(
    ranked: list[dict[str, Any]],
    *,
    avoid: list[str],
) -> bool:
    if len(ranked) < 2:
        return False
    avoid_ids = set(avoid)

    def evidence(agent: dict[str, Any]) -> tuple[bool, int, bool]:
        return (
            agent["id"] in avoid_ids,
            -agent["priority"],
            agent["model"]["id"] is None,
        )

    return evidence(ranked[0]) == evidence(ranked[1])


def recommend_graph_executors(
    graph: dict[str, Any],
    agents: list[dict[str, Any]],
    *,
    current_agent_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return non-binding executor advice without reading Loop payloads."""

    loop_nodes = [
        node
        for node in graph["nodes"]
        if node["kind"] in LOOP_NODE_KINDS
    ]
    incoming: dict[str, list[str]] = {
        node["id"]: []
        for node in graph["nodes"]
    }
    for edge in graph["edges"]:
        incoming[edge["target"]].append(edge["source"])

    tasks = sorted(
        (
            node
            for node in loop_nodes
            if node["kind"] == "TASK_LOOP"
        ),
        key=lambda node: node["id"],
    )
    reviews = sorted(
        (
            node
            for node in loop_nodes
            if node["kind"] != "TASK_LOOP"
        ),
        key=lambda node: (
            node["kind"] == "DELIVERY_REVIEW_LOOP",
            node["id"],
        ),
    )
    recommendations: list[dict[str, Any]] = []
    recommendations_by_node: dict[str, dict[str, Any]] = {}
    for node in [*tasks, *reviews]:
        role = _role(node["kind"])
        avoid = (
            _upstream_recommended_agents(
                node_id=node["id"],
                incoming=incoming,
                recommendations_by_node=recommendations_by_node,
            )
            if role == "INDEPENDENT_REVIEW"
            else list(current_agent_ids)
        )
        ranked = _ranked_agents(
            agents,
            role=role,
            avoid=avoid,
        )
        selected = ranked[0] if ranked else None
        independence_satisfied = (
            selected is not None and selected["id"] not in set(avoid)
            if role == "INDEPENDENT_REVIEW"
            else None
        )
        recommendation = {
            "nodeId": node["id"],
            "kind": node["kind"],
            "workItemId": node["workItemId"],
            "role": role,
            "binding": "ADVISORY",
            "dispatchAllowed": False,
            "recommended": (
                _candidate(selected)
                if selected is not None
                else None
            ),
            "alternatives": [
                _candidate(agent)
                for agent in ranked[1:4]
            ],
            "confidence": _confidence(
                selected=selected,
                independence_satisfied=independence_satisfied,
                selection_ambiguous=_selection_ambiguous(
                    ranked,
                    avoid=avoid,
                ),
            ),
            "reasons": _reasons(
                selected=selected,
                role=role,
                avoid=avoid,
                independence_satisfied=independence_satisfied,
            ),
            "independence": (
                {
                    "required": True,
                    "satisfied": independence_satisfied,
                    "advisoryUpstreamAgentIds": avoid,
                }
                if role == "INDEPENDENT_REVIEW"
                else {
                    "required": False,
                    "satisfied": None,
                    "advisoryUpstreamAgentIds": [],
                }
            ),
        }
        recommendations.append(recommendation)
        recommendations_by_node[node["id"]] = recommendation
    return {
        "recommendations": sorted(
            recommendations,
            key=lambda recommendation: recommendation["nodeId"],
        ),
        "summary": {
            "loopRecommendations": len(recommendations),
            "unavailable": sum(
                recommendation["recommended"] is None
                for recommendation in recommendations
            ),
            "reviewIndependenceUnsatisfied": sum(
                recommendation["role"] == "INDEPENDENT_REVIEW"
                and not recommendation["independence"]["satisfied"]
                for recommendation in recommendations
            ),
        },
    }


def available_agents(
    *,
    root: str,
    explicit_dogfood: bool = False,
    **_: Any,
) -> dict[str, Any]:
    """Return live local discovery without reading or writing Graph state."""

    del root, explicit_dogfood
    return discover_available_agents()


def recommend_executors(
    *,
    root: str,
    root_id: str,
    recommendation_mode: str,
    executor_inventory: list[dict[str, Any]] | None = None,
    node_requirements: list[dict[str, Any]] | None = None,
    current_executor: dict[str, str] | None = None,
    host_native_agent_ids: tuple[str, ...] | None = None,
    host_adapter_id: str | None = None,
    orchestrator_config: OrchestratorConfig | None = None,
    explicit_dogfood: bool = False,
    **_: Any,
) -> dict[str, Any]:
    """Recommend current-host native routes for automatic execution."""

    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    execution_mode = (
        repository.run(root_id)["executionMode"]
        if stored["status"] != "PREPARED"
        else None
    )
    if recommendation_mode == "AUTOMATIC":
        if executor_inventory is None or node_requirements is None:
            fail(
                "SCHEDULER_AUTOMATIC_RECOMMENDATION_INPUT_REQUIRED",
                "Automatic recommendations require host-native inventory and reasoning requirements",
            )
        preview = preview_dispatch_routes(
            graph=stored["graph"],
            graph_fingerprint=stored["graphFingerprint"],
            executor_inventory=executor_inventory,
            node_requirements=node_requirements,
            current_executor=current_executor,
            host_native_agent_ids=host_native_agent_ids,
            host_adapter_id=host_adapter_id,
            orchestrator_config=orchestrator_config,
        )
        recommendations = [
            {
                "nodeId": assignment["nodeId"],
                "kind": assignment["kind"],
                "workItemId": assignment["workItemId"],
                "role": assignment["role"],
                "binding": "HOST_NATIVE_DISPATCH_PREVIEW",
                "dispatchAllowed": False,
                "recommended": {
                    "agentId": assignment["agent"]["id"],
                    "adapterId": assignment["agent"]["adapterId"],
                    "agentDisplayName": assignment["agent"]["displayName"],
                    "model": dict(assignment["model"]),
                    "availabilityScope": "CURRENT_HOST_NATIVE",
                    "dispatchTransport": assignment["dispatchTransport"],
                    "hostDispatchEligible": True,
                },
                "alternatives": [],
                "confidence": "HIGH",
                "reasoningClass": assignment["reasoningClass"],
                "reasoningRequirement": dict(
                    assignment["reasoningRequirement"]
                ),
                "decisionFingerprint": assignment[
                    "decisionFingerprint"
                ],
                "reasons": list(assignment["reasons"]),
                "independence": dict(assignment["independence"]),
            }
            for assignment in preview["assignments"]
        ]
        return {
            "rootId": root_id,
            "graphFingerprint": stored["graphFingerprint"],
            **(
                {"executionMode": execution_mode}
                if execution_mode is not None
                else {}
            ),
            "previewFingerprint": preview["previewFingerprint"],
            "recommendations": sorted(
                recommendations,
                key=lambda item: item["nodeId"],
            ),
            "deferred": preview["deferred"],
            "summary": preview["summary"],
            "recommendationPolicy": {
                "mode": "AUTOMATIC",
                "binding": "HOST_NATIVE_DISPATCH_PREVIEW",
                "dispatchAllowed": False,
                "automaticDispatchAllowed": True,
                "use": "AUTOMATIC_DISPATCH_PREVIEW",
                "selectionScope": "CURRENT_EXECUTION_AGENT_ONLY",
                "crossAgentRecommendationAllowed": False,
                "hostInventoryEligible": True,
                "dispatchTransport": "HOST_NATIVE",
                "persisted": False,
                "payloadInterpreted": False,
                "actualModelAffectsRecommendation": False,
                "mayChangeAtDispatch": True,
            },
        }
    fail(
        "SCHEDULER_RECOMMENDATION_MODE_INVALID",
        "recommendation_mode must be AUTOMATIC; manual handoff does not "
        "select an Agent or model",
    )


__all__ = (
    "available_agents",
    "recommend_executors",
    "recommend_graph_executors",
)
