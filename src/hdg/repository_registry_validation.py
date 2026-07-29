from __future__ import annotations

from copy import deepcopy
from typing import Any

from .errors import GatedLoopError, fail
from .evidence_validation import (
    FINGERPRINT,
    evidence_record,
    safe_work_item_id,
    valid_acceptance,
    valid_acceptance_report,
    valid_development_mode,
    valid_evidence_record,
    valid_timestamp,
)
from .model_core import (
    WORK_ITEM_AUTHORITIES,
    WORK_ITEM_GATE_LEVELS,
    WORK_ITEM_KINDS,
)
from .repository_contracts import (
    ENTRY_FIELDS,
    WORK_ITEM_REGISTRY_SCHEMA_VERSION,
    WORK_ITEMS_DIRECTORY,
    _plain_int,
    _valid_claim,
    _valid_gate,
    _valid_latest_result,
    _valid_progress,
)


@staticmethod
def _validate_registry_entry(
    entry: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
) -> None:
    def hierarchy_root() -> dict[str, Any] | None:
        current = entry
        visited: set[str] = set()
        while current.get("parentId") is not None:
            if current.get("id") in visited:
                return None
            visited.add(current.get("id"))
            current = by_id.get(current.get("parentId"))
            if current is None:
                return None
        return current

    valid_entry = (
        set(entry) == ENTRY_FIELDS
        and entry.get("kind") in WORK_ITEM_KINDS
        and entry.get("authorityKind") == WORK_ITEM_AUTHORITIES.get(entry.get("kind"))
        and entry.get("gateLevel") in WORK_ITEM_GATE_LEVELS
        and (entry.get("kind") == "TASK" or entry.get("gateLevel") == "FULL")
        and (entry.get("parentId") is None or safe_work_item_id(entry.get("parentId")))
        and isinstance(entry.get("childIds"), list)
        and all(safe_work_item_id(item) for item in entry["childIds"])
        and isinstance(entry.get("packagePath"), str)
        and entry["packagePath"].replace("\\", "/") == entry["packagePath"]
        and entry["packagePath"].startswith(f"{WORK_ITEMS_DIRECTORY}/")
        and ".." not in entry["packagePath"].split("/")
        and entry.get("developmentPlan") is True
        and bool(FINGERPRINT.fullmatch(str(entry.get("baselineFingerprint", ""))))
        and bool(FINGERPRINT.fullmatch(str(entry.get("contractFingerprint", ""))))
        and (
            entry.get("parentContractFingerprint") is None
            if entry.get("parentId") is None
            else bool(FINGERPRINT.fullmatch(str(entry.get("parentContractFingerprint", ""))))
        )
        and entry.get("stage") in {"WAITING_FOR_BASELINE_CONFIRMATION", "BASELINE_FROZEN"}
        and entry.get("status") in {
            "PREPARED", "FROZEN", "CLAIMED", "IMPLEMENTED", "BLOCKED", "VERIFIED",
        }
        and _valid_gate(entry.get("gate"))
        and _plain_int(entry.get("recordRevision"), minimum=1)
        and valid_timestamp(entry.get("createdAt"))
        and valid_timestamp(entry.get("updatedAt"))
        and _valid_progress(entry.get("progress"))
        and (
            entry.get("latestEvidence") is None
            or valid_evidence_record(entry.get("latestEvidence"))
        )
        and (
            entry.get("latestResult") is None
            or _valid_latest_result(entry.get("latestResult"))
        )
    )
    if not valid_entry:
        fail("WORK_ITEM_REGISTRY_INVALID", f"Work item registry entry is invalid: {entry['id']}")
    root_entry = hierarchy_root()
    if root_entry is None:
        fail("WORK_ITEM_REGISTRY_INVALID", f"Work item hierarchy root is invalid: {entry['id']}")
    mode = entry.get("developmentMode")
    if entry["parentId"] is None:
        if entry["stage"] == "WAITING_FOR_BASELINE_CONFIRMATION" and mode is not None:
            fail("WORK_ITEM_REGISTRY_INVALID", f"Prepared requirement cannot store development mode: {entry['id']}")
        if entry["stage"] == "BASELINE_FROZEN" and not valid_development_mode(mode, entry):
            fail("WORK_ITEM_REGISTRY_INVALID", f"Requirement development mode is invalid: {entry['id']}")
    elif mode is not None:
        fail("WORK_ITEM_REGISTRY_INVALID", f"Only a requirement root can store development mode: {entry['id']}")
    root_mode = root_entry.get("developmentMode")
    if entry["stage"] == "BASELINE_FROZEN" and not valid_development_mode(root_mode, root_entry):
        fail("WORK_ITEM_REGISTRY_INVALID", f"Frozen tree development mode is invalid: {root_entry['id']}")
    if entry["stage"] == "WAITING_FOR_BASELINE_CONFIRMATION":
        if entry["status"] != "PREPARED" or mode is not None:
            fail("WORK_ITEM_REGISTRY_INVALID", f"Work item prepared state is inconsistent: {entry['id']}")
    elif entry["status"] == "PREPARED":
        fail("WORK_ITEM_REGISTRY_INVALID", f"Work item frozen state is inconsistent: {entry['id']}")
    if entry["kind"] != "TASK" and entry["status"] in {"CLAIMED", "IMPLEMENTED"}:
        fail("WORK_ITEM_REGISTRY_INVALID", f"Coordination work item status is invalid: {entry['id']}")
    claim = entry.get("claim")
    if (entry["status"] == "CLAIMED") != _valid_claim(claim):
        fail("WORK_ITEM_REGISTRY_INVALID", f"Work item claim is inconsistent: {entry['id']}")
    gate_status = entry["gate"]["status"]
    if (entry["status"] == "VERIFIED") != (gate_status == "PASS"):
        fail("WORK_ITEM_REGISTRY_INVALID", f"Work item PASS state is inconsistent: {entry['id']}")
    if gate_status == "FAIL" and entry["status"] != "BLOCKED":
        fail("WORK_ITEM_REGISTRY_INVALID", f"Work item FAIL state is inconsistent: {entry['id']}")
    if entry["parentId"] is None:
        if not valid_acceptance(entry.get("acceptance")):
            fail("WORK_ITEM_REGISTRY_INVALID", f"Work item acceptance state is invalid: {entry['id']}")
    elif entry.get("acceptance") is not None:
        fail("WORK_ITEM_REGISTRY_INVALID", f"Work item acceptance state is invalid: {entry['id']}")
    if not valid_acceptance_report(entry.get("acceptanceReport"), entry):
        fail("WORK_ITEM_REGISTRY_INVALID", f"Work item acceptance report is invalid: {entry['id']}")
    if entry["kind"] == "DELIVERY" and entry["parentId"] is not None:
        fail("WORK_ITEM_REGISTRY_INVALID", "Delivery entries cannot have parents")
    parent = by_id.get(entry["parentId"]) if entry["parentId"] is not None else None
    expected_package = (
        f"{WORK_ITEMS_DIRECTORY}/{entry['id']}"
        if entry["parentId"] is None
        else f"{parent.get('packagePath')}/children/{entry['id']}"
        if parent is not None
        else None
    )
    if entry["packagePath"] != expected_package:
        fail("WORK_ITEM_REGISTRY_INVALID", f"Work item package path is invalid: {entry['id']}")
    if any(child_id not in by_id for child_id in entry["childIds"]):
        fail("WORK_ITEM_REGISTRY_INVALID", f"Work item hierarchy is not fully materialized: {entry['id']}")
    if entry["kind"] != "DELIVERY" and entry["parentId"] is not None:
        expected_kind = "DELIVERY" if entry["kind"] == "CAPABILITY" else "CAPABILITY"
        if not parent or parent.get("kind") != expected_kind or entry["id"] not in parent.get("childIds", []):
            fail("WORK_ITEM_REGISTRY_INVALID", f"Work item parent relation is invalid: {entry['id']}")


@classmethod
def _is_read_only_evidence_entry(
    cls,
    entry: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
) -> bool:
    """Recognize an otherwise-current entry whose stored evidence reference is non-current."""
    candidate = deepcopy(entry)
    normalized_references: list[dict[str, str]] = []

    latest_result = candidate.get("latestResult")
    if isinstance(latest_result, dict) and isinstance(latest_result.get("artifact"), dict):
        latest_result["evidence"] = evidence_record(latest_result["artifact"])
        normalized_references.append(latest_result["evidence"])

    gate = candidate.get("gate")
    if isinstance(gate, dict) and gate.get("status") in {"PASS", "FAIL"}:
        if not isinstance(gate.get("artifact"), dict):
            return False
        gate["evidence"] = evidence_record(gate["artifact"])
        normalized_references.append(gate["evidence"])

    acceptance = candidate.get("acceptance")
    if isinstance(acceptance, dict):
        for key in ("review", "userConfirmation"):
            record = acceptance.get(key)
            if record is None:
                continue
            if not isinstance(record, dict) or not isinstance(record.get("artifact"), dict):
                return False
            record["evidence"] = evidence_record(record["artifact"])
            normalized_references.append(record["evidence"])

    if candidate.get("latestEvidence") is not None:
        if not normalized_references:
            return False
        candidate["latestEvidence"] = normalized_references[-1]

    try:
        cls._validate_registry_entry(candidate, by_id)
    except GatedLoopError:
        return False
    return True


def validate_registry(
    self,
    registry: object,
    *,
    isolate_historical_evidence: bool = False,
) -> dict[str, Any]:
    valid = (
        isinstance(registry, dict)
        and registry.get("schemaVersion") == WORK_ITEM_REGISTRY_SCHEMA_VERSION
        and registry.get("coordinationRoot") == str(self.root)
        and isinstance(registry.get("revision"), int)
        and not isinstance(registry.get("revision"), bool)
        and registry["revision"] >= 0
        and isinstance(registry.get("workItems"), list)
        and isinstance(registry.get("currentFocus"), dict)
        and set(registry["currentFocus"]) == {"workItemId", "purpose"}
        and valid_timestamp(registry.get("updatedAt"))
        and set(registry) == {
            "schemaVersion", "coordinationRoot", "revision", "currentFocus", "workItems",
            "updatedAt",
        }
    )
    if not valid:
        fail("WORK_ITEM_REGISTRY_INVALID", "Work item registry is invalid")
    ids = [item.get("id") for item in registry["workItems"] if isinstance(item, dict)]
    if len(ids) != len(registry["workItems"]) or len(set(ids)) != len(ids) or any(not safe_work_item_id(item) for item in ids):
        fail("WORK_ITEM_REGISTRY_INVALID", "Work item registry contains duplicate or unsafe IDs")
    by_id = {item["id"]: item for item in registry["workItems"]}

    isolated_entry_ids: set[str] = set()
    for entry in registry["workItems"]:
        try:
            self._validate_registry_entry(entry, by_id)
        except GatedLoopError:
            if not isolate_historical_evidence or not self._is_read_only_evidence_entry(entry, by_id):
                raise
            isolated_entry_ids.add(entry["id"])
    focus_id = registry["currentFocus"].get("workItemId")
    if focus_id is not None and (not safe_work_item_id(focus_id) or focus_id not in by_id):
        fail("WORK_ITEM_REGISTRY_INVALID", "Current focus references an unknown work item")
    focus_purpose = registry["currentFocus"].get("purpose")
    if (focus_id is None) != (focus_purpose is None) or (
        focus_purpose is not None and (not isinstance(focus_purpose, str) or not focus_purpose)
    ):
        fail("WORK_ITEM_REGISTRY_INVALID", "Current focus is invalid")
    self._isolated_entry_ids = isolated_entry_ids
    return registry
