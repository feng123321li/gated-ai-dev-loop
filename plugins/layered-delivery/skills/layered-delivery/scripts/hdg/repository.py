from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .constants import SCHEMA_VERSION
from .errors import GatedLoopError, fail
from .evidence import (
    FINGERPRINT,
    evidence_record,
    safe_work_item_id,
    valid_acceptance,
    valid_acceptance_report,
    valid_development_mode,
    valid_evidence_record,
    valid_gate_artifact,
    valid_review_artifact,
    valid_task_result_artifact,
    valid_validation_remediation_artifact,
    valid_timestamp,
)
from .fs_safe import (
    atomic_create_directory,
    atomic_replace_directory,
    atomic_write,
    exclusive_file_lock,
    read_regular_file,
    safe_path,
)
from .graph_model import (
    compile_runtime_policy,
    confirmation_node_id,
    execution_node_id,
    gate_node_id,
    graph_fingerprint,
    review_node_id,
    validate_delivery_graph,
)
from .graph_projections import (
    render_delivery_graph,
    render_frontier_dashboard,
    render_runtime_policy_summary,
    render_run_timeline,
    render_state_transition_graph,
)
from .svg_graphs import render_delivery_graph_svg_assets, render_runtime_policy_svg_assets
from .host_runtime import is_agent_runtime
from .jsonio import canonical_json, sha256_bytes, strict_json_loads
from .model import (
    WORK_ITEM_AUTHORITIES,
    WORK_ITEM_GATE_LEVELS,
    WORK_ITEM_KINDS,
    WORK_ITEM_SKILL_STAGES,
    WORK_ITEM_SCHEMA_VERSION,
    render_development_plan,
    render_hierarchy_plan,
    render_work_item_baseline,
    raw_definition,
    resolve_self_hosting_policy,
    work_item_baseline_fingerprint,
    work_item_child_contract_fingerprint,
    work_item_contract_fingerprint,
    validate_hierarchy_definition,
)
from .projections import (
    render_acceptance_report,
    render_development_review,
    render_item_overview,
    render_item_progress,
    render_interaction_log,
    render_requirement_handoff,
    render_workspace_month_overviews,
    render_workspace_overview,
    report_status,
)
from .timing import timed_stage, timing_increment, timing_metric


WORK_ITEM_DATABASE_FILE = "governance.sqlite3"
PROJECTION_LOCK_FILE = "projection.lock"
LEGACY_REGISTRY_FILE = "work-item-registry.json"
WORK_ITEMS_DIRECTORY = "work-items"
GOVERNANCE_DIRECTORY = ".layered-delivery"
WORK_ITEM_REGISTRY_SCHEMA_VERSION = SCHEMA_VERSION
ENTRY_FIELDS = {
    "id", "kind", "gateLevel", "authorityKind", "parentId", "childIds", "packagePath",
    "developmentPlan", "stage", "status", "baselineFingerprint", "contractFingerprint",
    "parentContractFingerprint", "gate", "acceptance", "acceptanceReport", "developmentMode",
    "claim", "latestEvidence", "latestResult", "recordRevision", "createdAt", "updatedAt", "progress",
}
STATE_FIELDS = {
    "schemaVersion", "id", "stage", "baselineFingerprint", "contractFingerprint",
    "parentContractFingerprint", "hostRuntime", "createdAt", "frozenAt", "baselineRevision", "revisedAt", "review",
}
DATABASE_TABLES = {
    "workspace", "work_items", "hierarchies", "task_contexts", "reports",
    "interaction_events", "graph_definitions", "graph_nodes", "graph_edges",
    "graph_runs", "node_runs", "graph_events", "graph_evidence",
    "payload_uploads", "payload_chunks",
}
DATABASE_COLUMN_CONTRACTS = {
    "workspace": (
        "singleton", "schema_version", "coordination_root", "revision",
        "current_focus_json", "updated_at",
    ),
    "work_items": (
        "id", "entry_json", "definition_json", "state_json",
    ),
    "hierarchies": (
        "root_id", "hierarchy_state_json",
    ),
    "task_contexts": (
        "work_item_id", "context_json", "handoff_markdown", "updated_at",
    ),
    "reports": (
        "work_item_id", "report_kind", "report_json", "generated_at",
    ),
    "interaction_events": (
        "event_id", "event_uuid", "work_item_id", "session_id", "actor",
        "event_type", "summary", "operation_id", "host_runtime",
        "payload_json", "registry_revision", "recorded_at", "previous_hash",
        "event_hash",
    ),
    "graph_definitions": (
        "root_id", "hierarchy_fingerprint", "graph_fingerprint",
        "definition_json", "created_at", "frozen_at",
    ),
    "graph_nodes": (
        "graph_fingerprint", "node_id", "node_kind", "planes_json",
        "work_item_id",
    ),
    "graph_edges": (
        "graph_fingerprint", "edge_id", "source_node_id", "target_node_id",
        "edge_kind", "plane", "join_group",
    ),
    "payload_uploads": (
        "upload_id", "generation_id", "target_tool", "target_argument",
        "total_chunks", "status", "received_bytes", "received_chunks",
        "content_sha256", "created_at", "expires_at", "finalized_at",
    ),
    "payload_chunks": (
        "upload_id", "generation_id", "chunk_index", "chunk_sha256",
        "byte_size", "chunk_text",
    ),
    "graph_runs": (
        "run_id", "root_id", "graph_fingerprint", "status", "started_at",
        "updated_at", "completed_at", "cancelled_at", "record_revision",
    ),
    "node_runs": (
        "run_id", "node_id", "attempt", "status", "owner", "operation_id",
        "claimed_at", "finished_at", "latest_evidence_hash", "lease_expires_at",
        "last_heartbeat_at", "failure_class", "last_transition", "retry_exhausted",
        "record_revision",
    ),
    "graph_events": (
        "event_id", "event_uuid", "run_id", "graph_fingerprint", "node_id",
        "attempt", "event_type", "actor", "operation_id", "payload_json",
        "recorded_at", "previous_hash", "event_hash",
    ),
    "graph_evidence": (
        "evidence_id", "bound_evidence_sha256", "run_id",
        "graph_fingerprint", "node_id", "attempt", "artifact_sha256",
        "bound_artifact_json", "recorded_at",
    ),
}


def _plain_int(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _valid_progress(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"directChildren", "descendants"}:
        return False
    for counts in value.values():
        if not isinstance(counts, dict) or set(counts) != {"total", "verified", "blocked", "active"}:
            return False
        if not all(_plain_int(count) for count in counts.values()):
            return False
        if counts["verified"] + counts["blocked"] + counts["active"] > counts["total"]:
            return False
    return True


def _valid_gate(value: object) -> bool:
    if not isinstance(value, dict) or value.get("status") not in {"NOT_RUN", "PASS", "FAIL"}:
        return False
    if value["status"] == "NOT_RUN":
        return set(value) == {"status", "evidence"} and value["evidence"] is None
    return (
        set(value) == {"status", "evidence", "artifact"}
        and valid_evidence_record(value["evidence"])
        and (value["artifact"] is None or isinstance(value["artifact"], dict))
    )


def _valid_claim(value: object) -> bool:
    valid = (
        isinstance(value, dict)
        and set(value) == {
            "owner", "operationId", "claimedAt", "lastHeartbeatAt", "leaseExpiresAt",
        }
        and isinstance(value.get("owner"), str)
        and bool(value["owner"])
        and isinstance(value.get("operationId"), str)
        and bool(value["operationId"])
        and valid_timestamp(value.get("claimedAt"))
        and valid_timestamp(value.get("lastHeartbeatAt"))
        and valid_timestamp(value.get("leaseExpiresAt"))
    )
    if not valid:
        return False
    claimed = datetime.fromisoformat(value["claimedAt"].replace("Z", "+00:00"))
    heartbeat = datetime.fromisoformat(value["lastHeartbeatAt"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(value["leaseExpiresAt"].replace("Z", "+00:00"))
    return claimed <= heartbeat < expires


def _valid_latest_result(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"evidence", "artifact", "recordedAt"}
        and valid_evidence_record(value.get("evidence"))
        and (value.get("artifact") is None or isinstance(value.get("artifact"), dict))
        and valid_timestamp(value.get("recordedAt"))
    )


def timestamp(now: object = None) -> str:
    value = now() if callable(now) else now
    if value is None:
        date = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        date = value
    elif isinstance(value, str):
        try:
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            fail("WORK_ITEM_TIMESTAMP_INVALID", "Work item timestamp is invalid")
    else:
        fail("WORK_ITEM_TIMESTAMP_INVALID", "Work item timestamp is invalid")
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def timestamp_after(value: object, seconds: int) -> str:
    at = timestamp(value)
    date = datetime.fromisoformat(at.replace("Z", "+00:00")) + timedelta(seconds=seconds)
    return timestamp(date)


class GovernanceRepository:
    """Own safe persistence, package integrity and registry projections."""

    def __init__(self, root: str | os.PathLike[str], *, now: object = None) -> None:
        self.root = Path(root).absolute()
        self.now = now
        self._connection: sqlite3.Connection | None = None
        self._isolated_entry_ids: set[str] = set()
        self._transaction_isolated_entry_ids: set[str] = set()
        self._transaction_isolated_snapshots: dict[str, str] = {}
        self._pending_projection: dict[str, Any] | None = None

    @property
    def governance_root(self) -> Path:
        return self.root / GOVERNANCE_DIRECTORY

    @property
    def database_path(self) -> Path:
        return self.governance_root / WORK_ITEM_DATABASE_FILE

    def item_path(self, entry: dict[str, Any]) -> Path:
        return self.governance_root / entry["packagePath"]

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
            root_stat = self.root.lstat()
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
            GovernanceRepository._assert_database_schema(connection)
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
        GovernanceRepository._assert_database_schema(connection)

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

    def empty_registry(self) -> dict[str, Any]:
        return {
            "schemaVersion": WORK_ITEM_REGISTRY_SCHEMA_VERSION,
            "coordinationRoot": str(self.root),
            "revision": 0,
            "currentFocus": {"workItemId": None, "purpose": None},
            "workItems": [],
            "updatedAt": timestamp(self.now),
        }

    def item_by_id(self, registry: dict[str, Any], item_id: str) -> dict[str, Any]:
        if item_id in self._isolated_entry_ids:
            fail(
                "WORK_ITEM_ENTRY_READ_ONLY_ISOLATED",
                f"Work item is invalid under the current contract and is isolated read-only: {item_id}",
                id=item_id,
            )
        for item in registry["workItems"]:
            if item["id"] == item_id:
                return item
        fail("WORK_ITEM_NOT_FOUND", f"Unknown work item: {item_id}", id=item_id)

    @staticmethod
    def lineage_item_ids(
        registry: dict[str, Any],
        item_id: str,
    ) -> set[str]:
        by_id = {entry["id"]: entry for entry in registry["workItems"]}
        result: set[str] = set()
        current = by_id.get(item_id)
        while current is not None:
            if current["id"] in result:
                fail("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle")
            result.add(current["id"])
            parent_id = current["parentId"]
            current = by_id.get(parent_id) if parent_id is not None else None
        if item_id not in result:
            fail("WORK_ITEM_NOT_FOUND", f"Unknown work item: {item_id}", id=item_id)
        return result

    def is_item_isolated(self, item_id: str) -> bool:
        return item_id in self._isolated_entry_ids

    def assert_subtree_operational(
        self,
        registry: dict[str, Any],
        entry: dict[str, Any],
    ) -> None:
        by_id = {
            candidate["id"]: candidate
            for candidate in registry["workItems"]
        }
        pending = [entry["id"]]
        visited: set[str] = set()
        isolated: list[str] = []
        while pending:
            item_id = pending.pop()
            if item_id in visited:
                fail(
                    "WORK_ITEM_HIERARCHY_CYCLE",
                    "Work item hierarchy contains a cycle",
                )
            visited.add(item_id)
            current = by_id.get(item_id)
            if current is None:
                fail(
                    "WORK_ITEM_HIERARCHY_INVALID",
                    f"Work item hierarchy entry is missing: {item_id}",
                )
            if item_id in self._isolated_entry_ids:
                isolated.append(item_id)
            pending.extend(reversed(current["childIds"]))
        if isolated:
            fail(
                "WORK_ITEM_HIERARCHY_ISOLATED",
                (
                    "A governance transition cannot advance while its "
                    "work-item subtree contains read-only isolated evidence"
                ),
                itemId=entry["id"],
                isolatedItemIds=sorted(isolated),
            )

    @staticmethod
    def _validate_registry_entry(
        entry: dict[str, Any],
        by_id: dict[str, dict[str, Any]],
    ) -> None:
        def hierarchy_root() -> dict[str, Any] | None:
            current = entry
            visited: set[str] = set()
            while current.get("parentId") is not None:
                if current.get("id") in visited:
                    return None
                visited.add(current.get("id"))
                current = by_id.get(current.get("parentId"))
                if current is None:
                    return None
            return current

        valid_entry = (
            set(entry) == ENTRY_FIELDS
            and entry.get("kind") in WORK_ITEM_KINDS
            and entry.get("authorityKind") == WORK_ITEM_AUTHORITIES.get(entry.get("kind"))
            and entry.get("gateLevel") in WORK_ITEM_GATE_LEVELS
            and (entry.get("kind") == "TASK" or entry.get("gateLevel") == "FULL")
            and (entry.get("parentId") is None or safe_work_item_id(entry.get("parentId")))
            and isinstance(entry.get("childIds"), list)
            and all(safe_work_item_id(item) for item in entry["childIds"])
            and isinstance(entry.get("packagePath"), str)
            and entry["packagePath"].replace("\\", "/") == entry["packagePath"]
            and entry["packagePath"].startswith(f"{WORK_ITEMS_DIRECTORY}/")
            and ".." not in entry["packagePath"].split("/")
            and entry.get("developmentPlan") is True
            and bool(FINGERPRINT.fullmatch(str(entry.get("baselineFingerprint", ""))))
            and bool(FINGERPRINT.fullmatch(str(entry.get("contractFingerprint", ""))))
            and (
                entry.get("parentContractFingerprint") is None
                if entry.get("parentId") is None
                else bool(FINGERPRINT.fullmatch(str(entry.get("parentContractFingerprint", ""))))
            )
            and entry.get("stage") in {"WAITING_FOR_BASELINE_CONFIRMATION", "BASELINE_FROZEN"}
            and entry.get("status") in {
                "PREPARED", "FROZEN", "CLAIMED", "IMPLEMENTED", "BLOCKED", "VERIFIED",
            }
            and _valid_gate(entry.get("gate"))
            and _plain_int(entry.get("recordRevision"), minimum=1)
            and valid_timestamp(entry.get("createdAt"))
            and valid_timestamp(entry.get("updatedAt"))
            and _valid_progress(entry.get("progress"))
            and (
                entry.get("latestEvidence") is None
                or valid_evidence_record(entry.get("latestEvidence"))
            )
            and (
                entry.get("latestResult") is None
                or _valid_latest_result(entry.get("latestResult"))
            )
        )
        if not valid_entry:
            fail("WORK_ITEM_REGISTRY_INVALID", f"Work item registry entry is invalid: {entry['id']}")
        root_entry = hierarchy_root()
        if root_entry is None:
            fail("WORK_ITEM_REGISTRY_INVALID", f"Work item hierarchy root is invalid: {entry['id']}")
        mode = entry.get("developmentMode")
        if entry["parentId"] is None:
            if entry["stage"] == "WAITING_FOR_BASELINE_CONFIRMATION" and mode is not None:
                fail("WORK_ITEM_REGISTRY_INVALID", f"Prepared requirement cannot store development mode: {entry['id']}")
            if entry["stage"] == "BASELINE_FROZEN" and not valid_development_mode(mode, entry):
                fail("WORK_ITEM_REGISTRY_INVALID", f"Requirement development mode is invalid: {entry['id']}")
        elif mode is not None:
            fail("WORK_ITEM_REGISTRY_INVALID", f"Only a requirement root can store development mode: {entry['id']}")
        root_mode = root_entry.get("developmentMode")
        if entry["stage"] == "BASELINE_FROZEN" and not valid_development_mode(root_mode, root_entry):
            fail("WORK_ITEM_REGISTRY_INVALID", f"Frozen tree development mode is invalid: {root_entry['id']}")
        if entry["stage"] == "WAITING_FOR_BASELINE_CONFIRMATION":
            if entry["status"] != "PREPARED" or mode is not None:
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item prepared state is inconsistent: {entry['id']}")
        elif entry["status"] == "PREPARED":
            fail("WORK_ITEM_REGISTRY_INVALID", f"Work item frozen state is inconsistent: {entry['id']}")
        if entry["kind"] != "TASK" and entry["status"] in {"CLAIMED", "IMPLEMENTED"}:
            fail("WORK_ITEM_REGISTRY_INVALID", f"Coordination work item status is invalid: {entry['id']}")
        claim = entry.get("claim")
        if (entry["status"] == "CLAIMED") != _valid_claim(claim):
            fail("WORK_ITEM_REGISTRY_INVALID", f"Work item claim is inconsistent: {entry['id']}")
        gate_status = entry["gate"]["status"]
        if (entry["status"] == "VERIFIED") != (gate_status == "PASS"):
            fail("WORK_ITEM_REGISTRY_INVALID", f"Work item PASS state is inconsistent: {entry['id']}")
        if gate_status == "FAIL" and entry["status"] != "BLOCKED":
            fail("WORK_ITEM_REGISTRY_INVALID", f"Work item FAIL state is inconsistent: {entry['id']}")
        if entry["parentId"] is None:
            if not valid_acceptance(entry.get("acceptance")):
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item acceptance state is invalid: {entry['id']}")
        elif entry.get("acceptance") is not None:
            fail("WORK_ITEM_REGISTRY_INVALID", f"Work item acceptance state is invalid: {entry['id']}")
        if not valid_acceptance_report(entry.get("acceptanceReport"), entry):
            fail("WORK_ITEM_REGISTRY_INVALID", f"Work item acceptance report is invalid: {entry['id']}")
        if entry["kind"] == "DELIVERY" and entry["parentId"] is not None:
            fail("WORK_ITEM_REGISTRY_INVALID", "Delivery entries cannot have parents")
        parent = by_id.get(entry["parentId"]) if entry["parentId"] is not None else None
        expected_package = (
            f"{WORK_ITEMS_DIRECTORY}/{entry['id']}"
            if entry["parentId"] is None
            else f"{parent.get('packagePath')}/children/{entry['id']}"
            if parent is not None
            else None
        )
        if entry["packagePath"] != expected_package:
            fail("WORK_ITEM_REGISTRY_INVALID", f"Work item package path is invalid: {entry['id']}")
        if any(child_id not in by_id for child_id in entry["childIds"]):
            fail("WORK_ITEM_REGISTRY_INVALID", f"Work item hierarchy is not fully materialized: {entry['id']}")
        if entry["kind"] != "DELIVERY" and entry["parentId"] is not None:
            expected_kind = "DELIVERY" if entry["kind"] == "CAPABILITY" else "CAPABILITY"
            if not parent or parent.get("kind") != expected_kind or entry["id"] not in parent.get("childIds", []):
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item parent relation is invalid: {entry['id']}")

    @classmethod
    def _is_read_only_evidence_entry(
        cls,
        entry: dict[str, Any],
        by_id: dict[str, dict[str, Any]],
    ) -> bool:
        """Recognize an otherwise-current entry whose stored evidence reference is non-current."""
        candidate = deepcopy(entry)
        normalized_references: list[dict[str, str]] = []

        latest_result = candidate.get("latestResult")
        if isinstance(latest_result, dict) and isinstance(latest_result.get("artifact"), dict):
            latest_result["evidence"] = evidence_record(latest_result["artifact"])
            normalized_references.append(latest_result["evidence"])

        gate = candidate.get("gate")
        if isinstance(gate, dict) and gate.get("status") in {"PASS", "FAIL"}:
            if not isinstance(gate.get("artifact"), dict):
                return False
            gate["evidence"] = evidence_record(gate["artifact"])
            normalized_references.append(gate["evidence"])

        acceptance = candidate.get("acceptance")
        if isinstance(acceptance, dict):
            for key in ("review", "userConfirmation"):
                record = acceptance.get(key)
                if record is None:
                    continue
                if not isinstance(record, dict) or not isinstance(record.get("artifact"), dict):
                    return False
                record["evidence"] = evidence_record(record["artifact"])
                normalized_references.append(record["evidence"])

        if candidate.get("latestEvidence") is not None:
            if not normalized_references:
                return False
            candidate["latestEvidence"] = normalized_references[-1]

        try:
            cls._validate_registry_entry(candidate, by_id)
        except GatedLoopError:
            return False
        return True

    def validate_registry(
        self,
        registry: object,
        *,
        isolate_historical_evidence: bool = False,
    ) -> dict[str, Any]:
        valid = (
            isinstance(registry, dict)
            and registry.get("schemaVersion") == WORK_ITEM_REGISTRY_SCHEMA_VERSION
            and registry.get("coordinationRoot") == str(self.root)
            and isinstance(registry.get("revision"), int)
            and not isinstance(registry.get("revision"), bool)
            and registry["revision"] >= 0
            and isinstance(registry.get("workItems"), list)
            and isinstance(registry.get("currentFocus"), dict)
            and set(registry["currentFocus"]) == {"workItemId", "purpose"}
            and valid_timestamp(registry.get("updatedAt"))
            and set(registry) == {
                "schemaVersion", "coordinationRoot", "revision", "currentFocus", "workItems",
                "updatedAt",
            }
        )
        if not valid:
            fail("WORK_ITEM_REGISTRY_INVALID", "Work item registry is invalid")
        ids = [item.get("id") for item in registry["workItems"] if isinstance(item, dict)]
        if len(ids) != len(registry["workItems"]) or len(set(ids)) != len(ids) or any(not safe_work_item_id(item) for item in ids):
            fail("WORK_ITEM_REGISTRY_INVALID", "Work item registry contains duplicate or unsafe IDs")
        by_id = {item["id"]: item for item in registry["workItems"]}

        isolated_entry_ids: set[str] = set()
        for entry in registry["workItems"]:
            try:
                self._validate_registry_entry(entry, by_id)
            except GatedLoopError:
                if not isolate_historical_evidence or not self._is_read_only_evidence_entry(entry, by_id):
                    raise
                isolated_entry_ids.add(entry["id"])
        focus_id = registry["currentFocus"].get("workItemId")
        if focus_id is not None and (not safe_work_item_id(focus_id) or focus_id not in by_id):
            fail("WORK_ITEM_REGISTRY_INVALID", "Current focus references an unknown work item")
        focus_purpose = registry["currentFocus"].get("purpose")
        if (focus_id is None) != (focus_purpose is None) or (
            focus_purpose is not None and (not isinstance(focus_purpose, str) or not focus_purpose)
        ):
            fail("WORK_ITEM_REGISTRY_INVALID", "Current focus is invalid")
        self._isolated_entry_ids = isolated_entry_ids
        return registry

    def read_registry(
        self,
        *,
        allow_missing: bool = False,
        isolate_historical_evidence: bool = False,
    ) -> dict[str, Any]:
        if not isolate_historical_evidence:
            self._isolated_entry_ids = set()
        if self._connection is None and not self.database_path.is_file():
            if allow_missing:
                self._isolated_entry_ids = set()
                return self.empty_registry()
            fail("WORK_ITEM_DATABASE_MISSING", "Governance database does not exist")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT schema_version, coordination_root, revision, current_focus_json, updated_at "
                "FROM workspace WHERE singleton = 1"
            ).fetchone()
            if row is None:
                if allow_missing:
                    self._isolated_entry_ids = set()
                    return self.empty_registry()
                fail("WORK_ITEM_REGISTRY_MISSING", "Governance registry does not exist")
            try:
                focus = json.loads(row["current_focus_json"])
                entries = [
                    json.loads(item["entry_json"])
                    for item in connection.execute("SELECT entry_json FROM work_items ORDER BY id")
                ]
            except (TypeError, json.JSONDecodeError):
                fail("WORK_ITEM_REGISTRY_INVALID", "Governance registry records are invalid")
            registry = {
                "schemaVersion": row["schema_version"],
                "coordinationRoot": row["coordination_root"],
                "revision": row["revision"],
                "currentFocus": focus,
                "workItems": entries,
                "updatedAt": row["updated_at"],
            }
            return self.validate_registry(
                registry,
                isolate_historical_evidence=isolate_historical_evidence,
            )

    def read_operational_registry(self, *, allow_missing: bool = False) -> dict[str, Any]:
        registry = self.read_registry(
            allow_missing=allow_missing,
            isolate_historical_evidence=True,
        )
        self.validate_stored_evidence(registry)
        return registry

    def validate_operational_registry(self, registry: dict[str, Any]) -> dict[str, Any]:
        self.validate_registry(registry, isolate_historical_evidence=True)
        if self._isolated_entry_ids != self._transaction_isolated_entry_ids:
            fail(
                "WORK_ITEM_ISOLATION_CHANGED",
                "A governance write cannot create, repair, or expand isolated entries implicitly",
            )
        by_id = {entry["id"]: entry for entry in registry["workItems"]}
        for item_id, snapshot in self._transaction_isolated_snapshots.items():
            if item_id not in by_id or canonical_json(by_id[item_id]) != snapshot:
                fail(
                    "WORK_ITEM_ISOLATED_ENTRY_CHANGED",
                    f"A read-only isolated entry cannot be changed: {item_id}",
                    id=item_id,
                )
        return registry

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        self.ensure_runtime_root()
        with timed_stage("sqlite.connect"):
            connection = self._connect(create=True)
        committed = False
        projection_request: dict[str, Any] | None = None
        try:
            with timed_stage("sqlite.lockWait"):
                connection.execute("BEGIN IMMEDIATE")
            self._initialize_database(connection, create=True)
            self._connection = connection
            self._pending_projection = None
            with timed_stage("sqlite.read"):
                registry = self.read_operational_registry(allow_missing=True)
            self._transaction_isolated_entry_ids = set(self._isolated_entry_ids)
            self._transaction_isolated_snapshots = {
                entry["id"]: canonical_json(entry)
                for entry in registry["workItems"]
                if entry["id"] in self._transaction_isolated_entry_ids
            }
            yield registry
            with timed_stage("sqlite.commit"):
                connection.commit()
            committed = True
            projection_request = self._pending_projection
        except Exception:
            if not committed:
                connection.rollback()
            raise
        finally:
            self._transaction_isolated_entry_ids = set()
            self._transaction_isolated_snapshots = {}
            self._pending_projection = None
            self._connection = None
            connection.close()
        if projection_request is not None:
            mode = projection_request["mode"]
            timing_metric("projectionMode", mode)
            try:
                with timed_stage(f"projection.{mode}"):
                    projection_lock = safe_path(
                        self.root,
                        (
                            f"{GOVERNANCE_DIRECTORY}/"
                            f"{PROJECTION_LOCK_FILE}"
                        ),
                    )
                    with exclusive_file_lock(projection_lock):
                        projection_registry = projection_request["registry"]
                        if (
                            self.current_registry_revision()
                            != projection_registry["revision"]
                        ):
                            timing_increment("projectionRefreshRetries")
                            projection_registry = (
                                self.read_operational_registry()
                            )
                            mode = "full"
                        for attempt in range(3):
                            if mode == "heartbeat":
                                self.refresh_heartbeat_projections(
                                    projection_registry,
                                    projection_request["rootId"],
                                )
                            elif mode == "interaction":
                                self.refresh_interaction_projection(
                                    projection_registry,
                                    projection_request["rootId"],
                                )
                            else:
                                self.refresh_registry_projections(
                                    projection_registry,
                                )
                            if (
                                self.current_registry_revision()
                                == projection_registry["revision"]
                            ):
                                break
                            timing_increment("projectionRefreshRetries")
                            projection_registry = (
                                self.read_operational_registry()
                            )
                            mode = "full"
                        else:
                            fail(
                                "WORK_ITEM_PROJECTION_BUSY",
                                (
                                    "Projection could not catch up with "
                                    "concurrent state changes"
                                ),
                            )
            except Exception as error:
                raise GatedLoopError(
                    "WORK_ITEM_PROJECTION_REFRESH_REQUIRED",
                    "Machine state committed, but derived projections require refresh",
                    details={
                        "registryRevision": projection_request["registry"]["revision"],
                        "projectionMode": mode,
                        "cause": type(error).__name__,
                    },
                ) from error

    def current_registry_revision(self) -> int:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT revision FROM workspace WHERE singleton = 1"
            ).fetchone()
        if row is None:
            fail("WORK_ITEM_REGISTRY_MISSING", "Governance registry does not exist")
        return row["revision"]

    def schedule_projection(
        self,
        registry: dict[str, Any],
        *,
        mode: str,
        root_id: str | None = None,
    ) -> None:
        if mode not in {"full", "heartbeat", "interaction"}:
            fail("WORK_ITEM_PROJECTION_MODE_INVALID", "Projection mode is invalid")
        if self._connection is None:
            fail("WORK_ITEM_TRANSACTION_REQUIRED", "Projection scheduling requires an active transaction")
        request = {
            "mode": mode,
            "registry": registry,
            "rootId": root_id,
        }
        current = self._pending_projection
        if current is None or mode == "full":
            self._pending_projection = request
        elif (
            current["mode"] in {"heartbeat", "interaction"}
            and (
                current["mode"] != mode
                or current["rootId"] != root_id
            )
        ):
            self._pending_projection = {
                "mode": "full",
                "registry": registry,
                "rootId": None,
            }

    @staticmethod
    def package_files(
        definition: dict[str, Any],
        state: dict[str, Any],
        *,
        human_plan: str | None = None,
    ) -> dict[str, str]:
        return {
            "baseline.md": render_work_item_baseline(definition),
            "development-plan.md": human_plan or render_development_plan(definition, state),
        }

    def write_new_package(self, target: Path, files: dict[str, str]) -> None:
        def populate(staging: Path) -> None:
            for name, contents in files.items():
                atomic_write(staging / name, contents)

        atomic_create_directory(target, populate)

    def write_hierarchy_package(
        self,
        target: Path,
        packages: list[tuple[Path, dict[str, str]]],
        *,
        replace: bool = False,
    ) -> None:
        """Atomically write one complete requirement tree below its root directory."""
        def populate(staging: Path) -> None:
            for relative, files in packages:
                directory = staging / relative
                directory.mkdir(parents=True, exist_ok=True)
                for name, contents in files.items():
                    atomic_write(directory / name, contents)

        if replace:
            atomic_replace_directory(target, populate)
        else:
            atomic_create_directory(target, populate)

    def store_hierarchy(
        self,
        records: list[dict[str, Any]],
        states: dict[str, dict[str, Any]],
        hierarchy_state: dict[str, Any],
    ) -> None:
        """Store the complete machine-authoritative requirement tree in SQLite."""
        connection = self._active_connection()
        for record in records:
            definition = record["definition"]
            state = states[definition["id"]]
            connection.execute(
                "INSERT INTO work_items(id, entry_json, definition_json, state_json) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET definition_json = excluded.definition_json, "
                "state_json = excluded.state_json",
                (
                    definition["id"],
                    "{}",
                    canonical_json(definition),
                    canonical_json(state),
                ),
            )
        connection.execute(
            "INSERT INTO hierarchies(root_id, hierarchy_state_json) VALUES (?, ?) "
            "ON CONFLICT(root_id) DO UPDATE SET hierarchy_state_json = excluded.hierarchy_state_json",
            (hierarchy_state["rootId"], canonical_json(hierarchy_state)),
        )

    def read_hierarchy_state(self, root_id: str) -> dict[str, Any]:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT hierarchy_state_json FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
        if row is None:
            fail("WORK_ITEM_HIERARCHY_INVALID", "Hierarchy state is missing")
        try:
            value = json.loads(row["hierarchy_state_json"])
        except (TypeError, json.JSONDecodeError):
            fail("WORK_ITEM_HIERARCHY_INVALID", "Hierarchy state is invalid")
        if not isinstance(value, dict):
            fail("WORK_ITEM_HIERARCHY_INVALID", "Hierarchy state is invalid")
        return value

    def store_graph_definition(
        self,
        graph: dict[str, Any],
        *,
        graph_fingerprint_value: str,
        created_at: str,
    ) -> None:
        normalized = validate_delivery_graph(graph)
        if graph_fingerprint(normalized) != graph_fingerprint_value:
            fail("DELIVERY_GRAPH_FINGERPRINT_INVALID", "Delivery graph fingerprint does not match its definition")
        connection = self._active_connection()
        existing = connection.execute(
            "SELECT frozen_at FROM graph_definitions WHERE root_id = ?",
            (normalized["rootId"],),
        ).fetchone()
        if existing is not None and existing["frozen_at"] is not None:
            fail("DELIVERY_GRAPH_FROZEN", "A frozen delivery graph cannot be replaced")
        connection.execute(
            "DELETE FROM graph_definitions WHERE root_id = ?",
            (normalized["rootId"],),
        )
        connection.execute(
            "INSERT INTO graph_definitions(root_id, hierarchy_fingerprint, graph_fingerprint, "
            "definition_json, created_at, frozen_at) VALUES (?, ?, ?, ?, ?, NULL)",
            (
                normalized["rootId"],
                normalized["hierarchyFingerprint"],
                graph_fingerprint_value,
                canonical_json(normalized),
                created_at,
            ),
        )
        for node in normalized["nodes"]:
            connection.execute(
                "INSERT INTO graph_nodes(graph_fingerprint, node_id, node_kind, planes_json, work_item_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    graph_fingerprint_value,
                    node["id"],
                    node["kind"],
                    canonical_json(node["planes"]),
                    node["workItemId"],
                ),
            )
        for edge in normalized["edges"]:
            connection.execute(
                "INSERT INTO graph_edges(graph_fingerprint, edge_id, source_node_id, target_node_id, "
                "edge_kind, plane, join_group) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    graph_fingerprint_value,
                    edge["id"],
                    edge["source"],
                    edge["target"],
                    edge["kind"],
                    edge["plane"],
                    edge["joinGroup"],
                ),
            )

    def read_graph_definition(self, root_id: str) -> dict[str, Any]:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT hierarchy_fingerprint, graph_fingerprint, definition_json, created_at, frozen_at "
                "FROM graph_definitions WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if row is None:
                fail("DELIVERY_GRAPH_MISSING", f"Delivery graph is missing: {root_id}")
            node_rows = connection.execute(
                "SELECT node_id, node_kind, planes_json, work_item_id FROM graph_nodes "
                "WHERE graph_fingerprint = ? ORDER BY node_id",
                (row["graph_fingerprint"],),
            ).fetchall()
            edge_rows = connection.execute(
                "SELECT edge_id, source_node_id, target_node_id, edge_kind, plane, join_group "
                "FROM graph_edges WHERE graph_fingerprint = ? ORDER BY edge_id",
                (row["graph_fingerprint"],),
            ).fetchall()
        try:
            graph = validate_delivery_graph(json.loads(row["definition_json"]))
            normalized_nodes = [
                {
                    "id": item["node_id"],
                    "kind": item["node_kind"],
                    "planes": json.loads(item["planes_json"]),
                    "workItemId": item["work_item_id"],
                }
                for item in node_rows
            ]
        except (TypeError, json.JSONDecodeError):
            fail("DELIVERY_GRAPH_INVALID", f"Stored delivery graph is invalid: {root_id}")
        normalized_edges = [
            {
                "id": item["edge_id"],
                "source": item["source_node_id"],
                "target": item["target_node_id"],
                "kind": item["edge_kind"],
                "plane": item["plane"],
                "joinGroup": item["join_group"],
            }
            for item in edge_rows
        ]
        if (
            graph["rootId"] != root_id
            or graph["hierarchyFingerprint"] != row["hierarchy_fingerprint"]
            or graph_fingerprint(graph) != row["graph_fingerprint"]
            or graph["nodes"] != normalized_nodes
            or graph["edges"] != normalized_edges
            or not valid_timestamp(row["created_at"])
            or (row["frozen_at"] is not None and not valid_timestamp(row["frozen_at"]))
        ):
            fail("DELIVERY_GRAPH_INVALID", f"Stored delivery graph changed: {root_id}")
        return {
            "graph": graph,
            "graphFingerprint": row["graph_fingerprint"],
            "createdAt": row["created_at"],
            "frozenAt": row["frozen_at"],
        }

    def freeze_graph_definition(
        self,
        root_id: str,
        *,
        expected_graph_fingerprint: str,
        frozen_at: str,
    ) -> dict[str, Any]:
        stored = self.read_graph_definition(root_id)
        if stored["graphFingerprint"] != expected_graph_fingerprint:
            fail("WORK_ITEM_REVISION_CONFLICT", "The delivery graph fingerprint is not current")
        connection = self._active_connection()
        connection.execute(
            "UPDATE graph_definitions SET frozen_at = COALESCE(frozen_at, ?) WHERE root_id = ?",
            (frozen_at, root_id),
        )
        return self.read_graph_definition(root_id)

    def start_graph_run(self, root_id: str, *, started_at: str) -> dict[str, Any]:
        stored = self.read_graph_definition(root_id)
        if stored["frozenAt"] is None:
            fail("DELIVERY_GRAPH_NOT_FROZEN", "Delivery graph must be frozen before it can run")
        connection = self._active_connection()
        existing = connection.execute(
            "SELECT run_id FROM graph_runs WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        if existing is not None:
            return self.read_graph_run(root_id)
        run_id = f"run-{uuid.uuid4().hex}"
        connection.execute(
            "INSERT INTO graph_runs(run_id, root_id, graph_fingerprint, status, started_at, "
            "updated_at, completed_at, cancelled_at, record_revision) "
            "VALUES (?, ?, ?, 'ACTIVE', ?, ?, NULL, NULL, 1)",
            (run_id, root_id, stored["graphFingerprint"], started_at, started_at),
        )
        for node in stored["graph"]["nodes"]:
            connection.execute(
                "INSERT INTO node_runs(run_id, node_id, attempt, status, owner, operation_id, "
                "claimed_at, finished_at, latest_evidence_hash, lease_expires_at, "
                "last_heartbeat_at, failure_class, last_transition, retry_exhausted, record_revision) "
                "VALUES (?, ?, 1, 'PENDING', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, 1)",
                (run_id, node["id"]),
            )
        return self.read_graph_run(root_id)

    def read_graph_run(
        self,
        root_id: str,
        *,
        allow_missing: bool = False,
    ) -> dict[str, Any] | None:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT run_id, root_id, graph_fingerprint, status, started_at, updated_at, "
                "completed_at, cancelled_at, record_revision FROM graph_runs WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if row is None:
                if allow_missing:
                    return None
                fail("DELIVERY_GRAPH_RUN_MISSING", f"Delivery graph run is missing: {root_id}")
            node_rows = connection.execute(
                "SELECT run_id, node_id, attempt, status, owner, operation_id, claimed_at, "
                "finished_at, latest_evidence_hash, lease_expires_at, last_heartbeat_at, "
                "failure_class, last_transition, retry_exhausted, record_revision FROM node_runs "
                "WHERE run_id = ? ORDER BY node_id, attempt",
                (row["run_id"],),
            ).fetchall()
        if (
            row["status"] not in {"ACTIVE", "BLOCKED", "PAUSED", "CANCELLED", "COMPLETED"}
            or not valid_timestamp(row["started_at"])
            or not valid_timestamp(row["updated_at"])
            or (row["completed_at"] is not None and not valid_timestamp(row["completed_at"]))
            or (row["cancelled_at"] is not None and not valid_timestamp(row["cancelled_at"]))
            or not _plain_int(row["record_revision"], minimum=1)
        ):
            fail("DELIVERY_GRAPH_RUN_INVALID", f"Delivery graph run is invalid: {root_id}")
        attempts = [
            {
                "nodeId": item["node_id"],
                "attempt": item["attempt"],
                "status": item["status"],
                "owner": item["owner"],
                "operationId": item["operation_id"],
                "claimedAt": item["claimed_at"],
                "finishedAt": item["finished_at"],
                "latestEvidenceHash": item["latest_evidence_hash"],
                "leaseExpiresAt": item["lease_expires_at"],
                "lastHeartbeatAt": item["last_heartbeat_at"],
                "failureClass": item["failure_class"],
                "lastTransition": item["last_transition"],
                "retryExhausted": bool(item["retry_exhausted"]),
                "recordRevision": item["record_revision"],
            }
            for item in node_rows
        ]
        latest_by_node: dict[str, dict[str, Any]] = {}
        for attempt in attempts:
            latest_by_node[attempt["nodeId"]] = attempt
        return {
            "runId": row["run_id"],
            "rootId": row["root_id"],
            "graphFingerprint": row["graph_fingerprint"],
            "status": row["status"],
            "startedAt": row["started_at"],
            "updatedAt": row["updated_at"],
            "completedAt": row["completed_at"],
            "cancelledAt": row["cancelled_at"],
            "recordRevision": row["record_revision"],
            "nodes": [latest_by_node[node_id] for node_id in sorted(latest_by_node)],
            "attempts": attempts,
        }

    def begin_graph_attempts(
        self,
        root_id: str,
        node_ids: list[str],
        *,
        at: str,
    ) -> list[dict[str, Any]]:
        run = self.read_graph_run(root_id)
        stored = self.read_graph_definition(root_id)
        known = {node["id"] for node in stored["graph"]["nodes"]}
        unknown = sorted(set(node_ids) - known)
        if unknown:
            fail("DELIVERY_GRAPH_NODE_INVALID", "Cannot retry unknown delivery graph nodes", nodes=unknown)
        connection = self._active_connection()
        attempts = []
        for node_id in sorted(set(node_ids)):
            current = connection.execute(
                "SELECT MAX(attempt) AS attempt FROM node_runs WHERE run_id = ? AND node_id = ?",
                (run["runId"], node_id),
            ).fetchone()["attempt"]
            attempt = current + 1
            connection.execute(
                "INSERT INTO node_runs(run_id, node_id, attempt, status, owner, operation_id, "
                "claimed_at, finished_at, latest_evidence_hash, lease_expires_at, "
                "last_heartbeat_at, failure_class, last_transition, retry_exhausted, record_revision) "
                "VALUES (?, ?, ?, 'PENDING', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, 1)",
                (run["runId"], node_id, attempt),
            )
            attempts.append({"nodeId": node_id, "attempt": attempt, "startedAt": at})
        return attempts

    def append_graph_event(
        self,
        *,
        root_id: str,
        node_id: str | None,
        event_type: str,
        actor: str,
        operation_id: str | None,
        payload: dict[str, Any],
        recorded_at: str,
        evidence_artifact: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self.read_graph_run(root_id)
        if node_id is not None:
            known = {node["nodeId"]: node for node in run["nodes"]}
            if node_id not in known:
                fail("DELIVERY_GRAPH_NODE_INVALID", "Graph event references an unknown node")
            attempt = known[node_id]["attempt"]
        else:
            attempt = None
        connection = self._active_connection()
        event_payload = deepcopy(payload)
        if evidence_artifact is not None:
            if node_id is None or attempt is None or "evidenceBinding" in event_payload:
                fail("DELIVERY_GRAPH_EVIDENCE_INVALID", "Graph evidence requires one unambiguous node attempt")
            artifact_sha256 = evidence_record(evidence_artifact)["sha256"]
            binding_material = {
                "schemaVersion": SCHEMA_VERSION,
                "runId": run["runId"],
                "nodeId": node_id,
                "attempt": attempt,
                "graphFingerprint": run["graphFingerprint"],
                "artifactSha256": artifact_sha256,
                "artifact": evidence_artifact,
            }
            bound_sha256 = sha256_bytes(canonical_json(binding_material).encode("utf-8"))
            binding = {
                key: binding_material[key]
                for key in (
                    "schemaVersion", "runId", "nodeId", "attempt", "graphFingerprint",
                    "artifactSha256",
                )
            }
            binding["boundEvidenceSha256"] = bound_sha256
            bound_artifact = {
                "schemaVersion": SCHEMA_VERSION,
                "kind": "GRAPH_BOUND_EVIDENCE",
                "binding": binding,
                "artifact": evidence_artifact,
            }
            connection.execute(
                "INSERT INTO graph_evidence(bound_evidence_sha256, run_id, graph_fingerprint, "
                "node_id, attempt, artifact_sha256, bound_artifact_json, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    bound_sha256,
                    run["runId"],
                    run["graphFingerprint"],
                    node_id,
                    attempt,
                    artifact_sha256,
                    canonical_json(bound_artifact),
                    recorded_at,
                ),
            )
            event_payload["evidenceBinding"] = binding
        previous_row = connection.execute(
            "SELECT event_hash FROM graph_events WHERE run_id = ? ORDER BY event_id DESC LIMIT 1",
            (run["runId"],),
        ).fetchone()
        previous_hash = previous_row["event_hash"] if previous_row else None
        event_uuid = str(uuid.uuid4())
        hash_payload = {
            "eventUuid": event_uuid,
            "runId": run["runId"],
            "graphFingerprint": run["graphFingerprint"],
            "nodeId": node_id,
            "attempt": attempt,
            "eventType": event_type,
            "actor": actor,
            "operationId": operation_id,
            "payload": event_payload,
            "recordedAt": recorded_at,
            "previousHash": previous_hash,
        }
        event_hash = sha256_bytes(canonical_json(hash_payload).encode("utf-8"))
        connection.execute(
            "INSERT INTO graph_events(event_uuid, run_id, graph_fingerprint, node_id, attempt, event_type, actor, "
            "operation_id, payload_json, recorded_at, previous_hash, event_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_uuid,
                run["runId"],
                run["graphFingerprint"],
                node_id,
                attempt,
                event_type,
                actor,
                operation_id,
                canonical_json(event_payload),
                recorded_at,
                previous_hash,
                event_hash,
            ),
        )
        return {**hash_payload, "eventHash": event_hash}

    def read_graph_evidence(self, root_id: str) -> list[dict[str, Any]]:
        run = self.read_graph_run(root_id, allow_missing=True)
        if run is None:
            return []
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT evidence_id, bound_evidence_sha256, run_id, graph_fingerprint, "
                "node_id, attempt, artifact_sha256, bound_artifact_json, recorded_at "
                "FROM graph_evidence WHERE run_id = ? ORDER BY evidence_id",
                (run["runId"],),
            ).fetchall()
        records = []
        for row in rows:
            try:
                bound_artifact = json.loads(row["bound_artifact_json"])
            except (TypeError, json.JSONDecodeError):
                fail("DELIVERY_GRAPH_EVIDENCE_INVALID", "Stored graph evidence is invalid")
            binding = bound_artifact.get("binding") if isinstance(bound_artifact, dict) else None
            artifact = bound_artifact.get("artifact") if isinstance(bound_artifact, dict) else None
            expected_binding = {
                "schemaVersion": SCHEMA_VERSION,
                "runId": row["run_id"],
                "nodeId": row["node_id"],
                "attempt": row["attempt"],
                "graphFingerprint": row["graph_fingerprint"],
                "artifactSha256": row["artifact_sha256"],
                "boundEvidenceSha256": row["bound_evidence_sha256"],
            }
            material = {
                key: expected_binding[key]
                for key in (
                    "schemaVersion", "runId", "nodeId", "attempt", "graphFingerprint",
                    "artifactSha256",
                )
            }
            material["artifact"] = artifact
            valid = (
                isinstance(bound_artifact, dict)
                and set(bound_artifact) == {"schemaVersion", "kind", "binding", "artifact"}
                and bound_artifact.get("schemaVersion") == SCHEMA_VERSION
                and bound_artifact.get("kind") == "GRAPH_BOUND_EVIDENCE"
                and binding == expected_binding
                and isinstance(artifact, dict)
                and evidence_record(artifact)["sha256"] == row["artifact_sha256"]
                and sha256_bytes(canonical_json(material).encode("utf-8"))
                == row["bound_evidence_sha256"]
                and row["graph_fingerprint"] == run["graphFingerprint"]
                and valid_timestamp(row["recorded_at"])
            )
            if not valid:
                fail("DELIVERY_GRAPH_EVIDENCE_INVALID", "Stored graph evidence binding changed")
            records.append({
                "evidenceId": row["evidence_id"],
                "boundEvidenceSha256": row["bound_evidence_sha256"],
                "boundArtifact": bound_artifact,
                "recordedAt": row["recorded_at"],
            })
        return records

    def read_graph_events(
        self,
        root_id: str,
        *,
        after_event_id: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if (after_event_id is None) != (limit is None) or (
            after_event_id is not None
            and (
                not _plain_int(after_event_id)
                or not _plain_int(limit, minimum=1)
            )
        ):
            fail(
                "DELIVERY_GRAPH_EVENT_PAGE_INVALID",
                "Graph event cursor and limit must be supplied together",
            )
        run = self.read_graph_run(root_id, allow_missing=True)
        if run is None:
            return []
        result = []
        previous_hash = None
        evidence_bindings = {
            record["boundEvidenceSha256"]: record["boundArtifact"]["binding"]
            for record in self.read_graph_evidence(root_id)
        }
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT event_id, event_uuid, run_id, graph_fingerprint, "
                "node_id, attempt, event_type, actor, operation_id, "
                "payload_json, recorded_at, previous_hash, event_hash "
                "FROM graph_events WHERE run_id = ? ORDER BY event_id",
                (run["runId"],),
            )
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"])
                except (TypeError, json.JSONDecodeError):
                    fail(
                        "DELIVERY_GRAPH_EVENT_INVALID",
                        "Stored graph event payload is invalid",
                    )
                hash_payload = {
                    "eventUuid": row["event_uuid"],
                    "runId": row["run_id"],
                    "graphFingerprint": row["graph_fingerprint"],
                    "nodeId": row["node_id"],
                    "attempt": row["attempt"],
                    "eventType": row["event_type"],
                    "actor": row["actor"],
                    "operationId": row["operation_id"],
                    "payload": payload,
                    "recordedAt": row["recorded_at"],
                    "previousHash": row["previous_hash"],
                }
                expected_hash = sha256_bytes(
                    canonical_json(hash_payload).encode("utf-8")
                )
                binding = (
                    payload.get("evidenceBinding")
                    if isinstance(payload, dict)
                    else None
                )
                binding_valid = (
                    binding is None
                    or (
                        isinstance(binding, dict)
                        and binding.get("runId") == run["runId"]
                        and binding.get("graphFingerprint")
                        == run["graphFingerprint"]
                        and binding.get("nodeId") == row["node_id"]
                        and binding.get("attempt") == row["attempt"]
                        and binding
                        == evidence_bindings.get(
                            binding.get("boundEvidenceSha256")
                        )
                    )
                )
                if (
                    row["graph_fingerprint"] != run["graphFingerprint"]
                    or row["previous_hash"] != previous_hash
                    or row["event_hash"] != expected_hash
                    or not binding_valid
                ):
                    fail(
                        "DELIVERY_GRAPH_EVENT_INVALID",
                        "Stored graph event chain is invalid",
                    )
                previous_hash = row["event_hash"]
                if (
                    after_event_id is not None
                    and row["event_id"] <= after_event_id
                ):
                    continue
                result.append({
                    "eventId": row["event_id"],
                    **hash_payload,
                    "eventHash": row["event_hash"],
                })
                if limit is not None and len(result) >= limit:
                    break
        return result

    def sync_graph_runs(
        self,
        registry: dict[str, Any],
        *,
        root_ids: set[str] | None = None,
    ) -> None:
        from .graph_runtime import replay_graph_events

        connection = self._active_connection()
        roots_with_runs = [
            row["root_id"]
            for row in connection.execute("SELECT root_id FROM graph_runs ORDER BY root_id")
            if root_ids is None or row["root_id"] in root_ids
        ]
        for root_id in roots_with_runs:
            stored = self.read_graph_definition(root_id)
            run = self.read_graph_run(root_id)
            replay = replay_graph_events(
                stored["graph"],
                run,
                self.read_graph_events(root_id),
            )
            current_by_attempt = {
                (node["nodeId"], node["attempt"]): node
                for node in run["attempts"]
            }
            changed = False
            for state in replay["attempts"]:
                current = current_by_attempt[(state["nodeId"], state["attempt"])]
                desired = tuple(
                    state[field]
                    for field in (
                        "status", "owner", "operationId", "claimedAt", "finishedAt",
                        "latestEvidenceHash", "leaseExpiresAt", "lastHeartbeatAt",
                        "failureClass", "lastTransition", "retryExhausted", "recordRevision",
                    )
                )
                actual = (
                    current["status"], current["owner"], current["operationId"],
                    current["claimedAt"], current["finishedAt"], current["latestEvidenceHash"],
                    current["leaseExpiresAt"], current["lastHeartbeatAt"],
                    current["failureClass"], current["lastTransition"], current["retryExhausted"],
                    current["recordRevision"],
                )
                if desired == actual:
                    continue
                connection.execute(
                    "UPDATE node_runs SET status = ?, owner = ?, operation_id = ?, claimed_at = ?, "
                    "finished_at = ?, latest_evidence_hash = ?, lease_expires_at = ?, "
                    "last_heartbeat_at = ?, failure_class = ?, last_transition = ?, retry_exhausted = ?, "
                    "record_revision = ? "
                    "WHERE run_id = ? AND node_id = ? AND attempt = ?",
                    (*desired, run["runId"], state["nodeId"], state["attempt"]),
                )
                changed = True
            if (
                changed
                or replay["status"] != run["status"]
                or replay["updatedAt"] != run["updatedAt"]
                or replay["completedAt"] != run["completedAt"]
                or replay["cancelledAt"] != run["cancelledAt"]
            ):
                connection.execute(
                    "UPDATE graph_runs SET status = ?, updated_at = ?, completed_at = ?, cancelled_at = ?, "
                    "record_revision = record_revision + 1 WHERE run_id = ?",
                    (
                        replay["status"], replay["updatedAt"], replay["completedAt"],
                        replay["cancelledAt"],
                        run["runId"],
                    ),
                )

    def rebuild_graph_run_from_events(self, root_id: str) -> dict[str, Any]:
        from .graph_runtime import replay_graph_events

        connection = self._active_connection()
        stored = self.read_graph_definition(root_id)
        run = self.read_graph_run(root_id)
        replay = replay_graph_events(
            stored["graph"],
            run,
            self.read_graph_events(root_id),
        )
        connection.execute("DELETE FROM node_runs WHERE run_id = ?", (run["runId"],))
        for node in replay["attempts"]:
            connection.execute(
                "INSERT INTO node_runs(run_id, node_id, attempt, status, owner, operation_id, "
                "claimed_at, finished_at, latest_evidence_hash, lease_expires_at, "
                "last_heartbeat_at, failure_class, last_transition, retry_exhausted, record_revision) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run["runId"], node["nodeId"], node["attempt"], node["status"],
                    node["owner"], node["operationId"], node["claimedAt"], node["finishedAt"],
                    node["latestEvidenceHash"], node["leaseExpiresAt"],
                    node["lastHeartbeatAt"], node["failureClass"],
                    node["lastTransition"], int(node["retryExhausted"]), node["recordRevision"],
                ),
            )
        connection.execute(
            "UPDATE graph_runs SET status = ?, started_at = ?, updated_at = ?, completed_at = ?, cancelled_at = ?, "
            "record_revision = record_revision + 1 WHERE run_id = ?",
            (
                replay["status"], replay["startedAt"], replay["updatedAt"],
                replay["completedAt"], replay["cancelledAt"], run["runId"],
            ),
        )
        return replay

    def replace_package(
        self,
        target: Path,
        files: dict[str, str],
        *,
        preserve_existing: bool = False,
        remove: tuple[str, ...] = (),
    ) -> None:
        def populate(staging: Path) -> None:
            if preserve_existing:
                for source in target.iterdir():
                    if source.is_symlink():
                        fail("WORK_ITEM_PACKAGE_INVALID", "Work item packages cannot contain symbolic links")
                    destination = staging / source.name
                    if source.is_dir():
                        shutil.copytree(source, destination)
                    else:
                        shutil.copy2(source, destination)
            for name, contents in files.items():
                atomic_write(staging / name, contents)
            for name in remove:
                candidate = staging / name
                if candidate.is_dir():
                    shutil.rmtree(candidate)
                else:
                    try:
                        candidate.unlink()
                    except FileNotFoundError:
                        pass

        atomic_replace_directory(target, populate)

    def read_package(self, registry: dict[str, Any], entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Path]:
        target = self.item_path(entry)
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT definition_json, state_json FROM work_items WHERE id = ?",
                (entry["id"],),
            ).fetchone()
        if row is None:
            fail("WORK_ITEM_PACKAGE_INVALID", f"{entry['id']} is missing from the governance database")
        try:
            definition = json.loads(row["definition_json"])
            state = json.loads(row["state_json"])
        except (TypeError, json.JSONDecodeError):
            fail("WORK_ITEM_PACKAGE_INVALID", f"{entry['id']} database records are invalid")
        baseline_fingerprint = work_item_baseline_fingerprint(definition)
        review = state.get("review")
        review_valid = (
            isinstance(review, dict)
            and set(review) == {"schemaVersion", "status", "baselineFingerprint", "reviewedBy", "reviewedAt"}
            and review.get("schemaVersion") == SCHEMA_VERSION
            and review.get("baselineFingerprint") == baseline_fingerprint
            and (
                (
                    state.get("stage") == "WAITING_FOR_BASELINE_CONFIRMATION"
                    and review.get("status") == "WAITING_FOR_HUMAN_REVIEW"
                    and review.get("reviewedBy") is None
                    and review.get("reviewedAt") is None
                )
                or (
                    state.get("stage") == "BASELINE_FROZEN"
                    and review.get("status") == "APPROVED"
                    and review.get("reviewedBy") == "user"
                    and valid_timestamp(review.get("reviewedAt"))
                )
            )
        )
        valid = (
            set(state) == STATE_FIELDS
            and state.get("schemaVersion") == WORK_ITEM_SCHEMA_VERSION
            and state.get("id") == entry["id"]
            and state.get("stage") == entry["stage"]
            and state.get("baselineFingerprint") == baseline_fingerprint
            and state.get("contractFingerprint") == work_item_contract_fingerprint(definition)
            and entry["baselineFingerprint"] == state["baselineFingerprint"]
            and entry["contractFingerprint"] == state["contractFingerprint"]
            and is_agent_runtime(state.get("hostRuntime"))
            and valid_timestamp(state.get("createdAt"))
            and _plain_int(state.get("baselineRevision"), minimum=1)
            and (state.get("revisedAt") is None or valid_timestamp(state.get("revisedAt")))
            and (
                state.get("frozenAt") is None
                if state.get("stage") == "WAITING_FOR_BASELINE_CONFIRMATION"
                else valid_timestamp(state.get("frozenAt"))
            )
            and review_valid
        )
        if not valid:
            fail("WORK_ITEM_PACKAGE_CHANGED", f"{entry['id']} package changed after preparation", id=entry["id"])
        return definition, state, target

    def assert_current_lineage(
        self,
        registry: dict[str, Any],
        entry: dict[str, Any],
        seen: set[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], Path]:
        visited = seen or set()
        if entry["id"] in visited:
            fail("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle")
        visited.add(entry["id"])
        own = self.read_package(registry, entry)
        if not entry["parentId"]:
            return own
        parent_entry = self.item_by_id(registry, entry["parentId"])
        parent_definition, _, _ = self.read_package(registry, parent_entry)
        actual = work_item_child_contract_fingerprint(parent_definition, entry["id"])
        if entry["parentContractFingerprint"] != actual or own[0]["parentContractFingerprint"] != actual:
            fail(
                "WORK_ITEM_BASELINE_STALE",
                f"{entry['id']} parent contract changed",
                id=entry["id"],
                parentId=parent_entry["id"],
                expected=entry["parentContractFingerprint"],
                actual=actual,
            )
        self.assert_current_lineage(registry, parent_entry, visited)
        return own

    @staticmethod
    def _progress_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "total": len(entries),
            "verified": sum(item.get("status") == "VERIFIED" for item in entries),
            "blocked": sum(item.get("status") == "BLOCKED" for item in entries),
            "active": sum(item.get("status") in {"CLAIMED", "IMPLEMENTED"} for item in entries),
        }

    def recompute_progress(self, registry: dict[str, Any]) -> None:
        by_id = {item["id"]: item for item in registry["workItems"]}

        def descendants(entry: dict[str, Any], visited: set[str] | None = None) -> list[dict[str, Any]]:
            path = visited or set()
            if entry["id"] in path:
                fail("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle")
            next_path = path | {entry["id"]}
            result = []
            for child_id in entry["childIds"]:
                child = by_id.get(child_id, {"id": child_id, "status": "PLANNED", "childIds": []})
                result.append(child)
                if child_id in by_id:
                    result.extend(descendants(child, next_path))
            return result

        for entry in registry["workItems"]:
            if entry["id"] in self._isolated_entry_ids:
                continue
            direct = [by_id.get(item, {"id": item, "status": "PLANNED"}) for item in entry["childIds"]]
            entry["progress"] = {
                "directChildren": self._progress_counts(direct),
                "descendants": self._progress_counts(descendants(entry)),
            }

    @staticmethod
    def _automatic_event_summary(purpose: str) -> str:
        return {
            "HIERARCHY_PLAN_AND_MODE_CONFIRMATION": "层级方案与方式确认",
            "ACTIVE_REQUIREMENT_DISPATCH": "主动开发调度",
            "MANUAL_REQUIREMENT_HANDOFF": "需求级开发交接",
            "EXECUTION": "任务执行状态更新",
            "GATE": "门禁验收状态更新",
            "ACCEPTANCE": "交付验收状态更新",
            "RETRY": "阻断任务重试",
            "VALIDATION_REMEDIATION_RETRY": "原任务验证修正重试",
            "GRAPH_REPLAY_REBUILD": "按图事件回放重建运行快照",
            "TASK_HEARTBEAT": "任务认领心跳续租",
            "TASK_PAUSED": "任务执行显式暂停",
            "TASK_RESUMED": "任务执行恢复",
            "GRAPH_ADVANCED": "图控制器自动推进与恢复",
            "GRAPH_RUN_CANCELLED": "图运行已确认取消",
        }.get(purpose, purpose)

    def append_interaction_event(
        self,
        *,
        work_item_id: str,
        session_id: str,
        actor: str,
        event_type: str,
        summary: str,
        operation_id: str | None,
        host_runtime: str | None,
        payload: dict[str, Any],
        registry_revision: int | None,
        recorded_at: str,
    ) -> dict[str, Any]:
        connection = self._active_connection()
        previous = connection.execute(
            "SELECT event_hash FROM interaction_events ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else None
        event_uuid = uuid.uuid4().hex
        material = {
            "eventUuid": event_uuid,
            "workItemId": work_item_id,
            "sessionId": session_id,
            "actor": actor,
            "eventType": event_type,
            "summary": summary,
            "operationId": operation_id,
            "hostRuntime": host_runtime,
            "payload": payload,
            "registryRevision": registry_revision,
            "recordedAt": recorded_at,
            "previousHash": previous_hash,
        }
        event_hash = sha256_bytes(canonical_json(material).encode("utf-8"))
        cursor = connection.execute(
            "INSERT INTO interaction_events("
            "event_uuid, work_item_id, session_id, actor, event_type, summary, operation_id, "
            "host_runtime, payload_json, registry_revision, recorded_at, previous_hash, event_hash"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_uuid,
                work_item_id,
                session_id,
                actor,
                event_type,
                summary,
                operation_id,
                host_runtime,
                canonical_json(payload),
                registry_revision,
                recorded_at,
                previous_hash,
                event_hash,
            ),
        )
        return {"eventId": cursor.lastrowid, **material, "eventHash": event_hash}

    def read_interaction_events(
        self,
        item_ids: list[str] | None = None,
        *,
        after_event_id: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if (after_event_id is None) != (limit is None) or (
            after_event_id is not None
            and (
                not _plain_int(after_event_id)
                or not _plain_int(limit, minimum=1)
            )
        ):
            fail(
                "WORK_ITEM_INTERACTION_PAGE_INVALID",
                "Interaction event cursor and limit must be supplied together",
            )
        selected_item_ids = set(item_ids) if item_ids is not None else None
        if selected_item_ids is not None and not selected_item_ids:
            return []
        result = []
        previous_hash = None
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT event_id, event_uuid, work_item_id, session_id, actor, "
                "event_type, summary, operation_id, host_runtime, "
                "payload_json, registry_revision, recorded_at, "
                "previous_hash, event_hash "
                "FROM interaction_events ORDER BY event_id"
            )
            for row in rows:
                try:
                    payload = strict_json_loads(row["payload_json"])
                except (
                    TypeError,
                    ValueError,
                    UnicodeError,
                    RecursionError,
                ):
                    fail(
                        "WORK_ITEM_INTERACTION_INVALID",
                        "Stored interaction payload is invalid",
                    )
                material = {
                    "eventUuid": row["event_uuid"],
                    "workItemId": row["work_item_id"],
                    "sessionId": row["session_id"],
                    "actor": row["actor"],
                    "eventType": row["event_type"],
                    "summary": row["summary"],
                    "operationId": row["operation_id"],
                    "hostRuntime": row["host_runtime"],
                    "payload": payload,
                    "registryRevision": row["registry_revision"],
                    "recordedAt": row["recorded_at"],
                    "previousHash": row["previous_hash"],
                }
                expected_hash = sha256_bytes(
                    canonical_json(material).encode("utf-8")
                )
                if (
                    row["previous_hash"] != previous_hash
                    or row["event_hash"] != expected_hash
                ):
                    fail(
                        "WORK_ITEM_INTERACTION_INVALID",
                        "Stored interaction event chain is invalid",
                    )
                previous_hash = row["event_hash"]
                if (
                    selected_item_ids is not None
                    and row["work_item_id"] not in selected_item_ids
                ):
                    continue
                if (
                    after_event_id is not None
                    and row["event_id"] <= after_event_id
                ):
                    continue
                result.append({
                    "eventId": row["event_id"],
                    **material,
                    "eventHash": row["event_hash"],
                })
                if limit is not None and len(result) >= limit:
                    break
        return result

    def read_validation_remediations(
        self,
        item_id: str,
        definition: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Read validated append-only remediation records for one frozen Task."""
        acceptance_ids = {item["id"] for item in definition["acceptance"]}
        result: list[dict[str, Any]] = []
        for event in self.read_interaction_events([item_id]):
            if event["eventType"] != "VALIDATION_REMEDIATION":
                continue
            payload = event["payload"]
            if not isinstance(payload, dict) or set(payload) != {"remediation", "previousState"}:
                fail("WORK_ITEM_REMEDIATION_INVALID", f"Stored validation remediation is invalid: {item_id}")
            record = payload["remediation"]
            previous_state = payload["previousState"]
            if not (
                isinstance(record, dict)
                and set(record) == {"evidence", "artifact", "recordedAt"}
                and isinstance(previous_state, dict)
                and set(previous_state) == {
                    "status", "gate", "acceptance", "latestEvidence", "latestResult",
                }
                and valid_evidence_record(record.get("evidence"))
                and record.get("recordedAt") == event["recordedAt"]
                and valid_validation_remediation_artifact(
                    record.get("artifact"),
                    item_id=item_id,
                    baseline_fingerprint=work_item_baseline_fingerprint(definition),
                    acceptance_ids=acceptance_ids,
                )
                and record["evidence"] == evidence_record(record["artifact"])
            ):
                fail("WORK_ITEM_REMEDIATION_INVALID", f"Stored validation remediation is invalid: {item_id}")
            result.append(deepcopy(record))
        return result

    def effective_task_file_changes(
        self,
        definition: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return frozen file changes plus validated remediation additions."""
        changes = deepcopy(definition["developmentPlan"].get("fileChanges", []))
        for record in self.read_validation_remediations(definition["id"], definition):
            changes.extend(deepcopy(record["artifact"]["fileChanges"]))
        return sorted(changes, key=lambda item: item["path"])

    def effective_required_skills(
        self,
        registry: dict[str, Any],
        entry: dict[str, Any],
        *,
        stage: str | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve inherited Skill requirements for one node and optional stage."""

        if stage is not None and stage not in WORK_ITEM_SKILL_STAGES:
            fail(
                "WORK_ITEM_REQUIRED_SKILL_INVALID",
                f"Unsupported required Skill stage: {stage}",
            )
        by_id = {item["id"]: item for item in registry["workItems"]}
        lineage: list[dict[str, Any]] = []
        current: dict[str, Any] | None = entry
        visited: set[str] = set()
        while current is not None:
            if current["id"] in visited:
                fail(
                    "WORK_ITEM_HIERARCHY_CYCLE",
                    "Work item hierarchy contains a cycle",
                )
            visited.add(current["id"])
            lineage.append(current)
            parent_id = current["parentId"]
            current = by_id.get(parent_id) if parent_id is not None else None
        lineage.reverse()

        aggregated: dict[tuple[str, str], dict[str, Any]] = {}
        for lineage_entry in lineage:
            definition = self.read_package(registry, lineage_entry)[0]
            for requirement in definition["requiredSkills"]:
                for requirement_stage in requirement["stages"]:
                    if stage is not None and requirement_stage != stage:
                        continue
                    key = (requirement["name"], requirement_stage)
                    effective = aggregated.setdefault(key, {
                        "name": requirement["name"],
                        "stage": requirement_stage,
                        "declaredBy": [],
                        "purposes": [],
                    })
                    if lineage_entry["id"] not in effective["declaredBy"]:
                        effective["declaredBy"].append(lineage_entry["id"])
                    if requirement["purpose"] not in effective["purposes"]:
                        effective["purposes"].append(requirement["purpose"])
        stage_order = {
            value: index
            for index, value in enumerate(WORK_ITEM_SKILL_STAGES)
        }
        return sorted(
            aggregated.values(),
            key=lambda item: (stage_order[item["stage"]], item["name"]),
        )

    def actual_development_skill_usage(
        self,
        registry: dict[str, Any],
        entry: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return Task result Skill usage for this work item subtree."""

        by_id = {
            candidate["id"]: candidate
            for candidate in registry["workItems"]
        }
        records: list[dict[str, Any]] = []

        def visit(current: dict[str, Any], path: set[str]) -> None:
            if current["id"] in path:
                fail(
                    "WORK_ITEM_HIERARCHY_CYCLE",
                    "Work item hierarchy contains a cycle",
                )
            next_path = path | {current["id"]}
            if current["kind"] == "TASK":
                result = current.get("latestResult")
                artifact = (
                    result.get("artifact")
                    if isinstance(result, dict)
                    else None
                )
                skill_usage = (
                    artifact.get("skillUsage")
                    if isinstance(artifact, dict)
                    else None
                )
                if isinstance(skill_usage, list) and skill_usage:
                    definition = self.read_package(
                        registry,
                        current,
                    )[0]
                    records.append({
                        "taskId": current["id"],
                        "taskTitle": definition["title"],
                        "operationId": artifact["operationId"],
                        "resultStatus": artifact["status"],
                        "recordedAt": result["recordedAt"],
                        "resultEvidence": deepcopy(result["evidence"]),
                        "skillUsage": deepcopy(skill_usage),
                    })
            for child_id in current["childIds"]:
                child = by_id.get(child_id)
                if child is None:
                    fail(
                        "WORK_ITEM_HIERARCHY_INVALID",
                        f"Work item child is missing: {child_id}",
                    )
                visit(child, next_path)

        visit(entry, set())
        return records

    @staticmethod
    def _stored_evidence_error(
        entry: dict[str, Any],
        record_kind: str,
        reason: str,
    ) -> None:
        fail(
            "WORK_ITEM_STORED_EVIDENCE_INVALID",
            (
                f"Stored {record_kind} evidence is invalid for "
                f"{entry['id']}: {reason}"
            ),
            itemId=entry["id"],
            recordKind=record_kind,
            reason=reason,
        )

    def _validated_stored_artifact(
        self,
        entry: dict[str, Any],
        record: object,
        *,
        record_kind: str,
        expected_node_id: str,
        bound_artifacts: dict[str, set[tuple[str, str, str]]],
    ) -> dict[str, Any]:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("artifact"), dict)
            or not valid_evidence_record(record.get("evidence"))
        ):
            self._stored_evidence_error(
                entry,
                record_kind,
                "the evidence record or artifact is missing",
            )
        artifact = record["artifact"]
        actual_reference = evidence_record(artifact)
        if record["evidence"] != actual_reference:
            self._stored_evidence_error(
                entry,
                record_kind,
                "the saved evidence hash does not match the artifact",
            )
        artifact_json = canonical_json(artifact)
        matching_bindings = bound_artifacts.get(
            actual_reference["sha256"],
            set(),
        )
        if not any(
            bound_json == artifact_json
            and node_id == expected_node_id
            and (
                "recordedAt" not in record
                or recorded_at == record["recordedAt"]
            )
            for bound_json, node_id, recorded_at in matching_bindings
        ):
            self._stored_evidence_error(
                entry,
                record_kind,
                "the artifact is not bound to the current graph evidence",
            )
        return artifact

    def validate_stored_evidence(
        self,
        registry: dict[str, Any],
    ) -> None:
        """Strictly revalidate current evidence artifacts during recovery."""

        from .skill_execution import assert_required_skill_conformance

        by_id = {
            entry["id"]: entry
            for entry in registry["workItems"]
        }

        def root_id(entry: dict[str, Any]) -> str:
            current = entry
            visited: set[str] = set()
            while current["parentId"] is not None:
                if (
                    current["id"] in visited
                    or current["parentId"] not in by_id
                ):
                    fail(
                        "WORK_ITEM_HIERARCHY_INVALID",
                        "Work item hierarchy is invalid",
                    )
                visited.add(current["id"])
                current = by_id[current["parentId"]]
            return current["id"]

        bound_by_root: dict[
            str,
            dict[str, set[tuple[str, str, str]]],
        ] = {}
        for entry in registry["workItems"]:
            if entry["parentId"] is not None:
                continue
            bound: dict[str, set[tuple[str, str, str]]] = {}
            for record in self.read_graph_evidence(entry["id"]):
                bound_artifact = record["boundArtifact"]
                artifact = bound_artifact["artifact"]
                artifact_sha256 = bound_artifact["binding"][
                    "artifactSha256"
                ]
                bound.setdefault(artifact_sha256, set()).add((
                    canonical_json(artifact),
                    bound_artifact["binding"]["nodeId"],
                    record["recordedAt"],
                ))
            bound_by_root[entry["id"]] = bound

        for entry in registry["workItems"]:
            if entry["id"] in self._isolated_entry_ids:
                continue
            definition = self.assert_current_lineage(registry, entry)[0]
            bound_artifacts = bound_by_root[root_id(entry)]

            latest_result = entry.get("latestResult")
            if latest_result is not None:
                artifact = self._validated_stored_artifact(
                    entry,
                    latest_result,
                    record_kind="Task result",
                    expected_node_id=execution_node_id(entry["id"]),
                    bound_artifacts=bound_artifacts,
                )
                status = artifact.get("status")
                if (
                    entry["kind"] != "TASK"
                    or status not in {"IMPLEMENTED", "BLOCKED"}
                    or not valid_task_result_artifact(
                        artifact,
                        item_id=entry["id"],
                        operation_id=artifact.get("operationId"),
                        status=status,
                        required_skills=self.effective_required_skills(
                            registry,
                            entry,
                            stage="DEVELOPMENT",
                        ),
                    )
                ):
                    self._stored_evidence_error(
                        entry,
                        "Task result",
                        (
                            "the artifact does not match the frozen "
                            "DEVELOPMENT Skill contract"
                        ),
                    )
                try:
                    assert_required_skill_conformance(
                        self,
                        registry,
                        entry,
                        stage="DEVELOPMENT",
                        skill_usage=artifact.get("skillUsage", []),
                        operation_id=artifact.get("operationId"),
                        require_pass=status == "IMPLEMENTED",
                    )
                except GatedLoopError as error:
                    self._stored_evidence_error(
                        entry,
                        "Task result",
                        (
                            "the native Skill activation or conformance "
                            f"evidence is invalid: {error.code}"
                        ),
                    )

            gate = entry["gate"]
            if gate["status"] in {"PASS", "FAIL"}:
                artifact = self._validated_stored_artifact(
                    entry,
                    gate,
                    record_kind="gate",
                    expected_node_id=gate_node_id(entry["id"]),
                    bound_artifacts=bound_artifacts,
                )
                additional_planned_files: set[str] = set()
                if entry["kind"] == "TASK":
                    frozen_files = {
                        item["path"]
                        for item in definition["developmentPlan"].get(
                            "fileChanges",
                            [],
                        )
                    }
                    effective_files = {
                        item["path"]
                        for item in self.effective_task_file_changes(
                            definition
                        )
                    }
                    additional_planned_files = (
                        effective_files - frozen_files
                    )
                if (
                    artifact.get("verdict") != gate["status"]
                    or not valid_gate_artifact(
                        artifact,
                        entry,
                        definition,
                        additional_planned_files=additional_planned_files,
                        required_skills=self.effective_required_skills(
                            registry,
                            entry,
                            stage="GATE",
                        ),
                    )
                ):
                    self._stored_evidence_error(
                        entry,
                        "gate",
                        (
                            "the artifact does not match the frozen GATE "
                            "Skill contract"
                        ),
                    )
                try:
                    assert_required_skill_conformance(
                        self,
                        registry,
                        entry,
                        stage="GATE",
                        skill_usage=artifact.get("skillUsage", []),
                        require_pass=gate["status"] == "PASS",
                    )
                except GatedLoopError as error:
                    self._stored_evidence_error(
                        entry,
                        "gate",
                        (
                            "the native Skill activation or conformance "
                            f"evidence is invalid: {error.code}"
                        ),
                    )

            acceptance = entry.get("acceptance")
            if not isinstance(acceptance, dict):
                continue
            required_review_skills = self.effective_required_skills(
                registry,
                entry,
                stage="FINAL_REVIEW",
            )
            review = acceptance.get("review")
            if review is not None:
                artifact = self._validated_stored_artifact(
                    entry,
                    review,
                    record_kind="review",
                    expected_node_id=review_node_id(entry["id"]),
                    bound_artifacts=bound_artifacts,
                )
                action = review.get("action")
                if (
                    action not in {
                        "INDEPENDENT_REVIEW_PASS",
                        "HUMAN_REVIEW_ACCEPTED",
                        "REVIEW_BLOCKED",
                    }
                    or (
                        action == "HUMAN_REVIEW_ACCEPTED"
                        and required_review_skills
                    )
                    or not valid_review_artifact(
                        action,
                        artifact,
                        required_skills=(
                            required_review_skills
                            if action in {
                                "INDEPENDENT_REVIEW_PASS",
                                "REVIEW_BLOCKED",
                            }
                            else None
                        ),
                    )
                ):
                    self._stored_evidence_error(
                        entry,
                        "review",
                        (
                            "the artifact does not match the frozen "
                            "FINAL_REVIEW Skill contract"
                        ),
                    )
                if action in {
                    "INDEPENDENT_REVIEW_PASS",
                    "REVIEW_BLOCKED",
                }:
                    try:
                        assert_required_skill_conformance(
                            self,
                            registry,
                            entry,
                            stage="FINAL_REVIEW",
                            skill_usage=artifact.get("skillUsage", []),
                            require_pass=(
                                action == "INDEPENDENT_REVIEW_PASS"
                            ),
                        )
                    except GatedLoopError as error:
                        self._stored_evidence_error(
                            entry,
                            "review",
                            (
                                "the native Skill activation or conformance "
                                f"evidence is invalid: {error.code}"
                            ),
                        )
            confirmation = acceptance.get("userConfirmation")
            if confirmation is not None:
                artifact = self._validated_stored_artifact(
                    entry,
                    confirmation,
                    record_kind="user confirmation",
                    expected_node_id=confirmation_node_id(entry["id"]),
                    bound_artifacts=bound_artifacts,
                )
                if (
                    confirmation.get("action") != "USER_CONFIRMED"
                    or not valid_review_artifact(
                        "USER_CONFIRMED",
                        artifact,
                    )
                ):
                    self._stored_evidence_error(
                        entry,
                        "user confirmation",
                        "the artifact does not match the confirmation contract",
                    )

    def _write_interaction_logs(self, registry: dict[str, Any]) -> None:
        by_id = {item["id"]: item for item in registry["workItems"]}

        def tree_ids(entry: dict[str, Any]) -> list[str]:
            result = [entry["id"]]
            for child_id in entry["childIds"]:
                result.extend(tree_ids(by_id[child_id]))
            return result

        for root in (item for item in registry["workItems"] if item["parentId"] is None):
            events = self.read_interaction_events(tree_ids(root))
            atomic_write(
                self.item_path(root) / "interaction-log.md",
                render_interaction_log(root, events),
                durable=False,
            )

    def refresh_interaction_logs(self, registry: dict[str, Any]) -> None:
        self._write_interaction_logs(registry)

    def refresh_interaction_projection(
        self,
        registry: dict[str, Any],
        root_id: str,
    ) -> None:
        by_id = {item["id"]: item for item in registry["workItems"]}
        root = by_id.get(root_id)
        if root is None or root["parentId"] is not None:
            fail(
                "WORK_ITEM_HIERARCHY_INVALID",
                "Interaction projection requires a requirement root",
            )

        def tree_ids(entry: dict[str, Any]) -> list[str]:
            result = [entry["id"]]
            for child_id in entry["childIds"]:
                result.extend(tree_ids(by_id[child_id]))
            return result

        events = self.read_interaction_events(tree_ids(root))
        atomic_write(
            self.item_path(root) / "interaction-log.md",
            render_interaction_log(root, events),
            durable=False,
        )

    def write_task_context(
        self,
        entry: dict[str, Any],
        context: dict[str, Any],
        handoff: str,
        at: str,
    ) -> None:
        self._active_connection().execute(
            "INSERT INTO task_contexts(work_item_id, context_json, handoff_markdown, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(work_item_id) DO UPDATE SET "
            "context_json = excluded.context_json, handoff_markdown = excluded.handoff_markdown, "
            "updated_at = excluded.updated_at",
            (entry["id"], canonical_json(context), handoff, at),
        )

    def _graph_projection_snapshot(
        self,
        registry: dict[str, Any],
        root: dict[str, Any],
        stored_graph: dict[str, Any],
        graph_run: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        from .graph_runtime import (
            build_graph_frontier,
            critical_path,
            derive_node_states,
            replay_graph_events,
            replay_mismatches,
        )

        graph_events = self.read_graph_events(root["id"])
        replay = (
            replay_graph_events(stored_graph["graph"], graph_run, graph_events)
            if graph_run is not None
            else None
        )
        if replay is not None:
            mismatches = replay_mismatches(replay, graph_run)
            if mismatches:
                fail(
                    "DELIVERY_GRAPH_REPLAY_MISMATCH",
                    "Cannot project graph snapshots that differ from event replay",
                    mismatches=mismatches,
                )
            graph_nodes = [
                {
                    "id": node["nodeId"],
                    **{key: value for key, value in node.items() if key != "nodeId"},
                }
                for node in replay["nodes"]
            ]
        else:
            graph_nodes = [
                {
                    **state,
                    "attempt": None,
                    "owner": None,
                    "operationId": None,
                    "claimedAt": None,
                    "finishedAt": None,
                    "latestEvidenceHash": None,
                    "leaseExpiresAt": None,
                    "lastHeartbeatAt": None,
                    "failureClass": None,
                    "lastTransition": None,
                    "retryExhausted": False,
                    "recordRevision": None,
                }
                for state in derive_node_states(stored_graph["graph"], registry)
            ]
        graph_status = {
            "rootId": root["id"],
            "graphFingerprint": stored_graph["graphFingerprint"],
            "run": graph_run,
            "nodes": graph_nodes,
            "criticalPath": critical_path(stored_graph["graph"], graph_nodes),
        }
        frontier = build_graph_frontier(
            self,
            registry,
            root,
            stored_graph,
            graph_run,
            graph_nodes,
        )
        return graph_events, graph_status, frontier

    def refresh_markdown_projections(self, registry: dict[str, Any]) -> None:
        """Rebuild every human artifact that has a complete SQLite source."""
        runtime_policy = compile_runtime_policy()
        atomic_write(
            self.governance_root / "state-transition-graph.md",
            render_state_transition_graph(runtime_policy),
            durable=False,
        )
        for relative_path, contents in render_runtime_policy_svg_assets(
            runtime_policy
        ).items():
            atomic_write(
                self.governance_root / relative_path,
                contents,
                durable=False,
            )

        by_id = {item["id"]: item for item in registry["workItems"]}
        definitions: dict[str, dict[str, Any]] = {}
        states: dict[str, dict[str, Any]] = {}
        for entry in registry["workItems"]:
            definition, state, _ = self.read_package(registry, entry)
            definitions[entry["id"]] = definition
            states[entry["id"]] = state

        def build(entry: dict[str, Any]) -> dict[str, Any]:
            return {
                "definition": raw_definition(definitions[entry["id"]]),
                "children": [build(by_id[child_id]) for child_id in entry["childIds"]],
            }

        for root in (item for item in registry["workItems"] if item["parentId"] is None):
            hierarchy = validate_hierarchy_definition({
                "schemaVersion": SCHEMA_VERSION,
                "root": build(root),
            })
            hierarchy_state = self.read_hierarchy_state(root["id"])
            stored_graph = self.read_graph_definition(root["id"])
            root_plan = (
                render_hierarchy_plan(hierarchy, states, hierarchy_state)
                + "\n"
                + render_runtime_policy_summary(stored_graph["graph"])
            )

            def project(entry: dict[str, Any], *, is_root: bool) -> None:
                target = self.item_path(entry)
                atomic_write(
                    target / "baseline.md",
                    render_work_item_baseline(definitions[entry["id"]]),
                    durable=False,
                )
                atomic_write(
                    target / "development-plan.md",
                    root_plan if is_root else render_development_plan(definitions[entry["id"]], states[entry["id"]]),
                    durable=False,
                )
                for child_id in entry["childIds"]:
                    project(by_id[child_id], is_root=False)

            project(root, is_root=True)
            graph_run = self.read_graph_run(root["id"], allow_missing=True)
            atomic_write(
                self.item_path(root) / "execution-graph.md",
                render_delivery_graph(
                    stored_graph["graph"],
                    graph_fingerprint=stored_graph["graphFingerprint"],
                    run=graph_run,
                ),
                durable=False,
            )
            for relative_path, contents in render_delivery_graph_svg_assets(
                stored_graph["graph"]
            ).items():
                atomic_write(
                    self.item_path(root) / relative_path,
                    contents,
                    durable=False,
                )
            for relative_path in (
                "state-transition-graph.md",
                "assets/development-flow.svg",
                "assets/node-state-machine.svg",
            ):
                legacy_projection = safe_path(self.item_path(root), relative_path)
                try:
                    read_regular_file(self.item_path(root), legacy_projection)
                except FileNotFoundError:
                    continue
                legacy_projection.unlink()
            graph_events, graph_status, frontier = self._graph_projection_snapshot(
                registry,
                root,
                stored_graph,
                graph_run,
            )
            atomic_write(
                self.item_path(root) / "run-timeline.md",
                render_run_timeline(graph_status, graph_events),
                durable=False,
            )
            atomic_write(
                self.item_path(root) / "frontier.md",
                render_frontier_dashboard(graph_status, frontier),
                durable=False,
            )

        with self._read_connection() as connection:
            context_rows = connection.execute(
                "SELECT work_item_id, context_json, handoff_markdown FROM task_contexts"
            ).fetchall()
            report_rows = connection.execute(
                "SELECT work_item_id, report_kind, report_json FROM reports"
            ).fetchall()
        for row in context_rows:
            entry = by_id.get(row["work_item_id"])
            if entry is not None:
                atomic_write(
                    self.item_path(entry) / "development-handoff.md",
                    row["handoff_markdown"],
                    durable=False,
                )
        for row in report_rows:
            entry = by_id.get(row["work_item_id"])
            if entry is None:
                continue
            try:
                report = json.loads(row["report_json"])
            except (TypeError, json.JSONDecodeError):
                fail("WORK_ITEM_REPORT_INVALID", f"Stored report is invalid: {row['work_item_id']}")
            if row["report_kind"] == "DEVELOPMENT_REVIEW":
                atomic_write(
                    self.item_path(entry) / "development-review.md",
                    render_development_review(report),
                    durable=False,
                )
            elif row["report_kind"] == "ACCEPTANCE":
                atomic_write(
                    self.item_path(entry) / "acceptance-report.md",
                    render_acceptance_report(report),
                    durable=False,
                )
            else:
                fail("WORK_ITEM_REPORT_INVALID", f"Unknown stored report kind: {row['report_kind']}")

    def refresh_heartbeat_projections(
        self,
        registry: dict[str, Any],
        root_id: str,
    ) -> None:
        by_id = {item["id"]: item for item in registry["workItems"]}
        root = by_id.get(root_id)
        if root is None or root["parentId"] is not None:
            fail(
                "WORK_ITEM_HIERARCHY_INVALID",
                "Heartbeat projection requires a requirement root",
            )
        stored_graph = self.read_graph_definition(root_id)
        graph_run = self.read_graph_run(root_id, allow_missing=True)
        graph_events, graph_status, frontier = self._graph_projection_snapshot(
            registry,
            root,
            stored_graph,
            graph_run,
        )
        atomic_write(
            self.item_path(root) / "execution-graph.md",
            render_delivery_graph(
                stored_graph["graph"],
                graph_fingerprint=stored_graph["graphFingerprint"],
                run=graph_run,
            ),
            durable=False,
        )
        atomic_write(
            self.item_path(root) / "run-timeline.md",
            render_run_timeline(graph_status, graph_events),
            durable=False,
        )
        atomic_write(
            self.item_path(root) / "frontier.md",
            render_frontier_dashboard(graph_status, frontier),
            durable=False,
        )

    def write_registry(
        self,
        registry: dict[str, Any],
        *,
        changed_item_ids: set[str] | None = None,
        projection_mode: str = "full",
        projection_root_id: str | None = None,
    ) -> None:
        self.recompute_progress(registry)
        self.validate_operational_registry(registry)
        registry["workItems"] = sorted(registry["workItems"], key=lambda item: item["id"])
        by_id = {item["id"]: item for item in registry["workItems"]}
        connection = self._active_connection()
        previous = connection.execute(
            "SELECT revision FROM workspace WHERE singleton = 1"
        ).fetchone()
        previous_revision = previous["revision"] if previous else None
        current_ids = set(by_id)
        stored_entries = {
            row["id"]: row["entry_json"]
            for row in connection.execute("SELECT id, entry_json FROM work_items")
        }
        stored_ids = set(stored_entries)
        if changed_item_ids is None:
            for stale_id in stored_ids - current_ids:
                connection.execute("DELETE FROM task_contexts WHERE work_item_id = ?", (stale_id,))
                connection.execute("DELETE FROM reports WHERE work_item_id = ?", (stale_id,))
                connection.execute("DELETE FROM work_items WHERE id = ?", (stale_id,))
            root_ids = {item["id"] for item in registry["workItems"] if item["parentId"] is None}
            for row in connection.execute("SELECT root_id FROM hierarchies").fetchall():
                if row["root_id"] not in root_ids:
                    connection.execute("DELETE FROM hierarchies WHERE root_id = ?", (row["root_id"],))
            for row in connection.execute("SELECT root_id FROM graph_definitions").fetchall():
                if row["root_id"] not in root_ids:
                    connection.execute("DELETE FROM graph_definitions WHERE root_id = ?", (row["root_id"],))
            candidate_ids = current_ids
        else:
            if current_ids != stored_ids or not changed_item_ids <= current_ids:
                fail(
                    "WORK_ITEM_INCREMENTAL_WRITE_INVALID",
                    "Incremental registry writes require an unchanged work-item set",
                )
            candidate_ids = changed_item_ids
        rows_updated = 0
        bytes_written = 0
        for item_id in sorted(candidate_ids):
            if item_id in self._isolated_entry_ids:
                continue
            serialized = canonical_json(by_id[item_id])
            if stored_entries.get(item_id) == serialized:
                continue
            cursor = connection.execute(
                "UPDATE work_items SET entry_json = ? WHERE id = ?",
                (serialized, item_id),
            )
            if cursor.rowcount != 1:
                fail("WORK_ITEM_PACKAGE_INVALID", f"{item_id} has no stored definition")
            rows_updated += 1
            bytes_written += len(serialized.encode("utf-8"))
        timing_metric("registryRowsConsidered", len(candidate_ids))
        timing_metric("registryRowsUpdated", rows_updated)
        timing_metric("registryRowsSkipped", len(candidate_ids) - rows_updated)
        timing_metric("registryBytesWritten", bytes_written)
        connection.execute(
            "INSERT INTO workspace(singleton, schema_version, coordination_root, revision, current_focus_json, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?) ON CONFLICT(singleton) DO UPDATE SET "
            "schema_version = excluded.schema_version, coordination_root = excluded.coordination_root, "
            "revision = excluded.revision, current_focus_json = excluded.current_focus_json, "
            "updated_at = excluded.updated_at",
            (
                registry["schemaVersion"],
                registry["coordinationRoot"],
                registry["revision"],
                canonical_json(registry["currentFocus"]),
                registry["updatedAt"],
            ),
        )
        focus = registry["currentFocus"]
        if previous_revision != registry["revision"] and focus["workItemId"] is not None:
            focused = by_id[focus["workItemId"]]
            state = connection.execute(
                "SELECT state_json FROM work_items WHERE id = ?",
                (focused["id"],),
            ).fetchone()
            host_runtime = None
            if state:
                try:
                    host_runtime = json.loads(state["state_json"]).get("hostRuntime")
                except (TypeError, json.JSONDecodeError):
                    pass
            self.append_interaction_event(
                work_item_id=focused["id"],
                session_id="controller",
                actor="AGENT",
                event_type=focus["purpose"],
                summary=self._automatic_event_summary(focus["purpose"]),
                operation_id=(focused.get("claim") or {}).get("operationId"),
                host_runtime=host_runtime,
                payload={"status": focused["status"], "stage": focused["stage"]},
                registry_revision=registry["revision"],
                recorded_at=registry["updatedAt"],
            )
        if projection_mode == "heartbeat" and projection_root_id is not None:
            graph_root_ids: set[str] | None = {projection_root_id}
        elif projection_mode == "interaction":
            graph_root_ids = set()
        else:
            graph_root_ids = None
        self.sync_graph_runs(registry, root_ids=graph_root_ids)
        self.schedule_projection(
            registry,
            mode=projection_mode,
            root_id=projection_root_id,
        )

    def refresh_registry_projections(self, registry: dict[str, Any]) -> None:
        self.refresh_markdown_projections(registry)
        by_id = {item["id"]: item for item in registry["workItems"]}
        atomic_write(
            self.governance_root / "workspace-overview.md",
            render_workspace_overview(
                registry,
                isolated_item_ids=self._isolated_entry_ids,
            ),
            durable=False,
        )
        monthly_overviews = render_workspace_month_overviews(registry)
        monthly_root = self.governance_root / "workspace-overview"
        if monthly_root.exists() and (
            not monthly_root.is_dir() or monthly_root.is_symlink()
        ):
            fail(
                "WORKSPACE_OVERVIEW_DIRECTORY_INVALID",
                "Monthly workspace overview path must be a regular directory",
            )

        def populate_monthly_overviews(staging: Path) -> None:
            for relative_path, content in monthly_overviews.items():
                atomic_write(staging / relative_path, content, durable=False)

        atomic_replace_directory(monthly_root, populate_monthly_overviews)
        for entry in registry["workItems"]:
            target = self.item_path(entry)
            if not target.exists():
                continue
            if not target.is_dir() or target.is_symlink():
                fail("WORK_ITEM_PACKAGE_INVALID", f"{entry['id']} package path is invalid")
            atomic_write(
                target / "overview.md",
                render_item_overview(entry, by_id),
                durable=False,
            )
            if entry["parentId"] is None:
                atomic_write(
                    target / "node-progress.md",
                    render_item_progress(entry, by_id),
                    durable=False,
                )
                atomic_write(
                    target / "progress.md",
                    render_item_progress(entry, by_id, include_hierarchy=True),
                    durable=False,
                )
            else:
                atomic_write(
                    target / "progress.md",
                    render_item_progress(entry, by_id),
                    durable=False,
                )
            if (
                entry["parentId"] is None
                and entry["stage"] == "BASELINE_FROZEN"
                and (entry.get("developmentMode") or {}).get("mode") == "manual"
            ):
                atomic_write(
                    target / "requirement-handoff.md",
                    render_requirement_handoff(entry, by_id),
                    durable=False,
                )
        self._write_interaction_logs(registry)

    def write_acceptance_report(
        self,
        registry: dict[str, Any],
        entry: dict[str, Any],
        definition: dict[str, Any],
        at: str,
    ) -> dict[str, Any]:
        from .skill_execution import skill_execution_audit

        acceptance = entry.get("acceptance") if entry["parentId"] is None else None
        report = {
            "schemaVersion": SCHEMA_VERSION,
            "workItem": {
                "id": entry["id"],
                "title": definition["title"],
                "kind": entry["kind"],
                "gateLevel": entry["gateLevel"],
                "baselineFingerprint": entry["baselineFingerprint"],
                "parentId": entry["parentId"],
            },
            "status": report_status(entry),
            "development": entry.get("latestResult"),
            "developmentSkillUsage": (
                self.actual_development_skill_usage(
                    registry,
                    entry,
                )
            ),
            "skillExecutionAudit": skill_execution_audit(
                self,
                registry,
                entry,
            ),
            "gate": entry["gate"],
            "criteria": definition["acceptance"],
            "developmentPlan": definition["developmentPlan"],
            "validationRemediations": self.read_validation_remediations(entry["id"], definition)
            if entry["kind"] == "TASK"
            else [],
            "review": acceptance.get("review") if acceptance else None,
            "userConfirmation": acceptance.get("userConfirmation") if acceptance else None,
            "generatedAt": at,
        }
        self._active_connection().execute(
            "INSERT INTO reports(work_item_id, report_kind, report_json, generated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(work_item_id, report_kind) DO UPDATE SET "
            "report_json = excluded.report_json, generated_at = excluded.generated_at",
            (entry["id"], "ACCEPTANCE", canonical_json(report), at),
        )
        base = f"{GOVERNANCE_DIRECTORY}/{entry['packagePath']}"
        entry["acceptanceReport"] = {
            "schemaVersion": SCHEMA_VERSION,
            "status": report["status"],
            "markdownPath": f"{base}/acceptance-report.md",
            "generatedAt": at,
        }
        return report

    def write_development_review(
        self,
        registry: dict[str, Any],
        entry: dict[str, Any],
        definition: dict[str, Any],
        at: str,
    ) -> dict[str, Any]:
        from .skill_execution import skill_execution_audit

        report = {
            "schemaVersion": SCHEMA_VERSION,
            "workItem": {
                "id": entry["id"],
                "title": definition["title"],
                "kind": entry["kind"],
                "gateLevel": entry["gateLevel"],
                "baselineFingerprint": entry["baselineFingerprint"],
                "parentId": entry["parentId"],
            },
            "status": entry["status"],
            "developmentPlan": definition["developmentPlan"],
            "validationRemediations": self.read_validation_remediations(entry["id"], definition),
            "result": entry.get("latestResult"),
            "skillExecutionAudit": skill_execution_audit(
                self,
                registry,
                entry,
            ),
            "generatedAt": at,
        }
        self._active_connection().execute(
            "INSERT INTO reports(work_item_id, report_kind, report_json, generated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(work_item_id, report_kind) DO UPDATE SET "
            "report_json = excluded.report_json, generated_at = excluded.generated_at",
            (entry["id"], "DEVELOPMENT_REVIEW", canonical_json(report), at),
        )
        return report


def entry_from_definition(
    definition: dict[str, Any],
    state: dict[str, Any],
    at: str,
    *,
    package_path: str | None = None,
) -> dict[str, Any]:
    return {
        "id": definition["id"],
        "kind": definition["kind"],
        "gateLevel": definition["gateLevel"],
        "authorityKind": definition["authorityKind"],
        "parentId": definition["parentId"],
        "childIds": [item["id"] for item in definition.get("children", [])],
        "packagePath": package_path or f"{WORK_ITEMS_DIRECTORY}/{definition['id']}",
        "developmentPlan": True,
        "stage": state["stage"],
        "status": "PREPARED",
        "baselineFingerprint": state["baselineFingerprint"],
        "contractFingerprint": state["contractFingerprint"],
        "parentContractFingerprint": state["parentContractFingerprint"],
        "gate": {"status": "NOT_RUN", "evidence": None},
        "acceptance": {"status": "NOT_READY", "review": None, "userConfirmation": None}
        if definition["parentId"] is None
        else None,
        "acceptanceReport": None,
        "developmentMode": None,
        "claim": None,
        "latestEvidence": None,
        "latestResult": None,
        "recordRevision": 1,
        "createdAt": at,
        "updatedAt": at,
    }
