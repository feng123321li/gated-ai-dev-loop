#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys


PROTECTED_TOOLS = frozenset(
    {
        "heartbeat_loop",
        "report_loop_progress",
        "pause_loop",
        "record_loop_result",
    }
)


def _claude_model_from_transcript(
    transcript_path: str,
    *,
    receiver_context_id: str,
    parent_context_id: str,
    tool_name: str,
    tool_input: dict[str, object],
    tool_use_id: str,
) -> str | None:
    """Read a display-only actual model after the tool use is persisted."""

    transcript = Path(transcript_path).expanduser().resolve(strict=True)
    if (
        transcript.suffix != ".jsonl"
        or not transcript.is_file()
        or "/" in receiver_context_id
        or "\\" in receiver_context_id
    ):
        return None

    candidates = [
        transcript,
        transcript.with_suffix("")
        / "subagents"
        / f"agent-{receiver_context_id}.jsonl",
    ]
    matched_model = None
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, ValueError):
            continue
        if resolved.suffix != ".jsonl" or not resolved.is_file():
            continue
        with resolved.open("r", encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                if (
                    not isinstance(event, dict)
                    or event.get("agentId") != receiver_context_id
                    or event.get("sessionId") != parent_context_id
                ):
                    continue
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                model_id = message.get("model")
                if (
                    message.get("role") != "assistant"
                    or not isinstance(content, list)
                    or not isinstance(model_id, str)
                    or not model_id
                ):
                    continue
                for item in content:
                    if (
                        not isinstance(item, dict)
                        or item.get("type") != "tool_use"
                        or item.get("id") != tool_use_id
                    ):
                        continue
                    if (
                        item.get("name") != tool_name
                        or item.get("input") != tool_input
                        or (
                            matched_model is not None
                            and matched_model != model_id
                        )
                    ):
                        return None
                    matched_model = model_id
    return matched_model


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
    cwd = hook_input.get("cwd")
    if (
        not isinstance(tool_input, dict)
        or not all(
            isinstance(value, str) and value
            for value in (
                session_id,
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
    codex_receiver_context_id = None
    if isinstance(transcript_path, str) and transcript_path:
        candidate_context_ids = [session_id]
        hook_agent_id = hook_input.get("agent_id")
        if (
            isinstance(hook_agent_id, str)
            and hook_agent_id
            and hook_agent_id != session_id
        ):
            candidate_context_ids.append(hook_agent_id)
        for candidate_context_id in candidate_context_ids:
            try:
                session_meta = _session_meta_from_transcript(
                    transcript_path,
                    session_id=candidate_context_id,
                )
            except (json.JSONDecodeError, KeyError, OSError, ValueError):
                session_meta = None
            if session_meta is not None:
                codex_receiver_context_id = candidate_context_id
                break

    host_adapter_id: str
    receiver_context_id: str
    parent_context_id: str
    dispatch_reservation_id: str | None
    if session_meta is not None:
        if codex_receiver_context_id is None:
            return _deny("Loop mutation lacks a host receiver context")
        try:
            metadata = _subagent_claim_metadata(
                transcript_path,
                receiver_context_id=codex_receiver_context_id,
            )
        except (json.JSONDecodeError, KeyError, OSError, ValueError):
            metadata = None
        source = session_meta.get("source")
        subagent = source.get("subagent") if isinstance(source, dict) else None
        thread_spawn = (
            subagent.get("thread_spawn")
            if isinstance(subagent, dict)
            else None
        )
        session_parent = session_meta.get("session_id")
        if (
            not isinstance(thread_spawn, dict)
            or not isinstance(session_parent, str)
            or not session_parent
            or thread_spawn.get("parent_thread_id") != session_parent
        ):
            return _deny("Only a Codex child may mutate this Loop")
        host_adapter_id = "codex"
        receiver_context_id = codex_receiver_context_id
        parent_context_id = session_parent
        dispatch_reservation_id = (
            metadata["dispatchReservationId"]
            if metadata is not None
            else None
        )
    else:
        claude_agent_id = hook_input.get("agent_id")
        if (
            not isinstance(claude_agent_id, str)
            or not claude_agent_id
        ):
            return _deny("Loop mutation lacks an attested host child")
        host_adapter_id = "claude-code"
        receiver_context_id = claude_agent_id
        parent_context_id = session_id
        dispatch_reservation_id = None
        # Claude may append the current tool_use only after PreToolUse
        # returns. The consumed claim-time receiver attestation is the
        # durable authority for subsequent Loop mutations, so do not race
        # the transcript here.

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
                dispatch_reservation_id=dispatch_reservation_id,
            )
        else:
            authorization = authorize_claude_subagent_operation(
                root=resolution.project_root,
                root_id=root_id,
                node_id=node_id,
                workspace_root=resolution.workspace_root,
                receiver_context_id=receiver_context_id,
                parent_context_id=parent_context_id,
            )
    except GatedLoopError as error:
        return _deny(
            "The current host context does not own this Loop "
            f"({error.code})"
        )
    except (OSError, ValueError):
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
