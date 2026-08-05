from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import fail
from .fs_safe import atomic_write, safe_path
from .git_binding import (
    inspect_delivery_git_workspace,
    inspect_frozen_git_workspace_provenance,
    verify_delivery_git_binding,
    verify_delivery_project_scopes,
)
from .graph_model import (
    compile_delivery_graph,
    graph_fingerprint,
    graph_summary,
)
from .interaction_contract import (
    execution_choice_contract,
    manual_receiver_prompt,
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
    base_ref: str | None = None,
    confirmed_dirty_state_fingerprint: str | None = None,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    host_adapter_id: str | None = None,
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
        current_project = (
            None
            if project_scopes is None
            else next(
                (
                    item
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
        current_binding = (
            git_binding
            if current_project is None
            else current_project.get("gitBinding")
        )
        git_workspace = (
            verify_delivery_git_binding(
                workspace_root or root,
                current_binding,
                preparing=False,
            )
            if project_scopes is None
            else (
                current_project.get("gitWorkspace")
                if current_project is not None
                else None
            )
        )
        if git_binding is not None:
            result["gitBinding"] = git_binding
        if git_workspace is not None:
            result["gitWorkspace"] = git_workspace
        if current_binding is not None:
            result.update(
                inspect_frozen_git_workspace_provenance(
                    workspace_root or root,
                    current_binding,
                    host_adapter_id=host_adapter_id,
                )
            )
        if project_scopes is not None:
            result["projectScopes"] = project_scopes
    else:
        discovery = inspect_delivery_git_workspace(
            workspace_root or root,
            base_ref=base_ref,
            confirmed_dirty_state_fingerprint=(
                confirmed_dirty_state_fingerprint
            ),
            host_adapter_id=host_adapter_id,
        )
        if discovery is not None:
            candidate_binding = discovery.get(
                "suggestedGitBinding",
                discovery.get("candidateGitBinding"),
            )
            if isinstance(candidate_binding, dict):
                branch_usage = repository.git_branch_usage(
                    candidate_binding["branchRef"]
                )
                if branch_usage:
                    discovery.pop("suggestedGitBinding", None)
                    discovery.pop("candidateGitBinding", None)
                    terminal_statuses = {
                        "COMPLETED",
                        "CANCELLED",
                        "SUPERSEDED",
                    }
                    active_usage = any(
                        item["status"] not in terminal_statuses
                        for item in branch_usage
                    )
                    discovery["branchAdoption"] = {
                        "state": (
                            "BRANCH_BOUND_TO_OTHER_DELIVERY"
                            if active_usage
                            else "BRANCH_USED_BY_HISTORICAL_DELIVERY"
                        ),
                        "nextAction": "CREATE_DELIVERY_FEATURE_BRANCH",
                        "workingTreeClean": discovery["workingTree"][
                            "clean"
                        ],
                        "conflictingDeliveries": branch_usage,
                    }
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
    host_adapter_id: str | None = None,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Validate a plan and stage artifacts before mode selection."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    normalized, graph, hierarchy_value, graph_value = _preview_values(
        hierarchy
    )
    staged = repository.record_choice_ready(
        normalized,
        graph,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
    )
    artifacts_ready = staged["artifactsReady"]
    choice_ready = artifacts_ready and staged["status"] == "CHOICE_READY"
    next_actions = {
        "HANDOFF_READY": (
            "OPEN_FROZEN_BUNDLE_AND_START_MANUAL_HANDOFF_IN_RECEIVING_CLI"
        ),
        "PREPARED": "FREEZE_PREPARED_HIERARCHY",
        "FROZEN": "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH",
    }
    result = {
        "rootId": normalized["delivery"]["id"],
        "status": staged["status"],
        "hierarchyFingerprint": hierarchy_value,
        "graphFingerprint": graph_value,
        "graphSummary": graph_summary(graph),
        "requiredProjectAuthorizations": normalized["delivery"].get(
            "projectScopes",
            [],
        ),
        "controlStateCreated": staged["controlStateCreated"],
        "artifactsReady": artifacts_ready,
        "nextAction": (
            "PRESENT_HOST_NATIVE_EXECUTION_CHOICE"
            if choice_ready
            else next_actions.get(
                staged["status"],
                "REGENERATE_ARTIFACTS_BEFORE_EXECUTION_CHOICE",
            )
        ),
    }
    if artifacts_ready:
        result["humanArtifacts"] = _human_artifacts(normalized)
    if choice_ready:
        result["executionChoice"] = execution_choice_contract(
            host_adapter_id
        )
    return result


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
    receiver_prompt = manual_receiver_prompt(relative_path)
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
        receiver_prompt=receiver_prompt,
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
            "receiverPrompt": receiver_prompt,
        },
        "humanArtifacts": _human_artifacts(
            normalized,
        ),
        "controlStateCreated": True,
        "graphRunCreated": False,
        "workspaceCreated": False,
        "nextAction": (
            "OPEN_FROZEN_BUNDLE_AND_START_MANUAL_HANDOFF_IN_RECEIVING_CLI"
        ),
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


def start_manual_handoff(
    *,
    root: str,
    root_id: str,
    expected_hierarchy_fingerprint: str,
    expected_graph_fingerprint: str,
    started_by: str,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Bind and start one frozen manual handoff as a governed Graph run."""

    if not isinstance(started_by, str) or not started_by.strip():
        fail(
            "SCHEDULER_MANUAL_START_IDENTITY_REQUIRED",
            "started_by must identify the receiving orchestrator",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    if (
        stored["hierarchyFingerprint"]
        != expected_hierarchy_fingerprint
        or stored["graphFingerprint"] != expected_graph_fingerprint
    ):
        fail(
            "SCHEDULER_MANUAL_HANDOFF_STALE",
            "The receiving CLI fingerprints do not match the frozen handoff",
            rootId=root_id,
            actualHierarchyFingerprint=stored["hierarchyFingerprint"],
            actualGraphFingerprint=stored["graphFingerprint"],
        )
    if stored["status"] == "FROZEN":
        existing_run = repository.run(root_id)
        if existing_run["executionMode"] != "manual":
            fail(
                "SCHEDULER_MANUAL_HANDOFF_START_CONFLICT",
                "This Delivery already has a non-manual Graph run",
                rootId=root_id,
                executionMode=existing_run["executionMode"],
            )
        return {
            **existing_run,
            "graphRunCreated": True,
            "manualStartAlreadyApplied": True,
            "nextAction": "READ_GRAPH_FRONTIER",
        }
    if stored["status"] not in {"HANDOFF_READY", "PREPARED"}:
        fail(
            "SCHEDULER_MANUAL_HANDOFF_NOT_READY",
            "Only a HANDOFF_READY snapshot or its interrupted PREPARED "
            "adoption can start manual TASK execution",
            rootId=root_id,
            status=stored["status"],
        )
    history = repository.revision_history(root_id)
    current = next(
        (
            item
            for item in history["revisions"]
            if item["revision"] == history["currentRevision"]
        ),
        None,
    )
    if (
        current is None
        or current["status"] not in {"HANDOFF_READY", "PREPARED"}
        or not isinstance(current["confirmedBy"], str)
        or not current["confirmedBy"].strip()
    ):
        fail(
            "SCHEDULER_MANUAL_HANDOFF_STATE_INVALID",
            "The frozen manual revision is missing its confirmation record",
            rootId=root_id,
        )
    if stored["status"] == "HANDOFF_READY":
        prepared = prepare_hierarchy(
            root=root,
            hierarchy=stored["hierarchy"],
            workspace_root=workspace_root or root,
            explicit_dogfood=explicit_dogfood,
            now=now,
        )
        if prepared["graphFingerprint"] != expected_graph_fingerprint:
            fail(
                "SCHEDULER_MANUAL_HANDOFF_STALE",
                "The prepared Graph no longer matches the frozen handoff",
                rootId=root_id,
            )
    else:
        delivery = stored["hierarchy"]["delivery"]
        verified_projects = verify_delivery_project_scopes(
            workspace_root or root,
            delivery,
            preparing=False,
        )
        if delivery.get("projectScopes") is None:
            verify_delivery_git_binding(
                workspace_root or root,
                delivery.get("gitBinding"),
                preparing=False,
            )
        elif not verified_projects:
            fail(
                "SCHEDULER_PROJECT_SCOPE_INVALID",
                "The interrupted manual adoption has no verified project",
                rootId=root_id,
            )
    result = repository.freeze_manual_handoff(
        root_id,
        expected_delivery_revision=current["revision"],
        expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
        authorized_project_ids=current["authorizedProjectIds"],
        confirmed_by=current["confirmedBy"].strip(),
        started_by=started_by.strip(),
    )
    return {
        **result,
        "startedBy": started_by.strip(),
        "graphRunCreated": True,
        "manualStartAlreadyApplied": False,
        "nextAction": "READ_GRAPH_FRONTIER",
    }


def select_execution_mode(
    *,
    root: str,
    root_id: str,
    selection: str,
    expected_hierarchy_fingerprint: str,
    expected_graph_fingerprint: str,
    authorized_project_ids: list[str] | None,
    confirmed_by: str,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Apply the controller-owned automatic or manual execution choice."""

    if selection not in {"AUTOMATIC", "MANUAL"}:
        fail(
            "SCHEDULER_EXECUTION_CHOICE_INVALID",
            "selection must be AUTOMATIC or MANUAL",
            selection=selection,
        )
    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "confirmed_by must identify the confirming human",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    if (
        stored["hierarchyFingerprint"]
        != expected_hierarchy_fingerprint
        or stored["graphFingerprint"] != expected_graph_fingerprint
    ):
        fail(
            "SCHEDULER_EXECUTION_CHOICE_STALE",
            "The selected execution choice does not match the generated "
            "baseline",
            rootId=root_id,
            actualHierarchyFingerprint=stored["hierarchyFingerprint"],
            actualGraphFingerprint=stored["graphFingerprint"],
        )
    hierarchy = stored["hierarchy"]
    if selection == "MANUAL":
        if stored["status"] not in {
            "CHOICE_READY",
            "HANDOFF_READY",
        }:
            fail(
                "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
                "Automatic execution has already started for this choice",
                rootId=root_id,
                status=stored["status"],
                selected=selection,
            )
        handoff = create_manual_handoff(
            root=root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                expected_hierarchy_fingerprint
            ),
            expected_graph_fingerprint=expected_graph_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed=True,
            confirmed_by=confirmed_by,
            explicit_dogfood=explicit_dogfood,
            now=now,
        )
        return {
            **handoff,
            "selection": "MANUAL",
            "selectionAlreadyApplied": (
                stored["status"] == "HANDOFF_READY"
            ),
        }

    if stored["status"] == "FROZEN":
        run = repository.run(root_id)
        return {
            **run,
            "selection": "AUTOMATIC",
            "selectionAlreadyApplied": True,
            "automaticDispatchRequested": True,
            "nextAction": "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH",
        }
    if stored["status"] == "HANDOFF_READY":
        fail(
            "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
            "Manual development has already been selected for this choice",
            rootId=root_id,
            status=stored["status"],
            selected=selection,
        )
    if stored["status"] == "CHOICE_READY":
        prepared = prepare_hierarchy(
            root=root,
            hierarchy=hierarchy,
            workspace_root=workspace_root or root,
            explicit_dogfood=explicit_dogfood,
            now=now,
        )
    elif stored["status"] == "PREPARED":
        prepared = {
            "rootId": root_id,
            "deliveryRevision": stored["deliveryRevision"],
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
        }
    else:
        fail(
            "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
            "The Delivery is not waiting for an execution choice",
            rootId=root_id,
            status=stored["status"],
            selected=selection,
        )
    frozen = freeze_hierarchy(
        root=root,
        root_id=root_id,
        expected_delivery_revision=prepared["deliveryRevision"],
        expected_hierarchy_fingerprint=(
            prepared["hierarchyFingerprint"]
        ),
        authorized_project_ids=authorized_project_ids,
        confirmed=True,
        confirmed_by=confirmed_by,
        explicit_dogfood=explicit_dogfood,
        now=now,
    )
    return {
        **frozen,
        "selection": "AUTOMATIC",
        "selectionAlreadyApplied": False,
        "automaticDispatchRequested": True,
        "nextAction": "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH",
    }


__all__ = (
    "create_manual_handoff",
    "delivery_revision_history",
    "freeze_hierarchy",
    "prepare_delivery_revision",
    "prepare_hierarchy",
    "preview_hierarchy",
    "select_execution_mode",
    "start_manual_handoff",
    "workspace_status",
)
