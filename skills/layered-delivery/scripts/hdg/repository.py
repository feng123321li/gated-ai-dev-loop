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
    validate_delivery_graph,
)
from .jsonio import canonical_json, fingerprint
from .model_core import validate_hierarchy_definition
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
                root_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                cancelled_at TEXT,
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

    def workspace_status(self) -> dict[str, Any]:
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
            latest = rows[0]
            _validated_stored_definition(latest)
            run = connection.execute(
                "SELECT status FROM runs WHERE root_id = ?",
                (latest["root_id"],),
            ).fetchone()
        state = (
            run["status"]
            if run is not None
            else latest["status"]
        )
        # Projection templates are rebuildable views, not stored schema.
        # Refresh every stored schema-v3 Delivery so workspaces created by an
        # earlier plugin release receive the current fixed projection set.
        for row in rows:
            self.write_projections(row["root_id"])
        return {
            "status": (
                "PREPARED"
                if state == "PREPARED"
                else state
            ),
            "rootId": latest["root_id"],
            "controlRoot": GOVERNANCE_DIRECTORY,
        }

    def prepare(
        self,
        hierarchy: dict[str, Any],
        graph: dict[str, Any],
        *,
        hierarchy_fingerprint: str,
        graph_fingerprint: str,
    ) -> dict[str, Any]:
        root_id = graph["rootId"]
        with self.transaction() as connection:
            active = connection.execute(
                "SELECT h.root_id FROM hierarchies h "
                "JOIN runs r ON r.root_id = h.root_id "
                "WHERE h.status = 'FROZEN' "
                "AND r.status NOT IN ('COMPLETED', 'CANCELLED') "
                "AND h.root_id != ?",
                (root_id,),
            ).fetchone()
            if active is not None:
                fail(
                    "SCHEDULER_ACTIVE_HIERARCHY_EXISTS",
                    "Only one frozen hierarchy may be active",
                    rootId=active["root_id"],
                )
            frozen = connection.execute(
                "SELECT status, updated_at FROM hierarchies "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if frozen is not None and frozen["status"] == "FROZEN":
                fail(
                    "SCHEDULER_HIERARCHY_FROZEN",
                    "A frozen hierarchy cannot be replaced",
                )
            at = _commit_timestamp(
                self.now,
                frozen["updated_at"] if frozen is not None else None,
            )
            connection.execute(
                """
                INSERT INTO hierarchies(
                    root_id, hierarchy_fingerprint, graph_fingerprint,
                    hierarchy_json, graph_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'PREPARED', ?, ?)
                ON CONFLICT(root_id) DO UPDATE SET
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
        self.write_projections(root_id)
        return {
            "rootId": root_id,
            "status": "PREPARED",
            "hierarchyFingerprint": hierarchy_fingerprint,
            "graphFingerprint": graph_fingerprint,
        }

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
            "status": row["status"],
            "hierarchyFingerprint": row["hierarchy_fingerprint"],
            "graphFingerprint": row["graph_fingerprint"],
            "hierarchy": hierarchy,
            "graph": graph,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def freeze(
        self,
        root_id: str,
        *,
        expected_hierarchy_fingerprint: str,
    ) -> dict[str, Any]:
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
                row["hierarchy_fingerprint"]
                != expected_hierarchy_fingerprint
            ):
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "Hierarchy fingerprint is not current",
                )
            if row["status"] == "FROZEN":
                return self.run(root_id)
            _, graph = _validated_stored_definition(row)
            at = _commit_timestamp(self.now, row["updated_at"])
            run_id = f"run-{uuid.uuid4().hex}"
            connection.execute(
                "UPDATE hierarchies SET status = 'FROZEN', updated_at = ? "
                "WHERE root_id = ?",
                (at, root_id),
            )
            connection.execute(
                "INSERT INTO runs(run_id, root_id, status, started_at, "
                "updated_at) VALUES (?, ?, 'ACTIVE', ?, ?)",
                (run_id, root_id, at, at),
            )
            for node in graph["nodes"]:
                connection.execute(
                    "INSERT INTO node_runs(run_id, node_id, attempt, status) "
                    "VALUES (?, ?, 1, 'PENDING')",
                    (run_id, node["id"]),
                )
            self._append_event(
                connection,
                run_id=run_id,
                node_id=None,
                attempt=None,
                event_type="GRAPH_RUN_STARTED",
                actor="USER",
                operation_id=None,
                payload={},
                at=at,
            )
            self.refresh_ready(connection, graph, run_id, at=at)
        self.write_projections(root_id)
        return self.run(root_id)

    def run(
        self,
        root_id: str,
    ) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if row is None:
                fail(
                    "SCHEDULER_RUN_MISSING",
                    f"Scheduler run is missing: {root_id}",
                )
            nodes = self.latest_nodes(connection, row["run_id"])
        return {
            "runId": row["run_id"],
            "rootId": row["root_id"],
            "status": row["status"],
            "startedAt": row["started_at"],
            "updatedAt": row["updated_at"],
            "completedAt": row["completed_at"],
            "cancelledAt": row["cancelled_at"],
            "nodes": nodes,
        }

    @staticmethod
    def latest_nodes(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> list[dict[str, Any]]:
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
        return [
            {
                "nodeId": row["node_id"],
                "attempt": row["attempt"],
                "status": row["status"],
                "owner": row["owner"],
                "operationId": row["operation_id"],
                "claimedAt": row["claimed_at"],
                "lastHeartbeatAt": row["last_heartbeat_at"],
                "leaseExpiresAt": row["lease_expires_at"],
                "finishedAt": row["finished_at"],
                "outcome": (
                    json.loads(row["outcome_json"])
                    if row["outcome_json"] is not None
                    else None
                ),
                "failureClass": row["failure_class"],
            }
            for row in rows
        ]

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
                "SELECT run_id FROM runs WHERE root_id = ?",
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
        if run_state["status"] in {"COMPLETED", "CANCELLED"}:
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
            documents = render_projection_documents(definition, run)
            optional_interface_projection = safe_path(
                projection_root,
                "interfaces.md",
            )
            if "interfaces.md" not in documents:
                try:
                    optional_interface_projection.unlink()
                except FileNotFoundError:
                    pass
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
                    "SELECT * FROM runs WHERE root_id = ?",
                    (row["root_id"],),
                ).fetchone()
                run = None
                if run_row is not None:
                    run = {
                        "runId": run_row["run_id"],
                        "rootId": run_row["root_id"],
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
