from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .constants import MAX_IDENTIFIER_LENGTH, SCHEMA_VERSION
from .errors import GatedLoopError, fail
from .jsonio import canonical_json, fingerprint
from .graph_model import FAILURE_CLASSES


FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")

SAFE_ID = re.compile(
    rf"^[a-z0-9][a-z0-9._-]{{0,{MAX_IDENTIFIER_LENGTH - 1}}}$"
)

FAILURE_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")

ACCEPTANCE_STATUSES = {
    "NOT_READY",
    "WAITING_FOR_INDEPENDENT_REVIEW",
    "REVIEW_BLOCKED",
    "WAITING_FOR_USER_CONFIRMATION",
    "COMPLETED",
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

SKILL_USAGE_FIELDS = {
    "name", "stage", "status", "evidence",
}

GENERIC_SKILL_EVIDENCE = {
    "applied",
    "used",
    "done",
    "已使用",
    "已应用",
    "完成",
}

SKILL_USAGE_STAGES = {
    "DEVELOPMENT", "GATE", "FINAL_REVIEW",
}

TEMPLATE_PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9_]*>")

def _generated_file_roots(
    definition: dict[str, Any],
) -> list[dict[str, str]]:
    return list(
        definition.get("developmentPlan", {}).get(
            "generatedFileRoots",
            [],
        )
    )

def _path_in_generated_roots(
    path: str,
    generated_file_roots: list[dict[str, str]],
) -> bool:
    normalized = path.replace("\\", "/")
    return any(
        normalized.startswith(root["path"][:-2])
        and normalized != root["path"][:-3]
        for root in generated_file_roots
    )

def _generated_file_issues(
    *,
    changed_files: object,
    generated_files: object,
    generated_file_roots: list[dict[str, str]],
    field: str,
) -> list[str]:
    if not generated_file_roots:
        return []
    if not isinstance(generated_files, list):
        return [f"{field} must be an array for ADD-only generated roots"]
    issues: list[str] = []
    normalized_generated: list[str] = []
    for index, path in enumerate(generated_files):
        if not non_empty_string(path):
            issues.append(f"{field}[{index}] must be a non-empty string")
            continue
        normalized = str(path).replace("\\", "/")
        normalized_generated.append(normalized)
        if not _path_in_generated_roots(
            normalized,
            generated_file_roots,
        ):
            issues.append(
                f"{field}[{index}] is outside authorized generated roots"
            )
    if len(set(normalized_generated)) != len(normalized_generated):
        issues.append(f"{field} must not contain duplicate paths")
    if isinstance(changed_files, list):
        changed = {
            str(path).replace("\\", "/")
            for path in changed_files
            if isinstance(path, str)
        }
        missing = sorted(set(normalized_generated) - changed)
        if missing:
            issues.append(
                f"{field} must be a subset of changedFiles: "
                + ", ".join(missing)
            )
    return issues

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

def concrete_skill_evidence(value: object) -> bool:
    if not non_empty_string(value):
        return False
    text = str(value).strip()
    return (
        len(text) >= 12
        and text.casefold() not in GENERIC_SKILL_EVIDENCE
        and TEMPLATE_PLACEHOLDER.search(text) is None
    )

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

def _skill_usage_template(
    required_skills: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {
            "name": item["name"],
            "stage": item["stage"],
            "status": "APPLIED",
            "evidence": "<CONCRETE_APPLICATION_EVIDENCE>",
        }
        for item in required_skills
    ]

def _skill_usage_issues(
    value: object,
    required_skills: list[dict[str, Any]],
    *,
    require_applied: bool,
) -> list[str]:
    expected = [
        (item["name"], item["stage"]) for item in required_skills
    ]
    if not isinstance(value, list):
        return ["skillUsage must be an array"]
    actual: list[tuple[object, object]] = []
    issues: list[str] = []
    for index, usage in enumerate(value):
        field = f"skillUsage[{index}]"
        if not isinstance(usage, dict):
            issues.append(f"{field} must be a mapping")
            continue
        if set(usage) != SKILL_USAGE_FIELDS:
            issues.append(
                f"{field} must contain only name, stage, status, and evidence"
            )
        actual.append((usage.get("name"), usage.get("stage")))
        if usage.get("status") not in {"APPLIED", "BLOCKED"}:
            issues.append(f"{field}.status must be APPLIED or BLOCKED")
        elif require_applied and usage.get("status") != "APPLIED":
            issues.append(f"{field}.status must be APPLIED")
        evidence = usage.get("evidence")
        if not concrete_skill_evidence(evidence):
            issues.append(
                (
                    f"{field}.evidence must concretely describe how the "
                    "complete Skill workflow was applied and must not contain "
                    "controller template placeholders"
                )
            )
    if actual != expected:
        issues.append(
            "skillUsage name/stage pairs must exactly match the frozen required Skills: "
            + ", ".join(f"{name}@{stage}" for name, stage in expected)
        )
    return issues

def _stored_skill_usage_valid(
    value: object,
    *,
    require_applied: bool,
) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(usage, dict)
            and set(usage) == SKILL_USAGE_FIELDS
            and non_empty_string(usage.get("name"))
            and usage.get("stage") in SKILL_USAGE_STAGES
            and usage.get("status") in {"APPLIED", "BLOCKED"}
            and (
                not require_applied
                or usage.get("status") == "APPLIED"
            )
            and concrete_skill_evidence(usage.get("evidence"))
            for usage in value
        )
        and (
            require_applied
            or any(usage["status"] == "BLOCKED" for usage in value)
        )
    )

def valid_review_artifact(
    action: str,
    value: object,
    *,
    required_skills: list[dict[str, Any]] | None = None,
) -> bool:
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        return False
    if action in {"INDEPENDENT_REVIEW_PASS", "REVIEW_BLOCKED"}:
        strict_skills = required_skills is not None
        skills = required_skills or []
        blocked = action == "REVIEW_BLOCKED"
        expected_keys = (
            {
                "schemaVersion", "kind", "reviewer", "isolation", "verdict",
                "summary",
            }
            if blocked
            else {
                "schemaVersion", "kind", "reviewer", "isolation", "verdict",
                "findings",
            }
        )
        if skills or blocked or (not strict_skills and "skillUsage" in value):
            expected_keys.add("skillUsage")
        skill_usage_valid = (
            not _skill_usage_issues(
                value.get("skillUsage", []),
                skills,
                require_applied=not blocked,
            )
            and (
                not blocked
                or any(
                    usage.get("status") == "BLOCKED"
                    for usage in value.get("skillUsage", [])
                    if isinstance(usage, dict)
                )
            )
            if strict_skills
            else (
                (
                    "skillUsage" not in value
                    and not blocked
                )
                or _stored_skill_usage_valid(
                    value.get("skillUsage"),
                    require_applied=not blocked,
                )
            )
        )
        result = (
            set(value) == expected_keys
            and value.get("kind") == "INDEPENDENT_REVIEW"
            and non_empty_string(value.get("reviewer"))
            and value.get("isolation") == "FRESH_READ_ONLY"
            and value.get("verdict") == (
                "BLOCKED" if blocked else "PASS"
            )
            and skill_usage_valid
        )
        if blocked:
            return (
                result
                and bool(skills or not strict_skills)
                and concrete_skill_evidence(value.get("summary"))
            )
        findings = value.get("findings")
        return (
            result
            and isinstance(findings, dict)
            and set(findings) == {"p0", "p1"}
            and findings.get("p0") == 0
            and findings.get("p1") == 0
            and skill_usage_valid
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
    if value["status"] == "REVIEW_BLOCKED":
        return (
            _valid_acceptance_evidence(
                value.get("review"),
                {"REVIEW_BLOCKED"},
            )
            and value.get("userConfirmation") is None
        )
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

def valid_task_result_artifact(
    value: object,
    *,
    item_id: str,
    operation_id: str,
    status: str,
    required_skills: list[dict[str, Any]] | None = None,
    generated_file_roots: list[dict[str, str]] | None = None,
) -> bool:
    return not task_result_artifact_issues(
        value,
        item_id=item_id,
        operation_id=operation_id,
        requested_status=status,
        required_skills=required_skills,
        generated_file_roots=generated_file_roots,
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
    required_skills: list[dict[str, Any]] | None = None,
    generated_file_roots: list[dict[str, str]] | None = None,
) -> list[str]:
    """Return precise field-level issues for one Task result artifact."""
    skills = required_skills or []
    expected_fields = set(TASK_RESULT_ARTIFACT_FIELDS)
    generated_roots = generated_file_roots or []
    if generated_roots:
        expected_fields.add("generatedFiles")
    if skills:
        expected_fields.add("skillUsage")
    issues = _key_issues(value, expected_fields)
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
    if skills:
        issues.extend(_skill_usage_issues(
            value.get("skillUsage"),
            skills,
            require_applied=requested_status == "IMPLEMENTED",
        ))

    changed_files = value.get("changedFiles")
    if not isinstance(changed_files, list):
        issues.append("changedFiles must be an array")
    else:
        for index, path in enumerate(changed_files):
            if not non_empty_string(path):
                issues.append(
                    f"changedFiles[{index}] must be a non-empty string"
                )
    if generated_roots:
        generated_files = value.get("generatedFiles")
        issues.extend(_generated_file_issues(
            changed_files=changed_files,
            generated_files=generated_files,
            generated_file_roots=generated_roots,
            field="generatedFiles",
        ))
        if isinstance(changed_files, list) and isinstance(
            generated_files,
            list,
        ):
            declared = {
                str(path).replace("\\", "/")
                for path in generated_files
                if isinstance(path, str)
            }
            undeclared = sorted(
                str(path).replace("\\", "/")
                for path in changed_files
                if isinstance(path, str)
                and _path_in_generated_roots(path, generated_roots)
                and str(path).replace("\\", "/") not in declared
            )
            if undeclared:
                issues.append(
                    (
                        "changedFiles under ADD-only generated roots must "
                        "also appear in generatedFiles: "
                    )
                    + ", ".join(undeclared)
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
    required_skills: list[dict[str, Any]] | None = None,
) -> list[str]:
    skills = required_skills or []
    expected_fields = set(GATE_ARTIFACT_FIELDS)
    if skills:
        expected_fields.add("skillUsage")
    issues = _key_issues(value, expected_fields)
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
    if skills:
        issues.extend(_skill_usage_issues(
            value.get("skillUsage"),
            skills,
            require_applied=(
                requested_verdict == "PASS"
                or (
                    requested_verdict is None
                    and value.get("verdict") == "PASS"
                )
            ),
        ))

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
        generated_roots = _generated_file_roots(definition)
        generated_files = scope.get("generatedFiles", [])
        issues.extend(_generated_file_issues(
            changed_files=scope.get("changedFiles"),
            generated_files=generated_files,
            generated_file_roots=generated_roots,
            field="scope.generatedFiles",
        ))
        declared_generated = {
            str(path).replace("\\", "/")
            for path in generated_files
            if isinstance(path, str)
        }
        allowed = {
            item["path"]
            for item in definition["developmentPlan"].get("fileChanges", [])
        }
        allowed.update(additional_planned_files or set())
        unauthorized = sorted(
            path.replace("\\", "/")
            for path in scope.get("changedFiles", [])
            if (
                isinstance(path, str)
                and path.replace("\\", "/") not in allowed
                and path.replace("\\", "/") not in declared_generated
            )
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
    required_skills: list[dict[str, Any]] | None = None,
) -> bool:
    if not isinstance(value, dict):
        return False
    scope = value.get("scope")
    findings = value.get("findings")
    skills = required_skills or []
    expected_fields = set(GATE_ARTIFACT_FIELDS)
    if skills:
        expected_fields.add("skillUsage")
    generated_roots = _generated_file_roots(definition)
    expected_scope_fields = {"changedFiles", "outOfScopeFiles"}
    if generated_roots:
        expected_scope_fields.add("generatedFiles")
    if not (
        set(value) == expected_fields
        and value.get("schemaVersion") == SCHEMA_VERSION
        and value.get("kind") == "WORK_ITEM_GATE"
        and value.get("workItemId") == entry["id"]
        and value.get("baselineFingerprint") == entry["baselineFingerprint"]
        and value.get("verdict") in {"PASS", "FAIL"}
        and non_empty_string(value.get("summary"))
        and isinstance(scope, dict)
        and set(scope) == expected_scope_fields
        and isinstance(scope.get("changedFiles"), list)
        and all(non_empty_string(item) for item in scope["changedFiles"])
        and isinstance(scope.get("outOfScopeFiles"), list)
        and all(non_empty_string(item) for item in scope["outOfScopeFiles"])
        and not _generated_file_issues(
            changed_files=scope.get("changedFiles"),
            generated_files=scope.get("generatedFiles", []),
            generated_file_roots=generated_roots,
            field="scope.generatedFiles",
        )
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
        and not _skill_usage_issues(
            value.get("skillUsage", []),
            skills,
            require_applied=value.get("verdict") == "PASS",
        )
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
    generated_files = {
        item.replace("\\", "/")
        for item in scope.get("generatedFiles", [])
    }
    if definition["kind"] == "TASK" and any(
        item.replace("\\", "/") not in planned_files
        and item.replace("\\", "/") not in generated_files
        for item in scope["changedFiles"]
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
