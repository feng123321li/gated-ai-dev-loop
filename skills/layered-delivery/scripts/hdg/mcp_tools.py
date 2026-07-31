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
            "Delivery feature-branch binding is verified read-only."
        ),
        _prepare_hierarchy_tool_schema(),
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
        "freeze_hierarchy",
        (
            "Freeze a prepared graph after the user selects active or manual "
            "execution; that mode selection is the one-time confirmation."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "expected_hierarchy_fingerprint": _string(
                    "Fingerprint returned by prepare_hierarchy."
                ),
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
                "expected_hierarchy_fingerprint",
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
                "operation_id": OPERATION_ID,
            },
            required=[
                "root_id",
                "node_id",
                "owner",
                "agent_id",
                "model_id",
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
            "Do not create a timed pause after an unhandled 429."
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
    if name == "prepare_hierarchy":
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
    **_: Any,
) -> dict[str, Any]:
    internal_arguments = validate_tool_arguments(name, arguments)
    execution_mode = None
    if name == "freeze_hierarchy":
        execution_mode = internal_arguments.pop("execution_mode")
        internal_arguments["confirmed"] = True
    result = controller.execute(
        name,
        internal_arguments,
        context=ControllerContext(
            project_root=root,
            workspace_root=workspace_root or root,
            explicit_dogfood=explicit_dogfood,
        ),
    )
    if name == "freeze_hierarchy":
        return {
            **result,
            "executionMode": execution_mode,
        }
    return result


__all__ = (
    "call_tool",
    "tool_definitions",
    "validate_tool_arguments",
)
