#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys


def _runtime_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "delivery-graph"
        / "scripts"
    )


def main() -> int:
    hook_input = json.load(sys.stdin)
    if (
        not isinstance(hook_input, dict)
        or hook_input.get("hook_event_name") != "PreToolUse"
    ):
        return 0
    tool_name = hook_input.get("tool_name")
    tool_input = hook_input.get("tool_input")
    tool_use_id = hook_input.get("tool_use_id")
    session_id = hook_input.get("session_id")
    transcript_path = hook_input.get("transcript_path")
    cwd = hook_input.get("cwd")
    if (
        not isinstance(tool_name, str)
        or "__" not in tool_name
        or not isinstance(tool_input, dict)
        or not all(
            isinstance(value, str) and value
            for value in (
                tool_use_id,
                session_id,
                transcript_path,
                cwd,
            )
        )
    ):
        return 0

    # Sensitive administrative tools are approval-gated by a later hook. Skip
    # workspace attestation for them so the 60s attestation cannot expire
    # while the user is still approving; they resolve via the control root.
    if (
        tool_name.rsplit("__", 1)[-1]
        in {
            "rebuild_graph_run",
            "cancel_graph_run",
            "unfreeze_task_requirement",
            "refreeze_task_requirement",
        }
    ):
        return 0

    hook_directory = Path(__file__).resolve().parent
    sys.path.insert(0, str(hook_directory))
    from attest_codex_subagent_receiver import (
        _session_meta_from_transcript,
        _workspace_start,
    )

    # The Codex adapter already obtains its workspace from trusted request
    # metadata. Never replace that channel with a Claude Hook credential.
    try:
        codex_meta = _session_meta_from_transcript(
            transcript_path,
            session_id=session_id,
        )
    except (json.JSONDecodeError, KeyError, OSError, ValueError):
        codex_meta = None
    if codex_meta is not None:
        return 0

    sys.path.insert(0, str(_runtime_path()))
    from hdg.errors import GatedLoopError
    from hdg.host_policy import ProjectRootBinding
    from hdg.repository import DATABASE_FILE, GOVERNANCE_DIRECTORY
    from hdg.repository import SchedulerRepository

    try:
        workspace = _workspace_start(cwd)
        resolution = ProjectRootBinding.from_startup(
            workspace
        ).resolve_request(None, stateless=False)
        database_path = (
            Path(resolution.project_root)
            / GOVERNANCE_DIRECTORY
            / DATABASE_FILE
        )
        if not database_path.is_file():
            return 0
        context_id = hook_input.get("agent_id")
        if not isinstance(context_id, str) or not context_id:
            context_id = session_id
        attestation = SchedulerRepository(
            resolution.project_root
        ).issue_host_workspace_attestation(
            host_adapter_id="claude-code",
            context_id=context_id,
            tool_name=tool_name.rsplit("__", 1)[-1],
            tool_use_id=tool_use_id,
            workspace_root=resolution.workspace_root,
        )
    except (GatedLoopError, OSError, ValueError):
        return 0

    updated_input = dict(tool_input)
    updated_input["_host_workspace_attestation"] = attestation
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": (
                        "Bound this MCP call to the host-observed workspace."
                    ),
                    "updatedInput": updated_input,
                }
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
