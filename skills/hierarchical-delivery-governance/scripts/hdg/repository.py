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

from .constants import SCHEMA_VERSION
from .errors import fail
from .evidence import (
    FINGERPRINT,
    safe_work_item_id,
    valid_acceptance,
    valid_acceptance_report,
    valid_development_mode,
    valid_evidence_reference,
    valid_timestamp,
)
from .fs_safe import atomic_create_directory, atomic_replace_directory, atomic_write, read_regular_file, safe_path
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
    render_workspace_overview,
    report_status,
)


WORK_ITEM_DATABASE_FILE = "governance.sqlite3"
LEGACY_REGISTRY_FILE = "work-item-registry.json"
WORK_ITEMS_DIRECTORY = "work-items"
GOVERNANCE_DIRECTORY = ".hierarchical-delivery-governance"
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
        and valid_evidence_reference(value["evidence"])
        and (value["artifact"] is None or isinstance(value["artifact"], dict))
    )


def _valid_claim(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"owner", "operationId", "claimedAt"}
        and isinstance(value.get("owner"), str)
        and bool(value["owner"])
        and isinstance(value.get("operationId"), str)
        and bool(value["operationId"])
        and valid_timestamp(value.get("claimedAt"))
    )


def _valid_latest_result(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"evidence", "artifact", "recordedAt"}
        and valid_evidence_reference(value.get("evidence"))
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


class GovernanceRepository:
    """Own safe persistence, package integrity and registry projections."""

    def __init__(self, root: str | os.PathLike[str], *, now: object = None) -> None:
        self.root = Path(root).absolute()
        self.now = now
        self._connection: sqlite3.Connection | None = None

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
        )
        for statement in statements:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

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
        for item in registry["workItems"]:
            if item["id"] == item_id:
                return item
        fail("WORK_ITEM_NOT_FOUND", f"Unknown work item: {item_id}", id=item_id)

    def validate_registry(self, registry: object) -> dict[str, Any]:
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

        def hierarchy_root(entry: dict[str, Any]) -> dict[str, Any] | None:
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

        for entry in registry["workItems"]:
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
                    or valid_evidence_reference(entry.get("latestEvidence"))
                )
                and (
                    entry.get("latestResult") is None
                    or _valid_latest_result(entry.get("latestResult"))
                )
            )
            if not valid_entry:
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item registry entry is invalid: {entry['id']}")
            root_entry = hierarchy_root(entry)
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
            if entry["kind"] != "TASK" and entry["status"] in {
                "CLAIMED", "IMPLEMENTED",
            }:
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
            expected_package = (
                f"{WORK_ITEMS_DIRECTORY}/{entry['id']}"
                if entry["parentId"] is None
                else f"{by_id[entry['parentId']]['packagePath']}/children/{entry['id']}"
                if entry["parentId"] in by_id
                else None
            )
            if entry["packagePath"] != expected_package:
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item package path is invalid: {entry['id']}")
            if any(child_id not in by_id for child_id in entry["childIds"]):
                fail("WORK_ITEM_REGISTRY_INVALID", f"Work item hierarchy is not fully materialized: {entry['id']}")
            if entry["kind"] != "DELIVERY" and entry["parentId"] is not None:
                parent = by_id.get(entry["parentId"])
                expected_kind = "DELIVERY" if entry["kind"] == "CAPABILITY" else "CAPABILITY"
                if not parent or parent["kind"] != expected_kind or entry["id"] not in parent["childIds"]:
                    fail("WORK_ITEM_REGISTRY_INVALID", f"Work item parent relation is invalid: {entry['id']}")
        focus_id = registry["currentFocus"].get("workItemId")
        if focus_id is not None and (not safe_work_item_id(focus_id) or focus_id not in by_id):
            fail("WORK_ITEM_REGISTRY_INVALID", "Current focus references an unknown work item")
        focus_purpose = registry["currentFocus"].get("purpose")
        if (focus_id is None) != (focus_purpose is None) or (
            focus_purpose is not None and (not isinstance(focus_purpose, str) or not focus_purpose)
        ):
            fail("WORK_ITEM_REGISTRY_INVALID", "Current focus is invalid")
        return registry

    def read_registry(self, *, allow_missing: bool = False) -> dict[str, Any]:
        if self._connection is None and not self.database_path.is_file():
            if allow_missing:
                return self.empty_registry()
            fail("WORK_ITEM_DATABASE_MISSING", "Governance database does not exist")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT schema_version, coordination_root, revision, current_focus_json, updated_at "
                "FROM workspace WHERE singleton = 1"
            ).fetchone()
            if row is None:
                if allow_missing:
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
            return self.validate_registry(registry)

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        self.ensure_runtime_root()
        connection = self._connect(create=True)
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._initialize_database(connection, create=True)
            self._connection = connection
            yield self.read_registry(allow_missing=True)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
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
            root_plan = render_hierarchy_plan(hierarchy, states, hierarchy_state)

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
        self.validate_registry(registry)
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
        for entry in registry["workItems"]:
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
        self.refresh_markdown_projections(registry)
        atomic_write(self.governance_root / "workspace-overview.md", render_workspace_overview(registry))
        for entry in registry["workItems"]:
            target = self.item_path(entry)
            if not target.exists():
                continue
            if not target.is_dir() or target.is_symlink():
                fail("WORK_ITEM_PACKAGE_INVALID", f"{entry['id']} package path is invalid")
            atomic_write(target / "overview.md", render_item_overview(entry, by_id))
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
