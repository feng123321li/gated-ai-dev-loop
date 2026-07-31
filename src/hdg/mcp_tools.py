from __future__ import annotations

from copy import deepcopy
from typing import Any

from .controller import (
    CONTROLLER_OPERATIONS,
    ControllerContext,
    DEFAULT_CONTROLLER,
    LayeredDeliveryController,
)
from .errors import GatedLoopError, fail
from .hierarchy_contract import hierarchy_input_schema
from .jsonio import canonical_json
from .model_core import validate_hierarchy_definition


def _object(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _string(description: str) -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "description": description,
    }


ROOT_ID = _string("Frozen Delivery and Graph run ID.")
NODE_ID = _string("Exact graph node ID from graph_frontier.")
OPERATION_ID = _string("Globally unique Loop operation ID.")
FINGERPRINT = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "description": "Exact SHA-256 fingerprint returned by the controller.",
}

HOST_MODEL = _object(
    {
        "id": _string("Model ID accepted by the host-native Agent."),
        "family": _string(
            "Optional model family used for Review diversity."
        ),
        "tier": {
            "type": "string",
            "enum": ["EFFICIENT", "BALANCED", "FRONTIER"],
        },
        "reasoningEffort": _string(
            "Optional host-native reasoning effort override."
        ),
        "priority": {
            "type": "integer",
            "minimum": -100,
            "maximum": 100,
        },
    },
    required=["id", "tier", "priority"],
)

HOST_EXECUTOR = _object(
    {
        "agentId": _string("Host-native Agent ID."),
        "displayName": _string("Host-native Agent display name."),
        "dispatchTransport": {
            "type": "string",
            "enum": ["HOST_NATIVE", "EXTERNAL_PROCESS"],
            "description": (
                "HOST_NATIVE means the current host creates the child "
                "through its built-in Agent API with normal sandbox and "
                "approval enforcement. EXTERNAL_PROCESS includes CLI, "
                "exec, subprocess, and companion-script bridges and is "
                "never eligible for automatic dispatch."
            ),
        },
        "capabilities": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["development", "review"],
            },
            "minItems": 1,
            "uniqueItems": True,
        },
        "availableSlots": {
            "type": "integer",
            "minimum": 0,
            "maximum": 64,
        },
        "priority": {
            "type": "integer",
            "minimum": -100,
            "maximum": 100,
        },
        "modelOverrideSupported": {
            "type": "boolean",
            "description": (
                "Whether child Agent creation can explicitly select one "
                "advertised model instead of inheriting the orchestrator."
            ),
        },
        "models": {
            "type": "array",
            "items": HOST_MODEL,
            "minItems": 1,
            "maxItems": 64,
        },
    },
    required=[
        "agentId",
        "displayName",
        "dispatchTransport",
        "capabilities",
        "availableSlots",
        "priority",
        "modelOverrideSupported",
        "models",
    ],
)

CURRENT_EXECUTOR = _object(
    {
        "agentId": _string(
            "Exact current host-native Agent ID from executor inventory."
        ),
        "modelId": _string(
            "Exact current model ID advertised by that Agent."
        ),
    },
    required=["agentId", "modelId"],
)

DISPATCH_NODE_REQUIREMENT = _object(
    {
        "nodeId": NODE_ID,
        "reasoningClass": {
            "type": "string",
            "enum": ["STANDARD", "HIGH"],
            "description": (
                "Host Agent analysis: STANDARD targets a balanced model; "
                "HIGH requires a frontier model."
            ),
        },
        "source": {
            "type": "string",
            "enum": ["PLANNING", "USER_POLICY", "LOOP_POLICY"],
        },
        "reason": _string(
            "Why this current frontier node needs the reasoning class."
        ),
    },
    required=["nodeId", "reasoningClass", "source", "reason"],
)

OUTCOME = _object(
    {
        "status": {
            "type": "string",
            "enum": [
                "SUCCEEDED",
                "BLOCKED",
                "REPLAN_REQUIRED",
                "CANCELLED",
            ],
            "description": (
                "Genuine terminal status. Correctable implementation, test, "
                "Gate, or Review findings are not BLOCKED; resolve and "
                "reevaluate them inside the current Loop."
            ),
        },
        "summary": _string("Concise Loop-owned result summary."),
        "result": {
            "type": "object",
            "description": "Opaque Loop-owned result payload.",
            "additionalProperties": True,
        },
    },
    required=["status", "summary", "result"],
)


def _tool(
    name: str,
    description: str,
    schema: dict[str, Any],
    *,
    human: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "description": description,
        "inputSchema": schema,
    }
    if human:
        result["_meta"] = {
            "anthropic/requiresUserInteraction": True,
        }
    return result


def _prepare_hierarchy_tool_schema() -> dict[str, Any]:
    hierarchy_schema = hierarchy_input_schema()
    definitions = hierarchy_schema.pop("$defs")
    tool_schema = _object(
        {"hierarchy": hierarchy_schema},
        required=["hierarchy"],
    )
    tool_schema["$defs"] = definitions
    return tool_schema


def _prepare_revision_tool_schema() -> dict[str, Any]:
    hierarchy_schema = hierarchy_input_schema()
    definitions = hierarchy_schema.pop("$defs")
    tool_schema = _object(
        {
            "root_id": ROOT_ID,
            "expected_current_revision": {
                "type": "integer",
                "minimum": 1,
            },
            "hierarchy": hierarchy_schema,
            "reason": _string(
                "Why the active, not-yet-accepted Delivery scope changed."
            ),
            "continuity_basis": {
                "type": "string",
                "enum": [
                    "USER_EXPLICIT_SAME_DELIVERY",
                    "ACTIVE_LOOP_REPLAN",
                ],
                "description": (
                    "Explicit evidence that this is the same logical "
                    "Delivery. Workspace/path reuse is never continuity."
                ),
            },
            "requested_by": _string("Human requester identity."),
        },
        required=[
            "root_id",
            "expected_current_revision",
            "hierarchy",
            "reason",
            "continuity_basis",
            "requested_by",
        ],
    )
    tool_schema["$defs"] = definitions
    return tool_schema


TOOLS = (
    _tool(
        "workspace_status",
        (
            "Inspect the Delivery bound to this conversation workspace, or "
            "select it by root ID. An unbound Git feature worktree also "
            "returns a suggested immutable Delivery Git binding."
        ),
        _object({"root_id": ROOT_ID}),
    ),
    _tool(
        "available_agents",
        (
            "Discover local terminal Agents and their current configured "
            "models without starting development commands or changing "
            "execution."
        ),
        _object({}),
    ),
    _tool(
        "hierarchy_contract",
        "Return the exact schema-v3 outer Graph contract and one example.",
        _object(
            {
                "root_kind": {
                    "type": "string",
                    "enum": ["GROUP", "TASK"],
                }
            },
            required=["root_kind"],
        ),
    ),
    _tool(
        "prepare_hierarchy",
        (
            "Validate and prepare an outer scheduling graph; shared Skill "
            "hints remain advisory, Loop payloads stay opaque, and a Git "
            "Delivery feature-branch binding is verified read-only. Reject "
            "a different Delivery before writing when this workspace "
            "already owns an unfinished Delivery."
        ),
        _prepare_hierarchy_tool_schema(),
    ),
    _tool(
        "prepare_delivery_revision",
        (
            "Prepare the next immutable revision of the same active "
            "Delivery after its frozen scope changes. The Delivery ID stays "
            "stable, completed unchanged TASKs are candidates for "
            "carry-forward, and every project scope is reauthorized at "
            "freeze."
        ),
        _prepare_revision_tool_schema(),
    ),
    _tool(
        "delivery_revision_history",
        (
            "Read every immutable revision and run status for one logical "
            "Delivery."
        ),
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "recommend_executors",
        (
            "Return non-binding local Agent and model recommendations, "
            "alternatives, confidence, and reasons for every TASK and "
            "Review Loop in one prepared or frozen Graph. Never claim or "
            "dispatch a Loop, and do not use recommendations for quota "
            "recovery."
        ),
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "plan_dispatch_batch",
        (
            "Plan one concurrent batch for the current DISPATCH_LOOP "
            "frontier using ephemeral host-native Agent capacity and "
            "selectable models. Missing host Agent analysis may use the "
            "exact current Agent/model reported by the host and remains "
            "UNCLASSIFIED. Atomically reserves every returned assignment "
            "before host Agent creation and returns model-selection "
            "instructions and decision fingerprints; never starts Agents "
            "or claims Loops."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "expected_graph_fingerprint": FINGERPRINT,
                "executor_inventory": {
                    "type": "array",
                    "items": HOST_EXECUTOR,
                    "minItems": 1,
                    "maxItems": 64,
                },
                "node_requirements": {
                    "type": "array",
                    "items": DISPATCH_NODE_REQUIREMENT,
                    "maxItems": 256,
                    "description": (
                        "Available Host Agent reasoning analyses for current "
                        "dispatch Loops. Missing nodes require "
                        "current_executor fallback; the controller never "
                        "analyzes Loop payloads."
                    ),
                },
                "current_executor": {
                    **CURRENT_EXECUTOR,
                    "description": (
                        "Exact current host Agent/model used only for "
                        "nodes lacking Agent analysis. It must match "
                        "executor_inventory."
                    ),
                },
            },
            required=[
                "root_id",
                "expected_graph_fingerprint",
                "executor_inventory",
                "node_requirements",
            ],
        ),
    ),
    _tool(
        "freeze_hierarchy",
        (
            "Freeze a prepared graph after the user selects active or manual "
            "execution; that mode selection is the one-time confirmation."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "expected_delivery_revision": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Exact Delivery revision returned by prepare."
                    ),
                },
                "expected_hierarchy_fingerprint": _string(
                    "Fingerprint returned by prepare_hierarchy."
                ),
                "authorized_project_ids": {
                    "type": "array",
                    "items": ROOT_ID,
                    "uniqueItems": True,
                    "description": (
                        "Exact project IDs explicitly authorized by the "
                        "user for this revision; use an empty array when "
                        "projectScopes is absent."
                    ),
                },
                "execution_mode": {
                    "type": "string",
                    "enum": ["active", "manual"],
                    "description": (
                        "User-selected host execution mode. active continues "
                        "the Graph in this session; manual freezes and emits "
                        "a handoff for another session."
                    ),
                },
                "confirmed_by": _string("Human confirmer identity."),
            },
            required=[
                "root_id",
                "expected_delivery_revision",
                "expected_hierarchy_fingerprint",
                "authorized_project_ids",
                "execution_mode",
                "confirmed_by",
            ],
        ),
    ),
    _tool(
        "graph_frontier",
        "Advance scheduler bookkeeping and return the next Graph actions.",
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "unfreeze_task_requirement",
        (
            "Unfreeze one not-yet-started TASK requirement so it can be "
            "revised without changing Delivery topology, dependencies, or "
            "resource locks."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "task_id": _string("Exact TASK work-item ID."),
                "expected_revision": {
                    "type": "integer",
                    "minimum": 1,
                },
                "authorized_by": _string("Human authorizer identity."),
                "reason": _string("Reason for revising the TASK requirement."),
            },
            required=[
                "root_id",
                "task_id",
                "expected_revision",
                "authorized_by",
                "reason",
            ],
        ),
        human=True,
    ),
    _tool(
        "refreeze_task_requirement",
        (
            "Replace and refreeze one previously unfrozen, unstarted TASK "
            "requirement. The replacement may change only title, summary, "
            "and opaque Loop payload."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "task_id": _string("Exact TASK work-item ID."),
                "expected_revision": {
                    "type": "integer",
                    "minimum": 1,
                },
                "requirement": _object(
                    {
                        "title": _string("Revised TASK title."),
                        "summary": _string("Revised TASK summary."),
                        "payload": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                    },
                    required=["title", "summary", "payload"],
                ),
                "confirmed_by": _string("Human confirmer identity."),
            },
            required=[
                "root_id",
                "task_id",
                "expected_revision",
                "requirement",
                "confirmed_by",
            ],
        ),
        human=True,
    ),
    _tool(
        "graph_status",
        "Read the current materialized Graph and Loop states.",
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "graph_events",
        "Read the tamper-evident scheduler event stream.",
        _object(
            {
                "root_id": ROOT_ID,
                "after_event_id": {
                    "type": "integer",
                    "minimum": 0,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                },
            },
            required=["root_id"],
        ),
    ),
    _tool(
        "advance_graph",
        "Advance lease expiry, infrastructure retry, joins, and readiness.",
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "rebuild_graph_run",
        "Rebuild mutable node projections from the verified event stream.",
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "loop_context",
        (
            "Read one opaque Loop descriptor, shared late-bound Skill hints, "
            "direct predecessors, transitive upstream results, TASK baseline "
            "path, completion policy for internal adaptation and rework, and "
            "the execution policy separating pre-claim capacity, live-lease "
            "handoff, and expired-lease recovery."
        ),
        _object(
            {"root_id": ROOT_ID, "node_id": NODE_ID},
            required=["root_id", "node_id"],
        ),
    ),
    _tool(
        "dispatch_loop",
        (
            "Claim one ready TASK, TASK Review, GROUP Review, or Delivery "
            "Review Loop "
            "for its receiving isolated executor, recording the actual "
            "Agent and model used by that executor and subject to exact "
            "resource locks."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "owner": _string("Current Loop executor identity."),
                "agent_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "description": (
                        "Actual receiving Agent ID, such as codex or "
                        "claude-code. This is execution evidence, not an "
                        "executor recommendation."
                    ),
                },
                "model_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "description": (
                        "Actual model ID used by the receiving Agent. This "
                        "is execution evidence, not a recommended model."
                    ),
                },
                "dispatch_mode": {
                    "type": "string",
                    "enum": ["AUTO", "MANUAL"],
                    "description": (
                        "Required dispatch provenance. AUTO requires the "
                        "exact decision fingerprint returned for this node."
                    ),
                },
                "receiver_context_id": _string(
                    "Host-native receiving Agent context ID. Review Loops "
                    "must differ from every upstream receiving context."
                ),
                "receiver_attestation_id": _string(
                    "One-time receiver grant issued by the model-external "
                    "host adapter after it creates this native context."
                ),
                "dispatch_transport": {
                    "type": "string",
                    "enum": ["HOST_NATIVE"],
                    "description": (
                        "Required with dispatch_mode=AUTO. It certifies "
                        "that the receiver was created through the current "
                        "host's native Agent API, never through a CLI, "
                        "subprocess, or companion script."
                    ),
                },
                "dispatch_reservation_id": _string(
                    "Required with dispatch_mode=AUTO. Use the exact "
                    "dispatchReservationId returned for this assignment."
                ),
                "dispatch_reasoning_class": {
                    "type": "string",
                    "enum": ["STANDARD", "HIGH", "UNCLASSIFIED"],
                    "description": (
                        "Reasoning class bound into an AUTO decision. "
                        "UNCLASSIFIED identifies current-executor fallback."
                    ),
                },
                "dispatch_decision_fingerprint": {
                    **FINGERPRINT,
                    "description": (
                        "Exact automatic dispatch decision fingerprint. "
                        "Only valid with dispatch_mode=AUTO."
                    ),
                },
                "operation_id": OPERATION_ID,
            },
            required=[
                "root_id",
                "node_id",
                "owner",
                "agent_id",
                "model_id",
                "dispatch_mode",
                "receiver_context_id",
                "receiver_attestation_id",
                "operation_id",
            ],
        ),
    ),
    _tool(
        "heartbeat_loop",
        "Renew the lease of one claimed Loop.",
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "operation_id": OPERATION_ID,
            },
            required=["root_id", "node_id", "operation_id"],
        ),
    ),
    _tool(
        "pause_loop",
        (
            "Pause one claimed Loop with a live lease while preserving its "
            "current attempt and frozen Graph. Provide resume_at for a "
            "known provider soft-stop window and identify whether the "
            "limited capacity belongs to the executor or the native host. "
            "A native host observing hard 429 uses its model-external "
            "capacity callback instead."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "operation_id": OPERATION_ID,
                "resume_at": _string(
                    "Optional known provider quota reset time as an ISO "
                    "8601 timestamp. Before it, the same Agent waits for a "
                    "host-native scheduled prompt or manual resume. The "
                    "first frontier call at or after it makes the same Loop "
                    "attempt ready for redispatch."
                ),
                "capacity_scope": {
                    "type": "string",
                    "enum": ["EXECUTOR", "HOST"],
                    "description": (
                        "Required with resume_at. EXECUTOR waits for the "
                        "same Loop Agent; HOST means the native orchestrator "
                        "itself is quota-limited. Both wait for a host-native "
                        "scheduled prompt or manual Agent resume."
                    ),
                },
            },
            required=["root_id", "node_id", "operation_id"],
        ),
    ),
    _tool(
        "resume_loop",
        (
            "Resume one paused Loop in a receiving independent context and "
            "return it to Graph readiness."
        ),
        _object(
            {"root_id": ROOT_ID, "node_id": NODE_ID},
            required=["root_id", "node_id"],
        ),
    ),
    _tool(
        "record_loop_result",
        (
            "Record a genuine terminal outcome returned by a claimed Loop. "
            "Do not call for a correctable finding or internal Gate failure; "
            "adapt the Loop plan, resolve it, and reevaluate first."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "operation_id": OPERATION_ID,
                "outcome": OUTCOME,
                "failure_class": {
                    "type": "string",
                    "enum": [
                        "RETRYABLE_INFRA",
                        "WORKER_LOST",
                        "LOOP_BLOCKED",
                        "REPLAN_REQUIRED",
                        "EXTERNAL_AUTHORITY",
                        "NON_RETRYABLE",
                    ],
                    "description": (
                        "Required when outcome.status is BLOCKED. Select it "
                        "only after the current Loop has no in-scope path to "
                        "progress with its existing authority."
                    ),
                },
            },
            required=[
                "root_id",
                "node_id",
                "operation_id",
                "outcome",
            ],
        ),
    ),
    _tool(
        "record_user_confirmation",
        "Complete the graph after its Review Loop succeeds and the user accepts.",
        _object(
            {
                "root_id": ROOT_ID,
                "confirmed": {
                    "type": "boolean",
                    "const": True,
                    "description": (
                        "JSON Boolean true after explicit user acceptance."
                    ),
                },
                "confirmed_by": _string("Human confirmer identity."),
                "summary": _string("Human completion summary."),
            },
            required=[
                "root_id",
                "confirmed",
                "confirmed_by",
                "summary",
            ],
        ),
    ),
    _tool(
        "cancel_graph_run",
        "Cancel all unfinished nodes in a non-terminal scheduler run.",
        _object(
            {
                "root_id": ROOT_ID,
                "cancelled_by": _string("Human canceller identity."),
                "reason": _string("Cancellation reason."),
            },
            required=["root_id", "cancelled_by", "reason"],
        ),
        human=True,
    ),
)


def tool_definitions() -> list[dict[str, Any]]:
    return deepcopy(list(TOOLS))


def _validate_schema(
    value: object,
    schema: dict[str, Any],
    field: str,
) -> None:
    expected_type = schema.get("type")
    if expected_type == "boolean" and not isinstance(value, bool):
        fail(
            "MCP_TOOL_ARGUMENT_INVALID",
            f"{field} must be a JSON boolean",
        )
    if "const" in schema:
        if value != schema["const"]:
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} must equal {schema['const']!r}",
            )
        return
    if expected_type == "object":
        if not isinstance(value, dict):
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} must be an object",
            )
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        missing = required - set(value)
        if missing:
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} is missing required fields",
                missingFields=sorted(missing),
            )
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                fail(
                    "MCP_TOOL_ARGUMENT_INVALID",
                    f"{field} contains unknown fields",
                    unknownFields=sorted(unknown),
                )
        for key, child in value.items():
            child_schema = properties.get(key)
            if child_schema is not None:
                _validate_schema(
                    child,
                    child_schema,
                    f"{field}.{key}",
                )
        return
    if expected_type == "string":
        if (
            not isinstance(value, str)
            or len(value) < schema.get("minLength", 0)
            or len(value) > schema.get("maxLength", len(value))
            or (
                "enum" in schema
                and value not in schema["enum"]
            )
        ):
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} must be a supported string",
            )
        return
    if expected_type == "array":
        if (
            not isinstance(value, list)
            or len(value) < schema.get("minItems", 0)
            or len(value) > schema.get("maxItems", len(value))
        ):
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} must be a supported array",
            )
        if schema.get("uniqueItems") and len(
            {
                canonical_json(item)
                for item in value
            }
        ) != len(value):
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} must contain unique items",
            )
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema(
                    item,
                    item_schema,
                    f"{field}[{index}]",
                )
        return
    if expected_type == "integer":
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < schema.get("minimum", value)
            or value > schema.get("maximum", value)
        ):
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} must be a supported integer",
            )


def validate_tool_arguments(
    name: str,
    arguments: object,
) -> dict[str, Any]:
    tool = next(
        (entry for entry in TOOLS if entry["name"] == name),
        None,
    )
    if tool is None or name not in CONTROLLER_OPERATIONS:
        fail("MCP_TOOL_UNKNOWN", f"Unknown scheduler tool: {name}")
    _validate_schema(arguments, tool["inputSchema"], "arguments")
    validated = dict(arguments)
    if name in {"prepare_hierarchy", "prepare_delivery_revision"}:
        try:
            validate_hierarchy_definition(validated["hierarchy"])
        except GatedLoopError as error:
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                "arguments.hierarchy does not match schema v3",
                schemaError={
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                },
            )
    return validated


def call_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    root: str,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    controller: LayeredDeliveryController = DEFAULT_CONTROLLER,
    client_info: dict[str, Any] | None = None,
    trusted_host_adapter: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    internal_arguments = validate_tool_arguments(name, arguments)
    if name == "freeze_hierarchy":
        internal_arguments["confirmed"] = True
    result = controller.execute(
        name,
        internal_arguments,
        context=ControllerContext(
            project_root=root,
            workspace_root=workspace_root or root,
            explicit_dogfood=explicit_dogfood,
            host_native_agent_ids=_host_native_agent_ids(
                trusted_host_adapter
            ),
            host_adapter_id=trusted_host_adapter,
        ),
    )
    return result


def _host_native_agent_ids(
    trusted_host_adapter: str | None,
) -> tuple[str, ...]:
    if trusted_host_adapter == "claude-code":
        return ("claude-code",)
    if trusted_host_adapter == "codex":
        return ("codex",)
    return ()


__all__ = (
    "call_tool",
    "tool_definitions",
    "validate_tool_arguments",
)
