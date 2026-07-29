from __future__ import annotations

import json
import uuid
from copy import deepcopy
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .evidence_validation import (
    evidence_record,
    valid_timestamp,
)
from .graph_model import (
    graph_fingerprint,
    validate_delivery_graph,
)
from .jsonio import canonical_json, sha256_bytes

from .repository_contracts import (
    _plain_int,
)

def store_graph_definition(
    self,
    graph: dict[str, Any],
    *,
    graph_fingerprint_value: str,
    created_at: str,
) -> None:
    normalized = validate_delivery_graph(graph)
    if graph_fingerprint(normalized) != graph_fingerprint_value:
        fail("DELIVERY_GRAPH_FINGERPRINT_INVALID", "Delivery graph fingerprint does not match its definition")
    connection = self._active_connection()
    existing = connection.execute(
        "SELECT frozen_at FROM graph_definitions WHERE root_id = ?",
        (normalized["rootId"],),
    ).fetchone()
    if existing is not None and existing["frozen_at"] is not None:
        fail("DELIVERY_GRAPH_FROZEN", "A frozen delivery graph cannot be replaced")
    connection.execute(
        "DELETE FROM graph_definitions WHERE root_id = ?",
        (normalized["rootId"],),
    )
    connection.execute(
        "INSERT INTO graph_definitions(root_id, hierarchy_fingerprint, graph_fingerprint, "
        "definition_json, created_at, frozen_at) VALUES (?, ?, ?, ?, ?, NULL)",
        (
            normalized["rootId"],
            normalized["hierarchyFingerprint"],
            graph_fingerprint_value,
            canonical_json(normalized),
            created_at,
        ),
    )
    for node in normalized["nodes"]:
        connection.execute(
            "INSERT INTO graph_nodes(graph_fingerprint, node_id, node_kind, planes_json, work_item_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                graph_fingerprint_value,
                node["id"],
                node["kind"],
                canonical_json(node["planes"]),
                node["workItemId"],
            ),
        )
    for edge in normalized["edges"]:
        connection.execute(
            "INSERT INTO graph_edges(graph_fingerprint, edge_id, source_node_id, target_node_id, "
            "edge_kind, plane, join_group) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                graph_fingerprint_value,
                edge["id"],
                edge["source"],
                edge["target"],
                edge["kind"],
                edge["plane"],
                edge["joinGroup"],
            ),
        )

def read_graph_definition(self, root_id: str) -> dict[str, Any]:
    with self._read_connection() as connection:
        row = connection.execute(
            "SELECT hierarchy_fingerprint, graph_fingerprint, definition_json, created_at, frozen_at "
            "FROM graph_definitions WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        if row is None:
            fail("DELIVERY_GRAPH_MISSING", f"Delivery graph is missing: {root_id}")
        node_rows = connection.execute(
            "SELECT node_id, node_kind, planes_json, work_item_id FROM graph_nodes "
            "WHERE graph_fingerprint = ? ORDER BY node_id",
            (row["graph_fingerprint"],),
        ).fetchall()
        edge_rows = connection.execute(
            "SELECT edge_id, source_node_id, target_node_id, edge_kind, plane, join_group "
            "FROM graph_edges WHERE graph_fingerprint = ? ORDER BY edge_id",
            (row["graph_fingerprint"],),
        ).fetchall()
    try:
        graph = validate_delivery_graph(json.loads(row["definition_json"]))
        normalized_nodes = [
            {
                "id": item["node_id"],
                "kind": item["node_kind"],
                "planes": json.loads(item["planes_json"]),
                "workItemId": item["work_item_id"],
            }
            for item in node_rows
        ]
    except (TypeError, json.JSONDecodeError):
        fail("DELIVERY_GRAPH_INVALID", f"Stored delivery graph is invalid: {root_id}")
    normalized_edges = [
        {
            "id": item["edge_id"],
            "source": item["source_node_id"],
            "target": item["target_node_id"],
            "kind": item["edge_kind"],
            "plane": item["plane"],
            "joinGroup": item["join_group"],
        }
        for item in edge_rows
    ]
    if (
        graph["rootId"] != root_id
        or graph["hierarchyFingerprint"] != row["hierarchy_fingerprint"]
        or graph_fingerprint(graph) != row["graph_fingerprint"]
        or graph["nodes"] != normalized_nodes
        or graph["edges"] != normalized_edges
        or not valid_timestamp(row["created_at"])
        or (row["frozen_at"] is not None and not valid_timestamp(row["frozen_at"]))
    ):
        fail("DELIVERY_GRAPH_INVALID", f"Stored delivery graph changed: {root_id}")
    return {
        "graph": graph,
        "graphFingerprint": row["graph_fingerprint"],
        "createdAt": row["created_at"],
        "frozenAt": row["frozen_at"],
    }

def freeze_graph_definition(
    self,
    root_id: str,
    *,
    expected_graph_fingerprint: str,
    frozen_at: str,
) -> dict[str, Any]:
    stored = self.read_graph_definition(root_id)
    if stored["graphFingerprint"] != expected_graph_fingerprint:
        fail("WORK_ITEM_REVISION_CONFLICT", "The delivery graph fingerprint is not current")
    connection = self._active_connection()
    connection.execute(
        "UPDATE graph_definitions SET frozen_at = COALESCE(frozen_at, ?) WHERE root_id = ?",
        (frozen_at, root_id),
    )
    return self.read_graph_definition(root_id)

def start_graph_run(self, root_id: str, *, started_at: str) -> dict[str, Any]:
    stored = self.read_graph_definition(root_id)
    if stored["frozenAt"] is None:
        fail("DELIVERY_GRAPH_NOT_FROZEN", "Delivery graph must be frozen before it can run")
    connection = self._active_connection()
    existing = connection.execute(
        "SELECT run_id FROM graph_runs WHERE root_id = ?",
        (root_id,),
    ).fetchone()
    if existing is not None:
        return self.read_graph_run(root_id)
    run_id = f"run-{uuid.uuid4().hex}"
    connection.execute(
        "INSERT INTO graph_runs(run_id, root_id, graph_fingerprint, status, started_at, "
        "updated_at, completed_at, cancelled_at, record_revision) "
        "VALUES (?, ?, ?, 'ACTIVE', ?, ?, NULL, NULL, 1)",
        (run_id, root_id, stored["graphFingerprint"], started_at, started_at),
    )
    for node in stored["graph"]["nodes"]:
        connection.execute(
            "INSERT INTO node_runs(run_id, node_id, attempt, status, owner, operation_id, "
            "claimed_at, finished_at, latest_evidence_hash, lease_expires_at, "
            "last_heartbeat_at, failure_class, last_transition, retry_exhausted, record_revision) "
            "VALUES (?, ?, 1, 'PENDING', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, 1)",
            (run_id, node["id"]),
        )
    return self.read_graph_run(root_id)

def read_graph_run(
    self,
    root_id: str,
    *,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    with self._read_connection() as connection:
        row = connection.execute(
            "SELECT run_id, root_id, graph_fingerprint, status, started_at, updated_at, "
            "completed_at, cancelled_at, record_revision FROM graph_runs WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        if row is None:
            if allow_missing:
                return None
            fail("DELIVERY_GRAPH_RUN_MISSING", f"Delivery graph run is missing: {root_id}")
        node_rows = connection.execute(
            "SELECT run_id, node_id, attempt, status, owner, operation_id, claimed_at, "
            "finished_at, latest_evidence_hash, lease_expires_at, last_heartbeat_at, "
            "failure_class, last_transition, retry_exhausted, record_revision FROM node_runs "
            "WHERE run_id = ? ORDER BY node_id, attempt",
            (row["run_id"],),
        ).fetchall()
    if (
        row["status"] not in {"ACTIVE", "BLOCKED", "PAUSED", "CANCELLED", "COMPLETED"}
        or not valid_timestamp(row["started_at"])
        or not valid_timestamp(row["updated_at"])
        or (row["completed_at"] is not None and not valid_timestamp(row["completed_at"]))
        or (row["cancelled_at"] is not None and not valid_timestamp(row["cancelled_at"]))
        or not _plain_int(row["record_revision"], minimum=1)
    ):
        fail("DELIVERY_GRAPH_RUN_INVALID", f"Delivery graph run is invalid: {root_id}")
    attempts = [
        {
            "nodeId": item["node_id"],
            "attempt": item["attempt"],
            "status": item["status"],
            "owner": item["owner"],
            "operationId": item["operation_id"],
            "claimedAt": item["claimed_at"],
            "finishedAt": item["finished_at"],
            "latestEvidenceHash": item["latest_evidence_hash"],
            "leaseExpiresAt": item["lease_expires_at"],
            "lastHeartbeatAt": item["last_heartbeat_at"],
            "failureClass": item["failure_class"],
            "lastTransition": item["last_transition"],
            "retryExhausted": bool(item["retry_exhausted"]),
            "recordRevision": item["record_revision"],
        }
        for item in node_rows
    ]
    latest_by_node: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        latest_by_node[attempt["nodeId"]] = attempt
    return {
        "runId": row["run_id"],
        "rootId": row["root_id"],
        "graphFingerprint": row["graph_fingerprint"],
        "status": row["status"],
        "startedAt": row["started_at"],
        "updatedAt": row["updated_at"],
        "completedAt": row["completed_at"],
        "cancelledAt": row["cancelled_at"],
        "recordRevision": row["record_revision"],
        "nodes": [latest_by_node[node_id] for node_id in sorted(latest_by_node)],
        "attempts": attempts,
    }

def begin_graph_attempts(
    self,
    root_id: str,
    node_ids: list[str],
    *,
    at: str,
) -> list[dict[str, Any]]:
    run = self.read_graph_run(root_id)
    stored = self.read_graph_definition(root_id)
    known = {node["id"] for node in stored["graph"]["nodes"]}
    unknown = sorted(set(node_ids) - known)
    if unknown:
        fail("DELIVERY_GRAPH_NODE_INVALID", "Cannot retry unknown delivery graph nodes", nodes=unknown)
    connection = self._active_connection()
    attempts = []
    for node_id in sorted(set(node_ids)):
        current = connection.execute(
            "SELECT MAX(attempt) AS attempt FROM node_runs WHERE run_id = ? AND node_id = ?",
            (run["runId"], node_id),
        ).fetchone()["attempt"]
        attempt = current + 1
        connection.execute(
            "INSERT INTO node_runs(run_id, node_id, attempt, status, owner, operation_id, "
            "claimed_at, finished_at, latest_evidence_hash, lease_expires_at, "
            "last_heartbeat_at, failure_class, last_transition, retry_exhausted, record_revision) "
            "VALUES (?, ?, ?, 'PENDING', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, 1)",
            (run["runId"], node_id, attempt),
        )
        attempts.append({"nodeId": node_id, "attempt": attempt, "startedAt": at})
    return attempts

def append_graph_event(
    self,
    *,
    root_id: str,
    node_id: str | None,
    event_type: str,
    actor: str,
    operation_id: str | None,
    payload: dict[str, Any],
    recorded_at: str,
    evidence_artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run = self.read_graph_run(root_id)
    if node_id is not None:
        known = {node["nodeId"]: node for node in run["nodes"]}
        if node_id not in known:
            fail("DELIVERY_GRAPH_NODE_INVALID", "Graph event references an unknown node")
        attempt = known[node_id]["attempt"]
    else:
        attempt = None
    connection = self._active_connection()
    event_payload = deepcopy(payload)
    if evidence_artifact is not None:
        if node_id is None or attempt is None or "evidenceBinding" in event_payload:
            fail("DELIVERY_GRAPH_EVIDENCE_INVALID", "Graph evidence requires one unambiguous node attempt")
        artifact_sha256 = evidence_record(evidence_artifact)["sha256"]
        binding_material = {
            "schemaVersion": SCHEMA_VERSION,
            "runId": run["runId"],
            "nodeId": node_id,
            "attempt": attempt,
            "graphFingerprint": run["graphFingerprint"],
            "artifactSha256": artifact_sha256,
            "artifact": evidence_artifact,
        }
        bound_sha256 = sha256_bytes(canonical_json(binding_material).encode("utf-8"))
        binding = {
            key: binding_material[key]
            for key in (
                "schemaVersion", "runId", "nodeId", "attempt", "graphFingerprint",
                "artifactSha256",
            )
        }
        binding["boundEvidenceSha256"] = bound_sha256
        bound_artifact = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "GRAPH_BOUND_EVIDENCE",
            "binding": binding,
            "artifact": evidence_artifact,
        }
        connection.execute(
            "INSERT INTO graph_evidence(bound_evidence_sha256, run_id, graph_fingerprint, "
            "node_id, attempt, artifact_sha256, bound_artifact_json, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bound_sha256,
                run["runId"],
                run["graphFingerprint"],
                node_id,
                attempt,
                artifact_sha256,
                canonical_json(bound_artifact),
                recorded_at,
            ),
        )
        event_payload["evidenceBinding"] = binding
    previous_row = connection.execute(
        "SELECT event_hash FROM graph_events WHERE run_id = ? ORDER BY event_id DESC LIMIT 1",
        (run["runId"],),
    ).fetchone()
    previous_hash = previous_row["event_hash"] if previous_row else None
    event_uuid = str(uuid.uuid4())
    hash_payload = {
        "eventUuid": event_uuid,
        "runId": run["runId"],
        "graphFingerprint": run["graphFingerprint"],
        "nodeId": node_id,
        "attempt": attempt,
        "eventType": event_type,
        "actor": actor,
        "operationId": operation_id,
        "payload": event_payload,
        "recordedAt": recorded_at,
        "previousHash": previous_hash,
    }
    event_hash = sha256_bytes(canonical_json(hash_payload).encode("utf-8"))
    connection.execute(
        "INSERT INTO graph_events(event_uuid, run_id, graph_fingerprint, node_id, attempt, event_type, actor, "
        "operation_id, payload_json, recorded_at, previous_hash, event_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_uuid,
            run["runId"],
            run["graphFingerprint"],
            node_id,
            attempt,
            event_type,
            actor,
            operation_id,
            canonical_json(event_payload),
            recorded_at,
            previous_hash,
            event_hash,
        ),
    )
    return {**hash_payload, "eventHash": event_hash}

def read_graph_evidence(self, root_id: str) -> list[dict[str, Any]]:
    run = self.read_graph_run(root_id, allow_missing=True)
    if run is None:
        return []
    with self._read_connection() as connection:
        rows = connection.execute(
            "SELECT evidence_id, bound_evidence_sha256, run_id, graph_fingerprint, "
            "node_id, attempt, artifact_sha256, bound_artifact_json, recorded_at "
            "FROM graph_evidence WHERE run_id = ? ORDER BY evidence_id",
            (run["runId"],),
        ).fetchall()
    records = []
    for row in rows:
        try:
            bound_artifact = json.loads(row["bound_artifact_json"])
        except (TypeError, json.JSONDecodeError):
            fail("DELIVERY_GRAPH_EVIDENCE_INVALID", "Stored graph evidence is invalid")
        binding = bound_artifact.get("binding") if isinstance(bound_artifact, dict) else None
        artifact = bound_artifact.get("artifact") if isinstance(bound_artifact, dict) else None
        expected_binding = {
            "schemaVersion": SCHEMA_VERSION,
            "runId": row["run_id"],
            "nodeId": row["node_id"],
            "attempt": row["attempt"],
            "graphFingerprint": row["graph_fingerprint"],
            "artifactSha256": row["artifact_sha256"],
            "boundEvidenceSha256": row["bound_evidence_sha256"],
        }
        material = {
            key: expected_binding[key]
            for key in (
                "schemaVersion", "runId", "nodeId", "attempt", "graphFingerprint",
                "artifactSha256",
            )
        }
        material["artifact"] = artifact
        valid = (
            isinstance(bound_artifact, dict)
            and set(bound_artifact) == {"schemaVersion", "kind", "binding", "artifact"}
            and bound_artifact.get("schemaVersion") == SCHEMA_VERSION
            and bound_artifact.get("kind") == "GRAPH_BOUND_EVIDENCE"
            and binding == expected_binding
            and isinstance(artifact, dict)
            and evidence_record(artifact)["sha256"] == row["artifact_sha256"]
            and sha256_bytes(canonical_json(material).encode("utf-8"))
            == row["bound_evidence_sha256"]
            and row["graph_fingerprint"] == run["graphFingerprint"]
            and valid_timestamp(row["recorded_at"])
        )
        if not valid:
            fail("DELIVERY_GRAPH_EVIDENCE_INVALID", "Stored graph evidence binding changed")
        records.append({
            "evidenceId": row["evidence_id"],
            "boundEvidenceSha256": row["bound_evidence_sha256"],
            "boundArtifact": bound_artifact,
            "recordedAt": row["recorded_at"],
        })
    return records

def read_graph_events(
    self,
    root_id: str,
    *,
    after_event_id: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if (after_event_id is None) != (limit is None) or (
        after_event_id is not None
        and (
            not _plain_int(after_event_id)
            or not _plain_int(limit, minimum=1)
        )
    ):
        fail(
            "DELIVERY_GRAPH_EVENT_PAGE_INVALID",
            "Graph event cursor and limit must be supplied together",
        )
    run = self.read_graph_run(root_id, allow_missing=True)
    if run is None:
        return []
    result = []
    previous_hash = None
    evidence_bindings = {
        record["boundEvidenceSha256"]: record["boundArtifact"]["binding"]
        for record in self.read_graph_evidence(root_id)
    }
    with self._read_connection() as connection:
        rows = connection.execute(
            "SELECT event_id, event_uuid, run_id, graph_fingerprint, "
            "node_id, attempt, event_type, actor, operation_id, "
            "payload_json, recorded_at, previous_hash, event_hash "
            "FROM graph_events WHERE run_id = ? ORDER BY event_id",
            (run["runId"],),
        )
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                fail(
                    "DELIVERY_GRAPH_EVENT_INVALID",
                    "Stored graph event payload is invalid",
                )
            hash_payload = {
                "eventUuid": row["event_uuid"],
                "runId": row["run_id"],
                "graphFingerprint": row["graph_fingerprint"],
                "nodeId": row["node_id"],
                "attempt": row["attempt"],
                "eventType": row["event_type"],
                "actor": row["actor"],
                "operationId": row["operation_id"],
                "payload": payload,
                "recordedAt": row["recorded_at"],
                "previousHash": row["previous_hash"],
            }
            expected_hash = sha256_bytes(
                canonical_json(hash_payload).encode("utf-8")
            )
            binding = (
                payload.get("evidenceBinding")
                if isinstance(payload, dict)
                else None
            )
            binding_valid = (
                binding is None
                or (
                    isinstance(binding, dict)
                    and binding.get("runId") == run["runId"]
                    and binding.get("graphFingerprint")
                    == run["graphFingerprint"]
                    and binding.get("nodeId") == row["node_id"]
                    and binding.get("attempt") == row["attempt"]
                    and binding
                    == evidence_bindings.get(
                        binding.get("boundEvidenceSha256")
                    )
                )
            )
            if (
                row["graph_fingerprint"] != run["graphFingerprint"]
                or row["previous_hash"] != previous_hash
                or row["event_hash"] != expected_hash
                or not binding_valid
            ):
                fail(
                    "DELIVERY_GRAPH_EVENT_INVALID",
                    "Stored graph event chain is invalid",
                )
            previous_hash = row["event_hash"]
            if (
                after_event_id is not None
                and row["event_id"] <= after_event_id
            ):
                continue
            result.append({
                "eventId": row["event_id"],
                **hash_payload,
                "eventHash": row["event_hash"],
            })
            if limit is not None and len(result) >= limit:
                break
    return result

def sync_graph_runs(
    self,
    registry: dict[str, Any],
    *,
    root_ids: set[str] | None = None,
) -> None:
    from .graph_state import replay_graph_events

    connection = self._active_connection()
    roots_with_runs = [
        row["root_id"]
        for row in connection.execute("SELECT root_id FROM graph_runs ORDER BY root_id")
        if root_ids is None or row["root_id"] in root_ids
    ]
    for root_id in roots_with_runs:
        stored = self.read_graph_definition(root_id)
        run = self.read_graph_run(root_id)
        replay = replay_graph_events(
            stored["graph"],
            run,
            self.read_graph_events(root_id),
        )
        current_by_attempt = {
            (node["nodeId"], node["attempt"]): node
            for node in run["attempts"]
        }
        changed = False
        for state in replay["attempts"]:
            current = current_by_attempt[(state["nodeId"], state["attempt"])]
            desired = tuple(
                state[field]
                for field in (
                    "status", "owner", "operationId", "claimedAt", "finishedAt",
                    "latestEvidenceHash", "leaseExpiresAt", "lastHeartbeatAt",
                    "failureClass", "lastTransition", "retryExhausted", "recordRevision",
                )
            )
            actual = (
                current["status"], current["owner"], current["operationId"],
                current["claimedAt"], current["finishedAt"], current["latestEvidenceHash"],
                current["leaseExpiresAt"], current["lastHeartbeatAt"],
                current["failureClass"], current["lastTransition"], current["retryExhausted"],
                current["recordRevision"],
            )
            if desired == actual:
                continue
            connection.execute(
                "UPDATE node_runs SET status = ?, owner = ?, operation_id = ?, claimed_at = ?, "
                "finished_at = ?, latest_evidence_hash = ?, lease_expires_at = ?, "
                "last_heartbeat_at = ?, failure_class = ?, last_transition = ?, retry_exhausted = ?, "
                "record_revision = ? "
                "WHERE run_id = ? AND node_id = ? AND attempt = ?",
                (*desired, run["runId"], state["nodeId"], state["attempt"]),
            )
            changed = True
        if (
            changed
            or replay["status"] != run["status"]
            or replay["updatedAt"] != run["updatedAt"]
            or replay["completedAt"] != run["completedAt"]
            or replay["cancelledAt"] != run["cancelledAt"]
        ):
            connection.execute(
                "UPDATE graph_runs SET status = ?, updated_at = ?, completed_at = ?, cancelled_at = ?, "
                "record_revision = record_revision + 1 WHERE run_id = ?",
                (
                    replay["status"], replay["updatedAt"], replay["completedAt"],
                    replay["cancelledAt"],
                    run["runId"],
                ),
            )

def rebuild_graph_run_from_events(self, root_id: str) -> dict[str, Any]:
    from .graph_state import replay_graph_events

    connection = self._active_connection()
    stored = self.read_graph_definition(root_id)
    run = self.read_graph_run(root_id)
    replay = replay_graph_events(
        stored["graph"],
        run,
        self.read_graph_events(root_id),
    )
    connection.execute("DELETE FROM node_runs WHERE run_id = ?", (run["runId"],))
    for node in replay["attempts"]:
        connection.execute(
            "INSERT INTO node_runs(run_id, node_id, attempt, status, owner, operation_id, "
            "claimed_at, finished_at, latest_evidence_hash, lease_expires_at, "
            "last_heartbeat_at, failure_class, last_transition, retry_exhausted, record_revision) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run["runId"], node["nodeId"], node["attempt"], node["status"],
                node["owner"], node["operationId"], node["claimedAt"], node["finishedAt"],
                node["latestEvidenceHash"], node["leaseExpiresAt"],
                node["lastHeartbeatAt"], node["failureClass"],
                node["lastTransition"], int(node["retryExhausted"]), node["recordRevision"],
            ),
        )
    connection.execute(
        "UPDATE graph_runs SET status = ?, started_at = ?, updated_at = ?, completed_at = ?, cancelled_at = ?, "
        "record_revision = record_revision + 1 WHERE run_id = ?",
        (
            replay["status"], replay["startedAt"], replay["updatedAt"],
            replay["completedAt"], replay["cancelledAt"], run["runId"],
        ),
    )
    return replay
