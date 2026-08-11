from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import secrets
import sqlite3
from typing import Any, Callable
import uuid

from .errors import fail
from .loop_contracts import resource_claims_overlap


RECEIVER_ATTESTATION_SECONDS = 300


class DeliveryDispatchStore:
    """Own dispatch reservations and receiver identity persistence."""

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
                _, graph = self.validate_stored_definition(row)
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
                at=self.timestamp_fn(self.now),
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
                _, graph = self.validate_stored_definition(row)
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
            _, graph = self.validate_stored_definition(hierarchy_row)
            run = connection.execute(
                "SELECT * FROM runs WHERE root_id = ? AND revision = ?",
                (root_id, hierarchy_row["revision"]),
            ).fetchone()
            if run is None:
                fail(
                    "SCHEDULER_RUN_MISSING",
                    f"Scheduler run is missing: {root_id}",
                )
            at = self.commit_timestamp_fn(self.now, run["updated_at"])
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

__all__ = (
    "RECEIVER_ATTESTATION_SECONDS",
    "DeliveryDispatchStore",
)
