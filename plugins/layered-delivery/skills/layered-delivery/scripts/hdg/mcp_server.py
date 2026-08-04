from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, BinaryIO, TextIO

from .errors import GatedLoopError
from .host_policy import ProjectRootBinding
from .jsonio import (
    json_structure_within_limits,
    redact,
    strict_json_loads,
)
from .mcp_adapter import McpConnection, handle_message


MAX_MESSAGE_BYTES = 8 * 1024 * 1024


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
) -> None:
    """Serve the MCP adapter over newline-delimited stdio JSON-RPC."""

    connection = McpConnection(
        project_root=ProjectRootBinding.from_startup(
            root,
            from_sandbox_meta=project_root_from_meta,
        ),
        trusted_host_adapter=os.environ.get("HDG_HOST_ADAPTER"),
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
        response = handle_message(
            message,
            connection=connection,
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
        help="Resolve the project root from Codex request metadata.",
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
