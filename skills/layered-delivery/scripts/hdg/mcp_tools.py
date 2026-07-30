from __future__ import annotations

from copy import deepcopy
from typing import Any

from .controller import (
    CONTROLLER_OPERATIONS,
    ControllerContext,
    DEFAULT_CONTROLLER,
    LayeredDeliveryController,
)
from .errors import fail


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


TOOLS = (
    _tool(
        "workspace_status",
        "Inspect whether this project has a prepared or active scheduler graph.",
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
            "hints remain advisory and Loop payloads stay opaque."
        ),
        _object(
            {
                "hierarchy": {
                    "type": "object",
                    "description": (
                        "Hierarchy matching hierarchy_contract.inputSchema."
                    ),
                    "additionalProperties": True,
                }
            },
            required=["hierarchy"],
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
            "Claim one ready TASK, GROUP Review, or Delivery Review Loop "
            "for its receiving isolated executor, subject to exact resource "
            "locks."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "owner": _string("Current Loop executor identity."),
                "operation_id": OPERATION_ID,
            },
            required=[
                "root_id",
                "node_id",
                "owner",
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
            "Pause one claimed Loop with a live lease for context handoff "
            "while preserving its current attempt and frozen Graph."
        ),
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
    return dict(arguments)


def call_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    root: str,
    explicit_dogfood: bool = False,
    controller: LayeredDeliveryController = DEFAULT_CONTROLLER,
    **_: Any,
) -> dict[str, Any]:
    validate_tool_arguments(name, arguments)
    internal_arguments = dict(arguments)
    execution_mode = None
    if name == "freeze_hierarchy":
        execution_mode = internal_arguments.pop("execution_mode")
        internal_arguments["confirmed"] = True
    result = controller.execute(
        name,
        internal_arguments,
        context=ControllerContext(
            project_root=root,
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
