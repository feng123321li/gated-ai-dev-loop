#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys


PROTECTED_TOOLS = frozenset(
    {
        "heartbeat_loop",
        "pause_loop",
        "record_loop_result",
    }
)


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
    if not isinstance(tool_name, str) or not any(
        tool_name.endswith("__" + protected)
        for protected in PROTECTED_TOOLS
    ):
        return 0
    tool_input = hook_input.get("tool_input")
    session_id = hook_input.get("session_id")
    transcript_path = hook_input.get("transcript_path")
    model_id = hook_input.get("model")
    cwd = hook_input.get("cwd")
    if (
        not isinstance(tool_input, dict)
        or not all(
            isinstance(value, str) and value
            for value in (
                session_id,
                model_id,
                cwd,
            )
        )
    ):
        return _deny("Loop mutation lacks a host receiver context")

    hook_directory = Path(__file__).resolve().parent
    sys.path.insert(0, str(hook_directory))
    from attest_codex_subagent_receiver import (
        _runtime_path,
        _session_meta_from_transcript,
        _subagent_claim_metadata,
        _workspace_start,
    )

    session_meta = None
    if isinstance(transcript_path, str) and transcript_path:
        try:
            session_meta = _session_meta_from_transcript(
                transcript_path,
                session_id=session_id,
            )
        except (json.JSONDecodeError, KeyError, OSError, ValueError):
            session_meta = None

    host_adapter_id: str
    receiver_context_id: str
    parent_context_id: str
    dispatch_reservation_id: str | None
    if session_meta is not None:
        try:
            metadata = _subagent_claim_metadata(
                transcript_path,
                receiver_context_id=session_id,
            )
        except (json.JSONDecodeError, KeyError, OSError, ValueError):
            metadata = None
        if metadata is None:
            return _deny("Only the assigned Codex child may mutate this Loop")
        host_adapter_id = "codex"
        receiver_context_id = session_id
        parent_context_id = metadata["parentContextId"]
        dispatch_reservation_id = metadata["dispatchReservationId"]
    else:
        claude_agent_id = hook_input.get("agent_id")
        if not isinstance(claude_agent_id, str) or not claude_agent_id:
            return _deny("Loop mutation lacks an attested host child")
        host_adapter_id = "claude-code"
        receiver_context_id = claude_agent_id
        parent_context_id = session_id
        dispatch_reservation_id = None

    root_id = tool_input.get("root_id")
    node_id = tool_input.get("node_id")
    if not all(
        isinstance(value, str) and value for value in (root_id, node_id)
    ):
        return _deny("Loop mutation requires its exact root and node")

    sys.path.insert(0, str(_runtime_path()))
    from hdg.errors import GatedLoopError
    from hdg.graph_runtime import (
        authorize_claude_subagent_operation,
        authorize_codex_subagent_operation,
    )
    from hdg.host_policy import ProjectRootBinding
    from hdg.repository import DATABASE_FILE, GOVERNANCE_DIRECTORY

    try:
        binding = ProjectRootBinding.from_startup(_workspace_start(cwd))
        resolution = binding.resolve_request(None, stateless=False)
        database_path = (
            Path(resolution.project_root)
            / GOVERNANCE_DIRECTORY
            / DATABASE_FILE
        )
        if not database_path.is_file():
            return _deny("The workspace has no scheduler state")
        if host_adapter_id == "codex":
            authorization = authorize_codex_subagent_operation(
                root=resolution.project_root,
                root_id=root_id,
                node_id=node_id,
                workspace_root=resolution.workspace_root,
                receiver_context_id=receiver_context_id,
                parent_context_id=parent_context_id,
                model_id=model_id,
                dispatch_reservation_id=str(
                    dispatch_reservation_id
                ),
            )
        else:
            authorization = authorize_claude_subagent_operation(
                root=resolution.project_root,
                root_id=root_id,
                node_id=node_id,
                workspace_root=resolution.workspace_root,
                receiver_context_id=receiver_context_id,
                parent_context_id=parent_context_id,
                model_id=model_id,
            )
    except (GatedLoopError, OSError, ValueError):
        return _deny("The current host context does not own this Loop")

    updated_input = dict(tool_input)
    updated_input["operation_id"] = authorization["operationId"]
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": (
                        "Injected the attested host child operation"
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
