#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from typing import Any


ISO_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b"
)
RESET_KEYS = frozenset({"reset_at", "resetAt", "resetsAt"})
LABELED_RESET = re.compile(
    r"(?:reset_at|resetAt|resetsAt)\s*[:=]\s*[\"']?"
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))"
)


def _runtime_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "delivery-graph"
        / "scripts"
    )


def _normalized_reset_value(value: object) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(
                seconds,
                tz=timezone.utc,
            ).isoformat().replace("+00:00", "Z")
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        match = ISO_TIMESTAMP.search(value)
        return match.group(0).replace(" ", "T") if match else None
    return None


def _structured_reset(value: object) -> str | None:
    if isinstance(value, dict):
        for key in RESET_KEYS:
            if key in value:
                normalized = _normalized_reset_value(value[key])
                if normalized is not None:
                    return normalized
        for nested in value.values():
            normalized = _structured_reset(nested)
            if normalized is not None:
                return normalized
    elif isinstance(value, list):
        for nested in value:
            normalized = _structured_reset(nested)
            if normalized is not None:
                return normalized
    return None


def _reset_at(hook_input: dict[str, Any]) -> str | None:
    error_details = hook_input.get("error_details")
    structured = _structured_reset(error_details)
    if structured is not None:
        return structured
    if isinstance(error_details, str):
        match = LABELED_RESET.search(error_details)
        if match is not None:
            return match.group(1).replace(" ", "T")
    return None


def main() -> int:
    hook_input = json.load(sys.stdin)
    if (
        not isinstance(hook_input, dict)
        or hook_input.get("hook_event_name") != "StopFailure"
        or hook_input.get("error") != "rate_limit"
    ):
        return 0
    reset_at = _reset_at(hook_input)
    cwd = hook_input.get("cwd")
    session_id = hook_input.get("session_id")
    agent_id = hook_input.get("agent_id")
    receiver_context_id = (
        agent_id if isinstance(agent_id, str) else session_id
    )
    if not isinstance(cwd, str) or not isinstance(
        receiver_context_id,
        str,
    ):
        return 0

    sys.path.insert(0, str(_runtime_path()))
    from hdg.errors import GatedLoopError
    from hdg.graph_runtime import pause_loop, report_host_capacity_exhausted
    from hdg.host_policy import ProjectRootBinding
    from hdg.repository import SchedulerRepository

    try:
        binding = ProjectRootBinding.from_startup(cwd)
        resolution = binding.resolve_request(None, stateless=False)
        repository = SchedulerRepository(resolution.project_root)
        status = repository.workspace_status(
            workspace_root=resolution.workspace_root,
        )
        root_id = status.get("rootId")
        if not isinstance(root_id, str):
            return 0
        run = repository.run(root_id)
        claimed = [
            node
            for node in run["nodes"]
            if node["status"] == "CLAIMED"
        ]
        matching = [
            node
            for node in claimed
            if node.get("receiverContextId") == receiver_context_id
            and node.get("agentId") == "claude-code"
        ]
        if len(matching) != 1:
            return 0
        if reset_at is None:
            operation_id = matching[0].get("operationId")
            if isinstance(operation_id, str):
                pause_loop(
                    root=resolution.project_root,
                    root_id=root_id,
                    node_id=matching[0]["nodeId"],
                    operation_id=operation_id,
                )
            print(
                "Delivery Graph paused the failed receiver without guessing "
                "a reset time; manual host recovery is required.",
                file=sys.stderr,
            )
            return 0
        report_material = json.dumps(
            {
                "receiverContextId": receiver_context_id,
                "resetAt": reset_at,
                "errorDetails": hook_input.get("error_details"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        report_id = hashlib.sha256(report_material).hexdigest()
        report_host_capacity_exhausted(
            root=resolution.project_root,
            root_id=root_id,
            node_id=matching[0]["nodeId"],
            reset_at=reset_at,
            host_adapter_id="claude-code",
            receiver_context_id=receiver_context_id,
            report_id=report_id,
            reason="Claude Code StopFailure reported rate_limit",
        )
    except (GatedLoopError, OSError, ValueError, KeyError, TypeError) as error:
        print(
            f"Delivery Graph rate-limit hook failed safely: {error}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
