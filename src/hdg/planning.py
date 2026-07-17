from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .fs_safe import read_regular_file, safe_path
from .jsonio import pretty_json
from .model import (
    hierarchy_fingerprint,
    iter_hierarchy_nodes,
    raw_definition,
    render_hierarchy_plan,
    validate_hierarchy_definition,
    work_item_baseline_fingerprint,
    work_item_contract_fingerprint,
)
from .projections import item_human_artifacts
from .repository import (
    GOVERNANCE_DIRECTORY,
    WORK_ITEMS_DIRECTORY,
    GovernanceRepository,
    entry_from_definition,
    timestamp,
)


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


def _hierarchy_records(hierarchy: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def visit(node: dict[str, Any], package_path: str) -> None:
        definition = node["definition"]
        records.append({
            "node": node,
            "definition": definition,
            "packagePath": package_path,
        })
        for child in node["children"]:
            visit(child, f"{package_path}/children/{child['definition']['id']}")

    root_id = hierarchy["root"]["definition"]["id"]
    visit(hierarchy["root"], f"{WORK_ITEMS_DIRECTORY}/{root_id}")
    return records


def _hierarchy_state(
    hierarchy: dict[str, Any],
    states: dict[str, dict[str, Any]],
    *,
    status: str,
    at: str | None = None,
) -> dict[str, Any]:
    root_id = hierarchy["root"]["definition"]["id"]
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "rootId": root_id,
        "stage": "BASELINE_FROZEN" if status == "APPROVED" else "WAITING_FOR_BASELINE_CONFIRMATION",
        "hierarchyFingerprint": hierarchy_fingerprint(hierarchy),
        "items": [
            {
                "id": record["definition"]["id"],
                "kind": record["definition"]["kind"],
                "parentId": record["definition"]["parentId"],
                "packagePath": record["packagePath"],
                "baselineFingerprint": states[record["definition"]["id"]]["baselineFingerprint"],
            }
            for record in _hierarchy_records(hierarchy)
        ],
        "review": {
            "schemaVersion": SCHEMA_VERSION,
            "status": status,
            "hierarchyFingerprint": hierarchy_fingerprint(hierarchy),
            "reviewedBy": "user" if status == "APPROVED" else None,
            "reviewedAt": at if status == "APPROVED" else None,
        },
    }
    return value


def _hierarchy_packages(
    repository: GovernanceRepository,
    hierarchy: dict[str, Any],
    states: dict[str, dict[str, Any]],
    hierarchy_state: dict[str, Any],
) -> list[tuple[Path, dict[str, str]]]:
    records = _hierarchy_records(hierarchy)
    root_path = records[0]["packagePath"]
    root_plan = render_hierarchy_plan(hierarchy, states, hierarchy_state)
    packages: list[tuple[Path, dict[str, str]]] = []
    for index, record in enumerate(records):
        relative = Path(record["packagePath"]).relative_to(root_path)
        files = repository.package_files(
            record["definition"],
            states[record["definition"]["id"]],
            human_plan=root_plan if index == 0 else None,
        )
        if index == 0:
            files["hierarchy.json"] = pretty_json(hierarchy_state)
        packages.append((relative, files))
    return packages


def _hierarchy_from_registry(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    root_entry: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], Path]:
    if root_entry["parentId"] is not None:
        fail("WORK_ITEM_HIERARCHY_ROOT_REQUIRED", "Hierarchy operations require a root work item")
    root_target = repository.item_path(root_entry)
    hierarchy_state = repository.read_json(
        root_target,
        "hierarchy.json",
        "WORK_ITEM_HIERARCHY_INVALID",
    )
    expected_keys = {"schemaVersion", "rootId", "stage", "hierarchyFingerprint", "items", "review"}
    if (
        set(hierarchy_state) != expected_keys
        or hierarchy_state.get("schemaVersion") != SCHEMA_VERSION
        or hierarchy_state.get("rootId") != root_entry["id"]
        or not isinstance(hierarchy_state.get("items"), list)
        or not isinstance(hierarchy_state.get("review"), dict)
    ):
        fail("WORK_ITEM_HIERARCHY_INVALID", "Hierarchy state is invalid")

    states: dict[str, dict[str, Any]] = {}

    def build(entry: dict[str, Any]) -> dict[str, Any]:
        definition, state, _ = repository.read_package(registry, entry)
        states[entry["id"]] = state
        return {
            "definition": raw_definition(definition),
            "children": [
                build(repository.item_by_id(registry, child_id))
                for child_id in sorted(entry["childIds"])
            ],
        }

    hierarchy = validate_hierarchy_definition({
        "schemaVersion": SCHEMA_VERSION,
        "root": build(root_entry),
    })
    records = _hierarchy_records(hierarchy)
    expected_items = [
        {
            "id": record["definition"]["id"],
            "kind": record["definition"]["kind"],
            "parentId": record["definition"]["parentId"],
            "packagePath": record["packagePath"],
            "baselineFingerprint": states[record["definition"]["id"]]["baselineFingerprint"],
        }
        for record in records
    ]
    review = hierarchy_state["review"]
    review_valid = (
        set(review) == {"schemaVersion", "status", "hierarchyFingerprint", "reviewedBy", "reviewedAt"}
        and review.get("schemaVersion") == SCHEMA_VERSION
        and review.get("hierarchyFingerprint") == hierarchy_state.get("hierarchyFingerprint")
        and (
            (
                hierarchy_state.get("stage") == "WAITING_FOR_BASELINE_CONFIRMATION"
                and review.get("status") == "WAITING_FOR_HUMAN_REVIEW"
                and review.get("reviewedBy") is None
                and review.get("reviewedAt") is None
            )
            or (
                hierarchy_state.get("stage") == "BASELINE_FROZEN"
                and review.get("status") == "APPROVED"
                and review.get("reviewedBy") == "user"
                and isinstance(review.get("reviewedAt"), str)
            )
        )
    )
    if (
        hierarchy_state["items"] != expected_items
        or hierarchy_state["hierarchyFingerprint"] != hierarchy_fingerprint(hierarchy)
        or not review_valid
    ):
        fail("WORK_ITEM_HIERARCHY_CHANGED", "Hierarchy package changed after preparation")
    expected_plan = render_hierarchy_plan(hierarchy, states, hierarchy_state).encode("utf-8")
    try:
        actual_plan = read_regular_file(root_target, root_target / "development-plan.md")
    except Exception:
        fail("WORK_ITEM_HIERARCHY_PLAN_CHANGED", "Hierarchy development plan is missing or unreadable")
    if actual_plan != expected_plan:
        fail("WORK_ITEM_HIERARCHY_PLAN_CHANGED", "Hierarchy development plan changed after preparation")
    return hierarchy, states, hierarchy_state, root_target


def prepare_hierarchy(
    *,
    root: str,
    hierarchy: dict[str, Any],
    host_runtime: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Prepare one complete requirement tree and its single human plan."""
    from .host_runtime import require_host_runtime

    normalized = validate_hierarchy_definition(hierarchy)
    runtime = require_host_runtime(host_runtime)
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    records = _hierarchy_records(normalized)
    root_id = records[0]["definition"]["id"]
    with repository.transaction() as registry:
        existing_by_id = {item["id"]: item for item in registry["workItems"]}
        existing_root = existing_by_id.get(root_id)
        replace = existing_root is not None
        old_ids: set[str] = set()
        if replace:
            if existing_root["parentId"] is not None:
                fail("WORK_ITEM_HIERARCHY_ROOT_REQUIRED", f"{root_id} is not a hierarchy root")
            old_hierarchy, old_states, old_state, old_target = _hierarchy_from_registry(
                repository,
                registry,
                existing_root,
            )
            if old_state["stage"] != "WAITING_FOR_BASELINE_CONFIRMATION":
                fail("WORK_ITEM_SOURCE_CHANGED", "Frozen hierarchies require an explicit hierarchy revision")
            old_ids = {node["definition"]["id"] for node in iter_hierarchy_nodes(old_hierarchy)}
            if hierarchy_fingerprint(old_hierarchy) == hierarchy_fingerprint(normalized):
                return {
                    "created": False,
                    "revised": False,
                    "idempotent": True,
                    "rootId": root_id,
                    "itemIds": [record["definition"]["id"] for record in records],
                    "stage": old_state["stage"],
                    "hierarchyFingerprint": old_state["hierarchyFingerprint"],
                    "baselineFingerprints": {
                        item_id: state["baselineFingerprint"] for item_id, state in old_states.items()
                    },
                    "artifactDir": str(old_target),
                    "humanArtifacts": {
                        "developmentPlan": f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}/{root_id}/development-plan.md",
                        "workspaceOverview": f"{GOVERNANCE_DIRECTORY}/workspace-overview.md",
                    },
                    "nextAction": "人工评审 development-plan.md；同意当前方案后直接确认冻结，无需复述指纹。",
                }
        new_ids = {record["definition"]["id"] for record in records}
        conflicts = sorted(item_id for item_id in new_ids if item_id in existing_by_id and item_id not in old_ids)
        if conflicts:
            fail("WORK_ITEM_ID_CONFLICT", "Hierarchy contains IDs already owned by another requirement", ids=conflicts)

        states = {record["definition"]["id"]: _state(record["definition"], runtime, at) for record in records}
        hierarchy_state = _hierarchy_state(
            normalized,
            states,
            status="WAITING_FOR_HUMAN_REVIEW",
        )
        target = safe_path(root, f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}/{root_id}")
        entries = [
            entry_from_definition(
                record["definition"],
                states[record["definition"]["id"]],
                at,
                package_path=record["packagePath"],
            )
            for record in records
        ]
        registry["workItems"] = [item for item in registry["workItems"] if item["id"] not in old_ids] + entries
        registry["currentFocus"] = {"workItemId": root_id, "purpose": "HIERARCHY_PLAN_CONFIRMATION"}
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.recompute_progress(registry)
        repository.validate_registry(registry)
        repository.write_hierarchy_package(
            target,
            _hierarchy_packages(repository, normalized, states, hierarchy_state),
            replace=replace,
        )
        repository.write_registry(registry)
        return {
            "created": not replace,
            "revised": replace,
            "idempotent": False,
            "rootId": root_id,
            "itemIds": [record["definition"]["id"] for record in records],
            "stage": hierarchy_state["stage"],
            "hierarchyFingerprint": hierarchy_state["hierarchyFingerprint"],
            "baselineFingerprints": {
                item_id: state["baselineFingerprint"] for item_id, state in states.items()
            },
            "artifactDir": str(target),
            "humanArtifacts": {
                "developmentPlan": f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}/{root_id}/development-plan.md",
                "workspaceOverview": f"{GOVERNANCE_DIRECTORY}/workspace-overview.md",
            },
            "nextAction": "人工评审 development-plan.md；同意当前方案后直接确认冻结，无需复述指纹。",
        }


def freeze_hierarchy(
    *,
    root: str,
    root_id: str,
    expected_hierarchy_fingerprint: str,
    confirmed: bool = False,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Atomically record one human approval for every node in a requirement tree."""
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if not confirmed:
        fail("CONFIRMATION_REQUIRED", "Hierarchy freeze requires explicit human confirmation")
    at = timestamp(now)
    with repository.transaction() as registry:
        root_entry = repository.item_by_id(registry, root_id)
        hierarchy, states, hierarchy_state, target = _hierarchy_from_registry(repository, registry, root_entry)
        if hierarchy_state["hierarchyFingerprint"] != expected_hierarchy_fingerprint:
            fail("WORK_ITEM_REVISION_CONFLICT", "The confirmed hierarchy fingerprint is not current")
        records = _hierarchy_records(hierarchy)
        if hierarchy_state["stage"] == "BASELINE_FROZEN":
            return {
                "created": False,
                "idempotent": True,
                "rootId": root_id,
                "hierarchyFingerprint": hierarchy_state["hierarchyFingerprint"],
                "frozenItemIds": [record["definition"]["id"] for record in records],
            }
        if any(states[record["definition"]["id"]]["stage"] != "WAITING_FOR_BASELINE_CONFIRMATION" for record in records):
            fail("WORK_ITEM_STAGE_INVALID", "Every hierarchy node must be waiting for the same freeze")

        frozen_states = {
            item_id: {
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
            for item_id, state in states.items()
        }
        frozen_hierarchy_state = _hierarchy_state(
            hierarchy,
            frozen_states,
            status="APPROVED",
            at=at,
        )
        repository.write_hierarchy_package(
            target,
            _hierarchy_packages(repository, hierarchy, frozen_states, frozen_hierarchy_state),
            replace=True,
        )
        for record in records:
            entry = repository.item_by_id(registry, record["definition"]["id"])
            entry["stage"] = "BASELINE_FROZEN"
            entry["status"] = (
                "WAITING_FOR_DEVELOPMENT_MODE_SELECTION"
                if entry["kind"] == "TASK"
                else "FROZEN"
            )
            entry["recordRevision"] += 1
            entry["updatedAt"] = at
        registry["currentFocus"] = {"workItemId": root_id, "purpose": "DEVELOPMENT_MODE_SELECTION"}
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_registry(registry)
        return {
            "created": True,
            "idempotent": False,
            "rootId": root_id,
            "stage": "BASELINE_FROZEN",
            "hierarchyFingerprint": frozen_hierarchy_state["hierarchyFingerprint"],
            "frozenItemIds": [record["definition"]["id"] for record in records],
            "taskBaselines": {
                record["definition"]["id"]: frozen_states[record["definition"]["id"]]["baselineFingerprint"]
                for record in records
                if record["definition"]["kind"] == "TASK"
            },
            "humanArtifacts": {
                "developmentPlan": f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}/{root_id}/development-plan.md",
                "workspaceOverview": f"{GOVERNANCE_DIRECTORY}/workspace-overview.md",
            },
            "nextAction": "为需要执行的 Task 选择 active 或 manual 开发方式。",
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


def refresh_work_item_projections(*, root: str, explicit_dogfood: bool = False) -> dict[str, Any]:
    repository = GovernanceRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as registry:
        repository.write_registry(registry)
        by_id = {entry["id"]: entry for entry in registry["workItems"]}

        def hierarchy_root(entry: dict[str, Any]) -> dict[str, Any]:
            current = entry
            while current["parentId"] is not None:
                current = by_id[current["parentId"]]
            return current

        return {
            "revision": registry["revision"],
            "workspaceOverview": f"{GOVERNANCE_DIRECTORY}/workspace-overview.md",
            "workItems": [
                {
                    "id": entry["id"],
                    "acceptanceReport": entry["acceptanceReport"]["markdownPath"] if entry.get("acceptanceReport") else None,
                    "humanArtifacts": item_human_artifacts(
                        entry,
                        entry.get("acceptanceReport"),
                        root_package_path=hierarchy_root(entry)["packagePath"],
                    ),
                }
                for entry in registry["workItems"]
            ],
        }
