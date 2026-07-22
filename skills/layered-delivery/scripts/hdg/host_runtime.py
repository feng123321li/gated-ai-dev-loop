from __future__ import annotations

import re

from .errors import GatedLoopError


AGENT_RUNTIME_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")


def is_agent_runtime(value: object) -> bool:
    return isinstance(value, str) and bool(AGENT_RUNTIME_PATTERN.fullmatch(value))


def is_claude_runtime(value: object) -> bool:
    return bool(
        is_agent_runtime(value)
        and (value == "claude" or value.startswith("claude-") or value.startswith("claude."))
    )


def require_host_runtime(value: object) -> str:
    if not is_agent_runtime(value):
        raise GatedLoopError(
            "HOST_RUNTIME_REQUIRED" if value is None else "HOST_RUNTIME_INVALID",
            "A writing workflow requires a --host-runtime Agent identifier"
            if value is None
            else "hostRuntime must be a safe lowercase Agent identifier",
        )
    return value
