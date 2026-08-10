from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import GatedLoopError, fail
from .fs_safe import (
    atomic_replace_directory,
    atomic_write,
    exclusive_file_lock,
    read_regular_file,
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
from .loop_contracts import resource_claims_overlap
from .model_core import (
    iter_hierarchy_nodes,
    validate_git_binding,
    validate_hierarchy_definition,
)
from .model_rendering import (
    WORK_ITEM_DIRECTORY,
    render_projection_documents,
    render_work_item_projection_documents,
    render_workspace_overview,
)
from .progress_reporting import attach_progress_monitor
from .storage_schema import (
    SCHEDULER_STATE_CONTRACT,
    initialize_scheduler_storage,
    verify_scheduler_state_contract,
)


GOVERNANCE_DIRECTORY = ".layered-delivery"
DATABASE_FILE = "scheduler.db"
RECEIVER_ATTESTATION_SECONDS = 300
HOST_WORKSPACE_ATTESTATION_SECONDS = 60
WORKTREE_SETUP_HEARTBEAT_SECONDS = 30
WORKTREE_SETUP_LEASE_SECONDS = 120
WORKTREE_SETUP_POLL_SECONDS = 10
MANUAL_WRITABLE_PROJECTIONS = frozenset(
    {"progress.md", "acceptance.md"}
)
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
    *,
    root_id: str,
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
        normalized = validate_hierarchy_definition(hierarchy)
    except GatedLoopError as error:
        error.details.setdefault("rootId", root_id)
        raise
    if normalized != hierarchy:
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored scheduler hierarchy is not canonical",
            rootId=root_id,
        )
    graph = _validated_stored_graph(
        row["graph_json"],
        row["graph_fingerprint"],
        root_id=root_id,
    )
    try:
        expected_graph = compile_delivery_graph(
            normalized,
            hierarchy_fingerprint=row["hierarchy_fingerprint"],
        )
    except GatedLoopError as error:
        error.details.setdefault("rootId", root_id)
        raise
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
            rootId=root_id,
        )
    return normalized, graph


def _event_material(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "eventUuid": row["event_uuid"],
        "runId": row["run_id"],
        "nodeId": row["node_id"],
        "attempt": row["attempt"],
        "eventType": row["event_type"],
        "actor": row["actor"],
        "operationId": row["operation_id"],
        "payload": json.loads(row["payload_json"]),
        "recordedAt": row["recorded_at"],
        "previousHash": row["previous_hash"],
    }


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


def _timestamp_after(value: str, *, seconds: int) -> str:
    return (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        + timedelta(seconds=seconds)
    ).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _worktree_setup_payload(
    row: sqlite3.Row,
    *,
    dispatch_already_issued: bool,
) -> dict[str, Any]:
    return {
        "reservationId": row["reservation_id"],
        "projectId": row["project_id"],
        "repositoryKey": row["repository_key"],
        "repositoryRoot": row["repository_root"],
        "branchRef": row["branch_ref"],
        "idempotencyKey": row["idempotency_key"],
        "status": row["status"],
        "attempt": row["attempt"],
        "phase": row["phase"],
        "summaryZh": row["summary_zh"],
        "progressPercent": row["progress_percent"],
        "issuedAt": row["issued_at"],
        "lastReportedAt": row["last_reported_at"],
        "leaseExpiresAt": row["lease_expires_at"],
        "readyAt": row["ready_at"],
        "failureCode": row["failure_code"],
        "failureMessageZh": row["failure_message_zh"],
        "reconciledAt": row["reconciled_at"],
        "retryRequestId": row["last_retry_request_id"],
        "dispatchAlreadyIssued": dispatch_already_issued,
    }


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
            if not database_exists:
                initialize_scheduler_storage(connection)
        except Exception:
            connection.close()
            raise
        return connection

    def issue_host_workspace_attestation(
        self,
        *,
        host_adapter_id: str,
        context_id: str,
        tool_name: str,
        tool_use_id: str,
        workspace_root: str | os.PathLike[str],
    ) -> str:
        """Bind one imminent MCP call to a host-observed workspace."""

        workspace = str(
            Path(workspace_root).absolute().resolve(strict=True)
        )
        attestation = secrets.token_hex(32)
        attestation_digest = hashlib.sha256(
            attestation.encode("utf-8")
        ).hexdigest()
        context_digest = hashlib.sha256(
            context_id.encode("utf-8")
        ).hexdigest()
        tool_use_digest = hashlib.sha256(
            tool_use_id.encode("utf-8")
        ).hexdigest()
        at = timestamp(self.now)
        expires_at = (
            datetime.fromisoformat(at.replace("Z", "+00:00"))
            + timedelta(seconds=HOST_WORKSPACE_ATTESTATION_SECONDS)
        ).isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            connection.execute(
                "UPDATE host_workspace_attestations "
                "SET status = 'SUPERSEDED' "
                "WHERE host_adapter_id = ? AND context_digest = ? "
                "AND tool_use_digest = ? AND status = 'ISSUED'",
                (
                    host_adapter_id,
                    context_digest,
                    tool_use_digest,
                ),
            )
            connection.execute(
                "INSERT INTO host_workspace_attestations("
                "attestation_digest, host_adapter_id, context_digest, "
                "tool_name, tool_use_digest, workspace_root, "
                "workspace_key, status, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'ISSUED', ?, ?)",
                (
                    attestation_digest,
                    host_adapter_id,
                    context_digest,
                    tool_name,
                    tool_use_digest,
                    workspace,
                    self.workspace_key(workspace),
                    at,
                    expires_at,
                ),
            )
        return attestation

    def consume_host_workspace_attestation(
        self,
        attestation: str,
        *,
        host_adapter_id: str,
        tool_name: str,
    ) -> str:
        """Consume host workspace evidence and return its verified path."""

        digest = hashlib.sha256(attestation.encode("utf-8")).hexdigest()
        at = timestamp(self.now)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM host_workspace_attestations "
                "WHERE attestation_digest = ?",
                (digest,),
            ).fetchone()
            if row is None:
                fail(
                    "SCHEDULER_HOST_WORKSPACE_ATTESTATION_MISSING",
                    "Host workspace evidence does not exist",
                )
            if row["status"] != "ISSUED":
                fail(
                    "SCHEDULER_HOST_WORKSPACE_ATTESTATION_CONSUMED",
                    "Host workspace evidence is no longer active",
                )
            if row["expires_at"] < at:
                fail(
                    "SCHEDULER_HOST_WORKSPACE_ATTESTATION_EXPIRED",
                    "Host workspace evidence expired",
                )
            if (
                row["host_adapter_id"] != host_adapter_id
                or row["tool_name"] != tool_name
            ):
                fail(
                    "SCHEDULER_HOST_WORKSPACE_ATTESTATION_MISMATCH",
                    "Host workspace evidence targets another call",
                )
            workspace = str(
                Path(row["workspace_root"]).absolute().resolve(strict=True)
            )
            if self.workspace_key(workspace) != row["workspace_key"]:
                fail(
                    "SCHEDULER_HOST_WORKSPACE_ATTESTATION_MISMATCH",
                    "Host workspace evidence no longer matches its path",
                )
            updated = connection.execute(
                "UPDATE host_workspace_attestations "
                "SET status = 'CONSUMED', consumed_at = ? "
                "WHERE attestation_digest = ? AND status = 'ISSUED'",
                (at, digest),
            )
            if updated.rowcount != 1:
                fail(
                    "SCHEDULER_HOST_WORKSPACE_ATTESTATION_CONSUMED",
                    "Host workspace evidence was consumed concurrently",
                )
        return workspace

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
        *,
        allow_unbound_manual: bool = False,
        allow_unbound_choice: bool = False,
    ) -> None:
        if not self.database_path.is_file():
            fail(
                "SCHEDULER_STATE_ABSENT",
                "No Delivery Graph scheduler state exists",
            )
        expected = self.workspace_key(workspace_root)
        if allow_unbound_manual or allow_unbound_choice:
            with self.read() as connection:
                manual = connection.execute(
                    "SELECT h.status, w.workspace_key "
                    "FROM hierarchies h "
                    "LEFT JOIN delivery_workspaces w "
                    "ON w.root_id = h.root_id "
                    "WHERE h.root_id = ?",
                    (root_id,),
                ).fetchone()
            if (
                manual is not None
                and manual["status"]
                in (
                    {"HANDOFF_READY"}
                    if not allow_unbound_choice
                    else {"CHOICE_READY", "HANDOFF_READY"}
                )
                and manual["workspace_key"] is None
            ):
                return
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
            candidates = (
                [row for row in rows if row["status"] != "ARCHIVED"]
                if root_id is None
                else rows
            )
            if root_id is not None:
                candidates = [
                    row for row in candidates if row["root_id"] == root_id
                ]
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
                    row
                    for row in candidates
                    if row["root_id"] in bound_ids
                    or row["status"]
                    in {"CHOICE_READY", "HANDOFF_READY"}
                ]
            if root_id is None:
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
                "SELECT status, execution_mode FROM runs "
                "WHERE root_id = ? AND revision = ?",
                (latest["root_id"], latest["revision"]),
            ).fetchone()
            if latest["status"] == "ARCHIVED":
                revision = connection.execute(
                    "SELECT status FROM delivery_revisions "
                    "WHERE root_id = ? AND revision = ?",
                    (latest["root_id"], latest["revision"]),
                ).fetchone()
                if (
                    run is None
                    or run["status"] != "COMPLETED"
                    or revision is None
                    or revision["status"] != "ARCHIVED"
                ):
                    fail(
                        "SCHEDULER_STATE_INVALID",
                        "Archived Delivery state is inconsistent",
                        rootId=latest["root_id"],
                    )
        state = (
            latest["status"]
            if latest["status"] in {"ARCHIVED", "PREPARED"}
            else (
                run["status"]
                if run is not None
                else latest["status"]
            )
        )
        # Projection templates are rebuildable views, not stored schema.
        # Refresh every stored schema-v3 Delivery so workspaces created by an
        # earlier plugin release receive the current fixed projection set.
        projection_issues: list[dict[str, str]] = []
        for row in rows:
            projection_root_id = row["root_id"]
            try:
                self.write_projections(
                    projection_root_id,
                    refresh_workspace_overview=False,
                )
            except (GatedLoopError, OSError) as error:
                if projection_root_id == latest["root_id"]:
                    raise
                code = (
                    error.code
                    if isinstance(error, GatedLoopError)
                    else "SCHEDULER_PROJECTION_REFRESH_FAILED"
                )
                message = (
                    error.message
                    if isinstance(error, GatedLoopError)
                    else (
                        "Controller could not refresh this Delivery "
                        "projection"
                    )
                )
                projection_issues.append(
                    {
                        "rootId": projection_root_id,
                        "code": code,
                        "message": message,
                    }
                )
        self.write_workspace_overview()
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
        if run is not None:
            result["executionMode"] = run["execution_mode"]
        if state == "ARCHIVED":
            result["archivedAt"] = latest["updated_at"]
            result["runStatus"] = run["status"]
            result["nextAction"] = "START_NEW_DELIVERY"
        if workspace_root is not None:
            if state == "CHOICE_READY":
                result["workspaceIsolation"] = {
                    "mode": "UNBOUND_EXECUTION_CHOICE",
                    "workspaceKey": None,
                }
            elif state == "HANDOFF_READY":
                result["workspaceIsolation"] = {
                    "mode": "UNBOUND_MANUAL_HANDOFF",
                    "workspaceKey": None,
                }
            else:
                result["workspaceIsolation"] = self.workspace_binding(
                    latest["root_id"]
                )
        if projection_issues:
            result["projectionIssues"] = projection_issues
        git_binding = latest_hierarchy["delivery"].get("gitBinding")
        if git_binding is not None:
            result["gitBinding"] = git_binding
        result["projectScopes"] = latest_hierarchy["delivery"].get(
            "projectScopes",
            [],
        )
        return result

    def git_branch_usage(
        self,
        branch_ref: str,
        *,
        repository_key: str | None = None,
    ) -> list[dict[str, str]]:
        """Return Delivery identities using a branch in one Git repository."""

        self._assert_no_legacy_state()
        if not self.database_path.is_file():
            return []
        usage: list[dict[str, str]] = []
        with self.read() as connection:
            rows = connection.execute(
                "SELECT * FROM hierarchies ORDER BY created_at, root_id"
            ).fetchall()
            from .git_binding import git_repository_identity

            try:
                primary_repository_key = git_repository_identity(
                    str(self.root)
                )
            except (FileNotFoundError, OSError, RuntimeError):
                primary_repository_key = None
            for row in rows:
                hierarchy, _ = _validated_stored_definition(row)
                delivery = hierarchy["delivery"]
                bindings: list[tuple[dict[str, str], str | None]] = []
                binding = delivery.get("gitBinding")
                if binding is not None:
                    bindings.append((binding, primary_repository_key))
                for scope in delivery.get("projectScopes", []):
                    scope_binding = scope.get("gitBinding")
                    if scope_binding is None:
                        continue
                    try:
                        scope_repository_key = git_repository_identity(
                            scope["workspaceRoot"]
                        )
                    except (FileNotFoundError, OSError, RuntimeError):
                        scope_repository_key = None
                    bindings.append(
                        (scope_binding, scope_repository_key)
                    )
                if not any(
                    item["branchRef"] == branch_ref
                    and (
                        repository_key is None
                        or item_repository_key == repository_key
                    )
                    for item, item_repository_key in bindings
                ):
                    continue
                run = connection.execute(
                    "SELECT status FROM runs WHERE root_id = ? "
                    "AND revision = ?",
                    (row["root_id"], row["revision"]),
                ).fetchone()
                status = (
                    "ARCHIVED"
                    if row["status"] == "ARCHIVED"
                    else (
                        run["status"]
                        if run is not None
                        else row["status"]
                    )
                )
                usage.append(
                    {"rootId": row["root_id"], "status": status}
                )
        return usage

    def development_preference(self, root_id: str) -> dict[str, Any] | None:
        """Return the remembered development baseline for one Delivery."""

        self._assert_no_legacy_state()
        if not self.database_path.is_file():
            return None
        with self.read() as connection:
            row = connection.execute(
                "SELECT branch_ref, base_ref, base_commit, "
                "integration_target, source, chosen_by, chosen_at "
                "FROM delivery_preferences WHERE root_id = ?",
                (root_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "branchRef": row["branch_ref"],
            "baseRef": row["base_ref"],
            "baseCommit": row["base_commit"],
            "integrationTarget": row["integration_target"],
            "source": row["source"],
            "chosenBy": row["chosen_by"],
            "chosenAt": row["chosen_at"],
        }

    def record_development_preference(
        self,
        root_id: str,
        *,
        binding: dict[str, str],
        source: str,
        chosen_by: str,
    ) -> dict[str, Any]:
        """Persist (UPSERT) the chosen development baseline for one Delivery."""

        normalized_binding = validate_git_binding(binding)
        chosen_at = timestamp(self.now)
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO delivery_preferences("
                "root_id, branch_ref, base_ref, base_commit, "
                "integration_target, source, chosen_by, chosen_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    root_id,
                    normalized_binding["branchRef"],
                    normalized_binding["baseRef"],
                    normalized_binding["baseCommit"],
                    normalized_binding["integrationTarget"],
                    source,
                    chosen_by,
                    chosen_at,
                ),
            )
        return {
            "branchRef": normalized_binding["branchRef"],
            "baseRef": normalized_binding["baseRef"],
            "baseCommit": normalized_binding["baseCommit"],
            "integrationTarget": normalized_binding["integrationTarget"],
            "source": source,
            "chosenBy": chosen_by,
            "chosenAt": chosen_at,
        }

    def clear_development_preference(self, root_id: str) -> None:
        """Drop the remembered development baseline (e.g. on abandon)."""

        if not self.database_path.is_file():
            return
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM delivery_preferences WHERE root_id = ?",
                (root_id,),
            )

    def record_choice_ready(
        self,
        hierarchy: dict[str, Any],
        graph: dict[str, Any],
        *,
        hierarchy_fingerprint: str,
        graph_fingerprint: str,
    ) -> dict[str, Any]:
        """Stage initial human artifacts before execution-mode selection."""

        root_id = graph["rootId"]
        hierarchy_json = canonical_json(hierarchy)
        graph_json = canonical_json(graph)
        staged = False
        with self.transaction() as connection:
            self._assert_delivery_requirement_available(
                connection,
                hierarchy,
            )
            existing = connection.execute(
                "SELECT * FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if existing is None:
                at = timestamp(self.now)
                connection.execute(
                    """
                    INSERT INTO hierarchies(
                        root_id, revision, hierarchy_fingerprint,
                        graph_fingerprint, hierarchy_json, graph_json,
                        status, created_at, updated_at
                    ) VALUES (?, 1, ?, ?, ?, ?, 'CHOICE_READY', ?, ?)
                    """,
                    (
                        root_id,
                        hierarchy_fingerprint,
                        graph_fingerprint,
                        hierarchy_json,
                        graph_json,
                        at,
                        at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO delivery_revisions(
                        root_id, revision, hierarchy_fingerprint,
                        graph_fingerprint, hierarchy_json, graph_json,
                        status, reason, created_at, updated_at
                    ) VALUES (
                        ?, 1, ?, ?, ?, ?, 'CHOICE_READY', ?, ?, ?
                    )
                    """,
                    (
                        root_id,
                        hierarchy_fingerprint,
                        graph_fingerprint,
                        hierarchy_json,
                        graph_json,
                        "已生成基线，待选择自动执行或手动开发",
                        at,
                        at,
                    ),
                )
                staged = True
                status = "CHOICE_READY"
            else:
                _validated_stored_definition(existing)
                if existing["status"] == "ARCHIVED":
                    fail(
                        "SCHEDULER_DELIVERY_ARCHIVED",
                        "An archived Delivery cannot be previewed again",
                        rootId=root_id,
                    )
                content_matches = (
                    existing["hierarchy_fingerprint"]
                    == hierarchy_fingerprint
                    and existing["graph_fingerprint"]
                    == graph_fingerprint
                )
                if existing["status"] == "CHOICE_READY":
                    at = _commit_timestamp(
                        self.now,
                        existing["updated_at"],
                    )
                    connection.execute(
                        "UPDATE hierarchies SET hierarchy_fingerprint = ?, "
                        "graph_fingerprint = ?, hierarchy_json = ?, "
                        "graph_json = ?, updated_at = ? WHERE root_id = ?",
                        (
                            hierarchy_fingerprint,
                            graph_fingerprint,
                            hierarchy_json,
                            graph_json,
                            at,
                            root_id,
                        ),
                    )
                    connection.execute(
                        "UPDATE delivery_revisions SET "
                        "hierarchy_fingerprint = ?, graph_fingerprint = ?, "
                        "hierarchy_json = ?, graph_json = ?, status = "
                        "'CHOICE_READY', reason = ?, "
                        "confirmed_by = CASE WHEN ? THEN confirmed_by "
                        "ELSE NULL END, authorized_project_ids_json = "
                        "CASE WHEN ? THEN authorized_project_ids_json "
                        "ELSE NULL END, execution_mode = CASE WHEN ? "
                        "THEN execution_mode ELSE NULL END, updated_at = ? "
                        "WHERE root_id = ? AND revision = ?",
                        (
                            hierarchy_fingerprint,
                            graph_fingerprint,
                            hierarchy_json,
                            graph_json,
                            (
                                "自动执行已确认，等待实际开发 worktree"
                                if content_matches
                                else "需求沟通后已重新生成基线，待选择开发方式"
                            ),
                            content_matches,
                            content_matches,
                            content_matches,
                            at,
                            root_id,
                            existing["revision"],
                        ),
                    )
                    if not content_matches:
                        connection.execute(
                            "UPDATE worktree_setup_reservations SET "
                            "status = 'SUPERSEDED' WHERE root_id = ? "
                            "AND revision = ? AND status = 'PENDING'",
                            (root_id, existing["revision"]),
                        )
                    staged = True
                    status = "CHOICE_READY"
                elif content_matches:
                    at = existing["updated_at"]
                    staged = True
                    status = existing["status"]
                else:
                    at = timestamp(self.now)
                    status = "PREVIEW"
        if staged and status == "CHOICE_READY":
            self.write_projections(root_id)
        return {
            "rootId": root_id,
            "status": status,
            "deliveryRevision": (
                1 if existing is None else existing["revision"]
            ),
            "artifactsReady": staged,
            "controlStateCreated": existing is not None or staged,
            "recordedAt": at,
        }

    def record_automatic_selection(
        self,
        root_id: str,
        *,
        expected_hierarchy_fingerprint: str,
        expected_graph_fingerprint: str,
        authorized_project_ids: list[str],
        confirmed_by: str,
        worktree_requests: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Persist one human AUTOMATIC choice before host worktree setup."""

        with self.transaction() as connection:
            hierarchy = connection.execute(
                "SELECT * FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if hierarchy is None:
                fail(
                    "SCHEDULER_HIERARCHY_MISSING",
                    f"Unknown hierarchy: {root_id}",
                )
            if (
                hierarchy["hierarchy_fingerprint"]
                != expected_hierarchy_fingerprint
                or hierarchy["graph_fingerprint"]
                != expected_graph_fingerprint
            ):
                fail(
                    "SCHEDULER_EXECUTION_CHOICE_STALE",
                    "The selected execution choice does not match the "
                    "generated baseline",
                    rootId=root_id,
                )
            if hierarchy["status"] not in {"CHOICE_READY", "PREPARED"}:
                fail(
                    "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
                    "The Delivery is not waiting for automatic execution",
                    rootId=root_id,
                    status=hierarchy["status"],
                )
            revision = connection.execute(
                "SELECT * FROM delivery_revisions WHERE root_id = ? "
                "AND revision = ?",
                (root_id, hierarchy["revision"]),
            ).fetchone()
            if revision is None:
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "The current Delivery revision is missing",
                    rootId=root_id,
                )
            encoded_projects = canonical_json(authorized_project_ids)
            selection_already_applied = (
                revision["execution_mode"] == "automatic_pending"
            )
            if selection_already_applied and (
                revision["confirmed_by"] != confirmed_by
                or revision["authorized_project_ids_json"]
                != encoded_projects
            ):
                fail(
                    "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
                    "The recorded automatic choice has different human "
                    "authorization",
                    rootId=root_id,
                )
            if revision["execution_mode"] not in {
                None,
                "automatic_pending",
            }:
                fail(
                    "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
                    "Another execution mode has already been selected",
                    rootId=root_id,
                    executionMode=revision["execution_mode"],
                )
            at = _commit_timestamp(self.now, hierarchy["updated_at"])
            reservations: list[dict[str, Any]] = []
            for request in worktree_requests or []:
                required_fields = {
                    "reservationId",
                    "projectId",
                    "repositoryKey",
                    "repositoryRoot",
                    "branchRef",
                    "idempotencyKey",
                }
                if not required_fields.issubset(request):
                    fail(
                        "SCHEDULER_STATE_INVALID",
                        "A worktree setup request is incomplete",
                        rootId=root_id,
                    )
                existing_reservation = connection.execute(
                    "SELECT * FROM worktree_setup_reservations "
                    "WHERE root_id = ? AND revision = ? AND project_id = ?",
                    (
                        root_id,
                        hierarchy["revision"],
                        request["projectId"],
                    ),
                ).fetchone()
                if existing_reservation is not None:
                    unchanged = all(
                        existing_reservation[column] == request[field]
                        for column, field in (
                            ("reservation_id", "reservationId"),
                            ("repository_key", "repositoryKey"),
                            ("repository_root", "repositoryRoot"),
                            ("branch_ref", "branchRef"),
                            ("idempotency_key", "idempotencyKey"),
                        )
                    )
                    if (
                        not unchanged
                        or existing_reservation["hierarchy_fingerprint"]
                        != expected_hierarchy_fingerprint
                    ):
                        fail(
                            "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
                            "The recorded worktree setup differs from the "
                            "current Delivery revision",
                            rootId=root_id,
                            projectId=request["projectId"],
                        )
                    reservations.append(
                        {
                            **request,
                            **_worktree_setup_payload(
                                existing_reservation,
                                dispatch_already_issued=True,
                            ),
                            "dispatchAlreadyIssued": True,
                        }
                    )
                    continue
                conflicting = connection.execute(
                    "SELECT root_id, revision, project_id FROM "
                    "worktree_setup_reservations WHERE repository_key = ? "
                    "AND branch_ref = ? AND status IN ('PENDING', "
                    "'IN_PROGRESS', 'READY', 'FAILED', 'EXPIRED')",
                    (request["repositoryKey"], request["branchRef"]),
                ).fetchone()
                if conflicting is not None:
                    fail(
                        "SCHEDULER_WORKTREE_BRANCH_RESERVED",
                        "The Git branch is already reserved by another "
                        "Delivery worktree setup",
                        repositoryKey=request["repositoryKey"],
                        branchRef=request["branchRef"],
                        conflictingRootId=conflicting["root_id"],
                        conflictingRevision=conflicting["revision"],
                        conflictingProjectId=conflicting["project_id"],
                    )
                connection.execute(
                    "INSERT INTO worktree_setup_reservations("
                    "reservation_id, root_id, revision, project_id, "
                    "repository_key, repository_root, branch_ref, "
                    "hierarchy_fingerprint, idempotency_key, status, "
                    "attempt, phase, summary_zh, progress_percent, "
                    "issued_at, last_reported_at, lease_expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 1, "
                    "'QUEUED', '等待宿主创建 worktree', 0, ?, ?, ?)",
                    (
                        request["reservationId"],
                        root_id,
                        hierarchy["revision"],
                        request["projectId"],
                        request["repositoryKey"],
                        request["repositoryRoot"],
                        request["branchRef"],
                        expected_hierarchy_fingerprint,
                        request["idempotencyKey"],
                        at,
                        at,
                        _timestamp_after(
                            at,
                            seconds=WORKTREE_SETUP_LEASE_SECONDS,
                        ),
                    ),
                )
                reservations.append(
                    {
                        **request,
                        "status": "PENDING",
                        "attempt": 1,
                        "phase": "QUEUED",
                        "summaryZh": "等待宿主创建 worktree",
                        "progressPercent": 0,
                        "issuedAt": at,
                        "lastReportedAt": at,
                        "leaseExpiresAt": _timestamp_after(
                            at,
                            seconds=WORKTREE_SETUP_LEASE_SECONDS,
                        ),
                        "failureCode": None,
                        "failureMessageZh": None,
                        "dispatchAlreadyIssued": False,
                    }
                )
            connection.execute(
                "UPDATE delivery_revisions SET confirmed_by = ?, "
                "authorized_project_ids_json = ?, execution_mode = "
                "'automatic_pending', reason = ?, updated_at = ? "
                "WHERE root_id = ? AND revision = ?",
                (
                    confirmed_by,
                    encoded_projects,
                    "用户已选择自动执行，等待宿主完成实际开发 worktree",
                    at,
                    root_id,
                    hierarchy["revision"],
                ),
            )
            connection.execute(
                "UPDATE hierarchies SET updated_at = ? WHERE root_id = ?",
                (at, root_id),
            )
        self.write_projections(root_id)
        return {
            "selection": "AUTOMATIC",
            "state": "RECORDED_PENDING_WORKTREE",
            "confirmationRequired": False,
            "confirmedBy": confirmed_by,
            "authorizedProjectIds": list(authorized_project_ids),
            "selectionAlreadyApplied": selection_already_applied,
            "worktreeReservations": reservations,
        }

    def execution_selection(
        self,
        root_id: str,
    ) -> dict[str, Any] | None:
        """Return a recorded execution selection for the current revision."""

        with self.read() as connection:
            row = connection.execute(
                "SELECT d.* FROM delivery_revisions d "
                "JOIN hierarchies h ON h.root_id = d.root_id "
                "AND h.revision = d.revision WHERE d.root_id = ?",
                (root_id,),
            ).fetchone()
        if row is None or row["execution_mode"] != "automatic_pending":
            return None
        authorized = json.loads(row["authorized_project_ids_json"] or "[]")
        return {
            "selection": "AUTOMATIC",
            "state": "RECORDED_PENDING_WORKTREE",
            "confirmationRequired": False,
            "confirmedBy": row["confirmed_by"],
            "authorizedProjectIds": authorized,
        }

    def worktree_setup_reservations(
        self,
        root_id: str,
    ) -> list[dict[str, Any]]:
        """Return the worktree setup reservations for the current revision."""

        if not self.database_path.is_file():
            return []
        at = timestamp(self.now)
        with self.transaction() as connection:
            connection.execute(
                "UPDATE worktree_setup_reservations SET status = 'EXPIRED', "
                "phase = 'LEASE_EXPIRED', "
                "summary_zh = 'worktree 创建心跳已超时，必须先核对宿主和残留路径', "
                "failure_code = 'WORKTREE_SETUP_LEASE_EXPIRED', "
                "failure_message_zh = '旧创建尝试可能仍在运行或留下半成品', "
                "last_reported_at = COALESCE(last_reported_at, issued_at) "
                "WHERE root_id = ? AND status IN ('PENDING', 'IN_PROGRESS') "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                (root_id, at),
            )
            rows = connection.execute(
                "SELECT w.* FROM worktree_setup_reservations w "
                "JOIN hierarchies h ON h.root_id = w.root_id "
                "AND h.revision = w.revision WHERE w.root_id = ? "
                "ORDER BY w.project_id",
                (root_id,),
            ).fetchall()
        return [
            _worktree_setup_payload(
                row,
                dispatch_already_issued=True,
            )
            for row in rows
        ]

    def mark_worktree_setups_ready(
        self,
        root_id: str,
        project_ids: list[str],
    ) -> None:
        """Record exact project worktrees observed by the Controller."""

        if not project_ids or not self.database_path.is_file():
            return
        at = timestamp(self.now)
        with self.transaction() as connection:
            connection.executemany(
                "UPDATE worktree_setup_reservations SET status = 'READY', "
                "phase = 'READY', summary_zh = '精确 worktree 已由 Controller 验证', "
                "progress_percent = 100, last_reported_at = ?, "
                "lease_expires_at = NULL, failure_code = NULL, "
                "failure_message_zh = NULL, "
                "ready_at = COALESCE(ready_at, ?) WHERE root_id = ? "
                "AND project_id = ? AND status NOT IN "
                "('RELEASED', 'SUPERSEDED')",
                (
                    (at, at, root_id, project_id)
                    for project_id in project_ids
                ),
            )

    def report_worktree_setup(
        self,
        root_id: str,
        *,
        project_id: str,
        reservation_id: str,
        expected_attempt: int,
        event: str,
        phase: str,
        summary_zh: str,
        progress_percent: int | None,
        failure_code: str | None,
        confirmed_previous_attempt_stopped: bool,
        confirmed_partial_state_reconciled: bool,
        retry_request_id: str | None,
    ) -> dict[str, Any]:
        """Record one host setup update or atomically grant a safe retry."""

        at = timestamp(self.now)
        retry_dispatch_granted = False
        with self.transaction() as connection:
            hierarchy = connection.execute(
                "SELECT revision FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if hierarchy is None:
                fail(
                    "SCHEDULER_HIERARCHY_MISSING",
                    f"Unknown hierarchy: {root_id}",
                )
            row = connection.execute(
                "SELECT * FROM worktree_setup_reservations WHERE root_id = ? "
                "AND revision = ? AND project_id = ?",
                (root_id, hierarchy["revision"], project_id),
            ).fetchone()
            if row is None or row["reservation_id"] != reservation_id:
                fail(
                    "SCHEDULER_WORKTREE_SETUP_RESERVATION_MISSING",
                    "The worktree setup reservation does not match the "
                    "current Delivery revision",
                    rootId=root_id,
                    projectId=project_id,
                )
            if row["attempt"] != expected_attempt:
                if (
                    event == "RETRY_CONFIRMED"
                    and row["attempt"] == expected_attempt + 1
                    and row["status"] == "PENDING"
                    and row["last_retry_request_id"] == retry_request_id
                    and row["lease_expires_at"] is not None
                    and row["lease_expires_at"] >= at
                ):
                    return {
                        **_worktree_setup_payload(
                            row,
                            dispatch_already_issued=False,
                        ),
                        "event": event,
                        "retryDispatchGranted": True,
                        "retryRequestReplayed": True,
                    }
                fail(
                    "SCHEDULER_WORKTREE_SETUP_ATTEMPT_STALE",
                    "The worktree setup update targets an old attempt",
                    rootId=root_id,
                    projectId=project_id,
                    expectedAttempt=expected_attempt,
                    actualAttempt=row["attempt"],
                )
            status = row["status"]
            if (
                status in {"PENDING", "IN_PROGRESS"}
                and row["lease_expires_at"] is not None
                and row["lease_expires_at"] < at
            ):
                status = "EXPIRED"
                connection.execute(
                    "UPDATE worktree_setup_reservations SET "
                    "status = 'EXPIRED', phase = 'LEASE_EXPIRED', "
                    "summary_zh = 'worktree 创建心跳已超时，必须先核对宿主和残留路径', "
                    "failure_code = 'WORKTREE_SETUP_LEASE_EXPIRED', "
                    "failure_message_zh = '旧创建尝试可能仍在运行或留下半成品' "
                    "WHERE reservation_id = ?",
                    (reservation_id,),
                )
            if event in {"STARTED", "PROGRESS"}:
                if status not in {"PENDING", "IN_PROGRESS"}:
                    fail(
                        "SCHEDULER_WORKTREE_SETUP_NOT_ACTIVE",
                        "Worktree setup progress cannot update an inactive "
                        "attempt",
                        rootId=root_id,
                        projectId=project_id,
                        status=status,
                        nextAction=(
                            "RECONCILE_EXPIRED_WORKTREE_SETUP"
                            if status == "EXPIRED"
                            else "RECONCILE_FAILED_WORKTREE_SETUP"
                        ),
                    )
                lease_expires_at = _timestamp_after(
                    at,
                    seconds=WORKTREE_SETUP_LEASE_SECONDS,
                )
                connection.execute(
                    "UPDATE worktree_setup_reservations SET "
                    "status = 'IN_PROGRESS', phase = ?, summary_zh = ?, "
                    "progress_percent = ?, last_reported_at = ?, "
                    "lease_expires_at = ?, failure_code = NULL, "
                    "failure_message_zh = NULL WHERE reservation_id = ?",
                    (
                        phase,
                        summary_zh,
                        progress_percent,
                        at,
                        lease_expires_at,
                        reservation_id,
                    ),
                )
            elif event == "FAILED":
                if status not in {"PENDING", "IN_PROGRESS", "EXPIRED"}:
                    fail(
                        "SCHEDULER_WORKTREE_SETUP_NOT_ACTIVE",
                        "Only an unfinished worktree setup can report failure",
                        rootId=root_id,
                        projectId=project_id,
                        status=status,
                    )
                connection.execute(
                    "UPDATE worktree_setup_reservations SET status = "
                    "'FAILED', phase = ?, summary_zh = ?, "
                    "progress_percent = ?, last_reported_at = ?, "
                    "lease_expires_at = NULL, failure_code = ?, "
                    "failure_message_zh = ? WHERE reservation_id = ?",
                    (
                        phase,
                        summary_zh,
                        progress_percent,
                        at,
                        failure_code,
                        summary_zh,
                        reservation_id,
                    ),
                )
            elif event == "RETRY_CONFIRMED":
                if status not in {"FAILED", "EXPIRED"}:
                    fail(
                        "SCHEDULER_WORKTREE_SETUP_RETRY_NOT_READY",
                        "A live or completed setup cannot start another "
                        "attempt",
                        rootId=root_id,
                        projectId=project_id,
                        status=status,
                    )
                if not (
                    confirmed_previous_attempt_stopped
                    and confirmed_partial_state_reconciled
                ):
                    fail(
                        "SCHEDULER_WORKTREE_SETUP_RECONCILIATION_REQUIRED",
                        "Retry requires confirmation that the old host action "
                        "stopped and every partial path/worktree was safely "
                        "reconciled",
                        rootId=root_id,
                        projectId=project_id,
                        actualAttempt=expected_attempt,
                    )
                new_attempt = expected_attempt + 1
                lease_expires_at = _timestamp_after(
                    at,
                    seconds=WORKTREE_SETUP_LEASE_SECONDS,
                )
                connection.execute(
                    "UPDATE worktree_setup_reservations SET status = "
                    "'PENDING', attempt = ?, phase = 'QUEUED', "
                    "summary_zh = ?, progress_percent = 0, issued_at = ?, "
                    "last_reported_at = ?, lease_expires_at = ?, "
                    "ready_at = NULL, failure_code = NULL, "
                    "failure_message_zh = NULL, reconciled_at = ? "
                    ", last_retry_request_id = ? "
                    "WHERE reservation_id = ? AND attempt = ?",
                    (
                        new_attempt,
                        summary_zh,
                        at,
                        at,
                        lease_expires_at,
                        at,
                        retry_request_id,
                        reservation_id,
                        expected_attempt,
                    ),
                )
                retry_dispatch_granted = True
            else:
                fail(
                    "SCHEDULER_WORKTREE_SETUP_EVENT_INVALID",
                    "Unknown worktree setup event",
                    event=event,
                )
            updated = connection.execute(
                "SELECT * FROM worktree_setup_reservations WHERE "
                "reservation_id = ?",
                (reservation_id,),
            ).fetchone()
        return {
            **_worktree_setup_payload(
                updated,
                dispatch_already_issued=not retry_dispatch_granted,
            ),
            "event": event,
            "retryDispatchGranted": retry_dispatch_granted,
            "retryRequestReplayed": False,
        }

    def record_manual_handoff(
        self,
        hierarchy: dict[str, Any],
        graph: dict[str, Any],
        *,
        hierarchy_fingerprint: str,
        graph_fingerprint: str,
        authorized_project_ids: list[str],
        expected_current_revision: int | None,
        continuity_basis: str | None,
        revision_reason: str | None,
        confirmed_by: str,
    ) -> dict[str, Any]:
        """Register a frozen manual snapshot without creating a Graph run."""

        root_id = graph["rootId"]
        hierarchy_json = canonical_json(hierarchy)
        graph_json = canonical_json(graph)
        previous_revision: int | None = None
        preserve_manual_updates = True
        with self.transaction() as connection:
            self._assert_delivery_requirement_available(
                connection,
                hierarchy,
            )
            existing = connection.execute(
                "SELECT * FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if existing is not None:
                _validated_stored_definition(existing)
                if existing["status"] == "ARCHIVED":
                    fail(
                        "SCHEDULER_DELIVERY_ARCHIVED",
                        "An archived Delivery cannot become a manual handoff",
                        rootId=root_id,
                    )
                content_changed = (
                    existing["hierarchy_fingerprint"]
                    != hierarchy_fingerprint
                    or existing["graph_fingerprint"]
                    != graph_fingerprint
                )
                if content_changed:
                    if existing["status"] != "HANDOFF_READY":
                        fail(
                            "SCHEDULER_HANDOFF_CONTROL_STATE_CONFLICT",
                            "A manual revision requires an existing "
                            "HANDOFF_READY Delivery",
                            rootId=root_id,
                            status=existing["status"],
                        )
                    if (
                        not isinstance(expected_current_revision, int)
                        or isinstance(expected_current_revision, bool)
                        or continuity_basis
                        != "USER_EXPLICIT_SAME_DELIVERY"
                        or not isinstance(revision_reason, str)
                        or not revision_reason.strip()
                    ):
                        fail(
                            "SCHEDULER_MANUAL_REVISION_CONTINUITY_REQUIRED",
                            "Changed manual content must explicitly continue "
                            "the same Delivery revision",
                            rootId=root_id,
                            currentRevision=existing["revision"],
                            requiredContinuityBasis=(
                                "USER_EXPLICIT_SAME_DELIVERY"
                            ),
                            nextAction=(
                                "CREATE_MANUAL_REVISION_IN_EXISTING_DIRECTORY"
                            ),
                        )
                    if expected_current_revision != existing["revision"]:
                        fail(
                            "SCHEDULER_REVISION_CONFLICT",
                            "The expected manual Delivery revision is not "
                            "current",
                            rootId=root_id,
                            expectedRevision=expected_current_revision,
                            actualRevision=existing["revision"],
                        )
                    previous_revision = existing["revision"]
                    delivery_revision = previous_revision + 1
                    at = _commit_timestamp(
                        self.now,
                        existing["updated_at"],
                    )
                    connection.execute(
                        "UPDATE delivery_revisions SET status = "
                        "'SUPERSEDED', updated_at = ?, superseded_at = ? "
                        "WHERE root_id = ? AND revision = ?",
                        (at, at, root_id, previous_revision),
                    )
                    connection.execute(
                        "UPDATE hierarchies SET revision = ?, "
                        "hierarchy_fingerprint = ?, graph_fingerprint = ?, "
                        "hierarchy_json = ?, graph_json = ?, "
                        "status = 'HANDOFF_READY', updated_at = ? "
                        "WHERE root_id = ?",
                        (
                            delivery_revision,
                            hierarchy_fingerprint,
                            graph_fingerprint,
                            hierarchy_json,
                            graph_json,
                            at,
                            root_id,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO delivery_revisions(
                            root_id, revision, hierarchy_fingerprint,
                            graph_fingerprint, hierarchy_json, graph_json,
                            status, reason, continuity_basis, requested_by,
                            confirmed_by, authorized_project_ids_json,
                            created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, 'HANDOFF_READY', ?, ?, ?,
                            ?, ?, ?, ?
                        )
                        """,
                        (
                            root_id,
                            delivery_revision,
                            hierarchy_fingerprint,
                            graph_fingerprint,
                            hierarchy_json,
                            graph_json,
                            revision_reason.strip(),
                            continuity_basis,
                            confirmed_by,
                            confirmed_by,
                            canonical_json(authorized_project_ids),
                            at,
                            at,
                        ),
                    )
                else:
                    delivery_revision = existing["revision"]
                    previous_revision = (
                        delivery_revision - 1
                        if delivery_revision > 1
                        else None
                    )
                    if (
                        expected_current_revision is not None
                        and expected_current_revision != delivery_revision
                    ):
                        fail(
                            "SCHEDULER_REVISION_CONFLICT",
                            "The expected manual Delivery revision is not "
                            "current",
                            rootId=root_id,
                            expectedRevision=expected_current_revision,
                            actualRevision=delivery_revision,
                        )
                    if existing["status"] == "CHOICE_READY":
                        preserve_manual_updates = False
                        if any(
                            value is not None
                            for value in (
                                expected_current_revision,
                                continuity_basis,
                                revision_reason,
                            )
                        ):
                            fail(
                                "SCHEDULER_REVISION_CONFLICT",
                                "An initial staged choice cannot declare a "
                                "previous Delivery revision",
                                rootId=root_id,
                            )
                        at = _commit_timestamp(
                            self.now,
                            existing["updated_at"],
                        )
                        connection.execute(
                            "UPDATE hierarchies SET status = "
                            "'HANDOFF_READY', updated_at = ? "
                            "WHERE root_id = ?",
                            (at, root_id),
                        )
                        connection.execute(
                            "UPDATE delivery_revisions SET status = "
                            "'HANDOFF_READY', reason = ?, confirmed_by = ?, "
                            "authorized_project_ids_json = ?, "
                            "updated_at = ? WHERE root_id = ? "
                            "AND revision = ?",
                            (
                                "手动开发需求快照（已冻结，未创建 Graph Run）",
                                confirmed_by,
                                canonical_json(authorized_project_ids),
                                at,
                                root_id,
                                delivery_revision,
                            ),
                        )
                    elif existing["status"] == "HANDOFF_READY":
                        at = _commit_timestamp(
                            self.now,
                            existing["updated_at"],
                        )
                        connection.execute(
                            "UPDATE hierarchies SET updated_at = ? "
                            "WHERE root_id = ?",
                            (at, root_id),
                        )
                        connection.execute(
                            "UPDATE delivery_revisions SET confirmed_by = ?, "
                            "authorized_project_ids_json = ?, updated_at = ? "
                            "WHERE root_id = ? AND revision = ?",
                            (
                                confirmed_by,
                                canonical_json(authorized_project_ids),
                                at,
                                root_id,
                                delivery_revision,
                            ),
                        )
                    else:
                        at = timestamp(self.now)
            else:
                if any(
                    value is not None
                    for value in (
                        expected_current_revision,
                        continuity_basis,
                        revision_reason,
                    )
                ):
                    fail(
                        "SCHEDULER_REVISION_CONFLICT",
                        "An initial manual handoff cannot declare a previous "
                        "Delivery revision",
                        rootId=root_id,
                    )
                delivery_revision = 1
                at = timestamp(self.now)
                connection.execute(
                    """
                    INSERT INTO hierarchies(
                        root_id, revision, hierarchy_fingerprint,
                        graph_fingerprint, hierarchy_json, graph_json,
                        status, created_at, updated_at
                    ) VALUES (?, 1, ?, ?, ?, ?, 'HANDOFF_READY', ?, ?)
                    """,
                    (
                        root_id,
                        hierarchy_fingerprint,
                        graph_fingerprint,
                        hierarchy_json,
                        graph_json,
                        at,
                        at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO delivery_revisions(
                        root_id, revision, hierarchy_fingerprint,
                        graph_fingerprint, hierarchy_json, graph_json,
                        status, reason, confirmed_by,
                        authorized_project_ids_json, created_at, updated_at
                    ) VALUES (
                        ?, 1, ?, ?, ?, ?, 'HANDOFF_READY', ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        root_id,
                        hierarchy_fingerprint,
                        graph_fingerprint,
                        hierarchy_json,
                        graph_json,
                        "手动开发需求快照（已冻结，未创建 Graph Run）",
                        confirmed_by,
                        canonical_json(authorized_project_ids),
                        at,
                        at,
                    ),
                )
        self.write_projections(
            root_id,
            preserve_manual_updates=preserve_manual_updates,
        )
        return {
            "rootId": root_id,
            "status": "HANDOFF_READY",
            "deliveryRevision": delivery_revision,
            "previousRevision": previous_revision,
            "recordedAt": at,
        }

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
            self._assert_delivery_requirement_available(
                connection,
                hierarchy,
            )
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
            occupied = connection.execute(
                "SELECT r.root_id, r.status "
                "FROM delivery_workspaces w "
                "JOIN runs r ON r.root_id = w.root_id "
                "WHERE w.workspace_key = ? AND r.root_id != ? "
                "AND r.status NOT IN "
                "('COMPLETED', 'CANCELLED', 'SUPERSEDED') "
                "LIMIT 1",
                (workspace_key, root_id),
            ).fetchone()
            if occupied is not None:
                fail(
                    "SCHEDULER_DELIVERY_WORKSPACE_OCCUPIED",
                    (
                        "An unfinished Delivery already owns this "
                        "workspace; prepare the new Delivery in an "
                        "independent worktree task"
                    ),
                    occupiedRootId=occupied["root_id"],
                    occupiedStatus=occupied["status"],
                    nextAction="CREATE_INDEPENDENT_WORKTREE_TASK",
                    worktreeSetup={
                        "owner": "HOST",
                        "strategy": "HOST_NATIVE_LINKED_WORKTREE",
                        "resumeAction": (
                            "CALL_WORKSPACE_STATUS_IN_NEW_WORKTREE"
                        ),
                        "controllerCreatesWorktree": False,
                    },
                )
            frozen = connection.execute(
                "SELECT status, revision, hierarchy_fingerprint, "
                "graph_fingerprint, updated_at FROM hierarchies "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            existing_run = connection.execute(
                "SELECT 1 FROM runs WHERE root_id = ? LIMIT 1",
                (root_id,),
            ).fetchone()
            if (
                frozen is not None
                and frozen["status"] == "HANDOFF_READY"
                and (
                    frozen["hierarchy_fingerprint"]
                    != hierarchy_fingerprint
                    or frozen["graph_fingerprint"]
                    != graph_fingerprint
                )
            ):
                fail(
                    "SCHEDULER_HANDOFF_CONTROL_STATE_CONFLICT",
                    "Prepared hierarchy differs from the frozen manual "
                    "snapshot",
                    rootId=root_id,
                    recovery=(
                        "Create an explicit manual revision under the same "
                        "Delivery ID, then prepare that exact snapshot"
                    ),
                )
            adopting_manual = (
                frozen is not None
                and frozen["status"] == "HANDOFF_READY"
                and frozen["hierarchy_fingerprint"]
                == hierarchy_fingerprint
                and frozen["graph_fingerprint"] == graph_fingerprint
                and existing_run is None
            )
            if (
                frozen is not None
                and (
                    frozen["status"] == "FROZEN"
                    or (
                        frozen["revision"] != 1
                        and not adopting_manual
                    )
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
            delivery_revision = frozen["revision"] if adopting_manual else 1
            if adopting_manual:
                connection.execute(
                    "UPDATE hierarchies SET status = 'PREPARED', "
                    "updated_at = ? WHERE root_id = ?",
                    (at, root_id),
                )
                connection.execute(
                    "UPDATE delivery_revisions SET status = 'PREPARED', "
                    "updated_at = ? WHERE root_id = ? AND revision = ?",
                    (at, root_id, delivery_revision),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO hierarchies(
                        root_id, revision, hierarchy_fingerprint,
                        graph_fingerprint, hierarchy_json, graph_json,
                        status, created_at, updated_at
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
                        reason = NULL,
                        continuity_basis = NULL,
                        requested_by = NULL,
                        confirmed_by = CASE WHEN
                            delivery_revisions.execution_mode =
                                'automatic_pending'
                            THEN delivery_revisions.confirmed_by
                            ELSE NULL END,
                        authorized_project_ids_json = CASE WHEN
                            delivery_revisions.execution_mode =
                                'automatic_pending'
                            THEN delivery_revisions.authorized_project_ids_json
                            ELSE NULL END,
                        execution_mode = CASE WHEN
                            delivery_revisions.execution_mode =
                                'automatic_pending'
                            THEN delivery_revisions.execution_mode
                            ELSE NULL END,
                        updated_at = excluded.updated_at,
                        frozen_at = NULL,
                        superseded_at = NULL
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
            "deliveryRevision": delivery_revision,
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
        continuity_basis: str,
        requested_by: str,
        workspace_root: str | os.PathLike[str],
    ) -> dict[str, Any]:
        workspace_key = self.workspace_key(workspace_root)
        with self.transaction() as connection:
            self._assert_delivery_requirement_available(
                connection,
                hierarchy,
            )
            row = connection.execute(
                "SELECT * FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if row is None:
                fail(
                    "SCHEDULER_HIERARCHY_MISSING",
                    f"Scheduler hierarchy is missing: {root_id}",
                )
            if row["status"] == "ARCHIVED":
                fail(
                    "SCHEDULER_DELIVERY_ARCHIVED",
                    "An archived Delivery cannot be revised",
                    rootId=root_id,
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
            candidate_row = connection.execute(
                "SELECT * FROM delivery_revisions "
                "WHERE root_id = ? AND revision = ?",
                (root_id, preparing_revision),
            ).fetchone()
            is_reprepare = (
                candidate_row is not None
                and candidate_row["status"] == "PREPARED"
            )
            if (
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
            if candidate_row is not None and not is_reprepare:
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "The next Delivery revision is not a prepared candidate",
                    expectedRevision=preparing_revision,
                    status=candidate_row["status"],
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
            if continuity_basis == "ACTIVE_LOOP_REPLAN" and not any(
                item["failureClass"] == "REPLAN_REQUIRED"
                for item in previous_nodes
            ):
                fail(
                    "SCHEDULER_REVISION_CONTINUITY_REQUIRED",
                    "ACTIVE_LOOP_REPLAN requires a recorded replan outcome",
                )
            carry_forward = self._carriable_task_ids(
                previous_hierarchy,
                hierarchy,
                previous_nodes,
            )
            at = _commit_timestamp(
                self.now,
                max(
                    row["updated_at"],
                    (
                        candidate_row["updated_at"]
                        if candidate_row is not None
                        else row["updated_at"]
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO delivery_revisions(
                    root_id, revision, hierarchy_fingerprint,
                    graph_fingerprint, hierarchy_json, graph_json, status,
                    reason, continuity_basis, requested_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?, ?, ?, ?)
                ON CONFLICT(root_id, revision) DO UPDATE SET
                    hierarchy_fingerprint =
                        excluded.hierarchy_fingerprint,
                    graph_fingerprint = excluded.graph_fingerprint,
                    hierarchy_json = excluded.hierarchy_json,
                    graph_json = excluded.graph_json,
                    status = 'PREPARED',
                    reason = excluded.reason,
                    continuity_basis = excluded.continuity_basis,
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
                    continuity_basis,
                    requested_by,
                    at,
                    at,
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
        return self._freeze(
            root_id,
            expected_delivery_revision=expected_delivery_revision,
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed_by=confirmed_by,
            execution_mode="active",
            graph_started_by=None,
        )

    def freeze_manual_handoff(
        self,
        root_id: str,
        *,
        expected_delivery_revision: int,
        expected_hierarchy_fingerprint: str,
        authorized_project_ids: list[str],
        confirmed_by: str,
        started_by: str,
    ) -> dict[str, Any]:
        return self._freeze(
            root_id,
            expected_delivery_revision=expected_delivery_revision,
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed_by=confirmed_by,
            execution_mode="manual",
            graph_started_by=started_by,
        )

    def _freeze(
        self,
        root_id: str,
        *,
        expected_delivery_revision: int,
        expected_hierarchy_fingerprint: str,
        authorized_project_ids: list[str],
        confirmed_by: str,
        execution_mode: str = "active",
        graph_started_by: str | None = None,
    ) -> dict[str, Any]:
        if execution_mode not in {"active", "manual"}:
            fail(
                "SCHEDULER_EXECUTION_MODE_INVALID",
                "Graph execution mode must be active or manual",
                executionMode=execution_mode,
            )
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
            if row["status"] == "ARCHIVED":
                fail(
                    "SCHEDULER_DELIVERY_ARCHIVED",
                    "An archived Delivery cannot be frozen again",
                    rootId=root_id,
                )
            if (
                not isinstance(expected_delivery_revision, int)
                or isinstance(expected_delivery_revision, bool)
                or expected_delivery_revision < 1
            ):
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "Delivery revision must be a positive integer",
                    expectedRevision=expected_delivery_revision,
                )
            if expected_delivery_revision == row["revision"]:
                revision_row = row
            elif (
                row["status"] == "FROZEN"
                and expected_delivery_revision == row["revision"] + 1
            ):
                revision_row = connection.execute(
                    "SELECT * FROM delivery_revisions "
                    "WHERE root_id = ? AND revision = ?",
                    (root_id, expected_delivery_revision),
                ).fetchone()
                if (
                    revision_row is None
                    or revision_row["status"] != "PREPARED"
                ):
                    fail(
                        "SCHEDULER_REVISION_CONFLICT",
                        "The requested Delivery revision is not prepared",
                        expectedRevision=expected_delivery_revision,
                        actualRevision=row["revision"],
                    )
            else:
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "Delivery revision is not current or next prepared",
                    expectedRevision=expected_delivery_revision,
                    actualRevision=row["revision"],
                    status=row["status"],
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
                    nextAction="CREATE_INDEPENDENT_WORKTREE_TASK",
                    worktreeSetup={
                        "owner": "HOST",
                        "strategy": "HOST_NATIVE_LINKED_WORKTREE",
                        "resumeAction": (
                            "CALL_WORKSPACE_STATUS_IN_NEW_WORKTREE"
                        ),
                        "controllerCreatesWorktree": False,
                    },
                )
            if (
                revision_row["hierarchy_fingerprint"]
                != expected_hierarchy_fingerprint
            ):
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "Hierarchy fingerprint is not current",
                )
            hierarchy, graph = _validated_stored_definition(revision_row)
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
            if (
                row["status"] == "FROZEN"
                and row["revision"] == expected_delivery_revision
            ):
                return self._run_from_connection(connection, root_id)
            at = _commit_timestamp(
                self.now,
                max(row["updated_at"], revision_row["updated_at"]),
            )
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
                if previous_definition is None:
                    fail(
                        "SCHEDULER_REVISION_CONFLICT",
                        "The previous Delivery revision is missing",
                    )
                previous_is_manual = (
                    previous_definition["status"] == "SUPERSEDED"
                    and previous_definition["confirmed_by"] is not None
                    and previous_definition[
                        "authorized_project_ids_json"
                    ]
                    is not None
                    and previous_definition["execution_mode"] is None
                )
                if previous_run is None and not previous_is_manual:
                    fail(
                        "SCHEDULER_REVISION_CONFLICT",
                        "The previous Delivery run is missing",
                    )
                if previous_run is not None:
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
                "UPDATE hierarchies SET revision = ?, "
                "hierarchy_fingerprint = ?, graph_fingerprint = ?, "
                "hierarchy_json = ?, graph_json = ?, status = 'FROZEN', "
                "updated_at = ? WHERE root_id = ?",
                (
                    expected_delivery_revision,
                    revision_row["hierarchy_fingerprint"],
                    revision_row["graph_fingerprint"],
                    revision_row["hierarchy_json"],
                    revision_row["graph_json"],
                    at,
                    root_id,
                ),
            )
            connection.execute(
                "INSERT INTO runs(run_id, root_id, revision, "
                "execution_mode, status, "
                "started_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)",
                (
                    run_id,
                    root_id,
                    expected_delivery_revision,
                    execution_mode,
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
                actor=graph_started_by or "USER",
                operation_id=None,
                payload={
                    "deliveryRevision": expected_delivery_revision,
                    "previousRevision": (
                        expected_delivery_revision - 1
                        if expected_delivery_revision > 1
                        else None
                    ),
                    "authorizedProjectIds": required_project_ids,
                    "executionMode": execution_mode,
                    **(
                        {"startedBy": graph_started_by}
                        if graph_started_by is not None
                        else {}
                    ),
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
                "execution_mode = ?, updated_at = ?, frozen_at = ? "
                "WHERE root_id = ? AND revision = ?",
                (
                    confirmed_by,
                    canonical_json(required_project_ids),
                    execution_mode,
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

    def _run_from_connection(
        self,
        connection: sqlite3.Connection,
        root_id: str,
    ) -> dict[str, Any]:
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
            "executionMode": row["execution_mode"],
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
        if row["host_capacity_reset_at"] is not None:
            result["hostCapacity"] = {
                "status": "OPEN",
                "capacityKey": row["host_capacity_key"],
                "resetAt": row["host_capacity_reset_at"],
                "reportedAt": row["host_capacity_reported_at"],
                "reason": row["host_capacity_reason"],
            }
        git_binding = hierarchy["delivery"].get("gitBinding")
        if git_binding is not None:
            result["gitBinding"] = git_binding
        result["projectScopes"] = hierarchy["delivery"].get(
            "projectScopes",
            [],
        )
        return result

    def run(
        self,
        root_id: str,
    ) -> dict[str, Any]:
        with self.read() as connection:
            return self._run_from_connection(connection, root_id)

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
                    "continuityBasis": row["continuity_basis"],
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
    def expire_dispatch_reservations(
        connection: sqlite3.Connection,
        *,
        at: str,
    ) -> None:
        connection.execute(
            "UPDATE dispatch_reservations SET status = 'EXPIRED' "
            "WHERE status = 'RESERVED' AND expires_at < ?",
            (at,),
        )

    def expire_dispatch_reservation_now(
        self,
        reservation_id: str,
        *,
        root_id: str,
        host_adapter_id: str,
        failure_code: str,
    ) -> bool:
        """Release one failed host-start reservation without a TTL wait."""

        with self.transaction() as connection:
            reservation = connection.execute(
                "SELECT d.* FROM dispatch_reservations d "
                "JOIN node_runs n ON n.run_id = d.run_id "
                "AND n.node_id = d.node_id AND n.attempt = d.attempt "
                "LEFT JOIN host_receiver_identities h "
                "ON h.reservation_id = d.reservation_id "
                "WHERE d.reservation_id = ? AND d.root_id = ? "
                "AND d.agent_id = ? "
                "AND d.status = 'RESERVED' AND n.status = 'READY' "
                "AND h.attestation_digest IS NULL LIMIT 1",
                (reservation_id, root_id, host_adapter_id),
            ).fetchone()
            if reservation is None:
                return False
            updated = connection.execute(
                "UPDATE dispatch_reservations SET status = 'EXPIRED' "
                "WHERE reservation_id = ? AND root_id = ? "
                "AND run_id = ? AND node_id = ? AND attempt = ? "
                "AND decision_fingerprint = ? AND status = 'RESERVED'",
                (
                    reservation_id,
                    root_id,
                    reservation["run_id"],
                    reservation["node_id"],
                    reservation["attempt"],
                    reservation["decision_fingerprint"],
                ),
            )
            if updated.rowcount != 1:
                return False
            self.append_event(
                connection,
                run_id=reservation["run_id"],
                node_id=reservation["node_id"],
                attempt=reservation["attempt"],
                event_type="DISPATCH_RECEIVER_START_FAILED",
                actor=host_adapter_id,
                operation_id=None,
                payload={
                    "dispatchReservationId": reservation_id,
                    "hostAdapterId": host_adapter_id,
                    "graphFingerprint": reservation[
                        "graph_fingerprint"
                    ],
                    "dispatchDecisionFingerprint": reservation[
                        "decision_fingerprint"
                    ],
                    "failureCode": failure_code,
                    "reservationReleased": True,
                },
                at=timestamp(self.now),
            )
        return True

    def active_dispatch_reservations(
        self,
        connection: sqlite3.Connection,
        *,
        at: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT d.*, h.hierarchy_json, h.graph_json,
                   h.hierarchy_fingerprint,
                   h.graph_fingerprint AS stored_graph_fingerprint
            FROM dispatch_reservations d
            JOIN runs r ON r.run_id = d.run_id
            JOIN delivery_revisions h ON h.root_id = r.root_id
                AND h.revision = r.revision
            WHERE (
                    (d.status = 'RESERVED' AND d.expires_at >= ?)
                    OR (
                        d.status = 'CLAIMED'
                        AND EXISTS (
                            SELECT 1 FROM node_runs n
                            WHERE n.run_id = d.run_id
                              AND n.node_id = d.node_id
                              AND n.attempt = d.attempt
                              AND n.status = 'CLAIMED'
                              AND n.lease_expires_at IS NOT NULL
                              AND n.lease_expires_at >= ?
                        )
                    )
                )
                AND r.status NOT IN
                    ('COMPLETED', 'CANCELLED', 'SUPERSEDED')
            ORDER BY d.root_id, d.node_id
            """,
            (at, at),
        ).fetchall()
        result: list[dict[str, Any]] = []
        graph_cache: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            cache_key = (row["root_id"], row["run_id"])
            graph = graph_cache.get(cache_key)
            if graph is None:
                _, graph = _validated_stored_definition(row)
                graph_cache[cache_key] = graph
            definition = next(
                (
                    node
                    for node in graph["nodes"]
                    if node["id"] == row["node_id"]
                ),
                None,
            )
            if definition is None or definition["loop"] is None:
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "Reserved dispatch Loop is missing from its Graph",
                )
            result.append(
                {
                    "dispatchReservationId": row["reservation_id"],
                    "runId": row["run_id"],
                    "rootId": row["root_id"],
                    "nodeId": row["node_id"],
                    "attempt": row["attempt"],
                    "agentId": row["agent_id"],
                    "graphFingerprint": row["graph_fingerprint"],
                    "decisionFingerprint": row[
                        "decision_fingerprint"
                    ],
                    "reservedAt": row["reserved_at"],
                    "reservationExpiresAt": row["expires_at"],
                    "resourceClaims": definition["loop"][
                        "resourceClaims"
                    ],
                }
            )
        return result

    def issue_receiver_attestation(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        root_id: str,
        node_id: str,
        attempt: int,
        receiver_context_id: str,
        parent_context_id: str,
        host_adapter_id: str,
        reservation_id: str | None,
        at: str,
    ) -> str:
        attestation_id = str(uuid.uuid4())
        expires_at = (
            datetime.fromisoformat(at.replace("Z", "+00:00"))
            + timedelta(seconds=RECEIVER_ATTESTATION_SECONDS)
        ).isoformat().replace("+00:00", "Z")
        self._assert_receiver_root(
            connection,
            run_id=run_id,
            node_id=node_id,
            attempt=attempt,
            host_adapter_id=host_adapter_id,
            parent_context_id=parent_context_id,
            at=at,
            commit=False,
        )
        connection.execute(
            "UPDATE receiver_attestations SET status = 'SUPERSEDED' "
            "WHERE run_id = ? AND node_id = ? AND attempt = ? "
            "AND receiver_context_id = ? AND status = 'ISSUED'",
            (run_id, node_id, attempt, receiver_context_id),
        )
        connection.execute(
            "INSERT INTO receiver_attestations("
            "attestation_id, run_id, root_id, node_id, attempt, "
            "receiver_context_id, parent_context_id, host_adapter_id, "
            "reservation_id, status, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ISSUED', ?, ?)",
            (
                attestation_id,
                run_id,
                root_id,
                node_id,
                attempt,
                receiver_context_id,
                parent_context_id,
                host_adapter_id,
                reservation_id,
                at,
                expires_at,
            ),
        )
        return attestation_id

    def _assert_receiver_root(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        node_id: str,
        attempt: int,
        host_adapter_id: str,
        parent_context_id: str,
        at: str,
        commit: bool,
    ) -> None:
        receiver_root = connection.execute(
            "SELECT * FROM run_receiver_roots WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if receiver_root is None:
            if commit:
                connection.execute(
                    "INSERT INTO run_receiver_roots("
                    "run_id, host_adapter_id, orchestrator_context_id, "
                    "created_at) VALUES (?, ?, ?, ?)",
                    (run_id, host_adapter_id, parent_context_id, at),
                )
            return

        same_adapter = receiver_root["host_adapter_id"] == host_adapter_id
        same_parent = (
            receiver_root["orchestrator_context_id"] == parent_context_id
        )
        if same_adapter and same_parent:
            return
        rotation_reason: str | None = None
        if same_adapter:
            if self._worker_lost_retry_allows_receiver_root_rotation(
                connection,
                run_id=run_id,
                node_id=node_id,
                attempt=attempt,
                parent_context_id=parent_context_id,
                at=at,
            ):
                rotation_reason = "WORKER_LOST_RETRY"
            elif self._idle_frontier_allows_receiver_root_rotation(
                connection,
                run_id=run_id,
                node_id=node_id,
                attempt=attempt,
                parent_context_id=parent_context_id,
                at=at,
            ):
                rotation_reason = "IDLE_FRONTIER_HANDOFF"
        if rotation_reason is None:
            fail(
                "SCHEDULER_RECEIVER_PARENT_UNTRUSTED",
                "Receiver attestations must originate from the run's "
                "host-attested orchestrator context",
                expectedHostAdapterId=receiver_root["host_adapter_id"],
                suppliedHostAdapterId=host_adapter_id,
                expectedOrchestratorContextId=(
                    receiver_root["orchestrator_context_id"]
                ),
                suppliedParentContextId=parent_context_id,
            )
        if not commit:
            return

        previous_context_id = receiver_root["orchestrator_context_id"]
        updated = connection.execute(
            "UPDATE run_receiver_roots SET host_adapter_id = ?, "
            "orchestrator_context_id = ? WHERE run_id = ? "
            "AND host_adapter_id = ? AND orchestrator_context_id = ?",
            (
                host_adapter_id,
                parent_context_id,
                run_id,
                receiver_root["host_adapter_id"],
                previous_context_id,
            ),
        )
        if updated.rowcount != 1:
            fail(
                "SCHEDULER_RECEIVER_PARENT_UNTRUSTED",
                "The receiver orchestrator root changed concurrently",
            )
        self.append_event(
            connection,
            run_id=run_id,
            node_id=node_id,
            attempt=attempt,
            event_type="RECEIVER_ROOT_ROTATED",
            actor=host_adapter_id,
            operation_id=None,
            payload={
                "reason": rotation_reason,
                "previousOrchestratorContextDigest": hashlib.sha256(
                    previous_context_id.encode("utf-8")
                ).hexdigest(),
                "orchestratorContextDigest": hashlib.sha256(
                    parent_context_id.encode("utf-8")
                ).hexdigest(),
            },
            at=at,
        )

    @staticmethod
    def _idle_frontier_allows_receiver_root_rotation(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        node_id: str,
        attempt: int,
        parent_context_id: str,
        at: str,
    ) -> bool:
        current = connection.execute(
            "SELECT status FROM node_runs WHERE run_id = ? "
            "AND node_id = ? AND attempt = ?",
            (run_id, node_id, attempt),
        ).fetchone()
        if current is None or current["status"] != "READY":
            return False
        completed_loop = connection.execute(
            "SELECT 1 FROM graph_events WHERE run_id = ? "
            "AND event_type = 'LOOP_SUCCEEDED' LIMIT 1",
            (run_id,),
        ).fetchone()
        if completed_loop is None:
            return False
        active_claim = connection.execute(
            "SELECT 1 FROM node_runs WHERE run_id = ? "
            "AND status = 'CLAIMED' LIMIT 1",
            (run_id,),
        ).fetchone()
        if active_claim is not None:
            return False
        active_claude_attestation = connection.execute(
            "SELECT 1 FROM receiver_attestations WHERE run_id = ? "
            "AND status = 'ISSUED' AND expires_at >= ? "
            "AND parent_context_id != ? LIMIT 1",
            (run_id, at, parent_context_id),
        ).fetchone()
        active_codex_identity = connection.execute(
            "SELECT 1 FROM host_receiver_identities WHERE run_id = ? "
            "AND status = 'ISSUED' AND expires_at >= ? "
            "AND parent_context_id != ? LIMIT 1",
            (run_id, at, parent_context_id),
        ).fetchone()
        return (
            active_claude_attestation is None
            and active_codex_identity is None
        )

    @staticmethod
    def _worker_lost_retry_allows_receiver_root_rotation(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        node_id: str,
        attempt: int,
        parent_context_id: str,
        at: str,
    ) -> bool:
        if attempt <= 1:
            return False
        current = connection.execute(
            "SELECT status FROM node_runs WHERE run_id = ? "
            "AND node_id = ? AND attempt = ?",
            (run_id, node_id, attempt),
        ).fetchone()
        previous = connection.execute(
            "SELECT status, failure_class FROM node_runs WHERE run_id = ? "
            "AND node_id = ? AND attempt = ?",
            (run_id, node_id, attempt - 1),
        ).fetchone()
        retry_event = connection.execute(
            "SELECT payload_json FROM graph_events WHERE run_id = ? "
            "AND node_id = ? AND attempt = ? "
            "AND event_type = 'LOOP_RETRY_SCHEDULED' "
            "ORDER BY event_id DESC LIMIT 1",
            (run_id, node_id, attempt),
        ).fetchone()
        if (
            current is None
            or current["status"] != "READY"
            or previous is None
            or previous["status"] != "BLOCKED"
            or previous["failure_class"] != "WORKER_LOST"
            or retry_event is None
        ):
            return False
        try:
            retry_payload = json.loads(retry_event["payload_json"])
        except (TypeError, ValueError):
            return False
        if (
            retry_payload.get("failureClass") != "WORKER_LOST"
            or retry_payload.get("previousAttempt") != attempt - 1
        ):
            return False
        active_claim = connection.execute(
            "SELECT 1 FROM node_runs WHERE run_id = ? "
            "AND status = 'CLAIMED' LIMIT 1",
            (run_id,),
        ).fetchone()
        if active_claim is not None:
            return False
        active_claude_attestation = connection.execute(
            "SELECT 1 FROM receiver_attestations WHERE run_id = ? "
            "AND status = 'ISSUED' AND expires_at >= ? "
            "AND parent_context_id != ? LIMIT 1",
            (run_id, at, parent_context_id),
        ).fetchone()
        active_codex_identity = connection.execute(
            "SELECT 1 FROM host_receiver_identities WHERE run_id = ? "
            "AND status = 'ISSUED' AND expires_at >= ? "
            "AND parent_context_id != ? LIMIT 1",
            (run_id, at, parent_context_id),
        ).fetchone()
        return (
            active_claude_attestation is None
            and active_codex_identity is None
        )

    @staticmethod
    def issue_host_receiver_identity(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        root_id: str,
        node_id: str,
        attempt: int,
        reservation_id: str,
        host_adapter_id: str,
        agent_id: str,
        receiver_context_id: str,
        parent_context_id: str,
        at: str,
    ) -> str:
        """Issue identity evidence from a native subagent lifecycle hook."""

        attestation_id = secrets.token_hex(32)
        attestation_digest = hashlib.sha256(
            attestation_id.encode("utf-8")
        ).hexdigest()
        expires_at = (
            datetime.fromisoformat(at.replace("Z", "+00:00"))
            + timedelta(seconds=RECEIVER_ATTESTATION_SECONDS)
        ).isoformat().replace("+00:00", "Z")
        connection.execute(
            "UPDATE host_receiver_identities SET status = 'SUPERSEDED' "
            "WHERE run_id = ? AND host_adapter_id = ? "
            "AND receiver_context_id = ? "
            "AND status = 'ISSUED'",
            (run_id, host_adapter_id, receiver_context_id),
        )
        connection.execute(
            "INSERT INTO host_receiver_identities("
            "attestation_digest, run_id, root_id, node_id, attempt, "
            "reservation_id, host_adapter_id, agent_id, "
            "receiver_context_id, parent_context_id, status, created_at, "
            "expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ISSUED', ?, ?)",
            (
                attestation_digest,
                run_id,
                root_id,
                node_id,
                attempt,
                reservation_id,
                host_adapter_id,
                agent_id,
                receiver_context_id,
                parent_context_id,
                at,
                expires_at,
            ),
        )
        return attestation_id

    def consume_receiver_attestation(
        self,
        connection: sqlite3.Connection,
        *,
        attestation_id: str,
        run_id: str,
        root_id: str,
        node_id: str,
        attempt: int,
        receiver_context_id: str,
        host_adapter_id: str,
        agent_id: str,
        reservation_id: str | None,
        operation_id: str,
        at: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM receiver_attestations WHERE attestation_id = ?",
            (attestation_id,),
        ).fetchone()
        if row is None:
            attestation_digest = hashlib.sha256(
                attestation_id.encode("utf-8")
            ).hexdigest()
            identity = connection.execute(
                "SELECT * FROM host_receiver_identities "
                "WHERE attestation_digest = ?",
                (attestation_digest,),
            ).fetchone()
            if identity is None:
                fail(
                    "SCHEDULER_RECEIVER_ATTESTATION_MISSING",
                    "The host-issued receiver attestation does not exist",
                )
            if identity["status"] != "ISSUED":
                fail(
                    "SCHEDULER_RECEIVER_ATTESTATION_CONSUMED",
                    "The host-issued receiver attestation is no longer active",
                    attestationStatus=identity["status"],
                )
            if identity["expires_at"] < at:
                fail(
                    "SCHEDULER_RECEIVER_ATTESTATION_EXPIRED",
                    "The host-issued receiver attestation expired",
                )
            reservation = (
                connection.execute(
                    "SELECT * FROM dispatch_reservations "
                    "WHERE reservation_id = ?",
                    (reservation_id,),
                ).fetchone()
                if reservation_id is not None
                else None
            )
            if (
                identity["run_id"] != run_id
                or identity["root_id"] != root_id
                or identity["node_id"] != node_id
                or identity["attempt"] != attempt
                or identity["reservation_id"] != reservation_id
                or identity["host_adapter_id"] != host_adapter_id
                or identity["agent_id"] != agent_id
                or identity["receiver_context_id"]
                != receiver_context_id
                or reservation is None
                or reservation["status"] != "RESERVED"
                or reservation["expires_at"] < at
                or reservation["run_id"] != run_id
                or reservation["root_id"] != root_id
                or reservation["node_id"] != node_id
                or reservation["attempt"] != attempt
                or reservation["agent_id"] != agent_id
            ):
                fail(
                    "SCHEDULER_RECEIVER_ATTESTATION_MISMATCH",
                    "The receiver attestation is not bound to this claim",
                )
            self._assert_receiver_root(
                connection,
                run_id=run_id,
                node_id=node_id,
                attempt=attempt,
                host_adapter_id=host_adapter_id,
                parent_context_id=identity["parent_context_id"],
                at=at,
                commit=True,
            )
            connection.execute(
                "UPDATE host_receiver_identities SET status = 'CONSUMED', "
                "consumed_at = ?, operation_id = ? "
                "WHERE attestation_digest = ? AND status = 'ISSUED'",
                (at, operation_id, attestation_digest),
            )
            return {
                "parentContextId": identity["parent_context_id"],
                "hostAdapterId": identity["host_adapter_id"],
            }
        if row["status"] != "ISSUED":
            fail(
                "SCHEDULER_RECEIVER_ATTESTATION_CONSUMED",
                "The host-issued receiver attestation is no longer active",
                attestationStatus=row["status"],
            )
        if row["expires_at"] is None or row["expires_at"] < at:
            fail(
                "SCHEDULER_RECEIVER_ATTESTATION_EXPIRED",
                "The host-issued receiver attestation expired",
            )
        if (
            row["run_id"] != run_id
            or row["root_id"] != root_id
            or row["node_id"] != node_id
            or row["attempt"] != attempt
            or row["receiver_context_id"] != receiver_context_id
            or row["host_adapter_id"] != host_adapter_id
            or row["reservation_id"] != reservation_id
        ):
            fail(
                "SCHEDULER_RECEIVER_ATTESTATION_MISMATCH",
                "The receiver attestation is not bound to this claim",
            )
        self._assert_receiver_root(
            connection,
            run_id=run_id,
            node_id=node_id,
            attempt=attempt,
            host_adapter_id=host_adapter_id,
            parent_context_id=row["parent_context_id"],
            at=at,
            commit=True,
        )
        connection.execute(
            "UPDATE receiver_attestations SET status = 'CONSUMED', "
            "consumed_at = ?, operation_id = ? "
            "WHERE attestation_id = ? AND status = 'ISSUED'",
            (at, operation_id, attestation_id),
        )
        return {
            "parentContextId": row["parent_context_id"],
            "hostAdapterId": row["host_adapter_id"],
        }

    @staticmethod
    def open_host_capacity_breaker(
        connection: sqlite3.Connection,
        *,
        agent_id: str,
        at: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM host_capacity_breakers "
            "WHERE agent_id = ? AND status = 'OPEN' "
            "AND reset_at > ? ORDER BY reset_at LIMIT 1",
            (agent_id, at),
        ).fetchone()
        if row is None:
            return None
        return {
            "capacityKey": row["capacity_key"],
            "hostAdapterId": row["host_adapter_id"],
            "agentId": row["agent_id"],
            "resetAt": row["reset_at"],
            "reportedAt": row["reported_at"],
            "reason": row["reason"],
        }

    def reserve_dispatch_assignments(
        self,
        *,
        root_id: str,
        graph_fingerprint: str,
        assignments: list[dict[str, Any]],
        agent_slot_limits: dict[str, int],
        orchestrator_slot_limit: int | None = None,
        reservation_seconds: int,
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            hierarchy_row = connection.execute(
                "SELECT * FROM hierarchies WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if hierarchy_row is None:
                fail(
                    "SCHEDULER_HIERARCHY_MISSING",
                    f"Scheduler hierarchy is missing: {root_id}",
                )
            if hierarchy_row["graph_fingerprint"] != graph_fingerprint:
                fail(
                    "SCHEDULER_GRAPH_FINGERPRINT_MISMATCH",
                    "The expected Graph fingerprint is stale",
                )
            _, graph = _validated_stored_definition(hierarchy_row)
            run = connection.execute(
                "SELECT * FROM runs WHERE root_id = ? AND revision = ?",
                (root_id, hierarchy_row["revision"]),
            ).fetchone()
            if run is None:
                fail(
                    "SCHEDULER_RUN_MISSING",
                    f"Scheduler run is missing: {root_id}",
                )
            at = _commit_timestamp(self.now, run["updated_at"])
            expires_at = (
                datetime.fromisoformat(at.replace("Z", "+00:00"))
                + timedelta(seconds=reservation_seconds)
            ).isoformat().replace("+00:00", "Z")
            self.expire_dispatch_reservations(connection, at=at)
            active = self.active_dispatch_reservations(
                connection,
                at=at,
            )
            active_by_node = {
                (
                    item["runId"],
                    item["nodeId"],
                    item["attempt"],
                ): item
                for item in active
            }
            reserved_agent_slots: dict[str, int] = {}
            for item in active:
                agent_id = item.get("agentId")
                if isinstance(agent_id, str):
                    reserved_agent_slots[agent_id] = (
                        reserved_agent_slots.get(agent_id, 0) + 1
                    )
            occupied = [
                *self.claimed_resource_reservations(
                    connection,
                    at=at,
                ),
                *active,
            ]
            states = {
                item["nodeId"]: item
                for item in self.latest_nodes(
                    connection,
                    run["run_id"],
                )
            }
            definitions = {
                item["id"]: item
                for item in graph["nodes"]
            }
            accepted: dict[str, dict[str, Any]] = {}
            rejected: dict[str, dict[str, Any]] = {}
            for assignment in assignments:
                node_id = assignment["nodeId"]
                agent_id = assignment["receiverAgentId"]
                state = states.get(node_id)
                definition = definitions.get(node_id)
                key = (
                    run["run_id"],
                    node_id,
                    state["attempt"] if state is not None else -1,
                )
                existing = active_by_node.get(key)
                if existing is not None:
                    rejected[node_id] = {
                        "code": "DISPATCH_ALREADY_RESERVED",
                        "message": (
                            "Another dispatcher already reserved this "
                            "Loop for host Agent creation."
                        ),
                        **existing,
                    }
                    continue
                if (
                    orchestrator_slot_limit is not None
                    and len(active) + len(accepted)
                    >= orchestrator_slot_limit
                ):
                    rejected[node_id] = {
                        "code": "ORCHESTRATOR_CAPACITY_RESERVED",
                        "message": (
                            "The configured central orchestrator "
                            "concurrency limit is already occupied."
                        ),
                        "maxConcurrentExecutors": (
                            orchestrator_slot_limit
                        ),
                    }
                    continue
                if reserved_agent_slots.get(agent_id, 0) >= (
                    agent_slot_limits.get(agent_id, 0)
                ):
                    rejected[node_id] = {
                        "code": "DISPATCH_AGENT_CAPACITY_RESERVED",
                        "message": (
                            "Another Delivery already reserved the "
                            "remaining host-native Agent slot."
                        ),
                        "agentId": agent_id,
                    }
                    continue
                if (
                    state is None
                    or state["status"] != "READY"
                    or state.get("manualHandoffEnabled") is True
                    or definition is None
                    or definition["loop"] is None
                ):
                    rejected[node_id] = {
                        "code": (
                            "DISPATCH_MANUAL_HANDOFF_ENABLED"
                            if state is not None
                            and state.get("manualHandoffEnabled") is True
                            else "DISPATCH_RESERVATION_NOT_READY"
                        ),
                        "message": (
                            "The TASK is reserved for manual receipt."
                            if state is not None
                            and state.get("manualHandoffEnabled") is True
                            else "The Loop is no longer ready for dispatch."
                        ),
                    }
                    continue
                conflict = next(
                    (
                        item
                        for item in occupied
                        if resource_claims_overlap(
                            definition["loop"]["resourceClaims"],
                            item["resourceClaims"],
                        )
                    ),
                    None,
                )
                if conflict is not None:
                    rejected[node_id] = {
                        "code": "DISPATCH_RESERVATION_CONFLICT",
                        "message": (
                            "A claimed or dispatch-reserved Loop already "
                            "holds an overlapping resource."
                        ),
                        "conflictingRootId": conflict["rootId"],
                        "conflictingNodeId": conflict["nodeId"],
                    }
                    continue
                reservation_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO dispatch_reservations(
                        reservation_id, run_id, root_id, node_id, attempt,
                        agent_id,
                        graph_fingerprint, decision_fingerprint, status,
                        reserved_at, expires_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?
                    )
                    """,
                    (
                        reservation_id,
                        run["run_id"],
                        root_id,
                        node_id,
                        state["attempt"],
                        agent_id,
                        graph_fingerprint,
                        assignment["decisionFingerprint"],
                        at,
                        expires_at,
                    ),
                )
                reservation = {
                    "dispatchReservationId": reservation_id,
                    "reservationExpiresAt": expires_at,
                }
                accepted[node_id] = reservation
                reserved_agent_slots[agent_id] = (
                    reserved_agent_slots.get(agent_id, 0) + 1
                )
                occupied.append(
                    {
                        "rootId": root_id,
                        "nodeId": node_id,
                        "resourceClaims": definition["loop"][
                            "resourceClaims"
                        ],
                    }
                )
        return {
            "accepted": accepted,
            "rejected": rejected,
        }

    def consume_dispatch_reservation(
        self,
        connection: sqlite3.Connection,
        *,
        reservation_id: str,
        run_id: str,
        node_id: str,
        attempt: int,
        graph_fingerprint: str,
        decision_fingerprint: str,
        operation_id: str,
        at: str,
    ) -> None:
        row = connection.execute(
            "SELECT * FROM dispatch_reservations "
            "WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            fail(
                "SCHEDULER_DISPATCH_RESERVATION_MISSING",
                "The automatic dispatch reservation does not exist",
            )
        if row["status"] != "RESERVED" or row["expires_at"] < at:
            fail(
                "SCHEDULER_DISPATCH_RESERVATION_EXPIRED",
                "The automatic dispatch reservation is no longer active",
                reservationExpiresAt=row["expires_at"],
            )
        if (
            row["run_id"] != run_id
            or row["node_id"] != node_id
            or row["attempt"] != attempt
            or row["graph_fingerprint"] != graph_fingerprint
            or row["decision_fingerprint"] != decision_fingerprint
        ):
            fail(
                "SCHEDULER_DISPATCH_RESERVATION_MISMATCH",
                "The reservation is not bound to this dispatch decision",
            )
        connection.execute(
            "UPDATE dispatch_reservations SET status = 'CLAIMED', "
            "claimed_at = ?, operation_id = ? "
            "WHERE reservation_id = ? AND status = 'RESERVED'",
            (at, operation_id, reservation_id),
        )

    @staticmethod
    def latest_nodes(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> list[dict[str, Any]]:
        executor_metadata: dict[tuple[str, int], dict[str, Any]] = {}
        first_heartbeats: dict[tuple[str, int], str] = {}
        latest_progress: dict[tuple[str, int], dict[str, Any]] = {}
        manual_handoffs: dict[str, dict[str, Any]] = {}
        handoff_rows = connection.execute(
            "SELECT node_id, actor, operation_id, payload_json, recorded_at "
            "FROM graph_events WHERE run_id = ? "
            "AND event_type = 'LOOP_MANUAL_HANDOFF_ENABLED' "
            "ORDER BY event_id",
            (run_id,),
        ).fetchall()
        for handoff_row in handoff_rows:
            payload = json.loads(handoff_row["payload_json"])
            manual_handoffs[handoff_row["node_id"]] = {
                "confirmedBy": handoff_row["actor"],
                "reason": payload.get("reason"),
                "handoffRequestId": handoff_row["operation_id"],
                "enabledAt": handoff_row["recorded_at"],
            }
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
        heartbeat_rows = connection.execute(
            """
            SELECT node_id, attempt, MIN(recorded_at) AS first_heartbeat_at
            FROM graph_events
            WHERE run_id = ? AND event_type = 'LOOP_HEARTBEAT'
            GROUP BY node_id, attempt
            """,
            (run_id,),
        ).fetchall()
        for heartbeat_row in heartbeat_rows:
            first_heartbeats[
                (heartbeat_row["node_id"], heartbeat_row["attempt"])
            ] = heartbeat_row["first_heartbeat_at"]
        progress_rows = connection.execute(
            """
            SELECT event_id, node_id, attempt, payload_json, recorded_at
            FROM graph_events
            WHERE run_id = ? AND event_type = 'LOOP_PROGRESS_REPORTED'
            ORDER BY event_id
            """,
            (run_id,),
        ).fetchall()
        for progress_row in progress_rows:
            payload = json.loads(progress_row["payload_json"])
            latest_progress[
                (progress_row["node_id"], progress_row["attempt"])
            ] = {
                **(payload if isinstance(payload, dict) else {}),
                "eventId": progress_row["event_id"],
                "reportedAt": progress_row["recorded_at"],
            }
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
                "actualModelId": executor.get("actualModelId"),
                "actualModelSource": executor.get(
                    "actualModelSource"
                ),
                "receiverContextId": (
                    executor.get("receiverContextId") or row["owner"]
                ),
                "dispatchMode": executor.get("dispatchMode"),
                "dispatchTransport": executor.get(
                    "dispatchTransport"
                ),
                "dispatchReservationId": executor.get(
                    "dispatchReservationId"
                ),
                "dispatchDecisionFingerprint": executor.get(
                    "dispatchDecisionFingerprint"
                ),
                "operationId": row["operation_id"],
                "claimedAt": row["claimed_at"],
                "lastHeartbeatAt": row["last_heartbeat_at"],
                "firstHeartbeatAt": first_heartbeats.get(
                    (row["node_id"], row["attempt"])
                ),
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
                "progress": latest_progress.get(
                    (row["node_id"], row["attempt"])
                ),
                "manualHandoffEnabled": (
                    row["node_id"] in manual_handoffs
                ),
                "manualTaskHandoff": manual_handoffs.get(
                    row["node_id"]
                ),
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
            anchor = None
            if after_event_id > 0:
                anchor = connection.execute(
                    "SELECT * FROM graph_events WHERE run_id = ? "
                    "AND event_id <= ? ORDER BY event_id DESC LIMIT 1",
                    (run["run_id"], after_event_id),
                ).fetchone()
            rows = connection.execute(
                "SELECT * FROM graph_events WHERE run_id = ? "
                "AND event_id > ? ORDER BY event_id LIMIT ?",
                (run["run_id"], after_event_id, limit),
            ).fetchall()
        previous_hash: str | None = None
        if anchor is not None:
            anchor_material = _event_material(anchor)
            if fingerprint(anchor_material) != anchor["event_hash"]:
                fail(
                    "SCHEDULER_EVENT_CHAIN_INVALID",
                    "Stored scheduler event chain changed",
                )
            previous_hash = anchor["event_hash"]
        result: list[dict[str, Any]] = []
        for row in rows:
            material = _event_material(row)
            if (
                row["previous_hash"] != previous_hash
                or fingerprint(material) != row["event_hash"]
            ):
                fail(
                    "SCHEDULER_EVENT_CHAIN_INVALID",
                    "Stored scheduler event chain changed",
                )
            previous_hash = row["event_hash"]
            result.append(
                {
                    "eventId": row["event_id"],
                    **material,
                    "eventHash": row["event_hash"],
                }
            )
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

    def write_projections(
        self,
        root_id: str,
        *,
        preserve_manual_updates: bool = True,
        refresh_workspace_overview: bool = True,
    ) -> None:
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
            if run is not None:
                run = attach_progress_monitor(
                    run,
                    definition["graph"],
                    observed_at=timestamp(self.now),
                )
            projection_root = safe_path(self.control_root, root_id)
            revision_history = self.revision_history(root_id)
            documents = render_projection_documents(
                definition,
                run,
                revision_history,
            )
            manual_snapshot = (
                definition["status"] == "HANDOFF_READY" and run is None
                and preserve_manual_updates
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
                target = projection_root / filename
                preserve_manual_update = (
                    manual_snapshot
                    and filename in MANUAL_WRITABLE_PROJECTIONS
                    and target.is_file()
                    and not target.is_symlink()
                )
                if not preserve_manual_update:
                    atomic_write(target, content)
            work_item_root = safe_path(
                projection_root,
                WORK_ITEM_DIRECTORY,
            )
            work_item_documents = render_work_item_projection_documents(
                definition,
                run,
            )
            preserved_work_item_documents: dict[str, bytes] = {}
            if manual_snapshot:
                for filename in work_item_documents:
                    if (
                        Path(filename).name
                        not in MANUAL_WRITABLE_PROJECTIONS
                    ):
                        continue
                    target = work_item_root / filename
                    if target.is_file() and not target.is_symlink():
                        preserved_work_item_documents[filename] = (
                            read_regular_file(work_item_root, filename)
                        )

            def populate_work_items(staging: Path) -> None:
                for filename, content in work_item_documents.items():
                    atomic_write(
                        staging / filename,
                        preserved_work_item_documents.get(
                            filename,
                            content,
                        ),
                    )

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
            if refresh_workspace_overview:
                self._write_workspace_overview()

    def _write_workspace_overview(self) -> None:
        atomic_write(
            safe_path(self.control_root, "overview.md"),
            render_workspace_overview(
                self._workspace_projection_sources()
            ),
        )

    def write_workspace_overview(self) -> None:
        """Refresh the cross-Delivery overview without coupling projections."""

        with exclusive_file_lock(self.lock_path):
            self._write_workspace_overview()

    def _workspace_projection_sources(self) -> list[dict[str, Any]]:
        """Load unarchived Delivery summaries for the root overview."""

        with self.read() as connection:
            rows = connection.execute(
                "SELECT * FROM hierarchies WHERE status != 'ARCHIVED' "
                "ORDER BY updated_at DESC, root_id"
            ).fetchall()
            sources: list[dict[str, Any]] = []
            for row in rows:
                try:
                    hierarchy, graph = _validated_stored_definition(row)
                except GatedLoopError as error:
                    sources.append(
                        {
                            "rootId": row["root_id"],
                            "status": "STATE_INVALID",
                            "createdAt": row["created_at"],
                            "updatedAt": row["updated_at"],
                            "stateError": {
                                "code": error.code,
                                "message": error.message,
                            },
                        }
                    )
                    continue
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
    "WORKTREE_SETUP_HEARTBEAT_SECONDS",
    "WORKTREE_SETUP_LEASE_SECONDS",
    "WORKTREE_SETUP_POLL_SECONDS",
    "timestamp",
)
