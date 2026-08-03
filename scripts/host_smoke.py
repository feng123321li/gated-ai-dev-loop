#!/usr/bin/env python3
"""Probe Codex/Claude hosts and run an explicit real-host smoke scenario."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import textwrap
import time


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
sys.path.insert(0, str(SOURCE))

from hdg import __version__  # noqa: E402
from hdg.mcp_tools import tool_definitions  # noqa: E402


def _host_version(executable: str) -> dict[str, object]:
    resolved = shutil.which(executable)
    if resolved is None:
        return {"available": False, "version": None}
    try:
        completed = subprocess.run(
            [resolved, "--version"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "version": None, "error": str(error)}
    version = (completed.stdout or completed.stderr).strip()
    return {
        "available": completed.returncode == 0,
        "version": version or None,
    }


def probe() -> dict[str, object]:
    """Return local facts only; never start an Agent or model request."""
    return {
        "pluginVersion": __version__,
        "toolCount": len(tool_definitions()),
        "modelInvocationStarted": False,
        "hosts": {
            "codex": _host_version("codex"),
            "claude-code": _host_version("claude"),
        },
    }


def _run_checked(command: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"command failed ({' '.join(command)}): {detail}")


def _prepare_workspace(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise RuntimeError(f"smoke workspace must be empty: {path}")
    _run_checked(["git", "init", "-b", "main"], cwd=path)
    _run_checked(
        ["git", "config", "user.email", "layered-delivery-smoke@example.invalid"],
        cwd=path,
    )
    _run_checked(
        ["git", "config", "user.name", "Layered Delivery Smoke"], cwd=path
    )
    (path / "README.md").write_text(
        "# Layered Delivery host smoke\n", encoding="utf-8", newline="\n"
    )
    _run_checked(["git", "add", "README.md"], cwd=path)
    _run_checked(["git", "commit", "-m", "Initialize smoke workspace"], cwd=path)
    _run_checked(
        ["git", "switch", "-c", "feature/m_lf_host_smoke"], cwd=path
    )


def _prompt(scenario: str) -> str:
    profile = "LIGHT" if scenario == "light" else "STANDARD"
    review_requirement = (
        "Do not create review loops because this is a LIGHT single-TASK graph."
        if profile == "LIGHT"
        else (
            "Create the required TASK review and Delivery review loops and run "
            "each in an independent host-native child context."
        )
    )
    return textwrap.dedent(
        f"""
        Run the official layered-delivery {profile} real-host smoke test in this
        disposable Git repository. The user has explicitly selected automatic
        execution. Use only the installed layered-delivery Skill and MCP tools;
        do not use a direct Python API or edit SQLite.

        This release tests current-host dispatch only. The executor inventory and
        every assignment must contain only the host that started this session;
        never dispatch to another Agent even if another CLI is discovered.

        The harness has already initialized the disposable development workspace
        on `feature/m_lf_host_smoke` from `main`. Use that current feature branch
        and its discovered gitBinding; do not create another branch or worktree.

        Create one root TASK whose implementation writes one small text artifact
        containing both `layered-delivery` (or `Layered Delivery`) and `smoke`,
        then verifies the artifact content with Python. Classify from the actual
        change content and impact scope.
        {review_requirement}

        The frozen TASK payload and every child assignment must explicitly make
        this host check an acceptance condition: immediately after a successful
        claim, the child calls heartbeat_loop once before editing any file. The
        smoke is failed if LOOP_HEARTBEAT is absent even when the task is short.

        When HOST_NATIVE_DISPATCH_PLAN is returned, start the current-host child
        immediately; do not read more documentation or inspect Plugin source.
        The child must call loop_context once and then dispatch_loop before any
        Bash, Read, Write, Edit, analysis of implementation, or extra discovery.
        A Claude child omits receiver_context_id and receiver_attestation_id so
        the PreToolUse hook can inject them; it must never invent placeholders.

        Prepare and freeze the hierarchy, honor the 30-second native routing
        adjustment window without asking another question, dispatch through a
        real host-native child Agent, and have every child send at least one
        heartbeat immediately after claim before reporting structured progress
        and recording a truthful Loop result. Continue until the frontier reaches
        RECORD_USER_CONFIRMATION. Stop there: final acceptance must remain a
        real user action and must not be fabricated. Do not commit, push, access
        the network, or modify anything outside this disposable repository.

        For Claude dispatch_loop, use `owner=claude-code` unless the host supplies
        another portable owner label; do not derive owner from a node ID.
        """
    ).strip()


def _codex_plugin_available() -> bool:
    executable = shutil.which("codex")
    if executable is None:
        return False
    completed = subprocess.run(
        [executable, "plugin", "list", "--json"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        return False
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False
    for plugin in payload.get("installed", []):
        if isinstance(plugin, str) and plugin == "layered-delivery":
            return True
        if isinstance(plugin, dict) and plugin.get("name") == "layered-delivery":
            return plugin.get("version") in (None, __version__)
    return False


def _host_command(
    host: str,
    *,
    workspace: Path,
    scenario: str,
    model: str | None,
) -> list[str]:
    prompt = _prompt(scenario)
    if host == "claude-code":
        executable = shutil.which("claude")
        if executable is None:
            raise RuntimeError("Claude Code is not available on PATH")
        allowed_tools = [
            "Agent",
            "Read",
            "Write",
            "Edit",
            "Bash(python *)",
            "Bash(py *)",
            *[
                "mcp__plugin_layered-delivery_layered-delivery__"
                f"{tool['name']}"
                for tool in tool_definitions()
            ],
        ]
        command = [
            executable,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-hook-events",
            "--forward-subagent-text",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            ",".join(allowed_tools),
            "--no-session-persistence",
            "--plugin-dir",
            str(ROOT / "plugins" / "layered-delivery"),
        ]
        if model:
            command.extend(["--model", model])
        command.append(prompt)
        return command
    if host == "codex":
        executable = shutil.which("codex")
        if executable is None:
            raise RuntimeError("Codex CLI is not available on PATH")
        if not _codex_plugin_available():
            raise RuntimeError(
                "layered-delivery 0.32.0 must be installed in Codex before "
                "running the real-host smoke test"
            )
        command = [
            executable,
            "exec",
            "--json",
            "--ephemeral",
            "--dangerously-bypass-hook-trust",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(workspace),
        ]
        if model:
            command.extend(["--model", model])
        command.append(prompt)
        return command
    raise RuntimeError(f"unsupported host: {host}")


def _find_smoke_artifact(workspace: Path) -> Path | None:
    ignored_roots = {".git", ".layered-delivery"}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.name == "README.md":
            continue
        relative = path.relative_to(workspace)
        if relative.parts and relative.parts[0] in ignored_roots:
            continue
        try:
            if path.stat().st_size > 64 * 1024:
                continue
            content = path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeError):
            continue
        if "smoke" in content and (
            "layered-delivery" in content or "layered delivery" in content
        ):
            return path
    return None


def _validate_smoke(
    workspace: Path,
    scenario: str,
    host: str,
) -> dict[str, object]:
    artifact = _find_smoke_artifact(workspace)
    if artifact is None:
        raise RuntimeError("host did not create a verifiable smoke artifact")
    database = workspace / ".layered-delivery" / "scheduler.db"
    if not database.is_file():
        raise RuntimeError("host did not create scheduler.db through the Plugin")
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        run = connection.execute(
            "SELECT run_id, root_id, status FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if run is None:
            raise RuntimeError("scheduler.db contains no Graph run")
        events = {
            row["event_type"]: row["count"]
            for row in connection.execute(
                "SELECT event_type, COUNT(*) AS count FROM graph_events "
                "WHERE run_id = ? GROUP BY event_type",
                (run["run_id"],),
            )
        }
        claim_payloads = [
            json.loads(row["payload_json"])
            for row in connection.execute(
                "SELECT payload_json FROM graph_events "
                "WHERE run_id = ? AND event_type = 'LOOP_CLAIMED'",
                (run["run_id"],),
            )
        ]
        nodes = connection.execute(
            "SELECT node_id, status FROM node_runs WHERE run_id = ?",
            (run["run_id"],),
        ).fetchall()
    minimum_successes = 1 if scenario == "light" else 3
    claimed_agents = {
        payload.get("agentId")
        for payload in claim_payloads
        if isinstance(payload.get("agentId"), str)
    }
    if claimed_agents != {host}:
        raise RuntimeError(
            "real-host smoke must claim only the current Agent; "
            f"expected {host!r}, found {sorted(claimed_agents)!r}"
        )
    for event_type in (
        "LOOP_CLAIMED",
        "LOOP_HEARTBEAT",
        "LOOP_PROGRESS_REPORTED",
        "LOOP_SUCCEEDED",
    ):
        if events.get(event_type, 0) < minimum_successes:
            raise RuntimeError(
                f"expected at least {minimum_successes} {event_type} events; "
                f"found {events.get(event_type, 0)}"
            )
    if run["status"] not in {"ACTIVE", "COMPLETED"}:
        raise RuntimeError(f"unexpected Graph run status: {run['status']}")
    if events.get("USER_CONFIRMED", 0) > 0:
        raise RuntimeError("host smoke fabricated final user confirmation")
    return {
        "rootId": run["root_id"],
        "runId": run["run_id"],
        "runStatus": run["status"],
        "artifact": artifact.relative_to(workspace).as_posix(),
        "claimedAgents": sorted(claimed_agents),
        "events": events,
        "nodes": [dict(node) for node in nodes],
        "finalUserConfirmationFabricated": False,
    }


def run_smoke(args: argparse.Namespace) -> int:
    if not args.execute:
        print(
            "real-host smoke is opt-in; inspect the plan, then repeat with --execute",
            file=sys.stderr,
        )
        print(_prompt(args.scenario))
        return 2
    result: dict[str, object]
    temporary_path: Path
    with TemporaryDirectory(
        prefix="layered-delivery-host-smoke-",
        ignore_cleanup_errors=True,
    ) as temporary:
        temporary_root = Path(temporary)
        temporary_path = temporary_root
        workspace = temporary_root / "workspace"
        _prepare_workspace(workspace)
        command = _host_command(
            args.host,
            workspace=workspace,
            scenario=args.scenario,
            model=args.model,
        )
        log_path = temporary_root / "host-output.jsonl"
        with log_path.open("w", encoding="utf-8", newline="\n") as log:
            completed = subprocess.run(
                command,
                cwd=workspace,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=args.timeout,
                check=False,
            )
        if completed.returncode != 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
            raise RuntimeError(
                f"{args.host} exited with {completed.returncode}; output tail:\n{tail}"
            )
        try:
            result = _validate_smoke(workspace, args.scenario, args.host)
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
        "--host", choices=("codex", "claude-code"), required=True
    )
    run_parser.add_argument(
        "--scenario", choices=("light", "standard"), default="standard"
    )
    run_parser.add_argument("--model")
    run_parser.add_argument("--timeout", type=int, default=1800)
    run_parser.add_argument("--execute", action="store_true")
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
                    availability = "available" if details["available"] else "missing"
                    print(f"{host}: {availability}; {details.get('version') or '-'}")
                print(
                    f"layered-delivery: {result['pluginVersion']}; "
                    f"tools: {result['toolCount']}; model invocation: no"
                )
            return 0
        return run_smoke(args)
    except (OSError, RuntimeError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        print(f"host smoke failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
