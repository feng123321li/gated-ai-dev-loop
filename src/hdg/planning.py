from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .fs_safe import atomic_write, safe_path
from .jsonio import pretty_json
from .model import (
    raw_definition,
    render_development_review,
    validate_work_item_definition,
    work_item_baseline_fingerprint,
    work_item_child_contract_fingerprint,
    work_item_contract_fingerprint,
)
from .projections import item_human_artifacts, next_action
from .repository import (
    GOVERNANCE_DIRECTORY,
    WORK_ITEMS_DIRECTORY,
    GovernanceRepository,
    entry_from_definition,
    timestamp,
)


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


def _validate_task_dependencies(definition: dict[str, Any], parent: dict[str, Any] | None) -> None:
    if definition["kind"] != "TASK":
        return
    if parent is None:
        if definition["execution"]["dependsOn"]:
            fail("WORK_ITEM_DEPENDENCY_INVALID", "A root Task cannot depend on sibling Tasks; use a Capability root")
        return
    sibling_ids = {item["id"] for item in parent["children"]}
    if any(item not in sibling_ids for item in definition["execution"]["dependsOn"]):
        fail("WORK_ITEM_DEPENDENCY_INVALID", "Task dependsOn must reference planned sibling Tasks")


def _validate_capability_graph(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    candidate: dict[str, Any],
) -> None:
    if candidate["kind"] != "CAPABILITY":
        return
    graph: dict[str, list[str]] = {}
    for entry in registry["workItems"]:
        if entry["kind"] != "CAPABILITY" or entry["parentId"] != candidate["parentId"]:
            continue
        definition = candidate if entry["id"] == candidate["id"] else repository.read_package(registry, entry)[0]
        graph[definition["id"]] = definition["decomposition"]["dependsOn"]
    graph[candidate["id"]] = candidate["decomposition"]["dependsOn"]
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item_id: str) -> None:
        if item_id in visiting:
            fail("WORK_ITEM_DEPENDENCY_CYCLE", "Capability dependencies contain a cycle")
        if item_id in visited:
            return
        visiting.add(item_id)
        for dependency in graph.get(item_id, []):
            if dependency in graph:
                visit(dependency)
        visiting.remove(item_id)
        visited.add(item_id)

    for item_id in graph:
        visit(item_id)


def _state(definition: dict[str, Any], host_runtime: str, at: str) -> dict[str, Any]:
    baseline = work_item_baseline_fingerprint(definition)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": definition["id"],
        "stage": "WAITING_FOR_BASELINE_CONFIRMATION",
        "baselineFingerprint": baseline,
        "contractFingerprint": work_item_contract_fingerprint(definition),
        "parentContractFingerprint": definition["parentContractFingerprint"],
        "hostRuntime": host_runtime,
        "createdAt": at,
        "frozenAt": None,
        "baselineRevision": 1,
        "revisedAt": None,
        "review": {
            "schemaVersion": SCHEMA_VERSION,
            "status": "WAITING_FOR_HUMAN_REVIEW",
            "baselineFingerprint": baseline,
            "reviewedBy": None,
            "reviewedAt": None,
        },
    }


def prepare_work_item(
    *,
    root: str,
    definition: dict[str, Any],
    host_runtime: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    from .host_runtime import require_host_runtime

    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    runtime = require_host_runtime(host_runtime)
    with repository.transaction() as registry:
        existing = next((item for item in registry["workItems"] if item["id"] == definition.get("id")), None)
        if existing:
            current_definition, current_state, target = repository.read_package(registry, existing)
            parent = None
            if definition["kind"] != "DELIVERY" and definition["parentId"] is not None:
                parent_entry = repository.item_by_id(registry, definition["parentId"])
                parent = repository.read_package(registry, parent_entry)[0]
            candidate = validate_work_item_definition(definition, parent=parent)
            _validate_task_dependencies(candidate, parent)
            _validate_capability_graph(repository, registry, candidate)
            candidate_fingerprint = work_item_baseline_fingerprint(candidate)
            if candidate_fingerprint != current_state["baselineFingerprint"]:
                if (
                    existing["stage"] != "WAITING_FOR_BASELINE_CONFIRMATION"
                    or candidate["id"] != existing["id"]
                    or candidate["kind"] != existing["kind"]
                    or candidate["parentId"] != existing["parentId"]
                ):
                    fail("WORK_ITEM_SOURCE_CHANGED", f"{existing['id']} prepared baseline differs from the requested definition")
                revised_state = {
                    **current_state,
                    "stage": "WAITING_FOR_BASELINE_CONFIRMATION",
                    "baselineFingerprint": candidate_fingerprint,
                    "contractFingerprint": work_item_contract_fingerprint(candidate),
                    "parentContractFingerprint": candidate["parentContractFingerprint"],
                    "hostRuntime": runtime,
                    "baselineRevision": current_state["baselineRevision"] + 1,
                    "revisedAt": at,
                    "frozenAt": None,
                    "review": {
                        "schemaVersion": SCHEMA_VERSION,
                        "status": "WAITING_FOR_HUMAN_REVIEW",
                        "baselineFingerprint": candidate_fingerprint,
                        "reviewedBy": None,
                        "reviewedAt": None,
                    },
                }
                repository.replace_package(target, repository.package_files(candidate, revised_state))
                existing.update({
                    "gateLevel": candidate["gateLevel"],
                    "childIds": [item["id"] for item in candidate.get("children", [])],
                    "baselineFingerprint": revised_state["baselineFingerprint"],
                    "contractFingerprint": revised_state["contractFingerprint"],
                    "parentContractFingerprint": revised_state["parentContractFingerprint"],
                    "recordRevision": existing["recordRevision"] + 1,
                    "updatedAt": at,
                })
                registry["currentFocus"] = {"workItemId": existing["id"], "purpose": "BASELINE_CONFIRMATION"}
                registry["revision"] += 1
                registry["updatedAt"] = at
                repository.write_registry(registry)
                return {
                    "created": False,
                    "idempotent": False,
                    "revised": True,
                    "id": existing["id"],
                    "kind": existing["kind"],
                    "stage": existing["stage"],
                    "baselineFingerprint": existing["baselineFingerprint"],
                    "artifactDir": str(target),
                    "humanArtifacts": item_human_artifacts(existing["id"], existing.get("acceptanceReport")),
                    "nextAction": next_action(existing),
                }
            return {
                "created": False,
                "idempotent": True,
                "id": existing["id"],
                "kind": existing["kind"],
                "stage": existing["stage"],
                "baselineFingerprint": existing["baselineFingerprint"],
                "artifactDir": str(target),
                "humanArtifacts": item_human_artifacts(existing["id"], existing.get("acceptanceReport")),
                "nextAction": next_action(existing),
            }

        parent = None
        if definition["kind"] != "DELIVERY" and definition["parentId"] is not None:
            parent_entry = repository.item_by_id(registry, definition["parentId"])
            if parent_entry["stage"] != "BASELINE_FROZEN":
                fail("WORK_ITEM_PARENT_NOT_FROZEN", "Parent baseline must be frozen first")
            parent = repository.assert_current_lineage(registry, parent_entry)[0]
        normalized = validate_work_item_definition(definition, parent=parent)
        _validate_task_dependencies(normalized, parent)
        _validate_capability_graph(repository, registry, normalized)
        state = _state(normalized, runtime, at)
        target = safe_path(root, f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}/{normalized['id']}")
        repository.write_new_package(target, repository.package_files(normalized, state))
        entry = entry_from_definition(normalized, state, at)
        registry["workItems"].append(entry)
        if entry["parentId"]:
            parent_entry = repository.item_by_id(registry, entry["parentId"])
            parent_entry["childIds"] = sorted(set(parent_entry["childIds"] + [entry["id"]]))
            parent_entry["recordRevision"] += 1
            parent_entry["updatedAt"] = at
        registry["currentFocus"] = {"workItemId": entry["id"], "purpose": "BASELINE_CONFIRMATION"}
        registry["revision"] += 1
        registry["updatedAt"] = at
        try:
            repository.write_registry(registry)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return {
            "created": True,
            "idempotent": False,
            "id": entry["id"],
            "kind": entry["kind"],
            "stage": entry["stage"],
            "baselineFingerprint": entry["baselineFingerprint"],
            "artifactDir": str(target),
            "humanArtifacts": item_human_artifacts(entry["id"]),
            "nextAction": next_action(entry),
        }


def freeze_work_item(
    *,
    root: str,
    item_id: str,
    expected_baseline_fingerprint: str,
    confirmed: bool = False,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if not confirmed:
        fail("CONFIRMATION_REQUIRED", "Work item baseline freeze requires explicit confirmation")
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        if entry["baselineFingerprint"] != expected_baseline_fingerprint:
            fail("WORK_ITEM_REVISION_CONFLICT", "The confirmed baseline fingerprint is not current")
        definition, state, target = repository.assert_current_lineage(registry, entry)
        if entry["stage"] == "BASELINE_FROZEN":
            return {
                "created": False,
                "idempotent": True,
                "id": item_id,
                "stage": entry["stage"],
                "baselineFingerprint": entry["baselineFingerprint"],
                "humanArtifacts": item_human_artifacts(item_id, entry.get("acceptanceReport")),
                "nextAction": next_action(entry),
            }
        if entry["stage"] != "WAITING_FOR_BASELINE_CONFIRMATION":
            fail("WORK_ITEM_STAGE_INVALID", f"{item_id} is not ready to freeze")
        frozen_state = {
            **state,
            "stage": "BASELINE_FROZEN",
            "frozenAt": at,
            "review": {
                **state["review"],
                "status": "APPROVED",
                "reviewedBy": "user",
                "reviewedAt": at,
            },
        }
        atomic_write(target / "state.json", pretty_json(frozen_state))
        atomic_write(target / "development-review.md", render_development_review(definition, frozen_state))
        entry["stage"] = "BASELINE_FROZEN"
        entry["status"] = "WAITING_FOR_DEVELOPMENT_MODE_SELECTION" if entry["kind"] == "TASK" else "FROZEN"
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["currentFocus"] = {
            "workItemId": item_id,
            "purpose": "DEVELOPMENT_MODE_SELECTION" if entry["kind"] == "TASK" else "DECOMPOSITION",
        }
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_registry(registry)
        return {
            "created": True,
            "idempotent": False,
            "id": item_id,
            "stage": entry["stage"],
            "baselineFingerprint": entry["baselineFingerprint"],
            "humanArtifacts": item_human_artifacts(item_id, entry.get("acceptanceReport")),
            "nextAction": next_action(entry),
        }


def retry_work_item(
    *,
    root: str,
    item_id: str,
    expected_baseline_fingerprint: str,
    confirmed: bool = False,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if not confirmed:
        fail("CONFIRMATION_REQUIRED", "Work item retry requires explicit confirmation")
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        if entry["status"] != "BLOCKED" or entry.get("claim"):
            fail("WORK_ITEM_RETRY_INVALID", "Only an unclaimed BLOCKED work item can be retried")
        if entry["baselineFingerprint"] != expected_baseline_fingerprint:
            fail("WORK_ITEM_REVISION_CONFLICT", "The retry baseline fingerprint is not current")
        definition = repository.assert_current_lineage(registry, entry)[0]
        entry["status"] = "FROZEN"
        entry["gate"] = {"status": "NOT_RUN", "evidence": None}
        if entry["parentId"] is None:
            entry["acceptance"] = {"status": "NOT_READY", "review": None, "userConfirmation": None}
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["currentFocus"] = {
            "workItemId": item_id,
            "purpose": "EXECUTION_RETRY" if entry["kind"] == "TASK" else "AGGREGATE_GATE_RETRY",
        }
        registry["revision"] += 1
        registry["updatedAt"] = at
        if entry.get("acceptanceReport"):
            repository.write_acceptance_report(entry, definition, at)
        repository.write_registry(registry)
        return {"id": item_id, "status": entry["status"], "baselineFingerprint": entry["baselineFingerprint"]}


def revise_work_item(
    *,
    root: str,
    definition: dict[str, Any],
    expected_baseline_fingerprint: str,
    confirmed: bool = False,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if not confirmed:
        fail("CONFIRMATION_REQUIRED", "Work item baseline revision requires explicit confirmation")
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, definition.get("id"))
        if entry["stage"] != "BASELINE_FROZEN":
            fail("WORK_ITEM_STAGE_INVALID", "Only frozen work items can be revised")
        if entry["status"] == "VERIFIED":
            fail("WORK_ITEM_REVISION_AFTER_VERIFICATION", "Verified work items cannot be revised")
        if entry["status"] == "BLOCKED":
            fail("WORK_ITEM_RETRY_REQUIRED", "A BLOCKED work item must be explicitly retried before baseline revision")
        if entry["baselineFingerprint"] != expected_baseline_fingerprint:
            fail("WORK_ITEM_REVISION_CONFLICT", "The expected baseline fingerprint is not current")
        current_definition, current_state, target = repository.assert_current_lineage(registry, entry)
        parent = None
        if entry["parentId"]:
            parent_entry = repository.item_by_id(registry, entry["parentId"])
            parent = repository.assert_current_lineage(registry, parent_entry)[0]
        normalized = validate_work_item_definition(definition, parent=parent)
        if normalized["id"] != entry["id"] or normalized["kind"] != entry["kind"]:
            fail("WORK_ITEM_REVISION_IDENTITY_CHANGED", "A revision cannot change work item identity or kind")
        if "children" in current_definition:
            revised_ids = {item["id"] for item in normalized["children"]}
            if any(item["id"] not in revised_ids for item in current_definition["children"]):
                fail("WORK_ITEM_CHILD_REMOVAL_FORBIDDEN", "Baseline revisions may append or refine children but cannot remove them")
        active_descendants = [
            item for item in registry["workItems"] if item.get("claim") and _is_descendant(registry, item, entry["id"])
        ]
        if entry["kind"] == "TASK" and active_descendants:
            fail("WORK_ITEM_REVISION_ACTIVE_CLAIM", "A claimed Task cannot be revised")
        for candidate in active_descendants:
            direct_child = candidate
            while direct_child["parentId"] and direct_child["parentId"] != entry["id"]:
                direct_child = repository.item_by_id(registry, direct_child["parentId"])
            before = work_item_child_contract_fingerprint(current_definition, direct_child["id"])
            after = work_item_child_contract_fingerprint(normalized, direct_child["id"])
            if before != after:
                fail("WORK_ITEM_REVISION_ACTIVE_CLAIM", "A revision cannot invalidate an actively claimed descendant")
        _validate_task_dependencies(normalized, parent)
        _validate_capability_graph(repository, registry, normalized)
        baseline = work_item_baseline_fingerprint(normalized)
        state = {
            **current_state,
            "baselineFingerprint": baseline,
            "contractFingerprint": work_item_contract_fingerprint(normalized),
            "parentContractFingerprint": normalized["parentContractFingerprint"],
            "baselineRevision": current_state.get("baselineRevision", 1) + 1,
            "revisedAt": at,
            "review": {
                "schemaVersion": SCHEMA_VERSION,
                "status": "APPROVED",
                "baselineFingerprint": baseline,
                "reviewedBy": "user",
                "reviewedAt": at,
            },
        }
        remove = ["acceptance-report.json", "acceptance-report.md"]
        if entry["kind"] == "TASK":
            remove.extend(["development-mode.json", "context-manifest.json", "development-handoff.md"])
        repository.replace_package(
            target,
            repository.package_files(normalized, state),
            preserve_existing=True,
            remove=tuple(remove),
        )
        entry.update({
            "childIds": [item["id"] for item in normalized.get("children", [])],
            "baselineFingerprint": state["baselineFingerprint"],
            "contractFingerprint": state["contractFingerprint"],
            "parentContractFingerprint": state["parentContractFingerprint"],
            "status": "WAITING_FOR_DEVELOPMENT_MODE_SELECTION" if entry["kind"] == "TASK" else "FROZEN",
            "developmentMode": None,
            "gate": {"status": "NOT_RUN", "evidence": None},
            "acceptance": {"status": "NOT_READY", "review": None, "userConfirmation": None}
            if entry["parentId"] is None
            else None,
            "acceptanceReport": None,
            "latestEvidence": None,
            "latestResult": None,
            "recordRevision": entry["recordRevision"] + 1,
            "updatedAt": at,
        })
        registry["currentFocus"] = {
            "workItemId": entry["id"],
            "purpose": "DEVELOPMENT_MODE_SELECTION" if entry["kind"] == "TASK" else "DECOMPOSITION",
        }
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_registry(registry)
        return {
            "id": entry["id"],
            "kind": entry["kind"],
            "baselineRevision": state["baselineRevision"],
            "baselineFingerprint": state["baselineFingerprint"],
            "status": entry["status"],
        }


def promote_work_item(
    *,
    root: str,
    item_id: str,
    parent_id: str,
    expected_baseline_fingerprint: str,
    expected_parent_baseline_fingerprint: str,
    confirmed: bool = False,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if not confirmed:
        fail("CONFIRMATION_REQUIRED", "Work item promotion requires explicit confirmation")
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        parent_entry = repository.item_by_id(registry, parent_id)
        if entry["id"] == parent_entry["id"]:
            fail("WORK_ITEM_PROMOTION_INVALID", "A work item cannot promote under itself")
        if entry["parentId"] is not None or entry["kind"] not in {"TASK", "CAPABILITY"}:
            fail("WORK_ITEM_PROMOTION_ROOT_REQUIRED", "Only a root Task or root Capability can be promoted")
        expected_kind = "CAPABILITY" if entry["kind"] == "TASK" else "DELIVERY"
        if parent_entry["kind"] != expected_kind or parent_entry["parentId"] is not None:
            fail("WORK_ITEM_PROMOTION_PARENT_INVALID", f"{entry['kind']} promotion requires a root {expected_kind} parent")
        if (
            entry["stage"] != "BASELINE_FROZEN"
            or entry["status"] not in {"FROZEN", "WAITING_FOR_DEVELOPMENT_MODE_SELECTION"}
            or entry["gate"]["status"] != "NOT_RUN"
        ):
            fail("WORK_ITEM_PROMOTION_SOURCE_NOT_FROZEN", "Promotion source must be an unblocked, unverified frozen root")
        if parent_entry["stage"] != "BASELINE_FROZEN" or parent_entry["status"] != "FROZEN" or parent_entry["gate"]["status"] != "NOT_RUN":
            fail("WORK_ITEM_PROMOTION_PARENT_NOT_FROZEN", "Promotion parent baseline must be frozen before attachment")
        if (
            entry["baselineFingerprint"] != expected_baseline_fingerprint
            or parent_entry["baselineFingerprint"] != expected_parent_baseline_fingerprint
        ):
            fail("WORK_ITEM_REVISION_CONFLICT", "Promotion fingerprints are not current")
        if any(item.get("claim") and _is_descendant(registry, item, entry["id"]) for item in registry["workItems"]):
            fail("WORK_ITEM_PROMOTION_ACTIVE_CLAIM", "A promoted subtree cannot contain an active claim")
        current_definition, current_state, target = repository.assert_current_lineage(registry, entry)
        parent_definition = repository.assert_current_lineage(registry, parent_entry)[0]
        normalized = validate_work_item_definition(
            {**raw_definition(current_definition), "parentId": parent_entry["id"]},
            parent=parent_definition,
        )
        _validate_task_dependencies(normalized, parent_definition)
        _validate_capability_graph(repository, registry, normalized)
        baseline = work_item_baseline_fingerprint(normalized)
        state = {
            **current_state,
            "baselineFingerprint": baseline,
            "contractFingerprint": work_item_contract_fingerprint(normalized),
            "parentContractFingerprint": normalized["parentContractFingerprint"],
            "baselineRevision": current_state.get("baselineRevision", 1) + 1,
            "revisedAt": at,
            "review": {
                "schemaVersion": SCHEMA_VERSION,
                "status": "APPROVED",
                "baselineFingerprint": baseline,
                "reviewedBy": "user",
                "reviewedAt": at,
            },
        }
        remove = ["acceptance-report.json", "acceptance-report.md"]
        if entry["kind"] == "TASK":
            remove.extend(["development-mode.json", "context-manifest.json", "development-handoff.md"])
        repository.replace_package(
            target,
            repository.package_files(normalized, state),
            preserve_existing=True,
            remove=tuple(remove),
        )
        previous_baseline = entry["baselineFingerprint"]
        entry.update({
            "parentId": parent_entry["id"],
            "baselineFingerprint": state["baselineFingerprint"],
            "contractFingerprint": state["contractFingerprint"],
            "parentContractFingerprint": state["parentContractFingerprint"],
            "status": "WAITING_FOR_DEVELOPMENT_MODE_SELECTION" if entry["kind"] == "TASK" else "FROZEN",
            "developmentMode": None,
            "gate": {"status": "NOT_RUN", "evidence": None},
            "acceptance": None,
            "acceptanceReport": None,
            "latestEvidence": None,
            "latestResult": None,
            "recordRevision": entry["recordRevision"] + 1,
            "updatedAt": at,
        })
        parent_entry["recordRevision"] += 1
        parent_entry["updatedAt"] = at
        registry["promotionHistory"].append({
            "schemaVersion": SCHEMA_VERSION,
            "childId": entry["id"],
            "childKind": entry["kind"],
            "parentId": parent_entry["id"],
            "parentKind": parent_entry["kind"],
            "previousBaselineFingerprint": previous_baseline,
            "promotedBaselineFingerprint": entry["baselineFingerprint"],
            "parentBaselineFingerprint": parent_entry["baselineFingerprint"],
            "promotedAt": at,
        })
        registry["currentFocus"] = {
            "workItemId": entry["id"],
            "purpose": "DEVELOPMENT_MODE_SELECTION" if entry["kind"] == "TASK" else "DECOMPOSITION",
        }
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_registry(registry)
        return {
            "id": entry["id"],
            "kind": entry["kind"],
            "gateLevel": entry["gateLevel"],
            "parentId": entry["parentId"],
            "baselineRevision": state["baselineRevision"],
            "baselineFingerprint": entry["baselineFingerprint"],
            "status": entry["status"],
        }


def refresh_work_item_projections(*, root: str, explicit_dogfood: bool = False) -> dict[str, Any]:
    repository = GovernanceRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as registry:
        repository.write_registry(registry)
        return {
            "revision": registry["revision"],
            "workspaceOverview": f"{GOVERNANCE_DIRECTORY}/workspace-overview.md",
            "workItems": [
                {
                    "id": entry["id"],
                    "acceptanceReport": entry["acceptanceReport"]["markdownPath"] if entry.get("acceptanceReport") else None,
                    "humanArtifacts": item_human_artifacts(entry["id"], entry.get("acceptanceReport")),
                }
                for entry in registry["workItems"]
            ],
        }
