from __future__ import annotations

import json

from dataclasses import dataclass, field

import sys

import time

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

DASHBOARD_READ_GRANT_TTL_SECONDS = 5 * 60

DASHBOARD_READ_GRANT_LIMIT = 8


@dataclass(frozen=True)
class _DashboardReadGrant:
    resolution: ProjectRootResolution
    host_adapter: str
    protocol_era: str
    expires_at: float


@dataclass
class _DashboardReadGrantStore:
    """Bounded, connection-local authority for embedded dashboard reads."""

    _entries: dict[str, _DashboardReadGrant] = field(
        default_factory=dict,
        repr=False,
    )

    def _purge_expired(self, now: float) -> None:
        expired = [
            root_id
            for root_id, grant in self._entries.items()
            if grant.expires_at <= now
        ]
        for root_id in expired:
            self._entries.pop(root_id, None)

    def get(
        self,
        root_id: str,
        *,
        host_adapter: str,
        protocol_era: str,
    ) -> ProjectRootResolution | None:
        now = time.monotonic()
        self._purge_expired(now)
        grant = self._entries.get(root_id)
        if grant is None:
            return None
        if (
            grant.host_adapter != host_adapter
            or grant.protocol_era != protocol_era
        ):
            return None
        return grant.resolution

    def remember(
        self,
        root_id: str,
        resolution: ProjectRootResolution,
        *,
        host_adapter: str,
        protocol_era: str,
    ) -> None:
        now = time.monotonic()
        self._purge_expired(now)
        self._entries.pop(root_id, None)
        while len(self._entries) >= DASHBOARD_READ_GRANT_LIMIT:
            oldest_root_id = next(iter(self._entries))
            self._entries.pop(oldest_root_id, None)
        self._entries[root_id] = _DashboardReadGrant(
            resolution=resolution,
            host_adapter=host_adapter,
            protocol_era=protocol_era,
            expires_at=now + DASHBOARD_READ_GRANT_TTL_SECONDS,
        )

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        self._purge_expired(time.monotonic())
        return len(self._entries)

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
    # The embedded app cannot mint workspace metadata. Reuse only a recent,
    # successful dashboard read from this exact host connection/root.
    _dashboard_read_grants: _DashboardReadGrantStore = field(
        default_factory=_DashboardReadGrantStore,
        repr=False,
    )

    def close(self) -> None:
        """Revoke connection-local embedded dashboard authority."""

        self._dashboard_read_grants.clear()

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
