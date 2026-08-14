from __future__ import annotations

from .mcp_tool_schemas import (
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
    _development_baseline_tool_schema,
    _execution_choice_tool_schema,
    _execution_resume_tool_schema,
    _group_integration_schema,
    _manual_handoff_tool_schema,
    _manual_start_tool_schema,
    _object,
    _prepare_hierarchy_tool_schema,
    _prepare_revision_tool_schema,
    _review_findings_schema,
    _string,
    _task_acceptance_schema,
    _text_array,
    _tool,
    canonical_json,
    deepcopy,
    fail,
    hierarchy_input_schema,
    json,
    read_regular_file,
    validate_hierarchy_definition,
)
from .mcp_tool_catalog_planning import PLANNING_TOOLS
from .mcp_tool_catalog_graph import GRAPH_TOOLS
from .mcp_tool_catalog_runtime import RUNTIME_TOOLS


TOOLS = PLANNING_TOOLS + GRAPH_TOOLS + RUNTIME_TOOLS


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

_HIERARCHY_FILE_TOOLS = frozenset(
    {
        "preview_hierarchy",
        "prepare_hierarchy",
        "create_manual_handoff",
        "prepare_delivery_revision",
    }
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
    if name in _HIERARCHY_FILE_TOOLS:
        has_inline = "hierarchy" in validated
        has_file = "hierarchy_file" in validated
        if has_inline and has_file:
            fail(
                "SCHEDULER_HIERARCHY_INPUT_CONFLICT",
                "Provide exactly one of hierarchy or hierarchy_file, not both",
            )
        if not has_inline and not has_file:
            fail(
                "SCHEDULER_HIERARCHY_INPUT_REQUIRED",
                "Provide the hierarchy object or a hierarchy_file path",
            )
        if has_inline:
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

def _apply_hierarchy_file(
    arguments: dict[str, Any],
    workspace_root: str,
) -> None:
    """Load the hierarchy from a workspace file when hierarchy_file is set."""
    if "hierarchy_file" not in arguments:
        return
    raw = read_regular_file(workspace_root, arguments["hierarchy_file"])
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        fail(
            "SCHEDULER_HIERARCHY_FILE_INVALID",
            f"hierarchy_file is not valid JSON: {error}",
        )
    if not isinstance(loaded, dict):
        fail(
            "SCHEDULER_HIERARCHY_FILE_INVALID",
            "hierarchy_file must contain a JSON object",
        )
    arguments["hierarchy"] = loaded
    del arguments["hierarchy_file"]

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
) -> dict[str, Any]:
    internal_arguments = validate_tool_arguments(name, arguments)
    _apply_hierarchy_file(internal_arguments, workspace_root or root)
    if name in {"create_manual_handoff", "freeze_hierarchy"}:
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
    if trusted_host_adapter == "zcode":
        return ("zcode",)
    return ()


__all__ = (
    "call_tool",
    "tool_definitions",
    "validate_tool_arguments",
)
