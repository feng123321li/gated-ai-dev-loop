from __future__ import annotations

import json
from dataclasses import dataclass, field
import sys
import traceback
from typing import Any, Mapping
import uuid

from . import __version__
from .errors import GatedLoopError
from .host_policy import (
    CODEX_SANDBOX_META_KEY,
    DEFAULT_HOST_POLICY,
    HostCompatibilityPolicy,
    ProjectRootBinding,
    ProjectRootResolution,
)
from .jsonio import redact
from .mcp_apps import read_resource, resource_definitions
from .mcp_catalog import (
    ALL_TOOL_PROFILE,
    server_instructions_for_profile,
    tool_definitions_for_profile,
    tool_names_for_profile,
    user_interaction_tool_names_for_profile,
)
from .mcp_tools import (
    call_tool,
    validate_tool_arguments,
)


MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PREFERRED_PROTOCOL_VERSION = "2025-11-25"
LEGACY_PROTOCOL_VERSIONS = (
    LEGACY_PREFERRED_PROTOCOL_VERSION,
)
SUPPORTED_PROTOCOL_VERSIONS = (
    MODERN_PROTOCOL_VERSION,
    *LEGACY_PROTOCOL_VERSIONS,
)
_MODERN_PROTOCOL_ERA = "modern"
_LEGACY_PROTOCOL_ERA = "legacy"

PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
CLIENT_CAPABILITIES_META_KEY = (
    "io.modelcontextprotocol/clientCapabilities"
)
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"

DISCOVERY_TTL_MS = 60 * 60 * 1000
TOOLS_TTL_MS = 5 * 60 * 1000
RESOURCES_TTL_MS = 60 * 60 * 1000
CACHE_SCOPE = "private"


def report_internal_error(
    error: BaseException,
    *,
    operation: str,
) -> str:
    """Write one data-minimized diagnostic and return its correlation ID."""

    diagnostic_id = uuid.uuid4().hex
    try:
        summaries = traceback.extract_tb(error.__traceback__, limit=-8)
        stack = [
            {
                "file": (
                    frame.filename.replace("\\", "/").rsplit("/", 1)[-1]
                )[:128]
                or "<unknown>",
                "function": frame.name[:128],
                "line": frame.lineno,
            }
            for frame in summaries
        ]
    except Exception:
        stack = []
    diagnostic = {
        "diagnosticId": diagnostic_id,
        "event": "delivery_graph_internal_error",
        "exceptionType": type(error).__name__[:128],
        "operation": operation[:128],
        "stack": stack,
    }
    try:
        sys.stderr.write(
            json.dumps(
                diagnostic,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        sys.stderr.flush()
    except Exception:
        pass
    return diagnostic_id


@dataclass
class McpConnection:
    """Transport connection plus its pinned stdio protocol era."""

    project_root: ProjectRootBinding
    host_policy: HostCompatibilityPolicy = DEFAULT_HOST_POLICY
    protocol_era: str | None = None
    protocol_version: str | None = None
    legacy_initialize_requested: bool = False
    legacy_initialized: bool = False
    legacy_client_info: dict[str, object] | None = None
    trusted_host_adapter: str | None = None
    tool_profile: str = ALL_TOOL_PROFILE
    # The embedded app cannot mint workspace metadata. Reuse only a prior
    # successful dashboard read from this exact legacy connection/root.
    _dashboard_read_grants: dict[str, ProjectRootResolution] = field(
        default_factory=dict,
        repr=False,
    )


@dataclass(frozen=True)
class ModernRequestContext:
    protocol_version: str
    client_capabilities: Mapping[str, object]
    client_info: Mapping[str, object] | None
    meta: Mapping[str, object]


def _server_instructions(connection: McpConnection) -> str:
    return server_instructions_for_profile(
        connection.tool_profile,
        host_adapter=connection.trusted_host_adapter,
    )


def _server_info() -> dict[str, str]:
    return {
        "name": "delivery-graph",
        "version": __version__,
    }


def _server_capabilities() -> dict[str, object]:
    return {
        "tools": {"listChanged": False},
        "resources": {
            "subscribe": False,
            "listChanged": False,
        },
        "experimental": {
            CODEX_SANDBOX_META_KEY: {},
        },
    }


def _result_meta() -> dict[str, object]:
    return {
        SERVER_INFO_META_KEY: _server_info(),
    }


def _rpc_error(
    request_id: object,
    code: int,
    message: str,
    *,
    data: object | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if data is not None:
        error["data"] = redact(data)
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error,
    }


def _complete_result(result: Mapping[str, object]) -> dict[str, object]:
    completed = dict(result)
    completed["resultType"] = "complete"
    response_meta = completed.get("_meta")
    if isinstance(response_meta, dict):
        merged_meta = dict(response_meta)
        merged_meta.update(_result_meta())
    else:
        merged_meta = _result_meta()
    completed["_meta"] = merged_meta
    return completed


def _rpc_result(
    request_id: object,
    result: Mapping[str, object],
    *,
    modern: bool,
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": (
            _complete_result(result)
            if modern
            else dict(result)
        ),
    }


def _tool_result(
    payload: dict[str, Any],
    *,
    is_error: bool,
    modern: bool,
) -> dict[str, Any]:
    safe_payload = redact(payload)
    business_result = safe_payload.get("result")
    progress_monitor = (
        business_result.get("progressMonitor")
        if isinstance(business_result, dict)
        else None
    )
    markdown_table = (
        progress_monitor.get("markdownTable")
        if isinstance(progress_monitor, dict)
        else None
    )
    if not is_error and isinstance(markdown_table, str) and markdown_table:
        alerts = progress_monitor.get("alerts", [])
        wait_directive = progress_monitor.get("waitDirective")
        wait_mode = (
            wait_directive.get("mode")
            if isinstance(wait_directive, dict)
            else None
        )
        poll_not_before = (
            wait_directive.get("pollNotBefore")
            if isinstance(wait_directive, dict)
            else None
        )
        poll_tool = (
            wait_directive.get("pollTool")
            if isinstance(wait_directive, dict)
            else None
        )
        consume_actions = (
            wait_directive.get("consumeActionsBeforeWaiting") is True
            if isinstance(wait_directive, dict)
            else False
        )
        immediate_actions = (
            wait_directive.get("immediateActions", [])
            if isinstance(wait_directive, dict)
            else []
        )
        native_wake = (
            wait_directive.get("nativeWakeDirective")
            if isinstance(wait_directive, dict)
            else None
        )
        native_wake_instruction = ""
        if isinstance(native_wake, dict):
            schedule_after = native_wake.get("scheduleAfter")
            cancel_instruction = (
                "先取消旧的重复监控，"
                if native_wake.get("cancelRecurringMonitors") is True
                else ""
            )
            if isinstance(schedule_after, str):
                native_wake_instruction = (
                    cancel_instruction
                    + "在容量截止时间后留少量安全余量，创建一次宿主原生 "
                    f"one-shot 唤醒（截止 {schedule_after}）；到时调用一次 "
                    "`graph_frontier`。"
                )
        alert_lines = [
            f"- ⚠️ {item['messageZh']}"
            for item in alerts
            if isinstance(item, dict)
            and isinstance(item.get("messageZh"), str)
        ]
        if wait_mode in {
            "ADVANCE_REQUIRED",
            "FRONTIER_ACTIONS_AVAILABLE",
        }:
            refresh_instruction = (
                "检测到需要推进或消费的 Graph 动作；立即调用一次 "
                "`graph_frontier`，不要继续轮询 `graph_status`。"
            )
        elif wait_mode in {
            "HOST_NATIVE_EVENT_OR_DEADLINE",
            "CONSUME_ACTIONS_THEN_HOST_NATIVE_EVENT_OR_DEADLINE",
        } and isinstance(poll_not_before, str):
            action_instruction = (
                "先完整消费本响应的立即动作："
                + "、".join(str(item) for item in immediate_actions)
                + "。"
                if consume_actions and immediate_actions
                else ""
            )
            action_instruction += native_wake_instruction
            timeout_instruction = (
                f"无事件时最早在 {poll_not_before} 调用一次只读 "
                "`graph_status`。"
                if poll_tool == "graph_status"
                else f"无事件时等到 {poll_not_before} 再调用一次 "
                "`graph_frontier`。"
            )
            refresh_instruction = (
                action_instruction
                + "随后不要立即再次调用 `graph_frontier`。先使用宿主原生等待"
                "能力，直到原生 receiver 完成或需要关注；"
                + timeout_instruction
                + "仅在 receiver 事件或 `nextWakeAt` 到达时调用一次 "
                "`graph_frontier`。`changeFingerprint` 未变化时不要重复播报进度。"
            )
        elif wait_mode == "CONSUME_ACTIONS_FIRST":
            refresh_instruction = (
                "先完整消费本响应的立即动作："
                + "、".join(str(item) for item in immediate_actions)
                + "。"
                + native_wake_instruction
                + "不要用连续 `graph_frontier` 调用代替动作处理。"
            )
        elif wait_mode == "DEADLINE_ONLY":
            refresh_instruction = (
                "使用宿主原生等待能力等到 `nextWakeAt`，届时调用一次 "
                "`graph_frontier`；不要忙轮询。"
            )
        else:
            refresh_instruction = (
                "当前没有自动等待要求；按返回状态继续，不要连续调用 "
                "`graph_frontier`。"
            )
        text = "\n".join(
            [
                "## 后台执行进度",
                "",
                *alert_lines,
                *([""] if alert_lines else []),
                markdown_table,
                "",
                refresh_instruction,
                "原始事件仅用于展开诊断。",
            ]
        )
    else:
        text = json.dumps(
            safe_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": safe_payload,
        "isError": is_error,
    }
    return _complete_result(result) if modern else result


def _gated_error_tool_result(
    error: GatedLoopError,
    *,
    modern: bool,
) -> dict[str, Any]:
    return _tool_result(
        {
            "ok": False,
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details,
            },
        },
        is_error=True,
        modern=modern,
    )


def _invalid_params(
    request_id: object,
    error: GatedLoopError | None = None,
) -> dict[str, Any]:
    data = None
    if error is not None:
        data = {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        }
    return _rpc_error(
        request_id,
        -32602,
        "Invalid params",
        data=data,
    )


def _unsupported_protocol_version(
    request_id: object,
    requested: str,
) -> dict[str, Any]:
    return _rpc_error(
        request_id,
        -32022,
        "Unsupported protocol version",
        data={
            "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
            "requested": requested,
        },
    )


def _connection_protocol_mismatch(
    request_id: object,
    *,
    connection: McpConnection,
    requested: str,
) -> dict[str, Any]:
    if connection.protocol_version is not None:
        supported = [connection.protocol_version]
    elif connection.protocol_era == _MODERN_PROTOCOL_ERA:
        supported = [MODERN_PROTOCOL_VERSION]
    else:
        supported = list(LEGACY_PROTOCOL_VERSIONS)
    return _rpc_error(
        request_id,
        -32022,
        "Unsupported protocol version",
        data={
            "supported": supported,
            "requested": requested,
        },
    )


def _requested_modern_version(params: object) -> str:
    if isinstance(params, dict):
        meta = params.get("_meta")
        if isinstance(meta, dict):
            version = meta.get(PROTOCOL_VERSION_META_KEY)
            if isinstance(version, str):
                return version
    return MODERN_PROTOCOL_VERSION


def _requested_legacy_version(
    method: str,
    params: object,
) -> str:
    if method == "initialize" and isinstance(params, dict):
        version = params.get("protocolVersion")
        if isinstance(version, str):
            return version
    return LEGACY_PREFERRED_PROTOCOL_VERSION


def _valid_client_info(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if not isinstance(value.get("name"), str):
        return False
    if not isinstance(value.get("version"), str):
        return False
    for optional_string in (
        "title",
        "description",
        "websiteUrl",
    ):
        if (
            optional_string in value
            and not isinstance(value[optional_string], str)
        ):
            return False
    return True


def _has_modern_metadata(params: object) -> bool:
    if not isinstance(params, dict):
        return False
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return False
    return (
        PROTOCOL_VERSION_META_KEY in meta
        or CLIENT_CAPABILITIES_META_KEY in meta
    )


def _is_modern_request(
    method: str,
    params: object,
    connection: McpConnection,
) -> bool:
    if method == "server/discover" or _has_modern_metadata(params):
        return True
    if method in {"initialize", "ping"}:
        return False
    return not connection.legacy_initialized


def _modern_request_context(
    params: Mapping[str, object],
    request_id: object,
) -> tuple[ModernRequestContext | None, dict[str, Any] | None]:
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None, _invalid_params(request_id)
    protocol_version = meta.get(PROTOCOL_VERSION_META_KEY)
    client_capabilities = meta.get(CLIENT_CAPABILITIES_META_KEY)
    client_info = meta.get(CLIENT_INFO_META_KEY)
    if (
        not isinstance(protocol_version, str)
        or not isinstance(client_capabilities, dict)
        or (
            client_info is not None
            and not _valid_client_info(client_info)
        )
    ):
        return None, _invalid_params(request_id)
    if protocol_version != MODERN_PROTOCOL_VERSION:
        return (
            None,
            _unsupported_protocol_version(
                request_id,
                protocol_version,
            ),
        )
    return (
        ModernRequestContext(
            protocol_version=protocol_version,
            client_capabilities=client_capabilities,
            client_info=client_info,
            meta=meta,
        ),
        None,
    )


def _validate_list_params(
    params: Mapping[str, object],
) -> bool:
    allowed = {"cursor", "_meta"}
    if set(params) - allowed:
        return False
    cursor = params.get("cursor")
    request_meta = params.get("_meta")
    return (
        (cursor is None or isinstance(cursor, str))
        and (
            request_meta is None
            or isinstance(request_meta, dict)
        )
    )


def _validate_call_params(
    params: Mapping[str, object],
    *,
    modern: bool,
) -> tuple[str, dict[str, Any]] | None:
    allowed = {"name", "arguments", "_meta"}
    if modern:
        allowed.update({"inputResponses", "requestState"})
    if set(params) - allowed:
        return None
    name = params.get("name")
    arguments = params.get("arguments", {})
    request_meta = params.get("_meta")
    input_responses = params.get("inputResponses")
    request_state = params.get("requestState")
    if (
        not isinstance(name, str)
        or not isinstance(arguments, dict)
        or (
            request_meta is not None
            and not isinstance(request_meta, dict)
        )
        or (
            input_responses is not None
            and not isinstance(input_responses, dict)
        )
        or (
            request_state is not None
            and not isinstance(request_state, str)
        )
    ):
        return None
    return name, arguments


def _validate_resource_read_params(
    params: Mapping[str, object],
) -> str | None:
    if set(params) - {"uri", "_meta"}:
        return None
    uri = params.get("uri")
    request_meta = params.get("_meta")
    if (
        not isinstance(uri, str)
        or not uri
        or (
            request_meta is not None
            and not isinstance(request_meta, dict)
        )
    ):
        return None
    return uri


def _read_mcp_resource(
    *,
    request_id: object,
    params: Mapping[str, object],
    modern: bool,
) -> dict[str, Any]:
    uri = _validate_resource_read_params(params)
    if uri is None:
        return _invalid_params(request_id)
    try:
        result = read_resource(uri)
    except GatedLoopError as error:
        if error.code == "MCP_RESOURCE_NOT_FOUND":
            return _rpc_error(
                request_id,
                -32602 if modern else -32002,
                "Resource not found",
                data={"uri": uri},
            )
        return _rpc_error(
            request_id,
            -32603,
            "Internal error",
        )
    except Exception as error:
        diagnostic_id = report_internal_error(
            error,
            operation="resources/read",
        )
        return _rpc_error(
            request_id,
            -32603,
            "Internal error",
            data={"diagnosticId": diagnostic_id},
        )
    if modern:
        result = {
            **result,
            "ttlMs": RESOURCES_TTL_MS,
            "cacheScope": CACHE_SCOPE,
        }
    return _rpc_result(request_id, result, modern=modern)


def _call_scheduler_tool(
    *,
    request_id: object,
    params: Mapping[str, object],
    connection: McpConnection,
    client_info: Mapping[str, object] | None,
    modern: bool,
    explicit_dogfood: bool,
) -> dict[str, Any]:
    validated_call = _validate_call_params(
        params,
        modern=modern,
    )
    if validated_call is None:
        return _invalid_params(request_id)
    name, arguments = validated_call
    if name not in tool_names_for_profile(connection.tool_profile):
        return _invalid_params(
            request_id,
            GatedLoopError(
                "MCP_TOOL_OUTSIDE_PROFILE",
                "The requested tool is not exposed by this MCP profile",
                details={
                    "tool": name,
                    "toolProfile": connection.tool_profile,
                },
            ),
        )
    try:
        validate_tool_arguments(name, arguments)
    except GatedLoopError as error:
        if error.code == "MCP_TOOL_UNKNOWN":
            return _invalid_params(request_id, error)
        return _rpc_result(
            request_id,
            _gated_error_tool_result(error, modern=modern),
            modern=False,
        )
    try:
        if not modern:
            connection.host_policy.ensure_user_interaction_tool_supported(
                requires_user_interaction=(
                    name
                    in user_interaction_tool_names_for_profile(
                        connection.tool_profile
                    )
                ),
                client_info=client_info,
            )
        dashboard_root_id = (
            arguments.get("root_id")
            if name == "open_delivery_dashboard"
            else None
        )
        root_resolution = None
        if (
            not modern
            and name == "open_delivery_dashboard"
            and "_meta" not in params
            and isinstance(dashboard_root_id, str)
            and connection.trusted_host_adapter == "codex"
            and connection.project_root.from_sandbox_meta
        ):
            root_resolution = connection._dashboard_read_grants.get(
                dashboard_root_id
            )
        if root_resolution is None:
            root_resolution = connection.project_root.resolve_request(
                params.get("_meta"),
                stateless=modern,
                require_sandbox_metadata=True,
            )
        workspace_root = root_resolution.workspace_root
        business_result = call_tool(
            name,
            dict(arguments),
            root=root_resolution.project_root,
            workspace_root=workspace_root,
            explicit_dogfood=explicit_dogfood,
            client_info=(dict(client_info) if client_info else None),
            trusted_host_adapter=connection.trusted_host_adapter,
        )
        if (
            not modern
            and name == "open_delivery_dashboard"
            and "_meta" in params
            and isinstance(dashboard_root_id, str)
            and connection.trusted_host_adapter == "codex"
            and connection.project_root.from_sandbox_meta
        ):
            connection._dashboard_read_grants[
                dashboard_root_id
            ] = root_resolution
        payload = {
            "ok": True,
            "result": business_result,
        }
        return _rpc_result(
            request_id,
            _tool_result(
                payload,
                is_error=False,
                modern=modern,
            ),
            modern=False,
        )
    except GatedLoopError as error:
        return _rpc_result(
            request_id,
            _gated_error_tool_result(error, modern=modern),
            modern=False,
        )
    except Exception as error:
        tool_name = (
            name
            if name in tool_names_for_profile(ALL_TOOL_PROFILE)
            else "unknown"
        )
        diagnostic_id = report_internal_error(
            error,
            operation=f"tool:{tool_name}",
        )
        return _rpc_result(
            request_id,
            _tool_result(
                {
                    "ok": False,
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "Unexpected error",
                        "details": {"diagnosticId": diagnostic_id},
                    },
                },
                is_error=True,
                modern=modern,
            ),
            modern=False,
        )


def _dispatch_initialized_method(
    *,
    request_id: object,
    method: str,
    params: Mapping[str, object],
    connection: McpConnection,
    client_info: Mapping[str, object] | None,
    modern: bool,
    explicit_dogfood: bool,
) -> dict[str, Any]:
    """Dispatch shared MCP methods after the wire-specific handshake."""

    if method == "tools/list":
        if not _validate_list_params(params):
            return _invalid_params(request_id)
        payload: dict[str, Any] = {
            "tools": tool_definitions_for_profile(connection.tool_profile)
        }
        if modern:
            payload.update(
                {
                    "ttlMs": TOOLS_TTL_MS,
                    "cacheScope": CACHE_SCOPE,
                }
            )
        return _rpc_result(request_id, payload, modern=modern)

    if method == "resources/list":
        if not _validate_list_params(params):
            return _invalid_params(request_id)
        payload = {"resources": resource_definitions()}
        if modern:
            payload.update(
                {
                    "ttlMs": RESOURCES_TTL_MS,
                    "cacheScope": CACHE_SCOPE,
                }
            )
        return _rpc_result(request_id, payload, modern=modern)

    if method == "resources/read":
        return _read_mcp_resource(
            request_id=request_id,
            params=params,
            modern=modern,
        )

    if method == "tools/call":
        return _call_scheduler_tool(
            request_id=request_id,
            params=params,
            connection=connection,
            client_info=client_info,
            modern=modern,
            explicit_dogfood=explicit_dogfood,
        )

    return _rpc_error(request_id, -32601, "Method not found")


def _handle_modern_request(
    *,
    request_id: object,
    method: str,
    params: Mapping[str, object],
    context: ModernRequestContext,
    connection: McpConnection,
    explicit_dogfood: bool,
) -> dict[str, Any]:
    if method == "server/discover":
        if set(params) - {"_meta"}:
            return _invalid_params(request_id)
        return _rpc_result(
            request_id,
            {
                "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
                "capabilities": _server_capabilities(),
                "instructions": _server_instructions(connection),
                "ttlMs": DISCOVERY_TTL_MS,
                "cacheScope": CACHE_SCOPE,
            },
            modern=True,
        )

    return _dispatch_initialized_method(
        request_id=request_id,
        method=method,
        params=params,
        connection=connection,
        client_info=context.client_info,
        modern=True,
        explicit_dogfood=explicit_dogfood,
    )


def _handle_legacy_request(
    *,
    request_id: object,
    method: str,
    params: Mapping[str, object],
    connection: McpConnection,
    explicit_dogfood: bool,
) -> dict[str, Any]:
    if method == "initialize":
        if connection.legacy_initialize_requested:
            return _rpc_error(
                request_id,
                -32600,
                "Already initialized",
            )
        protocol_version = params.get("protocolVersion")
        capabilities = params.get("capabilities")
        client_info = params.get("clientInfo")
        request_meta = params.get("_meta")
        if (
            not isinstance(protocol_version, str)
            or not isinstance(capabilities, dict)
            or not _valid_client_info(client_info)
            or (
                request_meta is not None
                and not isinstance(request_meta, dict)
            )
        ):
            return _invalid_params(request_id)
        negotiated = (
            protocol_version
            if protocol_version in LEGACY_PROTOCOL_VERSIONS
            else LEGACY_PREFERRED_PROTOCOL_VERSION
        )
        connection.legacy_initialize_requested = True
        connection.legacy_client_info = dict(client_info)
        connection.protocol_era = _LEGACY_PROTOCOL_ERA
        connection.protocol_version = negotiated
        return _rpc_result(
            request_id,
            {
                "protocolVersion": negotiated,
                "capabilities": _server_capabilities(),
                "serverInfo": _server_info(),
                "instructions": _server_instructions(connection),
            },
            modern=False,
        )

    if method == "ping":
        if connection.protocol_era is None:
            connection.protocol_era = _LEGACY_PROTOCOL_ERA
        return _rpc_result(request_id, {}, modern=False)

    if not connection.legacy_initialized:
        return _rpc_error(
            request_id,
            -32002,
            "Server not initialized",
        )

    return _dispatch_initialized_method(
        request_id=request_id,
        method=method,
        params=params,
        connection=connection,
        client_info=connection.legacy_client_info,
        modern=False,
        explicit_dogfood=explicit_dogfood,
    )


def handle_message(
    message: object,
    *,
    connection: McpConnection,
    explicit_dogfood: bool = False,
) -> dict[str, Any] | None:
    """Adapt one decoded MCP JSON-RPC message to the shared controller."""

    if not isinstance(message, dict):
        return _rpc_error(None, -32600, "Invalid Request")
    request_id = message.get("id")
    if (
        message.get("jsonrpc") != "2.0"
        or not isinstance(message.get("method"), str)
        or (
            "id" in message
            and (
                request_id is None
                or isinstance(request_id, bool)
                or not isinstance(request_id, (str, int))
            )
        )
    ):
        return _rpc_error(
            request_id if isinstance(request_id, (str, int)) else None,
            -32600,
            "Invalid Request",
        )

    method = message["method"]
    is_notification = "id" not in message
    params = message.get("params", {})
    if is_notification:
        if (
            method == "notifications/initialized"
            and connection.protocol_era == _LEGACY_PROTOCOL_ERA
            and connection.legacy_initialize_requested
            and not _has_modern_metadata(params)
            and (params is None or isinstance(params, dict))
        ):
            connection.legacy_initialized = True
        return None

    if not isinstance(params, dict):
        return _invalid_params(request_id)

    requests_modern_era = (
        method == "server/discover"
        or _has_modern_metadata(params)
    )
    if connection.protocol_era == _MODERN_PROTOCOL_ERA:
        if not requests_modern_era:
            return _connection_protocol_mismatch(
                request_id,
                connection=connection,
                requested=_requested_legacy_version(method, params),
            )
        modern = True
    elif connection.protocol_era == _LEGACY_PROTOCOL_ERA:
        if requests_modern_era:
            return _connection_protocol_mismatch(
                request_id,
                connection=connection,
                requested=_requested_modern_version(params),
            )
        modern = False
    else:
        modern = _is_modern_request(method, params, connection)
    if modern:
        context, error = _modern_request_context(
            params,
            request_id,
        )
        if error is not None:
            return error
        assert context is not None
        connection.protocol_era = _MODERN_PROTOCOL_ERA
        connection.protocol_version = context.protocol_version
        return _handle_modern_request(
            request_id=request_id,
            method=method,
            params=params,
            context=context,
            connection=connection,
            explicit_dogfood=explicit_dogfood,
        )

    return _handle_legacy_request(
        request_id=request_id,
        method=method,
        params=params,
        connection=connection,
        explicit_dogfood=explicit_dogfood,
    )


__all__ = (
    "CLIENT_CAPABILITIES_META_KEY",
    "CLIENT_INFO_META_KEY",
    "LEGACY_PREFERRED_PROTOCOL_VERSION",
    "LEGACY_PROTOCOL_VERSIONS",
    "MODERN_PROTOCOL_VERSION",
    "McpConnection",
    "PROTOCOL_VERSION_META_KEY",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "handle_message",
)
