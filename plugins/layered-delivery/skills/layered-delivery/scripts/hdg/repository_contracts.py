from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .constants import SCHEMA_VERSION
from .errors import fail
from .evidence_validation import valid_evidence_record, valid_timestamp


WORK_ITEM_DATABASE_FILE = "governance.sqlite3"

PROJECTION_LOCK_FILE = "projection.lock"

LEGACY_REGISTRY_FILE = "work-item-registry.json"

WORK_ITEMS_DIRECTORY = "work-items"

GOVERNANCE_DIRECTORY = ".layered-delivery"

WORK_ITEM_REGISTRY_SCHEMA_VERSION = SCHEMA_VERSION

ENTRY_FIELDS = {
    "id", "kind", "gateLevel", "authorityKind", "parentId", "childIds", "packagePath",
    "developmentPlan", "stage", "status", "baselineFingerprint", "contractFingerprint",
    "parentContractFingerprint", "gate", "acceptance", "acceptanceReport", "developmentMode",
    "claim", "latestEvidence", "latestResult", "recordRevision", "createdAt", "updatedAt", "progress",
}

STATE_FIELDS = {
    "schemaVersion", "id", "stage", "baselineFingerprint", "contractFingerprint",
    "parentContractFingerprint", "hostRuntime", "createdAt", "frozenAt", "baselineRevision", "revisedAt", "review",
}

DATABASE_TABLES = {
    "workspace", "work_items", "hierarchies", "task_contexts", "reports",
    "interaction_events", "graph_definitions", "graph_nodes", "graph_edges",
    "graph_runs", "node_runs", "graph_events", "graph_evidence",
    "payload_uploads", "payload_chunks",
}

DATABASE_COLUMN_CONTRACTS = {
    "workspace": (
        "singleton", "schema_version", "coordination_root", "revision",
        "current_focus_json", "updated_at",
    ),
    "work_items": (
        "id", "entry_json", "definition_json", "state_json",
    ),
    "hierarchies": (
        "root_id", "hierarchy_state_json",
    ),
    "task_contexts": (
        "work_item_id", "context_json", "handoff_markdown", "updated_at",
    ),
    "reports": (
        "work_item_id", "report_kind", "report_json", "generated_at",
    ),
    "interaction_events": (
        "event_id", "event_uuid", "work_item_id", "session_id", "actor",
        "event_type", "summary", "operation_id", "host_runtime",
        "payload_json", "registry_revision", "recorded_at", "previous_hash",
        "event_hash",
    ),
    "graph_definitions": (
        "root_id", "hierarchy_fingerprint", "graph_fingerprint",
        "definition_json", "created_at", "frozen_at",
    ),
    "graph_nodes": (
        "graph_fingerprint", "node_id", "node_kind", "planes_json",
        "work_item_id",
    ),
    "graph_edges": (
        "graph_fingerprint", "edge_id", "source_node_id", "target_node_id",
        "edge_kind", "plane", "join_group",
    ),
    "payload_uploads": (
        "upload_id", "generation_id", "target_tool", "target_argument",
        "total_chunks", "status", "received_bytes", "received_chunks",
        "content_sha256", "created_at", "expires_at", "finalized_at",
    ),
    "payload_chunks": (
        "upload_id", "generation_id", "chunk_index", "chunk_sha256",
        "byte_size", "chunk_text",
    ),
    "graph_runs": (
        "run_id", "root_id", "graph_fingerprint", "status", "started_at",
        "updated_at", "completed_at", "cancelled_at", "record_revision",
    ),
    "node_runs": (
        "run_id", "node_id", "attempt", "status", "owner", "operation_id",
        "claimed_at", "finished_at", "latest_evidence_hash", "lease_expires_at",
        "last_heartbeat_at", "failure_class", "last_transition", "retry_exhausted",
        "record_revision",
    ),
    "graph_events": (
        "event_id", "event_uuid", "run_id", "graph_fingerprint", "node_id",
        "attempt", "event_type", "actor", "operation_id", "payload_json",
        "recorded_at", "previous_hash", "event_hash",
    ),
    "graph_evidence": (
        "evidence_id", "bound_evidence_sha256", "run_id",
        "graph_fingerprint", "node_id", "attempt", "artifact_sha256",
        "bound_artifact_json", "recorded_at",
    ),
}

def _plain_int(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum

def _valid_progress(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"directChildren", "descendants"}:
        return False
    for counts in value.values():
        if not isinstance(counts, dict) or set(counts) != {"total", "verified", "blocked", "active"}:
            return False
        if not all(_plain_int(count) for count in counts.values()):
            return False
        if counts["verified"] + counts["blocked"] + counts["active"] > counts["total"]:
            return False
    return True

def _valid_gate(value: object) -> bool:
    if not isinstance(value, dict) or value.get("status") not in {"NOT_RUN", "PASS", "FAIL"}:
        return False
    if value["status"] == "NOT_RUN":
        return set(value) == {"status", "evidence"} and value["evidence"] is None
    return (
        set(value) == {"status", "evidence", "artifact"}
        and valid_evidence_record(value["evidence"])
        and (value["artifact"] is None or isinstance(value["artifact"], dict))
    )

def _valid_claim(value: object) -> bool:
    valid = (
        isinstance(value, dict)
        and set(value) == {
            "owner", "operationId", "claimedAt", "lastHeartbeatAt", "leaseExpiresAt",
        }
        and isinstance(value.get("owner"), str)
        and bool(value["owner"])
        and isinstance(value.get("operationId"), str)
        and bool(value["operationId"])
        and valid_timestamp(value.get("claimedAt"))
        and valid_timestamp(value.get("lastHeartbeatAt"))
        and valid_timestamp(value.get("leaseExpiresAt"))
    )
    if not valid:
        return False
    claimed = datetime.fromisoformat(value["claimedAt"].replace("Z", "+00:00"))
    heartbeat = datetime.fromisoformat(value["lastHeartbeatAt"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(value["leaseExpiresAt"].replace("Z", "+00:00"))
    return claimed <= heartbeat < expires

def _valid_latest_result(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"evidence", "artifact", "recordedAt"}
        and valid_evidence_record(value.get("evidence"))
        and (value.get("artifact") is None or isinstance(value.get("artifact"), dict))
        and valid_timestamp(value.get("recordedAt"))
    )

def timestamp(now: object = None) -> str:
    value = now() if callable(now) else now
    if value is None:
        date = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        date = value
    elif isinstance(value, str):
        try:
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            fail("WORK_ITEM_TIMESTAMP_INVALID", "Work item timestamp is invalid")
    else:
        fail("WORK_ITEM_TIMESTAMP_INVALID", "Work item timestamp is invalid")
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def timestamp_after(value: object, seconds: int) -> str:
    at = timestamp(value)
    date = datetime.fromisoformat(at.replace("Z", "+00:00")) + timedelta(seconds=seconds)
    return timestamp(date)
