from __future__ import annotations

import json

import os

import re

import sqlite3

from contextlib import contextmanager

from datetime import datetime, timezone

from pathlib import Path

from typing import Any, Iterator

from .errors import GatedLoopError, fail

from .fs_safe import (
    exclusive_file_lock,
)

from .graph_model import (
    compile_delivery_graph,
    graph_fingerprint,
    validate_delivery_graph,
)

from .jsonio import fingerprint

from .model_core import validate_hierarchy_definition

from .repository_dispatch import DeliveryDispatchStore

from .repository_events import DeliveryEventStore

from .repository_execution_setup import (
    DeliveryExecutionSetupStore,
)

from .repository_hierarchies import DeliveryHierarchyStore

from .repository_projections import (
    MANUAL_WRITABLE_PROJECTIONS,
    DeliveryProjectionStore,
)

from .repository_workspaces import DeliveryWorkspaceStore

from .storage_schema import (
    SCHEDULER_STATE_CONTRACT,
    ensure_compatible_scheduler_storage,
    initialize_scheduler_storage,
    verify_scheduler_state_contract,
)

from .workspace_identity import (
    workspace_identity,
)

GOVERNANCE_DIRECTORY = ".layered-delivery"

DATABASE_FILE = "scheduler.db"

DELIVERY_REQUIREMENT_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{1,31}-[0-9]{1,12})"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)

def _delivery_requirement_key(hierarchy: dict[str, Any]) -> str | None:
    delivery = hierarchy["delivery"]
    explicit = delivery.get("requirementKey")
    if isinstance(explicit, str) and explicit:
        return explicit.upper()
    for field in ("id", "title"):
        value = delivery.get(field)
        if not isinstance(value, str):
            continue
        match = DELIVERY_REQUIREMENT_REFERENCE.search(value)
        if match is not None:
            return match.group(1).upper()
    return None

def _validated_stored_graph(
    graph_json: object,
    graph_fingerprint: object,
    *,
    root_id: str,
    allow_pending_runtime_drift: bool = False,
) -> dict[str, Any]:
    if not isinstance(graph_json, str) or not isinstance(
        graph_fingerprint,
        str,
    ):
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler graph metadata is invalid",
            rootId=root_id,
        )
    try:
        graph = json.loads(graph_json)
    except (json.JSONDecodeError, RecursionError):
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler graph JSON is invalid",
            rootId=root_id,
        )
    if fingerprint(graph) != graph_fingerprint:
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler graph changed",
            rootId=root_id,
        )
    if allow_pending_runtime_drift:
        if not isinstance(graph, dict):
            fail(
                "SCHEDULER_STATE_INVALID",
                "Stored pending scheduler graph is not an object",
                rootId=root_id,
            )
        return graph
    try:
        return validate_delivery_graph(graph)
    except GatedLoopError as error:
        error.details.setdefault("rootId", root_id)
        raise

def _validated_stored_definition(
    row: sqlite3.Row,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root_id = row["root_id"]
    try:
        hierarchy = json.loads(row["hierarchy_json"])
    except (json.JSONDecodeError, RecursionError):
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler hierarchy JSON is invalid",
            rootId=root_id,
        )
    if fingerprint(hierarchy) != row["hierarchy_fingerprint"]:
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler hierarchy changed",
            rootId=root_id,
        )
    if not isinstance(hierarchy, dict) or "delivery" not in hierarchy:
        fail(
            "SCHEDULER_STATE_INCOMPATIBLE",
            "Stored scheduler state predates the recursive GROUP/TASK "
            "Delivery contract; archive it before creating a new Graph",
            rootId=root_id,
        )
    try:
        # Resource caps protect newly submitted payloads. State written under
        # this same scheduler contract predates those caps and must remain
        # readable; fingerprints and the canonical equality check below still
        # enforce its integrity and shape.
        normalized = validate_hierarchy_definition(
            hierarchy,
            enforce_resource_limits=False,
        )
    except GatedLoopError as error:
        error.details.setdefault("rootId", root_id)
        raise
    if normalized != hierarchy:
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler hierarchy is not canonical",
            rootId=root_id,
        )
    allow_pending_runtime_drift = row["status"] == "HANDOFF_READY"
    graph = _validated_stored_graph(
        row["graph_json"],
        row["graph_fingerprint"],
        root_id=root_id,
        allow_pending_runtime_drift=allow_pending_runtime_drift,
    )
    try:
        expected_graph = compile_delivery_graph(
            normalized,
            hierarchy_fingerprint=row["hierarchy_fingerprint"],
        )
    except GatedLoopError as error:
        error.details.setdefault("rootId", root_id)
        raise
    graph_identity_mismatch = (
        row["root_id"] != normalized["delivery"]["id"]
        or graph.get("rootId") != normalized["delivery"]["id"]
        or graph.get("hierarchyFingerprint")
        != row["hierarchy_fingerprint"]
    )
    graph_is_current = (
        graph == expected_graph
        and graph_fingerprint(expected_graph) == row["graph_fingerprint"]
    )
    pending_runtime_only_drift = (
        allow_pending_runtime_drift
        and isinstance(graph.get("runtime"), dict)
        and all(
            graph.get(field) == expected_graph[field]
            for field in (
                "schemaVersion",
                "rootId",
                "hierarchyFingerprint",
                "nodes",
                "edges",
            )
        )
    )
    if graph_identity_mismatch or not (
        graph_is_current or pending_runtime_only_drift
    ):
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler graph is not bound to its hierarchy",
            rootId=root_id,
        )
    return normalized, graph

def timestamp(now: object = None) -> str:
    value = now() if callable(now) else now
    if value is None:
        value = datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        fail("TIME_INVALID", "now must resolve to a datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )

def _commit_timestamp(
    now: object,
    current: str | None = None,
) -> str:
    """Resolve a transaction timestamp that never precedes stored state."""

    candidate = timestamp(now)
    if current is None:
        return candidate
    candidate_value = datetime.fromisoformat(
        candidate.replace("Z", "+00:00")
    )
    current_value = datetime.fromisoformat(
        current.replace("Z", "+00:00")
    )
    return current if candidate_value < current_value else candidate


class SchedulerRepositoryBase:
    """Own scheduler storage, locking, and state validation."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        now: object = None,
    ) -> None:
        self.root = Path(root).absolute()
        self.now = now
        self.control_root = self.root / GOVERNANCE_DIRECTORY
        self.database_path = self.control_root / DATABASE_FILE
        self.lock_path = self.control_root / ".scheduler.lock"
        self.legacy_database_path = (
            self.control_root / "governance.sqlite3"
        )

    def _assert_no_legacy_state(self) -> None:
        if self.legacy_database_path.exists():
            fail(
                "SCHEDULER_LEGACY_STATE_UNSUPPORTED",
                "Legacy governance.sqlite3 state is not compatible with "
                "the Task Loop scheduler schema; archive it before creating "
                "a new graph",
            )

    def assert_self_hosting_dogfood(
        self,
        explicit_dogfood: bool,
    ) -> None:
        project_file = self.root / "pyproject.toml"
        if not project_file.is_file():
            return
        text = project_file.read_text(encoding="utf-8")
        match = re.search(
            r"(?ms)^\[project\].*?^name\s*=\s*[\"']([^\"']+)",
            text,
        )
        if (
            match
            and match.group(1) == "delivery-graph"
            and not explicit_dogfood
        ):
            fail(
                "SELF_HOSTING_DOGFOOD_REQUIRED",
                "Maintaining delivery-graph does not create a runtime "
                "package unless --dogfood is explicitly authorized",
            )

    def _connect(self) -> sqlite3.Connection:
        self._assert_no_legacy_state()
        if self.control_root.exists() and self.control_root.is_symlink():
            fail(
                "SCHEDULER_PATH_INVALID",
                "Scheduler control root must not be a symbolic link",
            )
        self.control_root.mkdir(parents=True, exist_ok=True)
        database_exists = self.database_path.exists()
        if self.database_path.is_symlink():
            fail(
                "SCHEDULER_PATH_INVALID",
                "Scheduler database must not be a symbolic link",
            )
        if database_exists:
            database_stat = self.database_path.lstat()
            if (
                not self.database_path.is_file()
                or database_stat.st_nlink != 1
            ):
                fail(
                    "SCHEDULER_PATH_INVALID",
                    "Scheduler database must be one regular unlinked file",
                )
        if self.lock_path.exists() and self.lock_path.is_symlink():
            fail(
                "SCHEDULER_PATH_INVALID",
                "Scheduler lock must not be a symbolic link",
            )
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if database_exists:
                verify_scheduler_state_contract(connection)
            connection.execute("PRAGMA journal_mode = WAL")
            if database_exists:
                ensure_compatible_scheduler_storage(connection)
            else:
                initialize_scheduler_storage(connection)
        except Exception:
            connection.close()
            raise
        return connection

    @contextmanager
    def scheduler_lock(self) -> Iterator[None]:
        """Hold the controller lock across a multi-read/write operation."""

        with exclusive_file_lock(self.lock_path):
            yield

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with exclusive_file_lock(self.lock_path):
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        if not self.database_path.is_file():
            fail(
                "SCHEDULER_STATE_ABSENT",
                "No Delivery Graph scheduler state exists",
            )
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _assert_delivery_requirement_available(
        connection: sqlite3.Connection,
        hierarchy: dict[str, Any],
    ) -> None:
        requirement_key = _delivery_requirement_key(hierarchy)
        requested_root_id = hierarchy["delivery"]["id"]
        existing_identity = connection.execute(
            "SELECT * FROM hierarchies WHERE root_id = ?",
            (requested_root_id,),
        ).fetchone()
        if existing_identity is not None:
            stored_hierarchy, _ = _validated_stored_definition(
                existing_identity
            )
            existing_requirement_key = _delivery_requirement_key(
                stored_hierarchy
            )
            if (
                existing_requirement_key is not None
                and existing_requirement_key != requirement_key
            ):
                fail(
                    "SCHEDULER_DELIVERY_REQUIREMENT_KEY_IMMUTABLE",
                    "A Delivery revision must retain its external "
                    "requirement key",
                    rootId=requested_root_id,
                    existingRequirementKey=existing_requirement_key,
                    requestedRequirementKey=requirement_key,
                )
        if requirement_key is None:
            return
        rows = connection.execute(
            "SELECT * FROM hierarchies WHERE root_id != ? "
            "AND status != 'ABANDONED' "
            "ORDER BY created_at, root_id",
            (requested_root_id,),
        ).fetchall()
        for row in rows:
            stored_hierarchy, _ = _validated_stored_definition(row)
            if (
                _delivery_requirement_key(stored_hierarchy)
                != requirement_key
            ):
                continue
            if row["status"] == "ARCHIVED":
                fail(
                    "SCHEDULER_DELIVERY_REQUIREMENT_CONFLICT",
                    "The external requirement belongs to a completed, "
                    "archived Delivery; a new Delivery requires a new "
                    "external requirement identity",
                    requirementKey=requirement_key,
                    existingRootId=row["root_id"],
                    requestedRootId=requested_root_id,
                    nextAction="CREATE_NEW_REQUIREMENT_AND_DELIVERY",
                )
            fail(
                "SCHEDULER_DELIVERY_REQUIREMENT_CONFLICT",
                "The external requirement already belongs to another "
                "Delivery; reuse its stable Delivery ID and create a "
                "revision",
                requirementKey=requirement_key,
                existingRootId=row["root_id"],
                requestedRootId=requested_root_id,
                nextAction=(
                    "REUSE_EXISTING_DELIVERY_ID_AND_CREATE_REVISION"
                ),
            )

    def assert_delivery_requirement_available(
        self,
        hierarchy: dict[str, Any],
    ) -> None:
        """Reject a ticket-like requirement mapped to another Delivery ID."""

        if not self.database_path.is_file():
            return
        with self.read() as connection:
            self._assert_delivery_requirement_available(
                connection,
                hierarchy,
            )

    @staticmethod
    def workspace_key(workspace_root: str | os.PathLike[str]) -> str:
        return workspace_identity(workspace_root).key
