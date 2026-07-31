from __future__ import annotations

import re
from typing import Any

from .constants import MAX_IDENTIFIER_LENGTH, SCHEMA_VERSION
from .errors import fail
from .jsonio import fingerprint
from .loop_contracts import validate_loop_descriptor


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


def work_item_dependencies(definition: dict[str, Any]) -> list[str]:
    return (
        definition["execution"]["dependsOn"]
        if definition["kind"] == "TASK"
        else definition["decomposition"]["dependsOn"]
    )


def _delivery_definition(value: object) -> dict[str, Any]:
    required = {"id", "title", "summary", "reviewLoop"}
    expected = required | {"gitBinding"}
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or not set(value).issubset(expected)
    ):
        _shape_error(
            "DELIVERY_DEFINITION_INVALID",
            "Delivery fields are invalid",
            value,
            field="hierarchy.delivery",
            expected=expected,
        )
    normalized = {
        "id": safe_id(value["id"], "hierarchy.delivery.id"),
        "title": _text(value["title"], "hierarchy.delivery.title"),
        "summary": _text(value["summary"], "hierarchy.delivery.summary"),
        "reviewLoop": validate_loop_descriptor(
            value["reviewLoop"],
            field="hierarchy.delivery.reviewLoop",
        ),
    }
    if "gitBinding" in value:
        normalized["gitBinding"] = _git_binding(
            value["gitBinding"],
            field="hierarchy.delivery.gitBinding",
        )
    return normalized


def _git_branch_ref(value: object, field: str) -> str:
    if not isinstance(value, str):
        fail(
            "DELIVERY_GIT_BINDING_INVALID",
            f"{field} must be a local Git branch name",
            field=field,
        )
    branch = value.strip()
    components = branch.split("/")
    if (
        not branch
        or len(branch) > 240
        or branch == "@"
        or branch.startswith("-")
        or branch.startswith("refs/")
        or branch.startswith("/")
        or branch.endswith("/")
        or branch.endswith(".")
        or "//" in branch
        or ".." in branch
        or "@{" in branch
        or GIT_REF_FORBIDDEN.search(branch)
        or any(
            not component
            or component.startswith(".")
            or component.endswith(".lock")
            for component in components
        )
    ):
        fail(
            "DELIVERY_GIT_BINDING_INVALID",
            f"{field} must be a safe local Git branch name",
            field=field,
        )
    return branch


def _git_binding(value: object, *, field: str) -> dict[str, str]:
    expected = {
        "branchRef",
        "baseRef",
        "baseCommit",
        "integrationTarget",
    }
    if not _exact_keys(value, expected):
        _shape_error(
            "DELIVERY_GIT_BINDING_INVALID",
            "Git binding fields are invalid",
            value,
            field=field,
            expected=expected,
        )
    branch_ref = _git_branch_ref(
        value["branchRef"],
        f"{field}.branchRef",
    )
    base_ref = _git_branch_ref(
        value["baseRef"],
        f"{field}.baseRef",
    )
    integration_target = _git_branch_ref(
        value["integrationTarget"],
        f"{field}.integrationTarget",
    )
    base_commit = value["baseCommit"]
    if (
        not isinstance(base_commit, str)
        or not GIT_COMMIT.fullmatch(base_commit)
    ):
        fail(
            "DELIVERY_GIT_BINDING_INVALID",
            "baseCommit must be a lowercase full Git object ID",
            field=f"{field}.baseCommit",
        )
    if base_ref != integration_target:
        fail(
            "DELIVERY_GIT_BINDING_INVALID",
            "baseRef and integrationTarget must identify the same "
            "mainline branch",
            field=field,
        )
    if branch_ref == integration_target:
        fail(
            "DELIVERY_GIT_BINDING_INVALID",
            "Delivery feature branch must differ from its integration target",
            field=field,
        )
    if branch_ref in {"main", "master"}:
        fail(
            "DELIVERY_GIT_BINDING_INVALID",
            "main and master are integration branches, not Delivery "
            "feature branches",
            field=f"{field}.branchRef",
        )
    return {
        "branchRef": branch_ref,
        "baseRef": base_ref,
        "baseCommit": base_commit,
        "integrationTarget": integration_target,
    }


def validate_git_binding(
    value: object,
    *,
    field: str = "hierarchy.delivery.gitBinding",
) -> dict[str, str]:
    return _git_binding(value, field=field)


def _validate_dependency_dag(
    dependencies_by_id: dict[str, list[str]],
) -> None:
    outgoing = {item_id: [] for item_id in dependencies_by_id}
    indegree = {item_id: 0 for item_id in dependencies_by_id}
    for item_id, dependencies in dependencies_by_id.items():
        for dependency_id in dependencies:
            outgoing[dependency_id].append(item_id)
            indegree[item_id] += 1
    ready = sorted(
        item_id
        for item_id, degree in indegree.items()
        if degree == 0
    )
    visited = 0
    while ready:
        item_id = ready.pop(0)
        visited += 1
        for target in sorted(outgoing[item_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    if visited != len(dependencies_by_id):
        fail(
            "WORK_ITEM_DEPENDENCY_CYCLE",
            "Sibling GROUP/TASK dependencies must be acyclic",
        )


def validate_hierarchy_definition(hierarchy: object) -> dict[str, Any]:
    """Validate one Delivery with a recursive GROUP/TASK hierarchy."""

    expected = {"delivery", "root"}
    if not _exact_keys(hierarchy, expected):
        _shape_error(
            "WORK_ITEM_HIERARCHY_INVALID",
            "Hierarchy fields are invalid",
            hierarchy,
            field="hierarchy",
            expected=expected,
        )
    delivery = _delivery_definition(hierarchy["delivery"])
    seen: set[str] = {delivery["id"]}

    def normalize_node(
        value: object,
        parent: dict[str, Any] | None,
        *,
        field: str,
    ) -> dict[str, Any]:
        is_root = parent is None
        node_fields = {"definition", "reviewLoop", "children"}
        if is_root:
            node_fields |= {"schemaVersion", "skillHints"}
        if not _exact_keys(value, node_fields):
            _shape_error(
                "WORK_ITEM_HIERARCHY_INVALID",
                "Hierarchy node fields are invalid",
                value,
                field=field,
                expected=node_fields,
            )
        root_metadata: dict[str, Any] = {}
        if is_root:
            if value["schemaVersion"] != WORK_ITEM_SCHEMA_VERSION:
                fail(
                    "WORK_ITEM_SCHEMA_INVALID",
                    "Root schemaVersion must be "
                    f"{WORK_ITEM_SCHEMA_VERSION}",
                    field=f"{field}.schemaVersion",
                )
            root_metadata = {
                "schemaVersion": WORK_ITEM_SCHEMA_VERSION,
                "skillHints": _skill_hints(
                    value["skillHints"],
                    field=f"{field}.skillHints",
                ),
            }
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
        review_loop = (
            None
            if value["reviewLoop"] is None
            else validate_loop_descriptor(
                value["reviewLoop"],
                field=f"{field}.reviewLoop",
            )
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
            if review_loop is None:
                fail(
                    "WORK_ITEM_TASK_REVIEW_REQUIRED",
                    "Every TASK hierarchy node requires a Review Loop",
                    field=f"{field}.reviewLoop",
                )
            return {
                **root_metadata,
                "definition": definition,
                "reviewLoop": review_loop,
                "children": [],
            }

        if review_loop is None:
            fail(
                "WORK_ITEM_GROUP_REVIEW_REQUIRED",
                "Every GROUP hierarchy node requires a Review Loop",
                field=f"{field}.reviewLoop",
            )

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
            raw_id = child_definition.get("id")
            raw_kind = child_definition.get("kind")
            raw_title = child_definition.get("title")
            if (
                not isinstance(raw_id, str)
                or raw_kind not in WORK_ITEM_KINDS
                or not isinstance(raw_title, str)
            ):
                fail(
                    "WORK_ITEM_HIERARCHY_INCOMPLETE",
                    f"{item_id} contains an invalid child declaration",
                )
            actual_children.append(
                (raw_id, raw_kind, raw_title)
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
        dependencies_by_id = {
            child["definition"]["id"]: work_item_dependencies(
                child["definition"]
            )
            for child in children
        }
        for child_id, dependencies in dependencies_by_id.items():
            unknown = set(dependencies) - sibling_ids
            if unknown:
                fail(
                    "WORK_ITEM_DEPENDENCY_INVALID",
                    f"{child_id} depends on non-sibling work items",
                    dependencyIds=sorted(unknown),
                )
        _validate_dependency_dag(dependencies_by_id)
        return {
            **root_metadata,
            "definition": definition,
            "reviewLoop": review_loop,
            "children": children,
        }

    root = normalize_node(hierarchy["root"], None, field="root")
    root_definition = root["definition"]
    if work_item_dependencies(root_definition):
        fail(
            "WORK_ITEM_DEPENDENCY_INVALID",
            "A hierarchy root cannot depend on sibling work items",
        )
    return {
        "delivery": delivery,
        "root": root,
    }


def iter_hierarchy_nodes(
    hierarchy: dict[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    pending = [hierarchy["root"]]
    while pending:
        node = pending.pop()
        result.append(node)
        pending.extend(reversed(node["children"]))
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
            "reason": "GRAPH_SCHEDULER_SELF_MAINTENANCE",
        }
    return {
        "route": "STANDARD_GRAPH_SCHEDULER",
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
    "validate_git_binding",
    "validate_hierarchy_definition",
    "validate_work_item_definition",
    "work_item_dependencies",
    "work_item_baseline_fingerprint",
    "work_item_child_contract_fingerprint",
    "work_item_contract_fingerprint",
]
