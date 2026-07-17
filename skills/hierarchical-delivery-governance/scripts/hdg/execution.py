from __future__ import annotations

import json
import re
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import GatedLoopError, fail
from .evidence import evidence_record, valid_task_result_artifact
from .fs_safe import atomic_write, read_regular_file
from .jsonio import pretty_json, sha256_bytes
from .model import scope_patterns_overlap, work_item_child_contract_fingerprint
from .projections import render_task_handoff
from .repository import GovernanceRepository, timestamp


OPERATION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _safe_operation_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not OPERATION_ID.fullmatch(value):
        fail("WORK_ITEM_OPERATION_INVALID", f"{field} must be a safe lowercase identifier")
    return value


def _is_descendant(registry: dict[str, Any], entry: dict[str, Any], ancestor_id: str) -> bool:
    by_id = {item["id"]: item for item in registry["workItems"]}
    current = entry
    visited: set[str] = set()
    while current:
        if current["id"] == ancestor_id:
            return True
        if not current["parentId"] or current["id"] in visited:
            return False
        visited.add(current["id"])
        current = by_id.get(current["parentId"])
    return False


def select_development_mode(
    *,
    root: str,
    item_id: str,
    mode: str,
    expected_baseline_fingerprint: str,
    confirmed: bool = False,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if not confirmed:
        fail("CONFIRMATION_REQUIRED", "Development mode selection requires explicit user confirmation")
    if mode not in {"active", "manual"}:
        fail("WORK_ITEM_DEVELOPMENT_MODE_INVALID", "Development mode must be active or manual")
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        if entry["kind"] != "TASK" or entry["stage"] != "BASELINE_FROZEN":
            fail("WORK_ITEM_TASK_REQUIRED", "Development mode can only be selected for a frozen Task")
        if entry["baselineFingerprint"] != expected_baseline_fingerprint:
            fail("WORK_ITEM_REVISION_CONFLICT", "The development mode confirmation is not bound to the current baseline")
        if entry.get("claim") or entry["status"] not in {"WAITING_FOR_DEVELOPMENT_MODE_SELECTION", "FROZEN"}:
            fail("WORK_ITEM_DEVELOPMENT_MODE_LOCKED", "Development mode cannot change after Task dispatch begins")
        if (entry.get("developmentMode") or {}).get("mode") == mode:
            return {
                "created": False,
                "idempotent": True,
                "id": item_id,
                "status": entry["status"],
                "developmentMode": entry["developmentMode"],
            }
        if entry.get("developmentMode") is not None:
            fail("WORK_ITEM_DEVELOPMENT_MODE_LOCKED", "Development mode is fixed for the current Task baseline")
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "taskId": item_id,
            "baselineFingerprint": entry["baselineFingerprint"],
            "mode": mode,
            "confirmedBy": "user",
            "confirmedAt": at,
        }
        target = repository.item_path(entry) / "development-mode.json"
        atomic_write(target, pretty_json(record))
        entry["developmentMode"] = record
        entry["status"] = "FROZEN"
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["currentFocus"] = {
            "workItemId": item_id,
            "purpose": "ACTIVE_DISPATCH" if mode == "active" else "MANUAL_HANDOFF",
        }
        registry["revision"] += 1
        registry["updatedAt"] = at
        try:
            repository.write_registry(registry)
        except Exception:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            raise
        return {
            "created": True,
            "idempotent": False,
            "id": item_id,
            "status": entry["status"],
            "developmentMode": record,
        }


def _task_ready(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> bool:
    if (
        entry["kind"] != "TASK"
        or entry["stage"] != "BASELINE_FROZEN"
        or entry["status"] != "FROZEN"
        or entry.get("claim")
    ):
        return False
    definition = repository.assert_current_lineage(registry, entry)[0]
    if entry["parentId"] is not None:
        capability_entry = repository.item_by_id(registry, entry["parentId"])
        capability = repository.read_package(registry, capability_entry)[0]
        if any(
            next((item for item in registry["workItems"] if item["id"] == dependency), {}).get("status") != "VERIFIED"
            for dependency in capability["decomposition"]["dependsOn"]
        ):
            return False
    if any(
        next((item for item in registry["workItems"] if item["id"] == dependency), {}).get("status") != "VERIFIED"
        for dependency in definition["execution"]["dependsOn"]
    ):
        return False
    for claimed in (item for item in registry["workItems"] if item.get("claim")):
        claimed_definition = repository.read_package(registry, claimed)[0]
        if scope_patterns_overlap(definition["scope"], claimed_definition["scope"]):
            return False
    return True


def list_ready_tasks(*, root: str, work_item_id: str) -> list[str]:
    repository = GovernanceRepository(root)
    registry = repository.read_registry()
    repository.item_by_id(registry, work_item_id)
    return [
        entry["id"]
        for entry in sorted(registry["workItems"], key=lambda item: item["id"])
        if _is_descendant(registry, entry, work_item_id) and _task_ready(repository, registry, entry)
    ]


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
        if entry["kind"] == "TASK" and entry.get("developmentMode") is None:
            fail("WORK_ITEM_DEVELOPMENT_MODE_REQUIRED", f"{item_id} requires an explicitly confirmed development mode")
        if not _task_ready(repository, registry, entry):
            fail("WORK_ITEM_NOT_READY", f"{item_id} is not ready for dispatch")
        entry["claim"] = {
            "owner": _safe_operation_id(owner, "owner"),
            "operationId": _safe_operation_id(operation_id, "operationId"),
            "claimedAt": at,
        }
        entry["status"] = "CLAIMED"
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["currentFocus"] = {"workItemId": item_id, "purpose": "EXECUTION"}
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_registry(registry)
        return {"id": item_id, "status": entry["status"], "claim": entry["claim"]}


def _optional_task_result_artifact(
    repository: GovernanceRepository,
    evidence: object,
    *,
    item_id: str,
    operation_id: str,
    status: str,
    strict: bool,
) -> tuple[dict[str, str], dict[str, Any] | None]:
    reference = evidence_record(evidence)
    try:
        data = read_regular_file(repository.root, reference["path"])
    except Exception:
        if strict:
            fail("WORK_ITEM_RESULT_EVIDENCE_MISSING", f"Task result evidence is unavailable: {reference['path']}")
        return reference, None
    if sha256_bytes(data) != reference["sha256"]:
        fail("WORK_ITEM_RESULT_EVIDENCE_CHANGED", f"Task result evidence hash does not match: {reference['path']}")
    try:
        artifact = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("WORK_ITEM_RESULT_EVIDENCE_INVALID", "Task result evidence must be valid JSON")
    if not valid_task_result_artifact(artifact, item_id=item_id, operation_id=operation_id, status=status):
        fail("WORK_ITEM_RESULT_EVIDENCE_INVALID", "Task result evidence does not match the active operation")
    return reference, artifact


def record_task_result(
    *,
    root: str,
    item_id: str,
    operation_id: str,
    status: str,
    evidence: object,
    strict_evidence: bool = False,
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
        definition = repository.assert_current_lineage(registry, entry)[0]
        reference, artifact = _optional_task_result_artifact(
            repository,
            evidence,
            item_id=item_id,
            operation_id=operation_id,
            status=status,
            strict=strict_evidence,
        )
        entry["status"] = status
        entry["claim"] = None
        entry["latestEvidence"] = reference
        entry["latestResult"] = {"evidence": reference, "artifact": artifact, "recordedAt": at}
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_development_review(entry, definition, at)
        repository.write_registry(registry)
        base = f".hierarchical-delivery-governance/{entry['packagePath']}"
        return {
            "id": item_id,
            "status": status,
            "developmentReview": {
                "jsonPath": f"{base}/development-review.json",
                "markdownPath": f"{base}/development-review.md",
            },
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


def build_task_context(
    *,
    root: str,
    item_id: str,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    repository = GovernanceRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    registry = repository.read_registry()
    entry = repository.item_by_id(registry, item_id)
    if entry["kind"] != "TASK" or entry["stage"] != "BASELINE_FROZEN":
        fail("WORK_ITEM_TASK_REQUIRED", "Independent context can only be built for a frozen Task")
    if entry.get("developmentMode") is None:
        fail("WORK_ITEM_DEVELOPMENT_MODE_REQUIRED", f"{item_id} requires an explicitly confirmed development mode")
    definition, _, target = repository.assert_current_lineage(registry, entry)
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
        "developmentMode": entry["developmentMode"]["mode"],
        "operation": {
            "owner": entry["claim"]["owner"],
            "operationId": entry["claim"]["operationId"],
            "claimedAt": entry["claim"]["claimedAt"],
        } if entry.get("claim") else None,
        "task": {
            "id": definition["id"],
            "title": definition["title"],
            "goal": definition["goal"],
            "scope": definition["scope"],
            "baselineFingerprint": entry["baselineFingerprint"],
            "developmentPlan": definition["developmentPlan"],
        },
        "parentContracts": parents,
        "capabilityDependencies": capability_dependencies,
        "dependencies": dependencies,
        "requirements": definition["requirements"],
        "acceptance": definition["acceptance"],
        "execution": definition["execution"],
        "testCommands": definition["testCommands"],
        "rules": {
            "inheritConversation": False,
            "allowRequirementChanges": False,
            "allowExternalStateChanges": False,
        },
    }
    handoff = render_task_handoff(context)
    atomic_write(target / "context-manifest.json", pretty_json(context))
    atomic_write(target / "development-handoff.md", handoff)
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
    build_task_context(root=root, item_id=item_id, explicit_dogfood=explicit_dogfood)
    claim = claim_task(
        root=root,
        item_id=item_id,
        owner=owner,
        operation_id=operation_id,
        explicit_dogfood=explicit_dogfood,
        now=now,
    )
    context = build_task_context(root=root, item_id=item_id, explicit_dogfood=explicit_dogfood)
    return {**claim, **context}
