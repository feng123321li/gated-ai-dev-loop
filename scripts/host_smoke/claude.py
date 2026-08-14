"""Claude Code real-host smoke implementation.

Claude Code runs end to end in one headless `claude --print` invocation
against the primary checkout; the final user confirmation tool is hard
denied so the run stops at RECORD_USER_CONFIRMATION.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess

from .common import (
    PRIMARY_CHECKOUT_EXECUTION_REQUIREMENT,
    PROFILE_TOOL_PREFIXES,
    profile_name,
    render_smoke_prompt,
    ROOT,
)
from hdg.mcp_catalog import tool_names_for_profile


def claude_prompt(scenario: str) -> str:
    return render_smoke_prompt(
        profile_name(scenario),
        host="claude-code",
        workspace_requirement=(
            "This Claude Code session owns the CURRENT_WORKSPACE_SERIAL turn "
            "in the current checkout on `main`. Keep all coordination in "
            "this main session and do not open another checkout or run any "
            "`git worktree` command. When the Controller requests "
            "current-branch preparation, use only the exact branch and base "
            "commit in its gitBinding."
        ),
        execution_requirement=PRIMARY_CHECKOUT_EXECUTION_REQUIREMENT,
    )


def claude_host_command(
    *,
    workspace: Path,
    scenario: str,
    model: str | None,
) -> list[str]:
    prompt = claude_prompt(scenario)
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


def run_claude_session(
    args: argparse.Namespace,
    *,
    workspace: Path,
    log_path: Path,
) -> subprocess.CompletedProcess:
    """Run the single headless Claude Code invocation."""
    command = claude_host_command(
        workspace=workspace,
        scenario=args.scenario,
        model=args.model,
    )
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        return subprocess.run(
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
