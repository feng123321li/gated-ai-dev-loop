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


from .repository_contracts import (
    WORK_ITEM_DATABASE_FILE,
    PROJECTION_LOCK_FILE,
    LEGACY_REGISTRY_FILE,
    WORK_ITEMS_DIRECTORY,
    GOVERNANCE_DIRECTORY,
    WORK_ITEM_REGISTRY_SCHEMA_VERSION,
    ENTRY_FIELDS,
    STATE_FIELDS,
    DATABASE_TABLES,
    DATABASE_COLUMN_CONTRACTS,
    _plain_int,
    _valid_progress,
    _valid_gate,
    _valid_claim,
    _valid_latest_result,
    timestamp,
    timestamp_after,
)

class GovernanceRepository:
    """Own safe persistence, package integrity and registry projections."""

    from .repository_sqlite import (
        _connect,
        _initialize_database,
        _assert_database_schema,
        _active_connection,
        staging_transaction,
        _read_connection,
    )
    from .repository_graph_store import (
        store_graph_definition,
        read_graph_definition,
        freeze_graph_definition,
        start_graph_run,
        read_graph_run,
        begin_graph_attempts,
        append_graph_event,
        read_graph_evidence,
        read_graph_events,
        sync_graph_runs,
        rebuild_graph_run_from_events,
    )
    from .repository_evidence_store import (
        _automatic_event_summary,
        append_interaction_event,
        read_interaction_events,
        read_validation_remediations,
        effective_task_file_changes,
        effective_required_skills,
        actual_development_skill_usage,
        _stored_evidence_error,
        _validated_stored_artifact,
        validate_stored_evidence,
    )
    from .repository_projections import (
        _write_interaction_logs,
        refresh_interaction_logs,
        refresh_interaction_projection,
        write_task_context,
        _graph_projection_snapshot,
        refresh_markdown_projections,
        refresh_heartbeat_projections,
        write_registry,
        refresh_registry_projections,
        refresh_incremental_projections,
        write_acceptance_report,
        write_development_review,
    )

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
                            elif mode == "incremental":
                                self.refresh_incremental_projections(
                                    projection_registry,
                                    projection_request["rootIds"],
                                    projection_request["changedItemIds"],
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
        root_ids: set[str] | None = None,
        changed_item_ids: set[str] | None = None,
    ) -> None:
        if mode not in {"full", "incremental", "heartbeat", "interaction"}:
            fail("WORK_ITEM_PROJECTION_MODE_INVALID", "Projection mode is invalid")
        if self._connection is None:
            fail("WORK_ITEM_TRANSACTION_REQUIRED", "Projection scheduling requires an active transaction")
        if mode == "incremental" and (not root_ids or not changed_item_ids):
            fail(
                "WORK_ITEM_PROJECTION_MODE_INVALID",
                "Incremental projection requires affected roots and items",
            )
        request = {
            "mode": mode,
            "registry": registry,
            "rootId": root_id,
            "rootIds": set(root_ids or ()),
            "changedItemIds": set(changed_item_ids or ()),
        }
        current = self._pending_projection
        if current is None or mode == "full":
            self._pending_projection = request
        elif current["mode"] == "full":
            return
        elif current["mode"] == mode == "incremental":
            current["registry"] = registry
            current["rootIds"].update(request["rootIds"])
            current["changedItemIds"].update(request["changedItemIds"])
        elif (
            current["mode"] != mode
            or current["rootId"] != root_id
        ):
            self._pending_projection = {
                "mode": "full",
                "registry": registry,
                "rootId": None,
                "rootIds": set(),
                "changedItemIds": set(),
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
