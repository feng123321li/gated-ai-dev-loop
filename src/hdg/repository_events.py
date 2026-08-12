from __future__ import annotations

import json
import sqlite3
from typing import Any
import uuid

from .errors import fail
from .graph_model import JOIN_NODE_KINDS
from .jsonio import canonical_json, fingerprint


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


class DeliveryEventStore:
    """Own append-only graph events and derived node states."""

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def __getattr__(self, name: str) -> Any:
        return getattr(self.repository, name)

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
        # Track which PENDING nodes still need re-evaluation. The readiness
        # loop below only resolves PENDING nodes whose predecessors have all
        # reached a terminal success state. Re-running the full latest_nodes
        # projection on every iteration is wasteful because the only fields
        # that matter here are node_id, attempt, and status, and only PENDING
        # nodes can transition. We read the lightweight status snapshot once,
        # mutate the in-memory dict as we flip nodes, and stop when no PENDING
        # node can make further progress.
        status_rows = connection.execute(
            "SELECT n.node_id, n.attempt, n.status FROM node_runs n "
            "JOIN ("
            "SELECT node_id, MAX(attempt) AS attempt FROM node_runs "
            "WHERE run_id = ? GROUP BY node_id"
            ") latest ON n.node_id = latest.node_id "
            "AND n.attempt = latest.attempt "
            "WHERE n.run_id = ? ORDER BY n.node_id",
            (run_id, run_id),
        ).fetchall()
        current_status: dict[str, dict[str, Any]] = {
            row["node_id"]: {
                "attempt": row["attempt"],
                "status": row["status"],
            }
            for row in status_rows
        }
        while True:
            changed = False
            for node_id in sorted(current_status):
                node = current_status[node_id]
                if node["status"] != "PENDING":
                    continue
                predecessors = incoming[node_id]
                if not all(
                    current_status[source]["status"]
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
                # Keep the in-memory snapshot in lockstep with the row we just
                # mutated so subsequent iterations see the new status without
                # re-querying every node.
                node["status"] = status
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
