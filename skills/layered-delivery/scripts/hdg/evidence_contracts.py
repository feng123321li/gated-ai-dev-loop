from __future__ import annotations

from typing import Any

from .constants import SCHEMA_VERSION
from .graph_model import FAILURE_CLASSES


from .evidence_validation import (
    VALIDATION_REMEDIATION_SOURCES,
    VALIDATION_REMEDIATION_ASSERTIONS,
    VALIDATION_REMEDIATION_ARTIFACT_FIELDS,
    _generated_file_roots,
    _skill_usage_template,
)


def gate_evidence_contract(
    entry: dict[str, Any],
    definition: dict[str, Any],
    *,
    additional_planned_files: set[str] | None = None,
    required_skills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the compact v3 delta contract for one gate artifact."""
    allowed_changed_files = {
        item["path"]
        for item in definition["developmentPlan"].get("fileChanges", [])
    }
    allowed_changed_files.update(additional_planned_files or set())
    skills = required_skills or []
    generated_roots = _generated_file_roots(definition)
    delta_template = {
        "verdict": "<PASS_OR_FAIL>",
        "summary": "<REQUIRED_NON_EMPTY_STRING>",
        "changedFiles": [],
        "outOfScopeFiles": [],
        "acceptance": [
            {
                "id": item["id"],
                "status": "<PASS_OR_FAIL>",
                "evidence": "<REQUIRED_NON_EMPTY_STRING>",
            }
            for item in definition["acceptance"]
        ],
        "tests": [
            {
                "commandIndex": index,
                "exitCode": "<INTEGER>",
                "summary": "<REQUIRED_NON_EMPTY_STRING>",
            }
            for index, _ in enumerate(definition["testCommands"])
        ],
        "findings": {"p0": [], "p1": [], "p2": []},
    }
    if generated_roots:
        delta_template["generatedFiles"] = []
    if skills:
        delta_template["skillUsage"] = _skill_usage_template(skills)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifactKind": "WORK_ITEM_GATE",
        "submissionMode": "DELTA",
        "workItemId": entry["id"],
        "baselineFingerprint": entry["baselineFingerprint"],
        "immutableBindings": [
            "schemaVersion",
            "kind",
            "workItemId",
            "baselineFingerprint",
            "acceptance.requirementIds",
            "tests.argv",
        ],
        "evidenceDeltaTemplate": delta_template,
        "constraints": {
            "requiredSkills": skills,
            "acceptanceIds": [item["id"] for item in definition["acceptance"]],
            "acceptanceExpectedResults": {
                item["id"]: item["expectedResult"]
                for item in definition["acceptance"]
            },
            "testCommandIndexes": list(range(len(definition["testCommands"]))),
            "allowedChangedFiles": (
                sorted(allowed_changed_files)
                if definition["kind"] == "TASK"
                else None
            ),
            "addOnlyGeneratedRoots": generated_roots,
            "testsRun": "OPTIONAL_NON_NEGATIVE_INTEGER",
            "passRequires": [
                "outOfScopeFiles must be empty",
                "every acceptance status must be PASS",
                "every test exitCode must be 0",
                "findings.p0 and findings.p1 must be empty",
                "every required Skill usage status must be APPLIED",
            ],
        },
    }

def task_result_evidence_contract(
    entry: dict[str, Any],
    definition: dict[str, Any],
    *,
    authorized_file_changes: list[dict[str, Any]],
    required_skills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the compact v3 delta contract bound to the active claim."""
    operation_id = entry["claim"]["operationId"]
    test_templates = [
        {
            "commandIndex": index,
            "exitCode": "<INTEGER>",
        }
        for index, _ in enumerate(definition["testCommands"])
    ]
    skills = required_skills or []
    generated_roots = _generated_file_roots(definition)
    delta_template = {
        "summary": "<REQUIRED_NON_EMPTY_STRING>",
        "changedFiles": [],
        "tests": test_templates,
        "blockers": [],
        "failure": None,
    }
    if generated_roots:
        delta_template["generatedFiles"] = []
    if skills:
        delta_template["skillUsage"] = _skill_usage_template(skills)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifactKind": "TASK_RESULT",
        "submissionMode": "DELTA",
        "taskId": entry["id"],
        "operationId": operation_id,
        "immutableBindings": [
            "schemaVersion",
            "kind",
            "taskId",
            "operationId",
            "status",
            "tests.argv",
        ],
        "evidenceDeltaTemplate": delta_template,
        "constraints": {
            "requiredSkills": skills,
            "statusValues": ["IMPLEMENTED", "BLOCKED"],
            "testCommandIndexes": list(range(len(definition["testCommands"]))),
            "authorizedChangedFiles": sorted(
                item["path"] for item in authorized_file_changes
            ),
            "addOnlyGeneratedRoots": generated_roots,
            "changedFilesPolicy": (
                "REPORT_FACTUAL_FILES; AUTHORIZATION_IS_ENFORCED_BY_GATE"
            ),
            "testsRun": "OPTIONAL_NON_NEGATIVE_INTEGER",
            "failureClasses": list(FAILURE_CLASSES[:5]),
            "implementedRequires": [
                "blockers must be empty",
                "failure must be null",
                "every required Skill usage status must be APPLIED",
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

def review_evidence_contract(
    required_skills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    skills = required_skills or []
    independent = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "INDEPENDENT_REVIEW",
        "reviewer": "<REQUIRED_NON_EMPTY_STRING>",
        "isolation": "FRESH_READ_ONLY",
        "verdict": "PASS",
        "findings": {"p0": 0, "p1": 0},
    }
    if skills:
        independent["skillUsage"] = _skill_usage_template(skills)
    action_options = {
        "INDEPENDENT_REVIEW_PASS": independent,
        "HUMAN_REVIEW_ACCEPTED": {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "HUMAN_REVIEW",
            "reviewer": "<REQUIRED_NON_EMPTY_STRING>",
            "verdict": "ACCEPTED",
        },
    }
    if skills:
        action_options["REVIEW_BLOCKED"] = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "INDEPENDENT_REVIEW",
            "reviewer": "<REQUIRED_NON_EMPTY_STRING>",
            "isolation": "FRESH_READ_ONLY",
            "verdict": "BLOCKED",
            "summary": "<CONCRETE_UNAVAILABILITY_REASON>",
            "skillUsage": [
                {
                    "name": item["name"],
                    "stage": item["stage"],
                    "status": "BLOCKED",
                    "evidence": "<CONCRETE_UNAVAILABILITY_REASON>",
                }
                for item in skills
            ],
        }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifactKind": "ROOT_REVIEW",
        "actionOptions": action_options,
        "constraints": {
            "requiredSkills": skills,
            "humanReviewMayBypassRequiredSkills": False,
            "reviewBlockedRequires": (
                "At least one exact required Skill usage must be BLOCKED "
                "with a concrete unavailability reason"
            ),
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
