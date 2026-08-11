from __future__ import annotations

import sqlite3

from .errors import fail


SCHEDULER_STATE_CONTRACT = "schema-v3-graph-compiler-v1"


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
            host_capacity_key TEXT,
            host_capacity_reset_at TEXT,
            host_capacity_reported_at TEXT,
            host_capacity_reason TEXT,
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
        CREATE TABLE IF NOT EXISTS worktree_setup_reservations (
            reservation_id TEXT PRIMARY KEY,
            root_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            project_id TEXT NOT NULL,
            repository_key TEXT NOT NULL,
            repository_root TEXT NOT NULL,
            branch_ref TEXT NOT NULL,
            hierarchy_fingerprint TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 1,
            phase TEXT,
            summary_zh TEXT,
            progress_percent INTEGER,
            issued_at TEXT NOT NULL,
            last_reported_at TEXT,
            lease_expires_at TEXT,
            ready_at TEXT,
            failure_code TEXT,
            failure_message_zh TEXT,
            reconciled_at TEXT,
            last_retry_request_id TEXT,
            UNIQUE(root_id, revision, project_id),
            FOREIGN KEY(root_id) REFERENCES hierarchies(root_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS
        active_worktree_setup_by_repository_branch
        ON worktree_setup_reservations(repository_key, branch_ref)
        WHERE status IN (
            'PENDING', 'IN_PROGRESS', 'READY', 'FAILED', 'EXPIRED'
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
        CREATE TABLE IF NOT EXISTS host_capacity_breakers (
            capacity_key TEXT PRIMARY KEY,
            host_adapter_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            reset_at TEXT NOT NULL,
            report_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            reported_at TEXT NOT NULL,
            restored_at TEXT,
            reason TEXT NOT NULL
        );
        """
    )
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
