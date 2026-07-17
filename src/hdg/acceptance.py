from __future__ import annotations

import json
from typing import Any

from .errors import GatedLoopError, fail
from .evidence import evidence_record, valid_gate_artifact, valid_review_artifact
from .fs_safe import read_regular_file
from .jsonio import canonical_json, sha256_bytes
from .repository import GovernanceRepository, timestamp


def _read_evidence(
    repository: GovernanceRepository,
    evidence: object,
    *,
    missing_code: str,
    changed_code: str,
    invalid_code: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    reference = evidence_record(evidence)
    try:
        data = read_regular_file(repository.root, reference["path"])
    except GatedLoopError:
        raise
    except Exception:
        fail(missing_code, f"Unable to read evidence: {reference['path']}")
    if sha256_bytes(data) != reference["sha256"]:
        fail(changed_code, f"Evidence hash does not match: {reference['path']}")
    try:
        artifact = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail(invalid_code, "Evidence must be valid JSON")
    if not isinstance(artifact, dict):
        fail(invalid_code, "Evidence must be a JSON mapping")
    return reference, artifact


def _all_children_verified(
    registry: dict[str, Any],
    entry: dict[str, Any],
    definition: dict[str, Any],
) -> bool:
    actual = {item["id"]: item for item in registry["workItems"] if item["parentId"] == entry["id"]}
    return bool(definition["children"]) and all(actual.get(item["id"], {}).get("status") == "VERIFIED" for item in definition["children"])


def record_work_item_gate(
    *,
    root: str,
    item_id: str,
    status: str,
    evidence: object,
    gate_artifact: dict[str, Any] | None = None,
    strict_evidence: bool = False,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if status not in {"PASS", "FAIL"}:
        fail("WORK_ITEM_GATE_INVALID", "Gate status must be PASS or FAIL")
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        definition = repository.assert_current_lineage(registry, entry)[0]
        verified_reference = None
        verified_artifact = None
        if strict_evidence:
            verified_reference, verified_artifact = _read_evidence(
                repository,
                evidence,
                missing_code="WORK_ITEM_GATE_EVIDENCE_MISSING",
                changed_code="WORK_ITEM_GATE_EVIDENCE_CHANGED",
                invalid_code="WORK_ITEM_GATE_EVIDENCE_INVALID",
            )
            if (
                not valid_gate_artifact(verified_artifact, entry, definition)
                or verified_artifact["verdict"] != status
                or (gate_artifact is not None and canonical_json(gate_artifact) != canonical_json(verified_artifact))
            ):
                fail("WORK_ITEM_GATE_EVIDENCE_INVALID", "Gate evidence does not prove the requested result")
        if entry["status"] == "BLOCKED":
            fail("WORK_ITEM_RETRY_REQUIRED", f"{item_id} must be explicitly retried before its gate can run again")
        if entry["status"] == "VERIFIED":
            fail("WORK_ITEM_GATE_ALREADY_PASSED", f"{item_id} gate has already passed")
        if status == "PASS":
            if entry["kind"] == "TASK" and entry["status"] != "IMPLEMENTED":
                fail("WORK_ITEM_IMPLEMENTATION_INCOMPLETE", f"{item_id} must be implemented before its gate can pass")
            if entry["kind"] != "TASK":
                if definition["decomposition"]["status"] != "SEALED":
                    fail("WORK_ITEM_DECOMPOSITION_OPEN", f"{item_id} decomposition must be SEALED before its aggregate gate can pass")
                if not _all_children_verified(registry, entry, definition):
                    fail("WORK_ITEM_CHILDREN_INCOMPLETE", f"{item_id} children must all be verified before its aggregate gate can pass")
        entry["gate"] = {
            "status": status,
            "evidence": verified_reference or evidence_record(evidence),
            "artifact": verified_artifact if verified_artifact is not None else gate_artifact,
        }
        entry["status"] = "VERIFIED" if status == "PASS" else "BLOCKED"
        if entry["parentId"] is None:
            entry["acceptance"] = (
                {"status": "WAITING_FOR_INDEPENDENT_REVIEW", "review": None, "userConfirmation": None}
                if status == "PASS"
                else {"status": "NOT_READY", "review": None, "userConfirmation": None}
            )
        entry["latestEvidence"] = entry["gate"]["evidence"]
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["currentFocus"] = {
            "workItemId": item_id,
            "purpose": "INDEPENDENT_REVIEW"
            if status == "PASS" and entry["parentId"] is None
            else ("AGGREGATION" if status == "PASS" else "BLOCKER"),
        }
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_acceptance_report(entry, definition, at)
        repository.write_registry(registry)
        return {
            "id": item_id,
            "status": entry["status"],
            "gate": entry["gate"],
            "acceptance": entry.get("acceptance"),
            "acceptanceReport": entry["acceptanceReport"],
        }


def accept_work_item(
    *,
    root: str,
    item_id: str,
    evidence: object,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    registry = repository.read_registry()
    entry = repository.item_by_id(registry, item_id)
    definition = repository.assert_current_lineage(registry, entry)[0]
    reference, artifact = _read_evidence(
        repository,
        evidence,
        missing_code="WORK_ITEM_GATE_EVIDENCE_MISSING",
        changed_code="WORK_ITEM_GATE_EVIDENCE_CHANGED",
        invalid_code="WORK_ITEM_GATE_EVIDENCE_INVALID",
    )
    if not valid_gate_artifact(artifact, entry, definition):
        fail("WORK_ITEM_GATE_EVIDENCE_INVALID", "Gate evidence is incomplete or contradicts the requested verdict")
    return record_work_item_gate(
        root=root,
        item_id=item_id,
        status=artifact["verdict"],
        evidence=reference,
        gate_artifact=artifact,
        strict_evidence=True,
        explicit_dogfood=explicit_dogfood,
        now=now,
    )


def record_acceptance(
    *,
    root: str,
    item_id: str,
    action: str,
    evidence: object,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if action not in {"INDEPENDENT_REVIEW_PASS", "HUMAN_REVIEW_ACCEPTED", "USER_CONFIRMED"}:
        fail("WORK_ITEM_ACCEPTANCE_ACTION_INVALID", "Acceptance action is invalid")
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        if entry["parentId"] is not None or entry["status"] != "VERIFIED":
            fail("WORK_ITEM_ACCEPTANCE_INVALID", "Only a verified root work item can advance final acceptance")
        definition = repository.assert_current_lineage(registry, entry)[0]
        acceptance = entry["acceptance"]
        reference, artifact = _read_evidence(
            repository,
            evidence,
            missing_code="WORK_ITEM_ACCEPTANCE_EVIDENCE_MISSING",
            changed_code="WORK_ITEM_ACCEPTANCE_EVIDENCE_CHANGED",
            invalid_code="WORK_ITEM_ACCEPTANCE_EVIDENCE_INVALID",
        )
        if not valid_review_artifact(action, artifact):
            fail("WORK_ITEM_ACCEPTANCE_EVIDENCE_INVALID", f"Acceptance evidence does not prove {action}")
        if action == "USER_CONFIRMED":
            if acceptance["status"] != "WAITING_FOR_USER_CONFIRMATION":
                fail("WORK_ITEM_ACCEPTANCE_STAGE_INVALID", "User confirmation requires a passed independent or accepted human review")
            review_reference = acceptance["review"]["evidence"]
            if review_reference["path"] == reference["path"] or review_reference["sha256"] == reference["sha256"]:
                fail("WORK_ITEM_ACCEPTANCE_EVIDENCE_REUSED", "User confirmation evidence must be distinct from review evidence")
            entry["acceptance"] = {
                **acceptance,
                "status": "COMPLETED",
                "userConfirmation": {
                    "action": action,
                    "evidence": reference,
                    "artifact": artifact,
                    "recordedAt": at,
                },
            }
        else:
            if acceptance["status"] != "WAITING_FOR_INDEPENDENT_REVIEW":
                fail("WORK_ITEM_ACCEPTANCE_STAGE_INVALID", "Work item is not waiting for independent review")
            entry["acceptance"] = {
                **acceptance,
                "status": "WAITING_FOR_USER_CONFIRMATION",
                "review": {
                    "action": action,
                    "evidence": reference,
                    "artifact": artifact,
                    "recordedAt": at,
                },
            }
        entry["latestEvidence"] = (
            entry["acceptance"]["userConfirmation"]["evidence"]
            if action == "USER_CONFIRMED"
            else entry["acceptance"]["review"]["evidence"]
        )
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["currentFocus"] = {
            "workItemId": item_id,
            "purpose": "ACCEPTANCE_COMPLETE" if entry["acceptance"]["status"] == "COMPLETED" else "USER_CONFIRMATION",
        }
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_acceptance_report(entry, definition, at)
        repository.write_registry(registry)
        return {
            "id": item_id,
            "action": action,
            "acceptance": entry["acceptance"],
            "acceptanceReport": entry["acceptanceReport"],
        }
