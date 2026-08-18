from __future__ import annotations

from .mcp_adapter_common import (
    ALL_TOOL_PROFILE,
    Any,
    CACHE_SCOPE,
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    CODEX_SANDBOX_META_KEY,
    DEFAULT_HOST_POLICY,
    DISCOVERY_TTL_MS,
    GatedLoopError,
    HostCompatibilityPolicy,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    LEGACY_PROTOCOL_VERSIONS,
    MODERN_PROTOCOL_VERSION,
    Mapping,
    McpConnection,
    ModernRequestContext,
    PROTOCOL_VERSION_META_KEY,
    ProjectRootBinding,
    ProjectRootResolution,
    RESOURCES_TTL_MS,
    SERVER_INFO_META_KEY,
    SUPPORTED_PROTOCOL_VERSIONS,
    TOOLS_TTL_MS,
    _LEGACY_PROTOCOL_ERA,
    _MODERN_PROTOCOL_ERA,
    __version__,
    _complete_result,
    _connection_protocol_mismatch,
    _gated_error_tool_result,
    _has_modern_metadata,
    _invalid_params,
    _is_modern_request,
    _modern_request_context,
    _requested_legacy_version,
    _requested_modern_version,
    _result_meta,
    _rpc_error,
    _rpc_result,
    _server_capabilities,
    _server_info,
    _server_instructions,
    _tool_result,
    _unsupported_protocol_version,
    _valid_client_info,
    _validate_call_params,
    _validate_list_params,
    _validate_resource_read_params,
    call_tool,
    dataclass,
    field,
    json,
    read_resource,
    redact,
    report_internal_error,
    resource_definitions,
    server_instructions_for_profile,
    sys,
    tool_definitions_for_profile,
    tool_names_for_profile,
    traceback,
    user_interaction_tool_names_for_profile,
    uuid,
    validate_tool_arguments,
)


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
        request_meta = params.get("_meta")
        dashboard_grant_scope = (
            name == "open_delivery_dashboard"
            and isinstance(dashboard_root_id, str)
            and connection.trusted_host_adapter == "codex"
            and connection.project_root.from_sandbox_meta
        )
        has_sandbox_metadata = (
            isinstance(request_meta, dict)
            and CODEX_SANDBOX_META_KEY in request_meta
        )
        bridge_omitted_sandbox_metadata = (
            dashboard_grant_scope
            and (
                (
                    modern
                    and isinstance(request_meta, dict)
                    and not has_sandbox_metadata
                )
                or (not modern and "_meta" not in params)
            )
        )
        protocol_era = (
            _MODERN_PROTOCOL_ERA if modern else _LEGACY_PROTOCOL_ERA
        )
        root_resolution = None
        used_dashboard_grant = False
        if bridge_omitted_sandbox_metadata:
            assert isinstance(dashboard_root_id, str)
            root_resolution = connection._dashboard_read_grants.get(
                dashboard_root_id,
                host_adapter="codex",
                protocol_era=protocol_era,
            )
            used_dashboard_grant = root_resolution is not None
        if root_resolution is None:
            root_resolution = connection.project_root.resolve_request(
                request_meta,
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
            dashboard_grant_scope
            and (has_sandbox_metadata or used_dashboard_grant)
        ):
            assert isinstance(dashboard_root_id, str)
            connection._dashboard_read_grants.remember(
                dashboard_root_id,
                root_resolution,
                host_adapter="codex",
                protocol_era=protocol_era,
            )
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
