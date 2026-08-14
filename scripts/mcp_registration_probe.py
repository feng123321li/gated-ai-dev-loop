#!/usr/bin/env python3
"""Read host logs and report MCP tool registration without calling any MCP tool."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Iterable, Iterator, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
sys.path.insert(0, str(SOURCE))

from hdg.mcp_catalog import (  # noqa: E402
    DISPATCH_TOOL_PROFILE,
    PLANNING_TOOL_PROFILE,
    RECEIVER_TOOL_PROFILE,
    TOOL_PROFILES,
    tool_names_for_profile,
)


PROFILE_SERVER_SUFFIXES = {
    PLANNING_TOOL_PROFILE: "delivery-graph",
    DISPATCH_TOOL_PROFILE: "delivery-graph-dispatch",
    RECEIVER_TOOL_PROFILE: "delivery-graph-receiver",
}
HOSTS = ("zcode", "codex")


def _default_tool_prefix(profile: str) -> str:
    return (
        "mcp__plugin_delivery-graph_"
        f"{PROFILE_SERVER_SUFFIXES[profile]}__"
    )


def _default_server_name(profile: str) -> str:
    return (
        "plugin:delivery-graph:"
        f"{PROFILE_SERVER_SUFFIXES[profile]}"
    )


_LIFECYCLE_STAGES = {
    "mcp.server.connect.started": "SPAWN_STARTED",
    "mcp.server.connected": "CONNECTED",
    "mcp.server.closed": "CLOSED",
    "mcp.server.failed": "FAILED",
}


def _nested_mapping(value: object, *keys: str) -> Mapping[str, object] | None:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current if isinstance(current, Mapping) else None


def _tool_surface(entry: Mapping[str, object]) -> tuple[bool, list[str]]:
    body = _nested_mapping(entry, "request", "body")
    if body is None or "tools" not in body:
        return False, []
    tools = body.get("tools")
    if not isinstance(tools, list):
        return True, []
    names: list[str] = []
    for tool in tools:
        if not isinstance(tool, Mapping):
            continue
        name = tool.get("name")
        if not isinstance(name, str):
            function = tool.get("function")
            if isinstance(function, Mapping):
                name = function.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return True, names


def _role(entry: Mapping[str, object]) -> str:
    model = entry.get("model")
    if isinstance(model, Mapping):
        role = model.get("role")
        if isinstance(role, str) and role:
            return role
    return "unknown"


def _model_id(entry: Mapping[str, object]) -> str | None:
    model = entry.get("model")
    if isinstance(model, Mapping):
        value = model.get("modelId")
        if isinstance(value, str) and value:
            return value
    return None


def model_io_observation(
    entry: Mapping[str, object],
    *,
    source: str,
    host: str,
    tool_prefix: str,
    expected_count: int,
    workspace_index: Mapping[str, str],
    expected_names: Iterable[str] | None = None,
) -> dict[str, object]:
    """Convert one model request log into a registration-only observation."""

    surface_present, names = _tool_surface(entry)
    matching = sorted({name for name in names if name.startswith(tool_prefix)})
    expected_name_set = (
        set(expected_names) if expected_names is not None else None
    )
    missing = (
        sorted(expected_name_set - set(matching))
        if expected_name_set is not None
        else []
    )
    unexpected = (
        sorted(set(matching) - expected_name_set)
        if expected_name_set is not None
        else []
    )
    if not surface_present:
        status = "NOT_OBSERVABLE"
    elif not matching:
        status = "PLUGIN_MCP_UNAVAILABLE"
    elif expected_name_set is not None and not missing and not unexpected:
        status = "REGISTERED"
    elif expected_name_set is None and len(matching) == expected_count:
        status = "REGISTERED"
    else:
        status = "PARTIAL_REGISTRATION"
    session_id = entry.get("sessionId")
    actual_session_id = session_id if isinstance(session_id, str) else "unknown"
    observed_at = entry.get("completedAt") or entry.get("startedAt")
    return {
        "host": host,
        "workspace": workspace_index.get(actual_session_id),
        "sessionId": actual_session_id,
        "turnId": entry.get("turnId"),
        "agentRole": _role(entry),
        "modelId": _model_id(entry),
        "observedAt": observed_at if isinstance(observed_at, str) else None,
        "source": source,
        "status": status,
        "toolSurfacePresent": surface_present,
        "totalToolCount": len(names),
        "matchingToolCount": len(matching),
        "expectedToolCount": expected_count,
        "matchingToolNames": matching,
        "missingToolNames": missing,
        "unexpectedToolNames": unexpected,
        "protocolMode": "UNKNOWN",
        "mcpToolCallAttempted": False,
        "governanceWriteAttempted": False,
    }


def _context(entry: Mapping[str, object]) -> Mapping[str, object]:
    context = entry.get("context")
    return context if isinstance(context, Mapping) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def lifecycle_index(
    entries: Iterable[Mapping[str, object]],
    *,
    server_name: str,
) -> dict[str, dict[str, object]]:
    """Index non-sensitive MCP lifecycle facts by host session."""

    workspace_by_session: dict[str, str] = {}
    events_by_session: dict[str, list[dict[str, object]]] = {}
    for entry in entries:
        context = _context(entry)
        if context.get("mcpServerName") != server_name:
            continue
        session_id = _text(context.get("sessionId")) or _text(entry.get("sessionId"))
        if session_id is None:
            continue
        workspace = _text(context.get("workspaceKey")) or _text(
            context.get("workspace")
        )
        if workspace is not None:
            workspace_by_session[session_id] = workspace
        event_name = _text(entry.get("event")) or "unknown"
        stderr = context.get("stderr")
        stderr_text = stderr if isinstance(stderr, str) else ""
        event = {
            "stage": _LIFECYCLE_STAGES.get(event_name, event_name),
            "observedAt": entry.get("timestamp"),
            "exitCode": context.get("exitCode"),
            "timeoutMs": context.get("timeoutMs"),
            "stderrPresent": bool(stderr_text),
            "stderrSha256": (
                hashlib.sha256(stderr_text.encode("utf-8")).hexdigest()
                if stderr_text
                else None
            ),
        }
        events_by_session.setdefault(session_id, []).append(event)
    return {
        "workspaceBySession": workspace_by_session,
        "eventsBySession": events_by_session,
    }


def build_registration_matrix(
    entries: Iterable[Mapping[str, object]],
    *,
    source: str,
    host: str,
    tool_prefix: str,
    expected_count: int,
    lifecycle: Mapping[str, object],
    expected_names: Iterable[str] | None = None,
) -> dict[str, object]:
    """Keep the latest observation for every session and Agent role."""

    expected_name_values = (
        tuple(expected_names) if expected_names is not None else None
    )
    workspace_index = lifecycle.get("workspaceBySession")
    if not isinstance(workspace_index, Mapping):
        workspace_index = {}
    events_by_session = lifecycle.get("eventsBySession")
    if not isinstance(events_by_session, Mapping):
        events_by_session = {}
    latest: dict[tuple[str, str], dict[str, object]] = {}
    for entry in entries:
        if entry.get("type") not in {None, "model_io"}:
            continue
        observation = model_io_observation(
            entry,
            source=source,
            host=host,
            tool_prefix=tool_prefix,
            expected_count=expected_count,
            workspace_index={
                str(key): str(value) for key, value in workspace_index.items()
            },
            expected_names=expected_name_values,
        )
        key = (str(observation["sessionId"]), str(observation["agentRole"]))
        previous = latest.get(key)
        if previous is None or str(observation.get("observedAt") or "") >= str(
            previous.get("observedAt") or ""
        ):
            latest[key] = observation
    cases = sorted(
        latest.values(),
        key=lambda item: (
            str(item.get("workspace") or ""),
            str(item.get("sessionId") or ""),
            str(item.get("agentRole") or ""),
        ),
    )
    for case in cases:
        events = events_by_session.get(str(case["sessionId"]), [])
        case["lifecycleEvents"] = events if isinstance(events, list) else []
    counts = {
        "registered": 0,
        "unavailable": 0,
        "partial": 0,
        "notObservable": 0,
    }
    status_key = {
        "REGISTERED": "registered",
        "PLUGIN_MCP_UNAVAILABLE": "unavailable",
        "PARTIAL_REGISTRATION": "partial",
        "NOT_OBSERVABLE": "notObservable",
    }
    for case in cases:
        counts[status_key[str(case["status"])]] += 1
    return {
        "schemaVersion": 1,
        "host": host,
        "toolPrefix": tool_prefix,
        "expectedToolCount": expected_count,
        "summary": {"cases": len(cases), **counts},
        "cases": cases,
        "safety": {
            "modelInvocationStarted": False,
            "mcpToolCallAttempted": False,
            "governanceWriteAttempted": False,
            "schedulerDatabaseAccessed": False,
        },
    }


def strict_matrix_passes(matrix: Mapping[str, object]) -> bool:
    """Return true only when every expected case has an exact catalog."""

    summary = matrix.get("summary")
    if not isinstance(summary, Mapping):
        return False
    cases = summary.get("cases")
    registered = summary.get("registered")
    return (
        isinstance(cases, int)
        and not isinstance(cases, bool)
        and cases > 0
        and registered == cases
        and summary.get("unavailable") == 0
        and summary.get("partial") == 0
        and summary.get("notObservable") == 0
    )


def _read_jsonl(paths: Iterable[Path]) -> Iterator[Mapping[str, object]]:
    for path in paths:
        try:
            lines = path.open("r", encoding="utf-8")
        except OSError:
            continue
        with lines:
            for line in lines:
                try:
                    value = json.loads(line)
                except (UnicodeError, json.JSONDecodeError):
                    continue
                if isinstance(value, Mapping):
                    yield value


def _default_paths(host: str) -> tuple[list[Path], list[Path]]:
    if host == "zcode":
        base = Path.home() / ".zcode" / "cli"
        return (
            sorted((base / "rollout").glob("model-io-*.jsonl")),
            sorted((base / "log").glob("*.jsonl")),
        )
    return ([], [])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=HOSTS, required=True)
    parser.add_argument(
        "--profile",
        choices=TOOL_PROFILES,
        default=PLANNING_TOOL_PROFILE,
        help="Validate one profiled MCP server catalog.",
    )
    parser.add_argument("--model-io", action="append", default=[])
    parser.add_argument("--lifecycle-log", action="append", default=[])
    parser.add_argument("--tool-prefix")
    parser.add_argument("--server-name")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit nonzero unless every observed session/Agent case has the "
            "exact expected tool catalog."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    default_model, default_lifecycle = _default_paths(args.host)
    model_paths = [Path(value) for value in args.model_io] or default_model
    lifecycle_paths = [Path(value) for value in args.lifecycle_log] or default_lifecycle
    lifecycle = lifecycle_index(
        _read_jsonl(lifecycle_paths),
        server_name=args.server_name or _default_server_name(args.profile),
    )
    tool_prefix = args.tool_prefix or _default_tool_prefix(args.profile)
    profile_names = sorted(tool_names_for_profile(args.profile))
    expected_names = (
        None
        if args.expected_count is not None
        else [
            tool_prefix + tool_name
            for tool_name in profile_names
        ]
    )
    matrix = build_registration_matrix(
        _read_jsonl(model_paths),
        source=",".join(str(path) for path in model_paths),
        host=args.host,
        tool_prefix=tool_prefix,
        expected_count=args.expected_count or len(profile_names),
        lifecycle=lifecycle,
        expected_names=expected_names,
    )
    print(json.dumps(matrix, ensure_ascii=False, indent=2, sort_keys=True))
    if args.strict and not strict_matrix_passes(matrix):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
