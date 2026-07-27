from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import GatedLoopError, fail
from .jsonio import canonical_json, fingerprint
from .graph_model import FAILURE_CLASSES


FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
FAILURE_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")
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
TASK_RESULT_ARTIFACT_FIELDS = {
    "schemaVersion", "kind", "taskId", "operationId", "status", "summary",
    "changedFiles", "tests", "blockers", "failure",
}
GATE_ARTIFACT_FIELDS = {
    "schemaVersion", "kind", "workItemId", "baselineFingerprint", "verdict", "summary",
    "scope", "acceptance", "tests", "findings",
}
VALIDATION_REMEDIATION_ARTIFACT_FIELDS = {
    "schemaVersion", "kind", "taskId", "baselineFingerprint", "source", "summary",
    "acceptanceIds", "fileChanges", "assertions",
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
    return not task_result_artifact_issues(
        value,
        item_id=item_id,
        operation_id=operation_id,
        requested_status=status,
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
        set(value) == VALIDATION_REMEDIATION_ARTIFACT_FIELDS
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


def gate_evidence_contract(
    entry: dict[str, Any],
    definition: dict[str, Any],
    *,
    additional_planned_files: set[str] | None = None,
) -> dict[str, Any]:
    """Return a deterministic, directly fillable contract for one gate artifact."""
    allowed_changed_files = {
        item["path"]
        for item in definition["developmentPlan"].get("fileChanges", [])
    }
    allowed_changed_files.update(additional_planned_files or set())
    requirements_by_id = {
        item["id"]: item for item in definition["requirements"]
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifactKind": "WORK_ITEM_GATE",
        "workItemId": entry["id"],
        "baselineFingerprint": entry["baselineFingerprint"],
        "exactTopLevelKeys": sorted(GATE_ARTIFACT_FIELDS),
        "artifactTemplate": {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "WORK_ITEM_GATE",
            "workItemId": entry["id"],
            "baselineFingerprint": entry["baselineFingerprint"],
            "verdict": "<PASS_OR_FAIL>",
            "summary": "<REQUIRED_NON_EMPTY_STRING>",
            "scope": {
                "changedFiles": [],
                "outOfScopeFiles": [],
            },
            "acceptance": [
                {
                    "id": item["id"],
                    "requirementIds": list(item["requirementIds"]),
                    "status": "<PASS_OR_FAIL>",
                    "evidence": "<REQUIRED_NON_EMPTY_STRING>",
                }
                for item in definition["acceptance"]
            ],
            "tests": [
                {
                    "argv": list(argv),
                    "exitCode": "<INTEGER>",
                    "summary": "<REQUIRED_NON_EMPTY_STRING>",
                }
                for argv in definition["testCommands"]
            ],
            "findings": {"p0": [], "p1": [], "p2": []},
        },
        "constraints": {
            "acceptanceIds": [item["id"] for item in definition["acceptance"]],
            "acceptanceCriteria": [
                {
                    "id": item["id"],
                    "requirementIds": list(item["requirementIds"]),
                    "requirements": [
                        requirements_by_id[requirement_id]
                        for requirement_id in item["requirementIds"]
                    ],
                    "expectedResult": item["expectedResult"],
                }
                for item in definition["acceptance"]
            ],
            "testArgv": [list(argv) for argv in definition["testCommands"]],
            "testArgvMatching": "ONE_EXACT_ARGV_ARRAY_PER_FROZEN_COMMAND",
            "allowedChangedFiles": (
                sorted(allowed_changed_files)
                if definition["kind"] == "TASK"
                else None
            ),
            "testsRun": "OPTIONAL_NON_NEGATIVE_INTEGER",
            "passRequires": [
                "outOfScopeFiles must be empty",
                "every acceptance status must be PASS",
                "every test exitCode must be 0",
                "findings.p0 and findings.p1 must be empty",
            ],
        },
    }


def task_result_evidence_contract(
    entry: dict[str, Any],
    definition: dict[str, Any],
    *,
    authorized_file_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return directly fillable result templates bound to the active claim."""
    operation_id = entry["claim"]["operationId"]
    test_templates = [
        {
            "argv": list(argv),
            "exitCode": "<INTEGER>",
            "testsRun": "<OPTIONAL_NON_NEGATIVE_INTEGER>",
        }
        for argv in definition["testCommands"]
    ]
    shared = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "TASK_RESULT",
        "taskId": entry["id"],
        "operationId": operation_id,
        "summary": "<REQUIRED_NON_EMPTY_STRING>",
        "changedFiles": [],
        "tests": test_templates,
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifactKind": "TASK_RESULT",
        "taskId": entry["id"],
        "operationId": operation_id,
        "exactTopLevelKeys": sorted(TASK_RESULT_ARTIFACT_FIELDS),
        "artifactTemplates": {
            "IMPLEMENTED": {
                **shared,
                "status": "IMPLEMENTED",
                "blockers": [],
                "failure": None,
            },
            "BLOCKED": {
                **shared,
                "status": "BLOCKED",
                "blockers": ["<ONE_OR_MORE_REQUIRED_NON_EMPTY_STRINGS>"],
                "failure": {
                    "class": "<FAILURE_CLASS>",
                    "code": "<UPPER_SNAKE_CASE_CODE>",
                    "summary": "<REQUIRED_NON_EMPTY_STRING>",
                },
            },
        },
        "constraints": {
            "statusValues": ["IMPLEMENTED", "BLOCKED"],
            "frozenTestArgv": [
                list(argv) for argv in definition["testCommands"]
            ],
            "authorizedChangedFiles": sorted(
                item["path"] for item in authorized_file_changes
            ),
            "changedFilesPolicy": (
                "REPORT_FACTUAL_FILES; AUTHORIZATION_IS_ENFORCED_BY_GATE"
            ),
            "testsRun": "OPTIONAL_NON_NEGATIVE_INTEGER",
            "failureClasses": list(FAILURE_CLASSES[:5]),
            "implementedRequires": [
                "blockers must be empty",
                "failure must be null",
            ],
            "blockedRequires": [
                "blockers must contain one or more non-empty strings",
                "failure must contain class, code, and summary",
            ],
        },
    }


def validation_remediation_evidence_contract(
    entry: dict[str, Any],
    definition: dict[str, Any],
    *,
    authorized_file_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the exact append-only evidence contract for same-requirement remediation."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifactKind": "VALIDATION_REMEDIATION",
        "taskId": entry["id"],
        "baselineFingerprint": entry["baselineFingerprint"],
        "exactTopLevelKeys": sorted(VALIDATION_REMEDIATION_ARTIFACT_FIELDS),
        "artifactTemplate": {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "VALIDATION_REMEDIATION",
            "taskId": entry["id"],
            "baselineFingerprint": entry["baselineFingerprint"],
            "source": "<REGRESSION_OR_TASK_GATE_OR_INDEPENDENT_REVIEW_OR_USER_ACCEPTANCE>",
            "summary": "<REQUIRED_NON_EMPTY_STRING>",
            "acceptanceIds": ["<ONE_OR_MORE_FROZEN_ACCEPTANCE_IDS>"],
            "fileChanges": [{
                "path": "<PREVIOUSLY_UNAUTHORIZED_EXACT_FILE>",
                "action": "<ADD_OR_MODIFY_OR_REMOVE>",
                "purpose": "<REQUIRED_NON_EMPTY_STRING>",
            }],
            "assertions": {
                key: True for key in sorted(VALIDATION_REMEDIATION_ASSERTIONS)
            },
        },
        "constraints": {
            "sourceValues": sorted(VALIDATION_REMEDIATION_SOURCES),
            "acceptanceIds": [item["id"] for item in definition["acceptance"]],
            "taskScope": list(definition["scope"]),
            "alreadyAuthorizedFiles": sorted(
                item["path"] for item in authorized_file_changes
            ),
            "fileActions": ["ADD", "MODIFY", "REMOVE"],
            "filePathPolicy": "EXACT_NORMALIZED_PREVIOUSLY_UNAUTHORIZED_PATH",
            "allAssertionsMustBeTrue": True,
        },
    }


def review_evidence_contract() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifactKind": "ROOT_REVIEW",
        "actionOptions": {
            "INDEPENDENT_REVIEW_PASS": {
                "schemaVersion": SCHEMA_VERSION,
                "kind": "INDEPENDENT_REVIEW",
                "reviewer": "<REQUIRED_NON_EMPTY_STRING>",
                "isolation": "FRESH_READ_ONLY",
                "verdict": "PASS",
                "findings": {"p0": 0, "p1": 0},
            },
            "HUMAN_REVIEW_ACCEPTED": {
                "schemaVersion": SCHEMA_VERSION,
                "kind": "HUMAN_REVIEW",
                "reviewer": "<REQUIRED_NON_EMPTY_STRING>",
                "verdict": "ACCEPTED",
            },
        },
    }


def confirmation_evidence_contract() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifactKind": "USER_CONFIRMATION",
        "artifactTemplate": {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "USER_CONFIRMATION",
            "confirmedBy": "<REQUIRED_NON_EMPTY_STRING>",
            "decision": "CONFIRMED",
        },
    }


def _key_issues(value: object, expected: set[str]) -> list[str]:
    if not isinstance(value, dict):
        return ["artifact must be a JSON mapping"]
    issues: list[str] = []
    missing = sorted(expected - set(value))
    unexpected = sorted(set(value) - expected)
    if missing:
        issues.append(f"missing top-level keys: {', '.join(missing)}")
    if unexpected:
        issues.append(f"unexpected top-level keys: {', '.join(unexpected)}")
    return issues


def task_result_artifact_issues(
    value: object,
    *,
    item_id: str,
    operation_id: str,
    requested_status: str,
) -> list[str]:
    """Return precise field-level issues for one Task result artifact."""
    issues = _key_issues(value, TASK_RESULT_ARTIFACT_FIELDS)
    if not isinstance(value, dict):
        return issues
    if value.get("schemaVersion") != SCHEMA_VERSION:
        issues.append(f"schemaVersion must be {SCHEMA_VERSION}")
    if value.get("kind") != "TASK_RESULT":
        issues.append("kind must be TASK_RESULT")
    if value.get("taskId") != item_id:
        issues.append(f"taskId must be {item_id}")
    if value.get("operationId") != operation_id:
        issues.append(f"operationId must be {operation_id}")
    if value.get("status") != requested_status:
        issues.append(f"status must match requested status {requested_status}")
    if not non_empty_string(value.get("summary")):
        issues.append("summary must be a non-empty string")

    changed_files = value.get("changedFiles")
    if not isinstance(changed_files, list):
        issues.append("changedFiles must be an array")
    else:
        for index, path in enumerate(changed_files):
            if not non_empty_string(path):
                issues.append(
                    f"changedFiles[{index}] must be a non-empty string"
                )

    tests = value.get("tests")
    if not isinstance(tests, list):
        issues.append("tests must be an array")
    else:
        for index, test in enumerate(tests):
            prefix = f"tests[{index}]"
            if not isinstance(test, dict):
                issues.append(f"{prefix} must be a mapping")
                continue
            keys = set(test)
            allowed_keys = {"argv", "exitCode", "testsRun"}
            missing = sorted({"argv", "exitCode"} - keys)
            unexpected = sorted(keys - allowed_keys)
            if missing:
                issues.append(
                    f"{prefix} missing keys: {', '.join(missing)}"
                )
            if unexpected:
                issues.append(
                    f"{prefix} unexpected keys: {', '.join(unexpected)}"
                )
            argv = test.get("argv")
            if (
                not isinstance(argv, list)
                or any(not non_empty_string(item) for item in argv)
            ):
                issues.append(
                    f"{prefix}.argv must be an array of non-empty strings"
                )
            exit_code = test.get("exitCode")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                issues.append(f"{prefix}.exitCode must be an integer")
            if "testsRun" in test:
                tests_run = test["testsRun"]
                if (
                    not isinstance(tests_run, int)
                    or isinstance(tests_run, bool)
                    or tests_run < 0
                ):
                    issues.append(
                        f"{prefix}.testsRun must be a non-negative integer"
                    )

    blockers = value.get("blockers")
    if not isinstance(blockers, list):
        issues.append("blockers must be an array")
    elif any(not non_empty_string(item) for item in blockers):
        issues.append("blockers must contain only non-empty strings")

    failure = value.get("failure")
    if requested_status == "IMPLEMENTED":
        if isinstance(blockers, list) and blockers:
            issues.append("IMPLEMENTED requires blockers to be empty")
        if failure is not None:
            issues.append("IMPLEMENTED requires failure to be null")
    elif requested_status == "BLOCKED":
        if isinstance(blockers, list) and not blockers:
            issues.append(
                "BLOCKED requires blockers to contain at least one item"
            )
        if not isinstance(failure, dict):
            issues.append(
                "BLOCKED requires failure to contain class, code, and summary"
            )
        else:
            missing = sorted({"class", "code", "summary"} - set(failure))
            unexpected = sorted(
                set(failure) - {"class", "code", "summary"}
            )
            if missing:
                issues.append(
                    "failure missing keys: " + ", ".join(missing)
                )
            if unexpected:
                issues.append(
                    "failure unexpected keys: " + ", ".join(unexpected)
                )
            if failure.get("class") not in FAILURE_CLASSES[:5]:
                issues.append(
                    "failure.class must be one of: "
                    + ", ".join(FAILURE_CLASSES[:5])
                )
            code = failure.get("code")
            if not (
                isinstance(code, str)
                and bool(FAILURE_CODE.fullmatch(code))
            ):
                issues.append(
                    "failure.code must be an upper snake case identifier"
                )
            if not non_empty_string(failure.get("summary")):
                issues.append("failure.summary must be a non-empty string")
    return issues


def gate_artifact_issues(
    value: object,
    entry: dict[str, Any],
    definition: dict[str, Any],
    *,
    additional_planned_files: set[str] | None = None,
    requested_verdict: str | None = None,
) -> list[str]:
    issues = _key_issues(value, GATE_ARTIFACT_FIELDS)
    if not isinstance(value, dict):
        return issues
    if value.get("schemaVersion") != SCHEMA_VERSION:
        issues.append(f"schemaVersion must be {SCHEMA_VERSION}")
    if value.get("kind") != "WORK_ITEM_GATE":
        issues.append("kind must be WORK_ITEM_GATE")
    if value.get("workItemId") != entry["id"]:
        issues.append(f"workItemId must be {entry['id']}")
    if value.get("baselineFingerprint") != entry["baselineFingerprint"]:
        issues.append("baselineFingerprint must match the current frozen baseline")
    if requested_verdict is not None and value.get("verdict") != requested_verdict:
        issues.append(f"verdict must match requested status {requested_verdict}")

    expected_acceptance = [item["id"] for item in definition["acceptance"]]
    acceptance = value.get("acceptance")
    if isinstance(acceptance, list):
        received_acceptance = [
            item.get("id") for item in acceptance if isinstance(item, dict)
        ]
        if sorted(received_acceptance) != sorted(expected_acceptance):
            issues.append(
                "acceptance ids must exactly match the frozen ids: "
                + ", ".join(expected_acceptance)
            )
        criteria_by_id = {
            item["id"]: item for item in definition["acceptance"]
        }
        mismatched_trace = sorted(
            item.get("id", f"index-{index}")
            for index, item in enumerate(acceptance)
            if isinstance(item, dict)
            and item.get("id") in criteria_by_id
            and item.get("requirementIds")
            != criteria_by_id[item["id"]]["requirementIds"]
        )
        if mismatched_trace:
            issues.append(
                "acceptance requirementIds must match the frozen trace for: "
                + ", ".join(mismatched_trace)
            )
    elif "acceptance" in value:
        issues.append("acceptance must be an array")

    expected_tests = [canonical_json(argv) for argv in definition["testCommands"]]
    tests = value.get("tests")
    if isinstance(tests, list):
        received_tests = [
            canonical_json(item.get("argv"))
            for item in tests
            if isinstance(item, dict)
        ]
        if sorted(received_tests) != sorted(expected_tests):
            issues.append("tests argv must contain one exact match for every frozen testCommand")
    elif "tests" in value:
        issues.append("tests must be an array")

    scope = value.get("scope")
    if definition["kind"] == "TASK" and isinstance(scope, dict):
        allowed = {
            item["path"]
            for item in definition["developmentPlan"].get("fileChanges", [])
        }
        allowed.update(additional_planned_files or set())
        unauthorized = sorted(
            path.replace("\\", "/")
            for path in scope.get("changedFiles", [])
            if isinstance(path, str) and path.replace("\\", "/") not in allowed
        )
        if unauthorized:
            issues.append(
                "scope.changedFiles contains unauthorized files: "
                + ", ".join(unauthorized)
            )
    return issues or ["artifact values violate the emitted evidence contract"]


def validation_remediation_artifact_issues(
    value: object,
    *,
    item_id: str,
    baseline_fingerprint: str,
    acceptance_ids: set[str],
) -> list[str]:
    issues = _key_issues(value, VALIDATION_REMEDIATION_ARTIFACT_FIELDS)
    if not isinstance(value, dict):
        return issues
    if value.get("schemaVersion") != SCHEMA_VERSION:
        issues.append(f"schemaVersion must be {SCHEMA_VERSION}")
    if value.get("kind") != "VALIDATION_REMEDIATION":
        issues.append("kind must be VALIDATION_REMEDIATION")
    if value.get("taskId") != item_id:
        issues.append(f"taskId must be {item_id}")
    if value.get("baselineFingerprint") != baseline_fingerprint:
        issues.append("baselineFingerprint must match the current frozen baseline")
    if value.get("source") not in VALIDATION_REMEDIATION_SOURCES:
        issues.append(
            "source must be one of: "
            + ", ".join(sorted(VALIDATION_REMEDIATION_SOURCES))
        )
    linked = value.get("acceptanceIds")
    if not isinstance(linked, list) or not linked:
        issues.append("acceptanceIds must contain one or more frozen acceptance ids")
    elif any(item not in acceptance_ids for item in linked):
        issues.append("acceptanceIds contains ids outside the frozen acceptance contract")
    assertions = value.get("assertions")
    if isinstance(assertions, dict):
        for key in sorted(VALIDATION_REMEDIATION_ASSERTIONS):
            if assertions.get(key) is not True:
                issues.append(f"assertions.{key} must be true")
    else:
        issues.append("assertions must be a mapping with every frozen-contract assertion true")
    return issues or ["artifact values violate the emitted evidence contract"]


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
        set(value) == GATE_ARTIFACT_FIELDS
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
            and set(result) == {"id", "requirementIds", "status", "evidence"}
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
        if (
            not isinstance(result, dict)
            or result.get("requirementIds") != criterion["requirementIds"]
            or result.get("status") not in {"PASS", "FAIL"}
            or not non_empty_string(result.get("evidence"))
        ):
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
