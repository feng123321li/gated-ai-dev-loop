#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import time
import uuid


DISPATCH_TASK_NAME = re.compile(r"^ld_([0-9a-f]{32})$")
SESSION_IDENTIFIER = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,255}$")
CHILD_TRANSCRIPT_WAIT_SECONDS = 2.0
CHILD_TRANSCRIPT_POLL_SECONDS = 0.05


def _runtime_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "delivery-graph"
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


def _trusted_codex_sessions_root(
    transcript_path: str | None = None,
) -> Path:
    """Resolve the host-owned sessions root across desktop sandboxes.

    Codex Desktop may execute hooks as an isolated OS account while keeping
    the signed-in user's transcript under ``USERPROFILE``. The lifecycle
    event supplies the transcript path, so on Windows we accept that host
    profile only when the transcript is actually below its canonical Codex
    sessions directory. A custom ``CODEX_HOME`` remains rejected.
    """

    account_home = _account_home()
    host_profile = account_home
    if os.name == "nt":
        configured_profile = os.environ.get("USERPROFILE")
        if configured_profile:
            host_profile = Path(configured_profile).expanduser().resolve()
    codex_home = (host_profile / ".codex").resolve()
    configured_home = os.environ.get("CODEX_HOME")
    if (
        configured_home
        and Path(configured_home).expanduser().resolve() != codex_home
    ):
        raise OSError(
            "Custom CODEX_HOME lacks a host-authenticated sessions root"
        )
    sessions_root = (codex_home / "sessions").resolve()
    if transcript_path is not None:
        transcript = Path(transcript_path).expanduser().resolve(strict=True)
        try:
            transcript.relative_to(sessions_root)
        except ValueError as error:
            raise OSError(
                "Codex transcript is outside the host sessions root"
            ) from error
    return sessions_root


def _session_meta_from_transcript(
    transcript_path: str,
    *,
    session_id: str,
) -> dict[str, object] | None:
    sessions_root = _trusted_codex_sessions_root(transcript_path)
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


def _subagent_host_metadata(
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
    return {
        "parentContextId": parent_session_id,
        "agentType": transcript_agent_type,
        "taskName": task_name,
    }


def _subagent_claim_metadata(
    transcript_path: str,
    *,
    receiver_context_id: str,
) -> dict[str, str] | None:
    metadata = _subagent_host_metadata(
        transcript_path,
        receiver_context_id=receiver_context_id,
    )
    if metadata is None:
        return None
    task_name = metadata["taskName"]
    matched = DISPATCH_TASK_NAME.fullmatch(task_name)
    if matched is None:
        return None
    return {
        "parentContextId": metadata["parentContextId"],
        "agentType": metadata["agentType"],
        "dispatchReservationId": str(uuid.UUID(hex=matched.group(1))),
    }


def _child_transcript_from_parent(
    parent_transcript_path: str,
    *,
    parent_session_id: str,
    receiver_context_id: str,
) -> Path | None:
    if SESSION_IDENTIFIER.fullmatch(receiver_context_id) is None:
        return None
    parent_meta = _session_meta_from_transcript(
        parent_transcript_path,
        session_id=parent_session_id,
    )
    if parent_meta is None:
        return None

    sessions_root = _trusted_codex_sessions_root()
    parent_transcript = Path(parent_transcript_path).expanduser().resolve(
        strict=True
    )
    deadline = time.monotonic() + CHILD_TRANSCRIPT_WAIT_SECONDS
    pattern = f"*-{receiver_context_id}.jsonl"
    while True:
        sibling_candidates = tuple(parent_transcript.parent.glob(pattern))
        candidates = sibling_candidates
        if not candidates:
            candidates = tuple(sessions_root.rglob(pattern))
        matched: list[Path] = []
        for candidate in candidates:
            try:
                session_meta = _session_meta_from_transcript(
                    str(candidate),
                    session_id=receiver_context_id,
                )
            except (json.JSONDecodeError, OSError, ValueError):
                continue
            if session_meta is not None:
                matched.append(candidate.resolve(strict=True))
        unique_matches = tuple(dict.fromkeys(matched))
        if len(unique_matches) == 1:
            return unique_matches[0]
        if len(unique_matches) > 1 or time.monotonic() >= deadline:
            return None
        time.sleep(CHILD_TRANSCRIPT_POLL_SECONDS)


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


def _wait_for_direct_subagent_claim_metadata(
    transcript_path: str,
    *,
    receiver_context_id: str,
) -> dict[str, str] | None:
    deadline = time.monotonic() + CHILD_TRANSCRIPT_WAIT_SECONDS
    while True:
        try:
            metadata = _subagent_claim_metadata(
                transcript_path,
                receiver_context_id=receiver_context_id,
            )
        except (json.JSONDecodeError, OSError, ValueError):
            metadata = None
        if metadata is not None:
            return metadata
        if time.monotonic() >= deadline:
            return None
        time.sleep(CHILD_TRANSCRIPT_POLL_SECONDS)


def _dispatch_reservation_from_subagent_start(
    transcript_path: str,
    *,
    receiver_context_id: str,
    parent_session_id: str,
    agent_type: str,
) -> str | None:
    try:
        direct_reservation = _dispatch_reservation_from_transcript(
            transcript_path,
            receiver_context_id=receiver_context_id,
            parent_session_id=parent_session_id,
            agent_type=agent_type,
        )
    except (json.JSONDecodeError, OSError, ValueError):
        direct_reservation = None
    if direct_reservation is not None:
        return direct_reservation

    transcript_name = Path(transcript_path).name
    if transcript_name.endswith(f"-{receiver_context_id}.jsonl"):
        metadata = _wait_for_direct_subagent_claim_metadata(
            transcript_path,
            receiver_context_id=receiver_context_id,
        )
        if (
            metadata is not None
            and metadata["parentContextId"] == parent_session_id
            and metadata["agentType"] == agent_type
        ):
            return metadata["dispatchReservationId"]
        return None

    child_transcript = _child_transcript_from_parent(
        transcript_path,
        parent_session_id=parent_session_id,
        receiver_context_id=receiver_context_id,
    )
    if child_transcript is None:
        return None
    return _dispatch_reservation_from_transcript(
        str(child_transcript),
        receiver_context_id=receiver_context_id,
        parent_session_id=parent_session_id,
        agent_type=agent_type,
    )


def main() -> int:
    hook_input = json.load(sys.stdin)
    if (
        not isinstance(hook_input, dict)
        or hook_input.get("hook_event_name") != "SubagentStart"
    ):
        return 0
    hook_session_id = hook_input.get("session_id")
    receiver_context_id = hook_input.get("agent_id")
    agent_type = hook_input.get("agent_type")
    model_value = hook_input.get("model")
    model_id = (
        model_value
        if isinstance(model_value, str) and model_value
        else None
    )
    cwd = hook_input.get("cwd")
    transcript_path = hook_input.get("transcript_path")
    if not all(
        isinstance(value, str) and value
        for value in (
            hook_session_id,
            receiver_context_id,
            agent_type,
            cwd,
            transcript_path,
        )
    ):
        return 0

    try:
        direct_metadata = _subagent_claim_metadata(
            transcript_path,
            receiver_context_id=receiver_context_id,
        )
    except (json.JSONDecodeError, KeyError, OSError, ValueError):
        direct_metadata = None
    startup_failure_code = None
    if hook_session_id == receiver_context_id:
        if direct_metadata is None:
            direct_metadata = _wait_for_direct_subagent_claim_metadata(
                transcript_path,
                receiver_context_id=receiver_context_id,
            )
        if direct_metadata is None:
            return 0
        parent_session_id = direct_metadata["parentContextId"]
        dispatch_reservation_id = direct_metadata[
            "dispatchReservationId"
        ]
        if direct_metadata["agentType"] != agent_type:
            startup_failure_code = (
                "SCHEDULER_CODEX_SUBAGENT_CONTEXT_MISMATCH"
            )
    elif direct_metadata is not None:
        parent_session_id = direct_metadata["parentContextId"]
        dispatch_reservation_id = direct_metadata[
            "dispatchReservationId"
        ]
        if (
            direct_metadata["agentType"] != agent_type
            or hook_session_id != parent_session_id
        ):
            startup_failure_code = (
                "SCHEDULER_CODEX_SUBAGENT_CONTEXT_MISMATCH"
            )
    else:
        try:
            dispatch_reservation_id = (
                _dispatch_reservation_from_subagent_start(
                    transcript_path,
                    receiver_context_id=receiver_context_id,
                    parent_session_id=hook_session_id,
                    agent_type=agent_type,
                )
            )
        except (json.JSONDecodeError, KeyError, OSError, ValueError):
            return 0
        if dispatch_reservation_id is None:
            return 0
        parent_session_id = hook_session_id

    sys.path.insert(0, str(_runtime_path()))
    from hdg.errors import GatedLoopError, fail
    from hdg.graph_runtime import claim_codex_subagent_receiver
    from hdg.host_policy import ProjectRootBinding
    from hdg.repository import DATABASE_FILE, GOVERNANCE_DIRECTORY

    repository = None
    root_id = None
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
            or status.get("executionMode") not in {"active", "manual"}
        ):
            return 0
        if startup_failure_code is not None:
            fail(
                startup_failure_code,
                "Codex SubagentStart does not match the reserved receiver",
            )
        assignment = claim_codex_subagent_receiver(
            root=resolution.project_root,
            root_id=root_id,
            workspace_root=resolution.workspace_root,
            receiver_context_id=receiver_context_id,
            parent_context_id=parent_session_id,
            actual_model_id=model_id,
            dispatch_reservation_id=dispatch_reservation_id,
        )
    except Exception as error:
        code = getattr(error, "code", "HOST_CONTEXT_INVALID")
        if repository is not None and isinstance(root_id, str):
            try:
                repository.expire_dispatch_reservation_now(
                    dispatch_reservation_id,
                    root_id=root_id,
                    host_adapter_id="codex",
                    failure_code=code,
                )
            except (GatedLoopError, OSError, ValueError):
                pass
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SubagentStart",
                        "additionalContext": (
                            "Delivery Graph could not attest this AUTO "
                            f"receiver ({code}). Do not inspect or modify "
                            "the repository and do not call Loop tools. "
                            "Report this startup failure to the coordinator.\n"
                            "DELIVERY_GRAPH_STARTUP_ERROR="
                            + code
                        ),
                    }
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0

    context = {
        "agent_id": assignment["agentId"],
        "receiver_context_id": assignment["receiverContextId"],
        "root_id": assignment["rootId"],
        "node_id": assignment["nodeId"],
        "lease_expires_at": assignment["leaseExpiresAt"],
        "dispatch_reservation_id": assignment[
            "dispatchReservationId"
        ],
        "dispatch_decision_fingerprint": assignment[
            "dispatchDecisionFingerprint"
        ],
        "session_context_id": assignment["receiverContextId"],
        "session_attestation": assignment["hostSessionAttestation"],
    }
    additional_context = (
        "The host already claimed one exact Delivery Graph AUTO Loop for "
        "this Codex-native context before exposing this message. Do not call "
        "dispatch_loop. Load the assigned node with loop_context, immediately "
        "call heartbeat_loop once before any other tool, then execute it. "
        "Include session_context_id as _host_session_context_id and "
        "session_attestation as _host_session_attestation in heartbeat_loop, "
        "report_loop_progress, pause_loop, and record_loop_result. Omit "
        "operation_id because the Controller resolves it from this "
        "Hook-issued session capability. Never copy the capability to "
        "workers, logs, messages, or user-visible output.\n"
        "DELIVERY_GRAPH_ASSIGNMENT="
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
