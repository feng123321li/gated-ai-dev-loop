from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .evidence import (
    evidence_record,
    task_result_artifact_issues,
    task_result_evidence_contract,
)
from .graph_model import (
    DEFAULT_CLAIM_GRACE_SECONDS,
    DEFAULT_CLAIM_LEASE_SECONDS,
    DEFAULT_HEARTBEAT_SECONDS,
    execution_node_id,
)
from .graph_runtime import (
    build_graph_frontier,
    evidence_contract_ref,
    failure_routing_decision,
    get_graph_frontier,
    replay_graph_events,
)
from .model import work_item_child_contract_fingerprint
from .projections import render_task_handoff
from .repository import GovernanceRepository, timestamp, timestamp_after


OPERATION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _claim_hard_expired(claim: dict[str, Any], at: str) -> bool:
    hard_expires_at = timestamp_after(
        claim["leaseExpiresAt"],
        DEFAULT_CLAIM_GRACE_SECONDS,
    )
    return _parse_timestamp(at) >= _parse_timestamp(hard_expires_at)


def _new_claim(*, owner: str, operation_id: str, at: str) -> dict[str, str]:
    return {
        "owner": _safe_operation_id(owner, "owner"),
        "operationId": _safe_operation_id(operation_id, "operationId"),
        "claimedAt": at,
        "lastHeartbeatAt": at,
        "leaseExpiresAt": timestamp_after(at, DEFAULT_CLAIM_LEASE_SECONDS),
    }


def _claim_payload(claim: dict[str, str], status: str) -> dict[str, str]:
    return {
        "owner": claim["owner"],
        "status": status,
        "lastHeartbeatAt": claim["lastHeartbeatAt"],
        "leaseExpiresAt": claim["leaseExpiresAt"],
    }


def _lease_policy(item_id: str, claim: dict[str, str]) -> dict[str, Any]:
    return {
        "leaseSeconds": DEFAULT_CLAIM_LEASE_SECONDS,
        "heartbeatIntervalSeconds": DEFAULT_HEARTBEAT_SECONDS,
        "graceSeconds": DEFAULT_CLAIM_GRACE_SECONDS,
        "heartbeatDueAt": timestamp_after(
            claim["lastHeartbeatAt"],
            DEFAULT_HEARTBEAT_SECONDS,
        ),
        "leaseExpiresAt": claim["leaseExpiresAt"],
        "hardExpiresAt": timestamp_after(
            claim["leaseExpiresAt"],
            DEFAULT_CLAIM_GRACE_SECONDS,
        ),
        "responsibleParty": "EXECUTION_ADAPTER",
        "commandHint": (
            f"heartbeat-task --item {item_id} "
            f"--operation {claim['operationId']}"
        ),
    }


def _safe_operation_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not OPERATION_ID.fullmatch(value):
        fail("WORK_ITEM_OPERATION_INVALID", f"{field} must be a safe lowercase identifier")
    return value


def _assert_operation_id_unused(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    entry: dict[str, Any],
    operation_id: str,
) -> None:
    root_entry = _hierarchy_root_entry(registry, entry)
    if any(
        event.get("eventType") == "TASK_CLAIMED"
        and event.get("operationId") == operation_id
        for event in repository.read_graph_events(root_entry["id"])
    ):
        fail(
            "WORK_ITEM_OPERATION_REUSED",
            "operationId must not be reused within the current graph run",
            operationId=operation_id,
            rootId=root_entry["id"],
        )


def _hierarchy_root_entry(registry: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    current = entry
    visited: set[str] = set()
    while current["parentId"] is not None:
        if current["id"] in visited:
            fail("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle")
        visited.add(current["id"])
        current = next(
            (item for item in registry["workItems"] if item["id"] == current["parentId"]),
            None,
        )
        if current is None:
            fail("WORK_ITEM_HIERARCHY_INVALID", "Work item hierarchy has a missing parent")
    return current


def _task_ready(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> bool:
    if repository.is_item_isolated(entry["id"]) or entry["kind"] != "TASK":
        return False
    root_entry = _hierarchy_root_entry(registry, entry)
    stored_graph = repository.read_graph_definition(root_entry["id"])
    graph_run = repository.read_graph_run(root_entry["id"], allow_missing=True)
    if graph_run is None:
        return False
    replay = replay_graph_events(
        stored_graph["graph"],
        graph_run,
        repository.read_graph_events(root_entry["id"]),
    )
    states = [
        {
            "id": node["nodeId"],
            **{key: value for key, value in node.items() if key != "nodeId"},
        }
        for node in replay["nodes"]
    ]
    frontier = build_graph_frontier(
        repository,
        registry,
        root_entry,
        stored_graph,
        graph_run,
        states,
    )
    return entry["id"] in frontier["dispatchPlan"]["dispatchTaskIds"]


def list_ready_tasks(*, root: str, work_item_id: str) -> list[str]:
    frontier = get_graph_frontier(root=root, work_item_id=work_item_id)
    return list(frontier["dispatchPlan"]["dispatchTaskIds"])


def _append_task_graph_event(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    entry: dict[str, Any],
    *,
    event_type: str,
    operation_id: str,
    payload: dict[str, Any],
    at: str,
    evidence_artifact: dict[str, Any] | None = None,
) -> None:
    root_entry = _hierarchy_root_entry(registry, entry)
    repository.append_graph_event(
        root_id=root_entry["id"],
        node_id=execution_node_id(entry["id"]),
        event_type=event_type,
        actor="AGENT",
        operation_id=operation_id,
        payload=payload,
        recorded_at=at,
        evidence_artifact=evidence_artifact,
    )


def claim_task(
    *,
    root: str,
    item_id: str,
    owner: str,
    operation_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        if entry["kind"] == "TASK" and _hierarchy_root_entry(registry, entry).get("developmentMode") is None:
            fail("WORK_ITEM_DEVELOPMENT_MODE_REQUIRED", f"{item_id} requires a development mode selected during requirement freeze")
        if not _task_ready(repository, registry, entry):
            fail("WORK_ITEM_NOT_READY", f"{item_id} is not ready for dispatch")
        operation_id = _safe_operation_id(operation_id, "operationId")
        _assert_operation_id_unused(
            repository,
            registry,
            entry,
            operation_id,
        )
        entry["claim"] = _new_claim(owner=owner, operation_id=operation_id, at=at)
        entry["status"] = "CLAIMED"
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["currentFocus"] = {"workItemId": item_id, "purpose": "EXECUTION"}
        registry["revision"] += 1
        registry["updatedAt"] = at
        _append_task_graph_event(
            repository,
            registry,
            entry,
            event_type="TASK_CLAIMED",
            operation_id=operation_id,
            payload=_claim_payload(entry["claim"], entry["status"]),
            at=at,
        )
        repository.write_registry(
            registry,
            changed_item_ids=repository.lineage_item_ids(registry, item_id),
        )
        return {
            "id": item_id,
            "status": entry["status"],
            "claim": entry["claim"],
            "leasePolicy": _lease_policy(item_id, entry["claim"]),
        }


def _validated_task_result_artifact(
    evidence: object,
    *,
    entry: dict[str, Any],
    definition: dict[str, Any],
    authorized_file_changes: list[dict[str, Any]],
    status: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    operation_id = entry["claim"]["operationId"]
    issues = task_result_artifact_issues(
        evidence,
        item_id=entry["id"],
        operation_id=operation_id,
        requested_status=status,
    )
    if issues:
        fail(
            "WORK_ITEM_RESULT_EVIDENCE_INVALID",
            "Task result evidence does not match the active operation",
            issues=issues,
            evidenceContract=task_result_evidence_contract(
                entry,
                definition,
                authorized_file_changes=authorized_file_changes,
            ),
        )
    return evidence_record(evidence), evidence


def record_task_result(
    *,
    root: str,
    item_id: str,
    operation_id: str,
    status: str,
    evidence: object,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if status not in {"IMPLEMENTED", "BLOCKED"}:
        fail("WORK_ITEM_RESULT_INVALID", "Task result must be IMPLEMENTED or BLOCKED")
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        if entry["kind"] != "TASK" or entry["status"] != "CLAIMED" or (entry.get("claim") or {}).get("operationId") != operation_id:
            fail("WORK_ITEM_OPERATION_INVALID", f"{item_id} does not have the supplied active operation")
        if _claim_hard_expired(entry["claim"], at):
            fail("WORK_ITEM_CLAIM_EXPIRED", f"{item_id} claim lease has expired")
        definition = repository.assert_current_lineage(registry, entry)[0]
        root_entry = _hierarchy_root_entry(registry, entry)
        stored_graph = repository.read_graph_definition(root_entry["id"])
        graph_run = repository.read_graph_run(root_entry["id"])
        node_id = execution_node_id(entry["id"])
        node_attempt = next(
            node["attempt"] for node in graph_run["nodes"] if node["nodeId"] == node_id
        )
        authorized_file_changes = repository.effective_task_file_changes(
            definition
        )
        reference, artifact = _validated_task_result_artifact(
            evidence,
            entry=entry,
            definition=definition,
            authorized_file_changes=authorized_file_changes,
            status=status,
        )
        entry["status"] = status
        entry["claim"] = None
        entry["latestEvidence"] = reference
        entry["latestResult"] = {"evidence": reference, "artifact": artifact, "recordedAt": at}
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["revision"] += 1
        registry["updatedAt"] = at
        _append_task_graph_event(
            repository,
            registry,
            entry,
            event_type="TASK_IMPLEMENTED" if status == "IMPLEMENTED" else "TASK_BLOCKED",
            operation_id=operation_id,
            payload={
                "status": status,
                "evidence": reference,
                "failure": artifact["failure"],
            },
            at=at,
            evidence_artifact=artifact,
        )
        routing_decision = None
        if status == "BLOCKED":
            routing_decision = failure_routing_decision(
                stored_graph["graph"],
                attempt=node_attempt,
                failure_class=artifact["failure"]["class"],
            )
            if routing_decision["action"] == "RETRY_NODE":
                attempts = repository.begin_graph_attempts(
                    root_entry["id"],
                    [node_id],
                    at=at,
                )
                repository.append_graph_event(
                    root_id=root_entry["id"],
                    node_id=node_id,
                    event_type="NODE_RETRY_SCHEDULED",
                    actor="CONTROLLER",
                    operation_id=None,
                    payload={
                        "attempts": attempts,
                        "failureClass": routing_decision["failureClass"],
                        "routeCondition": routing_decision["routeCondition"],
                    },
                    recorded_at=at,
                )
                entry["status"] = "FROZEN"
            elif routing_decision["action"] == "BLOCK_RUN":
                repository.append_graph_event(
                    root_id=root_entry["id"],
                    node_id=node_id,
                    event_type="RETRY_EXHAUSTED",
                    actor="CONTROLLER",
                    operation_id=None,
                    payload={
                        "failureClass": routing_decision["failureClass"],
                        "routeCondition": routing_decision["routeCondition"],
                        "maxAttempts": routing_decision["maxAttempts"],
                    },
                    recorded_at=at,
                )
        repository.write_development_review(entry, definition, at)
        repository.write_registry(
            registry,
            changed_item_ids=repository.lineage_item_ids(registry, item_id),
        )
        base = f".layered-delivery/{entry['packagePath']}"
        return {
            "id": item_id,
            "status": status,
            "developmentReview": {
                "markdownPath": f"{base}/development-review.md",
            },
            "routingDecision": routing_decision,
        }


def _parent_contract_snapshot(parent: dict[str, Any], child_id: str) -> dict[str, Any]:
    child = next(item for item in parent["children"] if item["id"] == child_id)
    return {
        "id": parent["id"],
        "kind": parent["kind"],
        "contractFingerprint": work_item_child_contract_fingerprint(parent, child_id),
        "goal": parent["goal"],
        "scope": parent["scope"],
        "childContract": child,
        "developmentPlan": parent["developmentPlan"],
    }


def _task_context(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> tuple[dict[str, Any], str, Any]:
    item_id = entry["id"]
    if entry["kind"] != "TASK" or entry["stage"] != "BASELINE_FROZEN":
        fail("WORK_ITEM_TASK_REQUIRED", "Independent context can only be built for a frozen Task")
    root_entry = _hierarchy_root_entry(registry, entry)
    if root_entry.get("developmentMode") is None:
        fail("WORK_ITEM_DEVELOPMENT_MODE_REQUIRED", f"{item_id} requires a development mode selected during requirement freeze")
    definition, _, target = repository.assert_current_lineage(registry, entry)
    validation_remediations = repository.read_validation_remediations(item_id, definition)
    authorized_file_changes = repository.effective_task_file_changes(definition)
    parents = []
    child_id = entry["id"]
    parent_id = entry["parentId"]
    while parent_id:
        parent_entry = repository.item_by_id(registry, parent_id)
        parent = repository.read_package(registry, parent_entry)[0]
        parents.insert(0, _parent_contract_snapshot(parent, child_id))
        child_id = parent["id"]
        parent_id = parent["parentId"]
    dependencies = []
    for dependency_id in definition["execution"]["dependsOn"]:
        dependency = repository.item_by_id(registry, dependency_id)
        dependency_definition = repository.read_package(registry, dependency)[0]
        dependencies.append({
            "id": dependency["id"],
            "status": dependency["status"],
            "outputs": dependency_definition["execution"]["outputs"],
            "evidence": dependency.get("latestEvidence"),
        })
    if any(item["status"] != "VERIFIED" for item in dependencies):
        fail("WORK_ITEM_NOT_READY", f"{item_id} has unverified Task dependencies")
    capability_dependencies = []
    if entry["parentId"] is not None:
        capability_entry = repository.item_by_id(registry, entry["parentId"])
        capability = repository.read_package(registry, capability_entry)[0]
        for dependency_id in capability["decomposition"]["dependsOn"]:
            dependency = repository.item_by_id(registry, dependency_id)
            capability_dependencies.append({
                "id": dependency["id"],
                "status": dependency["status"],
                "contractFingerprint": dependency["contractFingerprint"],
                "evidence": dependency.get("latestEvidence"),
            })
    if any(item["status"] != "VERIFIED" for item in capability_dependencies):
        fail("WORK_ITEM_NOT_READY", f"{item_id} has unverified Capability dependencies")
    context = {
        "schemaVersion": SCHEMA_VERSION,
        "gateLevel": definition["gateLevel"],
        "developmentMode": root_entry["developmentMode"]["mode"],
        "operation": {
            "owner": entry["claim"]["owner"],
            "operationId": entry["claim"]["operationId"],
            "claimedAt": entry["claim"]["claimedAt"],
            "lastHeartbeatAt": entry["claim"]["lastHeartbeatAt"],
            "leaseExpiresAt": entry["claim"]["leaseExpiresAt"],
        } if entry.get("claim") else None,
        "leasePolicy": (
            _lease_policy(item_id, entry["claim"])
            if entry.get("claim")
            else None
        ),
        "task": {
            "id": definition["id"],
            "title": definition["title"],
            "goal": definition["goal"],
            "scope": definition["scope"],
            "baselineFingerprint": entry["baselineFingerprint"],
            "developmentPlan": definition["developmentPlan"],
            "validationRemediations": validation_remediations,
            "authorizedFileChanges": authorized_file_changes,
        },
        "parentContracts": parents,
        "capabilityDependencies": capability_dependencies,
        "dependencies": dependencies,
        "requirements": definition["requirements"],
        "acceptance": definition["acceptance"],
        "execution": definition["execution"],
        "testCommands": definition["testCommands"],
        "evidenceContractRefs": {
            **({
                "result": evidence_contract_ref(item_id, "result"),
            } if entry.get("claim") else {}),
            "gate": evidence_contract_ref(item_id, "gate"),
            "remediation": evidence_contract_ref(item_id, "remediation"),
        },
        "rules": {
            "inheritConversation": False,
            "allowRequirementChanges": False,
            "allowExternalStateChanges": False,
        },
    }
    handoff = render_task_handoff(context)
    return context, handoff, target


def build_task_context(
    *,
    root: str,
    item_id: str,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    repository = GovernanceRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    registry = repository.read_operational_registry()
    entry = repository.item_by_id(registry, item_id)
    context, handoff, _ = _task_context(repository, registry, entry)
    return {**context, "handoffPrompt": handoff}


def dispatch_task(
    *,
    root: str,
    item_id: str,
    owner: str,
    operation_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        if entry["kind"] == "TASK" and _hierarchy_root_entry(registry, entry).get("developmentMode") is None:
            fail("WORK_ITEM_DEVELOPMENT_MODE_REQUIRED", f"{item_id} requires a development mode selected during requirement freeze")
        if not _task_ready(repository, registry, entry):
            fail("WORK_ITEM_NOT_READY", f"{item_id} is not ready for dispatch")
        operation_id = _safe_operation_id(operation_id, "operationId")
        _assert_operation_id_unused(
            repository,
            registry,
            entry,
            operation_id,
        )
        claim = _new_claim(owner=owner, operation_id=operation_id, at=at)
        entry["claim"] = claim
        entry["status"] = "CLAIMED"
        context, handoff, _ = _task_context(repository, registry, entry)
        repository.write_task_context(entry, context, handoff, at)
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["currentFocus"] = {"workItemId": item_id, "purpose": "EXECUTION"}
        registry["revision"] += 1
        registry["updatedAt"] = at
        _append_task_graph_event(
            repository,
            registry,
            entry,
            event_type="TASK_CLAIMED",
            operation_id=operation_id,
            payload=_claim_payload(claim, entry["status"]),
            at=at,
        )
        repository.write_registry(
            registry,
            changed_item_ids=repository.lineage_item_ids(registry, item_id),
        )
        return {
            "id": item_id,
            "status": entry["status"],
            "claim": claim,
            "leasePolicy": _lease_policy(item_id, claim),
            **context,
            "handoffPrompt": handoff,
        }


def heartbeat_task(
    *,
    root: str,
    item_id: str,
    operation_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        claim = entry.get("claim") or {}
        if entry["kind"] != "TASK" or entry["status"] != "CLAIMED" or claim.get("operationId") != operation_id:
            fail("WORK_ITEM_OPERATION_INVALID", f"{item_id} does not have the supplied active operation")
        if _claim_hard_expired(claim, at):
            fail("WORK_ITEM_CLAIM_EXPIRED", f"{item_id} claim lease has expired")
        claim["lastHeartbeatAt"] = at
        claim["leaseExpiresAt"] = timestamp_after(at, DEFAULT_CLAIM_LEASE_SECONDS)
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["currentFocus"] = {"workItemId": item_id, "purpose": "TASK_HEARTBEAT"}
        registry["revision"] += 1
        registry["updatedAt"] = at
        _append_task_graph_event(
            repository,
            registry,
            entry,
            event_type="TASK_HEARTBEAT",
            operation_id=operation_id,
            payload={
                "lastHeartbeatAt": claim["lastHeartbeatAt"],
                "leaseExpiresAt": claim["leaseExpiresAt"],
            },
            at=at,
        )
        root_id = _hierarchy_root_entry(registry, entry)["id"]
        repository.write_registry(
            registry,
            changed_item_ids={item_id},
            projection_mode="heartbeat",
            projection_root_id=root_id,
        )
        return {
            "id": item_id,
            "status": entry["status"],
            "claim": claim,
            "leasePolicy": _lease_policy(item_id, claim),
        }


def pause_task(
    *,
    root: str,
    item_id: str,
    operation_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        claim = entry.get("claim") or {}
        if entry["kind"] != "TASK" or entry["status"] != "CLAIMED" or claim.get("operationId") != operation_id:
            fail("WORK_ITEM_OPERATION_INVALID", f"{item_id} does not have the supplied active operation")
        if _claim_hard_expired(claim, at):
            fail("WORK_ITEM_CLAIM_EXPIRED", f"{item_id} claim lease has expired")
        _append_task_graph_event(
            repository,
            registry,
            entry,
            event_type="NODE_PAUSED",
            operation_id=operation_id,
            payload={"reason": "explicit-pause"},
            at=at,
        )
        entry["claim"] = None
        entry["status"] = "FROZEN"
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["currentFocus"] = {"workItemId": item_id, "purpose": "TASK_PAUSED"}
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_registry(
            registry,
            changed_item_ids=repository.lineage_item_ids(registry, item_id),
        )
        return {"id": item_id, "status": entry["status"], "nodeStatus": "PAUSED"}


def resume_task(
    *,
    root: str,
    item_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        if entry["kind"] != "TASK" or entry["status"] != "FROZEN" or entry.get("claim") is not None:
            fail("WORK_ITEM_NOT_PAUSED", f"{item_id} is not paused")
        root_entry = _hierarchy_root_entry(registry, entry)
        stored = repository.read_graph_definition(root_entry["id"])
        run = repository.read_graph_run(root_entry["id"])
        replay = replay_graph_events(
            stored["graph"], run, repository.read_graph_events(root_entry["id"])
        )
        node = next(
            state for state in replay["nodes"]
            if state["nodeId"] == execution_node_id(item_id)
        )
        if node["status"] != "PAUSED":
            fail("WORK_ITEM_NOT_PAUSED", f"{item_id} is not paused")
        _append_task_graph_event(
            repository,
            registry,
            entry,
            event_type="NODE_RESUMED",
            operation_id=None,
            payload={"reason": "explicit-resume"},
            at=at,
        )
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["currentFocus"] = {"workItemId": item_id, "purpose": "TASK_RESUMED"}
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_registry(
            registry,
            changed_item_ids=repository.lineage_item_ids(registry, item_id),
        )
        return {"id": item_id, "status": entry["status"], "nodeStatus": "READY"}
