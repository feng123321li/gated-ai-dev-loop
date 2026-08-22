from __future__ import annotations

from .repository_core import (
    Any,
    DATABASE_FILE,
    DELIVERY_REQUIREMENT_REFERENCE,
    DeliveryDispatchStore,
    DeliveryEventStore,
    DeliveryExecutionSetupStore,
    DeliveryHierarchyStore,
    DeliveryProjectionStore,
    DeliveryWorkspaceStore,
    GOVERNANCE_DIRECTORY,
    GatedLoopError,
    Iterator,
    MANUAL_WRITABLE_PROJECTIONS,
    Path,
    SCHEDULER_STATE_CONTRACT,
    SchedulerRepositoryBase,
    _commit_timestamp,
    _delivery_requirement_key,
    _validated_stored_definition,
    _validated_stored_graph,
    compile_delivery_graph,
    contextmanager,
    datetime,
    ensure_compatible_scheduler_storage,
    exclusive_file_lock,
    fail,
    fingerprint,
    graph_fingerprint,
    initialize_scheduler_storage,
    json,
    os,
    re,
    sqlite3,
    timestamp,
    timezone,
    validate_delivery_graph,
    validate_hierarchy_definition,
    verify_scheduler_state_contract,
    workspace_identity,
)


class SchedulerRepository(SchedulerRepositoryBase):
    """Compose dedicated scheduler persistence stores."""

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
        workspace_key: str,
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
            workspace_key=workspace_key,
        )

    def refresh_manual_handoff_graph(
        self,
        root_id: str,
        *,
        expected_hierarchy_fingerprint: str,
        expected_graph_fingerprint: str,
    ) -> dict[str, Any]:
        return self._delivery_hierarchy_store().refresh_manual_handoff_graph(
            root_id,
            expected_hierarchy_fingerprint=(
                expected_hierarchy_fingerprint
            ),
            expected_graph_fingerprint=expected_graph_fingerprint,
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
        """Return the latest Controller-captured Git turn-start state."""

        if not self.database_path.is_file():
            return None
        with self.read() as connection:
            row = connection.execute(
                "SELECT e.payload_json FROM graph_events e "
                "JOIN runs r ON r.run_id = e.run_id "
                "JOIN hierarchies h ON h.root_id = r.root_id "
                "AND h.revision = r.revision "
                "WHERE r.root_id = ? "
                "AND e.event_type IN ("
                "'GRAPH_RUN_STARTED', 'WORKSPACE_TURN_REACQUIRED'"
                ") "
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
                "JOIN hierarchies h ON h.root_id = r.root_id "
                "AND h.revision = r.revision "
                "WHERE r.root_id = ? "
                "AND e.event_type = 'WORKSPACE_TURN_RELEASED' "
                "AND NOT EXISTS ("
                "SELECT 1 FROM graph_events requeued "
                "WHERE requeued.run_id = e.run_id "
                "AND requeued.event_type = 'WORKSPACE_TURN_REQUEUED' "
                "AND requeued.event_id > e.event_id"
                ") "
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

    def serial_workspace_release_blockers(
        self,
        root_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        store = self._delivery_execution_setup_store()
        return store.serial_workspace_release_blockers(root_id)

    def release_serial_workspace_turn(
        self,
        root_id: str,
        *,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist one idempotent commit/clean release decision."""

        with self.transaction() as connection:
            run = connection.execute(
                "SELECT r.*, d.hierarchy_json, d.graph_json, "
                "d.hierarchy_fingerprint, d.graph_fingerprint "
                "FROM runs r "
                "JOIN hierarchies h ON h.root_id = r.root_id "
                "AND h.revision = r.revision "
                "JOIN delivery_revisions d ON d.root_id = r.root_id "
                "AND d.revision = r.revision "
                "WHERE r.root_id = ?",
                (root_id,),
            ).fetchone()
            if run is None:
                fail(
                    "SCHEDULER_RUN_MISSING",
                    f"Graph run is missing: {root_id}",
                )
            existing = connection.execute(
                "SELECT released.payload_json FROM graph_events released "
                "WHERE released.run_id = ? "
                "AND released.event_type = 'WORKSPACE_TURN_RELEASED' "
                "AND NOT EXISTS ("
                "SELECT 1 FROM graph_events requeued "
                "WHERE requeued.run_id = released.run_id "
                "AND requeued.event_type = 'WORKSPACE_TURN_REQUEUED' "
                "AND requeued.event_id > released.event_id"
                ") "
                "ORDER BY released.event_id DESC LIMIT 1",
                (run["run_id"],),
            ).fetchone()
            if existing is not None:
                payload = json.loads(existing["payload_json"])
                return payload
            terminal = run["status"] in {
                "COMPLETED",
                "CANCELLED",
                "SUPERSEDED",
            }
            release_reason = "RUN_TERMINAL" if terminal else None
            if run["status"] == "PAUSED":
                release_reason = "RUN_PAUSED_SAFE_CHECKPOINT"
            if not terminal:
                _, graph = _validated_stored_definition(run)
                confirmation = next(
                    node
                    for node in graph["nodes"]
                    if node["kind"] == "USER_CONFIRMATION"
                )
                confirmation_state = connection.execute(
                    "SELECT status FROM node_runs WHERE run_id = ? "
                    "AND node_id = ? ORDER BY attempt DESC LIMIT 1",
                    (run["run_id"], confirmation["id"]),
                ).fetchone()
                if (
                    run["status"] == "ACTIVE"
                    and confirmation_state is not None
                    and confirmation_state["status"] == "READY"
                ):
                    release_reason = "USER_CONFIRMATION_READY"
            if release_reason is None:
                fail(
                    "SCHEDULER_WORKSPACE_TURN_NOT_RELEASABLE",
                    "A workspace turn can release only after its Run is "
                    "terminal, paused at a safe checkpoint, or ready for "
                    "final user confirmation",
                    rootId=root_id,
                    status=run["status"],
                )
            live_claim = connection.execute(
                "SELECT node_id, attempt, owner, lease_expires_at "
                "FROM node_runs WHERE run_id = ? AND status = 'CLAIMED' "
                "ORDER BY node_id LIMIT 1",
                (run["run_id"],),
            ).fetchone()
            if live_claim is not None:
                fail(
                    "SCHEDULER_WORKSPACE_TURN_RECEIVER_ACTIVE",
                    "A workspace turn cannot release while a receiver is active",
                    rootId=root_id,
                    nodeId=live_claim["node_id"],
                    attempt=live_claim["attempt"],
                    owner=live_claim["owner"],
                    leaseExpiresAt=live_claim["lease_expires_at"],
                )
            active_reservations = [
                reservation
                for reservation in self.active_dispatch_reservations(
                    connection,
                    at=timestamp(self.now),
                )
                if reservation["rootId"] == root_id
            ]
            if active_reservations:
                fail(
                    "SCHEDULER_WORKSPACE_TURN_RESERVATION_ACTIVE",
                    "A workspace turn cannot release while dispatch is reserved",
                    rootId=root_id,
                    reservations=active_reservations,
                )
            at = _commit_timestamp(self.now, run["updated_at"])
            payload = {
                "state": "RELEASED",
                "strategy": "CURRENT_WORKSPACE_SERIAL",
                "releasedAt": at,
                **evidence,
                "releaseReason": release_reason,
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

    def requeue_paused_workspace_turn(
        self,
        root_id: str,
        *,
        node_id: str,
    ) -> dict[str, Any]:
        return self._delivery_execution_setup_store(
        ).requeue_paused_workspace_turn(
            root_id,
            node_id=node_id,
        )

    def paused_workspace_turn_requeue(
        self,
        root_id: str,
    ) -> dict[str, Any] | None:
        store = self._delivery_execution_setup_store()
        return store.paused_workspace_turn_requeue(root_id)

    def reacquire_paused_workspace_turn(
        self,
        root_id: str,
        *,
        node_id: str,
        workspace_turn_start: dict[str, Any],
    ) -> dict[str, Any]:
        return self._delivery_execution_setup_store(
        ).reacquire_paused_workspace_turn(
            root_id,
            node_id=node_id,
            workspace_turn_start=workspace_turn_start,
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

    def reserve_dispatch_assignments(
        self,
        *,
        root_id: str,
        graph_fingerprint: str,
        assignments: list[dict[str, Any]],
        agent_slot_limits: dict[str, int],
        profile_slot_limits: dict[str, int],
        orchestrator_slot_limit: int | None = None,
        reservation_seconds: int,
    ) -> dict[str, Any]:
        return self._delivery_dispatch_store().reserve_dispatch_assignments(
            root_id=root_id,
            graph_fingerprint=graph_fingerprint,
            assignments=assignments,
            agent_slot_limits=agent_slot_limits,
            profile_slot_limits=profile_slot_limits,
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
    def delivery_closure_from_connection(
        connection: sqlite3.Connection,
        root_id: str,
    ) -> dict[str, Any]:
        return DeliveryEventStore.delivery_closure_from_connection(
            connection,
            root_id,
        )

    def delivery_closure(self, root_id: str) -> dict[str, Any]:
        return self._delivery_event_store().delivery_closure(root_id)

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
        touch_run: bool = True,
    ) -> bool:
        return self._delivery_event_store().refresh_ready(
            connection,
            graph,
            run_id,
            at=at,
            touch_run=touch_run,
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
