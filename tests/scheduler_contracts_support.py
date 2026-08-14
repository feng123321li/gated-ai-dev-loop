from __future__ import annotations

from contextlib import redirect_stderr

from copy import deepcopy

import io

import inspect

import json

from pathlib import Path

import re

import sqlite3

import subprocess

from tempfile import TemporaryDirectory

from types import SimpleNamespace

import unittest

from unittest.mock import Mock, patch

from hdg import mcp_server

from hdg.controller import (
    ControllerContext,
    LayeredDeliveryController,
)

from hdg.errors import GatedLoopError

from hdg.graph_model import loop_node_id

from hdg.git_binding import (
    capture_verified_workspace_changes,
    inspect_frozen_git_workspace_provenance,
)

from hdg.hierarchy_contract import hierarchy_contract

from hdg.host_policy import ProjectRootBinding

from hdg.jsonio import fingerprint

from hdg.mcp_adapter import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    LEGACY_PROTOCOL_VERSIONS,
    MODERN_PROTOCOL_VERSION,
    McpConnection,
    PROTOCOL_VERSION_META_KEY,
    SUPPORTED_PROTOCOL_VERSIONS,
    _tool_result,
    handle_message,
)

from hdg.mcp_tools import (
    call_tool,
    tool_definitions,
    validate_tool_arguments,
)

from hdg.model_core import validate_hierarchy_definition

from hdg.planning import workspace_status

from hdg.planning import freeze_hierarchy, prepare_hierarchy

from hdg.repository import (
    SCHEDULER_STATE_CONTRACT,
    SchedulerRepository,
)

from .test_loop_architecture import (
    group_hierarchy,
    loop_descriptor,
    task_hierarchy,
)

from .test_scheduler_runtime import at, database_hierarchy

from .automatic_dispatch import reserve_loop

def modern_meta(
    *,
    version: str = MODERN_PROTOCOL_VERSION,
    client_name: str = "test-modern-client",
    client_version: str = "1.0.0",
    **extra: object,
) -> dict[str, object]:
    return {
        PROTOCOL_VERSION_META_KEY: version,
        CLIENT_CAPABILITIES_META_KEY: {},
        CLIENT_INFO_META_KEY: {
            "name": client_name,
            "version": client_version,
        },
        **extra,
    }

def legacy_delivery_hierarchy_017() -> dict:
    tasks = [
        {
            "definition": {
                "schemaVersion": 3,
                "id": "t-api",
                "kind": "TASK",
                "parentId": "c-service",
                "title": "Run API task",
                "summary": "Run the API Task Loop.",
                "execution": {
                    "dependsOn": [],
                    "loop": loop_descriptor(),
                },
            },
            "children": [],
        }
    ]
    capability = {
        "definition": {
            "schemaVersion": 3,
            "id": "c-service",
            "kind": "CAPABILITY",
            "parentId": "d-service",
            "title": "Coordinate service capability",
            "summary": "Join service Task Loops.",
            "decomposition": {"dependsOn": []},
            "children": [
                {
                    "id": "t-api",
                    "kind": "TASK",
                    "title": "Run API task",
                }
            ],
        },
        "children": tasks,
    }

def isolated_task_hierarchy(
    delivery_id: str,
    task_id: str,
    *,
    claims: list[str] | None = None,
) -> dict:
    hierarchy = task_hierarchy()
    hierarchy["delivery"]["id"] = delivery_id
    hierarchy["delivery"]["title"] = f"Deliver {delivery_id}"
    definition = hierarchy["root"]["definition"]
    definition["id"] = task_id
    definition["title"] = f"Run {task_id}"
    definition["execution"]["loop"]["resourceClaims"] = claims or []
    return hierarchy
    return {
        "schemaVersion": 3,
        "skillHints": [],
        "reviewLoop": loop_descriptor(
            "root/independent-review-loop@1"
        ),
        "root": {
            "definition": {
                "schemaVersion": 3,
                "id": "d-service",
                "kind": "DELIVERY",
                "title": "Deliver service",
                "summary": "Coordinate the service delivery.",
                "decomposition": {},
                "children": [
                    {
                        "id": "c-service",
                        "kind": "CAPABILITY",
                        "title": "Coordinate service capability",
                    }
                ],
            },
            "children": [capability],
        },
    }

def git_command(worktree: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(worktree), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()

def git_delivery_checkout(
    root: str,
    *,
    delivery_id: str = "d-git",
    mainline: str = "main",
) -> tuple[Path, Path, str, str]:
    repository = Path(root, "repository")
    repository.mkdir()
    git_command(
        repository,
        "init",
        f"--initial-branch={mainline}",
    )
    git_command(repository, "config", "user.name", "Scheduler Tests")
    git_command(
        repository,
        "config",
        "user.email",
        "scheduler-tests@example.invalid",
    )
    Path(repository, "README.md").write_text(
        "# Git delivery fixture\n",
        encoding="utf-8",
    )
    git_command(repository, "add", "README.md")
    git_command(repository, "commit", "-m", "Initial main baseline")
    base_commit = git_command(repository, "rev-parse", "HEAD")
    branch_ref = f"feature/{delivery_id}"
    git_command(repository, "switch", "-c", branch_ref, mainline)
    return repository, repository, base_commit, branch_ref

def bind_delivery_to_git(
    hierarchy: dict,
    *,
    branch_ref: str,
    base_commit: str,
    base_ref: str = "main",
) -> dict:
    hierarchy["delivery"]["gitBinding"] = {
        "branchRef": branch_ref,
        "baseRef": base_ref,
        "baseCommit": base_commit,
        "integrationTarget": base_ref,
    }
    return hierarchy
