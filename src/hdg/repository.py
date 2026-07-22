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
    valid_validation_remediation_artifact,
    valid_timestamp,
)
from .fs_safe import atomic_create_directory, atomic_replace_directory, atomic_write, read_regular_file, safe_path
from .graph_model import graph_fingerprint, validate_delivery_graph
from .graph_projections import (
    render_delivery_graph,
    render_frontier_dashboard,
    render_runtime_policy_summary,
    render_run_timeline,
    render_state_transition_graph,
)
from .svg_graphs import render_graph_svg_assets
from .host_runtime import is_agent_runtime
from .jsonio import canonical_json, sha256_bytes
from .model import (
    WORK_ITEM_AUTHORITIES,
    WORK_ITEM_GATE_LEVELS,
    WORK_ITEM_KINDS,
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


WORK_ITEM_DATABASE_FILE = "governance.sqlite3"
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
}
DATABASE_COLUMN_CONTRACTS = {
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
        pyproject = self.root / "pyproject.toml"
        try:
            text = read_regular_file(self.root, pyproject).decode("utf-8")
            project_match = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", text)
            name_match = re.search(r'(?m)^name\s*=\s*["\']([^"\']+)["\']\s*$', project_match.group(1) if project_match else "")
            if name_match:
                project_name = name_match.group(1)
        except (FileNotFoundError, UnicodeDecodeError):
            pass
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
        if not create and not self.database_path.is_file():
            fail("WORK_ITEM_DATABASE_MISSING", "Governance database does not exist")
        connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
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

    def _active_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            fail("WORK_ITEM_TRANSACTION_REQUIRED", "This operation requires an active governance transaction")
        return self._connection

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

    def is_item_isolated(self, item_id: str) -> bool:
        return item_id in self._isolated_entry_ids

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
        return self.read_registry(
            allow_missing=allow_missing,
            isolate_historical_evidence=True,
        )

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
        connection = self._connect(create=True)
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._initialize_database(connection, create=True)
            self._connection = connection
            registry = self.read_operational_registry(allow_missing=True)
            self._transaction_isolated_entry_ids = set(self._isolated_entry_ids)
            self._transaction_isolated_snapshots = {
                entry["id"]: canonical_json(entry)
                for entry in registry["workItems"]
                if entry["id"] in self._transaction_isolated_entry_ids
            }
            yield registry
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self._transaction_isolated_entry_ids = set()
            self._transaction_isolated_snapshots = {}
            self._connection = None
            connection.close()

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

    def read_graph_events(self, root_id: str) -> list[dict[str, Any]]:
        run = self.read_graph_run(root_id, allow_missing=True)
        if run is None:
            return []
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT event_id, event_uuid, run_id, graph_fingerprint, node_id, attempt, event_type, actor, "
                "operation_id, payload_json, recorded_at, previous_hash, event_hash "
                "FROM graph_events WHERE run_id = ? ORDER BY event_id",
                (run["runId"],),
            ).fetchall()
        result = []
        previous_hash = None
        evidence_bindings = {
            record["boundEvidenceSha256"]: record["boundArtifact"]["binding"]
            for record in self.read_graph_evidence(root_id)
        }
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                fail("DELIVERY_GRAPH_EVENT_INVALID", "Stored graph event payload is invalid")
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
            expected_hash = sha256_bytes(canonical_json(hash_payload).encode("utf-8"))
            binding = payload.get("evidenceBinding") if isinstance(payload, dict) else None
            binding_valid = (
                binding is None
                or (
                    isinstance(binding, dict)
                    and binding.get("runId") == run["runId"]
                    and binding.get("graphFingerprint") == run["graphFingerprint"]
                    and binding.get("nodeId") == row["node_id"]
                    and binding.get("attempt") == row["attempt"]
                    and binding
                    == evidence_bindings.get(binding.get("boundEvidenceSha256"))
                )
            )
            if (
                row["graph_fingerprint"] != run["graphFingerprint"]
                or row["previous_hash"] != previous_hash
                or row["event_hash"] != expected_hash
                or not binding_valid
            ):
                fail("DELIVERY_GRAPH_EVENT_INVALID", "Stored graph event chain is invalid")
            previous_hash = row["event_hash"]
            result.append({
                "eventId": row["event_id"],
                **hash_payload,
                "eventHash": row["event_hash"],
            })
        return result

    def sync_graph_runs(self, registry: dict[str, Any]) -> None:
        from .graph_runtime import replay_graph_events

        connection = self._active_connection()
        roots_with_runs = [
            row["root_id"]
            for row in connection.execute("SELECT root_id FROM graph_runs ORDER BY root_id")
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

    def read_interaction_events(self, item_ids: list[str] | None = None) -> list[dict[str, Any]]:
        where = ""
        parameters: tuple[Any, ...] = ()
        if item_ids is not None:
            if not item_ids:
                return []
            where = f" WHERE work_item_id IN ({','.join('?' for _ in item_ids)})"
            parameters = tuple(item_ids)
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT event_id, event_uuid, work_item_id, session_id, actor, event_type, summary, "
                "operation_id, host_runtime, payload_json, registry_revision, recorded_at, "
                "previous_hash, event_hash FROM interaction_events"
                f"{where} ORDER BY event_id",
                parameters,
            ).fetchall()
        result = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                fail("WORK_ITEM_INTERACTION_INVALID", "Stored interaction payload is invalid")
            result.append({
                "eventId": row["event_id"],
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
                "eventHash": row["event_hash"],
            })
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

    def _write_interaction_logs(self, registry: dict[str, Any]) -> None:
        by_id = {item["id"]: item for item in registry["workItems"]}

        def tree_ids(entry: dict[str, Any]) -> list[str]:
            result = [entry["id"]]
            for child_id in entry["childIds"]:
                result.extend(tree_ids(by_id[child_id]))
            return result

        for root in (item for item in registry["workItems"] if item["parentId"] is None):
            events = self.read_interaction_events(tree_ids(root))
            atomic_write(self.item_path(root) / "interaction-log.md", render_interaction_log(root, events))

    def refresh_interaction_logs(self, registry: dict[str, Any]) -> None:
        self._write_interaction_logs(registry)

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

    def refresh_markdown_projections(self, registry: dict[str, Any]) -> None:
        """Rebuild every human artifact that has a complete SQLite source."""
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
                atomic_write(target / "baseline.md", render_work_item_baseline(definitions[entry["id"]]))
                atomic_write(
                    target / "development-plan.md",
                    root_plan if is_root else render_development_plan(definitions[entry["id"]], states[entry["id"]]),
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
            )
            atomic_write(
                self.item_path(root) / "state-transition-graph.md",
                render_state_transition_graph(
                    stored_graph["graph"],
                    graph_fingerprint=stored_graph["graphFingerprint"],
                ),
            )
            for relative_path, contents in render_graph_svg_assets(stored_graph["graph"]).items():
                atomic_write(self.item_path(root) / relative_path, contents)
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
            atomic_write(
                self.item_path(root) / "run-timeline.md",
                render_run_timeline(graph_status, graph_events),
            )
            atomic_write(
                self.item_path(root) / "frontier.md",
                render_frontier_dashboard(graph_status, frontier),
            )

        connection = self._active_connection()
        for row in connection.execute(
            "SELECT work_item_id, context_json, handoff_markdown FROM task_contexts"
        ):
            entry = by_id.get(row["work_item_id"])
            if entry is not None:
                atomic_write(self.item_path(entry) / "development-handoff.md", row["handoff_markdown"])
        for row in connection.execute(
            "SELECT work_item_id, report_kind, report_json FROM reports"
        ):
            entry = by_id.get(row["work_item_id"])
            if entry is None:
                continue
            try:
                report = json.loads(row["report_json"])
            except (TypeError, json.JSONDecodeError):
                fail("WORK_ITEM_REPORT_INVALID", f"Stored report is invalid: {row['work_item_id']}")
            if row["report_kind"] == "DEVELOPMENT_REVIEW":
                atomic_write(self.item_path(entry) / "development-review.md", render_development_review(report))
            elif row["report_kind"] == "ACCEPTANCE":
                atomic_write(self.item_path(entry) / "acceptance-report.md", render_acceptance_report(report))
            else:
                fail("WORK_ITEM_REPORT_INVALID", f"Unknown stored report kind: {row['report_kind']}")

    def write_registry(self, registry: dict[str, Any]) -> None:
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
        stored_ids = {row["id"] for row in connection.execute("SELECT id FROM work_items")}
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
        for entry in registry["workItems"]:
            if entry["id"] in self._isolated_entry_ids:
                continue
            cursor = connection.execute(
                "UPDATE work_items SET entry_json = ? WHERE id = ?",
                (canonical_json(entry), entry["id"]),
            )
            if cursor.rowcount != 1:
                fail("WORK_ITEM_PACKAGE_INVALID", f"{entry['id']} has no stored definition")
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
        self.sync_graph_runs(registry)
        self.refresh_markdown_projections(registry)
        atomic_write(
            self.governance_root / "workspace-overview.md",
            render_workspace_overview(
                registry,
                isolated_item_ids=self._isolated_entry_ids,
            ),
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
                atomic_write(staging / relative_path, content)

        atomic_replace_directory(monthly_root, populate_monthly_overviews)
        for entry in registry["workItems"]:
            target = self.item_path(entry)
            if not target.exists():
                continue
            if not target.is_dir() or target.is_symlink():
                fail("WORK_ITEM_PACKAGE_INVALID", f"{entry['id']} package path is invalid")
            atomic_write(target / "overview.md", render_item_overview(entry, by_id))
            if entry["parentId"] is None:
                atomic_write(
                    target / "node-progress.md",
                    render_item_progress(entry, by_id),
                )
                atomic_write(
                    target / "progress.md",
                    render_item_progress(entry, by_id, include_hierarchy=True),
                )
            else:
                atomic_write(target / "progress.md", render_item_progress(entry, by_id))
            if (
                entry["parentId"] is None
                and entry["stage"] == "BASELINE_FROZEN"
                and (entry.get("developmentMode") or {}).get("mode") == "manual"
            ):
                atomic_write(
                    target / "requirement-handoff.md",
                    render_requirement_handoff(entry, by_id),
                )
        self._write_interaction_logs(registry)

    def write_acceptance_report(
        self,
        entry: dict[str, Any],
        definition: dict[str, Any],
        at: str,
    ) -> dict[str, Any]:
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
        directory = self.item_path(entry)
        self._active_connection().execute(
            "INSERT INTO reports(work_item_id, report_kind, report_json, generated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(work_item_id, report_kind) DO UPDATE SET "
            "report_json = excluded.report_json, generated_at = excluded.generated_at",
            (entry["id"], "ACCEPTANCE", canonical_json(report), at),
        )
        atomic_write(directory / "acceptance-report.md", render_acceptance_report(report))
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
        entry: dict[str, Any],
        definition: dict[str, Any],
        at: str,
    ) -> dict[str, Any]:
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
            "generatedAt": at,
        }
        directory = self.item_path(entry)
        self._active_connection().execute(
            "INSERT INTO reports(work_item_id, report_kind, report_json, generated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(work_item_id, report_kind) DO UPDATE SET "
            "report_json = excluded.report_json, generated_at = excluded.generated_at",
            (entry["id"], "DEVELOPMENT_REVIEW", canonical_json(report), at),
        )
        atomic_write(directory / "development-review.md", render_development_review(report))
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
