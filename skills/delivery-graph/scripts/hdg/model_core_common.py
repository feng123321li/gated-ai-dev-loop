from __future__ import annotations

import os

import re

from pathlib import Path

from typing import Any

from .constants import MAX_HIERARCHY_DEPTH, MAX_IDENTIFIER_LENGTH, SCHEMA_VERSION

from .database_contracts import validate_task_database_contract

from .errors import fail

from .jsonio import fingerprint

from .loop_contracts import (
    validate_loop_assurance_profile,
    validate_loop_descriptor,
)

WORK_ITEM_SCHEMA_VERSION = SCHEMA_VERSION

WORK_ITEM_KINDS = ("GROUP", "TASK")

WORK_ITEM_AUTHORITIES = {
    "GROUP": "COORDINATION",
    "TASK": "EXECUTION",
}

ITEM_ID = re.compile(
    rf"^[a-z0-9][a-z0-9._-]{{0,{MAX_IDENTIFIER_LENGTH - 1}}}$"
)

SKILL_HINT_NAME = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
)

PLACEHOLDER = re.compile(
    r"\b(?:TBD|TODO|FIXME|PLACEHOLDER)\b|<[^>\n]+>|\{\{[^}\n]+\}\}|\?\?\?",
    re.I,
)

CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

GIT_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

GIT_REF_FORBIDDEN = re.compile(r"[\x00-\x20\x7f~^:?*[\]\\]")

REQUIREMENT_KEY = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,127}$"
)

def _exact_keys(value: object, expected: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == expected

def _shape_error(
    code: str,
    message: str,
    value: object,
    *,
    field: str,
    expected: set[str],
) -> None:
    actual = set(value) if isinstance(value, dict) else set()
    fail(
        code,
        message,
        field=field,
        expectedKeys=sorted(expected),
        actualKeys=sorted(actual),
        missingKeys=sorted(expected - actual),
        unknownKeys=sorted(actual - expected),
        actualType=type(value).__name__,
    )

def _text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or PLACEHOLDER.search(value)
        or CONTROL.search(value)
    ):
        fail(
            "WORK_ITEM_VALUE_INVALID",
            f"{field} must be nonempty text without placeholders",
            field=field,
        )
    return value.strip()

def safe_id(value: object, field: str = "id") -> str:
    reserved = re.compile(
        r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)"
    )
    if (
        not isinstance(value, str)
        or not ITEM_ID.fullmatch(value)
        or value.endswith(".")
        or reserved.match(value)
    ):
        fail(
            "WORK_ITEM_ID_INVALID",
            f"{field} must be a safe lowercase identifier",
            field=field,
            maxLength=MAX_IDENTIFIER_LENGTH,
        )
    return value

def _depends_on(
    value: object,
    *,
    item_id: str,
    field: str,
) -> list[str]:
    if not isinstance(value, list):
        fail(
            "WORK_ITEM_DEPENDENCY_INVALID",
            f"{field} must be an array",
            field=field,
        )
    result = [
        safe_id(entry, f"{field}[{index}]")
        for index, entry in enumerate(value)
    ]
    if item_id in result or len(set(result)) != len(result):
        fail(
            "WORK_ITEM_DEPENDENCY_INVALID",
            "Dependencies must be unique and cannot reference the item itself",
            field=field,
        )
    return sorted(result)

def _skill_hints(
    value: object,
    *,
    field: str = "hierarchy.root.skillHints",
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        fail(
            "WORK_ITEM_SKILL_HINT_INVALID",
            "skillHints must be an array",
            field=field,
        )
    expected = {"name", "purpose"}
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        entry_field = f"{field}[{index}]"
        if not _exact_keys(entry, expected):
            _shape_error(
                "WORK_ITEM_SKILL_HINT_INVALID",
                "Skill hint fields are invalid",
                entry,
                field=entry_field,
                expected=expected,
            )
        name = entry["name"]
        if (
            not isinstance(name, str)
            or not SKILL_HINT_NAME.fullmatch(name)
        ):
            fail(
                "WORK_ITEM_SKILL_HINT_INVALID",
                "Skill hint name must be a safe host catalog name",
                field=f"{entry_field}.name",
            )
        if name in seen:
            fail(
                "WORK_ITEM_SKILL_HINT_INVALID",
                f"Duplicate Skill hint: {name}",
                field=f"{entry_field}.name",
            )
        seen.add(name)
        normalized.append(
            {
                "name": name,
                "purpose": _text(
                    entry["purpose"],
                    f"{entry_field}.purpose",
                ),
            }
        )
    return sorted(normalized, key=lambda item: item["name"])

def _child_summaries(
    value: object,
    *,
    field: str,
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        fail(
            "WORK_ITEM_CHILDREN_INVALID",
            "GROUP must declare at least one child",
            field=field,
        )
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    expected = {"id", "kind", "title"}
    for index, entry in enumerate(value):
        child_field = f"{field}[{index}]"
        if not _exact_keys(entry, expected):
            _shape_error(
                "WORK_ITEM_CHILDREN_INVALID",
                "Child summary fields are invalid",
                entry,
                field=child_field,
                expected=expected,
            )
        child_kind = entry["kind"]
        if child_kind not in WORK_ITEM_KINDS:
            fail(
                "WORK_ITEM_CHILDREN_INVALID",
                "GROUP children must be GROUP or TASK",
                field=f"{child_field}.kind",
            )
        child_id = safe_id(entry["id"], f"{child_field}.id")
        if child_id in seen:
            fail(
                "WORK_ITEM_CHILDREN_INVALID",
                f"Duplicate child ID: {child_id}",
                field=f"{child_field}.id",
            )
        seen.add(child_id)
        result.append(
            {
                "id": child_id,
                "kind": child_kind,
                "title": _text(entry["title"], f"{child_field}.title"),
            }
        )
    return sorted(result, key=lambda item: item["id"])

def _normalize_parent(
    definition: dict[str, Any],
    parent: dict[str, Any] | None,
) -> str | None:
    kind = definition["kind"]
    parent_id = definition.get("parentId")
    if parent is None:
        if parent_id is not None:
            fail(
                "WORK_ITEM_PARENT_INVALID",
                "A hierarchy root must use parentId=null",
            )
        return None
    if parent_id is None:
        fail(
            "WORK_ITEM_PARENT_INVALID",
            f"Nested {kind} nodes must declare parentId",
        )
    normalized_parent_id = safe_id(parent_id, "parentId")
    if normalized_parent_id != parent["id"]:
        fail(
            "WORK_ITEM_PARENT_INVALID",
            f"{kind} must reference its supplied parent",
        )
    if parent["kind"] != "GROUP":
        fail(
            "WORK_ITEM_PARENT_INVALID",
            f"{kind} parent must be GROUP",
        )
    planned = next(
        (
            child
            for child in parent.get("children", [])
            if child["id"] == definition["id"]
            and child["kind"] == definition["kind"]
        ),
        None,
    )
    if planned is None:
        fail(
            "WORK_ITEM_PARENT_PLAN_MISMATCH",
            f"{definition['id']} is not declared by its parent",
        )
    return normalized_parent_id

def validate_work_item_definition(
    definition: object,
    *,
    parent: dict[str, Any] | None = None,
    field: str = "definition",
    enforce_resource_limits: bool = True,
) -> dict[str, Any]:
    """Validate scheduler metadata without interpreting Loop payloads."""

    if not isinstance(definition, dict):
        fail(
            "WORK_ITEM_DEFINITION_INVALID",
            "Work item definition must be an object",
            field=field,
        )
    if definition.get("schemaVersion") != WORK_ITEM_SCHEMA_VERSION:
        fail(
            "WORK_ITEM_SCHEMA_INVALID",
            f"schemaVersion must be {WORK_ITEM_SCHEMA_VERSION}",
            field=f"{field}.schemaVersion",
        )
    kind = definition.get("kind")
    if kind not in WORK_ITEM_KINDS:
        fail(
            "WORK_ITEM_KIND_INVALID",
            "kind must be GROUP or TASK",
            field=f"{field}.kind",
        )
    common = {
        "schemaVersion",
        "id",
        "kind",
        "parentId",
        "title",
        "summary",
    }
    if kind == "GROUP":
        expected = common | {"decomposition", "children"}
    else:
        expected = common | {"execution"}
    if not _exact_keys(definition, expected):
        _shape_error(
            "WORK_ITEM_DEFINITION_INVALID",
            "Work item definition fields are invalid",
            definition,
            field=field,
            expected=expected,
        )

    item_id = safe_id(definition["id"], f"{field}.id")
    normalized: dict[str, Any] = {
        "schemaVersion": WORK_ITEM_SCHEMA_VERSION,
        "id": item_id,
        "kind": kind,
        "title": _text(definition["title"], f"{field}.title"),
        "summary": _text(definition["summary"], f"{field}.summary"),
    }
    normalized["parentId"] = _normalize_parent(
        {**definition, "id": item_id, "kind": kind},
        parent,
    )

    if kind == "TASK":
        execution = definition["execution"]
        execution_fields = {"dependsOn", "loop"}
        if not _exact_keys(execution, execution_fields):
            _shape_error(
                "WORK_ITEM_EXECUTION_INVALID",
                "Task execution fields are invalid",
                execution,
                field=f"{field}.execution",
                expected=execution_fields,
            )
        normalized["execution"] = {
            "dependsOn": _depends_on(
                execution["dependsOn"],
                item_id=item_id,
                field=f"{field}.execution.dependsOn",
            ),
            "loop": validate_loop_descriptor(
                execution["loop"],
                field=f"{field}.execution.loop",
            ),
        }
        validate_task_database_contract(
            normalized["execution"]["loop"],
            field=f"{field}.execution.loop",
            enforce_resource_limits=enforce_resource_limits,
        )
        return normalized

    decomposition = definition["decomposition"]
    decomposition_fields = {"dependsOn"}
    if not _exact_keys(decomposition, decomposition_fields):
        _shape_error(
            "WORK_ITEM_DECOMPOSITION_INVALID",
            "Coordination decomposition fields are invalid",
            decomposition,
            field=f"{field}.decomposition",
            expected=decomposition_fields,
        )
    normalized["decomposition"] = {
        "dependsOn": _depends_on(
            decomposition["dependsOn"],
            item_id=item_id,
            field=f"{field}.decomposition.dependsOn",
        )
    }
    normalized["children"] = _child_summaries(
        definition["children"],
        field=f"{field}.children",
    )
    return normalized

def _requirement_key(value: object) -> str:
    field = "hierarchy.delivery.requirementKey"
    if (
        not isinstance(value, str)
        or not value.strip()
        or not REQUIREMENT_KEY.fullmatch(value.strip())
    ):
        fail(
            "DELIVERY_REQUIREMENT_KEY_INVALID",
            "requirementKey must be a stable external requirement key",
            field=field,
        )
    return value.strip().upper()

def work_item_dependencies(definition: dict[str, Any]) -> list[str]:
    return (
        definition["execution"]["dependsOn"]
        if definition["kind"] == "TASK"
        else definition["decomposition"]["dependsOn"]
    )
