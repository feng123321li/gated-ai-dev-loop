from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import GatedLoopError, fail
from .jsonio import canonical_json, fingerprint


FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ACCEPTANCE_STATUSES = {
    "NOT_READY", "WAITING_FOR_INDEPENDENT_REVIEW", "WAITING_FOR_USER_CONFIRMATION", "COMPLETED",
}
ACCEPTANCE_REPORT_STATUSES = ACCEPTANCE_STATUSES | {"WAITING_FOR_GATE", "BLOCKED", "VERIFIED"}
VALIDATION_REMEDIATION_SOURCES = {
    "REGRESSION", "TASK_GATE", "INDEPENDENT_REVIEW", "USER_ACCEPTANCE",
}
VALIDATION_REMEDIATION_ASSERTIONS = {
    "goalUnchanged",
    "requirementsUnchanged",
    "acceptanceUnchanged",
    "interfacesUnchanged",
    "dataContractUnchanged",
    "testCommandsUnchanged",
    "topologyUnchanged",
    "externalAuthorityUnchanged",
}


def valid_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def safe_work_item_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(SAFE_ID.fullmatch(value))
        and not value.endswith(".")
        and not re.match(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", value)
    )


def valid_evidence_record(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"sha256"}
        and isinstance(value.get("sha256"), str)
        and bool(FINGERPRINT.fullmatch(value["sha256"]))
    )


def evidence_record(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        fail("WORK_ITEM_EVIDENCE_INVALID", "Evidence artifact must be a JSON mapping")
    return {"sha256": fingerprint(value)}


def valid_development_mode(value: object, entry: dict[str, Any]) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {
            "schemaVersion", "rootId", "baselineFingerprint", "mode", "confirmedBy", "confirmedAt",
        }
        and value.get("schemaVersion") == SCHEMA_VERSION
        and entry.get("parentId") is None
        and value.get("rootId") == entry["id"]
        and value.get("baselineFingerprint") == entry["baselineFingerprint"]
        and value.get("mode") in {"active", "manual"}
        and value.get("confirmedBy") == "user"
        and valid_timestamp(value.get("confirmedAt"))
    )


def valid_review_artifact(action: str, value: object) -> bool:
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        return False
    if action == "INDEPENDENT_REVIEW_PASS":
        findings = value.get("findings")
        return (
            set(value) == {"schemaVersion", "kind", "reviewer", "isolation", "verdict", "findings"}
            and value.get("kind") == "INDEPENDENT_REVIEW"
            and non_empty_string(value.get("reviewer"))
            and value.get("isolation") == "FRESH_READ_ONLY"
            and value.get("verdict") == "PASS"
            and isinstance(findings, dict)
            and set(findings) == {"p0", "p1"}
            and findings.get("p0") == 0
            and findings.get("p1") == 0
        )
    if action == "HUMAN_REVIEW_ACCEPTED":
        return (
            set(value) == {"schemaVersion", "kind", "reviewer", "verdict"}
            and value.get("kind") == "HUMAN_REVIEW"
            and non_empty_string(value.get("reviewer"))
            and value.get("verdict") == "ACCEPTED"
        )
    return (
        action == "USER_CONFIRMED"
        and set(value) == {"schemaVersion", "kind", "confirmedBy", "decision"}
        and value.get("kind") == "USER_CONFIRMATION"
        and non_empty_string(value.get("confirmedBy"))
        and value.get("decision") == "CONFIRMED"
    )


def _valid_acceptance_evidence(value: object, actions: set[str]) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"action", "evidence", "artifact", "recordedAt"}
        and value.get("action") in actions
        and valid_evidence_record(value.get("evidence"))
        and valid_review_artifact(value["action"], value.get("artifact"))
        and valid_timestamp(value.get("recordedAt"))
    )


def valid_acceptance(value: object) -> bool:
    if (
        not isinstance(value, dict)
        or set(value) != {"status", "review", "userConfirmation"}
        or value.get("status") not in ACCEPTANCE_STATUSES
    ):
        return False
    if value["status"] in {"NOT_READY", "WAITING_FOR_INDEPENDENT_REVIEW"}:
        return value.get("review") is None and value.get("userConfirmation") is None
    review_valid = _valid_acceptance_evidence(
        value.get("review"), {"INDEPENDENT_REVIEW_PASS", "HUMAN_REVIEW_ACCEPTED"}
    )
    if value["status"] == "WAITING_FOR_USER_CONFIRMATION":
        return review_valid and value.get("userConfirmation") is None
    return review_valid and _valid_acceptance_evidence(value.get("userConfirmation"), {"USER_CONFIRMED"})


def valid_acceptance_report(value: object, entry: dict[str, Any]) -> bool:
    if value is None:
        return True
    expected = f".layered-delivery/{entry['packagePath']}"
    return (
        isinstance(value, dict)
        and set(value) == {"schemaVersion", "status", "markdownPath", "generatedAt"}
        and value.get("schemaVersion") == SCHEMA_VERSION
        and value.get("status") in ACCEPTANCE_REPORT_STATUSES
        and value.get("markdownPath") == f"{expected}/acceptance-report.md"
        and valid_timestamp(value.get("generatedAt"))
    )


def valid_task_result_artifact(value: object, *, item_id: str, operation_id: str, status: str) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {
            "schemaVersion", "kind", "taskId", "operationId", "status", "summary",
            "changedFiles", "tests", "blockers",
        }
        and value.get("schemaVersion") == SCHEMA_VERSION
        and value.get("kind") == "TASK_RESULT"
        and value.get("taskId") == item_id
        and value.get("operationId") == operation_id
        and value.get("status") == status
        and non_empty_string(value.get("summary"))
        and isinstance(value.get("changedFiles"), list)
        and all(non_empty_string(item) for item in value["changedFiles"])
        and isinstance(value.get("tests"), list)
        and all(
            isinstance(test, dict)
            and set(test) in ({"argv", "exitCode"}, {"argv", "exitCode", "testsRun"})
            and isinstance(test.get("argv"), list)
            and all(non_empty_string(item) for item in test["argv"])
            and isinstance(test.get("exitCode"), int)
            and not isinstance(test.get("exitCode"), bool)
            for test in value["tests"]
        )
        and isinstance(value.get("blockers"), list)
        and all(non_empty_string(item) for item in value["blockers"])
    )


def valid_validation_remediation_artifact(
    value: object,
    *,
    item_id: str,
    baseline_fingerprint: str,
    acceptance_ids: set[str],
) -> bool:
    """Validate an append-only repair that keeps the frozen requirement contract unchanged."""
    if not isinstance(value, dict):
        return False
    assertions = value.get("assertions")
    file_changes = value.get("fileChanges")
    linked_acceptance = value.get("acceptanceIds")
    if not (
        set(value) == {
            "schemaVersion", "kind", "taskId", "baselineFingerprint", "source", "summary",
            "acceptanceIds", "fileChanges", "assertions",
        }
        and value.get("schemaVersion") == SCHEMA_VERSION
        and value.get("kind") == "VALIDATION_REMEDIATION"
        and value.get("taskId") == item_id
        and value.get("baselineFingerprint") == baseline_fingerprint
        and value.get("source") in VALIDATION_REMEDIATION_SOURCES
        and non_empty_string(value.get("summary"))
        and isinstance(linked_acceptance, list)
        and bool(linked_acceptance)
        and all(isinstance(item, str) and item in acceptance_ids for item in linked_acceptance)
        and len(set(linked_acceptance)) == len(linked_acceptance)
        and isinstance(file_changes, list)
        and bool(file_changes)
        and isinstance(assertions, dict)
        and set(assertions) == VALIDATION_REMEDIATION_ASSERTIONS
        and all(assertions.get(key) is True for key in VALIDATION_REMEDIATION_ASSERTIONS)
    ):
        return False

    from .model import WILDCARD, normalize_scope_pattern

    paths: list[str] = []
    for change in file_changes:
        if not (
            isinstance(change, dict)
            and set(change) == {"path", "action", "purpose"}
            and change.get("action") in {"ADD", "MODIFY", "REMOVE"}
            and non_empty_string(change.get("purpose"))
        ):
            return False
        try:
            normalized = normalize_scope_pattern(change.get("path"))
        except GatedLoopError:
            return False
        if normalized != change.get("path") or WILDCARD.search(normalized):
            return False
        paths.append(normalized)
    return len(set(paths)) == len(paths)


def valid_gate_artifact(
    value: object,
    entry: dict[str, Any],
    definition: dict[str, Any],
    *,
    additional_planned_files: set[str] | None = None,
) -> bool:
    if not isinstance(value, dict):
        return False
    scope = value.get("scope")
    findings = value.get("findings")
    if not (
        set(value) == {
            "schemaVersion", "kind", "workItemId", "baselineFingerprint", "verdict", "summary",
            "scope", "acceptance", "tests", "findings",
        }
        and value.get("schemaVersion") == SCHEMA_VERSION
        and value.get("kind") == "WORK_ITEM_GATE"
        and value.get("workItemId") == entry["id"]
        and value.get("baselineFingerprint") == entry["baselineFingerprint"]
        and value.get("verdict") in {"PASS", "FAIL"}
        and non_empty_string(value.get("summary"))
        and isinstance(scope, dict)
        and set(scope) == {"changedFiles", "outOfScopeFiles"}
        and isinstance(scope.get("changedFiles"), list)
        and all(non_empty_string(item) for item in scope["changedFiles"])
        and isinstance(scope.get("outOfScopeFiles"), list)
        and all(non_empty_string(item) for item in scope["outOfScopeFiles"])
        and isinstance(value.get("acceptance"), list)
        and len(value["acceptance"]) == len(definition["acceptance"])
        and all(
            isinstance(result, dict)
            and set(result) == {"id", "status", "evidence"}
            for result in value["acceptance"]
        )
        and isinstance(value.get("tests"), list)
        and len(value["tests"]) == len(definition["testCommands"])
        and all(
            isinstance(result, dict)
            and set(result) in (
                {"argv", "exitCode", "summary"},
                {"argv", "exitCode", "testsRun", "summary"},
            )
            for result in value["tests"]
        )
        and isinstance(findings, dict)
        and set(findings) == {"p0", "p1", "p2"}
        and all(isinstance(findings.get(level), list) for level in ("p0", "p1", "p2"))
    ):
        return False
    acceptance_by_id = {
        result.get("id"): result for result in value["acceptance"] if isinstance(result, dict)
    }
    tests_by_argv = {
        canonical_json(result.get("argv")): result for result in value["tests"] if isinstance(result, dict)
    }
    if len(acceptance_by_id) != len(value["acceptance"]) or len(tests_by_argv) != len(value["tests"]):
        return False
    for criterion in definition["acceptance"]:
        result = acceptance_by_id.get(criterion["id"])
        if not isinstance(result, dict) or result.get("status") not in {"PASS", "FAIL"} or not non_empty_string(result.get("evidence")):
            return False
    for argv in definition["testCommands"]:
        result = tests_by_argv.get(canonical_json(argv))
        if not isinstance(result, dict) or not isinstance(result.get("exitCode"), int) or isinstance(result.get("exitCode"), bool):
            return False
        if not non_empty_string(result.get("summary")):
            return False
        if "testsRun" in result and (
            not isinstance(result["testsRun"], int) or isinstance(result["testsRun"], bool) or result["testsRun"] < 0
        ):
            return False
    planned_files = {item["path"] for item in definition["developmentPlan"].get("fileChanges", [])}
    planned_files.update(additional_planned_files or set())
    if definition["kind"] == "TASK" and any(
        item.replace("\\", "/") not in planned_files for item in scope["changedFiles"]
    ):
        return False
    if value["verdict"] == "PASS":
        return (
            not scope["outOfScopeFiles"]
            and all(acceptance_by_id[item["id"]]["status"] == "PASS" for item in definition["acceptance"])
            and all(tests_by_argv[canonical_json(argv)]["exitCode"] == 0 for argv in definition["testCommands"])
            and not findings["p0"]
            and not findings["p1"]
        )
    return True
