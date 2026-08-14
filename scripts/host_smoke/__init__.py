"""Real-host smoke harness for delivery-graph.

Codex (codex.py) and Claude Code (claude.py) run end to end in one
headless invocation. ZCode (zcode.py) has no headless contract, so its
smoke is two-phase: the harness prepares a persistent disposable
workspace and writes the prompt, a real ZCode session completes it, and
the harness verifies the scheduler.db evidence chain. Host-neutral
evidence rules and the shared prompt frame live in common.py; run with
``python -m scripts.host_smoke``.
"""

from __future__ import annotations

from .claude import claude_host_command, claude_prompt, run_claude_session
from .cli import build_parser, main, probe, run_smoke
from .codex import (
    codex_bootstrap_prompt,
    codex_host_command,
    codex_plan_prompt,
    codex_plugin_available,
    codex_plugin_state,
    codex_resume_command,
    codex_resume_prompt,
    codex_session_id,
    run_codex_session,
)
from .common import (
    find_smoke_artifact,
    host_version,
    HOST_LABELS,
    prepare_workspace,
    PROFILE_TOOL_PREFIXES,
    validate_smoke,
)
from .zcode import run_zcode_smoke, zcode_prompt


__all__ = (
    "HOST_LABELS",
    "PROFILE_TOOL_PREFIXES",
    "build_parser",
    "claude_host_command",
    "claude_prompt",
    "codex_bootstrap_prompt",
    "codex_host_command",
    "codex_plan_prompt",
    "codex_plugin_available",
    "codex_plugin_state",
    "codex_resume_command",
    "codex_resume_prompt",
    "codex_session_id",
    "find_smoke_artifact",
    "host_version",
    "main",
    "prepare_workspace",
    "probe",
    "run_claude_session",
    "run_codex_session",
    "run_smoke",
    "run_zcode_smoke",
    "validate_smoke",
    "zcode_prompt",
)
