from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any, Callable

from .errors import fail
from .git_binding import git_repository_identity
from .jsonio import canonical_json
from .model_core import validate_git_binding


WORKTREE_SETUP_HEARTBEAT_SECONDS = 30
WORKTREE_SETUP_LEASE_SECONDS = 120
WORKTREE_SETUP_POLL_SECONDS = 10


def _timestamp_after(value: str, *, seconds: int) -> str:
    return (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        + timedelta(seconds=seconds)
    ).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


class DeliveryExecutionSetupStore:
    """Own execution-mode choices and worktree setup persistence."""

    def __init__(
        self,
        repository: Any,
        *,
        validate_stored_definition: Callable[..., Any],
        commit_timestamp_fn: Callable[..., str],
        timestamp_fn: Callable[[object], str],
    ) -> None:
        self.repository = repository
        self.validate_stored_definition = validate_stored_definition
        self.commit_timestamp_fn = commit_timestamp_fn
        self.timestamp_fn = timestamp_fn

    def __getattr__(self, name: str) -> Any:
        return getattr(self.repository, name)

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
                hierarchy, _ = self.validate_stored_definition(row)
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
        chosen_at = self.timestamp_fn(self.now)
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
                at = self.timestamp_fn(self.now)
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
                self.validate_stored_definition(existing)
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
                    at = self.commit_timestamp_fn(
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
                    at = self.timestamp_fn(self.now)
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
            at = self.commit_timestamp_fn(self.now, hierarchy["updated_at"])
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
        at = self.timestamp_fn(self.now)
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
        at = self.timestamp_fn(self.now)
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

        at = self.timestamp_fn(self.now)
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
