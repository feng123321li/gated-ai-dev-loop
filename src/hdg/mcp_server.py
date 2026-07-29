from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, TextIO
from urllib.parse import unquote, urlsplit

from . import __version__
from .errors import GatedLoopError
from .jsonio import (
    json_structure_within_limits,
    redact,
    strict_json_loads,
)
from .mcp_tools import (
    call_tool,
    tool_definitions,
    validate_tool_arguments,
)


LATEST_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = (
    LATEST_PROTOCOL_VERSION,
    "2025-06-18",
)
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
CODEX_SANDBOX_META_KEY = "codex/sandbox-state-meta"
MINIMUM_CLAUDE_CODE_USER_INTERACTION_VERSION = (2, 1, 199)
_USER_INTERACTION_TOOLS = frozenset(
    tool["name"]
    for tool in tool_definitions()
    if tool.get("_meta", {}).get("anthropic/requiresUserInteraction") is True
)


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


def _rpc_result(request_id: object, result: object) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def _tool_result(payload: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    safe_payload = redact(payload)
    text = json.dumps(
        safe_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": safe_payload,
        "isError": is_error,
    }


def _gated_error_tool_result(error: GatedLoopError) -> dict[str, Any]:
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


def _resolve_project_root(
    root: str | os.PathLike[str] | None,
) -> str:
    configured = root
    if configured is None:
        configured = os.environ.get("HDG_PROJECT_ROOT") or os.getcwd()
    candidate = Path(configured).expanduser()
    if candidate.is_symlink():
        raise GatedLoopError(
            "PROJECT_ROOT_INVALID",
            "MCP project root must not be a symbolic link",
        )
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise GatedLoopError(
            "PROJECT_ROOT_INVALID",
            "MCP project root must be an existing directory",
        )
    if not resolved.is_dir():
        raise GatedLoopError(
            "PROJECT_ROOT_INVALID",
            "MCP project root must be an existing directory",
        )
    return str(resolved)


def _local_path_from_file_uri(uri: str) -> str:
    if not uri.lower().startswith("file://"):
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "sandboxCwd must be a local file URI",
        )
    parsed = urlsplit(uri)
    if (
        parsed.scheme.lower() != "file"
        or parsed.query
        or parsed.fragment
        or parsed.netloc.lower() not in {"", "localhost"}
    ):
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "sandboxCwd must be a local file URI",
        )
    decoded = unquote(parsed.path)
    if not decoded or "\x00" in decoded:
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "sandboxCwd must contain a valid local path",
        )
    if os.name == "nt":
        if re.match(r"^/[A-Za-z]:/", decoded):
            decoded = decoded[1:]
        decoded = decoded.replace("/", "\\")
    elif not decoded.startswith("/"):
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "sandboxCwd must contain an absolute local path",
        )
    return decoded


def _project_root_from_sandbox_meta(
    meta: object,
) -> str | None:
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "MCP request _meta must be a JSON object",
        )
    if CODEX_SANDBOX_META_KEY not in meta:
        return None
    sandbox_state = meta[CODEX_SANDBOX_META_KEY]
    if not isinstance(sandbox_state, dict):
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "Codex sandbox metadata must be a JSON object",
        )
    sandbox_cwd = sandbox_state.get("sandboxCwd")
    if not isinstance(sandbox_cwd, str):
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "Codex sandbox metadata must include sandboxCwd",
        )
    return _local_path_from_file_uri(sandbox_cwd)


@dataclass
class ProjectRootBinding:
    """Bind one MCP process to one immutable business project root."""

    _bound_root: str | None
    from_sandbox_meta: bool = False

    @classmethod
    def from_startup(
        cls,
        root: str | os.PathLike[str] | None,
        *,
        from_sandbox_meta: bool = False,
    ) -> ProjectRootBinding:
        configured_environment_root = os.environ.get("HDG_PROJECT_ROOT")
        if from_sandbox_meta:
            if root is not None or configured_environment_root:
                raise GatedLoopError(
                    "PROJECT_ROOT_CONFIGURATION_CONFLICT",
                    "Sandbox metadata root binding cannot be combined with "
                    "--project-root or HDG_PROJECT_ROOT",
                )
            return cls(None, from_sandbox_meta=True)
        return cls(_resolve_project_root(root))

    @property
    def bound_root(self) -> str | None:
        return self._bound_root

    def resolve(self, meta: object) -> str:
        metadata_root = _project_root_from_sandbox_meta(meta)
        if metadata_root is None:
            if self.from_sandbox_meta:
                raise GatedLoopError(
                    "PROJECT_ROOT_UNAVAILABLE",
                    "Codex sandbox metadata is required to bind the project root",
                )
            if self._bound_root is None:
                raise GatedLoopError(
                    "PROJECT_ROOT_UNAVAILABLE",
                    "MCP project root is not bound",
                )
            return self._bound_root

        resolved_metadata_root = _resolve_project_root(metadata_root)
        if self._bound_root is None:
            self._bound_root = resolved_metadata_root
            return resolved_metadata_root
        if os.path.normcase(self._bound_root) != os.path.normcase(
            resolved_metadata_root
        ):
            raise GatedLoopError(
                "PROJECT_ROOT_MISMATCH",
                "MCP process is already bound to a different project root",
            )
        return self._bound_root


@dataclass
class ServerSession:
    """Mutable MCP lifecycle state for one stdio client connection."""

    project_root: ProjectRootBinding
    initialize_requested: bool = False
    initialized: bool = False
    client_name: str | None = None
    client_version: str | None = None


def _valid_client_info(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if not isinstance(value.get("name"), str):
        return False
    if not isinstance(value.get("version"), str):
        return False
    title = value.get("title")
    return title is None or isinstance(title, str)


def _version_triplet(value: str | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+.]|$)", value.strip())
    if match is None:
        return None
    return tuple(int(component) for component in match.groups())


def _claude_user_interaction_is_supported(session: ServerSession) -> bool:
    name = (session.client_name or "").casefold()
    if "claude" not in name or "code" not in name:
        return True
    version = _version_triplet(session.client_version)
    return (
        version is not None
        and version >= MINIMUM_CLAUDE_CODE_USER_INTERACTION_VERSION
    )


def handle_message(
    message: object,
    *,
    session: ServerSession,
    explicit_dogfood: bool = False,
) -> dict[str, Any] | None:
    """Handle one decoded MCP JSON-RPC message."""

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
            and session.initialize_requested
            and (params is None or isinstance(params, dict))
        ):
            session.initialized = True
        return None

    if not isinstance(params, dict):
        return _invalid_params(request_id)

    if method == "initialize":
        if session.initialize_requested:
            return _rpc_error(request_id, -32600, "Already initialized")
        protocol_version = params.get("protocolVersion")
        capabilities = params.get("capabilities")
        client_info = params.get("clientInfo")
        request_meta = params.get("_meta")
        if (
            not isinstance(protocol_version, str)
            or not isinstance(capabilities, dict)
            or not _valid_client_info(client_info)
            or (request_meta is not None and not isinstance(request_meta, dict))
        ):
            return _invalid_params(request_id)
        negotiated = (
            protocol_version
            if protocol_version in SUPPORTED_PROTOCOL_VERSIONS
            else LATEST_PROTOCOL_VERSION
        )
        session.initialize_requested = True
        session.client_name = client_info["name"]
        session.client_version = client_info["version"]
        return _rpc_result(
            request_id,
            {
                "protocolVersion": negotiated,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "experimental": {
                        CODEX_SANDBOX_META_KEY: {},
                    },
                },
                "serverInfo": {
                    "name": "layered-delivery",
                    "version": __version__,
                },
                "instructions": (
                    "Use these tools as an outer Graph scheduler for one "
                    "Delivery. Its decomposition is a recursive GROUP/TASK "
                    "hierarchy: TASK is the execution leaf, while every GROUP "
                    "joins and reviews its child subtree before succeeding. "
                    "Start with workspace_status. Prepare and explicitly "
                    "freeze a hierarchy, then follow every graph_frontier "
                    "action. Each TASK or Review Loop owns its internal plan, "
                    "tests, gates, rework, and Skill usage. Shared skillHints "
                    "are advisory runtime preferences: each Loop discovers "
                    "its actual context and prioritizes only applicable hints; "
                    "they are not assigned during requirement planning. The scheduler "
                    "treats Loop payload and result as opaque and accepts only "
                    "standard Loop outcomes. "
                    "resourceClaims are exact scheduling locks, not file "
                    "scopes. Final completion still requires explicit user "
                    "confirmation. External Git and publication actions remain "
                    "outside this server."
                ),
            },
        )

    if method == "ping":
        return _rpc_result(request_id, {})

    if not session.initialized:
        return _rpc_error(request_id, -32002, "Server not initialized")

    if method == "tools/list":
        if set(params) - {"cursor", "_meta"}:
            return _invalid_params(request_id)
        cursor = params.get("cursor")
        request_meta = params.get("_meta")
        if (
            (cursor is not None and not isinstance(cursor, str))
            or (
                request_meta is not None
                and not isinstance(request_meta, dict)
            )
        ):
            return _invalid_params(request_id)
        return _rpc_result(
            request_id,
            {"tools": tool_definitions()},
        )

    if method == "tools/call":
        if set(params) - {"name", "arguments", "_meta"}:
            return _invalid_params(request_id)
        name = params.get("name")
        arguments = params.get("arguments", {})
        request_meta = params.get("_meta")
        if (
            not isinstance(name, str)
            or not isinstance(arguments, dict)
            or (
                request_meta is not None
                and not isinstance(request_meta, dict)
            )
        ):
            return _invalid_params(request_id)
        if (
            name in _USER_INTERACTION_TOOLS
            and not _claude_user_interaction_is_supported(session)
        ):
            return _rpc_result(
                request_id,
                _gated_error_tool_result(
                    GatedLoopError(
                        "MCP_CLIENT_UPGRADE_REQUIRED",
                        (
                            "Claude Code 2.1.199 or later is required for "
                            "tools that must always reach a human approval prompt"
                        ),
                        details={
                            "minimumVersion": "2.1.199",
                            "clientName": session.client_name,
                            "clientVersion": session.client_version,
                        },
                    )
                ),
            )
        try:
            validate_tool_arguments(name, arguments)
        except GatedLoopError as error:
            if error.code == "MCP_TOOL_UNKNOWN":
                return _invalid_params(request_id, error)
            return _rpc_result(
                request_id,
                _gated_error_tool_result(error),
            )
        try:
            root = session.project_root.resolve(request_meta)
            business_result = call_tool(
                name,
                arguments,
                root=root,
                explicit_dogfood=explicit_dogfood,
            )
            payload = {
                "ok": True,
                "result": business_result,
            }
            return _rpc_result(
                request_id,
                _tool_result(payload, is_error=False),
            )
        except GatedLoopError as error:
            return _rpc_result(
                request_id,
                _gated_error_tool_result(error),
            )
        except Exception:
            payload = {
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Unexpected error",
                    "details": {},
                },
            }
            return _rpc_result(
                request_id,
                _tool_result(payload, is_error=True),
            )

    return _rpc_error(request_id, -32601, "Method not found")


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
) -> None:
    """Serve newline-delimited JSON-RPC over stdio."""

    session = ServerSession(
        project_root=ProjectRootBinding.from_startup(
            root,
            from_sandbox_meta=project_root_from_meta,
        ),
    )
    while True:
        try:
            line, oversized, invalid_encoding = _read_bounded_line(stdin)
        except UnicodeError:
            _write_response(
                stdout,
                _rpc_error(None, -32700, "Parse error"),
            )
            continue
        if line is None:
            break
        if invalid_encoding:
            _write_response(
                stdout,
                _rpc_error(None, -32700, "Parse error"),
            )
            continue
        if line.isspace():
            continue
        if oversized:
            _write_response(
                stdout,
                _rpc_error(
                    None,
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
                _rpc_error(
                    None,
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
                _rpc_error(None, -32700, "Parse error"),
            )
            continue
        response = handle_message(
            message,
            session=session,
            explicit_dogfood=explicit_dogfood,
        )
        if response is not None:
            _write_response(stdout, response)


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hdg-mcp",
        description="Layered Delivery stdio MCP server",
    )
    root_group = parser.add_mutually_exclusive_group()
    root_group.add_argument(
        "--project-root",
        help="Bind this server process to one project directory.",
    )
    root_group.add_argument(
        "--project-root-from-meta",
        action="store_true",
        help="Bind once from Codex-injected sandboxCwd metadata.",
    )
    parser.add_argument(
        "--dogfood",
        action="store_true",
        help="Explicitly allow writes in the layered-delivery source repository.",
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
        )
        return 0
    except (BrokenPipeError, ConnectionResetError):
        sys.stderr.write(
            "ERROR PLUGIN_MCP_DISCONNECTED: "
            "MCP transport closed before response delivery\n"
        )
        return 1
    except GatedLoopError as error:
        sys.stderr.write(f"ERROR {error.code}: {error.message}\n")
        return error.exit_code
    except Exception:
        sys.stderr.write("ERROR INTERNAL_ERROR: Unexpected error\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
