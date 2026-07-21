from __future__ import annotations

from copy import deepcopy
from typing import Any

from .errors import fail
from .evidence import evidence_record, valid_validation_remediation_artifact
from .graph_model import execution_node_id
from .model import scope_patterns_overlap
from .repository import GovernanceRepository, timestamp


def _hierarchy_chain(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    chain = [entry]
    current = entry
    visited = {entry["id"]}
    while current["parentId"] is not None:
        current = repository.item_by_id(registry, current["parentId"])
        if current["id"] in visited:
            fail("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle")
        visited.add(current["id"])
        chain.append(current)
    return chain


def _reset_for_remediation(entry: dict[str, Any], at: str) -> None:
    entry["status"] = "FROZEN"
    entry["gate"] = {"status": "NOT_RUN", "evidence": None}
    entry["latestEvidence"] = (
        entry["latestResult"]["evidence"] if entry.get("latestResult") else None
    )
    if entry["parentId"] is None:
        entry["acceptance"] = {
            "status": "NOT_READY",
            "review": None,
            "userConfirmation": None,
        }
    entry["recordRevision"] += 1
    entry["updatedAt"] = at


def _downstream_nodes(graph: dict[str, Any], source_node_id: str) -> list[str]:
    outgoing: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        outgoing.setdefault(edge["source"], []).append(edge["target"])
    visited: set[str] = set()
    pending = [source_node_id]
    while pending:
        node_id = pending.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        pending.extend(outgoing.get(node_id, []))
    return sorted(visited)


def record_validation_remediation(
    *,
    root: str,
    item_id: str,
    expected_baseline_fingerprint: str,
    evidence: object,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Append a same-contract validation repair to an unfinished frozen Task."""
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        if entry["kind"] != "TASK" or entry["stage"] != "BASELINE_FROZEN":
            fail("WORK_ITEM_REMEDIATION_TASK_REQUIRED", "Validation remediation requires a frozen Task")
        if entry.get("claim"):
            fail("WORK_ITEM_REMEDIATION_ACTIVE_CLAIM", "Release the active Task claim before recording remediation")
        if entry["baselineFingerprint"] != expected_baseline_fingerprint:
            fail("WORK_ITEM_REVISION_CONFLICT", "The remediation baseline fingerprint is not current")

        chain = _hierarchy_chain(repository, registry, entry)
        root_entry = chain[-1]
        stored_graph = repository.read_graph_definition(root_entry["id"])
        graph_run = repository.read_graph_run(root_entry["id"])
        invalidated_node_ids = _downstream_nodes(
            stored_graph["graph"],
            execution_node_id(item_id),
        )
        nodes_by_id = {
            node["id"]: node for node in stored_graph["graph"]["nodes"]
        }
        invalidated_work_item_ids = {
            nodes_by_id[node_id]["workItemId"] for node_id in invalidated_node_ids
        }
        active_invalidated_claims = sorted(
            candidate["id"]
            for candidate in registry["workItems"]
            if candidate["id"] in invalidated_work_item_ids and candidate.get("claim")
        )
        if active_invalidated_claims:
            fail(
                "DELIVERY_GRAPH_INVALIDATION_ACTIVE_CLAIM",
                "Graph invalidation cannot cross an actively claimed downstream Task",
                taskIds=active_invalidated_claims,
            )
        definition, state, _ = repository.assert_current_lineage(registry, entry)
        acceptance_ids = {item["id"] for item in definition["acceptance"]}
        if not valid_validation_remediation_artifact(
            evidence,
            item_id=item_id,
            baseline_fingerprint=entry["baselineFingerprint"],
            acceptance_ids=acceptance_ids,
        ):
            fail(
                "WORK_ITEM_REMEDIATION_EVIDENCE_INVALID",
                "Validation remediation must prove the frozen goal, contract, tests, topology, and authority are unchanged",
            )
        artifact = evidence
        reference = evidence_record(artifact)
        existing_remediations = repository.read_validation_remediations(item_id, definition)
        repeated = next(
            (record for record in existing_remediations if record["evidence"] == reference),
            None,
        )
        base = f".layered-delivery/{entry['packagePath']}"
        if repeated is not None:
            return {
                "id": item_id,
                "status": entry["status"],
                "baselineFingerprint": entry["baselineFingerprint"],
                "idempotent": True,
                "remediation": repeated,
                "authorizedFileChanges": repository.effective_task_file_changes(definition),
                "developmentReview": {"markdownPath": f"{base}/development-review.md"},
                "nextAction": "继续原 Task 的当前验证修正流程；不得创建重复需求根。",
            }
        if (root_entry.get("acceptance") or {}).get("status") == "COMPLETED":
            fail(
                "WORK_ITEM_REMEDIATION_COMPLETED",
                "A completed requirement is immutable; plan a new requirement for later feedback",
            )
        if entry["status"] not in {"IMPLEMENTED", "BLOCKED", "VERIFIED"}:
            fail(
                "WORK_ITEM_REMEDIATION_STAGE_INVALID",
                "Validation remediation requires an implemented, blocked, or verified Task",
            )

        existing_changes = repository.effective_task_file_changes(definition)
        existing_paths = {item["path"] for item in existing_changes}
        added_paths = {item["path"] for item in artifact["fileChanges"]}
        duplicate_paths = sorted(existing_paths & added_paths)
        if duplicate_paths:
            fail(
                "WORK_ITEM_REMEDIATION_FILE_ALREADY_AUTHORIZED",
                "Validation remediation can only append previously unauthorized exact files",
                paths=duplicate_paths,
            )

        for claimed in (item for item in registry["workItems"] if item.get("claim")):
            claimed_definition = repository.read_package(registry, claimed)[0]
            claimed_scope = list(claimed_definition["scope"])
            if claimed["kind"] == "TASK":
                claimed_scope.extend(
                    item["path"]
                    for item in repository.effective_task_file_changes(claimed_definition)
                )
            if scope_patterns_overlap(sorted(added_paths), claimed_scope):
                fail(
                    "WORK_ITEM_REMEDIATION_SCOPE_CONFLICT",
                    "Validation remediation overlaps an actively claimed Task",
                    claimedTaskId=claimed["id"],
                )

        previous_state = {
            "status": entry["status"],
            "gate": deepcopy(entry["gate"]),
            "acceptance": deepcopy(root_entry.get("acceptance")),
            "latestEvidence": deepcopy(entry.get("latestEvidence")),
            "latestResult": deepcopy(entry.get("latestResult")),
        }
        remediation_record = {
            "evidence": reference,
            "artifact": artifact,
            "recordedAt": at,
        }
        repository.append_interaction_event(
            work_item_id=item_id,
            session_id="controller",
            actor="AGENT",
            event_type="VALIDATION_REMEDIATION",
            summary=artifact["summary"],
            operation_id=None,
            host_runtime=state["hostRuntime"],
            payload={
                "remediation": remediation_record,
                "previousState": previous_state,
            },
            registry_revision=registry["revision"] + 1,
            recorded_at=at,
        )

        affected_entries = [
            candidate
            for candidate in registry["workItems"]
            if candidate["id"] in invalidated_work_item_ids
            and candidate["status"] in {"IMPLEMENTED", "BLOCKED", "VERIFIED"}
        ]
        if entry not in affected_entries:
            affected_entries.append(entry)
        current_nodes = {node["nodeId"]: node for node in graph_run["nodes"]}
        retry_node_ids = sorted(
            {
                node_id
                for node_id in invalidated_node_ids
                if current_nodes[node_id]["status"]
                in {"CLAIMED", "SUCCEEDED", "BLOCKED", "COMPLETED"}
            }
            | {execution_node_id(item_id)}
        )
        graph_attempts = repository.begin_graph_attempts(
            root_entry["id"],
            retry_node_ids,
            at=at,
        )
        for affected in affected_entries:
            _reset_for_remediation(affected, at)
        repository.append_graph_event(
            root_id=root_entry["id"],
            node_id=execution_node_id(item_id),
            event_type="GRAPH_INVALIDATED",
            actor="AGENT",
            operation_id=None,
            payload={
                "invalidatedNodeIds": invalidated_node_ids,
                "attempts": graph_attempts,
            },
            recorded_at=at,
        )
        registry["currentFocus"] = {
            "workItemId": item_id,
            "purpose": "VALIDATION_REMEDIATION_RETRY",
        }
        registry["revision"] += 1
        registry["updatedAt"] = at

        repository.write_development_review(entry, definition, at)
        for affected in affected_entries:
            if not affected.get("acceptanceReport"):
                continue
            affected_definition = repository.read_package(registry, affected)[0]
            repository.write_acceptance_report(affected, affected_definition, at)
        repository.write_registry(registry)
        return {
            "id": item_id,
            "status": entry["status"],
            "baselineFingerprint": entry["baselineFingerprint"],
            "idempotent": False,
            "remediation": remediation_record,
            "authorizedFileChanges": repository.effective_task_file_changes(definition),
            "developmentReview": {"markdownPath": f"{base}/development-review.md"},
            "invalidatedNodeIds": invalidated_node_ids,
            "graphAttempts": graph_attempts,
            "nextAction": "继续调度原 Task，完成验证修正、回归、复测和同一门禁；不得创建重复需求根。",
        }
