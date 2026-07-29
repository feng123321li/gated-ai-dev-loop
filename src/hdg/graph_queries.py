from __future__ import annotations

from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .evidence import (
    confirmation_evidence_contract,
    review_evidence_contract,
)
from .graph_model import graph_summary
from .graph_contracts import (
    _gate_contract,
    _remediation_contract,
    _result_contract,
    mcp_call,
)

from .graph_state import (
    hierarchy_root_entry,
    derive_node_states,
    replay_graph_events,
    replay_mismatches,
    critical_path,
)

def _root_for_requested_item(
    registry: dict[str, Any],
    work_item_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    by_id = {item["id"]: item for item in registry["workItems"]}
    entry = by_id.get(work_item_id)
    if entry is None:
        fail("WORK_ITEM_NOT_FOUND", f"Unknown work item: {work_item_id}", id=work_item_id)
    return entry, hierarchy_root_entry(registry, entry)


def _load_graph_view(
    *,
    root: str,
    work_item_id: str,
    now: object = None,
) -> tuple[
    Any,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any] | None,
]:
    from .repository import GovernanceRepository

    repository = GovernanceRepository(root, now=now)
    registry = repository.read_operational_registry()
    requested, root_entry = _root_for_requested_item(registry, work_item_id)
    stored = repository.read_graph_definition(root_entry["id"])
    run = repository.read_graph_run(root_entry["id"], allow_missing=True)
    return repository, registry, requested, stored, run


def get_evidence_contract(
    *,
    root: str,
    work_item_id: str,
    contract_kind: str,
) -> dict[str, Any]:
    """Read the current compact evidence contract from SQLite on demand."""
    from .repository import GovernanceRepository

    if contract_kind not in {
        "result", "gate", "remediation", "review", "confirmation",
    }:
        fail(
            "WORK_ITEM_EVIDENCE_CONTRACT_KIND_INVALID",
            "Evidence contract kind must be result, gate, remediation, review, or confirmation",
        )
    repository = GovernanceRepository(root)
    registry = repository.read_operational_registry()
    entry = repository.item_by_id(registry, work_item_id)
    if contract_kind == "result":
        contract = _result_contract(repository, registry, entry)
        operation_id = entry["claim"]["operationId"]
        submit_mcp_calls = [
            mcp_call(
                "task_result",
                item_id=work_item_id,
                operation_id=operation_id,
                status="<IMPLEMENTED_OR_BLOCKED>",
                evidence="<evidence>",
            )
        ]
    elif contract_kind == "gate":
        contract = _gate_contract(repository, registry, entry)
        submit_mcp_calls = [
            mcp_call(
                "accept_item",
                item_id=work_item_id,
                evidence="<evidence>",
            )
        ]
    elif contract_kind == "remediation":
        if entry["kind"] != "TASK":
            fail(
                "WORK_ITEM_REMEDIATION_TASK_REQUIRED",
                "Validation remediation evidence contracts require a frozen Task",
            )
        contract = _remediation_contract(repository, registry, entry)
        submit_mcp_calls = [
            mcp_call(
                "remediate_task",
                item_id=work_item_id,
                expected_baseline_fingerprint=entry["baselineFingerprint"],
                evidence="<evidence>",
            )
        ]
    else:
        if entry["parentId"] is not None:
            fail(
                "WORK_ITEM_ACCEPTANCE_ROOT_REQUIRED",
                "Review and confirmation evidence contracts require a root work item",
            )
        if contract_kind == "review":
            contract = review_evidence_contract(
                repository.effective_required_skills(
                    registry,
                    entry,
                    stage="FINAL_REVIEW",
                )
            )
            review_tools = {
                "INDEPENDENT_REVIEW_PASS": "record_independent_review_pass",
                "REVIEW_BLOCKED": "record_independent_review_blocked",
                "HUMAN_REVIEW_ACCEPTED": "record_human_review_acceptance",
            }
            submit_mcp_calls = [
                mcp_call(
                    review_tools[action],
                    item_id=work_item_id,
                    evidence=f"<{action.lower()}-evidence>",
                )
                for action in contract["actionOptions"]
            ]
        else:
            contract = confirmation_evidence_contract()
            submit_mcp_calls = [
                mcp_call(
                    "record_user_confirmation",
                    item_id=work_item_id,
                    evidence="<evidence>",
                )
            ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": "governance.sqlite3",
        "itemId": work_item_id,
        "contractKind": contract_kind,
        "submitMcpCalls": submit_mcp_calls,
        "evidenceContract": contract,
    }


def get_graph_status(*, root: str, work_item_id: str) -> dict[str, Any]:
    repository, registry, requested, stored, run = _load_graph_view(
        root=root,
        work_item_id=work_item_id,
    )
    graph = stored["graph"]
    replay: dict[str, Any] | None = None
    if run is None:
        nodes = [
            {
                **state,
                "attempt": None,
                "owner": None,
                "operationId": None,
                "claimedAt": None,
                "finishedAt": None,
                "latestEvidenceHash": None,
                "leaseExpiresAt": None,
                "lastHeartbeatAt": None,
                "failureClass": None,
                "lastTransition": None,
                "retryExhausted": False,
                "recordRevision": None,
            }
            for state in derive_node_states(graph, registry)
        ]
    else:
        replay = replay_graph_events(
            graph,
            run,
            repository.read_graph_events(graph["rootId"]),
        )
        mismatches = replay_mismatches(replay, run)
        if mismatches:
            fail(
                "DELIVERY_GRAPH_REPLAY_MISMATCH",
                "Persisted graph snapshots do not match the immutable event replay",
                mismatches=mismatches,
            )
        nodes = [
            {"id": node["nodeId"], **{key: value for key, value in node.items() if key != "nodeId"}}
            for node in replay["nodes"]
        ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "rootId": graph["rootId"],
        "requestedItemId": requested["id"],
        "hierarchyFingerprint": graph["hierarchyFingerprint"],
        "graphFingerprint": stored["graphFingerprint"],
        "graphSummary": graph_summary(graph),
        "run": None if run is None else {
            key: run[key]
            for key in (
                "runId", "status", "startedAt", "updatedAt", "completedAt", "cancelledAt",
                "recordRevision",
            )
        },
        "nodes": nodes,
        "edges": graph["edges"],
        "runtime": graph["runtime"],
        "criticalPath": critical_path(graph, nodes),
        "replay": None if replay is None else {
            "eventCount": replay["eventCount"],
            "replayFingerprint": replay["replayFingerprint"],
            "consistentWithSnapshots": True,
        },
    }


def get_graph_replay(*, root: str, work_item_id: str) -> dict[str, Any]:
    repository, _, _, stored, run = _load_graph_view(
        root=root,
        work_item_id=work_item_id,
    )
    if run is None:
        fail("DELIVERY_GRAPH_RUN_MISSING", "Delivery graph has not been frozen")
    replay = replay_graph_events(
        stored["graph"],
        run,
        repository.read_graph_events(stored["graph"]["rootId"]),
    )
    mismatches = replay_mismatches(replay, run)
    return {
        **replay,
        "consistentWithSnapshots": not mismatches,
        "mismatches": mismatches,
    }
