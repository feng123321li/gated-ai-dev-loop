#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys


def main() -> int:
    hook_input = json.load(sys.stdin)
    if (
        not isinstance(hook_input, dict)
        or hook_input.get("hook_event_name") != "SessionStart"
    ):
        return 0
    session_id = hook_input.get("session_id")
    transcript_path = hook_input.get("transcript_path")
    cwd = hook_input.get("cwd")
    if not all(
        isinstance(value, str) and value
        for value in (session_id, transcript_path, cwd)
    ):
        return 0

    hook_directory = Path(__file__).resolve().parent
    sys.path.insert(0, str(hook_directory))
    from attest_codex_subagent_receiver import (
        _runtime_path,
        _session_meta_from_transcript,
        _workspace_start,
    )

    try:
        session_meta = _session_meta_from_transcript(
            transcript_path,
            session_id=session_id,
        )
        if session_meta is None:
            return 0
        session_source = session_meta.get("source")
        if (
            isinstance(session_source, dict)
            and isinstance(session_source.get("subagent"), dict)
        ):
            return 0
    except (json.JSONDecodeError, KeyError, OSError, ValueError):
        return 0

    sys.path.insert(0, str(_runtime_path()))
    from hdg.errors import GatedLoopError
    from hdg.host_policy import ProjectRootBinding
    from hdg.repository import (
        DATABASE_FILE,
        GOVERNANCE_DIRECTORY,
        SchedulerRepository,
    )

    try:
        resolution = ProjectRootBinding.from_startup(
            _workspace_start(cwd)
        ).resolve_request(None, stateless=False)
        database_path = (
            Path(resolution.project_root)
            / GOVERNANCE_DIRECTORY
            / DATABASE_FILE
        )
        if not database_path.is_file():
            return 0
        repository = SchedulerRepository(resolution.project_root)
        status = repository.workspace_status(
            workspace_root=resolution.workspace_root,
        )
        root_id = status.get("rootId")
        pending_selection = (
            repository.execution_selection(root_id)
            if isinstance(root_id, str)
            else None
        )
        active_automatic = (
            status.get("status") in {"ACTIVE", "PAUSED", "BLOCKED"}
            and status.get("executionMode") == "active"
        )
        pending_automatic = (
            status.get("status") in {"CHOICE_READY", "PREPARED"}
            and isinstance(pending_selection, dict)
            and pending_selection.get("selection") == "AUTOMATIC"
        )
        if (
            not isinstance(root_id, str)
            or not (active_automatic or pending_automatic)
        ):
            return 0
        repository.assert_delivery_workspace(
            root_id,
            resolution.workspace_root,
            allow_unbound_choice=(
                pending_automatic and status.get("status") == "CHOICE_READY"
            ),
        )
        capability = repository.issue_host_workspace_attestation(
            host_adapter_id="codex",
            context_id=session_id,
            tool_name="delivery_session",
            tool_use_id="session:" + session_id,
            workspace_root=resolution.workspace_root,
            lifetime_seconds=86_400,
        )
    except (GatedLoopError, OSError, ValueError):
        return 0

    context = {
        "root_id": root_id,
        "session_context_id": session_id,
        "session_attestation": capability,
    }
    additional_context = (
        "The trusted Delivery Graph SessionStart Hook attested this Codex "
        "Delivery session. For a READY AUTOMATIC TASK_LOOP, call "
        "claim_current_task and implement the TASK in this session. Review "
        "Loops still require an independent native receiver. Include the "
        "two private session fields below in claim_current_task, "
        "plan_dispatch_batch, heartbeat_loop, report_loop_progress, "
        "pause_loop, and record_loop_result. Never copy them to workers, "
        "messages, logs, or user-visible output. Omit operation_id; the "
        "Controller resolves it from the attested session.\n"
        "DELIVERY_GRAPH_SESSION_AUTH="
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
                    "hookEventName": "SessionStart",
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
