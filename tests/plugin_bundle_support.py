from __future__ import annotations

from contextlib import redirect_stderr

import io

import json

import os

from pathlib import Path

import re

import runpy

import subprocess

import sys

from tempfile import TemporaryDirectory

import unittest

from unittest.mock import patch

import hdg

from hdg.mcp_tools import tool_definitions

from hdg.mcp_catalog import (
    PLANNING_TOOL_PROFILE,
    tool_names_for_profile,
)

from hdg.mcp_adapter import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    McpConnection,
    MODERN_PROTOCOL_VERSION,
    PROTOCOL_VERSION_META_KEY,
    handle_message,
)

from hdg.mcp_apps import DASHBOARD_RESOURCE_URI, MCP_APP_MIME_TYPE

from hdg.host_policy import ProjectRootBinding

from hdg.model_core import validate_hierarchy_definition

from hdg.planning import preview_hierarchy

from .test_loop_architecture import group_hierarchy

ROOT = Path(__file__).resolve().parents[1]

SOURCE = ROOT / "src" / "hdg"

SKILL = ROOT / "skills" / "delivery-graph"

SKILL_RUNTIME = SKILL / "scripts" / "hdg"

PLUGIN = ROOT / "plugins" / "delivery-graph"

PLUGIN_SKILL = PLUGIN / "skills" / "delivery-graph"

DISPATCH_SKILL = ROOT / "skills" / "delivery-graph-dispatch"

TASK_SKILL = ROOT / "skills" / "delivery-graph-task"

REVIEW_SKILL = ROOT / "skills" / "delivery-graph-review"

def _allowed_tools(path: Path) -> list[str]:
    document = path.read_text(encoding="utf-8")
    frontmatter = document.split("---", 2)[1]
    return [
        line.removeprefix("  - ")
        for line in frontmatter.splitlines()
        if line.startswith("  - ")
    ]
