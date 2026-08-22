from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from typing import Any, Callable
import uuid

from .errors import fail
from .loop_contracts import resource_claims_overlap


def _timestamp_value(value: object) -> datetime:
    if not isinstance(value, str):
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored dispatch timestamp is invalid",
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail(
            "SCHEDULER_STATE_INVALID",
            "Stored dispatch timestamp is invalid",
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class DeliveryDispatchStore:
    """Own dispatch reservations and resource locks."""

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
            "n.node_id AS reservation_node_id, "
            "n.lease_expires_at AS reservation_lease_expires_at "
            "FROM node_runs n "
            "JOIN runs r ON r.run_id = n.run_id "
            "JOIN delivery_revisions h ON h.root_id = r.root_id "
            "AND h.revision = r.revision "
            "WHERE n.status = 'CLAIMED' "
            "AND r.status NOT IN "
            "('COMPLETED', 'CANCELLED', 'SUPERSEDED') "
            "AND n.lease_expires_at IS NOT NULL "
            "AND julianday(n.lease_expires_at) > julianday(?) "
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
                    "reservationStatus": "CLAIMED",
                    "leaseExpiresAt": row[
                        "reservation_lease_expires_at"
                    ],
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
            "WHERE status = 'RESERVED' "
            "AND julianday(expires_at) <= julianday(?)",
            (at,),
        )

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
                   h.graph_fingerprint AS stored_graph_fingerprint,
                   (
                       SELECT n.lease_expires_at FROM node_runs n
                       WHERE n.run_id = d.run_id
                         AND n.node_id = d.node_id
                         AND n.attempt = d.attempt
                         AND n.status = 'CLAIMED'
                       LIMIT 1
                   ) AS claim_lease_expires_at
            FROM dispatch_reservations d
            JOIN runs r ON r.run_id = d.run_id
            JOIN delivery_revisions h ON h.root_id = r.root_id
                AND h.revision = r.revision
            WHERE (
                    (d.status = 'RESERVED'
                     AND julianday(d.expires_at) > julianday(?))
                    OR (
                        d.status = 'CLAIMED'
                        AND EXISTS (
                            SELECT 1 FROM node_runs n
                            WHERE n.run_id = d.run_id
                              AND n.node_id = d.node_id
                              AND n.attempt = d.attempt
                              AND n.status = 'CLAIMED'
                              AND n.lease_expires_at IS NOT NULL
                              AND julianday(n.lease_expires_at) > julianday(?)
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
                    "reservationStatus": row["status"],
                    "runId": row["run_id"],
                    "rootId": row["root_id"],
                    "nodeId": row["node_id"],
                    "attempt": row["attempt"],
                    "agentId": row["agent_id"],
                    "graphFingerprint": row["graph_fingerprint"],
                    "decisionFingerprint": row[
                        "decision_fingerprint"
                    ],
                    "agentProfileId": row["agent_profile_id"],
                    "agentCatalogFingerprint": row[
                        "agent_catalog_fingerprint"
                    ],
                    "teamPlanFingerprint": row[
                        "team_plan_fingerprint"
                    ],
                    "reservedAt": row["reserved_at"],
                    "reservationExpiresAt": row["expires_at"],
                    **(
                        {"leaseExpiresAt": row["claim_lease_expires_at"]}
                        if row["status"] == "CLAIMED"
                        else {}
                    ),
                    "resourceClaims": definition["loop"][
                        "resourceClaims"
                    ],
                }
            )
        return result

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
            reserved_profile_slots: dict[str, int] = {}
            for item in active:
                agent_id = item.get("agentId")
                if isinstance(agent_id, str):
                    reserved_agent_slots[agent_id] = (
                        reserved_agent_slots.get(agent_id, 0) + 1
                    )
                profile_id = item.get("agentProfileId")
                if isinstance(profile_id, str):
                    reserved_profile_slots[profile_id] = (
                        reserved_profile_slots.get(profile_id, 0) + 1
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
                profile_id = assignment["agentProfileId"]
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
                        "code": "ORCHESTRATOR_SLOT_LIMIT_REACHED",
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
                        "code": "DISPATCH_AGENT_SLOT_LIMIT_REACHED",
                        "message": (
                            "Another Delivery already reserved the "
                            "remaining host-native Agent slot."
                        ),
                        "agentId": agent_id,
                    }
                    continue
                if reserved_profile_slots.get(profile_id, 0) >= (
                    profile_slot_limits.get(profile_id, 0)
                ):
                    rejected[node_id] = {
                        "code": "DISPATCH_PROFILE_SLOT_LIMIT_REACHED",
                        "message": (
                            "The specialist Agent profile concurrency "
                            "limit is already occupied."
                        ),
                        "agentProfileId": profile_id,
                        "maxConcurrent": profile_slot_limits.get(
                            profile_id,
                            0,
                        ),
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
                        graph_fingerprint, decision_fingerprint,
                        agent_profile_id, agent_catalog_fingerprint,
                        team_plan_fingerprint, status,
                        reserved_at, expires_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?
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
                        profile_id,
                        assignment["agentCatalogFingerprint"],
                        assignment["teamPlan"]["teamPlanFingerprint"],
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
                reserved_profile_slots[profile_id] = (
                    reserved_profile_slots.get(profile_id, 0) + 1
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
        if (
            row["status"] != "RESERVED"
            or _timestamp_value(row["expires_at"]) <= _timestamp_value(at)
        ):
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
    "DeliveryDispatchStore",
)
