"""Shared CLI for the per-host real-host smoke implementations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from .claude import claude_prompt, run_claude_session
from .codex import codex_plan_prompt, run_codex_session
from .common import (
    __version__,
    host_version,
    prepare_workspace,
    PROFILE_TOOL_PREFIXES,
    validate_smoke,
)
from .zcode import run_zcode_smoke
from hdg.mcp_catalog import tool_names_for_profile
from hdg.mcp_tools import tool_definitions


def probe() -> dict[str, object]:
    """Return local facts only; never start an Agent or model request."""
    return {
        "pluginVersion": __version__,
        "toolCount": len(tool_definitions()),
        "mcpServerCount": len(PROFILE_TOOL_PREFIXES),
        "profileToolCounts": {
            profile: len(tool_names_for_profile(profile))
            for profile in PROFILE_TOOL_PREFIXES
        },
        "modelInvocationStarted": False,
        "hosts": {
            "codex": host_version("codex"),
            "claude-code": host_version("claude"),
            "zcode": host_version("zcode"),
        },
    }


PLAN_PROMPTS = {
    "codex": codex_plan_prompt,
    "claude-code": claude_prompt,
}


def run_smoke(args: argparse.Namespace) -> int:
    if args.host == "zcode":
        return run_zcode_smoke(args)
    if args.workspace_dir is not None or args.verify_only:
        raise RuntimeError(
            "--workspace-dir/--verify-only apply only to the zcode smoke"
        )
    if not args.execute:
        print(
            "real-host smoke is opt-in; inspect the plan, then repeat with --execute",
            file=sys.stderr,
        )
        print(PLAN_PROMPTS[args.host](args.scenario))
        return 2
    session_runner = (
        run_codex_session if args.host == "codex" else run_claude_session
    )
    result: dict[str, object]
    temporary_path: Path
    with TemporaryDirectory(
        prefix="delivery-graph-host-smoke-",
        ignore_cleanup_errors=True,
    ) as temporary:
        temporary_root = Path(temporary)
        temporary_path = temporary_root
        control_root = temporary_root / "workspace"
        workspace = prepare_workspace(control_root, args.host)
        log_path = temporary_root / "host-output.jsonl"
        completed = session_runner(
            args,
            workspace=workspace,
            log_path=log_path,
        )
        if completed.returncode != 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
            raise RuntimeError(
                f"{args.host} exited with {completed.returncode}; output tail:\n{tail}"
            )
        try:
            result = validate_smoke(
                workspace,
                args.scenario,
                args.host,
                control_root=control_root,
            )
        except RuntimeError as error:
            tail = log_path.read_text(
                encoding="utf-8", errors="replace"
            )[-12000:]
            raise RuntimeError(f"{error}; output tail:\n{tail}") from error
        result.update(
            {
                "host": args.host,
                "scenario": args.scenario,
                "pluginVersion": __version__,
            }
        )
        # Claude can outlive its stdio parent for a short Windows teardown
        # interval while the Plugin MCP process closes scheduler.db.
        time.sleep(2)
    if temporary_path.exists():
        print(
            "warning: Windows still holds the disposable smoke workspace; "
            f"best-effort cleanup was deferred: {temporary_path}",
            file=sys.stderr,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe_parser = subparsers.add_parser(
        "probe", help="read local host and Plugin facts without model calls"
    )
    probe_parser.add_argument("--json", action="store_true")
    run_parser = subparsers.add_parser(
        "run", help="run an isolated, explicitly authorized real-host smoke"
    )
    run_parser.add_argument(
        "--host", choices=("codex", "claude-code", "zcode"), required=True
    )
    run_parser.add_argument(
        "--scenario", choices=("light", "standard"), default="standard"
    )
    run_parser.add_argument("--model")
    run_parser.add_argument("--timeout", type=int, default=1800)
    run_parser.add_argument("--execute", action="store_true")
    run_parser.add_argument(
        "--workspace-dir",
        type=Path,
        help="zcode only: persistent disposable workspace kept between the "
        "prepare and verify phases",
    )
    run_parser.add_argument(
        "--verify-only",
        action="store_true",
        help="zcode only: verify a workspace already completed by a real "
        "ZCode session",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "probe":
            result = probe()
            if args.json:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            else:
                for host, details in result["hosts"].items():
                    availability = (
                        "available" if details["available"] else "missing"
                    )
                    print(
                        f"{host}: {availability}; {details.get('version') or '-'}"
                    )
                print(
                    f"delivery-graph: {result['pluginVersion']}; "
                    f"tools: {result['toolCount']}; model invocation: no"
                )
            return 0
        return run_smoke(args)
    except (OSError, RuntimeError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        print(f"host smoke failed: {error}", file=sys.stderr)
        return 1
