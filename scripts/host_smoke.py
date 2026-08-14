#!/usr/bin/env python3
"""Probe Codex/Claude hosts and run an explicit real-host smoke scenario."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
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
from hdg.mcp_catalog import (  # noqa: E402
    DISPATCH_TOOL_PROFILE,
    PLANNING_TOOL_PROFILE,
    RECEIVER_TOOL_PROFILE,
    tool_names_for_profile,
)
from hdg.mcp_tools import tool_definitions  # noqa: E402


PROFILE_TOOL_PREFIXES = {
    PLANNING_TOOL_PROFILE: "mcp__plugin_delivery-graph_delivery-graph__",
    DISPATCH_TOOL_PROFILE: (
        "mcp__plugin_delivery-graph_delivery-graph-dispatch__"
    ),
    RECEIVER_TOOL_PROFILE: (
        "mcp__plugin_delivery-graph_delivery-graph-receiver__"
    ),
}


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
        "mcpServerCount": len(PROFILE_TOOL_PREFIXES),
        "profileToolCounts": {
            profile: len(tool_names_for_profile(profile))
            for profile in PROFILE_TOOL_PREFIXES
        },
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


def _prepare_workspace(path: Path, host: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise RuntimeError(f"smoke workspace must be empty: {path}")
    _run_checked(["git", "init", "-b", "main"], cwd=path)
    _run_checked(
        ["git", "config", "user.email", "delivery-graph-smoke@example.invalid"],
        cwd=path,
    )
    _run_checked(
        ["git", "config", "user.name", "Delivery Graph Smoke"], cwd=path
    )
    (path / "README.md").write_text(
        "# Delivery Graph host smoke\n", encoding="utf-8", newline="\n"
    )
    _run_checked(["git", "add", "README.md"], cwd=path)
    _run_checked(["git", "commit", "-m", "Initialize smoke workspace"], cwd=path)
    branch_ref = "feature/m_lf_host_smoke"
    if host == "claude-code":
        return path
    if host == "codex":
        worktree = path.with_name(f"{path.name}-worktree")
        _run_checked(
            [
                "git",
                "worktree",
                "add",
                "-b",
                branch_ref,
                str(worktree),
                "main",
            ],
            cwd=path,
        )
        return worktree
    raise RuntimeError(f"unsupported host: {host}")


def _prompt(scenario: str, host: str) -> str:
    profile = "LIGHT" if scenario == "light" else "STANDARD"
    review_requirement = (
        "Do not create review loops because this is a LIGHT single-TASK graph."
        if profile == "LIGHT"
        else (
            "Create the required TASK review and Delivery review loops and run "
            "each in an independent host-native child context."
        )
    )
    workspace_requirement = (
        "This Claude Code session owns the CURRENT_WORKSPACE_SERIAL turn in "
        "the current checkout on `main`. Keep all coordination in this main "
        "session and do not open another checkout or run any `git worktree` "
        "command. When the Controller requests current-branch preparation, "
        "use only the exact branch and base commit in its gitBinding."
        if host == "claude-code"
        else (
            "The current Codex session is already running in a host-created "
            "linked worktree on the Delivery feature branch. Use it as-is; "
            "do not create or switch another branch or worktree."
        )
    )
    execution_requirement = (
        "Preview the hierarchy with the TASK and acceptance conditions above, "
        "then call select_execution_mode(AUTOMATIC). If the Controller returns "
        "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION, create or "
        "switch to the exact gitBinding branch in the current checkout from "
        "its frozen base commit, then call resume_execution_mode with the "
        "retained rootId and fingerprints; never retry the execution choice. "
        "Once the Graph is ACTIVE, this main session calls "
        "plan_dispatch_batch and immediately starts one independent "
        "current-host child Agent for each assignment. The main session must "
        "never call dispatch_loop, implement a Loop, or reuse one receiver "
        "for multiple TASK or Review assignments."
        if host == "claude-code"
        else (
            "Prepare and freeze the hierarchy, then call "
            "plan_dispatch_batch for the READY TASK and every later Review. "
            "Create a real host-native child for each assignment. The "
            "coordinator must never call dispatch_loop, implement a Loop, "
            "or reuse one receiver for multiple TASK or Review "
            "assignments."
        )
    )
    receiver_start_requirement = (
        "The child must call dispatch_loop first with the exact reservation_id "
        "and decision_fingerprint, its own receiver_context_id, and a fresh "
        "operation_id, then read loop_context once. A short LIGHT receiver may "
        "finish without an explicit heartbeat; if it keeps working beyond the "
        "initial lease window, it must call heartbeat_loop before that window "
        "expires."
        if profile == "LIGHT"
        else (
            "Each child must call dispatch_loop first with the exact "
            "reservation_id and decision_fingerprint, its own "
            "receiver_context_id, and a fresh operation_id; then call "
            "loop_context once and heartbeat_loop with that operation_id "
            "immediately before any shell, file read/write, implementation "
            "analysis, or extra discovery."
        )
    )
    frozen_progress_requirement = (
        "The frozen TASK payload must explicitly allow a short LIGHT receiver "
        "to finish without heartbeat_loop. The claim establishes its initial "
        "lease; heartbeat only if work continues beyond that lease window."
        if profile == "LIGHT"
        else (
            "The frozen TASK payload and every child assignment must explicitly "
            "make this host check an acceptance condition: immediately after a "
            "successful claim, the child calls heartbeat_loop once before "
            "editing any file. The smoke is failed if LOOP_HEARTBEAT is absent "
            "even when the task is short."
        )
    )
    completion_progress_requirement = (
        "The TASK receiver may report its truthful final result directly when "
        "the short LIGHT task completes inside the initial lease; structured "
        "progress and heartbeat remain optional in that case."
        if profile == "LIGHT"
        else (
            "Every TASK or Review receiver must send at least one heartbeat "
            "immediately after claim before reporting structured progress and "
            "recording a truthful Loop result."
        )
    )
    return textwrap.dedent(
        f"""
        Run the official delivery-graph {profile} real-host smoke test in this
        disposable Git repository. The user has explicitly selected automatic
        execution. Use only the installed delivery-graph Skill and MCP tools;
        do not use a direct Python API or edit SQLite.

        Hard harness boundary: NEVER call record_user_confirmation. Automatic
        execution authorization is not final acceptance. When the frontier
        returns RECORD_USER_CONFIRMATION, stop immediately and leave the run
        ACTIVE at that gate. A COMPLETED run fails this smoke test.

        This release tests current-host dispatch only. The executor inventory and
        every assignment must contain only the host that started this session;
        never dispatch to another Agent even if another CLI is discovered.

        Treat this run as fresh and isolated. Do not read prior Codex/Claude
        sessions, history, caches, user configuration, or earlier smoke output;
        they are not evidence for this run. Use only this prompt, the installed
        Skill/MCP contract, and files inside the disposable workspace.
        Do not call TaskCreate, TaskUpdate, TaskList, or TaskGet; the MCP Graph
        is the only progress tracker for this disposable run.

        The harness has initialized a disposable Git repository on `main` as
        the primary checkout (the shared control root). {workspace_requirement}

        Create one root TASK with stable ID `t-smoke-artifact`. Its entire
        implementation is to create `smoke.txt` with exactly
        `delivery-graph smoke\\n`, then use one Python command to assert that
        exact content. Do not create any other business file, test scaffold, or
        additional verification command. Classify from this exact local change.
        {review_requirement}

        {frozen_progress_requirement}

        When HOST_NATIVE_DISPATCH_PLAN returns any assignment, start the
        current-host child immediately; do not read more documentation or
        inspect Plugin source.
        {receiver_start_requirement}

        {execution_requirement} {completion_progress_requirement} Continue
        until the frontier reaches RECORD_USER_CONFIRMATION. Stop there: final
        acceptance must remain a
        real user action and must not be fabricated. Do not commit, push,
        access the network, or modify anything outside this disposable
        repository.

        For Claude dispatch_loop, use `owner=claude-code` unless the host supplies
        another portable owner label; do not derive owner from a node ID.
        """
    ).strip()


def _codex_bootstrap_prompt(scenario: str) -> str:
    profile = "LIGHT" if scenario == "light" else "STANDARD"
    review_requirement = (
        "Use no Review Loop because this is a LIGHT single-TASK Graph."
        if profile == "LIGHT"
        else (
            "Include the required TASK Review and Delivery Review Loops, "
            "but do not dispatch them during this bootstrap turn."
        )
    )
    return textwrap.dedent(
        f"""
        Bootstrap the official delivery-graph {profile} Codex host smoke in
        this disposable feature worktree. Use only the installed Skill and
        MCP tools. Create one root TASK with stable ID `t-smoke-artifact`.
        Its frozen implementation is to create `smoke.txt` with exactly
        `delivery-graph smoke\\n` and verify that exact content with one
        Python command. {review_requirement}

        The user has already explicitly selected AUTOMATIC. Complete the
        baseline and execution-mode flow, adopt this existing host-created
        linked worktree, and call resume_execution_mode until the Graph is
        ACTIVE with the TASK READY. Do not claim any Loop, call
        plan_dispatch_batch, inspect implementation files, create smoke.txt,
        or record final user confirmation in this bootstrap turn. Stop as soon
        as the READY frontier is visible. A second invocation will resume the
        same ACTIVE Delivery.
        """
    ).strip()


def _codex_resume_prompt(scenario: str) -> str:
    review_requirement = (
        "This LIGHT Graph has no Review Loop."
        if scenario == "light"
        else (
            "After TASK success, reserve and start every required Review in "
            "a distinct host-native child with its own operation_id."
        )
    )
    receiver_progress_requirement = (
        "A short LIGHT receiver may finish without an explicit heartbeat. Its "
        "dispatch_loop claim establishes the initial lease; call heartbeat_loop "
        "only if work continues beyond that lease window."
        if scenario == "light"
        else (
            "The child must call heartbeat_loop with that operation_id before "
            "any implementation inspection or edit."
        )
    )
    result_progress_requirement = (
        "It may report the truthful final result directly when it finishes "
        "inside the initial lease."
        if scenario == "light"
        else "Report structured progress before recording the truthful result."
    )
    return textwrap.dedent(
        f"""
        Resume the already ACTIVE delivery-graph Codex host smoke. Do not
        preview, prepare, freeze, or create another Delivery. Call
        workspace_status and graph_frontier, then plan_dispatch_batch for the
        READY `t-smoke-artifact` TASK. Start its distinct current-host child
        immediately. The child must call dispatch_loop with the exact
        reservation_id and decision_fingerprint, its own receiver_context_id,
        and a fresh operation_id, then read loop_context once.
        {receiver_progress_requirement} The coordinator must not inspect or
        edit implementation files.

        Create only `smoke.txt` with exactly `delivery-graph smoke\\n`, then
        use one Python command to assert that exact content.
        {result_progress_requirement} {review_requirement} Every Review
        receiver must heartbeat immediately after its dispatch_loop claim and
        before inspecting the repository. Never dispatch to another Agent,
        fabricate final acceptance, commit, push, access the network, or
        modify anything outside this disposable repository. Stop when the
        frontier reaches RECORD_USER_CONFIRMATION; never call
        record_user_confirmation.
        """
    ).strip()


def _codex_plugin_state() -> tuple[bool, list[str]]:
    executable = shutil.which("codex")
    if executable is None:
        return False, []
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
        return False, []
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False, []
    candidate_available = False
    competing_plugin_ids: list[str] = []
    for plugin in payload.get("installed", []):
        if isinstance(plugin, str) and plugin == "delivery-graph":
            candidate_available = True
            continue
        if not isinstance(plugin, dict):
            continue
        if plugin.get("name") != "delivery-graph":
            continue
        if plugin.get("enabled") is False:
            continue
        if plugin.get("version") in (None, __version__):
            candidate_available = True
            continue
        plugin_id = plugin.get("pluginId")
        if isinstance(plugin_id, str) and re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._@/-]{0,255}",
            plugin_id,
        ):
            competing_plugin_ids.append(plugin_id)
    return candidate_available, sorted(set(competing_plugin_ids))


def _codex_plugin_available() -> bool:
    return _codex_plugin_state()[0]


def _host_command(
    host: str,
    *,
    workspace: Path,
    scenario: str,
    model: str | None,
    prompt: str | None = None,
) -> list[str]:
    prompt = prompt or _prompt(scenario, host)
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
            "Bash(git *)",
            *[
                prefix + tool_name
                for profile, prefix in PROFILE_TOOL_PREFIXES.items()
                for tool_name in sorted(tool_names_for_profile(profile))
                if tool_name != "record_user_confirmation"
            ],
        ]
        final_confirmation_tool = (
            "mcp__plugin_delivery-graph_delivery-graph__"
            "record_user_confirmation"
        )
        command = [
            executable,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--forward-subagent-text",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            ",".join(allowed_tools),
            "--disallowedTools",
            final_confirmation_tool,
            "--no-session-persistence",
            "--plugin-dir",
            str(ROOT / "plugins" / "delivery-graph"),
        ]
        if model:
            command.extend(["--model", model])
        command.append(prompt)
        return command
    if host == "codex":
        executable = shutil.which("codex")
        if executable is None:
            raise RuntimeError("Codex CLI is not available on PATH")
        candidate_available, competing_plugin_ids = _codex_plugin_state()
        if not candidate_available:
            raise RuntimeError(
                f"delivery-graph {__version__} must be installed in "
                "Codex before running the real-host smoke test"
            )
        command = [executable]
        for plugin_id in competing_plugin_ids:
            command.extend(
                [
                    "-c",
                    f'plugins."{plugin_id}".enabled=false',
                ]
            )
        command.extend(
            [
                "exec",
                "--json",
                "--sandbox",
                "workspace-write",
                "--cd",
                str(workspace),
            ]
        )
        if model:
            command.extend(["--model", model])
        command.append(prompt)
        return command
    raise RuntimeError(f"unsupported host: {host}")


def _codex_session_id(log_path: Path) -> str:
    for line in log_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "thread.started":
            continue
        thread_id = event.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    raise RuntimeError("Codex bootstrap output did not expose a thread id")


def _codex_resume_command(
    *,
    session_id: str,
    prompt: str,
    model: str | None,
) -> list[str]:
    executable = shutil.which("codex")
    if executable is None:
        raise RuntimeError("Codex CLI is not available on PATH")
    _, competing_plugin_ids = _codex_plugin_state()
    command = [executable]
    for plugin_id in competing_plugin_ids:
        command.extend(
            ["-c", f'plugins."{plugin_id}".enabled=false']
        )
    command.extend(
        [
            "exec",
            "resume",
            "--json",
        ]
    )
    if model:
        command.extend(["--model", model])
    command.extend([session_id, prompt])
    return command


def _git_worktree_roots(repo_root: Path) -> list[Path]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "worktree",
            "list",
            "--porcelain",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        return [repo_root]
    roots: list[Path] = []
    for line in completed.stdout.splitlines():
        if line.startswith("worktree "):
            candidate = Path(line[len("worktree ") :].strip())
            if candidate.is_dir():
                roots.append(candidate)
    return roots or [repo_root]


def _find_smoke_artifact(repo_root: Path) -> Path | None:
    ignored_roots = {".git", ".layered-delivery"}
    for workspace in _git_worktree_roots(repo_root):
        for path in sorted(workspace.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(workspace)
            if relative.parts and relative.parts[0] in ignored_roots:
                continue
            if path.name == "README.md":
                changed = subprocess.run(
                    ["git", "diff", "--quiet", "--", relative.as_posix()],
                    cwd=workspace,
                    capture_output=True,
                    check=False,
                )
                if changed.returncode != 1:
                    continue
            try:
                if path.stat().st_size > 64 * 1024:
                    continue
                content = path.read_text(encoding="utf-8").lower()
            except (OSError, UnicodeError):
                continue
            if "smoke" in content and (
                "delivery-graph" in content or "delivery graph" in content
            ):
                return path
    return None


def _validate_smoke(
    workspace: Path,
    scenario: str,
    host: str,
    *,
    control_root: Path | None = None,
) -> dict[str, object]:
    artifact = _find_smoke_artifact(control_root or workspace)
    if artifact is None:
        raise RuntimeError("host did not create a verifiable smoke artifact")
    database = (
        control_root or workspace
    ) / ".layered-delivery" / "scheduler.db"
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
    required_event_types = ["LOOP_CLAIMED", "LOOP_SUCCEEDED"]
    if scenario != "light":
        required_event_types.extend(["LOOP_HEARTBEAT", "LOOP_PROGRESS_REPORTED"])
    for event_type in required_event_types:
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
        "artifact": str(artifact),
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
        print(_prompt(args.scenario, args.host))
        return 2
    result: dict[str, object]
    temporary_path: Path
    with TemporaryDirectory(
        prefix="delivery-graph-host-smoke-",
        ignore_cleanup_errors=True,
    ) as temporary:
        temporary_root = Path(temporary)
        temporary_path = temporary_root
        control_root = temporary_root / "workspace"
        workspace = _prepare_workspace(control_root, args.host)
        log_path = temporary_root / "host-output.jsonl"
        if args.host == "codex":
            bootstrap_command = _host_command(
                args.host,
                workspace=workspace,
                scenario=args.scenario,
                model=args.model,
                prompt=_codex_bootstrap_prompt(args.scenario),
            )
            with log_path.open("w", encoding="utf-8", newline="\n") as log:
                completed = subprocess.run(
                    bootstrap_command,
                    cwd=workspace,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=args.timeout,
                    check=False,
                )
            if completed.returncode == 0:
                resume_command = _codex_resume_command(
                    session_id=_codex_session_id(log_path),
                    prompt=_codex_resume_prompt(args.scenario),
                    model=args.model,
                )
                with log_path.open(
                    "a",
                    encoding="utf-8",
                    newline="\n",
                ) as log:
                    completed = subprocess.run(
                        resume_command,
                        cwd=workspace,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=args.timeout,
                        check=False,
                    )
        else:
            command = _host_command(
                args.host,
                workspace=workspace,
                scenario=args.scenario,
                model=args.model,
            )
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
            result = _validate_smoke(
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
                    f"delivery-graph: {result['pluginVersion']}; "
                    f"tools: {result['toolCount']}; model invocation: no"
                )
            return 0
        return run_smoke(args)
    except (OSError, RuntimeError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        print(f"host smoke failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
