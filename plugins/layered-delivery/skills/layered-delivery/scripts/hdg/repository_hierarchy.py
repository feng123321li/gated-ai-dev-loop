from __future__ import annotations

from typing import Any

from .errors import fail
from .repository_contracts import (
    WORK_ITEM_REGISTRY_SCHEMA_VERSION,
    timestamp,
)


def empty_registry(self) -> dict[str, Any]:
    return {
        "schemaVersion": WORK_ITEM_REGISTRY_SCHEMA_VERSION,
        "coordinationRoot": str(self.root),
        "revision": 0,
        "currentFocus": {"workItemId": None, "purpose": None},
        "workItems": [],
        "updatedAt": timestamp(self.now),
    }


def item_by_id(self, registry: dict[str, Any], item_id: str) -> dict[str, Any]:
    if item_id in self._isolated_entry_ids:
        fail(
            "WORK_ITEM_ENTRY_READ_ONLY_ISOLATED",
            f"Work item is invalid under the current contract and is isolated read-only: {item_id}",
            id=item_id,
        )
    for item in registry["workItems"]:
        if item["id"] == item_id:
            return item
    fail("WORK_ITEM_NOT_FOUND", f"Unknown work item: {item_id}", id=item_id)


@staticmethod
def lineage_item_ids(
    registry: dict[str, Any],
    item_id: str,
) -> set[str]:
    by_id = {entry["id"]: entry for entry in registry["workItems"]}
    result: set[str] = set()
    current = by_id.get(item_id)
    while current is not None:
        if current["id"] in result:
            fail("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle")
        result.add(current["id"])
        parent_id = current["parentId"]
        current = by_id.get(parent_id) if parent_id is not None else None
    if item_id not in result:
        fail("WORK_ITEM_NOT_FOUND", f"Unknown work item: {item_id}", id=item_id)
    return result


def is_item_isolated(self, item_id: str) -> bool:
    return item_id in self._isolated_entry_ids


def assert_subtree_operational(
    self,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> None:
    by_id = {
        candidate["id"]: candidate
        for candidate in registry["workItems"]
    }
    pending = [entry["id"]]
    visited: set[str] = set()
    isolated: list[str] = []
    while pending:
        item_id = pending.pop()
        if item_id in visited:
            fail(
                "WORK_ITEM_HIERARCHY_CYCLE",
                "Work item hierarchy contains a cycle",
            )
        visited.add(item_id)
        current = by_id.get(item_id)
        if current is None:
            fail(
                "WORK_ITEM_HIERARCHY_INVALID",
                f"Work item hierarchy entry is missing: {item_id}",
            )
        if item_id in self._isolated_entry_ids:
            isolated.append(item_id)
        pending.extend(reversed(current["childIds"]))
    if isolated:
        fail(
            "WORK_ITEM_HIERARCHY_ISOLATED",
            (
                "A governance transition cannot advance while its "
                "work-item subtree contains read-only isolated evidence"
            ),
            itemId=entry["id"],
            isolatedItemIds=sorted(isolated),
        )
