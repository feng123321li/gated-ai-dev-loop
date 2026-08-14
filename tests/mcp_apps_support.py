from __future__ import annotations

from contextlib import redirect_stderr

import io

import json

from pathlib import Path

from tempfile import TemporaryDirectory

import unittest

from unittest.mock import patch

from hdg.errors import GatedLoopError

from hdg.host_policy import CODEX_SANDBOX_META_KEY, ProjectRootBinding

from hdg.mcp_adapter import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    McpConnection,
    PROTOCOL_VERSION_META_KEY,
    handle_message,
)

from hdg.mcp_tools import tool_definitions

DASHBOARD_RESOURCE_URI = "ui://delivery-graph/dashboard-v2.html"

DASHBOARD_MIME_TYPE = "text/html;profile=mcp-app"

EXISTING_TOOL_NAMES = (
    "workspace_status",
    "recommend_assurance_profile",
    "hierarchy_contract",
    "preview_hierarchy",
    "confirm_development_baseline",
    "select_execution_mode",
    "resume_execution_mode",
    "create_manual_handoff",
    "start_manual_handoff",
    "prepare_hierarchy",
    "prepare_delivery_revision",
    "delivery_revision_history",
    "plan_dispatch_batch",
    "freeze_hierarchy",
    "graph_frontier",
    "unfreeze_task_requirement",
    "refreeze_task_requirement",
    "graph_status",
    "graph_events",
    "advance_graph",
    "rebuild_graph_run",
    "loop_context",
    "dispatch_loop",
    "handoff_ready_automatic_task",
    "heartbeat_loop",
    "report_loop_progress",
    "pause_loop",
    "resume_loop",
    "record_loop_result",
    "record_user_confirmation",
    "cancel_graph_run",
    "archive_delivery",
)

def modern_meta() -> dict[str, object]:
    return {
        PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION,
        CLIENT_CAPABILITIES_META_KEY: {},
        CLIENT_INFO_META_KEY: {
            "name": "mcp-apps-contract-test",
            "version": "1.0.0",
        },
    }
