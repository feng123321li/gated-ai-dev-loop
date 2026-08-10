#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys


def _deny(reason: str) -> int:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


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
    cwd = hook_input.get("cwd")
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return 0
    short_tool_name = tool_name.rsplit("__", 1)[-1]
    if short_tool_name not in {
        "plan_dispatch_batch",
        "claim_current_task",
    }:
        return 0
    if not all(
        isinstance(value, str) and value
        for value in (tool_use_id, session_id, cwd)
    ):
        return _deny("Delivery Graph AUTO preflight lacks host identity.")

    hook_directory = Path(__file__).resolve().parent
    sys.path.insert(0, str(hook_directory))
    from attest_codex_subagent_receiver import (
        _runtime_path,
        _session_meta_from_transcript,
        _workspace_start,
    )

    sys.path.insert(0, str(_runtime_path()))
    from hdg.errors import GatedLoopError
    from hdg.host_policy import ProjectRootBinding
    from hdg.repository import SchedulerRepository

    try:
        resolution = ProjectRootBinding.from_startup(
            _workspace_start(cwd)
        ).resolve_request(None, stateless=False)
        root_id = tool_input.get("root_id")
        if not isinstance(root_id, str) or not root_id:
            return _deny("Delivery Graph AUTO preflight has no root_id.")
        repository = SchedulerRepository(resolution.project_root)
        repository.assert_delivery_workspace(
            root_id,
            resolution.workspace_root,
        )
        if short_tool_name == "claim_current_task":
            transcript_path = hook_input.get("transcript_path")
            if not isinstance(transcript_path, str) or not transcript_path:
                return _deny(
                    "Delivery Graph current TASK claim lacks its transcript."
                )
            session_meta = _session_meta_from_transcript(
                transcript_path,
                session_id=session_id,
            )
            if session_meta is None:
                return _deny(
                    "Delivery Graph current TASK claim lacks session metadata."
                )
            source = session_meta.get("source")
            if (
                isinstance(source, dict)
                and isinstance(source.get("subagent"), dict)
            ):
                return _deny(
                    "Only the top-level Delivery task may claim the current TASK."
                )
            attestation = repository.issue_host_workspace_attestation(
                host_adapter_id="codex",
                context_id=session_id,
                tool_name="delivery_session",
                tool_use_id="claim-session:" + tool_use_id,
                workspace_root=resolution.workspace_root,
                lifetime_seconds=86_400,
            )
        else:
            attestation = repository.issue_host_workspace_attestation(
                host_adapter_id="codex",
                context_id=session_id,
                tool_name="plan_dispatch_batch",
                tool_use_id=tool_use_id,
                workspace_root=resolution.workspace_root,
            )
    except (GatedLoopError, OSError, ValueError) as error:
        code = getattr(error, "code", "HOST_CONTEXT_INVALID")
        return _deny(f"Delivery Graph AUTO preflight failed ({code}).")

    updated_input = dict(tool_input)
    if short_tool_name == "claim_current_task":
        updated_input["_host_session_attestation"] = attestation
        updated_input["_host_session_context_id"] = session_id
    else:
        updated_input["_host_workspace_attestation"] = attestation
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": (
                        "Attested the Codex AUTO dispatch preflight."
                        if short_tool_name == "plan_dispatch_batch"
                        else "Attested the current Codex TASK session."
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
