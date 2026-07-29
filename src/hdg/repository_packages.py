from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .evidence_validation import (
    valid_timestamp,
)
from .fs_safe import (
    atomic_create_directory,
    atomic_replace_directory,
    atomic_write,
)
from .host_runtime import is_agent_runtime
from .jsonio import canonical_json
from .model_core import (
    WORK_ITEM_SCHEMA_VERSION,
    work_item_baseline_fingerprint,
    work_item_child_contract_fingerprint,
    work_item_contract_fingerprint,
)
from .model_rendering import (
    render_development_plan,
    render_work_item_baseline,
)
from .repository_contracts import (
    STATE_FIELDS,
    WORK_ITEMS_DIRECTORY,
    _plain_int,
)


@staticmethod
def package_files(
    definition: dict[str, Any],
    state: dict[str, Any],
    *,
    human_plan: str | None = None,
) -> dict[str, str]:
    return {
        "baseline.md": render_work_item_baseline(definition),
        "development-plan.md": human_plan or render_development_plan(definition, state),
    }

def write_new_package(self, target: Path, files: dict[str, str]) -> None:
    def populate(staging: Path) -> None:
        for name, contents in files.items():
            atomic_write(staging / name, contents)

    atomic_create_directory(target, populate)

def write_hierarchy_package(
    self,
    target: Path,
    packages: list[tuple[Path, dict[str, str]]],
    *,
    replace: bool = False,
) -> None:
    """Atomically write one complete requirement tree below its root directory."""
    def populate(staging: Path) -> None:
        for relative, files in packages:
            directory = staging / relative
            directory.mkdir(parents=True, exist_ok=True)
            for name, contents in files.items():
                atomic_write(directory / name, contents)

    if replace:
        atomic_replace_directory(target, populate)
    else:
        atomic_create_directory(target, populate)

def store_hierarchy(
    self,
    records: list[dict[str, Any]],
    states: dict[str, dict[str, Any]],
    hierarchy_state: dict[str, Any],
) -> None:
    """Store the complete machine-authoritative requirement tree in SQLite."""
    connection = self._active_connection()
    for record in records:
        definition = record["definition"]
        state = states[definition["id"]]
        connection.execute(
            "INSERT INTO work_items(id, entry_json, definition_json, state_json) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET definition_json = excluded.definition_json, "
            "state_json = excluded.state_json",
            (
                definition["id"],
                "{}",
                canonical_json(definition),
                canonical_json(state),
            ),
        )
    connection.execute(
        "INSERT INTO hierarchies(root_id, hierarchy_state_json) VALUES (?, ?) "
        "ON CONFLICT(root_id) DO UPDATE SET hierarchy_state_json = excluded.hierarchy_state_json",
        (hierarchy_state["rootId"], canonical_json(hierarchy_state)),
    )

def read_hierarchy_state(self, root_id: str) -> dict[str, Any]:
    with self._read_connection() as connection:
        row = connection.execute(
            "SELECT hierarchy_state_json FROM hierarchies WHERE root_id = ?",
            (root_id,),
        ).fetchone()
    if row is None:
        fail("WORK_ITEM_HIERARCHY_INVALID", "Hierarchy state is missing")
    try:
        value = json.loads(row["hierarchy_state_json"])
    except (TypeError, json.JSONDecodeError):
        fail("WORK_ITEM_HIERARCHY_INVALID", "Hierarchy state is invalid")
    if not isinstance(value, dict):
        fail("WORK_ITEM_HIERARCHY_INVALID", "Hierarchy state is invalid")
    return value












def replace_package(
    self,
    target: Path,
    files: dict[str, str],
    *,
    preserve_existing: bool = False,
    remove: tuple[str, ...] = (),
) -> None:
    def populate(staging: Path) -> None:
        if preserve_existing:
            for source in target.iterdir():
                if source.is_symlink():
                    fail("WORK_ITEM_PACKAGE_INVALID", "Work item packages cannot contain symbolic links")
                destination = staging / source.name
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
        for name, contents in files.items():
            atomic_write(staging / name, contents)
        for name in remove:
            candidate = staging / name
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass

    atomic_replace_directory(target, populate)

def read_package(self, registry: dict[str, Any], entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Path]:
    target = self.item_path(entry)
    with self._read_connection() as connection:
        row = connection.execute(
            "SELECT definition_json, state_json FROM work_items WHERE id = ?",
            (entry["id"],),
        ).fetchone()
    if row is None:
        fail("WORK_ITEM_PACKAGE_INVALID", f"{entry['id']} is missing from the governance database")
    try:
        definition = json.loads(row["definition_json"])
        state = json.loads(row["state_json"])
    except (TypeError, json.JSONDecodeError):
        fail("WORK_ITEM_PACKAGE_INVALID", f"{entry['id']} database records are invalid")
    baseline_fingerprint = work_item_baseline_fingerprint(definition)
    review = state.get("review")
    review_valid = (
        isinstance(review, dict)
        and set(review) == {"schemaVersion", "status", "baselineFingerprint", "reviewedBy", "reviewedAt"}
        and review.get("schemaVersion") == SCHEMA_VERSION
        and review.get("baselineFingerprint") == baseline_fingerprint
        and (
            (
                state.get("stage") == "WAITING_FOR_BASELINE_CONFIRMATION"
                and review.get("status") == "WAITING_FOR_HUMAN_REVIEW"
                and review.get("reviewedBy") is None
                and review.get("reviewedAt") is None
            )
            or (
                state.get("stage") == "BASELINE_FROZEN"
                and review.get("status") == "APPROVED"
                and review.get("reviewedBy") == "user"
                and valid_timestamp(review.get("reviewedAt"))
            )
        )
    )
    valid = (
        set(state) == STATE_FIELDS
        and state.get("schemaVersion") == WORK_ITEM_SCHEMA_VERSION
        and state.get("id") == entry["id"]
        and state.get("stage") == entry["stage"]
        and state.get("baselineFingerprint") == baseline_fingerprint
        and state.get("contractFingerprint") == work_item_contract_fingerprint(definition)
        and entry["baselineFingerprint"] == state["baselineFingerprint"]
        and entry["contractFingerprint"] == state["contractFingerprint"]
        and is_agent_runtime(state.get("hostRuntime"))
        and valid_timestamp(state.get("createdAt"))
        and _plain_int(state.get("baselineRevision"), minimum=1)
        and (state.get("revisedAt") is None or valid_timestamp(state.get("revisedAt")))
        and (
            state.get("frozenAt") is None
            if state.get("stage") == "WAITING_FOR_BASELINE_CONFIRMATION"
            else valid_timestamp(state.get("frozenAt"))
        )
        and review_valid
    )
    if not valid:
        fail("WORK_ITEM_PACKAGE_CHANGED", f"{entry['id']} package changed after preparation", id=entry["id"])
    return definition, state, target

def assert_current_lineage(
    self,
    registry: dict[str, Any],
    entry: dict[str, Any],
    seen: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    visited = seen or set()
    if entry["id"] in visited:
        fail("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle")
    visited.add(entry["id"])
    own = self.read_package(registry, entry)
    if not entry["parentId"]:
        return own
    parent_entry = self.item_by_id(registry, entry["parentId"])
    parent_definition, _, _ = self.read_package(registry, parent_entry)
    actual = work_item_child_contract_fingerprint(parent_definition, entry["id"])
    if entry["parentContractFingerprint"] != actual or own[0]["parentContractFingerprint"] != actual:
        fail(
            "WORK_ITEM_BASELINE_STALE",
            f"{entry['id']} parent contract changed",
            id=entry["id"],
            parentId=parent_entry["id"],
            expected=entry["parentContractFingerprint"],
            actual=actual,
        )
    self.assert_current_lineage(registry, parent_entry, visited)
    return own

@staticmethod
def _progress_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(entries),
        "verified": sum(item.get("status") == "VERIFIED" for item in entries),
        "blocked": sum(item.get("status") == "BLOCKED" for item in entries),
        "active": sum(item.get("status") in {"CLAIMED", "IMPLEMENTED"} for item in entries),
    }

def recompute_progress(self, registry: dict[str, Any]) -> None:
    by_id = {item["id"]: item for item in registry["workItems"]}

    def descendants(entry: dict[str, Any], visited: set[str] | None = None) -> list[dict[str, Any]]:
        path = visited or set()
        if entry["id"] in path:
            fail("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle")
        next_path = path | {entry["id"]}
        result = []
        for child_id in entry["childIds"]:
            child = by_id.get(child_id, {"id": child_id, "status": "PLANNED", "childIds": []})
            result.append(child)
            if child_id in by_id:
                result.extend(descendants(child, next_path))
        return result

    for entry in registry["workItems"]:
        if entry["id"] in self._isolated_entry_ids:
            continue
        direct = [by_id.get(item, {"id": item, "status": "PLANNED"}) for item in entry["childIds"]]
        entry["progress"] = {
            "directChildren": self._progress_counts(direct),
            "descendants": self._progress_counts(descendants(entry)),
        }


def entry_from_definition(
    definition: dict[str, Any],
    state: dict[str, Any],
    at: str,
    *,
    package_path: str | None = None,
) -> dict[str, Any]:
    return {
        "id": definition["id"],
        "kind": definition["kind"],
        "gateLevel": definition["gateLevel"],
        "authorityKind": definition["authorityKind"],
        "parentId": definition["parentId"],
        "childIds": [item["id"] for item in definition.get("children", [])],
        "packagePath": package_path or f"{WORK_ITEMS_DIRECTORY}/{definition['id']}",
        "developmentPlan": True,
        "stage": state["stage"],
        "status": "PREPARED",
        "baselineFingerprint": state["baselineFingerprint"],
        "contractFingerprint": state["contractFingerprint"],
        "parentContractFingerprint": state["parentContractFingerprint"],
        "gate": {"status": "NOT_RUN", "evidence": None},
        "acceptance": {"status": "NOT_READY", "review": None, "userConfirmation": None}
        if definition["parentId"] is None
        else None,
        "acceptanceReport": None,
        "developmentMode": None,
        "claim": None,
        "latestEvidence": None,
        "latestResult": None,
        "recordRevision": 1,
        "createdAt": at,
        "updatedAt": at,
    }
