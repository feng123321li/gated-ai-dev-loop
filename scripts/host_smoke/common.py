"""Shared evidence rules and prompt scaffolding for the real-host smoke.

One module per host builds its own prompt, host command, and session
runner; this module owns only the host-neutral parts: disposable
workspace preparation, smoke artifact discovery, scheduler.db evidence
validation, and the shared prompt frame.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[2]
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

HOST_LABELS = {
    "claude-code": "Claude Code",
    "zcode": "ZCode",
    "codex": "Codex",
}

# Claude Code and ZCode both run the Graph in the primary checkout under
# CURRENT_WORKSPACE_SERIAL; Codex runs it in a host-created worktree.
PRIMARY_CHECKOUT_EXECUTION_REQUIREMENT = (
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
)


def host_version(executable: str) -> dict[str, object]:
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


def run_checked(command: list[str], *, cwd: Path) -> None:
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


def prepare_workspace(path: Path, host: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise RuntimeError(f"smoke workspace must be empty: {path}")
    run_checked(["git", "init", "-b", "main"], cwd=path)
    run_checked(
        ["git", "config", "user.email", "delivery-graph-smoke@example.invalid"],
        cwd=path,
    )
    run_checked(
        ["git", "config", "user.name", "Delivery Graph Smoke"], cwd=path
    )
    (path / "README.md").write_text(
        "# Delivery Graph host smoke\n", encoding="utf-8", newline="\n"
    )
    run_checked(["git", "add", "README.md"], cwd=path)
    run_checked(["git", "commit", "-m", "Initialize smoke workspace"], cwd=path)
    branch_ref = "feature/m_lf_host_smoke"
    if host in {"claude-code", "zcode"}:
        return path
    if host == "codex":
        worktree = path.with_name(f"{path.name}-worktree")
        run_checked(
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


def find_smoke_artifact(repo_root: Path) -> Path | None:
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


def validate_smoke(
    workspace: Path,
    scenario: str,
    host: str,
    *,
    control_root: Path | None = None,
) -> dict[str, object]:
    artifact = find_smoke_artifact(control_root or workspace)
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
    required_event_types = [
        "LOOP_CLAIMED",
        "LOOP_HEARTBEAT",
        "LOOP_SUCCEEDED",
    ]
    if scenario != "light":
        required_event_types.append("LOOP_PROGRESS_REPORTED")
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


def profile_name(scenario: str) -> str:
    return "LIGHT" if scenario == "light" else "STANDARD"


def review_requirement(profile: str) -> str:
    return (
        "Do not create review loops because this is a LIGHT single-TASK graph."
        if profile == "LIGHT"
        else (
            "Create the required TASK review and Delivery review loops and run "
            "each in an independent host-native child context."
        )
    )


def frozen_progress_requirement(profile: str) -> str:
    return (
        "The frozen TASK payload and every child assignment must explicitly "
        "make this host check an acceptance condition for every assurance "
        "profile: immediately after a successful claim, the child calls "
        "heartbeat_loop once before interpreting loop_context or inspecting "
        "any file. The smoke is failed if LOOP_HEARTBEAT is absent even when "
        "the task is short."
    )


def completion_progress_requirement(profile: str) -> str:
    return (
        "The TASK receiver must send an immediate heartbeat, but may omit "
        "nonessential structured progress before its truthful final result."
        if profile == "LIGHT"
        else (
            "Every TASK or Review receiver must send at least one heartbeat "
            "immediately after claim before reporting structured progress and "
            "recording a truthful Loop result."
        )
    )


def receiver_start_requirement(profile: str) -> str:
    return (
        "Each child must call dispatch_loop first with the exact "
        "reservation_id and decision_fingerprint, its own receiver_context_id, "
        "and a fresh operation_id; then call heartbeat_loop with that operation "
        "immediately before interpreting the returned Loop context or doing any "
        "shell, file read/write, implementation analysis, or extra discovery. "
        "NOT_REQUIRED does not cancel the next heartbeat."
    )


def render_smoke_prompt(
    profile: str,
    *,
    host: str,
    workspace_requirement: str,
    execution_requirement: str,
) -> str:
    """Render the shared smoke prompt frame for one host implementation."""
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
        {review_requirement(profile)}

        {frozen_progress_requirement(profile)}

        When HOST_NATIVE_DISPATCH_PLAN returns any assignment, start the
        current-host child immediately; do not read more documentation or
        inspect Plugin source.
        {receiver_start_requirement(profile)}

        {execution_requirement} {completion_progress_requirement(profile)} Continue
        until the frontier reaches RECORD_USER_CONFIRMATION. Stop there: final
        acceptance must remain a
        real user action and must not be fabricated. Do not commit, push,
        access the network, or modify anything outside this disposable
        repository.

        For {HOST_LABELS[host]} dispatch_loop, use `owner={host}` unless the
        host supplies another portable owner label; do not derive owner from
        a node ID.
        """
    ).strip()
