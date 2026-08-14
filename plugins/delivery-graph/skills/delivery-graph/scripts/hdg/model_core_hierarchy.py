from __future__ import annotations

from .model_core_common import (
    Any,
    CONTROL,
    GIT_COMMIT,
    GIT_REF_FORBIDDEN,
    MAX_HIERARCHY_DEPTH,
    Path,
    WORK_ITEM_KINDS,
    WORK_ITEM_SCHEMA_VERSION,
    _exact_keys,
    _requirement_key,
    _shape_error,
    _skill_hints,
    _text,
    fail,
    fingerprint,
    os,
    safe_id,
    validate_loop_assurance_profile,
    validate_loop_descriptor,
    validate_work_item_definition,
    work_item_dependencies,
)


def _delivery_definition(value: object) -> dict[str, Any]:
    required = {"id", "title", "summary", "reviewLoop"}
    expected = required | {
        "assuranceProfile",
        "assuranceRationale",
        "requirementKey",
        "gitBinding",
        "projectScopes",
    }
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
    assurance_profile = validate_loop_assurance_profile(
        value.get("assuranceProfile", "STANDARD")
    )
    assurance_rationale = value.get("assuranceRationale")
    if assurance_profile == "LIGHT" and assurance_rationale is None:
        fail(
            "DELIVERY_ASSURANCE_INVALID",
            "LIGHT assurance requires a rationale based on the actual "
            "change content and impact scope",
            field="hierarchy.delivery.assuranceRationale",
        )
    if assurance_rationale is not None:
        assurance_rationale = _text(
            assurance_rationale,
            "hierarchy.delivery.assuranceRationale",
        )
    review_loop = value["reviewLoop"]
    if assurance_profile == "LIGHT":
        if review_loop is not None:
            fail(
                "DELIVERY_ASSURANCE_INVALID",
                "LIGHT assurance does not create Delivery Acceptance/Readiness",
                field="hierarchy.delivery.reviewLoop",
            )
        normalized_review_loop = None
    else:
        if review_loop is None:
            fail(
                "DELIVERY_REVIEW_REQUIRED",
                "STANDARD assurance requires Delivery Acceptance/Readiness",
                field="hierarchy.delivery.reviewLoop",
            )
        normalized_review_loop = validate_loop_descriptor(
            review_loop,
            field="hierarchy.delivery.reviewLoop",
        )
    normalized = {
        "id": safe_id(value["id"], "hierarchy.delivery.id"),
        "title": _text(value["title"], "hierarchy.delivery.title"),
        "summary": _text(value["summary"], "hierarchy.delivery.summary"),
        "reviewLoop": normalized_review_loop,
    }
    if "assuranceProfile" in value:
        normalized["assuranceProfile"] = assurance_profile
    if assurance_rationale is not None:
        normalized["assuranceRationale"] = assurance_rationale
    if "requirementKey" in value:
        normalized["requirementKey"] = _requirement_key(
            value["requirementKey"]
        )
    if "gitBinding" in value:
        normalized["gitBinding"] = _git_binding(
            value["gitBinding"],
            field="hierarchy.delivery.gitBinding",
        )
    if "projectScopes" in value:
        normalized["projectScopes"] = _project_scopes(
            value["projectScopes"],
        )
        branch_refs = {
            binding["branchRef"]
            for binding in (
                [normalized["gitBinding"]]
                if "gitBinding" in normalized
                else []
            )
            + [
                scope["gitBinding"]
                for scope in normalized["projectScopes"]
                if "gitBinding" in scope
                and scope["access"] == "READ_WRITE"
            ]
        }
        if len(branch_refs) > 1:
            fail(
                "DELIVERY_PROJECT_BRANCH_MISMATCH",
                "Every writable Git project in one Delivery must use the "
                "same feature branch name",
                branchRefs=sorted(branch_refs),
            )
    return normalized

def _project_scopes(value: object) -> list[dict[str, Any]]:
    field = "hierarchy.delivery.projectScopes"
    if not isinstance(value, list) or not value:
        fail(
            "DELIVERY_PROJECT_SCOPE_INVALID",
            "projectScopes must be a nonempty array when supplied",
            field=field,
        )
    normalized: list[dict[str, Any]] = []
    project_ids: set[str] = set()
    workspace_roots: set[str] = set()
    required = {"id", "workspaceRoot", "access"}
    allowed = required | {"gitBinding"}
    for index, entry in enumerate(value):
        entry_field = f"{field}[{index}]"
        if (
            not isinstance(entry, dict)
            or not required.issubset(entry)
            or not set(entry).issubset(allowed)
        ):
            _shape_error(
                "DELIVERY_PROJECT_SCOPE_INVALID",
                "Project scope fields are invalid",
                entry,
                field=entry_field,
                expected=allowed,
            )
        project_id = safe_id(entry["id"], f"{entry_field}.id")
        if project_id in project_ids:
            fail(
                "DELIVERY_PROJECT_SCOPE_INVALID",
                f"Duplicate project scope ID: {project_id}",
                field=f"{entry_field}.id",
            )
        workspace_root_value = entry["workspaceRoot"]
        if (
            not isinstance(workspace_root_value, str)
            or not workspace_root_value.strip()
            or CONTROL.search(workspace_root_value)
        ):
            fail(
                "DELIVERY_PROJECT_SCOPE_INVALID",
                "workspaceRoot must be an absolute filesystem path",
                field=f"{entry_field}.workspaceRoot",
            )
        raw_workspace_root = Path(workspace_root_value.strip())
        if not raw_workspace_root.is_absolute():
            fail(
                "DELIVERY_PROJECT_SCOPE_INVALID",
                "workspaceRoot must be an absolute filesystem path",
                field=f"{entry_field}.workspaceRoot",
            )
        workspace_root = str(raw_workspace_root.absolute())
        normalized_root = os.path.normcase(
            os.path.normpath(str(Path(workspace_root)))
        )
        if normalized_root in workspace_roots:
            fail(
                "DELIVERY_PROJECT_SCOPE_INVALID",
                "Project workspace roots must be unique",
                field=f"{entry_field}.workspaceRoot",
            )
        access = entry["access"]
        if access not in {"READ_ONLY", "READ_WRITE"}:
            fail(
                "DELIVERY_PROJECT_SCOPE_INVALID",
                "Project access must be READ_ONLY or READ_WRITE",
                field=f"{entry_field}.access",
            )
        project_scope: dict[str, Any] = {
            "id": project_id,
            "workspaceRoot": workspace_root,
            "access": access,
        }
        if "gitBinding" in entry:
            project_scope["gitBinding"] = _git_binding(
                entry["gitBinding"],
                field=f"{entry_field}.gitBinding",
            )
        normalized.append(project_scope)
        project_ids.add(project_id)
        workspace_roots.add(normalized_root)
    return sorted(normalized, key=lambda item: item["id"])

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
            "baseRef and integrationTarget must identify the same base "
            "integration branch",
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

def validate_hierarchy_definition(
    hierarchy: object,
    *,
    enforce_resource_limits: bool = True,
) -> dict[str, Any]:
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
        depth: int = 0,
    ) -> dict[str, Any]:
        if enforce_resource_limits and depth > MAX_HIERARCHY_DEPTH:
            fail(
                "WORK_ITEM_HIERARCHY_TOO_DEEP",
                f"Hierarchy nesting exceeds the {MAX_HIERARCHY_DEPTH} level "
                "limit",
                field=field,
            )
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
            enforce_resource_limits=enforce_resource_limits,
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
            if review_loop is None and not (
                is_root
                and delivery.get("assuranceProfile", "STANDARD") == "LIGHT"
            ):
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
                depth=depth + 1,
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
    if delivery.get("assuranceProfile", "STANDARD") == "LIGHT" and any(
        node["definition"]["kind"] == "TASK"
        and "databaseChanges"
        in node["definition"]["execution"]["loop"]["payload"]
        for node in iter_hierarchy_nodes({"root": root})
    ):
        fail(
            "DELIVERY_ASSURANCE_INVALID",
            "Database schema or migration changes require STANDARD assurance",
            field="hierarchy.delivery.assuranceProfile",
        )
    if delivery.get("assuranceProfile", "STANDARD") == "LIGHT":
        if root_definition["kind"] != "TASK":
            fail(
                "DELIVERY_ASSURANCE_INVALID",
                "LIGHT assurance supports only one root TASK",
                field="hierarchy.root.definition.kind",
            )
        if root["reviewLoop"] is not None:
            fail(
                "DELIVERY_ASSURANCE_INVALID",
                "LIGHT assurance does not create a TASK Review Loop",
                field="hierarchy.root.reviewLoop",
            )
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
    if project_name == "delivery-graph" and not explicit_dogfood:
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
