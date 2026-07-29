from __future__ import annotations

from typing import Any

from .constants import SCHEMA_VERSION


from .evidence_validation import (
    _generated_file_roots,
)


def hydrate_task_result_evidence(
    value: object,
    *,
    entry: dict[str, Any],
    definition: dict[str, Any],
    status: str,
) -> object:
    """Hydrate a compact v3 submission into the canonical v3 artifact."""
    if not (
        isinstance(value, dict)
        and set(value) == {"evidenceDelta"}
    ):
        return value
    delta = value["evidenceDelta"]
    if not isinstance(delta, dict):
        return value
    generated_roots = _generated_file_roots(definition)
    allowed = {
        "summary",
        "changedFiles",
        "tests",
        "blockers",
        "failure",
        "skillUsage",
    }
    if generated_roots:
        allowed.add("generatedFiles")
    if not set(delta).issubset(allowed):
        return value
    if not isinstance(delta.get("tests", []), list) or any(
        not isinstance(test, dict)
        or not set(test).issubset(
            {"commandIndex", "exitCode", "testsRun"}
        )
        for test in delta.get("tests", [])
    ):
        return value
    tests = []
    for test in delta.get("tests", []):
        if not isinstance(test, dict):
            tests.append(test)
            continue
        command_index = test.get("commandIndex")
        hydrated = {
            "argv": (
                list(definition["testCommands"][command_index])
                if isinstance(command_index, int)
                and not isinstance(command_index, bool)
                and 0 <= command_index < len(definition["testCommands"])
                else None
            ),
            "exitCode": test.get("exitCode"),
        }
        if "testsRun" in test:
            hydrated["testsRun"] = test["testsRun"]
        tests.append(hydrated)
    artifact = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "TASK_RESULT",
        "taskId": entry["id"],
        "operationId": entry["claim"]["operationId"],
        "status": status,
        "summary": delta.get("summary"),
        "changedFiles": delta.get("changedFiles"),
        "tests": tests,
        "blockers": (
            delta.get("blockers", [])
            if status == "IMPLEMENTED"
            else delta.get("blockers")
        ),
        "failure": delta.get("failure"),
    }
    if generated_roots:
        artifact["generatedFiles"] = delta.get("generatedFiles")
    if "skillUsage" in delta:
        artifact["skillUsage"] = delta["skillUsage"]
    return artifact

def hydrate_gate_evidence(
    value: object,
    *,
    entry: dict[str, Any],
    definition: dict[str, Any],
) -> object:
    """Hydrate a compact v3 gate delta into the canonical v3 artifact."""
    if not (
        isinstance(value, dict)
        and set(value) == {"evidenceDelta"}
    ):
        return value
    delta = value["evidenceDelta"]
    if not isinstance(delta, dict):
        return value
    generated_roots = _generated_file_roots(definition)
    allowed = {
        "verdict",
        "summary",
        "changedFiles",
        "outOfScopeFiles",
        "acceptance",
        "tests",
        "findings",
        "skillUsage",
    }
    if generated_roots:
        allowed.add("generatedFiles")
    if not set(delta).issubset(allowed):
        return value
    if (
        not isinstance(delta.get("acceptance", []), list)
        or not isinstance(delta.get("tests", []), list)
        or any(
            not isinstance(result, dict)
            or not set(result).issubset(
                {"id", "status", "evidence"}
            )
            for result in delta.get("acceptance", [])
        )
        or any(
            not isinstance(test, dict)
            or not set(test).issubset(
                {
                    "commandIndex",
                    "exitCode",
                    "testsRun",
                    "summary",
                }
            )
            for test in delta.get("tests", [])
        )
    ):
        return value
    criteria = {
        criterion["id"]: criterion
        for criterion in definition["acceptance"]
    }
    acceptance = []
    for result in delta.get("acceptance", []):
        if not isinstance(result, dict):
            acceptance.append(result)
            continue
        criterion = criteria.get(result.get("id"), {})
        acceptance.append({
            "id": result.get("id"),
            "requirementIds": criterion.get("requirementIds"),
            "status": result.get("status"),
            "evidence": result.get("evidence"),
        })
    tests = []
    for test in delta.get("tests", []):
        if not isinstance(test, dict):
            tests.append(test)
            continue
        command_index = test.get("commandIndex")
        hydrated = {
            "argv": (
                list(definition["testCommands"][command_index])
                if isinstance(command_index, int)
                and not isinstance(command_index, bool)
                and 0 <= command_index < len(definition["testCommands"])
                else None
            ),
            "exitCode": test.get("exitCode"),
            "summary": test.get("summary"),
        }
        if "testsRun" in test:
            hydrated["testsRun"] = test["testsRun"]
        tests.append(hydrated)
    scope = {
        "changedFiles": delta.get("changedFiles"),
        "outOfScopeFiles": delta.get("outOfScopeFiles"),
    }
    if generated_roots:
        scope["generatedFiles"] = delta.get("generatedFiles")
    artifact = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "WORK_ITEM_GATE",
        "workItemId": entry["id"],
        "baselineFingerprint": entry["baselineFingerprint"],
        "verdict": delta.get("verdict"),
        "summary": delta.get("summary"),
        "scope": scope,
        "acceptance": acceptance,
        "tests": tests,
        "findings": delta.get("findings"),
    }
    if "skillUsage" in delta:
        artifact["skillUsage"] = delta["skillUsage"]
    return artifact
