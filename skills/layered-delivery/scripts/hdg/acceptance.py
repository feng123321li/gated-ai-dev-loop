from __future__ import annotations

from typing import Any

from .errors import fail
from .evidence import (
    confirmation_evidence_contract,
    evidence_record,
    gate_artifact_issues,
    gate_evidence_contract,
    review_evidence_contract,
    valid_gate_artifact,
    valid_review_artifact,
)
from .graph_model import confirmation_node_id, gate_node_id, review_node_id
from .graph_runtime import hierarchy_root_entry
from .repository import GovernanceRepository, timestamp


def _remediation_files(
    repository: GovernanceRepository,
    entry: dict[str, Any],
    definition: dict[str, Any],
) -> set[str]:
    if entry["kind"] != "TASK":
        return set()
    frozen = {
        item["path"] for item in definition["developmentPlan"].get("fileChanges", [])
    }
    effective = {
        item["path"] for item in repository.effective_task_file_changes(definition)
    }
    return effective - frozen


def _validated_gate_artifact(
    evidence: object,
    *,
    entry: dict[str, Any],
    definition: dict[str, Any],
    status: str | None = None,
    additional_planned_files: set[str] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    if not valid_gate_artifact(
        evidence,
        entry,
        definition,
        additional_planned_files=additional_planned_files,
    ) or (
        status is not None and evidence.get("verdict") != status
    ):
        fail(
            "WORK_ITEM_GATE_EVIDENCE_INVALID",
            "Gate evidence is incomplete or contradicts the emitted evidence contract",
            issues=gate_artifact_issues(
                evidence,
                entry,
                definition,
                additional_planned_files=additional_planned_files,
                requested_verdict=status,
            ),
            evidenceContract=gate_evidence_contract(
                entry,
                definition,
                additional_planned_files=additional_planned_files,
            ),
        )
    return evidence_record(evidence), evidence


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
        remediation_files = _remediation_files(repository, entry, definition)
        verified_reference, verified_artifact = _validated_gate_artifact(
            evidence,
            entry=entry,
            definition=definition,
            status=status,
            additional_planned_files=remediation_files,
        )
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
            "evidence": verified_reference,
            "artifact": verified_artifact,
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
        root_entry = hierarchy_root_entry(registry, entry)
        repository.append_graph_event(
            root_id=root_entry["id"],
            node_id=gate_node_id(item_id),
            event_type="GATE_PASSED" if status == "PASS" else "GATE_FAILED",
            actor="AGENT",
            operation_id=None,
            payload={"status": status, "evidence": verified_reference},
            recorded_at=at,
            evidence_artifact=verified_artifact,
        )
        repository.write_acceptance_report(entry, definition, at)
        repository.write_registry(
            registry,
            changed_item_ids=repository.lineage_item_ids(registry, item_id),
        )
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
    registry = repository.read_operational_registry()
    entry = repository.item_by_id(registry, item_id)
    definition = repository.assert_current_lineage(registry, entry)[0]
    remediation_files = _remediation_files(repository, entry, definition)
    _, artifact = _validated_gate_artifact(
        evidence,
        entry=entry,
        definition=definition,
        additional_planned_files=remediation_files,
    )
    return record_work_item_gate(
        root=root,
        item_id=item_id,
        status=artifact["verdict"],
        evidence=artifact,
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
        artifact = evidence
        if not valid_review_artifact(action, artifact):
            fail(
                "WORK_ITEM_ACCEPTANCE_EVIDENCE_INVALID",
                f"Acceptance evidence does not prove {action}",
                evidenceContract=(
                    confirmation_evidence_contract()
                    if action == "USER_CONFIRMED"
                    else review_evidence_contract()
                ),
            )
        reference = evidence_record(artifact)
        if action == "USER_CONFIRMED":
            if acceptance["status"] != "WAITING_FOR_USER_CONFIRMATION":
                fail("WORK_ITEM_ACCEPTANCE_STAGE_INVALID", "User confirmation requires a passed independent or accepted human review")
            review_reference = acceptance["review"]["evidence"]
            if review_reference["sha256"] == reference["sha256"]:
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
        node_id = (
            confirmation_node_id(item_id)
            if action == "USER_CONFIRMED"
            else review_node_id(item_id)
        )
        repository.append_graph_event(
            root_id=item_id,
            node_id=node_id,
            event_type="USER_CONFIRMED" if action == "USER_CONFIRMED" else "REVIEW_PASSED",
            actor="USER" if action == "USER_CONFIRMED" else "REVIEWER",
            operation_id=None,
            payload={"action": action, "evidence": reference},
            recorded_at=at,
            evidence_artifact=artifact,
        )
        repository.write_acceptance_report(entry, definition, at)
        repository.write_registry(
            registry,
            changed_item_ids={item_id},
        )
        return {
            "id": item_id,
            "action": action,
            "acceptance": entry["acceptance"],
            "acceptanceReport": entry["acceptanceReport"],
        }
