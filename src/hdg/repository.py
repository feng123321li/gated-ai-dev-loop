from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import GatedLoopError, fail
from .fs_safe import (
    exclusive_file_lock,
)
from .graph_model import (
    compile_delivery_graph,
    graph_fingerprint,
    validate_delivery_graph,
)
from .jsonio import fingerprint
from .model_core import validate_hierarchy_definition
from .repository_dispatch import DeliveryDispatchStore
from .repository_events import DeliveryEventStore
from .repository_execution_setup import (
    DeliveryExecutionSetupStore,
)
from .repository_hierarchies import DeliveryHierarchyStore
from .repository_projections import (
    MANUAL_WRITABLE_PROJECTIONS,
    DeliveryProjectionStore,
)
from .repository_workspaces import DeliveryWorkspaceStore
from .storage_schema import (
    SCHEDULER_STATE_CONTRACT,
    ensure_compatible_scheduler_storage,
    initialize_scheduler_storage,
    verify_scheduler_state_contract,
)
from .workspace_identity import (
    workspace_identity,
)


GOVERNANCE_DIRECTORY = ".layered-delivery"
DATABASE_FILE = "scheduler.db"
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
        # Resource caps protect newly submitted payloads. State written under
        # this same scheduler contract predates those caps and must remain
        # readable; fingerprints and the canonical equality check below still
        # enforce its integrity and shape.
        normalized = validate_hierarchy_definition(
            hierarchy,
            enforce_resource_limits=False,
        )
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
            if database_exists:
                ensure_compatible_scheduler_storage(connection)
            else:
                initialize_scheduler_storage(connection)
        except Exception:
            connection.close()
            raise
        return connection

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
        return workspace_identity(workspace_root).key

    def _delivery_workspace_store(self) -> DeliveryWorkspaceStore:
        return DeliveryWorkspaceStore(
            self,
            governance_directory=GOVERNANCE_DIRECTORY,
            validate_stored_definition=_validated_stored_definition,
            timestamp_fn=timestamp,
        )

    def _delivery_dispatch_store(self) -> DeliveryDispatchStore:
        return DeliveryDispatchStore(
            self,
            validate_stored_definition=_validated_stored_definition,
            commit_timestamp_fn=_commit_timestamp,
            timestamp_fn=timestamp,
        )

    def _delivery_projection_store(self) -> DeliveryProjectionStore:
        return DeliveryProjectionStore(
            self,
            validate_stored_definition=_validated_stored_definition,
            timestamp_fn=timestamp,
        )

    def _delivery_execution_setup_store(
        self,
    ) -> DeliveryExecutionSetupStore:
        return DeliveryExecutionSetupStore(
            self,
            validate_stored_definition=_validated_stored_definition,
            commit_timestamp_fn=_commit_timestamp,
            timestamp_fn=timestamp,
        )

    def _delivery_hierarchy_store(self) -> DeliveryHierarchyStore:
        return DeliveryHierarchyStore(
            self,
            validate_stored_definition=_validated_stored_definition,
            commit_timestamp_fn=_commit_timestamp,
            timestamp_fn=timestamp,
        )

    def _delivery_event_store(self) -> DeliveryEventStore:
        return DeliveryEventStore(self)

    def workspace_binding(self, root_id: str) -> dict[str, Any]:
        return self._delivery_workspace_store().binding(root_id)

    def serial_workspace_turns(
        self,
        workspace_root: str | os.PathLike[str],
        *,
        exclude_root_id: str | None = None,
        include_terminal: bool = False,
    ) -> list[dict[str, str]]:
        return self._delivery_workspace_store().serial_turns(
            workspace_root,
            exclude_root_id=exclude_root_id,
            include_terminal=include_terminal,
        )

    def assert_delivery_workspace(
        self,
        root_id: str,
        workspace_root: str | os.PathLike[str],
        *,
        allow_unbound_manual: bool = False,
        allow_unbound_choice: bool = False,
    ) -> None:
        self._delivery_workspace_store().assert_bound(
            root_id,
            workspace_root,
            allow_unbound_manual=allow_unbound_manual,
            allow_unbound_choice=allow_unbound_choice,
        )

    def workspace_status(
        self,
        *,
        root_id: str | None = None,
        workspace_root: str | os.PathLike[str] | None = None,
    ) -> dict[str, Any]:
        return self._delivery_workspace_store().status(
            root_id=root_id,
            workspace_root=workspace_root,
        )

    def git_branch_usage(
        self,
        branch_ref: str,
        *,
        repository_key: str | None = None,
    ) -> list[dict[str, str]]:
        return self._delivery_execution_setup_store().git_branch_usage(
            branch_ref,
            repository_key=repository_key,
        )

    def development_preference(self, root_id: str) -> dict[str, Any] | None:
        return self._delivery_execution_setup_store().development_preference(
            root_id,
        )

    def record_development_preference(
        self,
        root_id: str,
        *,
        binding: dict[str, str],
        source: str,
        chosen_by: str,
    ) -> dict[str, Any]:
        return self._delivery_execution_setup_store().record_development_preference(
            root_id,
            binding=binding,
            source=source,
            chosen_by=chosen_by,
        )

    def clear_development_preference(self, root_id: str) -> None:
        return self._delivery_execution_setup_store().clear_development_preference(
            root_id,
        )

    def record_choice_ready(
        self,
        hierarchy: dict[str, Any],
        graph: dict[str, Any],
        *,
        hierarchy_fingerprint: str,
        graph_fingerprint: str,
    ) -> dict[str, Any]:
        return self._delivery_execution_setup_store().record_choice_ready(
            hierarchy,
            graph,
            hierarchy_fingerprint=hierarchy_fingerprint,
            graph_fingerprint=graph_fingerprint,
        )

    def record_automatic_selection(
        self,
        root_id: str,
        *,
        expected_hierarchy_fingerprint: str,
        expected_graph_fingerprint: str,
        authorized_project_ids: list[str],
        confirmed_by: str,
        workspace_key: str | None = None,
    ) -> dict[str, Any]:
        return self._delivery_execution_setup_store().record_automatic_selection(
            root_id,
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            expected_graph_fingerprint=expected_graph_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed_by=confirmed_by,
            workspace_key=workspace_key,
        )

    def serial_workspace_turn_state(
        self,
        root_id: str,
    ) -> dict[str, Any]:
        return self._delivery_execution_setup_store().serial_workspace_turn_state(
            root_id
        )

    def execution_selection(
        self,
        root_id: str,
    ) -> dict[str, Any] | None:
        return self._delivery_execution_setup_store().execution_selection(
            root_id,
        )

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
        return self._delivery_hierarchy_store().record_manual_handoff(
            hierarchy,
            graph,
            hierarchy_fingerprint=hierarchy_fingerprint,
            graph_fingerprint=graph_fingerprint,
            authorized_project_ids=authorized_project_ids,
            expected_current_revision=expected_current_revision,
            continuity_basis=continuity_basis,
            revision_reason=revision_reason,
            confirmed_by=confirmed_by,
        )

    def prepare(
        self,
        hierarchy: dict[str, Any],
        graph: dict[str, Any],
        *,
        hierarchy_fingerprint: str,
        graph_fingerprint: str,
        workspace_root: str | os.PathLike[str],
    ) -> dict[str, Any]:
        return self._delivery_hierarchy_store().prepare(
            hierarchy,
            graph,
            hierarchy_fingerprint=hierarchy_fingerprint,
            graph_fingerprint=graph_fingerprint,
            workspace_root=workspace_root,
        )

    def hierarchy(
        self,
        root_id: str | None = None,
    ) -> dict[str, Any]:
        return self._delivery_hierarchy_store().hierarchy(
            root_id,
        )

    def revision_hierarchy(
        self,
        root_id: str,
        revision: int,
    ) -> dict[str, Any]:
        return self._delivery_hierarchy_store().revision_hierarchy(
            root_id, revision
        )

    @staticmethod
    def _carriable_task_ids(
        previous_hierarchy: dict[str, Any],
        revised_hierarchy: dict[str, Any],
        previous_nodes: list[dict[str, Any]],
    ) -> list[str]:
        return DeliveryHierarchyStore._carriable_task_ids(
            previous_hierarchy,
            revised_hierarchy,
            previous_nodes,
        )

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
        return self._delivery_hierarchy_store().prepare_revision(
            hierarchy,
            graph,
            root_id=root_id,
            expected_current_revision=expected_current_revision,
            hierarchy_fingerprint=hierarchy_fingerprint,
            graph_fingerprint=graph_fingerprint,
            reason=reason,
            continuity_basis=continuity_basis,
            requested_by=requested_by,
            workspace_root=workspace_root,
        )

    def freeze(
        self,
        root_id: str,
        *,
        expected_delivery_revision: int,
        expected_hierarchy_fingerprint: str,
        authorized_project_ids: list[str],
        confirmed_by: str,
        workspace_turn_start: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._delivery_hierarchy_store().freeze(
            root_id,
            expected_delivery_revision=expected_delivery_revision,
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed_by=confirmed_by,
            workspace_turn_start=workspace_turn_start,
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
        workspace_turn_start: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._delivery_hierarchy_store().freeze_manual_handoff(
            root_id,
            expected_delivery_revision=expected_delivery_revision,
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed_by=confirmed_by,
            started_by=started_by,
            workspace_turn_start=workspace_turn_start,
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
        workspace_turn_start: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._delivery_hierarchy_store()._freeze(
            root_id,
            expected_delivery_revision=expected_delivery_revision,
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed_by=confirmed_by,
            execution_mode=execution_mode,
            graph_started_by=graph_started_by,
            workspace_turn_start=workspace_turn_start,
        )

    def _run_from_connection(
        self,
        connection: sqlite3.Connection,
        root_id: str,
    ) -> dict[str, Any]:
        return self._delivery_hierarchy_store()._run_from_connection(
            connection,
            root_id,
        )

    def run(
        self,
        root_id: str,
    ) -> dict[str, Any]:
        return self._delivery_hierarchy_store().run(
            root_id,
        )
    def workspace_turn_start(
        self,
        root_id: str,
    ) -> dict[str, Any] | None:
        """Return Controller-captured Git state from GRAPH_RUN_STARTED."""

        if not self.database_path.is_file():
            return None
        with self.read() as connection:
            row = connection.execute(
                "SELECT e.payload_json FROM graph_events e "
                "JOIN runs r ON r.run_id = e.run_id "
                "JOIN hierarchies h ON h.root_id = r.root_id "
                "AND h.revision = r.revision "
                "WHERE r.root_id = ? "
                "AND e.event_type = 'GRAPH_RUN_STARTED' "
                "ORDER BY e.event_id DESC LIMIT 1",
                (root_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        value = payload.get("workspaceTurnStart")
        return value if isinstance(value, dict) else None

    def workspace_turn_release(
        self,
        root_id: str,
    ) -> dict[str, Any] | None:
        if not self.database_path.is_file():
            return None
        with self.read() as connection:
            row = connection.execute(
                "SELECT e.payload_json FROM graph_events e "
                "JOIN runs r ON r.run_id = e.run_id "
                "WHERE r.root_id = ? "
                "AND e.event_type = 'WORKSPACE_TURN_RELEASED' "
                "ORDER BY e.event_id DESC LIMIT 1",
                (root_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        return payload if isinstance(payload, dict) else None

    def unexpired_cancelled_receiver_leases(
        self,
        root_id: str,
    ) -> list[dict[str, Any]]:
        """Return receivers still live when a Run was cancelled or superseded.

        A future lease timestamp alone is insufficient: successful and
        otherwise finished Loops retain their last lease for audit purposes.
        The claim must therefore be the node attempt's latest claim and have
        no claim-ending event before the terminal Run event.
        """

        if not self.database_path.is_file():
            return []
        at = timestamp(self.now)
        with self.read() as connection:
            rows = connection.execute(
                """
                SELECT
                    r.run_id,
                    r.revision,
                    r.status AS run_status,
                    n.node_id,
                    n.attempt,
                    n.owner,
                    n.operation_id,
                    n.claimed_at,
                    n.last_heartbeat_at,
                    n.lease_expires_at,
                    claim.payload_json AS claim_payload_json
                FROM runs r
                JOIN node_runs n ON n.run_id = r.run_id
                JOIN graph_events terminal
                  ON terminal.run_id = r.run_id
                 AND terminal.event_type = CASE r.status
                       WHEN 'CANCELLED' THEN 'GRAPH_RUN_CANCELLED'
                       ELSE 'GRAPH_RUN_SUPERSEDED'
                     END
                JOIN graph_events claim
                  ON claim.run_id = r.run_id
                 AND claim.node_id = n.node_id
                 AND claim.attempt = n.attempt
                 AND claim.event_type = 'LOOP_CLAIMED'
                 AND claim.event_id = (
                       SELECT MAX(candidate.event_id)
                       FROM graph_events candidate
                       WHERE candidate.run_id = r.run_id
                         AND candidate.node_id = n.node_id
                         AND candidate.attempt = n.attempt
                         AND candidate.event_type = 'LOOP_CLAIMED'
                     )
                WHERE r.root_id = ?
                  AND r.status IN ('CANCELLED', 'SUPERSEDED')
                  AND n.status = 'CANCELLED'
                  AND n.owner IS NOT NULL
                  AND n.operation_id IS NOT NULL
                  AND claim.operation_id = n.operation_id
                  AND n.lease_expires_at IS NOT NULL
                  AND julianday(n.lease_expires_at) >= julianday(?)
                  AND claim.event_id < terminal.event_id
                  AND NOT EXISTS (
                        SELECT 1
                        FROM graph_events ended
                        WHERE ended.run_id = r.run_id
                          AND ended.node_id = n.node_id
                          AND ended.attempt = n.attempt
                          AND ended.event_id > claim.event_id
                          AND ended.event_id < terminal.event_id
                          AND ended.event_type IN (
                              'LOOP_SUCCEEDED',
                              'LOOP_BLOCKED',
                              'LOOP_REPLAN_REQUIRED',
                              'LOOP_CANCELLED',
                              'CLAIM_LEASE_EXPIRED',
                              'NODE_PAUSED'
                          )
                    )
                ORDER BY r.revision, n.node_id, n.attempt
                """,
                (root_id, at),
            ).fetchall()
        leases: list[dict[str, Any]] = []
        for row in rows:
            claim_payload = json.loads(row["claim_payload_json"])
            receiver_context_id = (
                claim_payload.get("receiverContextId")
                if isinstance(claim_payload, dict)
                else None
            )
            leases.append(
                {
                    "rootId": root_id,
                    "runId": row["run_id"],
                    "revision": row["revision"],
                    "runStatus": row["run_status"],
                    "nodeId": row["node_id"],
                    "attempt": row["attempt"],
                    "owner": row["owner"],
                    "receiverContextId": (
                        receiver_context_id
                        if isinstance(receiver_context_id, str)
                        and receiver_context_id
                        else row["owner"]
                    ),
                    "operationId": row["operation_id"],
                    "claimedAt": row["claimed_at"],
                    "lastHeartbeatAt": row["last_heartbeat_at"],
                    "leaseExpiresAt": row["lease_expires_at"],
                }
            )
        return leases

    def release_serial_workspace_turn(
        self,
        root_id: str,
        *,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist one idempotent terminal commit/clean release decision."""

        with self.transaction() as connection:
            run = connection.execute(
                "SELECT r.* FROM runs r "
                "JOIN hierarchies h ON h.root_id = r.root_id "
                "AND h.revision = r.revision "
                "WHERE r.root_id = ?",
                (root_id,),
            ).fetchone()
            if run is None:
                fail(
                    "SCHEDULER_RUN_MISSING",
                    f"Graph run is missing: {root_id}",
                )
            existing = connection.execute(
                "SELECT payload_json FROM graph_events "
                "WHERE run_id = ? "
                "AND event_type = 'WORKSPACE_TURN_RELEASED' "
                "ORDER BY event_id DESC LIMIT 1",
                (run["run_id"],),
            ).fetchone()
            if existing is not None:
                payload = json.loads(existing["payload_json"])
                return payload
            if run["status"] not in {
                "COMPLETED",
                "CANCELLED",
                "SUPERSEDED",
            }:
                fail(
                    "SCHEDULER_WORKSPACE_TURN_NOT_TERMINAL",
                    "A workspace turn can release only after its Run is "
                    "terminal",
                    rootId=root_id,
                    status=run["status"],
                )
            at = _commit_timestamp(self.now, run["updated_at"])
            payload = {
                "state": "RELEASED",
                "strategy": "CURRENT_WORKSPACE_SERIAL",
                "releasedAt": at,
                **evidence,
            }
            self.append_event(
                connection,
                run_id=run["run_id"],
                node_id=None,
                attempt=None,
                event_type="WORKSPACE_TURN_RELEASED",
                actor="CONTROLLER",
                operation_id=None,
                payload=payload,
                at=at,
            )
        return payload


    def revision_history(self, root_id: str) -> dict[str, Any]:
        return self._delivery_hierarchy_store().revision_history(
            root_id,
        )

    @staticmethod
    def task_requirement_states(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> list[dict[str, Any]]:
        return DeliveryHierarchyStore.task_requirement_states(
            connection,
            run_id,
        )

    def claimed_resource_reservations(
        self,
        connection: sqlite3.Connection,
        *,
        at: str,
        exclude_root_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._delivery_dispatch_store().claimed_resource_reservations(
            connection,
            at=at,
            exclude_root_id=exclude_root_id,
        )
    @staticmethod
    def expire_dispatch_reservations(
        connection: sqlite3.Connection,
        *,
        at: str,
    ) -> None:
        return DeliveryDispatchStore.expire_dispatch_reservations(
            connection,
            at=at,
        )
    def active_dispatch_reservations(
        self,
        connection: sqlite3.Connection,
        *,
        at: str,
    ) -> list[dict[str, Any]]:
        return self._delivery_dispatch_store().active_dispatch_reservations(
            connection,
            at=at,
        )
    @staticmethod
    def open_host_capacity_breaker(
        connection: sqlite3.Connection,
        *,
        agent_id: str,
        at: str,
    ) -> dict[str, Any] | None:
        return DeliveryDispatchStore.open_host_capacity_breaker(
            connection,
            agent_id=agent_id,
            at=at,
        )
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
        return self._delivery_dispatch_store().reserve_dispatch_assignments(
            root_id=root_id,
            graph_fingerprint=graph_fingerprint,
            assignments=assignments,
            agent_slot_limits=agent_slot_limits,
            orchestrator_slot_limit=orchestrator_slot_limit,
            reservation_seconds=reservation_seconds,
        )
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
        return self._delivery_dispatch_store().consume_dispatch_reservation(
            connection,
            reservation_id=reservation_id,
            run_id=run_id,
            node_id=node_id,
            attempt=attempt,
            graph_fingerprint=graph_fingerprint,
            decision_fingerprint=decision_fingerprint,
            operation_id=operation_id,
            at=at,
        )
    @staticmethod
    def latest_nodes(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> list[dict[str, Any]]:
        return DeliveryEventStore.latest_nodes(
            connection,
            run_id,
        )

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
        return self._delivery_event_store()._append_event(
            connection,
            run_id=run_id,
            node_id=node_id,
            attempt=attempt,
            event_type=event_type,
            actor=actor,
            operation_id=operation_id,
            payload=payload,
            at=at,
        )

    def append_event(
        self,
        connection: sqlite3.Connection,
        **arguments: Any,
    ) -> dict[str, Any]:
        return self._delivery_event_store().append_event(
            connection,
            **arguments,
        )

    def events(
        self,
        root_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return self._delivery_event_store().events(
            root_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    def refresh_ready(
        self,
        connection: sqlite3.Connection,
        graph: dict[str, Any],
        run_id: str,
        *,
        at: str,
    ) -> None:
        return self._delivery_event_store().refresh_ready(
            connection,
            graph,
            run_id,
            at=at,
        )

    def write_projections(
        self,
        root_id: str,
        *,
        preserve_manual_updates: bool = True,
        refresh_workspace_overview: bool = True,
    ) -> None:
        return self._delivery_projection_store().write_projections(
            root_id,
            preserve_manual_updates=preserve_manual_updates,
            refresh_workspace_overview=refresh_workspace_overview,
        )
    def _write_workspace_overview(self) -> None:
        return self._delivery_projection_store()._write_workspace_overview()
    def write_workspace_overview(self) -> None:
        return self._delivery_projection_store().write_workspace_overview()
    def _workspace_projection_sources(self) -> list[dict[str, Any]]:
        return self._delivery_projection_store()._workspace_projection_sources()
GovernanceRepository = SchedulerRepository


__all__ = (
    "DATABASE_FILE",
    "GOVERNANCE_DIRECTORY",
    "GovernanceRepository",
    "SchedulerRepository",
    "timestamp",
)
