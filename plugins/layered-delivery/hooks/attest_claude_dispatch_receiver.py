#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


def _runtime_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "layered-delivery"
        / "scripts"
    )


def _block(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def main() -> int:
    hook_input = json.load(sys.stdin)
    if (
        not isinstance(hook_input, dict)
        or hook_input.get("hook_event_name") != "PreToolUse"
    ):
        return 0
    tool_input = hook_input.get("tool_input")
    agent_id = hook_input.get("agent_id")
    parent_session_id = hook_input.get("session_id")
    cwd = hook_input.get("cwd")
    if (
        not isinstance(tool_input, dict)
        or not isinstance(agent_id, str)
        or not isinstance(parent_session_id, str)
        or not isinstance(cwd, str)
    ):
        return _block(
            "Layered Delivery dispatch_loop must run inside a Claude "
            "subagent so the host can attest its independent context."
        )
    root_id = tool_input.get("root_id")
    node_id = tool_input.get("node_id")
    reservation_id = tool_input.get("dispatch_reservation_id")
    if not isinstance(root_id, str) or not isinstance(node_id, str):
        return _block("Layered Delivery dispatch target is incomplete.")
    if reservation_id is not None and not isinstance(reservation_id, str):
        return _block("Layered Delivery dispatch reservation is invalid.")

    sys.path.insert(0, str(_runtime_path()))
    from hdg.errors import GatedLoopError
    from hdg.graph_runtime import attest_loop_receiver
    from hdg.host_policy import ProjectRootBinding
    from hdg.repository import SchedulerRepository

    try:
        binding = ProjectRootBinding.from_startup(cwd)
        resolution = binding.resolve_request(None, stateless=False)
        SchedulerRepository(resolution.project_root).assert_delivery_workspace(
            root_id,
            resolution.workspace_root,
        )
        attestation = attest_loop_receiver(
            root=resolution.project_root,
            root_id=root_id,
            node_id=node_id,
            receiver_context_id=agent_id,
            parent_context_id=parent_session_id,
            host_adapter_id="claude-code",
            dispatch_reservation_id=reservation_id,
        )
    except (GatedLoopError, OSError, ValueError) as error:
        return _block(f"Layered Delivery receiver attestation failed: {error}")

    updated_input: dict[str, Any] = dict(tool_input)
    updated_input["agent_id"] = "claude-code"
    updated_input["receiver_context_id"] = agent_id
    updated_input["receiver_attestation_id"] = attestation[
        "receiverAttestationId"
    ]
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": (
                        "Host attested the spawned Claude receiver."
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
