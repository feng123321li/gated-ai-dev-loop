from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import fail
from .fs_safe import atomic_write, safe_path
from .git_binding import (
    inspect_delivery_git_workspace,
    verify_delivery_git_binding,
    verify_delivery_project_scopes,
)
from .graph_model import (
    compile_delivery_graph,
    graph_fingerprint,
    graph_summary,
)
from .model_core import (
    hierarchy_fingerprint,
    iter_hierarchy_nodes,
    validate_hierarchy_definition,
)
from .model_rendering import (
    render_manual_handoff,
    task_baseline_relative_path,
    task_has_interface_projection,
    work_item_projection_relative_path,
)
from .repository import (
    GOVERNANCE_DIRECTORY,
    SchedulerRepository,
)


def workspace_status(
    *,
    root: str,
    root_id: str | None = None,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    result = repository.workspace_status(
        root_id=root_id,
        workspace_root=workspace_root,
    )
    selected_root_id = result.get("rootId")
    if isinstance(selected_root_id, str):
        stored = repository.hierarchy(selected_root_id)
        delivery = stored["hierarchy"]["delivery"]
        git_binding = delivery.get("gitBinding")
        project_scopes = delivery.get("projectScopes")
        verified_projects = verify_delivery_project_scopes(
            workspace_root or root,
            delivery,
            preparing=False,
        )
        git_workspace = (
            verify_delivery_git_binding(
                workspace_root or root,
                git_binding,
                preparing=False,
            )
            if project_scopes is None
            else next(
                (
                    item.get("gitWorkspace")
                    for item in verified_projects
                    if SchedulerRepository.workspace_key(
                        item["workspaceRoot"]
                    )
                    == SchedulerRepository.workspace_key(
                        workspace_root or root
                    )
                ),
                None,
            )
        )
        if git_binding is not None:
            result["gitBinding"] = git_binding
        if git_workspace is not None:
            result["gitWorkspace"] = git_workspace
        if project_scopes is not None:
            result["projectScopes"] = project_scopes
    else:
        discovery = inspect_delivery_git_workspace(
            workspace_root or root,
        )
        if discovery is not None:
            result.update(discovery)
    return result


def _human_artifacts(
    hierarchy: dict[str, Any],
) -> dict[str, Any]:
    projection_root = (
        f".layered-delivery/{hierarchy['delivery']['id']}"
    )
    task_baselines = {
        node["definition"]["id"]: f"{projection_root}/"
        + task_baseline_relative_path(
            hierarchy,
            node["definition"]["id"],
        )
        for node in iter_hierarchy_nodes(hierarchy)
        if node["definition"]["kind"] == "TASK"
    }
    work_items = {}
    for node in iter_hierarchy_nodes(hierarchy):
        definition = node["definition"]
        item_id = definition["id"]
        artifacts = {
            "kind": definition["kind"],
            "baseline": (
                f"{projection_root}/"
                + work_item_projection_relative_path(
                    hierarchy,
                    item_id,
                    "baseline.md",
                )
            ),
            "progress": (
                f"{projection_root}/"
                + work_item_projection_relative_path(
                    hierarchy,
                    item_id,
                    "progress.md",
                )
            ),
            "acceptance": (
                f"{projection_root}/"
                + work_item_projection_relative_path(
                    hierarchy,
                    item_id,
                    "acceptance.md",
                )
            ),
        }
        if task_has_interface_projection(definition):
            artifacts["interfaces"] = (
                f"{projection_root}/"
                + work_item_projection_relative_path(
                    hierarchy,
                    item_id,
                    "interfaces.md",
                )
            )
        work_items[item_id] = artifacts
    artifacts = {
        "overview": f"{projection_root}/overview.md",
        "baseline": f"{projection_root}/baseline.md",
        "progress": f"{projection_root}/progress.md",
        "acceptance": f"{projection_root}/acceptance.md",
        "revisions": f"{projection_root}/revisions.md",
        "taskBaselines": task_baselines,
        "workItems": work_items,
    }
    return {
        "workspaceOverview": ".layered-delivery/overview.md",
        **artifacts,
    }


def _preview_values(hierarchy: object) -> tuple[
    dict[str, Any],
    dict[str, Any],
    str,
    str,
]:
    normalized = validate_hierarchy_definition(hierarchy)
    hierarchy_value = hierarchy_fingerprint(normalized)
    graph = compile_delivery_graph(
        normalized,
        hierarchy_fingerprint=hierarchy_value,
    )
    return (
        normalized,
        graph,
        hierarchy_value,
        graph_fingerprint(graph),
    )


def preview_hierarchy(
    *,
    root: str,
    hierarchy: object,
    explicit_dogfood: bool = False,
    **_: Any,
) -> dict[str, Any]:
    """Validate and fingerprint a plan without creating controller state."""

    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    normalized, graph, hierarchy_value, graph_value = _preview_values(
        hierarchy
    )
    repository.assert_delivery_requirement_available(normalized)
    return {
        "rootId": normalized["delivery"]["id"],
        "status": "PREVIEW",
        "hierarchyFingerprint": hierarchy_value,
        "graphFingerprint": graph_value,
        "graphSummary": graph_summary(graph),
        "requiredProjectAuthorizations": normalized["delivery"].get(
            "projectScopes",
            [],
        ),
        "controlStateCreated": False,
        "nextAction": (
            "SELECT_AUTOMATIC_EXECUTION_OR_MANUAL_HANDOFF"
        ),
    }


def _assert_exact_project_authorization(
    hierarchy: dict[str, Any],
    authorized_project_ids: list[str] | None,
) -> None:
    required = sorted(
        item["id"]
        for item in hierarchy["delivery"].get("projectScopes", [])
    )
    if (
        not isinstance(authorized_project_ids, list)
        or any(
            not isinstance(item, str) or not item
            for item in authorized_project_ids
        )
        or len(set(authorized_project_ids))
        != len(authorized_project_ids)
    ):
        fail(
            "SCHEDULER_PROJECT_AUTHORIZATION_REQUIRED",
            "authorized_project_ids must contain unique project IDs",
        )
    supplied = sorted(authorized_project_ids)
    if supplied != required:
        fail(
            "SCHEDULER_PROJECT_AUTHORIZATION_REQUIRED",
            "Manual handoff requires exact authorization of every project",
            requiredProjectIds=required,
            suppliedProjectIds=supplied,
            missingProjectIds=sorted(set(required) - set(supplied)),
            unexpectedProjectIds=sorted(set(supplied) - set(required)),
        )


def create_manual_handoff(
    *,
    root: str,
    hierarchy: object,
    expected_hierarchy_fingerprint: str,
    expected_graph_fingerprint: str,
    authorized_project_ids: list[str] | None,
    confirmed: bool,
    confirmed_by: str,
    expected_current_revision: int | None = None,
    continuity_basis: str | None = None,
    revision_reason: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Create a portable handoff bundle without preparing a Graph."""

    if confirmed is not True:
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "Creating a manual handoff requires explicit user confirmation",
        )
    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "confirmed_by must identify the confirming human",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    normalized, graph, hierarchy_value, graph_value = _preview_values(
        hierarchy
    )
    if hierarchy_value != expected_hierarchy_fingerprint:
        fail(
            "SCHEDULER_HANDOFF_PREVIEW_CONFLICT",
            "The confirmed hierarchy differs from the preview",
            expectedHierarchyFingerprint=(
                expected_hierarchy_fingerprint
            ),
            actualHierarchyFingerprint=hierarchy_value,
        )
    if graph_value != expected_graph_fingerprint:
        fail(
            "SCHEDULER_HANDOFF_PREVIEW_CONFLICT",
            "The confirmed Graph differs from the preview",
            expectedGraphFingerprint=expected_graph_fingerprint,
            actualGraphFingerprint=graph_value,
        )
    _assert_exact_project_authorization(
        normalized,
        authorized_project_ids,
    )
    root_id = normalized["delivery"]["id"]
    relative_path = (
        f"{GOVERNANCE_DIRECTORY}/{root_id}/"
        f"handoff-{hierarchy_value[:12]}.md"
    )
    registration = repository.record_manual_handoff(
        normalized,
        graph,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
        authorized_project_ids=list(authorized_project_ids or []),
        expected_current_revision=expected_current_revision,
        continuity_basis=continuity_basis,
        revision_reason=revision_reason,
        confirmed_by=confirmed_by.strip(),
    )
    created_at = registration["recordedAt"]
    content = render_manual_handoff(
        normalized,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
        confirmed_by=confirmed_by.strip(),
        created_at=created_at,
    )
    atomic_write(safe_path(root, relative_path), content)
    return {
        "rootId": root_id,
        "status": "HANDOFF_READY",
        "deliveryRevision": registration["deliveryRevision"],
        "previousRevision": registration["previousRevision"],
        "requirementSnapshotStatus": "FROZEN",
        "hierarchyFingerprint": hierarchy_value,
        "graphFingerprint": graph_value,
        "confirmedBy": confirmed_by.strip(),
        "createdAt": created_at,
        "manualHandoff": {
            "path": relative_path,
            "format": "MARKDOWN",
            "selfContained": True,
        },
        "humanArtifacts": _human_artifacts(
            normalized,
        ),
        "controlStateCreated": True,
        "graphRunCreated": False,
        "workspaceCreated": False,
        "nextAction": "OPEN_FROZEN_BUNDLE_IN_ANY_CLI",
    }


def prepare_hierarchy(
    *,
    root: str,
    hierarchy: object,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Validate and prepare scheduler metadata for human confirmation."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    normalized = validate_hierarchy_definition(hierarchy)
    project_scopes = normalized["delivery"].get("projectScopes")
    verified_projects = verify_delivery_project_scopes(
        workspace_root or root,
        normalized["delivery"],
        preparing=True,
    )
    git_binding = normalized["delivery"].get("gitBinding")
    git_workspace = (
        verify_delivery_git_binding(
            workspace_root or root,
            git_binding,
            preparing=True,
        )
        if project_scopes is None
        else next(
            (
                item.get("gitWorkspace")
                for item in verified_projects
                if item["workspaceRoot"]
                == str(
                    Path(workspace_root or root)
                    .absolute()
                    .resolve(strict=True)
                )
            ),
            None,
        )
    )
    hierarchy_value = hierarchy_fingerprint(normalized)
    graph = compile_delivery_graph(
        normalized,
        hierarchy_fingerprint=hierarchy_value,
    )
    graph_value = graph_fingerprint(graph)
    prepared = repository.prepare(
        normalized,
        graph,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
        workspace_root=workspace_root or root,
    )
    human_artifacts = _human_artifacts(normalized)
    result = {
        **prepared,
        "graphSummary": graph_summary(graph),
        "humanArtifacts": human_artifacts,
        "nextAction": "FREEZE_HIERARCHY_AFTER_USER_CONFIRMATION",
        "requiredProjectAuthorizations": normalized["delivery"].get(
            "projectScopes",
            [],
        ),
    }
    if git_binding is not None:
        result["gitBinding"] = git_binding
    if git_workspace is not None:
        result["gitWorkspace"] = git_workspace
    return result


def prepare_delivery_revision(
    *,
    root: str,
    root_id: str,
    expected_current_revision: int,
    hierarchy: object,
    reason: str,
    continuity_basis: str,
    requested_by: str,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Prepare the next immutable revision of one active Delivery."""

    if not isinstance(reason, str) or not reason.strip():
        fail(
            "SCHEDULER_REVISION_REASON_REQUIRED",
            "A Delivery revision requires a concrete reason",
        )
    if not isinstance(requested_by, str) or not requested_by.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "requested_by must identify the requesting human",
        )
    if continuity_basis not in {
        "USER_EXPLICIT_SAME_DELIVERY",
        "ACTIVE_LOOP_REPLAN",
    }:
        fail(
            "SCHEDULER_REVISION_CONTINUITY_REQUIRED",
            "Revision continuity must come from explicit same-Delivery "
            "intent or an active Loop replan",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    normalized = validate_hierarchy_definition(hierarchy)
    if normalized["delivery"]["id"] != root_id:
        fail(
            "SCHEDULER_DELIVERY_IDENTITY_IMMUTABLE",
            "A Delivery revision must retain the original Delivery ID",
            rootId=root_id,
        )
    verify_delivery_project_scopes(
        workspace_root or root,
        normalized["delivery"],
        preparing=True,
    )
    if normalized["delivery"].get("projectScopes") is None:
        verify_delivery_git_binding(
            workspace_root or root,
            normalized["delivery"].get("gitBinding"),
            preparing=True,
        )
    hierarchy_value = hierarchy_fingerprint(normalized)
    graph = compile_delivery_graph(
        normalized,
        hierarchy_fingerprint=hierarchy_value,
    )
    graph_value = graph_fingerprint(graph)
    prepared = repository.prepare_revision(
        normalized,
        graph,
        root_id=root_id,
        expected_current_revision=expected_current_revision,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
        reason=reason.strip(),
        continuity_basis=continuity_basis,
        requested_by=requested_by.strip(),
        workspace_root=workspace_root or root,
    )
    return {
        **prepared,
        "graphSummary": graph_summary(graph),
        "humanArtifacts": _human_artifacts(normalized),
        "requiredProjectAuthorizations": normalized["delivery"].get(
            "projectScopes",
            [],
        ),
        "nextAction": "FREEZE_HIERARCHY_AFTER_USER_CONFIRMATION",
    }


def delivery_revision_history(
    *,
    root: str,
    root_id: str,
    explicit_dogfood: bool = False,
    **_: Any,
) -> dict[str, Any]:
    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    return repository.revision_history(root_id)


def freeze_hierarchy(
    *,
    root: str,
    root_id: str,
    expected_delivery_revision: int = 1,
    expected_hierarchy_fingerprint: str,
    authorized_project_ids: list[str] | None = None,
    confirmed: bool,
    confirmed_by: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Freeze the graph after explicit human confirmation and start it."""

    if confirmed is not True:
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "Freezing a hierarchy requires explicit user confirmation",
        )
    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "confirmed_by must identify the confirming human",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    result = repository.freeze(
        root_id,
        expected_delivery_revision=expected_delivery_revision,
        expected_hierarchy_fingerprint=(
            expected_hierarchy_fingerprint
        ),
        authorized_project_ids=authorized_project_ids or [],
        confirmed_by=confirmed_by.strip(),
    )
    return {
        **result,
        "confirmedBy": confirmed_by.strip(),
        "nextAction": "READ_GRAPH_FRONTIER",
    }


__all__ = (
    "create_manual_handoff",
    "delivery_revision_history",
    "freeze_hierarchy",
    "prepare_delivery_revision",
    "prepare_hierarchy",
    "preview_hierarchy",
    "workspace_status",
)
