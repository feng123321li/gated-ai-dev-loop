#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import uuid


DISPATCH_TASK_NAME = re.compile(r"^ld_([0-9a-f]{32})$")


def _runtime_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "layered-delivery"
        / "scripts"
    )


def _workspace_start(cwd: str) -> str:
    current = Path(cwd).expanduser().resolve(strict=True)
    candidates = (current, *current.parents)
    for candidate in candidates:
        if (
            candidate / ".layered-delivery" / "scheduler.db"
        ).is_file():
            return str(candidate)
        if (candidate / ".git").exists():
            return str(candidate)
    return str(current)


def _account_home() -> Path:
    """Resolve the OS account profile without trusting process env vars."""

    if os.name == "nt":
        import ctypes

        profile = ctypes.create_unicode_buffer(32768)
        result = ctypes.windll.shell32.SHGetFolderPathW(
            None,
            0x0028,  # CSIDL_PROFILE
            None,
            0,
            profile,
        )
        if result != 0 or not profile.value:
            raise OSError("Windows account profile is unavailable")
        return Path(profile.value).resolve()

    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def _trusted_codex_sessions_root() -> Path:
    codex_home = (_account_home() / ".codex").resolve()
    configured_home = os.environ.get("CODEX_HOME")
    if (
        configured_home
        and Path(configured_home).expanduser().resolve() != codex_home
    ):
        raise OSError(
            "Custom CODEX_HOME lacks a host-authenticated sessions root"
        )
    return (codex_home / "sessions").resolve()


def _session_meta_from_transcript(
    transcript_path: str,
    *,
    session_id: str,
) -> dict[str, object] | None:
    sessions_root = _trusted_codex_sessions_root()
    transcript = Path(transcript_path).expanduser().resolve(strict=True)
    try:
        transcript.relative_to(sessions_root)
    except ValueError:
        return None
    if transcript.suffix != ".jsonl":
        return None

    session_meta = None
    with transcript.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index >= 16:
                break
            event = json.loads(line)
            if event.get("type") == "session_meta":
                session_meta = event.get("payload")
                break
    if not isinstance(session_meta, dict):
        return None
    if session_meta.get("id") != session_id:
        return None
    return session_meta


def _subagent_claim_metadata(
    transcript_path: str,
    *,
    receiver_context_id: str,
) -> dict[str, str] | None:
    session_meta = _session_meta_from_transcript(
        transcript_path,
        session_id=receiver_context_id,
    )
    if session_meta is None:
        return None
    source = session_meta.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    thread_spawn = (
        subagent.get("thread_spawn")
        if isinstance(subagent, dict)
        else None
    )
    if not isinstance(thread_spawn, dict):
        return None
    parent_session_id = session_meta.get("session_id")
    if (
        not isinstance(parent_session_id, str)
        or not parent_session_id
        or thread_spawn.get("parent_thread_id") != parent_session_id
    ):
        return None
    transcript_agent_type = thread_spawn.get("agent_role") or "default"
    if not isinstance(transcript_agent_type, str):
        return None
    agent_path = thread_spawn.get("agent_path")
    if not isinstance(agent_path, str):
        return None
    task_name = agent_path.replace("\\", "/").rsplit("/", 1)[-1]
    matched = DISPATCH_TASK_NAME.fullmatch(task_name)
    if matched is None:
        return None
    return {
        "parentContextId": parent_session_id,
        "agentType": transcript_agent_type,
        "dispatchReservationId": str(uuid.UUID(hex=matched.group(1))),
    }


def _dispatch_reservation_from_transcript(
    transcript_path: str,
    *,
    receiver_context_id: str,
    parent_session_id: str,
    agent_type: str,
) -> str | None:
    metadata = _subagent_claim_metadata(
        transcript_path,
        receiver_context_id=receiver_context_id,
    )
    if (
        metadata is None
        or metadata["parentContextId"] != parent_session_id
        or metadata["agentType"] != agent_type
    ):
        return None
    return metadata["dispatchReservationId"]


def main() -> int:
    hook_input = json.load(sys.stdin)
    if (
        not isinstance(hook_input, dict)
        or hook_input.get("hook_event_name") != "SubagentStart"
    ):
        return 0
    parent_session_id = hook_input.get("session_id")
    receiver_context_id = hook_input.get("agent_id")
    agent_type = hook_input.get("agent_type")
    model_id = hook_input.get("model")
    cwd = hook_input.get("cwd")
    transcript_path = hook_input.get("transcript_path")
    if not all(
        isinstance(value, str) and value
        for value in (
            parent_session_id,
            receiver_context_id,
            agent_type,
            model_id,
            cwd,
            transcript_path,
        )
    ):
        return 0

    try:
        dispatch_reservation_id = _dispatch_reservation_from_transcript(
            transcript_path,
            receiver_context_id=receiver_context_id,
            parent_session_id=parent_session_id,
            agent_type=agent_type,
        )
    except (json.JSONDecodeError, KeyError, OSError, ValueError):
        return 0
    if dispatch_reservation_id is None:
        return 0

    sys.path.insert(0, str(_runtime_path()))
    from hdg.errors import GatedLoopError
    from hdg.graph_runtime import claim_codex_subagent_receiver
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
            return 0

        from hdg.repository import SchedulerRepository

        repository = SchedulerRepository(resolution.project_root)
        status = repository.workspace_status(
            workspace_root=resolution.workspace_root,
        )
        root_id = status.get("rootId")
        if (
            not isinstance(root_id, str)
            or status.get("executionMode") != "active"
        ):
            return 0
        assignment = claim_codex_subagent_receiver(
            root=resolution.project_root,
            root_id=root_id,
            workspace_root=resolution.workspace_root,
            receiver_context_id=receiver_context_id,
            parent_context_id=parent_session_id,
            actual_model_id=model_id,
            dispatch_reservation_id=dispatch_reservation_id,
        )
    except (GatedLoopError, OSError, ValueError):
        # SubagentStart cannot block startup. Missing or stale state stays
        # silent, so no new authoritative claim is created for this child.
        return 0

    context = {
        "agent_id": assignment["agentId"],
        "model_id": assignment["modelId"],
        "receiver_context_id": assignment["receiverContextId"],
        "root_id": assignment["rootId"],
        "node_id": assignment["nodeId"],
        "lease_expires_at": assignment["leaseExpiresAt"],
        "dispatch_reservation_id": assignment[
            "dispatchReservationId"
        ],
        "dispatch_reasoning_class": assignment[
            "dispatchReasoningClass"
        ],
        "dispatch_decision_fingerprint": assignment[
            "dispatchDecisionFingerprint"
        ],
    }
    additional_context = (
        "The host already claimed one exact Layered Delivery AUTO Loop for "
        "this Codex-native context before exposing this message. Do not call "
        "dispatch_loop. Load the assigned node with loop_context, immediately "
        "call heartbeat_loop once before any other tool, then execute it. "
        "Omit operation_id from heartbeat_loop, pause_loop, and "
        "record_loop_result because PreToolUse injects it for this child. "
        "This message contains no receiver or operation bearer.\n"
        "LAYERED_DELIVERY_ASSIGNMENT="
        + json.dumps(
            context,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SubagentStart",
                    "additionalContext": additional_context,
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
