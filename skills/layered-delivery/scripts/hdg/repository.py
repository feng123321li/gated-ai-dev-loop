from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import fail
from .fs_safe import (
    atomic_replace_directory,
    atomic_write,
    exclusive_file_lock,
    safe_path,
)
from .graph_model import (
    JOIN_NODE_KINDS,
    compile_delivery_graph,
    graph_fingerprint,
    loop_node_id,
    task_review_node_id,
    validate_delivery_graph,
)
from .jsonio import canonical_json, fingerprint
from .model_core import (
    iter_hierarchy_nodes,
    validate_hierarchy_definition,
)
from .model_rendering import (
    WORK_ITEM_DIRECTORY,
    render_projection_documents,
    render_work_item_projection_documents,
    render_workspace_overview,
)


GOVERNANCE_DIRECTORY = ".layered-delivery"
DATABASE_FILE = "scheduler.db"


def _projection_tree_matches(
    directory: Path,
    documents: dict[str, str],
) -> bool:
    try:
        entries = list(directory.rglob("*"))
    except (FileNotFoundError, NotADirectoryError):
        return False
    if any(entry.is_symlink() for entry in entries):
        return False
    if any(
        not entry.is_dir() and not entry.is_file()
        for entry in entries
    ):
        return False
    relative_files = {
        entry.relative_to(directory).as_posix()
        for entry in entries
        if entry.is_file()
    }
    if relative_files != set(documents):
        return False
    expected_directories = {
        Path(filename).parent.as_posix()
        for filename in documents
        if Path(filename).parent != Path(".")
    }
    actual_directories = {
        entry.relative_to(directory).as_posix()
        for entry in entries
        if entry.is_dir()
    }
    if actual_directories != expected_directories:
        return False
    try:
        return all(
            (directory / filename).read_bytes()
            == content.encode("utf-8")
            for filename, content in documents.items()
        )
    except (FileNotFoundError, OSError):
        return False


def _validated_stored_graph(
    graph_json: object,
    graph_fingerprint: object,
) -> dict[str, Any]:
    if not isinstance(graph_json, str) or not isinstance(
        graph_fingerprint,
        str,
    ):
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler graph metadata is invalid",
        )
    try:
        graph = json.loads(graph_json)
    except (json.JSONDecodeError, RecursionError):
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler graph JSON is invalid",
        )
    if fingerprint(graph) != graph_fingerprint:
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler graph changed",
        )
    return validate_delivery_graph(graph)


def _validated_stored_definition(
    row: sqlite3.Row,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        hierarchy = json.loads(row["hierarchy_json"])
    except (json.JSONDecodeError, RecursionError):
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler hierarchy JSON is invalid",
        )
    if fingerprint(hierarchy) != row["hierarchy_fingerprint"]:
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler hierarchy changed",
        )
    if not isinstance(hierarchy, dict) or "delivery" not in hierarchy:
        fail(
            "SCHEDULER_STATE_INCOMPATIBLE",
            "Stored scheduler state predates the recursive GROUP/TASK "
            "Delivery contract; archive it before creating a new Graph",
        )
    normalized = validate_hierarchy_definition(hierarchy)
    if normalized != hierarchy:
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler hierarchy is not canonical",
        )
    graph = _validated_stored_graph(
        row["graph_json"],
        row["graph_fingerprint"],
    )
    expected_graph = compile_delivery_graph(
        normalized,
        hierarchy_fingerprint=row["hierarchy_fingerprint"],
    )
    if (
        row["root_id"] != normalized["delivery"]["id"]
        or graph["rootId"] != normalized["delivery"]["id"]
        or graph["hierarchyFingerprint"]
        != row["hierarchy_fingerprint"]
        or graph != expected_graph
        or graph_fingerprint(expected_graph) != row["graph_fingerprint"]
    ):
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler graph is not bound to its hierarchy",
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


class SchedulerRepository:
    """SQLite-backed outer-graph scheduler state.

    The repository persists shared Skill hints with the hierarchy and keeps
    Loop descriptors and outcomes as opaque JSON. It never stores per-TASK
    Skill assignments, implementation plans, file scopes, test commands,
    gates, or Skill lifecycle records.
    """

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
            and match.group(1) == "layered-delivery"
            and not explicit_dogfood
        ):
            fail(
                "SELF_HOSTING_DOGFOOD_REQUIRED",
                "Maintaining layered-delivery does not create a runtime "
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
        if self.database_path.exists():
            database_stat = self.database_path.lstat()
            if (
                self.database_path.is_symlink()
                or not self.database_path.is_file()
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
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        self._initialize(connection)
        return connection

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS hierarchies (
                root_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL DEFAULT 1,
                hierarchy_fingerprint TEXT NOT NULL,
                graph_fingerprint TEXT NOT NULL,
                hierarchy_json TEXT NOT NULL,
                graph_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                root_id TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                cancelled_at TEXT,
                superseded_at TEXT,
                superseded_by_revision INTEGER,
                UNIQUE(root_id, revision),
                FOREIGN KEY(root_id) REFERENCES hierarchies(root_id)
            );
            CREATE TABLE IF NOT EXISTS node_runs (
                run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                status TEXT NOT NULL,
                owner TEXT,
                operation_id TEXT,
                claimed_at TEXT,
                last_heartbeat_at TEXT,
                lease_expires_at TEXT,
                finished_at TEXT,
                outcome_json TEXT,
                failure_class TEXT,
                PRIMARY KEY(run_id, node_id, attempt),
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS operation_ids_unique
            ON node_runs(operation_id)
            WHERE operation_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS graph_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_uuid TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                node_id TEXT,
                attempt INTEGER,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                operation_id TEXT,
                payload_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                previous_hash TEXT,
                event_hash TEXT NOT NULL UNIQUE,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS delivery_workspaces (
                root_id TEXT PRIMARY KEY,
                workspace_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(root_id) REFERENCES hierarchies(root_id)
            );
            CREATE INDEX IF NOT EXISTS delivery_workspaces_by_key
            ON delivery_workspaces(workspace_key);
            CREATE TABLE IF NOT EXISTS task_requirement_states (
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(run_id, task_id),
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS delivery_revisions (
                root_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                hierarchy_fingerprint TEXT NOT NULL,
                graph_fingerprint TEXT NOT NULL,
                hierarchy_json TEXT NOT NULL,
                graph_json TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                requested_by TEXT,
                confirmed_by TEXT,
                authorized_project_ids_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                frozen_at TEXT,
                superseded_at TEXT,
                PRIMARY KEY(root_id, revision),
                FOREIGN KEY(root_id) REFERENCES hierarchies(root_id)
            );
            """
        )
        SchedulerRepository._upgrade_revision_storage(connection)

    @staticmethod
    def _upgrade_revision_storage(
        connection: sqlite3.Connection,
    ) -> None:
        """Upgrade internal SQLite storage without accepting old contracts."""

        hierarchy_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(hierarchies)"
            ).fetchall()
        }
        if "revision" not in hierarchy_columns:
            connection.execute(
                "ALTER TABLE hierarchies "
                "ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )

        run_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(runs)"
            ).fetchall()
        }
        needs_run_rebuild = (
            "revision" not in run_columns
            or "superseded_at" not in run_columns
            or "superseded_by_revision" not in run_columns
        )
        if needs_run_rebuild:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.executescript(
                """
                DROP INDEX IF EXISTS operation_ids_unique;
                CREATE TABLE runs_revision_upgrade (
                    run_id TEXT PRIMARY KEY,
                    root_id TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    cancelled_at TEXT,
                    superseded_at TEXT,
                    superseded_by_revision INTEGER,
                    UNIQUE(root_id, revision),
                    FOREIGN KEY(root_id) REFERENCES hierarchies(root_id)
                );
                INSERT INTO runs_revision_upgrade(
                    run_id, root_id, revision, status, started_at,
                    updated_at, completed_at, cancelled_at
                )
                SELECT run_id, root_id, 1, status, started_at,
                       updated_at, completed_at, cancelled_at
                FROM runs;

                CREATE TABLE node_runs_revision_upgrade (
                    run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    owner TEXT,
                    operation_id TEXT,
                    claimed_at TEXT,
                    last_heartbeat_at TEXT,
                    lease_expires_at TEXT,
                    finished_at TEXT,
                    outcome_json TEXT,
                    failure_class TEXT,
                    PRIMARY KEY(run_id, node_id, attempt),
                    FOREIGN KEY(run_id)
                        REFERENCES runs_revision_upgrade(run_id)
                );
                INSERT INTO node_runs_revision_upgrade
                SELECT * FROM node_runs;

                CREATE TABLE graph_events_revision_upgrade (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_uuid TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    node_id TEXT,
                    attempt INTEGER,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    operation_id TEXT,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    previous_hash TEXT,
                    event_hash TEXT NOT NULL UNIQUE,
                    FOREIGN KEY(run_id)
                        REFERENCES runs_revision_upgrade(run_id)
                );
                INSERT INTO graph_events_revision_upgrade
                SELECT * FROM graph_events;

                CREATE TABLE task_requirement_states_revision_upgrade (
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, task_id),
                    FOREIGN KEY(run_id)
                        REFERENCES runs_revision_upgrade(run_id)
                );
                INSERT INTO task_requirement_states_revision_upgrade
                SELECT * FROM task_requirement_states;

                DROP TABLE graph_events;
                DROP TABLE node_runs;
                DROP TABLE task_requirement_states;
                DROP TABLE runs;
                ALTER TABLE runs_revision_upgrade RENAME TO runs;
                ALTER TABLE node_runs_revision_upgrade
                    RENAME TO node_runs;
                ALTER TABLE graph_events_revision_upgrade
                    RENAME TO graph_events;
                ALTER TABLE task_requirement_states_revision_upgrade
                    RENAME TO task_requirement_states;
                CREATE UNIQUE INDEX operation_ids_unique
                ON node_runs(operation_id)
                WHERE operation_id IS NOT NULL;
                """
            )
            connection.execute("PRAGMA foreign_keys = ON")

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS delivery_revisions (
                root_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                hierarchy_fingerprint TEXT NOT NULL,
                graph_fingerprint TEXT NOT NULL,
                hierarchy_json TEXT NOT NULL,
                graph_json TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                requested_by TEXT,
                confirmed_by TEXT,
                authorized_project_ids_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                frozen_at TEXT,
                superseded_at TEXT,
                PRIMARY KEY(root_id, revision),
                FOREIGN KEY(root_id) REFERENCES hierarchies(root_id)
            );
            INSERT OR IGNORE INTO delivery_revisions(
                root_id, revision, hierarchy_fingerprint,
                graph_fingerprint, hierarchy_json, graph_json, status,
                created_at, updated_at, frozen_at
            )
            SELECT root_id, revision, hierarchy_fingerprint,
                   graph_fingerprint, hierarchy_json, graph_json, status,
                   created_at, updated_at,
                   CASE WHEN status = 'FROZEN' THEN updated_at ELSE NULL END
            FROM hierarchies;
            """
        )

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
                "No layered-delivery scheduler state exists",
            )
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def workspace_key(workspace_root: str | os.PathLike[str]) -> str:
        workspace = Path(workspace_root).absolute().resolve(strict=True)
        normalized = os.path.normcase(str(workspace))
        return fingerprint({"workspace": normalized})

    def workspace_binding(self, root_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                "SELECT workspace_key FROM delivery_workspaces "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()
        if row is None:
            fail(
                "SCHEDULER_DELIVERY_WORKSPACE_MISSING",
                f"Delivery workspace binding is missing: {root_id}",
            )
        return {
            "mode": "DEDICATED_CONVERSATION_WORKSPACE",
            "workspaceKey": row["workspace_key"],
        }

    def assert_delivery_workspace(
        self,
        root_id: str,
        workspace_root: str | os.PathLike[str],
    ) -> None:
        if not self.database_path.is_file():
            fail(
                "SCHEDULER_STATE_ABSENT",
                "No layered-delivery scheduler state exists",
            )
        expected = self.workspace_key(workspace_root)
        binding = self.workspace_binding(root_id)
        if binding["workspaceKey"] != expected:
            fail(
                "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
                "This Delivery belongs to another conversation workspace",
                rootId=root_id,
                workspaceKey=binding["workspaceKey"],
            )

    def workspace_status(
        self,
        *,
        root_id: str | None = None,
        workspace_root: str | os.PathLike[str] | None = None,
    ) -> dict[str, Any]:
        self._assert_no_legacy_state()
        if not self.database_path.is_file():
            return {
                "status": "ABSENT",
                "controlRoot": GOVERNANCE_DIRECTORY,
            }
        with self.read() as connection:
            rows = connection.execute(
                "SELECT * "
                "FROM hierarchies ORDER BY updated_at DESC"
            ).fetchall()
            if not rows:
                return {
                    "status": "ABSENT",
                    "controlRoot": GOVERNANCE_DIRECTORY,
                }
            workspace_key = (
                self.workspace_key(workspace_root)
                if workspace_root is not None
                else None
            )
            candidates = rows
            if workspace_key is not None:
                bound_ids = {
                    row["root_id"]
                    for row in connection.execute(
                        "SELECT root_id FROM delivery_workspaces "
                        "WHERE workspace_key = ?",
                        (workspace_key,),
                    ).fetchall()
                }
                candidates = [
                    row for row in rows if row["root_id"] in bound_ids
                ]
            if root_id is not None:
                candidates = [
                    row for row in candidates if row["root_id"] == root_id
                ]
            else:
                active_ids = {
                    row["root_id"]
                    for row in connection.execute(
                        "SELECT root_id FROM runs "
                        "WHERE status NOT IN "
                        "('COMPLETED', 'CANCELLED', 'SUPERSEDED')"
                    ).fetchall()
                }
                active_candidates = [
                    row
                    for row in candidates
                    if row["root_id"] in active_ids
                ]
                if active_candidates:
                    candidates = active_candidates
            if not candidates:
                return {
                    "status": "ABSENT",
                    "controlRoot": GOVERNANCE_DIRECTORY,
                    "workspaceIsolation": (
                        {
                            "mode": "DEDICATED_CONVERSATION_WORKSPACE",
                            "workspaceKey": workspace_key,
                        }
                        if workspace_key is not None
                        else None
                    ),
                }
            latest = candidates[0]
            latest_hierarchy, _ = _validated_stored_definition(latest)
            run = connection.execute(
                "SELECT status FROM runs "
                "WHERE root_id = ? AND revision = ?",
                (latest["root_id"], latest["revision"]),
            ).fetchone()
        state = (
            latest["status"]
            if latest["status"] == "PREPARED"
            else (
                run["status"]
                if run is not None
                else latest["status"]
            )
        )
        # Projection templates are rebuildable views, not stored schema.
        # Refresh every stored schema-v3 Delivery so workspaces created by an
        # earlier plugin release receive the current fixed projection set.
        for row in rows:
            self.write_projections(row["root_id"])
        result = {
            "status": (
                "PREPARED"
                if state == "PREPARED"
                else state
            ),
            "rootId": latest["root_id"],
            "deliveryRevision": latest["revision"],
            "controlRoot": GOVERNANCE_DIRECTORY,
        }
        if workspace_root is not None:
            result["workspaceIsolation"] = self.workspace_binding(
                latest["root_id"]
            )
        git_binding = latest_hierarchy["delivery"].get("gitBinding")
        if git_binding is not None:
            result["gitBinding"] = git_binding
        result["projectScopes"] = latest_hierarchy["delivery"].get(
            "projectScopes",
            [],
        )
        return result

    def prepare(
        self,
        hierarchy: dict[str, Any],
        graph: dict[str, Any],
        *,
        hierarchy_fingerprint: str,
        graph_fingerprint: str,
        workspace_root: str | os.PathLike[str],
    ) -> dict[str, Any]:
        root_id = graph["rootId"]
        workspace_key = self.workspace_key(workspace_root)
        with self.transaction() as connection:
            existing_binding = connection.execute(
                "SELECT workspace_key FROM delivery_workspaces "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if (
                existing_binding is not None
                and existing_binding["workspace_key"] != workspace_key
            ):
                fail(
                    "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
                    "A prepared Delivery cannot move to another workspace",
                    rootId=root_id,
                )
            frozen = connection.execute(
                "SELECT status, revision, updated_at FROM hierarchies "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            existing_run = connection.execute(
                "SELECT 1 FROM runs WHERE root_id = ? LIMIT 1",
                (root_id,),
            ).fetchone()
            if (
                frozen is not None
                and (
                    frozen["status"] == "FROZEN"
                    or frozen["revision"] != 1
                    or existing_run is not None
                )
            ):
                fail(
                    "SCHEDULER_HIERARCHY_FROZEN",
                    "Use prepare_delivery_revision to revise a Delivery "
                    "after its first freeze",
                )
            at = _commit_timestamp(
                self.now,
                frozen["updated_at"] if frozen is not None else None,
            )
            connection.execute(
                """
                INSERT INTO hierarchies(
                    root_id, revision, hierarchy_fingerprint, graph_fingerprint,
                    hierarchy_json, graph_json, status, created_at, updated_at
                ) VALUES (?, 1, ?, ?, ?, ?, 'PREPARED', ?, ?)
                ON CONFLICT(root_id) DO UPDATE SET
                    revision = 1,
                    hierarchy_fingerprint = excluded.hierarchy_fingerprint,
                    graph_fingerprint = excluded.graph_fingerprint,
                    hierarchy_json = excluded.hierarchy_json,
                    graph_json = excluded.graph_json,
                    status = 'PREPARED',
                    updated_at = excluded.updated_at
                """,
                (
                    root_id,
                    hierarchy_fingerprint,
                    graph_fingerprint,
                    canonical_json(hierarchy),
                    canonical_json(graph),
                    at,
                    at,
                ),
            )
            connection.execute(
                """
                INSERT INTO delivery_revisions(
                    root_id, revision, hierarchy_fingerprint,
                    graph_fingerprint, hierarchy_json, graph_json, status,
                    created_at, updated_at
                ) VALUES (?, 1, ?, ?, ?, ?, 'PREPARED', ?, ?)
                ON CONFLICT(root_id, revision) DO UPDATE SET
                    hierarchy_fingerprint =
                        excluded.hierarchy_fingerprint,
                    graph_fingerprint = excluded.graph_fingerprint,
                    hierarchy_json = excluded.hierarchy_json,
                    graph_json = excluded.graph_json,
                    status = 'PREPARED',
                    updated_at = excluded.updated_at
                """,
                (
                    root_id,
                    hierarchy_fingerprint,
                    graph_fingerprint,
                    canonical_json(hierarchy),
                    canonical_json(graph),
                    at,
                    at,
                ),
            )
            connection.execute(
                "INSERT INTO delivery_workspaces("
                "root_id, workspace_key, created_at, updated_at"
                ") VALUES (?, ?, ?, ?) "
                "ON CONFLICT(root_id) DO UPDATE SET "
                "workspace_key = excluded.workspace_key, "
                "updated_at = excluded.updated_at",
                (root_id, workspace_key, at, at),
            )
        self.write_projections(root_id)
        result = {
            "rootId": root_id,
            "deliveryRevision": 1,
            "status": "PREPARED",
            "hierarchyFingerprint": hierarchy_fingerprint,
            "graphFingerprint": graph_fingerprint,
            "workspaceIsolation": {
                "mode": "DEDICATED_CONVERSATION_WORKSPACE",
                "workspaceKey": workspace_key,
            },
        }
        git_binding = hierarchy["delivery"].get("gitBinding")
        if git_binding is not None:
            result["gitBinding"] = git_binding
        result["projectScopes"] = hierarchy["delivery"].get(
            "projectScopes",
            [],
        )
        return result

    def hierarchy(
        self,
        root_id: str | None = None,
    ) -> dict[str, Any]:
        with self.read() as connection:
            if root_id is None:
                row = connection.execute(
                    "SELECT * FROM hierarchies "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM hierarchies WHERE root_id = ?",
                    (root_id,),
                ).fetchone()
        if row is None:
            fail(
                "SCHEDULER_HIERARCHY_MISSING",
                "Scheduler hierarchy is missing",
            )
        hierarchy, graph = _validated_stored_definition(row)
        return {
            "rootId": row["root_id"],
            "deliveryRevision": row["revision"],
            "status": row["status"],
            "hierarchyFingerprint": row["hierarchy_fingerprint"],
            "graphFingerprint": row["graph_fingerprint"],
            "hierarchy": hierarchy,
            "graph": graph,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _carriable_task_ids(
        previous_hierarchy: dict[str, Any],
        revised_hierarchy: dict[str, Any],
        previous_nodes: list[dict[str, Any]],
    ) -> list[str]:
        previous_tasks = {
            item["definition"]["id"]: item
            for item in iter_hierarchy_nodes(previous_hierarchy)
            if item["definition"]["kind"] == "TASK"
        }
        revised_tasks = {
            item["definition"]["id"]: item
            for item in iter_hierarchy_nodes(revised_hierarchy)
            if item["definition"]["kind"] == "TASK"
        }
        state = {
            item["nodeId"]: item["status"]
            for item in previous_nodes
        }
        result = []
        for task_id, revised in revised_tasks.items():
            previous = previous_tasks.get(task_id)
            if (
                previous is None
                or previous["definition"] != revised["definition"]
                or previous["reviewLoop"] != revised["reviewLoop"]
                or state.get(loop_node_id(task_id)) != "SUCCEEDED"
                or state.get(task_review_node_id(task_id)) != "SUCCEEDED"
            ):
                continue
            result.append(task_id)
        return sorted(result)

    def prepare_revision(
        self,
        hierarchy: dict[str, Any],
        graph: dict[str, Any],
        *,
        root_id: str,
        expected_current_revision: int,
        hierarchy_fingerprint: str,
        graph_fingerprint: str,
        reason: str,
        requested_by: str,
        workspace_root: str | os.PathLike[str],
    ) -> dict[str, Any]:
        workspace_key = self.workspace_key(workspace_root)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if row is None:
                fail(
                    "SCHEDULER_HIERARCHY_MISSING",
                    f"Scheduler hierarchy is missing: {root_id}",
                )
            binding = connection.execute(
                "SELECT workspace_key FROM delivery_workspaces "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if (
                binding is None
                or binding["workspace_key"] != workspace_key
            ):
                fail(
                    "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
                    "A Delivery revision must stay in its bound workspace",
                    rootId=root_id,
                )
            if hierarchy["delivery"]["id"] != root_id:
                fail(
                    "SCHEDULER_DELIVERY_IDENTITY_IMMUTABLE",
                    "A Delivery revision must retain the original "
                    "Delivery ID",
                    rootId=root_id,
                )
            if (
                not isinstance(expected_current_revision, int)
                or isinstance(expected_current_revision, bool)
                or expected_current_revision < 1
            ):
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "expected_current_revision must be a positive integer",
                )
            preparing_revision = expected_current_revision + 1
            is_reprepare = (
                row["status"] == "PREPARED"
                and row["revision"] == preparing_revision
            )
            if not is_reprepare and (
                row["status"] != "FROZEN"
                or row["revision"] != expected_current_revision
            ):
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "The expected Delivery revision is not current",
                    expectedRevision=expected_current_revision,
                    actualRevision=row["revision"],
                    status=row["status"],
                )
            previous_revision_row = connection.execute(
                "SELECT * FROM delivery_revisions "
                "WHERE root_id = ? AND revision = ?",
                (root_id, expected_current_revision),
            ).fetchone()
            previous_run = connection.execute(
                "SELECT * FROM runs WHERE root_id = ? AND revision = ?",
                (root_id, expected_current_revision),
            ).fetchone()
            if previous_revision_row is None or previous_run is None:
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "The previous frozen Delivery revision is missing",
                )
            if previous_run["status"] in {
                "COMPLETED",
                "SUPERSEDED",
            }:
                fail(
                    "SCHEDULER_DELIVERY_TERMINAL",
                    "Only an unaccepted active Delivery can be revised",
                    runStatus=previous_run["status"],
                )
            previous_hierarchy = validate_hierarchy_definition(
                json.loads(previous_revision_row["hierarchy_json"])
            )
            previous_nodes = self.latest_nodes(
                connection,
                previous_run["run_id"],
            )
            carry_forward = self._carriable_task_ids(
                previous_hierarchy,
                hierarchy,
                previous_nodes,
            )
            at = _commit_timestamp(self.now, row["updated_at"])
            connection.execute(
                """
                INSERT INTO delivery_revisions(
                    root_id, revision, hierarchy_fingerprint,
                    graph_fingerprint, hierarchy_json, graph_json, status,
                    reason, requested_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?, ?, ?)
                ON CONFLICT(root_id, revision) DO UPDATE SET
                    hierarchy_fingerprint =
                        excluded.hierarchy_fingerprint,
                    graph_fingerprint = excluded.graph_fingerprint,
                    hierarchy_json = excluded.hierarchy_json,
                    graph_json = excluded.graph_json,
                    status = 'PREPARED',
                    reason = excluded.reason,
                    requested_by = excluded.requested_by,
                    updated_at = excluded.updated_at
                """,
                (
                    root_id,
                    preparing_revision,
                    hierarchy_fingerprint,
                    graph_fingerprint,
                    canonical_json(hierarchy),
                    canonical_json(graph),
                    reason,
                    requested_by,
                    at,
                    at,
                ),
            )
            connection.execute(
                "UPDATE hierarchies SET revision = ?, "
                "hierarchy_fingerprint = ?, graph_fingerprint = ?, "
                "hierarchy_json = ?, graph_json = ?, status = 'PREPARED', "
                "updated_at = ? WHERE root_id = ?",
                (
                    preparing_revision,
                    hierarchy_fingerprint,
                    graph_fingerprint,
                    canonical_json(hierarchy),
                    canonical_json(graph),
                    at,
                    root_id,
                ),
            )
        self.write_projections(root_id)
        return {
            "rootId": root_id,
            "deliveryRevision": preparing_revision,
            "previousRevision": expected_current_revision,
            "status": "PREPARED",
            "hierarchyFingerprint": hierarchy_fingerprint,
            "graphFingerprint": graph_fingerprint,
            "carryForwardTaskIds": carry_forward,
            "projectScopes": hierarchy["delivery"].get(
                "projectScopes",
                [],
            ),
            "workspaceIsolation": {
                "mode": "DEDICATED_CONVERSATION_WORKSPACE",
                "workspaceKey": workspace_key,
            },
        }

    def freeze(
        self,
        root_id: str,
        *,
        expected_delivery_revision: int,
        expected_hierarchy_fingerprint: str,
        authorized_project_ids: list[str],
        confirmed_by: str,
    ) -> dict[str, Any]:
        carried_forward: list[str] = []
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if row is None:
                fail(
                    "SCHEDULER_HIERARCHY_MISSING",
                    f"Scheduler hierarchy is missing: {root_id}",
                )
            if (
                not isinstance(expected_delivery_revision, int)
                or isinstance(expected_delivery_revision, bool)
                or expected_delivery_revision < 1
                or row["revision"] != expected_delivery_revision
            ):
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "Delivery revision is not current",
                    expectedRevision=expected_delivery_revision,
                    actualRevision=row["revision"],
                )
            binding = connection.execute(
                "SELECT workspace_key FROM delivery_workspaces "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if binding is None:
                fail(
                    "SCHEDULER_DELIVERY_WORKSPACE_MISSING",
                    f"Delivery workspace binding is missing: {root_id}",
                )
            occupied = connection.execute(
                "SELECT r.root_id FROM delivery_workspaces w "
                "JOIN runs r ON r.root_id = w.root_id "
                "WHERE w.workspace_key = ? AND r.root_id != ? "
                "AND r.status NOT IN "
                "('COMPLETED', 'CANCELLED', 'SUPERSEDED') "
                "LIMIT 1",
                (binding["workspace_key"], root_id),
            ).fetchone()
            if occupied is not None:
                fail(
                    "SCHEDULER_DELIVERY_WORKSPACE_OCCUPIED",
                    "A second active Delivery requires another conversation "
                    "worktree",
                    rootId=occupied["root_id"],
                )
            if (
                row["hierarchy_fingerprint"]
                != expected_hierarchy_fingerprint
            ):
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "Hierarchy fingerprint is not current",
                )
            hierarchy, graph = _validated_stored_definition(row)
            project_scopes = hierarchy["delivery"].get(
                "projectScopes",
                [],
            )
            required_project_ids = sorted(
                item["id"] for item in project_scopes
            )
            if (
                not isinstance(authorized_project_ids, list)
                or any(
                    not isinstance(item, str) or not item
                    for item in authorized_project_ids
                )
                or len(set(authorized_project_ids))
                != len(authorized_project_ids)
            ):
                fail(
                    "SCHEDULER_PROJECT_AUTHORIZATION_REQUIRED",
                    "authorized_project_ids must contain unique project IDs",
                )
            supplied_project_ids = sorted(authorized_project_ids)
            if supplied_project_ids != required_project_ids:
                fail(
                    "SCHEDULER_PROJECT_AUTHORIZATION_REQUIRED",
                    "Freeze requires exact authorization of every project "
                    "in this Delivery revision",
                    requiredProjectIds=required_project_ids,
                    suppliedProjectIds=supplied_project_ids,
                    missingProjectIds=sorted(
                        set(required_project_ids)
                        - set(supplied_project_ids)
                    ),
                    unknownProjectIds=sorted(
                        set(supplied_project_ids)
                        - set(required_project_ids)
                    ),
                )
            if row["status"] == "FROZEN":
                return self.run(root_id)
            at = _commit_timestamp(self.now, row["updated_at"])
            previous_run = None
            previous_nodes: dict[str, dict[str, Any]] = {}
            previous_requirement_revisions: dict[str, int] = {}
            if expected_delivery_revision > 1:
                previous_revision = expected_delivery_revision - 1
                previous_definition = connection.execute(
                    "SELECT * FROM delivery_revisions "
                    "WHERE root_id = ? AND revision = ?",
                    (root_id, previous_revision),
                ).fetchone()
                previous_run = connection.execute(
                    "SELECT * FROM runs "
                    "WHERE root_id = ? AND revision = ?",
                    (root_id, previous_revision),
                ).fetchone()
                if previous_definition is None or previous_run is None:
                    fail(
                        "SCHEDULER_REVISION_CONFLICT",
                        "The previous Delivery revision is missing",
                    )
                previous_hierarchy = validate_hierarchy_definition(
                    json.loads(previous_definition["hierarchy_json"])
                )
                previous_node_values = self.latest_nodes(
                    connection,
                    previous_run["run_id"],
                )
                carried_forward = self._carriable_task_ids(
                    previous_hierarchy,
                    hierarchy,
                    previous_node_values,
                )
                previous_nodes = {
                    item["nodeId"]: item
                    for item in previous_node_values
                }
                previous_requirement_revisions = {
                    item["taskId"]: item["revision"]
                    for item in self.task_requirement_states(
                        connection,
                        previous_run["run_id"],
                    )
                }
                connection.execute(
                    "UPDATE node_runs SET status = 'CANCELLED', "
                    "finished_at = COALESCE(finished_at, ?) "
                    "WHERE run_id = ? AND status NOT IN "
                    "('SUCCEEDED', 'COMPLETED', 'CANCELLED')",
                    (at, previous_run["run_id"]),
                )
                self._append_event(
                    connection,
                    run_id=previous_run["run_id"],
                    node_id=None,
                    attempt=None,
                    event_type="GRAPH_RUN_SUPERSEDED",
                    actor="USER",
                    operation_id=None,
                    payload={
                        "fromRevision": previous_revision,
                        "toRevision": expected_delivery_revision,
                        "confirmedBy": confirmed_by,
                    },
                    at=at,
                )
                connection.execute(
                    "UPDATE runs SET status = 'SUPERSEDED', "
                    "updated_at = ?, superseded_at = ?, "
                    "superseded_by_revision = ? WHERE run_id = ?",
                    (
                        at,
                        at,
                        expected_delivery_revision,
                        previous_run["run_id"],
                    ),
                )
                connection.execute(
                    "UPDATE delivery_revisions "
                    "SET status = 'SUPERSEDED', updated_at = ?, "
                    "superseded_at = ? "
                    "WHERE root_id = ? AND revision = ?",
                    (at, at, root_id, previous_revision),
                )
            run_id = f"run-{uuid.uuid4().hex}"
            connection.execute(
                "UPDATE hierarchies SET status = 'FROZEN', updated_at = ? "
                "WHERE root_id = ?",
                (at, root_id),
            )
            connection.execute(
                "INSERT INTO runs(run_id, root_id, revision, status, "
                "started_at, updated_at) "
                "VALUES (?, ?, ?, 'ACTIVE', ?, ?)",
                (
                    run_id,
                    root_id,
                    expected_delivery_revision,
                    at,
                    at,
                ),
            )
            for node in graph["nodes"]:
                carried_task = (
                    node["workItemId"]
                    if (
                        node["kind"]
                        in {"TASK_LOOP", "TASK_REVIEW_LOOP"}
                        and node["workItemId"] in carried_forward
                    )
                    else None
                )
                previous_state = (
                    previous_nodes.get(node["id"])
                    if carried_task is not None
                    else None
                )
                status = (
                    "SUCCEEDED"
                    if previous_state is not None
                    else "PENDING"
                )
                connection.execute(
                    "INSERT INTO node_runs("
                    "run_id, node_id, attempt, status, finished_at, "
                    "outcome_json, failure_class"
                    ") VALUES (?, ?, 1, ?, ?, ?, ?)",
                    (
                        run_id,
                        node["id"],
                        status,
                        at if previous_state is not None else None,
                        (
                            canonical_json(previous_state["outcome"])
                            if (
                                previous_state is not None
                                and previous_state["outcome"] is not None
                            )
                            else None
                        ),
                        (
                            previous_state["failureClass"]
                            if previous_state is not None
                            else None
                        ),
                    ),
                )
                if node["kind"] == "TASK_LOOP":
                    connection.execute(
                        "INSERT INTO task_requirement_states("
                        "run_id, task_id, revision, status, updated_at"
                        ") VALUES (?, ?, ?, 'FROZEN', ?)",
                        (
                            run_id,
                            node["workItemId"],
                            previous_requirement_revisions.get(
                                node["workItemId"],
                                1,
                            ),
                            at,
                        ),
                    )
            self._append_event(
                connection,
                run_id=run_id,
                node_id=None,
                attempt=None,
                event_type="GRAPH_RUN_STARTED",
                actor="USER",
                operation_id=None,
                payload={
                    "deliveryRevision": expected_delivery_revision,
                    "previousRevision": (
                        expected_delivery_revision - 1
                        if expected_delivery_revision > 1
                        else None
                    ),
                    "authorizedProjectIds": required_project_ids,
                },
                at=at,
            )
            for task_id in carried_forward:
                for node_id in (
                    loop_node_id(task_id),
                    task_review_node_id(task_id),
                ):
                    self._append_event(
                        connection,
                        run_id=run_id,
                        node_id=node_id,
                        attempt=1,
                        event_type="NODE_RESULT_CARRIED_FORWARD",
                        actor="CONTROLLER",
                        operation_id=None,
                        payload={
                            "taskId": task_id,
                            "fromRevision": (
                                expected_delivery_revision - 1
                            ),
                            "outcome": previous_nodes[node_id][
                                "outcome"
                            ],
                            "failureClass": previous_nodes[node_id][
                                "failureClass"
                            ],
                            "requirementRevision": (
                                previous_requirement_revisions.get(
                                    task_id,
                                    1,
                                )
                            ),
                        },
                        at=at,
                    )
            connection.execute(
                "UPDATE delivery_revisions SET status = 'FROZEN', "
                "confirmed_by = ?, authorized_project_ids_json = ?, "
                "updated_at = ?, frozen_at = ? "
                "WHERE root_id = ? AND revision = ?",
                (
                    confirmed_by,
                    canonical_json(required_project_ids),
                    at,
                    at,
                    root_id,
                    expected_delivery_revision,
                ),
            )
            self.refresh_ready(connection, graph, run_id, at=at)
        self.write_projections(root_id)
        result = self.run(root_id)
        result["carriedForwardTaskIds"] = carried_forward
        return result

    def run(
        self,
        root_id: str,
    ) -> dict[str, Any]:
        with self.read() as connection:
            hierarchy_row = connection.execute(
                "SELECT * FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if hierarchy_row is None:
                fail(
                    "SCHEDULER_HIERARCHY_MISSING",
                    f"Scheduler hierarchy is missing: {root_id}",
                )
            row = connection.execute(
                "SELECT * FROM runs WHERE root_id = ? AND revision = ?",
                (root_id, hierarchy_row["revision"]),
            ).fetchone()
            if row is None:
                fail(
                    "SCHEDULER_RUN_MISSING",
                    f"Scheduler run is missing: {root_id}",
                )
            nodes = self.latest_nodes(connection, row["run_id"])
            task_requirements = self.task_requirement_states(
                connection,
                row["run_id"],
            )
            workspace = connection.execute(
                "SELECT workspace_key FROM delivery_workspaces "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if workspace is None:
                fail(
                    "SCHEDULER_DELIVERY_WORKSPACE_MISSING",
                    f"Delivery workspace binding is missing: {root_id}",
                )
            hierarchy, _ = _validated_stored_definition(hierarchy_row)
        result = {
            "runId": row["run_id"],
            "rootId": row["root_id"],
            "deliveryRevision": row["revision"],
            "status": row["status"],
            "startedAt": row["started_at"],
            "updatedAt": row["updated_at"],
            "completedAt": row["completed_at"],
            "cancelledAt": row["cancelled_at"],
            "supersededAt": row["superseded_at"],
            "supersededByRevision": row[
                "superseded_by_revision"
            ],
            "nodes": nodes,
            "taskRequirements": task_requirements,
            "workspaceIsolation": {
                "mode": "DEDICATED_CONVERSATION_WORKSPACE",
                "workspaceKey": workspace["workspace_key"],
            },
        }
        git_binding = hierarchy["delivery"].get("gitBinding")
        if git_binding is not None:
            result["gitBinding"] = git_binding
        result["projectScopes"] = hierarchy["delivery"].get(
            "projectScopes",
            [],
        )
        return result

    def revision_history(self, root_id: str) -> dict[str, Any]:
        with self.read() as connection:
            hierarchy = connection.execute(
                "SELECT revision FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if hierarchy is None:
                fail(
                    "SCHEDULER_HIERARCHY_MISSING",
                    f"Scheduler hierarchy is missing: {root_id}",
                )
            rows = connection.execute(
                "SELECT d.*, r.run_id, r.status AS run_status, "
                "r.started_at, r.completed_at, r.cancelled_at, "
                "r.superseded_at AS run_superseded_at "
                "FROM delivery_revisions d "
                "LEFT JOIN runs r ON r.root_id = d.root_id "
                "AND r.revision = d.revision "
                "WHERE d.root_id = ? ORDER BY d.revision",
                (root_id,),
            ).fetchall()
        return {
            "rootId": root_id,
            "currentRevision": hierarchy["revision"],
            "revisions": [
                {
                    "revision": row["revision"],
                    "status": row["status"],
                    "runId": row["run_id"],
                    "runStatus": row["run_status"],
                    "hierarchyFingerprint": row[
                        "hierarchy_fingerprint"
                    ],
                    "graphFingerprint": row["graph_fingerprint"],
                    "reason": row["reason"],
                    "requestedBy": row["requested_by"],
                    "confirmedBy": row["confirmed_by"],
                    "authorizedProjectIds": (
                        json.loads(
                            row["authorized_project_ids_json"]
                        )
                        if row["authorized_project_ids_json"]
                        else []
                    ),
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                    "frozenAt": row["frozen_at"],
                    "completedAt": row["completed_at"],
                    "cancelledAt": row["cancelled_at"],
                    "supersededAt": (
                        row["run_superseded_at"]
                        or row["superseded_at"]
                    ),
                }
                for row in rows
            ],
        }

    @staticmethod
    def task_requirement_states(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT task_id, revision, status, updated_at "
            "FROM task_requirement_states WHERE run_id = ? "
            "ORDER BY task_id",
            (run_id,),
        ).fetchall()
        return [
            {
                "taskId": row["task_id"],
                "revision": row["revision"],
                "status": row["status"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def claimed_resource_reservations(
        self,
        connection: sqlite3.Connection,
        *,
        at: str,
        exclude_root_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return live exact resource locks across every active Delivery."""

        parameters: tuple[object, ...] = ()
        exclusion = ""
        if exclude_root_id is not None:
            exclusion = "AND r.root_id != ? "
            parameters = (exclude_root_id,)
        rows = connection.execute(
            "SELECT h.*, r.root_id AS reservation_root_id, "
            "n.node_id AS reservation_node_id "
            "FROM node_runs n "
            "JOIN runs r ON r.run_id = n.run_id "
            "JOIN delivery_revisions h ON h.root_id = r.root_id "
            "AND h.revision = r.revision "
            "WHERE n.status = 'CLAIMED' "
            "AND r.status NOT IN "
            "('COMPLETED', 'CANCELLED', 'SUPERSEDED') "
            "AND n.lease_expires_at IS NOT NULL "
            "AND n.lease_expires_at >= ? "
            + exclusion
            + "ORDER BY r.root_id, n.node_id",
            (at, *parameters),
        ).fetchall()
        reservations: list[dict[str, Any]] = []
        graph_cache: dict[str, dict[str, Any]] = {}
        for row in rows:
            root_id = row["reservation_root_id"]
            graph = graph_cache.get(root_id)
            if graph is None:
                _, graph = _validated_stored_definition(row)
                graph_cache[root_id] = graph
            node_id = row["reservation_node_id"]
            definition = next(
                (
                    node
                    for node in graph["nodes"]
                    if node["id"] == node_id
                ),
                None,
            )
            if definition is None or definition["loop"] is None:
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "Claimed Loop is missing from its stored Graph",
                )
            reservations.append(
                {
                    "rootId": root_id,
                    "nodeId": node_id,
                    "resourceClaims": definition["loop"][
                        "resourceClaims"
                    ],
                }
            )
        return reservations

    @staticmethod
    def latest_nodes(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> list[dict[str, Any]]:
        executor_metadata: dict[tuple[str, int], dict[str, Any]] = {}
        claim_rows = connection.execute(
            """
            SELECT node_id, attempt, payload_json
            FROM graph_events
            WHERE run_id = ? AND event_type = 'LOOP_CLAIMED'
            ORDER BY event_id
            """,
            (run_id,),
        ).fetchall()
        for claim_row in claim_rows:
            payload = json.loads(claim_row["payload_json"])
            executor_metadata[
                (claim_row["node_id"], claim_row["attempt"])
            ] = payload if isinstance(payload, dict) else {}
        rows = connection.execute(
            """
            SELECT n.* FROM node_runs n
            JOIN (
                SELECT node_id, MAX(attempt) AS attempt
                FROM node_runs WHERE run_id = ? GROUP BY node_id
            ) latest
            ON n.node_id = latest.node_id
            AND n.attempt = latest.attempt
            WHERE n.run_id = ?
            ORDER BY n.node_id
            """,
            (run_id, run_id),
        ).fetchall()
        nodes: list[dict[str, Any]] = []
        for row in rows:
            executor = (
                executor_metadata.get(
                    (row["node_id"], row["attempt"]),
                    {},
                )
                if row["operation_id"] is not None
                else {}
            )
            stored_outcome = (
                json.loads(row["outcome_json"])
                if row["outcome_json"] is not None
                else None
            )
            pause_metadata = (
                stored_outcome.get("schedulerPause", {})
                if row["status"] == "PAUSED"
                and isinstance(stored_outcome, dict)
                else {}
            )
            node = {
                "nodeId": row["node_id"],
                "attempt": row["attempt"],
                "status": row["status"],
                "owner": row["owner"],
                "agentId": executor.get("agentId"),
                "modelId": executor.get("modelId"),
                "dispatchMode": executor.get("dispatchMode"),
                "dispatchReasoningClass": executor.get(
                    "dispatchReasoningClass"
                ),
                "dispatchDecisionFingerprint": executor.get(
                    "dispatchDecisionFingerprint"
                ),
                "operationId": row["operation_id"],
                "claimedAt": row["claimed_at"],
                "lastHeartbeatAt": row["last_heartbeat_at"],
                "leaseExpiresAt": (
                    row["lease_expires_at"]
                    if row["status"] == "CLAIMED"
                    else None
                ),
                "resumeAt": (
                    row["finished_at"]
                    if row["status"] == "PAUSED"
                    else None
                ),
                "finishedAt": (
                    None
                    if row["status"] == "PAUSED"
                    else row["finished_at"]
                ),
                "outcome": (
                    None
                    if row["status"] == "PAUSED"
                    else stored_outcome
                ),
                "failureClass": row["failure_class"],
            }
            capacity_scope = pause_metadata.get("capacityScope")
            if capacity_scope in {"EXECUTOR", "HOST"}:
                node["capacityScope"] = capacity_scope
            nodes.append(node)
        return nodes

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        node_id: str | None,
        attempt: int | None,
        event_type: str,
        actor: str,
        operation_id: str | None,
        payload: dict[str, Any],
        at: str,
    ) -> dict[str, Any]:
        previous = connection.execute(
            "SELECT event_hash FROM graph_events WHERE run_id = ? "
            "ORDER BY event_id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        previous_hash = (
            previous["event_hash"]
            if previous is not None
            else None
        )
        material = {
            "eventUuid": str(uuid.uuid4()),
            "runId": run_id,
            "nodeId": node_id,
            "attempt": attempt,
            "eventType": event_type,
            "actor": actor,
            "operationId": operation_id,
            "payload": payload,
            "recordedAt": at,
            "previousHash": previous_hash,
        }
        event_hash = fingerprint(material)
        connection.execute(
            "INSERT INTO graph_events(event_uuid, run_id, node_id, attempt, "
            "event_type, actor, operation_id, payload_json, recorded_at, "
            "previous_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                material["eventUuid"],
                run_id,
                node_id,
                attempt,
                event_type,
                actor,
                operation_id,
                canonical_json(payload),
                at,
                previous_hash,
                event_hash,
            ),
        )
        return {**material, "eventHash": event_hash}

    def append_event(
        self,
        connection: sqlite3.Connection,
        **arguments: Any,
    ) -> dict[str, Any]:
        return self._append_event(connection, **arguments)

    def events(
        self,
        root_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(after_event_id, int)
            or isinstance(after_event_id, bool)
            or after_event_id < 0
            or not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > 200
        ):
            fail(
                "SCHEDULER_EVENT_PAGE_INVALID",
                "Event cursor or limit is invalid",
            )
        with self.read() as connection:
            run = connection.execute(
                "SELECT r.run_id FROM runs r "
                "JOIN hierarchies h ON h.root_id = r.root_id "
                "AND h.revision = r.revision "
                "WHERE r.root_id = ?",
                (root_id,),
            ).fetchone()
            if run is None:
                fail(
                    "SCHEDULER_RUN_MISSING",
                    f"Scheduler run is missing: {root_id}",
                )
            all_rows = connection.execute(
                "SELECT * FROM graph_events WHERE run_id = ? "
                "ORDER BY event_id",
                (run["run_id"],),
            ).fetchall()
        previous_hash: str | None = None
        result: list[dict[str, Any]] = []
        for row in all_rows:
            payload = json.loads(row["payload_json"])
            material = {
                "eventUuid": row["event_uuid"],
                "runId": row["run_id"],
                "nodeId": row["node_id"],
                "attempt": row["attempt"],
                "eventType": row["event_type"],
                "actor": row["actor"],
                "operationId": row["operation_id"],
                "payload": payload,
                "recordedAt": row["recorded_at"],
                "previousHash": row["previous_hash"],
            }
            if (
                row["previous_hash"] != previous_hash
                or fingerprint(material) != row["event_hash"]
            ):
                fail(
                    "SCHEDULER_EVENT_CHAIN_INVALID",
                    "Stored scheduler event chain changed",
                )
            previous_hash = row["event_hash"]
            if row["event_id"] <= after_event_id:
                continue
            result.append(
                {
                    "eventId": row["event_id"],
                    **material,
                    "eventHash": row["event_hash"],
                }
            )
            if len(result) == limit:
                break
        return result

    def refresh_ready(
        self,
        connection: sqlite3.Connection,
        graph: dict[str, Any],
        run_id: str,
        *,
        at: str,
    ) -> None:
        """Advance dependency-ready nodes and deterministic joins."""

        run_state = connection.execute(
            "SELECT status FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run_state is None:
            fail(
                "SCHEDULER_RUN_MISSING",
                f"Scheduler run is missing: {run_id}",
            )
        if run_state["status"] in {
            "COMPLETED",
            "CANCELLED",
            "SUPERSEDED",
        }:
            return

        incoming: dict[str, list[str]] = {
            node["id"]: []
            for node in graph["nodes"]
        }
        for edge in graph["edges"]:
            incoming[edge["target"]].append(edge["source"])
        node_kind = {
            node["id"]: node["kind"]
            for node in graph["nodes"]
        }
        while True:
            current = {
                node["nodeId"]: node
                for node in self.latest_nodes(connection, run_id)
            }
            changed = False
            for node_id in sorted(current):
                node = current[node_id]
                if node["status"] != "PENDING":
                    continue
                predecessors = incoming[node_id]
                if not all(
                    current[source]["status"]
                    in {"SUCCEEDED", "COMPLETED"}
                    for source in predecessors
                ):
                    continue
                if node_kind[node_id] in JOIN_NODE_KINDS:
                    status = "SUCCEEDED"
                    event_type = "JOIN_COMPLETED"
                    finished = at
                else:
                    status = "READY"
                    event_type = "NODE_READY"
                    finished = None
                connection.execute(
                    "UPDATE node_runs SET status = ?, finished_at = ? "
                    "WHERE run_id = ? AND node_id = ? AND attempt = ?",
                    (
                        status,
                        finished,
                        run_id,
                        node_id,
                        node["attempt"],
                    ),
                )
                self._append_event(
                    connection,
                    run_id=run_id,
                    node_id=node_id,
                    attempt=node["attempt"],
                    event_type=event_type,
                    actor="CONTROLLER",
                    operation_id=None,
                    payload={"predecessors": sorted(predecessors)},
                    at=at,
                )
                changed = True
            if not changed:
                break
        current = {
            node["nodeId"]: node
            for node in self.latest_nodes(connection, run_id)
        }
        confirmation = next(
            node
            for node in graph["nodes"]
            if node["kind"] == "USER_CONFIRMATION"
        )
        confirmation_state = current[confirmation["id"]]["status"]
        if confirmation_state == "COMPLETED":
            connection.execute(
                "UPDATE runs SET status = 'COMPLETED', updated_at = ?, "
                "completed_at = ? WHERE run_id = ?",
                (at, at, run_id),
            )
        elif any(
            node["status"] in {"BLOCKED", "CANCELLED"}
            for node in current.values()
        ):
            connection.execute(
                "UPDATE runs SET status = 'BLOCKED', updated_at = ? "
                "WHERE run_id = ?",
                (at, run_id),
            )
        elif any(
            node["status"] == "PAUSED"
            for node in current.values()
        ) and not any(
            node["status"] in {"READY", "CLAIMED"}
            for node in current.values()
        ):
            connection.execute(
                "UPDATE runs SET status = 'PAUSED', updated_at = ? "
                "WHERE run_id = ?",
                (at, run_id),
            )
        else:
            connection.execute(
                "UPDATE runs SET status = 'ACTIVE', updated_at = ? "
                "WHERE run_id = ? AND status != 'CANCELLED'",
                (at, run_id),
            )

    def write_projections(self, root_id: str) -> None:
        """Regenerate controller-owned projections from SQLite state."""

        with exclusive_file_lock(self.lock_path):
            definition = self.hierarchy(root_id)
            run = None
            try:
                run = self.run(root_id)
            except Exception as error:
                if (
                    getattr(error, "code", None)
                    != "SCHEDULER_RUN_MISSING"
                ):
                    raise
            projection_root = safe_path(self.control_root, root_id)
            revision_history = self.revision_history(root_id)
            documents = render_projection_documents(
                definition,
                run,
                revision_history,
            )
            for legacy_filename in (
                "hierarchy.json",
                "graph.json",
                "state.json",
                "interfaces.md",
            ):
                legacy_projection = safe_path(
                    projection_root,
                    legacy_filename,
                )
                if (
                    legacy_projection.is_file()
                    or legacy_projection.is_symlink()
                ):
                    legacy_projection.unlink()
            for filename, content in documents.items():
                atomic_write(
                    projection_root / filename,
                    content,
                )
            work_item_root = safe_path(
                projection_root,
                WORK_ITEM_DIRECTORY,
            )
            work_item_documents = render_work_item_projection_documents(
                definition,
                run,
            )

            def populate_work_items(staging: Path) -> None:
                for filename, content in work_item_documents.items():
                    atomic_write(staging / filename, content)

            if not _projection_tree_matches(
                work_item_root,
                work_item_documents,
            ):
                atomic_replace_directory(
                    work_item_root,
                    populate_work_items,
                )
            legacy_task_baselines = safe_path(
                projection_root,
                "task-baselines",
            )
            if legacy_task_baselines.is_dir():
                shutil.rmtree(legacy_task_baselines)
            elif legacy_task_baselines.exists():
                legacy_task_baselines.unlink()
            atomic_write(
                safe_path(self.control_root, "overview.md"),
                render_workspace_overview(
                    self._workspace_projection_sources()
                ),
            )

    def _workspace_projection_sources(self) -> list[dict[str, Any]]:
        """Load every Delivery summary from SQLite for the root overview."""

        with self.read() as connection:
            rows = connection.execute(
                "SELECT * FROM hierarchies ORDER BY updated_at DESC, root_id"
            ).fetchall()
            sources: list[dict[str, Any]] = []
            for row in rows:
                hierarchy, graph = _validated_stored_definition(row)
                run_row = connection.execute(
                    "SELECT * FROM runs "
                    "WHERE root_id = ? AND revision = ?",
                    (row["root_id"], row["revision"]),
                ).fetchone()
                run = None
                if run_row is not None:
                    run = {
                        "runId": run_row["run_id"],
                        "rootId": run_row["root_id"],
                        "deliveryRevision": run_row["revision"],
                        "status": run_row["status"],
                        "startedAt": run_row["started_at"],
                        "updatedAt": run_row["updated_at"],
                        "completedAt": run_row["completed_at"],
                        "cancelledAt": run_row["cancelled_at"],
                        "nodes": self.latest_nodes(
                            connection,
                            run_row["run_id"],
                        ),
                    }
                sources.append(
                    {
                        "rootId": row["root_id"],
                        "status": row["status"],
                        "hierarchyFingerprint": row[
                            "hierarchy_fingerprint"
                        ],
                        "graphFingerprint": row[
                            "graph_fingerprint"
                        ],
                        "hierarchy": hierarchy,
                        "graph": graph,
                        "createdAt": row["created_at"],
                        "updatedAt": row["updated_at"],
                        "run": run,
                    }
                )
        return sources


GovernanceRepository = SchedulerRepository


__all__ = (
    "DATABASE_FILE",
    "GOVERNANCE_DIRECTORY",
    "GovernanceRepository",
    "SchedulerRepository",
    "timestamp",
)
