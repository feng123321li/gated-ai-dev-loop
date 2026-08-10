#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from typing import Any


MCP_PERMISSION_PREFIX = "mcp__plugin_delivery-graph_delivery-graph__"
SENSITIVE_TOOL_REASONS = {
    f"{MCP_PERMISSION_PREFIX}rebuild_graph_run": (
        "从事件重建 Graph 查询快照前需要用户明确确认。"
    ),
    f"{MCP_PERMISSION_PREFIX}cancel_graph_run": (
        "取消当前 Graph 运行前需要用户明确确认。"
    ),
    f"{MCP_PERMISSION_PREFIX}archive_delivery": (
        "归档已完成的 Delivery 前需要用户明确确认。"
    ),
    f"{MCP_PERMISSION_PREFIX}unfreeze_task_requirement": (
        "解冻尚未开始的 TASK 需求前需要用户明确确认。"
    ),
    f"{MCP_PERMISSION_PREFIX}refreeze_task_requirement": (
        "重新冻结修改后的 TASK 需求前需要用户明确确认。"
    ),
    f"{MCP_PERMISSION_PREFIX}handoff_ready_automatic_task": (
        "把未领取的自动 TASK 改为人工接收前，需要用户明确确认无代码改动。"
    ),
}


def _block(reason: str) -> int:
    print(reason, file=sys.stderr)
    return 2


def _load_input() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict):
        raise ValueError("hook input must be a JSON object")
    return value


def main() -> int:
    try:
        hook_input = _load_input()
        if hook_input.get("hook_event_name") != "PreToolUse":
            return _block(
                "Delivery Graph permission hook received an unexpected event; "
                "the tool call was blocked."
            )

        tool_name = hook_input.get("tool_name")
        if not isinstance(tool_name, str):
            return _block(
                "Delivery Graph permission hook received an invalid tool name; "
                "the tool call was blocked."
            )
        reason = SENSITIVE_TOOL_REASONS.get(tool_name)
        if reason is None:
            return _block(
                "Delivery Graph permission hook received an unexpected tool name; "
                "the tool call was blocked."
            )

        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                }
            },
            sys.stdout,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except Exception as error:
        return _block(
            "Delivery Graph permission hook failed closed; "
            f"the tool call was blocked: {type(error).__name__}: {error}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
