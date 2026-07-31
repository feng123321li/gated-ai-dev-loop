from __future__ import annotations

import re
from typing import Any

from .dispatch_contracts import (
    ANALYZED_DISPATCH_REASONING_CLASSES,
    DISPATCH_POLICY_VERSION,
    DISPATCH_TRANSPORTS,
    HOST_NATIVE_DISPATCH_TRANSPORT,
    automatic_dispatch_decision_fingerprint,
    dispatch_model_selection,
)
from .errors import fail
from .graph_frontier import get_graph_frontier
from .jsonio import fingerprint
from .repository import SchedulerRepository, timestamp


DISPATCH_RESERVATION_SECONDS = 300


MODEL_TIERS = ("EFFICIENT", "BALANCED", "FRONTIER")
EXECUTOR_CAPABILITIES = frozenset({"development", "review"})
REASONING_SOURCES = frozenset(
    {"PLANNING", "USER_POLICY", "LOOP_POLICY"}
)
SAFE_EXECUTOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$")
SAFE_MODEL_TEXT = re.compile(r"^[^\x00-\x1f\x7f-\x9f]{1,256}$")


def _safe_text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or SAFE_MODEL_TEXT.fullmatch(value.strip()) is None
    ):
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field} must be safe non-empty text",
        )
    return value.strip()


def _safe_executor_id(value: object, field: str) -> str:
    normalized = _safe_text(value, field)
    if SAFE_EXECUTOR_ID.fullmatch(normalized) is None:
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field} contains unsupported characters",
        )
    return normalized


def _priority(value: object, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not -100 <= value <= 100
    ):
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field} must be an integer from -100 through 100",
        )
    return value


def _validate_model(
    value: object,
    *,
    field: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field} must be an object",
        )
    allowed = {
        "id",
        "family",
        "tier",
        "reasoningEffort",
        "priority",
    }
    required = {"id", "tier", "priority"}
    if set(value) - allowed or not required <= set(value):
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field} fields are invalid",
        )
    tier = value["tier"]
    if tier not in MODEL_TIERS:
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field}.tier is invalid",
        )
    family = value.get("family")
    reasoning_effort = value.get("reasoningEffort")
    return {
        "id": _safe_text(value["id"], f"{field}.id"),
        "family": (
            _safe_text(family, f"{field}.family")
            if family is not None
            else None
        ),
        "tier": tier,
        "reasoningEffort": (
            _safe_text(
                reasoning_effort,
                f"{field}.reasoningEffort",
            )
            if reasoning_effort is not None
            else None
        ),
        "priority": _priority(
            value["priority"],
            f"{field}.priority",
        ),
    }


def _validate_executor(
    value: object,
    *,
    field: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field} must be an object",
        )
    allowed = {
        "agentId",
        "displayName",
        "dispatchTransport",
        "capabilities",
        "availableSlots",
        "priority",
        "modelOverrideSupported",
        "models",
    }
    if set(value) != allowed:
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field} fields are invalid",
        )
    dispatch_transport = value["dispatchTransport"]
    if dispatch_transport not in DISPATCH_TRANSPORTS:
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field}.dispatchTransport is invalid",
        )
    capabilities = value["capabilities"]
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or len(set(capabilities)) != len(capabilities)
        or any(
            capability not in EXECUTOR_CAPABILITIES
            for capability in capabilities
        )
    ):
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field}.capabilities are invalid",
        )
    available_slots = value["availableSlots"]
    if (
        isinstance(available_slots, bool)
        or not isinstance(available_slots, int)
        or not 0 <= available_slots <= 64
    ):
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field}.availableSlots must be from 0 through 64",
        )
    if not isinstance(value["modelOverrideSupported"], bool):
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field}.modelOverrideSupported must be a boolean",
        )
    raw_models = value["models"]
    if not isinstance(raw_models, list) or not raw_models:
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field}.models must be a non-empty array",
        )
    models = [
        _validate_model(model, field=f"{field}.models[{index}]")
        for index, model in enumerate(raw_models)
    ]
    if len({model["id"] for model in models}) != len(models):
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            f"{field}.models must have unique IDs",
        )
    return {
        "agentId": _safe_executor_id(
            value["agentId"],
            f"{field}.agentId",
        ),
        "displayName": _safe_text(
            value["displayName"],
            f"{field}.displayName",
        ),
        "dispatchTransport": dispatch_transport,
        "capabilities": sorted(capabilities),
        "availableSlots": available_slots,
        "priority": _priority(value["priority"], f"{field}.priority"),
        "modelOverrideSupported": value["modelOverrideSupported"],
        "models": sorted(models, key=lambda model: model["id"]),
    }


def _validate_inventory(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            "executor_inventory must be a non-empty array",
        )
    executors = [
        _validate_executor(executor, field=f"executor_inventory[{index}]")
        for index, executor in enumerate(value)
    ]
    if len({executor["agentId"] for executor in executors}) != len(
        executors
    ):
        fail(
            "SCHEDULER_EXECUTOR_INVENTORY_INVALID",
            "executor_inventory must have unique agent IDs",
        )
    return sorted(executors, key=lambda executor: executor["agentId"])


def _validate_node_requirements(
    value: object,
) -> dict[str, dict[str, str]]:
    if not isinstance(value, list):
        fail(
            "SCHEDULER_DISPATCH_REQUIREMENT_INVALID",
            "node_requirements must be an array",
        )
    requirements: dict[str, dict[str, str]] = {}
    for index, raw in enumerate(value):
        field = f"node_requirements[{index}]"
        if not isinstance(raw, dict) or set(raw) != {
            "nodeId",
            "reasoningClass",
            "source",
            "reason",
        }:
            fail(
                "SCHEDULER_DISPATCH_REQUIREMENT_INVALID",
                f"{field} fields are invalid",
            )
        node_id = _safe_executor_id(raw["nodeId"], f"{field}.nodeId")
        reasoning_class = raw["reasoningClass"]
        source = raw["source"]
        if (
            reasoning_class not in ANALYZED_DISPATCH_REASONING_CLASSES
            or source not in REASONING_SOURCES
            or node_id in requirements
        ):
            fail(
                "SCHEDULER_DISPATCH_REQUIREMENT_INVALID",
                f"{field} is invalid or duplicated",
            )
        requirements[node_id] = {
            "nodeId": node_id,
            "reasoningClass": reasoning_class,
            "source": source,
            "reason": _safe_text(raw["reason"], f"{field}.reason"),
        }
    return requirements


def _validate_current_executor(
    value: object,
    *,
    executors: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "agentId",
        "modelId",
    }:
        fail(
            "SCHEDULER_CURRENT_EXECUTOR_INVALID",
            "current_executor must contain exactly agentId and modelId",
        )
    raw_agent_id = value["agentId"]
    raw_model_id = value["modelId"]
    if (
        not isinstance(raw_agent_id, str)
        or SAFE_EXECUTOR_ID.fullmatch(raw_agent_id.strip()) is None
        or not isinstance(raw_model_id, str)
        or SAFE_MODEL_TEXT.fullmatch(raw_model_id.strip()) is None
    ):
        fail(
            "SCHEDULER_CURRENT_EXECUTOR_INVALID",
            "current_executor Agent and model IDs must be safe text",
        )
    agent_id = raw_agent_id.strip()
    model_id = raw_model_id.strip()
    executor = next(
        (
            candidate
            for candidate in executors
            if candidate["agentId"] == agent_id
        ),
        None,
    )
    if executor is None:
        fail(
            "SCHEDULER_CURRENT_EXECUTOR_NOT_IN_INVENTORY",
            "The host-reported current Agent is absent from inventory",
            agentId=agent_id,
        )
    model = next(
        (
            candidate
            for candidate in executor["models"]
            if candidate["id"] == model_id
        ),
        None,
    )
    if model is None:
        fail(
            "SCHEDULER_CURRENT_EXECUTOR_NOT_IN_INVENTORY",
            (
                "The host-reported current model is absent from the "
                "current Agent inventory"
            ),
            agentId=agent_id,
            modelId=model_id,
        )
    return executor, model


def _role(node_kind: str) -> str:
    return (
        "DEVELOPMENT"
        if node_kind == "TASK_LOOP"
        else "INDEPENDENT_REVIEW"
    )


def _required_capability(role: str) -> str:
    return "development" if role == "DEVELOPMENT" else "review"


def _desired_model_tier(reasoning_class: str) -> str:
    return "FRONTIER" if reasoning_class == "HIGH" else "BALANCED"


def _select_model(
    executor: dict[str, Any],
    *,
    desired_tier: str,
) -> dict[str, Any]:
    desired_rank = MODEL_TIERS.index(desired_tier)
    return min(
        executor["models"],
        key=lambda model: (
            abs(MODEL_TIERS.index(model["tier"]) - desired_rank),
            -MODEL_TIERS.index(model["tier"]),
            -model["priority"],
            model["id"],
        ),
    )


def _incoming_edges(graph: dict[str, Any]) -> dict[str, list[str]]:
    incoming = {node["id"]: [] for node in graph["nodes"]}
    for edge in graph["edges"]:
        incoming[edge["target"]].append(edge["source"])
    return incoming


def _upstream_actual_executors(
    *,
    node_id: str,
    incoming: dict[str, list[str]],
    states: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    pending = list(incoming.get(node_id, []))
    visited: set[str] = set()
    agent_ids: set[str] = set()
    model_ids: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        state = states.get(current, {})
        agent_id = state.get("agentId")
        model_id = state.get("modelId")
        if isinstance(agent_id, str):
            agent_ids.add(agent_id)
        if isinstance(model_id, str):
            model_ids.add(model_id)
        pending.extend(incoming.get(current, []))
    return sorted(agent_ids), sorted(model_ids)


def _model_families(
    model_ids: list[str],
    executors: list[dict[str, Any]],
) -> list[str]:
    wanted = set(model_ids)
    return sorted(
        {
            model["family"] or model["id"]
            for executor in executors
            for model in executor["models"]
            if model["id"] in wanted
        }
    )


def _deferred_code(
    executors: list[dict[str, Any]],
    *,
    capability: str,
    remaining_slots: dict[str, int],
    reasoning_class: str,
) -> tuple[str, str]:
    capable_any_transport = [
        executor
        for executor in executors
        if capability in executor["capabilities"]
    ]
    capable = [
        executor
        for executor in capable_any_transport
        if (
            executor["dispatchTransport"]
            == HOST_NATIVE_DISPATCH_TRANSPORT
        )
    ]
    if not capable:
        if capable_any_transport:
            return (
                "UNSAFE_EXECUTOR_TRANSPORT",
                (
                    "Compatible Agents are available only through an "
                    "external process, CLI, or companion bridge; automatic "
                    "dispatch requires the host-native Agent API."
                ),
            )
        return (
            "NO_COMPATIBLE_HOST_EXECUTOR",
            "No host-native Agent advertises the required capability.",
        )
    override_capable = [
        executor
        for executor in capable
        if executor["modelOverrideSupported"]
    ]
    if not override_capable:
        return (
            "MODEL_OVERRIDE_UNAVAILABLE",
            (
                "Compatible Agents cannot guarantee an explicit child "
                "model override."
            ),
        )
    if reasoning_class == "HIGH" and not any(
        any(model["tier"] == "FRONTIER" for model in executor["models"])
        for executor in override_capable
    ):
        return (
            "NO_HIGH_REASONING_MODEL",
            (
                "Compatible Agents expose no FRONTIER model for the "
                "high-reasoning requirement."
            ),
        )
    if not any(
        remaining_slots[executor["agentId"]] > 0
        for executor in override_capable
    ):
        return (
            "NO_HOST_EXECUTOR_CAPACITY",
            "Compatible host-native Agents have no available slots.",
        )
    return (
        "NO_SELECTABLE_MODEL",
        "Compatible host-native Agents expose no selectable model.",
    )


def _assignment(
    *,
    node: dict[str, Any],
    role: str,
    executor: dict[str, Any],
    model: dict[str, Any],
    graph_fingerprint: str,
    upstream_agents: list[str],
    upstream_models: list[str],
    upstream_model_families: list[str],
    reasoning_requirement: dict[str, str],
    routing_basis: str,
) -> dict[str, Any]:
    agent_diverse = (
        executor["agentId"] not in set(upstream_agents)
        if role == "INDEPENDENT_REVIEW" and upstream_agents
        else None
    )
    model_identity = model["family"] or model["id"]
    model_diverse = (
        model_identity not in set(upstream_model_families)
        if role == "INDEPENDENT_REVIEW" and upstream_model_families
        else (
            model["id"] not in set(upstream_models)
            if role == "INDEPENDENT_REVIEW" and upstream_models
            else None
        )
    )
    if role != "INDEPENDENT_REVIEW":
        diversity_level = "NOT_APPLICABLE"
    elif agent_diverse is True and model_diverse is True:
        diversity_level = "AGENT_AND_MODEL_DIVERSE"
    elif agent_diverse is True:
        diversity_level = "AGENT_DIVERSE"
    elif model_diverse is True:
        diversity_level = "MODEL_DIVERSE"
    else:
        diversity_level = "CONTEXT_ONLY"
    reasoning_class = reasoning_requirement["reasoningClass"]
    model_selection = dispatch_model_selection(reasoning_class)
    if routing_basis == "CURRENT_EXECUTOR_FALLBACK":
        routing_reasons = [
            {
                "code": "CURRENT_EXECUTOR_FALLBACK",
                "message": (
                    "Agent analysis is unavailable, so this route uses "
                    "the exact current Agent and model reported by the "
                    "host."
                ),
            },
            {
                "code": "REASONING_UNCLASSIFIED",
                "message": (
                    "No STANDARD or HIGH classification is inferred by "
                    "the controller."
                ),
            },
        ]
    else:
        routing_reasons = [
            {
                "code": "EXPLICIT_MODEL_TIER_MATCH",
                "message": (
                    f"{model['id']} is the best available "
                    f"{_desired_model_tier(reasoning_class).lower()} "
                    "model-tier match."
                ),
            },
            {
                "code": "REASONING_REQUIREMENT_APPLIED",
                "message": (
                    f"{reasoning_class} reasoning was requested by "
                    f"{reasoning_requirement['source']}."
                ),
            },
        ]
    return {
        "nodeId": node["id"],
        "kind": node["kind"],
        "workItemId": node["workItemId"],
        "role": role,
        "reasoningClass": reasoning_class,
        "reasoningRequirement": dict(reasoning_requirement),
        "routingBasis": routing_basis,
        "agent": {
            "id": executor["agentId"],
            "displayName": executor["displayName"],
        },
        "dispatchTransport": executor["dispatchTransport"],
        "model": {
            "id": model["id"],
            "family": model["family"],
            "tier": model["tier"],
            "reasoningEffort": model["reasoningEffort"],
        },
        "modelSelection": model_selection,
        "hostDispatchAllowed": True,
        "contextInput": {
            "rootId": None,
            "nodeId": node["id"],
        },
        "decisionFingerprint": (
            automatic_dispatch_decision_fingerprint(
                graph_fingerprint=graph_fingerprint,
                node_id=node["id"],
                agent_id=executor["agentId"],
                model_id=model["id"],
                reasoning_class=reasoning_class,
                dispatch_transport=executor["dispatchTransport"],
            )
        ),
        "reasons": [
            {
                "code": "HOST_NATIVE_AGENT_CAPABLE",
                "message": (
                    "The host-native Agent advertises the required "
                    f"{_required_capability(role)} capability."
                ),
            },
            *routing_reasons,
            *(
                [
                    {
                        "code": (
                            "REVIEW_HETEROGENEOUS_INDEPENDENCE_"
                            "UNSATISFIED"
                        ),
                        "message": (
                            "The safe host-native route provides an "
                            "isolated Review context but not both a "
                            "different Agent and model family."
                        ),
                    }
                ]
                if (
                    role == "INDEPENDENT_REVIEW"
                    and diversity_level != "AGENT_AND_MODEL_DIVERSE"
                )
                else []
            ),
        ],
        "independence": {
            "required": role == "INDEPENDENT_REVIEW",
            "agentDiverse": agent_diverse,
            "modelDiverse": model_diverse,
            "diversityLevel": diversity_level,
            "upstreamAgentIds": upstream_agents,
            "upstreamModelIds": upstream_models,
            "upstreamModelFamilies": upstream_model_families,
        },
    }


def plan_dispatch_batch(
    *,
    root: str,
    root_id: str,
    expected_graph_fingerprint: str,
    executor_inventory: list[dict[str, Any]],
    node_requirements: list[dict[str, Any]],
    current_executor: dict[str, str] | None = None,
    host_native_agent_ids: tuple[str, ...] | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Plan and reserve one concurrent host-native dispatch batch."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    if expected_graph_fingerprint != stored["graphFingerprint"]:
        fail(
            "SCHEDULER_GRAPH_FINGERPRINT_MISMATCH",
            "The expected Graph fingerprint is stale",
            expectedGraphFingerprint=expected_graph_fingerprint,
            actualGraphFingerprint=stored["graphFingerprint"],
        )
    executors = _validate_inventory(executor_inventory)
    if host_native_agent_ids is not None:
        allowed_native_agents = set(host_native_agent_ids)
        unsupported_native_agents = sorted(
            executor["agentId"]
            for executor in executors
            if (
                executor["dispatchTransport"]
                == HOST_NATIVE_DISPATCH_TRANSPORT
                and executor["agentId"] not in allowed_native_agents
            )
        )
        if unsupported_native_agents:
            fail(
                "SCHEDULER_HOST_NATIVE_INVENTORY_MISMATCH",
                "Host-native inventory contains Agents this MCP client "
                "cannot create through its native Agent API",
                supportedAgentIds=sorted(allowed_native_agents),
                unsupportedAgentIds=unsupported_native_agents,
            )
    requirements = _validate_node_requirements(node_requirements)
    fallback_executor = _validate_current_executor(
        current_executor,
        executors=executors,
    )
    frontier = get_graph_frontier(
        root=root,
        root_id=root_id,
        explicit_dogfood=explicit_dogfood,
        now=now,
    )
    run = repository.run(root_id)
    if run["executionMode"] != "active":
        fail(
            "SCHEDULER_AUTO_DISPATCH_DISABLED",
            "Host-native automatic dispatch requires active execution mode",
            executionMode=run["executionMode"],
        )
    if run.get("hostCapacity") is not None:
        fail(
            "SCHEDULER_HOST_CAPACITY_EXHAUSTED",
            "Automatic dispatch is paused by the host capacity breaker",
            **run["hostCapacity"],
        )
    observation_at = max(timestamp(now), run["updatedAt"])
    with repository.read() as connection:
        for executor in executors:
            if (
                executor["dispatchTransport"]
                != HOST_NATIVE_DISPATCH_TRANSPORT
            ):
                continue
            shared_breaker = repository.open_host_capacity_breaker(
                connection,
                agent_id=executor["agentId"],
                at=observation_at,
            )
            if shared_breaker is not None:
                fail(
                    "SCHEDULER_HOST_CAPACITY_EXHAUSTED",
                    "Automatic dispatch is paused by a shared host capacity breaker",
                    **shared_breaker,
                )
    graph = stored["graph"]
    definitions = {node["id"]: node for node in graph["nodes"]}
    states = {node["nodeId"]: node for node in run["nodes"]}
    incoming = _incoming_edges(graph)
    dispatch_node_ids = sorted(
        action["nodeId"]
        for action in frontier["actions"]
        if action["action"] == "DISPATCH_LOOP"
    )
    reserved_actions = {
        action["nodeId"]: action
        for action in frontier["actions"]
        if action["action"] == "WAIT_FOR_DISPATCH_RECEIVER"
    }
    eligible_requirement_ids = (
        set(dispatch_node_ids) | set(reserved_actions)
    )
    stale_requirements = set(requirements) - eligible_requirement_ids
    if stale_requirements:
        fail(
            "SCHEDULER_DISPATCH_REQUIREMENT_STALE",
            "node_requirements must target the current dispatch frontier",
            nodeIds=sorted(stale_requirements),
        )
    missing_requirements = set(dispatch_node_ids) - set(requirements)
    if missing_requirements and fallback_executor is None:
        fail(
            "SCHEDULER_DISPATCH_REQUIREMENT_MISSING",
            (
                "Every current dispatch Loop requires an Agent-provided "
                "reasoning analysis"
            ),
            nodeIds=sorted(missing_requirements),
        )
    remaining_slots = {
        executor["agentId"]: executor["availableSlots"]
        for executor in executors
    }
    assignments: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = [
        {
            "nodeId": node_id,
            "code": "DISPATCH_ALREADY_RESERVED",
            "message": (
                "Another dispatcher already reserved this Loop for host "
                "Agent creation."
            ),
            "dispatchReservationId": action[
                "dispatchReservationId"
            ],
            "reservationExpiresAt": action["reservationExpiresAt"],
            "claimCreated": False,
        }
        for node_id, action in sorted(reserved_actions.items())
    ]
    routing_node_ids = sorted(
        dispatch_node_ids,
        key=lambda node_id: (
            node_id in requirements,
            node_id,
        ),
    )
    for node_id in routing_node_ids:
        node = definitions[node_id]
        role = _role(node["kind"])
        capability = _required_capability(role)
        reasoning_requirement = requirements.get(node_id)
        routing_basis = "AGENT_ANALYSIS"
        if reasoning_requirement is None:
            reasoning_requirement = {
                "nodeId": node_id,
                "reasoningClass": "UNCLASSIFIED",
                "source": "CURRENT_EXECUTOR_FALLBACK",
                "reason": (
                    "Agent analysis unavailable; use the exact current "
                    "Agent and model reported by the host."
                ),
            }
            routing_basis = "CURRENT_EXECUTOR_FALLBACK"
        reasoning_class = reasoning_requirement["reasoningClass"]
        upstream_agents, upstream_models = _upstream_actual_executors(
            node_id=node_id,
            incoming=incoming,
            states=states,
        )
        upstream_model_families = _model_families(
            upstream_models,
            executors,
        )
        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        if routing_basis == "CURRENT_EXECUTOR_FALLBACK":
            executor, model = fallback_executor
            if (
                executor["dispatchTransport"]
                == HOST_NATIVE_DISPATCH_TRANSPORT
                and capability in executor["capabilities"]
                and remaining_slots[executor["agentId"]] > 0
            ):
                candidates.append((executor, model))
        else:
            for executor in executors:
                if (
                    executor["dispatchTransport"]
                    != HOST_NATIVE_DISPATCH_TRANSPORT
                    or capability not in executor["capabilities"]
                    or not executor["modelOverrideSupported"]
                    or remaining_slots[executor["agentId"]] <= 0
                    or (
                        reasoning_class == "HIGH"
                        and not any(
                            model["tier"] == "FRONTIER"
                            for model in executor["models"]
                        )
                    )
                ):
                    continue
                model = _select_model(
                    executor,
                    desired_tier=_desired_model_tier(reasoning_class),
                )
                candidates.append((executor, model))
        if not candidates:
            if routing_basis == "CURRENT_EXECUTOR_FALLBACK":
                executor, _ = fallback_executor
                if (
                    executor["dispatchTransport"]
                    != HOST_NATIVE_DISPATCH_TRANSPORT
                ):
                    code = "CURRENT_EXECUTOR_UNSAFE_TRANSPORT"
                    message = (
                        "The current Agent was reported through an "
                        "external process transport; automatic fallback "
                        "requires the host-native Agent API."
                    )
                elif capability not in executor["capabilities"]:
                    code = "CURRENT_EXECUTOR_CAPABILITY_MISMATCH"
                    message = (
                        "The current host Agent does not advertise the "
                        f"required {capability} capability."
                    )
                else:
                    code = "CURRENT_EXECUTOR_NO_CAPACITY"
                    message = (
                        "The current host Agent has no available child "
                        "execution slot."
                    )
            else:
                code, message = _deferred_code(
                    executors,
                    capability=capability,
                    remaining_slots=remaining_slots,
                    reasoning_class=reasoning_class,
                )
            deferred.append(
                {
                    "nodeId": node_id,
                    "kind": node["kind"],
                    "workItemId": node["workItemId"],
                    "code": code,
                    "message": message,
                    "claimCreated": False,
                }
            )
            continue
        candidates.sort(
            key=lambda candidate: (
                (
                    candidate[0]["agentId"] in set(upstream_agents)
                    if role == "INDEPENDENT_REVIEW"
                    else False
                ),
                (
                    (
                        candidate[1]["family"]
                        or candidate[1]["id"]
                    )
                    in set(upstream_model_families)
                    if role == "INDEPENDENT_REVIEW"
                    else False
                ),
                -candidate[0]["priority"],
                -candidate[1]["priority"],
                candidate[0]["agentId"],
                candidate[1]["id"],
            )
        )
        executor, model = candidates[0]
        remaining_slots[executor["agentId"]] -= 1
        assignment = _assignment(
            node=node,
            role=role,
            executor=executor,
            model=model,
            graph_fingerprint=stored["graphFingerprint"],
            upstream_agents=upstream_agents,
            upstream_models=upstream_models,
            upstream_model_families=upstream_model_families,
            reasoning_requirement=reasoning_requirement,
            routing_basis=routing_basis,
        )
        assignment["contextInput"]["rootId"] = root_id
        assignments.append(assignment)
    reservations = repository.reserve_dispatch_assignments(
        root_id=root_id,
        graph_fingerprint=stored["graphFingerprint"],
        assignments=assignments,
        agent_slot_limits={
            executor["agentId"]: executor["availableSlots"]
            for executor in executors
        },
        reservation_seconds=DISPATCH_RESERVATION_SECONDS,
    )
    reserved_assignments: list[dict[str, Any]] = []
    for assignment in assignments:
        node_id = assignment["nodeId"]
        reservation = reservations["accepted"].get(node_id)
        if reservation is not None:
            assignment.update(reservation)
            assignment["contextInput"]["dispatchReservationId"] = (
                reservation["dispatchReservationId"]
            )
            reserved_assignments.append(assignment)
            continue
        rejection = reservations["rejected"][node_id]
        deferred.append(
            {
                "nodeId": node_id,
                "kind": assignment["kind"],
                "workItemId": assignment["workItemId"],
                **rejection,
                "claimCreated": False,
            }
        )
    assignments = reserved_assignments
    plan_material = {
        "policyVersion": DISPATCH_POLICY_VERSION,
        "rootId": root_id,
        "graphFingerprint": stored["graphFingerprint"],
        "dispatchNodeIds": dispatch_node_ids,
        "inventory": executors,
        "nodeRequirements": [
            requirements[node_id]
            for node_id in sorted(requirements)
        ],
        "currentExecutor": (
            {
                "agentId": fallback_executor[0]["agentId"],
                "modelId": fallback_executor[1]["id"],
            }
            if fallback_executor is not None
            else None
        ),
        "assignments": assignments,
        "deferred": deferred,
    }
    return {
        "rootId": root_id,
        "graphFingerprint": stored["graphFingerprint"],
        "policyVersion": DISPATCH_POLICY_VERSION,
        "binding": "HOST_NATIVE_DISPATCH_PLAN",
        "planFingerprint": fingerprint(plan_material),
        "assignments": assignments,
        "deferred": deferred,
        "concurrentDispatchGroups": (
            [[assignment["nodeId"] for assignment in assignments]]
            if assignments
            else []
        ),
        "summary": {
            "frontierDispatchLoops": len(dispatch_node_ids),
            "dispatchable": len(assignments),
            "deferred": len(deferred),
            "concurrent": len(assignments) > 1,
        },
        "dispatchPolicy": {
            "hostNativeOnly": True,
            "externalProcessLaunchAllowed": False,
            "companionScriptLaunchAllowed": False,
            "analyzedRoutesRequireExplicitModelOverride": True,
            "currentExecutorFallbackAllowed": True,
            "fallbackModelSelection": "CURRENT_HOST_DEFAULT",
            "fallbackReasoningClass": "UNCLASSIFIED",
            "spawnBeforeClaim": True,
            "reserveBeforeSpawn": True,
            "reservationSeconds": DISPATCH_RESERVATION_SECONDS,
            "toolStartsAgents": False,
            "toolClaimsLoops": False,
            "parallelizeCurrentGroup": True,
            "childContext": "ROOT_ID_NODE_ID_AND_DECISION_ONLY",
            "reasoningClassSource": (
                "HOST_AGENT_ANALYSIS_OR_CURRENT_EXECUTOR_FALLBACK"
            ),
            "controllerAnalyzesLoopPayload": False,
            "quotaRecoveryUsesPlan": False,
        },
    }


__all__ = (
    "DISPATCH_POLICY_VERSION",
    "plan_dispatch_batch",
)
