from __future__ import annotations

import re
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .fs_safe import (
    read_regular_file,
    safe_path,
)
from .model_core import (
    resolve_self_hosting_policy,
)
from .repository_contracts import (
    DATABASE_TABLES,
    GOVERNANCE_DIRECTORY,
    LEGACY_REGISTRY_FILE,
    WORK_ITEM_DATABASE_FILE,
    WORK_ITEMS_DIRECTORY,
    timestamp,
)


def assert_self_hosting_dogfood(self, explicit_dogfood: bool) -> None:
    project_name = None
    source_checkout_detected = False
    for candidate in (self.root, *self.root.parents):
        source_checkout_detected = source_checkout_detected or all(
            path.is_file()
            for path in (
                candidate / "scripts" / "build_skill.py",
                candidate / "src" / "hdg" / "repository.py",
                candidate / "skills" / "layered-delivery" / "SKILL.md",
            )
        )
        pyproject = candidate / "pyproject.toml"
        try:
            text = read_regular_file(candidate, pyproject).decode("utf-8")
        except FileNotFoundError:
            text = None
        except UnicodeDecodeError:
            if source_checkout_detected:
                project_name = "layered-delivery"
                break
            text = None
        if text is not None:
            project_match = re.search(
                r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)",
                text,
            )
            name_match = re.search(
                r"(?m)^name\s*=\s*([\"'])([^\"']+)\1"
                r"\s*(?:#.*)?$",
                project_match.group(1) if project_match else "",
            )
            if name_match and name_match.group(2) == "layered-delivery":
                project_name = "layered-delivery"
                break
        if source_checkout_detected:
            project_name = "layered-delivery"
            break
    policy = resolve_self_hosting_policy(project_name=project_name, explicit_dogfood=explicit_dogfood)
    if not policy["createsRuntimePackage"]:
        fail(
            "SELF_HOSTING_DOGFOOD_REQUIRED",
            "The hierarchical governance implementation repository requires explicit dogfood for runtime mutations",
        )

def ensure_runtime_root(self) -> None:
    try:
        self.root.lstat()
    except FileNotFoundError:
        fail("WORK_ITEM_ROOT_INVALID", "Coordination root must already exist")
    if not self.root.is_dir() or self.root.is_symlink():
        fail("WORK_ITEM_ROOT_INVALID", "Coordination root must be a regular directory")
    runtime = safe_path(self.root, GOVERNANCE_DIRECTORY)
    runtime.mkdir(parents=True, exist_ok=True)
    safe_path(self.root, GOVERNANCE_DIRECTORY)
    legacy_registry = runtime / LEGACY_REGISTRY_FILE
    if legacy_registry.exists() and not self.database_path.exists():
        fail(
            "WORK_ITEM_STORAGE_UNSUPPORTED",
            "Legacy JSON governance storage is unsupported; create a new SQLite-governed requirement",
        )
    items = safe_path(self.root, f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}")
    items.mkdir(parents=True, exist_ok=True)
    safe_path(self.root, f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}")







def inspect_workspace_state(self) -> dict[str, Any]:
    """Classify absent, staging-only, and active governance state."""

    database_path = safe_path(
        self.root,
        f"{GOVERNANCE_DIRECTORY}/{WORK_ITEM_DATABASE_FILE}",
    )
    if self.governance_root.exists() and not self.governance_root.is_dir():
        fail(
            "WORK_ITEM_DATABASE_PATH_INVALID",
            "Governance runtime path must be a directory",
        )
    if not database_path.exists():
        return {
            "state": "ABSENT",
            "databaseExists": False,
            "activePayloadUploads": 0,
            "stagedPayloadBytes": 0,
        }
    with self._read_connection() as connection:
        workspace_rows = connection.execute(
            "SELECT schema_version, coordination_root FROM workspace"
        ).fetchall()
        payload_summary = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(received_bytes), 0) "
            "FROM payload_uploads WHERE expires_at > ?",
            (timestamp(self.now),),
        ).fetchone()
        if not workspace_rows:
            domain_tables = sorted(
                DATABASE_TABLES - {"workspace", "payload_uploads", "payload_chunks"}
            )
            populated = [
                table
                for table in domain_tables
                if connection.execute(
                    f"SELECT 1 FROM {table} LIMIT 1"
                ).fetchone()
                is not None
            ]
            if populated:
                fail(
                    "WORK_ITEM_REGISTRY_MISSING",
                    "Governance database has domain rows without workspace state",
                    populatedTables=populated,
                )
            state = "STAGING_ONLY"
        elif (
            len(workspace_rows) == 1
            and workspace_rows[0]["schema_version"] == SCHEMA_VERSION
            and workspace_rows[0]["coordination_root"] == str(self.root)
        ):
            state = "ACTIVE"
        else:
            fail(
                "WORK_ITEM_REGISTRY_INVALID",
                "Governance workspace identity is invalid",
            )
        return {
            "state": state,
            "databaseExists": True,
            "activePayloadUploads": payload_summary[0],
            "stagedPayloadBytes": payload_summary[1],
        }
