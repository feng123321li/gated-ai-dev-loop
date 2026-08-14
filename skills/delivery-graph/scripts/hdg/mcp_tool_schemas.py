from __future__ import annotations

from .mcp_tool_schema_common import (
    Any,
    BASE_REF,
    CONTROLLER_OPERATIONS,
    ControllerContext,
    DASHBOARD_RESOURCE_URI,
    DEFAULT_CONTROLLER,
    DESTRUCTIVE_TOOLS,
    DIRTY_STATE_FINGERPRINT,
    FINGERPRINT,
    GatedLoopError,
    LayeredDeliveryController,
    NODE_ID,
    OPERATION_ID,
    OUTCOME,
    READ_ONLY_TOOLS,
    ROOT_ID,
    SCHEDULER_IDENTITY,
    TOOL_OUTPUT_SCHEMA,
    _bounded_string,
    _delivery_readiness_schema,
    _group_integration_schema,
    _object,
    _review_findings_schema,
    _string,
    _task_acceptance_schema,
    _text_array,
    canonical_json,
    deepcopy,
    fail,
    hierarchy_input_schema,
    json,
    read_regular_file,
    validate_hierarchy_definition,
)


def _tool(
    name: str,
    description: str,
    schema: dict[str, Any],
    *,
    human: bool = False,
    title: str | None = None,
    annotations: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema_value = deepcopy(schema)
    result: dict[str, Any] = {
        "name": name,
        "title": title or name.replace("_", " ").title(),
        "description": description,
        "inputSchema": schema_value,
        "outputSchema": deepcopy(TOOL_OUTPUT_SCHEMA),
    }
    default_annotations = {
        "readOnlyHint": name in READ_ONLY_TOOLS,
        "destructiveHint": name in DESTRUCTIVE_TOOLS,
        "idempotentHint": name in READ_ONLY_TOOLS,
        "openWorldHint": False,
    }
    if annotations is not None:
        default_annotations.update(deepcopy(annotations))
    result["annotations"] = default_annotations
    tool_meta = deepcopy(meta) if meta is not None else {}
    if human:
        tool_meta["anthropic/requiresUserInteraction"] = True
    if tool_meta:
        result["_meta"] = tool_meta
    return result

def _prepare_hierarchy_tool_schema() -> dict[str, Any]:
    hierarchy_schema = hierarchy_input_schema()
    definitions = hierarchy_schema.pop("$defs")
    tool_schema = _object(
        {
            "hierarchy": hierarchy_schema,
            "hierarchy_file": _string(
                "Path (workspace-relative preferred) to a UTF-8 JSON file "
                "containing the hierarchy object. Mutually exclusive with "
                "hierarchy; the controller reads and validates it. Use when "
                "the hierarchy is too large to emit inline."
            ),
        },
        required=[],
    )
    tool_schema["$defs"] = definitions
    return tool_schema

def _manual_handoff_tool_schema() -> dict[str, Any]:
    hierarchy_schema = hierarchy_input_schema()
    definitions = hierarchy_schema.pop("$defs")
    tool_schema = _object(
        {
            "hierarchy": hierarchy_schema,
            "hierarchy_file": _string(
                "Path (workspace-relative preferred) to a UTF-8 JSON file "
                "containing the hierarchy object. Mutually exclusive with "
                "hierarchy; the controller reads and validates it. Use when "
                "the hierarchy is too large to emit inline."
            ),
            "expected_hierarchy_fingerprint": FINGERPRINT,
            "expected_graph_fingerprint": FINGERPRINT,
            "authorized_project_ids": {
                "type": "array",
                "items": ROOT_ID,
                "uniqueItems": True,
                "description": (
                    "Exact project IDs authorized for the handoff; use an "
                    "empty array when projectScopes is absent."
                ),
            },
            "expected_current_revision": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "Current HANDOFF_READY revision when explicitly "
                    "revising the same manual Delivery. Omit for Revision 1."
                ),
            },
            "continuity_basis": {
                "type": "string",
                "enum": ["USER_EXPLICIT_SAME_DELIVERY"],
                "description": (
                    "Required with changed manual content to prove the user "
                    "explicitly continued the same Delivery."
                ),
            },
            "revision_reason": _string(
                "Required explanation of the changed manual requirement."
            ),
            "confirmed_by": _string("Human confirmer identity."),
        },
        required=[
            "expected_hierarchy_fingerprint",
            "expected_graph_fingerprint",
            "authorized_project_ids",
            "confirmed_by",
        ],
    )
    tool_schema["$defs"] = definitions
    return tool_schema

def _execution_choice_tool_schema() -> dict[str, Any]:
    return _object(
        {
            "root_id": ROOT_ID,
            "selection": {
                "type": "string",
                "enum": ["AUTOMATIC", "MANUAL"],
                "description": (
                    "Exact option ID from the EXECUTION_MODE "
                    "pendingInteraction.options."
                ),
            },
            "expected_hierarchy_fingerprint": FINGERPRINT,
            "expected_graph_fingerprint": FINGERPRINT,
            "authorized_project_ids": {
                "type": "array",
                "items": ROOT_ID,
                "uniqueItems": True,
                "description": (
                    "Exact project IDs authorized for the selected mode."
                ),
            },
            "confirmed_by": _string("Human selector identity."),
        },
        required=[
            "root_id",
            "selection",
            "expected_hierarchy_fingerprint",
            "expected_graph_fingerprint",
            "authorized_project_ids",
            "confirmed_by",
        ],
    )

def _development_baseline_tool_schema() -> dict[str, Any]:
    return _object(
        {
            "root_id": ROOT_ID,
            "selection": {
                "type": "string",
                "description": (
                    "A local branch_ref from the DEVELOPMENT_BASELINE "
                    "pendingInteraction.options, NEW_FROM_MAINLINE, or "
                    "NEW_FROM_CURRENT_BRANCH."
                ),
            },
            "branch_name": {
                "type": "string",
                "description": (
                    "Required for NEW_FROM_MAINLINE and "
                    "NEW_FROM_CURRENT_BRANCH: the new Delivery branch name "
                    "the host creates from the frozen base."
                ),
            },
            "expected_hierarchy_fingerprint": FINGERPRINT,
            "expected_graph_fingerprint": FINGERPRINT,
            "expected_delivery_revision": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "Required when reconfirming a blocked manual handoff."
                ),
            },
            "baseline_context_fingerprint": FINGERPRINT,
            "confirmed_dirty_state_fingerprint": (
                DIRTY_STATE_FINGERPRINT
            ),
            "confirmed_by": _string("Human confirmer identity."),
        },
        required=[
            "root_id",
            "selection",
            "expected_hierarchy_fingerprint",
            "confirmed_by",
        ],
    )

def _execution_resume_tool_schema() -> dict[str, Any]:
    return _object(
        {
            "root_id": ROOT_ID,
            "expected_hierarchy_fingerprint": FINGERPRINT,
            "expected_graph_fingerprint": FINGERPRINT,
        },
        required=[
            "root_id",
            "expected_hierarchy_fingerprint",
            "expected_graph_fingerprint",
        ],
    )

def _manual_start_tool_schema() -> dict[str, Any]:
    return _object(
        {
            "root_id": ROOT_ID,
            "expected_hierarchy_fingerprint": FINGERPRINT,
            "expected_graph_fingerprint": FINGERPRINT,
            "started_by": SCHEDULER_IDENTITY,
        },
        required=[
            "root_id",
            "expected_hierarchy_fingerprint",
            "expected_graph_fingerprint",
            "started_by",
        ],
    )

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
            "hierarchy_file": _string(
                "Path (workspace-relative preferred) to a UTF-8 JSON file "
                "containing the hierarchy object. Mutually exclusive with "
                "hierarchy; the controller reads and validates it. Use when "
                "the hierarchy is too large to emit inline."
            ),
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
            "reason",
            "continuity_basis",
            "requested_by",
        ],
    )
    tool_schema["$defs"] = definitions
    return tool_schema
