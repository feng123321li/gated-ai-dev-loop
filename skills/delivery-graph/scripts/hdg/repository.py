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
    WORKTREE_SETUP_HEARTBEAT_SECONDS,
    WORKTREE_SETUP_LEASE_SECONDS,
    WORKTREE_SETUP_POLL_SECONDS,
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
            if not database_exists:
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
        worktree_requests: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return self._delivery_execution_setup_store().record_automatic_selection(
            root_id,
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            expected_graph_fingerprint=expected_graph_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed_by=confirmed_by,
            worktree_requests=worktree_requests,
        )

    def execution_selection(
        self,
        root_id: str,
    ) -> dict[str, Any] | None:
        return self._delivery_execution_setup_store().execution_selection(
            root_id,
        )

    def worktree_setup_reservations(
        self,
        root_id: str,
    ) -> list[dict[str, Any]]:
        return self._delivery_execution_setup_store().worktree_setup_reservations(
            root_id,
        )

    def mark_worktree_setups_ready(
        self,
        root_id: str,
        project_ids: list[str],
    ) -> None:
        return self._delivery_execution_setup_store().mark_worktree_setups_ready(
            root_id,
            project_ids,
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
        return self._delivery_execution_setup_store().report_worktree_setup(
            root_id,
            project_id=project_id,
            reservation_id=reservation_id,
            expected_attempt=expected_attempt,
            event=event,
            phase=phase,
            summary_zh=summary_zh,
            progress_percent=progress_percent,
            failure_code=failure_code,
            confirmed_previous_attempt_stopped=confirmed_previous_attempt_stopped,
            confirmed_partial_state_reconciled=confirmed_partial_state_reconciled,
            retry_request_id=retry_request_id,
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
    ) -> dict[str, Any]:
        return self._delivery_hierarchy_store().freeze(
            root_id,
            expected_delivery_revision=expected_delivery_revision,
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed_by=confirmed_by,
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
        return self._delivery_hierarchy_store().freeze_manual_handoff(
            root_id,
            expected_delivery_revision=expected_delivery_revision,
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed_by=confirmed_by,
            started_by=started_by,
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
        return self._delivery_hierarchy_store()._freeze(
            root_id,
            expected_delivery_revision=expected_delivery_revision,
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed_by=confirmed_by,
            execution_mode=execution_mode,
            graph_started_by=graph_started_by,
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
    "WORKTREE_SETUP_HEARTBEAT_SECONDS",
    "WORKTREE_SETUP_LEASE_SECONDS",
    "WORKTREE_SETUP_POLL_SECONDS",
    "timestamp",
)
