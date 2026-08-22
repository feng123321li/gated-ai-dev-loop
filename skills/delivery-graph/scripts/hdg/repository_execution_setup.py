from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from .errors import fail
from .git_binding import git_repository_identity
from .jsonio import canonical_json
from .model_core import validate_git_binding
from .repository_execution_selection import DeliveryExecutionSelectionMixin


class DeliveryExecutionSetupStore(DeliveryExecutionSelectionMixin):
    """Own execution-mode choices and serial workspace coordination."""

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
                                "自动执行已确认，等待当前 workspace 串行调度"
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
                            "DELETE FROM delivery_workspaces "
                            "WHERE root_id = ? AND NOT EXISTS ("
                            "SELECT 1 FROM runs WHERE root_id = ?"
                            ")",
                            (root_id, root_id),
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

    @staticmethod
    def _serial_workspace_turn_state_from_connection(
        connection: sqlite3.Connection,
        *,
        workspace_key: str,
        requested_root_id: str,
    ) -> dict[str, Any]:
        rows = connection.execute(
            "SELECT w.root_id, w.workspace_key, w.created_at, "
            "h.status AS hierarchy_status, r.status AS run_status, "
            "CASE WHEN EXISTS("
            "SELECT 1 FROM graph_events e WHERE e.run_id = r.run_id "
            "AND e.event_type = 'WORKSPACE_TURN_RELEASED' "
            "AND NOT EXISTS("
            "SELECT 1 FROM graph_events requeued "
            "WHERE requeued.run_id = e.run_id "
            "AND requeued.event_type = 'WORKSPACE_TURN_REQUEUED' "
            "AND requeued.event_id > e.event_id"
            ")"
            ") AND NOT EXISTS("
            "SELECT 1 FROM delivery_revisions pending "
            "WHERE pending.root_id = h.root_id "
            "AND pending.revision > h.revision "
            "AND pending.status = 'PREPARED'"
            ") THEN 1 ELSE 0 END AS turn_released "
            "FROM delivery_workspaces w "
            "JOIN hierarchies h ON h.root_id = w.root_id "
            "LEFT JOIN runs r "
            "ON r.root_id = h.root_id AND r.revision = h.revision "
            "WHERE w.workspace_key = ? "
            "ORDER BY w.created_at ASC, w.root_id ASC",
            (workspace_key,),
        ).fetchall()
        queue = []
        requested_row = None
        for row in rows:
            if row["root_id"] == requested_root_id:
                requested_row = row
            if bool(row["turn_released"]):
                continue
            queue.append(
                {
                    "rootId": row["root_id"],
                    "status": (
                        row["run_status"]
                        if row["run_status"] is not None
                        else row["hierarchy_status"]
                    ),
                    "workspaceKey": row["workspace_key"],
                    "createdAt": row["created_at"],
                }
            )
        if requested_row is None:
            fail(
                "SCHEDULER_WORKSPACE_TURN_STATE_INVALID",
                "The selected Delivery is missing from its serial workspace "
                "bindings",
                rootId=requested_root_id,
                workspaceKey=workspace_key,
            )
        if bool(requested_row["turn_released"]):
            owner = queue[0] if queue else None
            return {
                "state": "RELEASED",
                "strategy": "CURRENT_WORKSPACE_SERIAL",
                "workspaceKey": workspace_key,
                "ownerRootId": (
                    owner["rootId"] if owner is not None else None
                ),
                "ownerStatus": (
                    owner["status"] if owner is not None else None
                ),
                "requestedRootId": requested_root_id,
                "position": None,
                "queueLength": len(queue),
                "releasePolicy": (
                    "OWNER_COMMIT_CLEAN_AND_SAFE_BOUNDARY_THEN_RELEASE"
                ),
            }
        requested_position = next(
            (
                index
                for index, item in enumerate(queue, start=1)
                if item["rootId"] == requested_root_id
            ),
            None,
        )
        if requested_position is None:
            fail(
                "SCHEDULER_WORKSPACE_TURN_STATE_INVALID",
                "The selected Delivery is missing from its serial workspace "
                "queue",
                rootId=requested_root_id,
                workspaceKey=workspace_key,
            )
        owner = queue[0]
        acquired = owner["rootId"] == requested_root_id
        return {
            "state": (
                "ACQUIRED"
                if acquired
                else "WAITING_FOR_WORKSPACE_TURN"
            ),
            "strategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceKey": workspace_key,
            "ownerRootId": owner["rootId"],
            "ownerStatus": owner["status"],
            "requestedRootId": requested_root_id,
            "position": requested_position,
            "queueLength": len(queue),
            "releasePolicy": (
                "OWNER_COMMIT_CLEAN_AND_SAFE_BOUNDARY_THEN_RELEASE"
            ),
        }

    def serial_workspace_turn_state(
        self,
        root_id: str,
    ) -> dict[str, Any]:
        with self.read() as connection:
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
            return self._serial_workspace_turn_state_from_connection(
                connection,
                workspace_key=binding["workspace_key"],
                requested_root_id=root_id,
            )

    def serial_workspace_release_blockers(
        self,
        root_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return live receiver and reservation blockers for one release."""

        if not self.database_path.is_file():
            return {"receiverClaims": [], "dispatchReservations": []}
        with self.read() as connection:
            run = connection.execute(
                "SELECT run_id FROM runs WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if run is None:
                return {"receiverClaims": [], "dispatchReservations": []}
            claims = [
                {
                    "nodeId": row["node_id"],
                    "attempt": row["attempt"],
                    "owner": row["owner"],
                    "operationId": row["operation_id"],
                    "leaseExpiresAt": row["lease_expires_at"],
                }
                for row in connection.execute(
                    "SELECT node_id, attempt, owner, operation_id, "
                    "lease_expires_at FROM node_runs WHERE run_id = ? "
                    "AND status = 'CLAIMED' ORDER BY node_id, attempt",
                    (run["run_id"],),
                ).fetchall()
            ]
            reservations = [
                reservation
                for reservation in self.active_dispatch_reservations(
                    connection,
                    at=self.timestamp_fn(self.now),
                )
                if reservation["rootId"] == root_id
            ]
        return {
            "receiverClaims": claims,
            "dispatchReservations": reservations,
        }

    def requeue_paused_workspace_turn(
        self,
        root_id: str,
        *,
        node_id: str,
    ) -> dict[str, Any]:
        """Invalidate a paused release and append the Delivery at queue tail."""

        with self.transaction() as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if run is None:
                fail(
                    "SCHEDULER_RUN_MISSING",
                    f"Graph run is missing: {root_id}",
                )
            node = connection.execute(
                "SELECT status, attempt FROM node_runs WHERE run_id = ? "
                "AND node_id = ? ORDER BY attempt DESC LIMIT 1",
                (run["run_id"], node_id),
            ).fetchone()
            if (
                run["status"] != "PAUSED"
                or node is None
                or node["status"] != "PAUSED"
            ):
                fail(
                    "SCHEDULER_LOOP_NOT_PAUSED",
                    f"{node_id} is not paused at a releasable checkpoint",
                )
            release = connection.execute(
                "SELECT released.event_id, released.payload_json "
                "FROM graph_events released "
                "WHERE released.run_id = ? "
                "AND released.event_type = 'WORKSPACE_TURN_RELEASED' "
                "AND NOT EXISTS ("
                "SELECT 1 FROM graph_events requeued "
                "WHERE requeued.run_id = released.run_id "
                "AND requeued.event_type = 'WORKSPACE_TURN_REQUEUED' "
                "AND requeued.event_id > released.event_id"
                ") ORDER BY released.event_id DESC LIMIT 1",
                (run["run_id"],),
            ).fetchone()
            if release is None:
                existing = connection.execute(
                    "SELECT payload_json FROM graph_events WHERE run_id = ? "
                    "AND event_type = 'WORKSPACE_TURN_REQUEUED' "
                    "ORDER BY event_id DESC LIMIT 1",
                    (run["run_id"],),
                ).fetchone()
                if existing is not None:
                    return json.loads(existing["payload_json"])
                fail(
                    "SCHEDULER_WORKSPACE_TURN_NOT_RELEASED",
                    "A paused workspace turn must be released before it can "
                    "requeue",
                    rootId=root_id,
                    nodeId=node_id,
                )
            at = self.commit_timestamp_fn(self.now, run["updated_at"])
            payload = {
                "state": "QUEUED",
                "strategy": "CURRENT_WORKSPACE_SERIAL",
                "nodeId": node_id,
                "requeuedAt": at,
                "release": json.loads(release["payload_json"]),
                "requeueReason": "PAUSED_LOOP_RESUME_REQUESTED",
            }
            self.append_event(
                connection,
                run_id=run["run_id"],
                node_id=node_id,
                attempt=node["attempt"],
                event_type="WORKSPACE_TURN_REQUEUED",
                actor="CONTROLLER",
                operation_id=None,
                payload=payload,
                at=at,
            )
            connection.execute(
                "UPDATE delivery_workspaces SET created_at = ?, "
                "updated_at = ? WHERE root_id = ?",
                (at, at, root_id),
            )
            connection.execute(
                "UPDATE runs SET updated_at = ? WHERE run_id = ?",
                (at, run["run_id"]),
            )
        return payload

    def paused_workspace_turn_requeue(
        self,
        root_id: str,
    ) -> dict[str, Any] | None:
        """Return an active paused requeue not yet reacquired."""

        if not self.database_path.is_file():
            return None
        with self.read() as connection:
            row = connection.execute(
                "SELECT requeued.payload_json FROM graph_events requeued "
                "JOIN runs r ON r.run_id = requeued.run_id "
                "WHERE r.root_id = ? "
                "AND requeued.event_type = 'WORKSPACE_TURN_REQUEUED' "
                "AND NOT EXISTS ("
                "SELECT 1 FROM graph_events reacquired "
                "WHERE reacquired.run_id = requeued.run_id "
                "AND reacquired.event_type = 'WORKSPACE_TURN_REACQUIRED' "
                "AND reacquired.event_id > requeued.event_id"
                ") ORDER BY requeued.event_id DESC LIMIT 1",
                (root_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        return payload if isinstance(payload, dict) else None

    def reacquire_paused_workspace_turn(
        self,
        root_id: str,
        *,
        node_id: str,
        workspace_turn_start: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a fresh Git checkpoint before a paused Loop resumes."""

        with self.transaction() as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if run is None:
                fail(
                    "SCHEDULER_RUN_MISSING",
                    f"Graph run is missing: {root_id}",
                )
            node = connection.execute(
                "SELECT status, attempt FROM node_runs WHERE run_id = ? "
                "AND node_id = ? ORDER BY attempt DESC LIMIT 1",
                (run["run_id"], node_id),
            ).fetchone()
            if (
                run["status"] != "PAUSED"
                or node is None
                or node["status"] != "PAUSED"
            ):
                fail(
                    "SCHEDULER_LOOP_NOT_PAUSED",
                    f"{node_id} is not paused",
                )
            requeue = connection.execute(
                "SELECT event_id, payload_json FROM graph_events "
                "WHERE run_id = ? "
                "AND event_type = 'WORKSPACE_TURN_REQUEUED' "
                "ORDER BY event_id DESC LIMIT 1",
                (run["run_id"],),
            ).fetchone()
            if requeue is None:
                fail(
                    "SCHEDULER_WORKSPACE_TURN_REQUEUE_REQUIRED",
                    "A released paused Loop must requeue before reacquiring",
                    rootId=root_id,
                    nodeId=node_id,
                )
            existing = connection.execute(
                "SELECT payload_json FROM graph_events WHERE run_id = ? "
                "AND event_type = 'WORKSPACE_TURN_REACQUIRED' "
                "AND event_id > ? ORDER BY event_id DESC LIMIT 1",
                (run["run_id"], requeue["event_id"]),
            ).fetchone()
            if existing is not None:
                return json.loads(existing["payload_json"])
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
            turn = self._serial_workspace_turn_state_from_connection(
                connection,
                workspace_key=binding["workspace_key"],
                requested_root_id=root_id,
            )
            if turn["state"] != "ACQUIRED":
                fail(
                    "SCHEDULER_WORKSPACE_TURN_NOT_OWNED",
                    "A paused Loop cannot resume before it reacquires the "
                    "serial workspace turn",
                    rootId=root_id,
                    workspaceTurn=turn,
                )
            at = self.commit_timestamp_fn(self.now, run["updated_at"])
            payload = {
                "state": "ACQUIRED",
                "strategy": "CURRENT_WORKSPACE_SERIAL",
                "nodeId": node_id,
                "reacquiredAt": at,
                "workspaceTurnStart": workspace_turn_start,
                "requeue": json.loads(requeue["payload_json"]),
            }
            self.append_event(
                connection,
                run_id=run["run_id"],
                node_id=node_id,
                attempt=node["attempt"],
                event_type="WORKSPACE_TURN_REACQUIRED",
                actor="CONTROLLER",
                operation_id=None,
                payload=payload,
                at=at,
            )
            connection.execute(
                "UPDATE runs SET updated_at = ? WHERE run_id = ?",
                (at, run["run_id"]),
            )
        return payload
