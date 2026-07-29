from __future__ import annotations

import re
from typing import Any

from .constants import MAX_IDENTIFIER_LENGTH, SCHEMA_VERSION
from .errors import fail
from .jsonio import fingerprint
from .loop_contracts import validate_loop_descriptor


WORK_ITEM_SCHEMA_VERSION = SCHEMA_VERSION
WORK_ITEM_KINDS = ("DELIVERY", "CAPABILITY", "TASK")
WORK_ITEM_AUTHORITIES = {
    "DELIVERY": "COORDINATION",
    "CAPABILITY": "COORDINATION",
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


def _skill_hints(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        fail(
            "WORK_ITEM_SKILL_HINT_INVALID",
            "skillHints must be an array",
            field="hierarchy.skillHints",
        )
    expected = {"name", "purpose"}
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        field = f"hierarchy.skillHints[{index}]"
        if not _exact_keys(entry, expected):
            _shape_error(
                "WORK_ITEM_SKILL_HINT_INVALID",
                "Skill hint fields are invalid",
                entry,
                field=field,
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
                field=f"{field}.name",
            )
        if name in seen:
            fail(
                "WORK_ITEM_SKILL_HINT_INVALID",
                f"Duplicate Skill hint: {name}",
                field=f"{field}.name",
            )
        seen.add(name)
        normalized.append(
            {
                "name": name,
                "purpose": _text(
                    entry["purpose"],
                    f"{field}.purpose",
                ),
            }
        )
    return sorted(normalized, key=lambda item: item["name"])


def _child_summaries(
    value: object,
    *,
    parent_kind: str,
    field: str,
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        fail(
            "WORK_ITEM_CHILDREN_INVALID",
            f"{parent_kind} must declare at least one child",
            field=field,
        )
    expected_kind = "CAPABILITY" if parent_kind == "DELIVERY" else "TASK"
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
        if entry["kind"] != expected_kind:
            fail(
                "WORK_ITEM_CHILDREN_INVALID",
                f"{parent_kind} children must be {expected_kind}",
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
                "kind": expected_kind,
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
    if kind == "DELIVERY":
        return None
    if parent_id is None:
        if parent is not None:
            fail(
                "WORK_ITEM_PARENT_INVALID",
                f"{kind} nested in a hierarchy must declare parentId",
            )
        return None
    normalized_parent_id = safe_id(parent_id, "parentId")
    if parent is None or normalized_parent_id != parent["id"]:
        fail(
            "WORK_ITEM_PARENT_INVALID",
            f"{kind} must reference its supplied parent",
        )
    expected_parent_kind = (
        "DELIVERY" if kind == "CAPABILITY" else "CAPABILITY"
    )
    if parent["kind"] != expected_parent_kind:
        fail(
            "WORK_ITEM_PARENT_INVALID",
            f"{kind} parent must be {expected_parent_kind}",
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
            "kind must be DELIVERY, CAPABILITY, or TASK",
            field=f"{field}.kind",
        )
    common = {"schemaVersion", "id", "kind", "title", "summary"}
    if kind == "DELIVERY":
        expected = common | {"decomposition", "children"}
    elif kind == "CAPABILITY":
        expected = common | {
            "parentId",
            "decomposition",
            "children",
        }
    else:
        expected = common | {"parentId", "execution"}
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
    if kind != "DELIVERY":
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
        return normalized

    decomposition = definition["decomposition"]
    decomposition_fields = (
        set() if kind == "DELIVERY" else {"dependsOn"}
    )
    if not _exact_keys(decomposition, decomposition_fields):
        _shape_error(
            "WORK_ITEM_DECOMPOSITION_INVALID",
            "Coordination decomposition fields are invalid",
            decomposition,
            field=f"{field}.decomposition",
            expected=decomposition_fields,
        )
    normalized["decomposition"] = (
        {}
        if kind == "DELIVERY"
        else {
            "dependsOn": _depends_on(
                decomposition["dependsOn"],
                item_id=item_id,
                field=f"{field}.decomposition.dependsOn",
            )
        }
    )
    normalized["children"] = _child_summaries(
        definition["children"],
        parent_kind=kind,
        field=f"{field}.children",
    )
    return normalized


def validate_hierarchy_definition(hierarchy: object) -> dict[str, Any]:
    """Validate one complete scheduler hierarchy and its review Loop."""

    expected = {
        "schemaVersion",
        "skillHints",
        "reviewLoop",
        "root",
    }
    if not _exact_keys(hierarchy, expected):
        _shape_error(
            "WORK_ITEM_HIERARCHY_INVALID",
            "Hierarchy fields are invalid",
            hierarchy,
            field="hierarchy",
            expected=expected,
        )
    if hierarchy["schemaVersion"] != WORK_ITEM_SCHEMA_VERSION:
        fail(
            "WORK_ITEM_SCHEMA_INVALID",
            f"Hierarchy schemaVersion must be {WORK_ITEM_SCHEMA_VERSION}",
        )

    seen: set[str] = set()

    def normalize_node(
        value: object,
        parent: dict[str, Any] | None,
        *,
        field: str,
    ) -> dict[str, Any]:
        node_fields = {"definition", "children"}
        if not _exact_keys(value, node_fields):
            _shape_error(
                "WORK_ITEM_HIERARCHY_INVALID",
                "Hierarchy node fields are invalid",
                value,
                field=field,
                expected=node_fields,
            )
        if not isinstance(value["children"], list):
            fail(
                "WORK_ITEM_HIERARCHY_INVALID",
                "Hierarchy children must be an array",
                field=f"{field}.children",
            )
        definition = validate_work_item_definition(
            value["definition"],
            parent=parent,
            field=f"{field}.definition",
        )
        item_id = definition["id"]
        if item_id in seen:
            fail(
                "WORK_ITEM_HIERARCHY_INVALID",
                f"Duplicate work item ID: {item_id}",
            )
        seen.add(item_id)
        if definition["kind"] == "TASK":
            if value["children"]:
                fail(
                    "WORK_ITEM_TASK_NOT_LEAF",
                    "Task hierarchy nodes cannot contain children",
                )
            return {"definition": definition, "children": []}

        expected_children = {
            (child["id"], child["kind"], child["title"])
            for child in definition["children"]
        }
        actual_children: list[tuple[object, object, object]] = []
        for child in value["children"]:
            child_definition = (
                child.get("definition")
                if isinstance(child, dict)
                else None
            )
            if not isinstance(child_definition, dict):
                fail(
                    "WORK_ITEM_HIERARCHY_INVALID",
                    "Hierarchy child definition must be an object",
                )
            actual_children.append(
                (
                    child_definition.get("id"),
                    child_definition.get("kind"),
                    child_definition.get("title"),
                )
            )
        if (
            set(actual_children) != expected_children
            or len(actual_children) != len(expected_children)
        ):
            fail(
                "WORK_ITEM_HIERARCHY_INCOMPLETE",
                f"{item_id} must materialize every declared child once",
            )
        children = [
            normalize_node(
                child,
                definition,
                field=f"{field}.children[{index}]",
            )
            for index, child in enumerate(value["children"])
        ]
        children.sort(key=lambda item: item["definition"]["id"])
        sibling_ids = {
            child["definition"]["id"]
            for child in children
        }
        dependency_fields = (
            [
                (
                    child["definition"]["id"],
                    child["definition"]["execution"]["dependsOn"],
                )
                for child in children
            ]
            if definition["kind"] == "CAPABILITY"
            else [
                (
                    child["definition"]["id"],
                    child["definition"]["decomposition"]["dependsOn"],
                )
                for child in children
            ]
        )
        for child_id, dependencies in dependency_fields:
            unknown = set(dependencies) - sibling_ids
            if unknown:
                fail(
                    "WORK_ITEM_DEPENDENCY_INVALID",
                    f"{child_id} depends on non-sibling work items",
                    dependencyIds=sorted(unknown),
                )
        return {"definition": definition, "children": children}

    root = normalize_node(hierarchy["root"], None, field="root")
    root_definition = root["definition"]
    if (
        root_definition["kind"] == "TASK"
        and root_definition["execution"]["dependsOn"]
    ) or (
        root_definition["kind"] == "CAPABILITY"
        and root_definition["decomposition"]["dependsOn"]
    ):
        fail(
            "WORK_ITEM_DEPENDENCY_INVALID",
            "A hierarchy root cannot depend on sibling work items",
        )
    return {
        "schemaVersion": WORK_ITEM_SCHEMA_VERSION,
        "skillHints": _skill_hints(hierarchy["skillHints"]),
        "reviewLoop": validate_loop_descriptor(
            hierarchy["reviewLoop"],
            field="hierarchy.reviewLoop",
        ),
        "root": root,
    }


def iter_hierarchy_nodes(
    hierarchy: dict[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        result.append(node)
        for child in node["children"]:
            visit(child)

    visit(hierarchy["root"])
    return result


def hierarchy_fingerprint(hierarchy: dict[str, Any]) -> str:
    return fingerprint(hierarchy)


def work_item_contract_fingerprint(
    definition: dict[str, Any],
) -> str:
    return fingerprint(definition)


def work_item_child_contract_fingerprint(
    parent: dict[str, Any],
    child_id: str,
) -> str:
    child = next(
        (
            item
            for item in parent.get("children", [])
            if item["id"] == child_id
        ),
        None,
    )
    if child is None:
        fail(
            "WORK_ITEM_PARENT_PLAN_MISMATCH",
            f"{child_id} is not declared by its parent",
        )
    return fingerprint({"parentId": parent["id"], "child": child})


def work_item_baseline_fingerprint(
    definition: dict[str, Any],
) -> str:
    return fingerprint(definition)


def resource_claims_overlap(
    left: list[str],
    right: list[str],
) -> bool:
    """Compatibility-free scheduler overlap: claims are exact lock keys."""

    return bool(set(left) & set(right))


def resolve_self_hosting_policy(
    *,
    project_name: str | None,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    if project_name == "layered-delivery" and not explicit_dogfood:
        return {
            "route": "SELF_HOSTING_MAINTENANCE",
            "createsRuntimePackage": False,
            "reason": "HIERARCHICAL_GOVERNANCE_SELF_MAINTENANCE",
        }
    return {
        "route": "STANDARD_HIERARCHICAL_GOVERNANCE",
        "createsRuntimePackage": True,
        "reason": (
            "EXPLICIT_DOGFOOD"
            if explicit_dogfood
            else "NOT_SELF_HOSTING"
        ),
    }


__all__ = [
    "WORK_ITEM_AUTHORITIES",
    "WORK_ITEM_KINDS",
    "WORK_ITEM_SCHEMA_VERSION",
    "hierarchy_fingerprint",
    "iter_hierarchy_nodes",
    "resolve_self_hosting_policy",
    "resource_claims_overlap",
    "safe_id",
    "validate_hierarchy_definition",
    "validate_work_item_definition",
    "work_item_baseline_fingerprint",
    "work_item_child_contract_fingerprint",
    "work_item_contract_fingerprint",
]
