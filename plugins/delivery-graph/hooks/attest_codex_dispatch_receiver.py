#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


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
    transcript_path = hook_input.get("transcript_path")
    cwd = hook_input.get("cwd")
    session_id = hook_input.get("session_id")
    if (
        not isinstance(tool_name, str)
        or not tool_name.endswith("__dispatch_loop")
        or not isinstance(tool_input, dict)
    ):
        return 0
    if tool_input.get("dispatch_mode") != "MANUAL":
        return _deny(
            "Codex AUTO receivers are claimed only by SubagentStart."
        )
    if tool_input.get("dispatch_reservation_id") is not None:
        return _deny("Codex MANUAL receivers cannot use an AUTO reservation.")
    if not all(
        isinstance(value, str) and value
        for value in (transcript_path, cwd, session_id)
    ):
        return _deny("Codex MANUAL dispatch lacks host lifecycle identity.")

    hook_directory = Path(__file__).resolve().parent
    sys.path.insert(0, str(hook_directory))
    from attest_codex_subagent_receiver import (
        _runtime_path,
        _subagent_host_metadata,
        _workspace_start,
    )

    candidates: list[str] = []
    top_level_agent_id = hook_input.get("agent_id")
    if isinstance(top_level_agent_id, str) and top_level_agent_id:
        candidates.append(top_level_agent_id)
    subagent = hook_input.get("subagent")
    nested_agent_id = (
        subagent.get("agent_id") if isinstance(subagent, dict) else None
    )
    if (
        isinstance(nested_agent_id, str)
        and nested_agent_id
        and nested_agent_id not in candidates
    ):
        candidates.append(nested_agent_id)
    if session_id not in candidates:
        candidates.append(session_id)

    receiver_context_id = None
    host_metadata = None
    for candidate in candidates:
        try:
            metadata = _subagent_host_metadata(
                transcript_path,
                receiver_context_id=candidate,
            )
        except (json.JSONDecodeError, KeyError, OSError, ValueError):
            metadata = None
        if metadata is not None:
            receiver_context_id = candidate
            host_metadata = metadata
            break
    if receiver_context_id is None or host_metadata is None:
        return _deny("Only a native Codex child may claim a MANUAL TASK.")
    parent_context_id = host_metadata["parentContextId"]

    root_id = tool_input.get("root_id")
    node_id = tool_input.get("node_id")
    if not all(
        isinstance(value, str) and value for value in (root_id, node_id)
    ):
        return _deny("Codex MANUAL dispatch target is incomplete.")

    sys.path.insert(0, str(_runtime_path()))
    from hdg.errors import GatedLoopError
    from hdg.graph_runtime import attest_loop_receiver
    from hdg.host_policy import ProjectRootBinding
    from hdg.repository import SchedulerRepository

    try:
        resolution = ProjectRootBinding.from_startup(
            _workspace_start(cwd)
        ).resolve_request(None, stateless=False)
        SchedulerRepository(resolution.project_root).assert_delivery_workspace(
            root_id,
            resolution.workspace_root,
        )
        attestation = attest_loop_receiver(
            root=resolution.project_root,
            root_id=root_id,
            node_id=node_id,
            receiver_context_id=receiver_context_id,
            parent_context_id=parent_context_id,
            host_adapter_id="codex",
            dispatch_reservation_id=None,
            dispatch_mode="MANUAL",
        )
    except (GatedLoopError, OSError, ValueError) as error:
        code = getattr(error, "code", "HOST_CONTEXT_INVALID")
        return _deny(f"Codex MANUAL attestation failed ({code}).")

    updated_input: dict[str, Any] = dict(tool_input)
    updated_input.pop("model_id", None)
    updated_input.pop("actual_model_id", None)
    updated_input["agent_id"] = "codex"
    updated_input["owner"] = receiver_context_id
    updated_input["receiver_context_id"] = receiver_context_id
    updated_input["receiver_attestation_id"] = attestation[
        "receiverAttestationId"
    ]
    observed_model = hook_input.get("model")
    if isinstance(observed_model, str) and observed_model:
        updated_input["actual_model_id"] = observed_model
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": (
                        "Attested the native Codex MANUAL receiver."
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
