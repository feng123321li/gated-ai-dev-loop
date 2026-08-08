#!/usr/bin/env python3
"""Validate a delivery-graph release candidate without network access."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
import re
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
sys.path.insert(0, str(SOURCE))

from hdg import __version__  # noqa: E402
from hdg.graph_model import (  # noqa: E402
    compile_delivery_graph,
    graph_assurance_profile,
)
from hdg.jsonio import fingerprint  # noqa: E402
from hdg.mcp_tools import tool_definitions  # noqa: E402
from hdg.model_core import validate_hierarchy_definition  # noqa: E402
from hdg.planning import freeze_hierarchy  # noqa: E402


CANONICAL_SKILL = ROOT / "skills" / "delivery-graph"
SKILL_RUNTIME = CANONICAL_SKILL / "scripts" / "hdg"
PLUGIN = ROOT / "plugins" / "delivery-graph"
PLUGIN_SKILL = PLUGIN / "skills" / "delivery-graph"
CODEX_MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
CLAUDE_MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"
CODEX_HOOKS = PLUGIN / "hooks" / "hooks.json"
CLAUDE_HOOKS = PLUGIN / "hooks" / "claude-hooks.json"
REPO_MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"
TEMPLATES = ROOT / "examples" / "team-loops"
EXPECTED_TOOL_COUNT = 29


def _version_from_pyproject() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, flags=re.MULTILINE)
    if match is None:
        raise ValueError("pyproject.toml does not contain a project version")
    return match.group(1)


def _files(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    if not root.is_dir():
        return result
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def _json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("root value must be an object")
    return value


def _plugin_path(value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not value.startswith("./"):
        raise ValueError(f"{field} must be a ./-relative path")
    if "\\" in value:
        raise ValueError(f"{field} must use forward slashes")
    candidate = (PLUGIN / value).resolve()
    try:
        candidate.relative_to(PLUGIN.resolve())
    except ValueError as error:
        raise ValueError(f"{field} escapes the Plugin root") from error
    if not candidate.exists():
        raise ValueError(f"{field} does not exist: {candidate}")
    return candidate


def _hook_commands(hooks: object) -> list[dict[str, object]]:
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object")
    commands: list[dict[str, object]] = []
    for groups in hooks.values():
        if not isinstance(groups, list):
            raise ValueError("hook event entries must be arrays")
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError("hook groups must be objects")
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                raise ValueError("hook group handlers must be arrays")
            for handler in handlers:
                if not isinstance(handler, dict):
                    raise ValueError("hook handlers must be objects")
                if handler.get("type") == "command":
                    commands.append(handler)
    return commands


def _compare_trees(
    left: Path,
    right: Path,
    *,
    excluded: Iterable[str] = (),
) -> list[str]:
    excluded_names = set(excluded)
    left_files = {
        name: content
        for name, content in _files(left).items()
        if name not in excluded_names
    }
    right_files = _files(right)
    problems: list[str] = []
    if set(left_files) != set(right_files):
        missing = sorted(set(left_files) - set(right_files))
        extra = sorted(set(right_files) - set(left_files))
        if missing:
            problems.append(f"{right}: missing {', '.join(missing)}")
        if extra:
            problems.append(f"{right}: unexpected {', '.join(extra)}")
    changed = sorted(
        name
        for name in set(left_files) & set(right_files)
        if left_files[name] != right_files[name]
    )
    if changed:
        problems.append(f"{right}: stale {', '.join(changed)}")
    return problems


def validate_release() -> list[str]:
    problems: list[str] = []
    try:
        version = _version_from_pyproject()
    except (OSError, ValueError) as error:
        return [str(error)]

    if __version__ != version:
        problems.append(
            f"src/hdg version {__version__!r} does not match {version!r}"
        )

    manifests = (CODEX_MANIFEST, CLAUDE_MANIFEST)
    for manifest in manifests:
        try:
            manifest_version = json.loads(
                manifest.read_text(encoding="utf-8")
            )["version"]
        except (OSError, KeyError, json.JSONDecodeError) as error:
            problems.append(f"invalid manifest {manifest}: {error}")
            continue
        if manifest_version != version:
            problems.append(
                f"{manifest}: version {manifest_version!r} != {version!r}"
            )

    required_text = {
        ROOT / "README.md": f"当前版本：**{version}**",
        ROOT / "CHANGELOG.md": f"## {version}",
        ROOT / "docs" / "project-engineering.md": (
            f"{EXPECTED_TOOL_COUNT} 个模型可调用工具"
        ),
        ROOT / "docs" / "team-loop-templates.md": "实际改动内容和影响范围",
        ROOT / "docs" / "team-operations.md": "卸载",
        ROOT / "docs" / "host-compatibility.md": "真实宿主",
    }
    for path, needle in required_text.items():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            problems.append(f"missing required release file {path}: {error}")
            continue
        if needle not in text:
            problems.append(f"{path}: missing required text {needle!r}")

    required_files = (
        ROOT / ".gitlab-ci.yml",
        ROOT / "scripts" / "host_smoke.py",
        TEMPLATES / "light-change.json",
        TEMPLATES / "single-task-standard.json",
        TEMPLATES / "parallel-group-standard.json",
    )
    for path in required_files:
        if not path.is_file():
            problems.append(f"missing required release file {path}")

    tools = tool_definitions()
    if len(tools) != EXPECTED_TOOL_COUNT:
        problems.append(
            f"MCP tool count is {len(tools)}, expected {EXPECTED_TOOL_COUNT}"
        )
    by_name = {tool["name"]: tool for tool in tools}
    for tool in tools:
        name = tool.get("name", "<unnamed>")
        title = tool.get("title")
        if not isinstance(title, str) or not title.strip():
            problems.append(f"MCP tool {name}: missing human-readable title")
        input_schema = tool.get("inputSchema")
        if (
            not isinstance(input_schema, dict)
            or input_schema.get("type") != "object"
        ):
            problems.append(
                f"MCP tool {name}: inputSchema must be a root object"
            )
        output_schema = tool.get("outputSchema")
        if (
            not isinstance(output_schema, dict)
            or output_schema.get("type") != "object"
        ):
            problems.append(
                f"MCP tool {name}: outputSchema must be a root object"
            )
        annotations = tool.get("annotations")
        for hint in (
            "readOnlyHint",
            "destructiveHint",
            "idempotentHint",
            "openWorldHint",
        ):
            if (
                not isinstance(annotations, dict)
                or not isinstance(annotations.get(hint), bool)
            ):
                problems.append(
                    f"MCP tool {name}: annotations.{hint} must be boolean"
                )
    if "execution_mode" in inspect.signature(freeze_hierarchy).parameters:
        problems.append("freeze_hierarchy still exposes execution_mode")
    freeze_schema = by_name.get("freeze_hierarchy", {}).get(
        "inputSchema", {}
    )
    if "execution_mode" in freeze_schema.get("properties", {}):
        problems.append("freeze_hierarchy MCP schema exposes execution_mode")
    dispatch_schema = by_name.get("dispatch_loop", {}).get(
        "inputSchema", {}
    )
    dispatch_enum = (
        dispatch_schema.get("properties", {})
        .get("dispatch_mode", {})
        .get("enum")
    )
    if dispatch_enum != ["AUTO", "MANUAL"]:
        problems.append(
            "dispatch_loop dispatch_mode enum is "
            f"{dispatch_enum!r}, expected ['AUTO', 'MANUAL']"
        )

    try:
        codex_manifest = _json_object(CODEX_MANIFEST)
        _plugin_path(codex_manifest.get("skills"), field="skills")
        mcp_servers = codex_manifest.get("mcpServers")
        if not isinstance(mcp_servers, dict) or not mcp_servers:
            raise ValueError("mcpServers must be a non-empty inline object")
        declared_hooks = codex_manifest.get("hooks")
        if declared_hooks is not None:
            if _plugin_path(declared_hooks, field="hooks") != CODEX_HOOKS.resolve():
                raise ValueError("Codex hooks override must resolve to hooks.json")
        interface = codex_manifest.get("interface")
        if not isinstance(interface, dict):
            raise ValueError("interface must be an object")
        for field in (
            "displayName",
            "shortDescription",
            "longDescription",
            "developerName",
            "category",
        ):
            value = interface.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"interface.{field} must be non-empty")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        problems.append(f"invalid Agent Plugin manifest {CODEX_MANIFEST}: {error}")

    try:
        codex_hook_document = _json_object(CODEX_HOOKS)
        codex_events = codex_hook_document.get("hooks")
        if not isinstance(codex_events, dict):
            raise ValueError("hooks must be an object")
        if "StopFailure" in codex_events:
            raise ValueError("Codex hooks must not register Claude StopFailure")
        codex_commands = _hook_commands(codex_events)
        if not codex_commands:
            raise ValueError("at least one Codex command hook is required")
        for handler in codex_commands:
            command = handler.get("command")
            if not isinstance(command, str) or "${PLUGIN_ROOT}" not in command:
                raise ValueError(
                    "Codex command hooks must resolve through ${PLUGIN_ROOT}"
                )
            command_windows = handler.get("commandWindows")
            if (
                not isinstance(command_windows, str)
                or "%PLUGIN_ROOT%" not in command_windows
            ):
                raise ValueError(
                    "Codex command hooks must provide a PLUGIN_ROOT Windows command"
                )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        problems.append(f"invalid Codex hooks {CODEX_HOOKS}: {error}")

    try:
        claude_manifest = _json_object(CLAUDE_MANIFEST)
        declared_hooks = _plugin_path(
            claude_manifest.get("hooks"),
            field="Claude hooks",
        )
        if declared_hooks != CLAUDE_HOOKS.resolve():
            raise ValueError("Claude manifest must point to claude-hooks.json")
        claude_events = _json_object(CLAUDE_HOOKS).get("hooks")
        if not isinstance(claude_events, dict) or "StopFailure" not in claude_events:
            raise ValueError("Claude hooks must retain StopFailure")
        _hook_commands(claude_events)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        problems.append(f"invalid Claude hooks {CLAUDE_HOOKS}: {error}")

    try:
        marketplace = _json_object(REPO_MARKETPLACE)
        entries = marketplace.get("plugins")
        if not isinstance(entries, list):
            raise ValueError("plugins must be an array")
        matches = [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("name") == "delivery-graph"
        ]
        if len(matches) != 1:
            raise ValueError("exactly one delivery-graph entry is required")
        entry = matches[0]
        source = entry.get("source")
        if not isinstance(source, dict) or source.get("source") != "local":
            raise ValueError("Plugin source must be local")
        source_path = source.get("path")
        if not isinstance(source_path, str) or not source_path.startswith("./"):
            raise ValueError("local Plugin source path must start with ./")
        resolved_source = (ROOT / source_path).resolve()
        if resolved_source != PLUGIN.resolve():
            raise ValueError("local Plugin source must resolve to the bundle")
        policy = entry.get("policy")
        if not isinstance(policy, dict):
            raise ValueError("policy must be an object")
        if policy.get("installation") != "AVAILABLE":
            raise ValueError("policy.installation must be AVAILABLE")
        if policy.get("authentication") != "ON_INSTALL":
            raise ValueError("policy.authentication must be ON_INSTALL")
        category = entry.get("category")
        if not isinstance(category, str) or not category.strip():
            raise ValueError("category must be non-empty")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        problems.append(
            f"invalid repository Agent Plugin marketplace {REPO_MARKETPLACE}: {error}"
        )

    expected_profiles = {
        "light-change.json": "LIGHT",
        "single-task-standard.json": "STANDARD",
        "parallel-group-standard.json": "STANDARD",
    }
    for name, expected_profile in expected_profiles.items():
        path = TEMPLATES / name
        if not path.is_file():
            continue
        try:
            source = json.loads(path.read_text(encoding="utf-8"))
            hierarchy = validate_hierarchy_definition(source)
            graph = compile_delivery_graph(
                hierarchy,
                hierarchy_fingerprint=fingerprint(hierarchy),
            )
            actual_profile = graph_assurance_profile(graph)
        except Exception as error:  # validation must report every bad template
            problems.append(f"invalid hierarchy template {path}: {error}")
            continue
        if actual_profile != expected_profile:
            problems.append(
                f"{path}: profile {actual_profile!r} != {expected_profile!r}"
            )

    problems.extend(
        _compare_trees(
            ROOT / "src" / "hdg",
            SKILL_RUNTIME,
            excluded=("cli.py", "__main__.py"),
        )
    )
    problems.extend(_compare_trees(CANONICAL_SKILL, PLUGIN_SKILL))
    for forbidden in (
        CANONICAL_SKILL / "scripts" / "hdg.py",
        SKILL_RUNTIME / "cli.py",
        SKILL_RUNTIME / "__main__.py",
    ):
        if forbidden.exists():
            problems.append(f"generated Plugin payload restored CLI entry {forbidden}")
    return problems


def main() -> int:
    problems = validate_release()
    if problems:
        print("release candidate invalid:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print(
        "release candidate valid: "
        f"{__version__}; {EXPECTED_TOOL_COUNT} tools; "
        "LIGHT/STANDARD templates and generated payloads match"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
