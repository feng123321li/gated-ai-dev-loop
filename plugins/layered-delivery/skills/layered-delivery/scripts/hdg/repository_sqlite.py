from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .constants import SCHEMA_VERSION
from .errors import fail
from .fs_safe import (
    safe_path,
)
from .timing import timed_stage

from .repository_contracts import (
    DATABASE_COLUMN_CONTRACTS,
    DATABASE_TABLES,
    GOVERNANCE_DIRECTORY,
    WORK_ITEM_DATABASE_FILE,
)

def _connect(self, *, create: bool) -> sqlite3.Connection:
    database_path = safe_path(
        self.root,
        f"{GOVERNANCE_DIRECTORY}/{WORK_ITEM_DATABASE_FILE}",
    )
    if database_path.exists():
        database_stat = database_path.lstat()
        if (
            database_path.is_symlink()
            or not database_path.is_file()
        ):
            fail(
                "WORK_ITEM_DATABASE_PATH_INVALID",
                "Governance database must be a regular in-root file",
            )
        if database_stat.st_nlink != 1:
            fail(
                "PATH_HARDLINK",
                "Governance database hard links are not allowed",
            )
    if not create and not database_path.is_file():
        fail("WORK_ITEM_DATABASE_MISSING", "Governance database does not exist")
    if create:
        connection = sqlite3.connect(
            database_path,
            timeout=30.0,
            isolation_level=None,
        )
    else:
        database_uri = (
            database_path.absolute().as_uri()
            + "?mode=ro"
        )
        connection = sqlite3.connect(
            database_uri,
            timeout=30.0,
            isolation_level=None,
            uri=True,
        )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    if create:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA secure_delete = ON")
    else:
        connection.execute("PRAGMA query_only = ON")
    database_stat = database_path.lstat()
    if database_path.is_symlink() or database_stat.st_nlink != 1:
        connection.close()
        fail(
            "WORK_ITEM_DATABASE_PATH_INVALID",
            "Governance database path changed while it was opened",
        )
    return connection

@staticmethod
def _initialize_database(connection: sqlite3.Connection, *, create: bool) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in {0, SCHEMA_VERSION} or (version == 0 and not create):
        fail(
            "WORK_ITEM_DATABASE_SCHEMA_UNSUPPORTED",
            f"Governance database schema {version} is unsupported; expected {SCHEMA_VERSION}",
        )
    if version == SCHEMA_VERSION:
        _assert_database_schema(connection)
        return
    statements = (
        """CREATE TABLE IF NOT EXISTS workspace (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_version INTEGER NOT NULL,
            coordination_root TEXT NOT NULL,
            revision INTEGER NOT NULL,
            current_focus_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS work_items (
            id TEXT PRIMARY KEY,
            entry_json TEXT NOT NULL,
            definition_json TEXT NOT NULL,
            state_json TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS hierarchies (
            root_id TEXT PRIMARY KEY,
            hierarchy_state_json TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS task_contexts (
            work_item_id TEXT PRIMARY KEY,
            context_json TEXT NOT NULL,
            handoff_markdown TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS reports (
            work_item_id TEXT NOT NULL,
            report_kind TEXT NOT NULL,
            report_json TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (work_item_id, report_kind)
        )""",
        """CREATE TABLE IF NOT EXISTS payload_uploads (
            upload_id TEXT PRIMARY KEY,
            generation_id TEXT NOT NULL,
            target_tool TEXT NOT NULL,
            target_argument TEXT NOT NULL,
            total_chunks INTEGER NOT NULL CHECK (total_chunks > 0),
            status TEXT NOT NULL CHECK (
                status IN ('UPLOADING', 'FINALIZING', 'READY', 'INVALID')
            ),
            received_bytes INTEGER NOT NULL CHECK (received_bytes >= 0),
            received_chunks INTEGER NOT NULL CHECK (received_chunks >= 0),
            content_sha256 TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            finalized_at TEXT,
            UNIQUE (upload_id, generation_id)
        )""",
        """CREATE TABLE IF NOT EXISTS payload_chunks (
            upload_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            PRIMARY KEY (upload_id, generation_id, chunk_index),
            FOREIGN KEY (upload_id, generation_id)
                REFERENCES payload_uploads(upload_id, generation_id)
                ON DELETE CASCADE
        )""",
        """CREATE INDEX IF NOT EXISTS payload_uploads_expiry
            ON payload_uploads(expires_at)""",
        """CREATE TABLE IF NOT EXISTS interaction_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uuid TEXT NOT NULL UNIQUE,
            work_item_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            operation_id TEXT,
            host_runtime TEXT,
            payload_json TEXT NOT NULL,
            registry_revision INTEGER,
            recorded_at TEXT NOT NULL,
            previous_hash TEXT,
            event_hash TEXT NOT NULL UNIQUE
        )""",
        """CREATE INDEX IF NOT EXISTS interaction_events_item_order
            ON interaction_events(work_item_id, event_id)""",
        """CREATE TABLE IF NOT EXISTS graph_definitions (
            root_id TEXT PRIMARY KEY,
            hierarchy_fingerprint TEXT NOT NULL,
            graph_fingerprint TEXT NOT NULL UNIQUE,
            definition_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            frozen_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS graph_nodes (
            graph_fingerprint TEXT NOT NULL,
            node_id TEXT NOT NULL,
            node_kind TEXT NOT NULL,
            planes_json TEXT NOT NULL,
            work_item_id TEXT NOT NULL,
            PRIMARY KEY (graph_fingerprint, node_id),
            FOREIGN KEY (graph_fingerprint) REFERENCES graph_definitions(graph_fingerprint) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS graph_edges (
            graph_fingerprint TEXT NOT NULL,
            edge_id TEXT NOT NULL,
            source_node_id TEXT NOT NULL,
            target_node_id TEXT NOT NULL,
            edge_kind TEXT NOT NULL,
            plane TEXT NOT NULL,
            join_group TEXT,
            PRIMARY KEY (graph_fingerprint, edge_id),
            FOREIGN KEY (graph_fingerprint) REFERENCES graph_definitions(graph_fingerprint) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS graph_runs (
            run_id TEXT PRIMARY KEY,
            root_id TEXT NOT NULL UNIQUE,
            graph_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            cancelled_at TEXT,
            record_revision INTEGER NOT NULL,
            FOREIGN KEY (root_id) REFERENCES graph_definitions(root_id) ON DELETE CASCADE,
            FOREIGN KEY (graph_fingerprint) REFERENCES graph_definitions(graph_fingerprint)
        )""",
        """CREATE TABLE IF NOT EXISTS node_runs (
            run_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            status TEXT NOT NULL,
            owner TEXT,
            operation_id TEXT,
            claimed_at TEXT,
            finished_at TEXT,
            latest_evidence_hash TEXT,
            lease_expires_at TEXT,
            last_heartbeat_at TEXT,
            failure_class TEXT,
            last_transition TEXT,
            retry_exhausted INTEGER NOT NULL,
            record_revision INTEGER NOT NULL,
            PRIMARY KEY (run_id, node_id, attempt),
            FOREIGN KEY (run_id) REFERENCES graph_runs(run_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS graph_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uuid TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            graph_fingerprint TEXT NOT NULL,
            node_id TEXT,
            attempt INTEGER,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            operation_id TEXT,
            payload_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            previous_hash TEXT,
            event_hash TEXT NOT NULL UNIQUE,
            FOREIGN KEY (run_id) REFERENCES graph_runs(run_id) ON DELETE CASCADE
        )""",
        """CREATE INDEX IF NOT EXISTS graph_events_run_order
            ON graph_events(run_id, event_id)""",
        """CREATE TABLE IF NOT EXISTS graph_evidence (
            evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            bound_evidence_sha256 TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            graph_fingerprint TEXT NOT NULL,
            node_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            bound_artifact_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES graph_runs(run_id) ON DELETE CASCADE
        )""",
        """CREATE INDEX IF NOT EXISTS graph_evidence_run_node
            ON graph_evidence(run_id, node_id, attempt)""",
    )
    for statement in statements:
        connection.execute(statement)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    _assert_database_schema(connection)

@staticmethod
def _assert_database_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if tables != DATABASE_TABLES:
        fail(
            "WORK_ITEM_DATABASE_SCHEMA_UNSUPPORTED",
            "Governance database does not match the current complete schema v3",
            missing=sorted(DATABASE_TABLES - tables),
            unexpected=sorted(tables - DATABASE_TABLES),
        )
    for table, expected in DATABASE_COLUMN_CONTRACTS.items():
        actual = tuple(
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        )
        if actual != expected:
            fail(
                "WORK_ITEM_DATABASE_SCHEMA_UNSUPPORTED",
                "Governance database does not match the current complete schema v3",
                table=table,
                expectedColumns=list(expected),
                actualColumns=list(actual),
            )
    payload_schema_contracts = {
        "payload_uploads": {
            "positive total_chunks": "check(total_chunks>0)",
            "closed status set": (
                "check(statusin('uploading','finalizing','ready','invalid'))"
            ),
            "non-negative received_bytes": "check(received_bytes>=0)",
            "non-negative received_chunks": "check(received_chunks>=0)",
            "generation identity": "unique(upload_id,generation_id)",
        },
        "payload_chunks": {
            "generation-scoped primary key": (
                "primarykey(upload_id,generation_id,chunk_index)"
            ),
            "generation-scoped cascading foreign key": (
                "foreignkey(upload_id,generation_id)"
                "referencespayload_uploads(upload_id,generation_id)"
                "ondeletecascade"
            ),
        },
    }
    for table, contract in payload_schema_contracts.items():
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        normalized_sql = (
            re.sub(r"\s+", "", str(row["sql"])).casefold()
            if row is not None and row["sql"] is not None
            else ""
        )
        missing_constraints = [
            name
            for name, fragment in contract.items()
            if fragment not in normalized_sql
        ]
        if missing_constraints:
            fail(
                "WORK_ITEM_DATABASE_SCHEMA_UNSUPPORTED",
                "Governance database payload constraint contract is invalid",
                table=table,
                missingConstraints=missing_constraints,
            )
    payload_foreign_keys = {
        (
            row["table"],
            row["from"],
            row["to"],
            row["on_delete"],
        )
        for row in connection.execute(
            "PRAGMA foreign_key_list(payload_chunks)"
        )
    }
    expected_payload_foreign_keys = {
        ("payload_uploads", "upload_id", "upload_id", "CASCADE"),
        (
            "payload_uploads",
            "generation_id",
            "generation_id",
            "CASCADE",
        ),
    }
    if payload_foreign_keys != expected_payload_foreign_keys:
        fail(
            "WORK_ITEM_DATABASE_SCHEMA_UNSUPPORTED",
            "Governance database payload foreign-key contract is invalid",
        )
    expiry_index = tuple(
        row["name"]
        for row in connection.execute(
            "PRAGMA index_info(payload_uploads_expiry)"
        )
    )
    if expiry_index != ("expires_at",):
        fail(
            "WORK_ITEM_DATABASE_SCHEMA_UNSUPPORTED",
            "Governance database payload expiry index is invalid",
        )

def _active_connection(self) -> sqlite3.Connection:
    if self._connection is None:
        fail("WORK_ITEM_TRANSACTION_REQUIRED", "This operation requires an active governance transaction")
    return self._connection

@contextmanager
def staging_transaction(self) -> Iterator[sqlite3.Connection]:
    """Run a short auxiliary write without changing domain revision."""

    self.ensure_runtime_root()
    with timed_stage("sqlite.staging.connect"):
        connection = self._connect(create=True)
    committed = False
    try:
        with timed_stage("sqlite.staging.lockWait"):
            connection.execute("BEGIN IMMEDIATE")
        self._initialize_database(connection, create=True)
        yield connection
        with timed_stage("sqlite.staging.commit"):
            connection.commit()
        committed = True
    except Exception:
        if not committed:
            connection.rollback()
        raise
    finally:
        connection.close()

@contextmanager
def _read_connection(self) -> Iterator[sqlite3.Connection]:
    if self._connection is not None:
        yield self._connection
        return
    connection = self._connect(create=False)
    try:
        self._initialize_database(connection, create=False)
        yield connection
    finally:
        connection.close()
