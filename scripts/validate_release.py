#!/usr/bin/env python3
"""Validate a layered-delivery release candidate without network access."""

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


CANONICAL_SKILL = ROOT / "skills" / "layered-delivery"
SKILL_RUNTIME = CANONICAL_SKILL / "scripts" / "hdg"
PLUGIN = ROOT / "plugins" / "layered-delivery"
PLUGIN_SKILL = PLUGIN / "skills" / "layered-delivery"
TEMPLATES = ROOT / "examples" / "team-loops"
EXPECTED_TOOL_COUNT = 30


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

    manifests = (
        PLUGIN / ".codex-plugin" / "plugin.json",
        PLUGIN / ".claude-plugin" / "plugin.json",
    )
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
