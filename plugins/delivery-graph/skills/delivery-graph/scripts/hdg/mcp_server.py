from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, BinaryIO, TextIO

from . import __version__
from .errors import GatedLoopError
from .host_policy import ProjectRootBinding
from .jsonio import (
    json_structure_within_limits,
    redact,
    strict_json_loads,
)
from .mcp_adapter import (
    MODERN_PROTOCOL_VERSION,
    McpConnection,
    SUPPORTED_PROTOCOL_VERSIONS,
    handle_message,
    report_internal_error,
)
from .mcp_catalog import ALL_TOOL_PROFILE, TOOL_PROFILES


MAX_MESSAGE_BYTES = 8 * 1024 * 1024


def _write_lifecycle_event(
    stream: TextIO | None,
    stage: str,
    **details: object,
) -> None:
    """Write a bounded lifecycle event without request payloads or paths."""

    if stream is None:
        return
    event = {
        "event": "delivery_graph_mcp_lifecycle",
        "server": "delivery-graph",
        "serverVersion": __version__,
        "stage": stage,
        "transport": "stdio",
        **details,
    }
    try:
        stream.write(
            json.dumps(
                event,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        stream.flush()
    except Exception:
        pass


def _protocol_mode(connection: McpConnection) -> str:
    if connection.protocol_version == MODERN_PROTOCOL_VERSION:
        return "STATELESS_2026_07_28"
    if connection.protocol_version:
        return f"LEGACY_{connection.protocol_version.replace('-', '_')}"
    return "UNKNOWN"


def _tool_count(response: object) -> int | None:
    if not isinstance(response, dict):
        return None
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    tools = result.get("tools")
    return len(tools) if isinstance(tools, list) else None


def _transport_error(
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
        "id": None,
        "error": error,
    }


def _write_response(stdout: TextIO, response: dict[str, Any]) -> None:
    stdout.write(
        json.dumps(
            response,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    stdout.flush()


def _utf8_size_exceeds(value: str, limit: int) -> bool:
    size = 0
    for character in value:
        codepoint = ord(character)
        size += (
            1
            if codepoint <= 0x7F
            else 2
            if codepoint <= 0x7FF
            else 3
            if codepoint <= 0xFFFF
            else 4
        )
        if size > limit:
            return True
    return False


def _read_bounded_line(
    stdin: TextIO | BinaryIO,
) -> tuple[str | None, bool, bool]:
    line = stdin.readline(MAX_MESSAGE_BYTES + 1)
    if line in {"", b""}:
        return None, False, False
    binary = isinstance(line, bytes)
    newline = b"\n" if binary else "\n"
    truncated = (
        len(line) == MAX_MESSAGE_BYTES + 1
        and not line.endswith(newline)
    )
    oversized = truncated or (
        len(line) > MAX_MESSAGE_BYTES
        if binary
        else _utf8_size_exceeds(line, MAX_MESSAGE_BYTES)
    )
    if truncated:
        while True:
            remainder = stdin.readline(MAX_MESSAGE_BYTES + 1)
            if remainder in {"", b""} or remainder.endswith(newline):
                break
    if oversized:
        return "", True, False
    if binary:
        try:
            return line.decode("utf-8", errors="strict"), False, False
        except UnicodeError:
            return "", False, True
    return line, False, False


def serve(
    *,
    stdin: TextIO | BinaryIO,
    stdout: TextIO,
    root: str | os.PathLike[str] | None = None,
    project_root_from_meta: bool = False,
    explicit_dogfood: bool = False,
    tool_profile: str = ALL_TOOL_PROFILE,
    diagnostic_stream: TextIO | None = None,
) -> None:
    """Serve the MCP adapter over newline-delimited stdio JSON-RPC."""

    connection = McpConnection(
        project_root=ProjectRootBinding.from_startup(
            root,
            from_sandbox_meta=project_root_from_meta,
        ),
        trusted_host_adapter=os.environ.get("HDG_HOST_ADAPTER"),
        tool_profile=tool_profile,
    )
    request_count = 0
    tool_catalog_delivered = False
    _write_lifecycle_event(
        diagnostic_stream,
        "SERVER_STARTED",
        hostAdapter=connection.trusted_host_adapter or "unconfigured",
        projectRootSource=(
            "REQUEST_META" if project_root_from_meta else "STARTUP_CONFIGURATION"
        ),
        supportedProtocolVersions=list(SUPPORTED_PROTOCOL_VERSIONS),
        toolProfile=connection.tool_profile,
        diagnosticHint=(
            "The host spawned delivery-graph over stdio. The next expected "
            "step is server/discover or direct tools/list for MCP 2026-07-28, "
            "or initialize for a legacy client."
        ),
    )
    while True:
        try:
            line, oversized, invalid_encoding = _read_bounded_line(stdin)
        except UnicodeError:
            _write_response(
                stdout,
                _transport_error(-32700, "Parse error"),
            )
            continue
        if line is None:
            if request_count == 0:
                hint = (
                    "The host closed stdin before any MCP request. Check the "
                    "resolved command, cwd, environment expansion, spawn timeout, "
                    "and host-side stderr capture."
                )
            elif tool_catalog_delivered:
                hint = (
                    "The server delivered its tool catalog before the host closed "
                    "the transport. If tools are absent from the Agent, inspect "
                    "host-side schema validation, cache refresh, and Agent schema "
                    "injection."
                )
            else:
                hint = (
                    "The host closed the transport before a tools/list catalog was "
                    "delivered. Inspect protocol selection and the host discovery "
                    "request."
                )
            _write_lifecycle_event(
                diagnostic_stream,
                "TRANSPORT_EOF",
                protocolMode=_protocol_mode(connection),
                requestCount=request_count,
                toolCatalogDelivered=tool_catalog_delivered,
                diagnosticHint=hint,
            )
            break
        if invalid_encoding:
            _write_response(
                stdout,
                _transport_error(-32700, "Parse error"),
            )
            continue
        if line.isspace():
            continue
        if oversized:
            _write_response(
                stdout,
                _transport_error(
                    -32600,
                    "Message exceeds the server input limit",
                    data={
                        "maxBytes": MAX_MESSAGE_BYTES,
                        "messageDiscarded": True,
                        "recovery": (
                            "Keep outer scheduler payloads compact; let the "
                            "selected Loop own any large artifact transport."
                        ),
                    },
                ),
            )
            continue
        if not json_structure_within_limits(line):
            _write_response(
                stdout,
                _transport_error(
                    -32600,
                    "Message exceeds the server structure limit",
                ),
            )
            continue
        try:
            message = strict_json_loads(line)
        except (
            ValueError,
            UnicodeError,
            RecursionError,
            MemoryError,
        ):
            _write_response(
                stdout,
                _transport_error(-32700, "Parse error"),
            )
            continue
        request_count += 1
        method = message.get("method") if isinstance(message, dict) else None
        safe_method = method if isinstance(method, str) else "invalid"
        response = handle_message(
            message,
            connection=connection,
            explicit_dogfood=explicit_dogfood,
        )
        if response is not None:
            try:
                _write_response(stdout, response)
            except (BrokenPipeError, ConnectionResetError):
                _write_lifecycle_event(
                    diagnostic_stream,
                    "RESPONSE_DELIVERY_FAILED",
                    protocolMode=_protocol_mode(connection),
                    method=safe_method,
                    requestCount=request_count,
                    diagnosticHint=(
                        "The host closed the stdio response channel. Inspect the "
                        "host lifecycle log for schema validation, timeout, reload, "
                        "or process cancellation details."
                    ),
                )
                raise
        if safe_method == "server/discover" and response is not None:
            _write_lifecycle_event(
                diagnostic_stream,
                "DISCOVERY_RESPONDED",
                protocolMode=_protocol_mode(connection),
                supportedProtocolVersions=list(SUPPORTED_PROTOCOL_VERSIONS),
                diagnosticHint=(
                    "Capability discovery completed. The host may now request "
                    "tools/list."
                ),
            )
        elif safe_method == "initialize" and response is not None:
            _write_lifecycle_event(
                diagnostic_stream,
                "LEGACY_INITIALIZE_RESPONDED",
                protocolMode=_protocol_mode(connection),
                diagnosticHint=(
                    "Legacy initialization completed. This does not prove the "
                    "tool catalog was requested or injected into the Agent."
                ),
            )
        elif safe_method == "notifications/initialized":
            _write_lifecycle_event(
                diagnostic_stream,
                "LEGACY_INITIALIZED",
                protocolMode=_protocol_mode(connection),
                diagnosticHint="The legacy client marked initialization complete.",
            )
        elif safe_method == "tools/list" and response is not None:
            count = _tool_count(response)
            tool_catalog_delivered = count is not None
            _write_lifecycle_event(
                diagnostic_stream,
                "TOOLS_LIST_RESPONDED",
                protocolMode=_protocol_mode(connection),
                toolCount=count,
                diagnosticHint=(
                    "The server returned the tool catalog. The host must validate "
                    "and inject it into the current Agent schema."
                ),
            )


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hdg-mcp",
        description="Delivery Graph stdio MCP server",
    )
    root_group = parser.add_mutually_exclusive_group()
    root_group.add_argument(
        "--project-root",
        help="Bind this server process to one project directory.",
    )
    root_group.add_argument(
        "--project-root-from-meta",
        action="store_true",
        help="Resolve the project root from Codex request metadata.",
    )
    parser.add_argument(
        "--dogfood",
        action="store_true",
        help="Explicitly allow writes in the delivery-graph source repository.",
    )
    parser.add_argument(
        "--tool-profile",
        choices=TOOL_PROFILES,
        default=ALL_TOOL_PROFILE,
        help="Expose only the tools assigned to one workflow role.",
    )
    args = parser.parse_args(argv)
    try:
        _configure_utf8_stdio()
        serve(
            stdin=sys.stdin.buffer,
            stdout=sys.stdout,
            root=args.project_root,
            project_root_from_meta=args.project_root_from_meta,
            explicit_dogfood=args.dogfood,
            tool_profile=args.tool_profile,
            diagnostic_stream=sys.stderr,
        )
        return 0
    except (BrokenPipeError, ConnectionResetError):
        _write_lifecycle_event(
            sys.stderr,
            "TRANSPORT_DISCONNECTED",
            errorCode="PLUGIN_MCP_DISCONNECTED",
            diagnosticHint=(
                "The host closed the MCP transport before response delivery. "
                "Check the preceding lifecycle event and the host-side spawn, "
                "timeout, schema validation, and Agent injection logs."
            ),
        )
        return 1
    except GatedLoopError as error:
        sys.stderr.write(f"ERROR {error.code}: {error.message}\n")
        return error.exit_code
    except Exception as error:
        report_internal_error(
            error,
            operation="mcp_server",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
