from __future__ import annotations

import sqlite3

from .errors import fail


SCHEDULER_STATE_CONTRACT = "schema-v3-graph-compiler-v1"


_COMPATIBLE_INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS node_runs_by_run_status
ON node_runs(run_id, status);
CREATE INDEX IF NOT EXISTS node_runs_by_lease_expires
ON node_runs(lease_expires_at)
WHERE lease_expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS graph_events_by_run_type_event_id
ON graph_events(run_id, event_type, event_id);
CREATE INDEX IF NOT EXISTS active_dispatch_reservations_by_expiry
ON dispatch_reservations(status, expires_at)
WHERE status = 'RESERVED';
"""


def ensure_compatible_scheduler_storage(
    connection: sqlite3.Connection,
) -> None:
    """Apply non-destructive additions within the current state contract."""

    dispatch_columns = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in connection.execute(
            "PRAGMA table_info(dispatch_reservations)"
        ).fetchall()
    }
    for column_name in (
        "agent_profile_id",
        "agent_catalog_fingerprint",
        "team_plan_fingerprint",
    ):
        if dispatch_columns and column_name not in dispatch_columns:
            connection.execute(
                f"ALTER TABLE dispatch_reservations "
                f"ADD COLUMN {column_name} TEXT"
            )
    connection.executescript(_COMPATIBLE_INDEX_SCHEMA)


def initialize_scheduler_storage(connection: sqlite3.Connection) -> None:
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
        CREATE TABLE IF NOT EXISTS scheduler_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            root_id TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            execution_mode TEXT NOT NULL DEFAULT 'active',
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
        CREATE INDEX IF NOT EXISTS graph_events_by_run_event_id
        ON graph_events(run_id, event_id);
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
            continuity_basis TEXT,
            requested_by TEXT,
            confirmed_by TEXT,
            authorized_project_ids_json TEXT,
            execution_mode TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            frozen_at TEXT,
            superseded_at TEXT,
            PRIMARY KEY(root_id, revision),
            FOREIGN KEY(root_id) REFERENCES hierarchies(root_id)
        );
        CREATE TABLE IF NOT EXISTS delivery_preferences (
            root_id TEXT PRIMARY KEY,
            branch_ref TEXT NOT NULL,
            base_ref TEXT NOT NULL,
            base_commit TEXT NOT NULL,
            integration_target TEXT NOT NULL,
            source TEXT NOT NULL,
            chosen_by TEXT NOT NULL,
            chosen_at TEXT NOT NULL,
            FOREIGN KEY(root_id) REFERENCES hierarchies(root_id)
        );
        CREATE TABLE IF NOT EXISTS dispatch_reservations (
            reservation_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            root_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            agent_id TEXT,
            graph_fingerprint TEXT NOT NULL,
            decision_fingerprint TEXT NOT NULL,
            agent_profile_id TEXT,
            agent_catalog_fingerprint TEXT,
            team_plan_fingerprint TEXT,
            status TEXT NOT NULL,
            reserved_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            claimed_at TEXT,
            operation_id TEXT,
            FOREIGN KEY(run_id) REFERENCES runs(run_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS
        active_dispatch_reservation_by_node
        ON dispatch_reservations(run_id, node_id, attempt)
        WHERE status = 'RESERVED';
        """
    )
    ensure_compatible_scheduler_storage(connection)
    connection.execute(
        "INSERT INTO scheduler_metadata(key, value) VALUES (?, ?)",
        ("state_contract", SCHEDULER_STATE_CONTRACT),
    )
    connection.commit()


def verify_scheduler_state_contract(connection: sqlite3.Connection) -> None:
    """Read and reject an incompatible scheduler state contract."""

    metadata_table = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name = 'scheduler_metadata'"
    ).fetchone()
    row = None
    if metadata_table is not None:
        metadata_columns = {
            item["name"]
            for item in connection.execute(
                "PRAGMA table_info(scheduler_metadata)"
            ).fetchall()
        }
        if {"key", "value"}.issubset(metadata_columns):
            row = connection.execute(
                "SELECT value FROM scheduler_metadata WHERE key = ?",
                ("state_contract",),
            ).fetchone()
    actual_contract = (
        row["value"]
        if row is not None and isinstance(row["value"], str)
        else None
    )
    if actual_contract != SCHEDULER_STATE_CONTRACT:
        fail(
            "SCHEDULER_STATE_CONTRACT_MISMATCH",
            "Scheduler state was created by an incompatible graph "
            "generator contract",
            expectedStateContract=SCHEDULER_STATE_CONTRACT,
            actualStateContract=actual_contract,
        )
