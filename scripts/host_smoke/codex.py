"""Codex real-host smoke implementation.

Codex runs end to end in one harness-managed headless flow: a bootstrap
invocation prepares the Graph inside a host-created linked worktree, then
a resume invocation on the same thread claims and finishes the Loops.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import textwrap

from .common import (
    __version__,
    profile_name,
    render_smoke_prompt,
)


CODEX_WORKSPACE_REQUIREMENT = (
    "The current Codex session is already running in a host-created "
    "linked worktree on the Delivery feature branch. Use it as-is; "
    "do not create or switch another branch or worktree."
)

CODEX_EXECUTION_REQUIREMENT = (
    "Prepare and freeze the hierarchy, then call "
    "plan_dispatch_batch for the READY TASK and every later Review. "
    "Create a real host-native child for each assignment. The "
    "coordinator must never call dispatch_loop, implement a Loop, "
    "or reuse one receiver for multiple TASK or Review "
    "assignments."
)


def codex_plan_prompt(scenario: str) -> str:
    """Plan-review prompt shown before --execute; execution uses the
    bootstrap/resume prompts below."""
    return render_smoke_prompt(
        profile_name(scenario),
        host="codex",
        workspace_requirement=CODEX_WORKSPACE_REQUIREMENT,
        execution_requirement=CODEX_EXECUTION_REQUIREMENT,
    )


def codex_bootstrap_prompt(scenario: str) -> str:
    profile = profile_name(scenario)
    bootstrap_review_requirement = (
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
        Python command. {bootstrap_review_requirement}

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


def codex_resume_prompt(scenario: str) -> str:
    resume_review_requirement = (
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
        {result_progress_requirement} {resume_review_requirement} Every Review
        receiver must heartbeat immediately after its dispatch_loop claim and
        before inspecting the repository. Never dispatch to another Agent,
        fabricate final acceptance, commit, push, access the network, or
        modify anything outside this disposable repository. Stop when the
        frontier reaches RECORD_USER_CONFIRMATION; never call
        record_user_confirmation.
        """
    ).strip()


def codex_plugin_state() -> tuple[bool, list[str]]:
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


def codex_plugin_available() -> bool:
    return codex_plugin_state()[0]


def codex_host_command(
    *,
    workspace: Path,
    scenario: str,
    model: str | None,
    prompt: str | None = None,
) -> list[str]:
    prompt = prompt or codex_plan_prompt(scenario)
    executable = shutil.which("codex")
    if executable is None:
        raise RuntimeError("Codex CLI is not available on PATH")
    candidate_available, competing_plugin_ids = codex_plugin_state()
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


def codex_session_id(log_path: Path) -> str:
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


def codex_resume_command(
    *,
    session_id: str,
    prompt: str,
    model: str | None,
) -> list[str]:
    executable = shutil.which("codex")
    if executable is None:
        raise RuntimeError("Codex CLI is not available on PATH")
    _, competing_plugin_ids = codex_plugin_state()
    command = [executable]
    for plugin_id in competing_plugin_ids:
        command.extend(["-c", f'plugins."{plugin_id}".enabled=false'])
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


def run_codex_session(
    args: argparse.Namespace,
    *,
    workspace: Path,
    log_path: Path,
) -> subprocess.CompletedProcess:
    """Run the bootstrap invocation, then resume the same thread."""
    bootstrap_command = codex_host_command(
        workspace=workspace,
        scenario=args.scenario,
        model=args.model,
        prompt=codex_bootstrap_prompt(args.scenario),
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
    if completed.returncode != 0:
        return completed
    resume_command = codex_resume_command(
        session_id=codex_session_id(log_path),
        prompt=codex_resume_prompt(args.scenario),
        model=args.model,
    )
    with log_path.open(
        "a",
        encoding="utf-8",
        newline="\n",
    ) as log:
        return subprocess.run(
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
