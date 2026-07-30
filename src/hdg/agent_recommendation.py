from __future__ import annotations

from typing import Any

from .agent_discovery import discover_available_agents
from .graph_model import LOOP_NODE_KINDS
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
            else []
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
    explicit_dogfood: bool = False,
    **_: Any,
) -> dict[str, Any]:
    """Recommend one local Agent/model for every Graph Loop."""

    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    discovery = discover_available_agents()
    recommendation = recommend_graph_executors(
        stored["graph"],
        discovery["agents"],
    )
    return {
        "rootId": root_id,
        "graphFingerprint": stored["graphFingerprint"],
        "discoveryFingerprint": discovery.get("discoveryFingerprint"),
        "availableAgents": discovery["agents"],
        "discoveryWarnings": discovery.get("warnings", []),
        **recommendation,
        "recommendationPolicy": {
            "binding": "ADVISORY",
            "dispatchAllowed": False,
            "persisted": False,
            "payloadInterpreted": False,
        },
    }


__all__ = (
    "available_agents",
    "recommend_executors",
    "recommend_graph_executors",
)
