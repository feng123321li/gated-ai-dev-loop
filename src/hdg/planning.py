from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import GatedLoopError, fail
from .fs_safe import atomic_write, safe_path
from .git_binding import (
    enumerate_local_feature_branches,
    git_repository_identity,
    inspect_business_commit_range,
    inspect_delivery_git_workspace,
    inspect_frozen_git_workspace_provenance,
    resolve_branch_binding,
    verify_delivery_git_binding,
    verify_delivery_project_scopes,
)
from .graph_model import (
    compile_delivery_graph,
    graph_fingerprint,
    graph_summary,
)
from .interaction_contract import (
    development_baseline_contract,
    execution_choice_contract,
    manual_receiver_prompt,
)
from .jsonio import fingerprint
from .model_core import (
    hierarchy_fingerprint,
    iter_hierarchy_nodes,
    validate_git_binding,
    validate_hierarchy_definition,
)
from .model_rendering import (
    render_manual_handoff,
    task_baseline_relative_path,
    task_has_database_projection,
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
    now: object = None,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    result = repository.workspace_status(
        root_id=root_id,
        workspace_root=workspace_root,
    )
    if result["status"] == "DELIVERY_SELECTION_REQUIRED":
        for candidate in result.get("candidateDeliveries", []):
            candidate_root_id = candidate.get("rootId")
            if not isinstance(candidate_root_id, str):
                continue
            selection = repository.execution_selection(candidate_root_id)
            if selection is None:
                continue
            try:
                workspace_turn = repository.serial_workspace_turn_state(
                    candidate_root_id
                )
            except GatedLoopError as error:
                if error.code == "SCHEDULER_DELIVERY_WORKSPACE_MISSING":
                    continue
                raise
            if workspace_turn["state"] != "ACQUIRED":
                serial_gate = {
                    "state": workspace_turn["state"],
                    "workspaceTurn": workspace_turn,
                }
                candidate["deliveryStatus"] = candidate["status"]
                candidate["status"] = "QUEUED"
                candidate["deliveryQueue"] = _delivery_queue_marker(
                    serial_gate,
                    candidate_root_id,
                )
        return result
    selected_root_id = result.get("rootId")
    if isinstance(selected_root_id, str):
        if result["status"] == "ARCHIVED":
            return result
        stored = repository.hierarchy(selected_root_id)
        delivery = stored["hierarchy"]["delivery"]
        git_binding = delivery.get("gitBinding")
        project_scopes = delivery.get("projectScopes")
        selection = repository.execution_selection(selected_root_id)
        if selection is not None:
            result["executionSelection"] = selection
        if stored["status"] == "CHOICE_READY" and selection is None:
            interaction = _pending_interaction(
                stored["hierarchy"],
                repository,
                workspace_root or root,
                host_adapter_id,
                expected_hierarchy_fingerprint=stored[
                    "hierarchyFingerprint"
                ],
                expected_graph_fingerprint=stored["graphFingerprint"],
                expected_delivery_revision=stored["deliveryRevision"],
            )
            result["hierarchyFingerprint"] = stored[
                "hierarchyFingerprint"
            ]
            result["graphFingerprint"] = stored["graphFingerprint"]
            _attach_pending_interaction(result, interaction)
            return result
        if selection is None and result["status"] in {
            "ACTIVE",
            "BLOCKED",
            "PAUSED",
            "COMPLETED",
            "CANCELLED",
            "SUPERSEDED",
        }:
            try:
                unselected_workspace_turn = (
                    repository.serial_workspace_turn_state(
                        selected_root_id
                    )
                )
            except GatedLoopError as error:
                if error.code != "SCHEDULER_DELIVERY_WORKSPACE_MISSING":
                    raise
            else:
                serial_gate = _resolve_serial_workspace_gate(
                    repository,
                    workspace_root or root,
                    selected_root_id,
                    unselected_workspace_turn,
                )
                result["workspaceStrategy"] = "CURRENT_WORKSPACE_SERIAL"
                result["workspaceTurn"] = serial_gate["workspaceTurn"]
                if serial_gate["state"] == "RELEASED":
                    result["nextAction"] = (
                        "GRAPH_RUN_ALREADY_TERMINAL"
                        if result["status"] in _SERIAL_TERMINAL_STATUSES
                        else "READ_GRAPH_FRONTIER"
                    )
                    return result
                if serial_gate["state"] == "WAITING_FOR_WORKSPACE_COMMIT":
                    result["nextAction"] = "WAIT_FOR_WORKSPACE_COMMIT"
                    return result
        if selection is not None:
            serial_gate = _resolve_serial_workspace_gate(
                repository,
                workspace_root or root,
                selected_root_id,
                _serial_turn_for_recorded_selection(
                    repository,
                    root_id=selected_root_id,
                    stored=stored,
                    selection=selection,
                    workspace_root=workspace_root or root,
                ),
            )
            result["workspaceStrategy"] = "CURRENT_WORKSPACE_SERIAL"
            result["workspaceTurn"] = serial_gate["workspaceTurn"]
            if serial_gate["state"] == "RELEASED":
                result["nextAction"] = (
                    "GRAPH_RUN_ALREADY_TERMINAL"
                    if result["status"] in _SERIAL_TERMINAL_STATUSES
                    else "READ_GRAPH_FRONTIER"
                )
                return result
            if serial_gate["state"] != "ACQUIRED":
                previous_status = result["status"]
                result["deliveryStatus"] = previous_status
                result["status"] = "QUEUED"
                result["deliveryQueue"] = _delivery_queue_marker(
                    serial_gate,
                    selected_root_id,
                )
                result["automaticDispatchRequested"] = False
                result["nextAction"] = "WAIT_FOR_AUTOMATIC_QUEUE_TURN"
                if git_binding is not None:
                    result["gitBinding"] = git_binding
                result["projectScopes"] = project_scopes or []
                return result
            recorded_preparation = _automatic_serial_workspace_preparation(
                control_root=root,
                workspace_root=workspace_root or root,
                hierarchy=stored["hierarchy"],
            )
            if recorded_preparation is not None:
                result["workspacePreparation"] = recorded_preparation
                result["nextAction"] = recorded_preparation["nextAction"]
                if git_binding is not None:
                    result["gitBinding"] = git_binding
                result["projectScopes"] = project_scopes or []
                return result
        discovery = (
            inspect_delivery_git_workspace(
                workspace_root or root,
                base_ref=(
                    git_binding.get("baseRef")
                    if isinstance(git_binding, dict)
                    else None
                ),
                host_adapter_id=host_adapter_id,
            )
            if stored["status"] == "CHOICE_READY"
            else None
        )
        if (
            isinstance(discovery, dict)
            and discovery.get("workspacePreparation") is not None
        ):
            result.update(discovery)
            if git_binding is not None:
                result["gitBinding"] = git_binding
            result["projectScopes"] = project_scopes or []
            return result
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
            result["verifiedProjectScopes"] = verified_projects
        if selection is not None:
            result["nextAction"] = "RESUME_RECORDED_AUTOMATIC_SELECTION"
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
                    candidate_binding["branchRef"],
                    repository_key=git_repository_identity(
                        workspace_root or root
                    ),
                )
                if branch_usage:
                    discovery.pop("suggestedGitBinding", None)
                    discovery.pop("candidateGitBinding", None)
                    terminal_statuses = {
                        "ARCHIVED",
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
        if task_has_database_projection(definition):
            artifacts["databaseChanges"] = (
                f"{projection_root}/"
                + work_item_projection_relative_path(
                    hierarchy,
                    item_id,
                    "database-changes.md",
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


def _automatic_workspace_requests(
    *,
    control_root: str,
    workspace_root: str,
    hierarchy: dict[str, Any],
) -> list[dict[str, Any]]:
    """Describe the current workspaces required by one Delivery."""

    delivery = hierarchy["delivery"]
    delivery_id = delivery["id"]
    workspace_key = git_repository_identity(workspace_root)
    scopes = delivery.get("projectScopes")
    candidates: list[dict[str, Any]] = []
    if scopes is None:
        binding = delivery.get("gitBinding")
        if isinstance(binding, dict):
            candidates.append(
                {
                    "projectId": delivery_id,
                    "access": "READ_WRITE",
                    "repositoryRoot": str(
                        Path(control_root).absolute().resolve(strict=True)
                    ),
                    "gitBinding": binding,
                }
            )
    else:
        for scope in scopes:
            binding = scope.get("gitBinding")
            if (
                scope["access"] != "READ_WRITE"
                or not isinstance(binding, dict)
            ):
                continue
            candidates.append(
                {
                    "projectId": scope["id"],
                    "access": scope["access"],
                    "repositoryRoot": str(
                        Path(scope["workspaceRoot"]).absolute().resolve(
                            strict=True
                        )
                    ),
                    "gitBinding": binding,
                }
            )

    requests: list[dict[str, Any]] = []
    for candidate in candidates:
        repository_key = git_repository_identity(
            candidate["repositoryRoot"]
        )
        if repository_key is None:
            fail(
                "SCHEDULER_GIT_CHECKOUT_REQUIRED",
                "A Git-bound project scope requires a Git repository",
                projectId=candidate["projectId"],
                workspaceRoot=candidate["repositoryRoot"],
            )
        binding = validate_git_binding(candidate["gitBinding"])
        requests.append(
            {
                **candidate,
                "gitBinding": binding,
                "repositoryKey": repository_key,
                "branchRef": binding["branchRef"],
                "coordinatorWorkspace": repository_key == workspace_key,
            }
        )
    if requests and sum(
        bool(request["coordinatorWorkspace"]) for request in requests
    ) != 1:
        fail(
            "SCHEDULER_PROJECT_SCOPE_INVALID",
            "Git project scopes must identify exactly one coordinator "
            "repository",
            coordinatorProjectIds=[
                request["projectId"]
                for request in requests
                if request["coordinatorWorkspace"]
            ],
        )
    return sorted(requests, key=lambda item: item["projectId"])

def _request_workspace_root(
    request: dict[str, Any],
    workspace_root: str,
) -> str:
    return (
        workspace_root
        if request["coordinatorWorkspace"]
        else request["repositoryRoot"]
    )


def _current_workspace_satisfies_request(
    request: dict[str, Any],
    workspace_root: str,
) -> bool:
    """Return whether the serial current workspace is branch-ready and clean."""

    target_root = _request_workspace_root(request, workspace_root)
    if git_repository_identity(target_root) != request["repositoryKey"]:
        return False
    try:
        verify_delivery_git_binding(
            target_root,
            request["gitBinding"],
            preparing=True,
        )
    except GatedLoopError as error:
        if error.code in {
            "SCHEDULER_GIT_BRANCH_MISMATCH",
            "SCHEDULER_GIT_DETACHED_HEAD",
        }:
            return False
        raise
    discovery = inspect_delivery_git_workspace(target_root)
    working_tree = (
        discovery.get("workingTree", {})
        if isinstance(discovery, dict)
        else {}
    )
    return working_tree.get("clean") is True


def _current_workspace_serial_preparation(
    requests: list[dict[str, Any]],
    workspace_root: str,
) -> dict[str, Any]:
    project_preparations = []
    for request in requests:
        target_root = _request_workspace_root(request, workspace_root)
        discovery = inspect_delivery_git_workspace(target_root)
        working_tree = (
            discovery.get("workingTree", {})
            if isinstance(discovery, dict)
            else {}
        )
        dirty = working_tree.get("clean") is False
        next_action = (
            (
                "RESOLVE_UNMERGED_CHANGES_OR_KEEP_CHANGES_AND_WAIT"
                if working_tree.get("hasUnmergedChanges") is True
                else "HOST_STASH_PREPARE_BRANCH_THEN_RESUME_EXECUTION"
            )
            if dirty
            else "CREATE_OR_SWITCH_CURRENT_WORKSPACE_BRANCH"
        )
        project_preparations.append(
            {
                "projectId": request["projectId"],
                "workspaceRoot": str(
                    Path(target_root).absolute().resolve(strict=True)
                ),
                "repositoryKey": request["repositoryKey"],
                "branchRef": request["branchRef"],
                "gitBinding": request["gitBinding"],
                "state": (
                    "CURRENT_WORKSPACE_DIRTY"
                    if dirty
                    else "CURRENT_WORKSPACE_BRANCH_REQUIRED"
                ),
                "nextAction": next_action,
                "workingTree": working_tree,
            }
        )
    dirty_projects = [
        item
        for item in project_preparations
        if item["state"] == "CURRENT_WORKSPACE_DIRTY"
    ]
    dirty = bool(dirty_projects)
    stash_available = dirty and not any(
        item["workingTree"].get("hasUnmergedChanges") is True
        for item in dirty_projects
    )
    next_action = (
        (
            "HOST_STASH_PREPARE_BRANCH_THEN_RESUME_EXECUTION"
            if stash_available
            else "RESOLVE_CONFLICTS_OR_KEEP_CHANGES_AND_WAIT"
        )
        if dirty
        else "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION"
    )
    result = {
        "state": "CURRENT_WORKSPACE_PREPARATION_REQUIRED",
        "owner": "HOST",
        "strategy": "CURRENT_WORKSPACE_SERIAL",
        "nextAction": next_action,
        "controllerCreatesBranch": False,
        "projectPreparations": project_preparations,
    }
    if dirty:
        expected_projects = [
            {
                "projectId": item["projectId"],
                "workspaceRoot": item["workspaceRoot"],
                "workingTreeStateFingerprint": item["workingTree"][
                    "stateFingerprint"
                ],
            }
            for item in dirty_projects
        ]
        if stash_available:
            result["workspaceChangeHandling"] = {
                "kind": "AUTOMATIC_DIRTY_WORKSPACE_PREPARATION",
                "action": "STASH_AND_RUN",
                "confirmationRequired": False,
                "authorizationSource": "AUTOMATIC_EXECUTION_SELECTION",
                "fallbackAction": "KEEP_CHANGES_AND_WAIT",
                "hostAction": {
                    "label": "暂存现有改动后运行",
                    "description": (
                        "AUTOMATIC 选择已授权宿主机械准备 workspace；宿主"
                        "精确复核工作树指纹，stash 已跟踪、暂存和未跟踪"
                        "业务改动；工作树变干净后创建或切换 Delivery 分支，"
                        "再调用 resume_execution_mode。"
                    ),
                    "owner": "HOST",
                    "controllerExecutesGit": False,
                    "expectedProjects": expected_projects,
                    "stashPolicy": {
                        "includeTracked": True,
                        "includeStaged": True,
                        "includeUntracked": True,
                        "includeIgnored": False,
                        "pathspec": [
                            ".",
                            ":(exclude).layered-delivery",
                            ":(exclude).layered-delivery/**",
                        ],
                        "verifyFingerprintImmediatelyBeforeWrite": True,
                        "requireCleanWorkspaceAfterWrite": True,
                    },
                    "restorePolicy": {
                        "automatic": False,
                        "restoreAfterDeliveryBranchUse": True,
                        "restoreOnOriginalBranch": True,
                        "restoreIndex": True,
                        "retainStashUntilSuccessfulRestore": True,
                    },
                    "nextAction": (
                        "HOST_STASH_PREPARE_BRANCH_THEN_RESUME_EXECUTION"
                    ),
                },
                "preservedUnrelatedChanges": {
                    "supported": False,
                    "reason": "DELIVERY_TURN_MUST_START_CLEAN",
                    "explanation": (
                        "每个 Delivery 使用独立分支，且 turn-start、提交归属与"
                        "验收快照都要求干净边界；脏改动不能跨分支原地保留。"
                    ),
                },
            }
        else:
            result["workspaceChangeHandling"] = {
                "kind": "AUTOMATIC_DIRTY_WORKSPACE_PREPARATION",
                "action": "KEEP_CHANGES_AND_WAIT",
                "confirmationRequired": False,
                "authorizationSource": "AUTOMATIC_EXECUTION_SELECTION",
                "blockedAutomaticAction": "STASH_AND_RUN",
                "blockedReason": "UNMERGED_CHANGES",
                "expectedProjects": expected_projects,
                "nextAction": "RESOLVE_CONFLICTS_OR_WAIT_FOR_CLEAN_WORKSPACE",
                "preservedUnrelatedChanges": {
                    "supported": False,
                    "reason": "DELIVERY_TURN_MUST_START_CLEAN",
                },
            }
    if not dirty or stash_available:
        actions: list[dict[str, Any]] = []
        if dirty:
            actions.append(
                {
                    "action": "STASH_BUSINESS_CHANGES",
                    "projects": expected_projects,
                    "pathspec": [
                        ".",
                        ":(exclude).layered-delivery",
                        ":(exclude).layered-delivery/**",
                    ],
                    "includeUntracked": True,
                    "restoreIndex": True,
                }
            )
        actions.extend(
            [
                {
                    "action": "CREATE_OR_SWITCH_DELIVERY_BRANCH",
                    "projects": [
                        {
                            "projectId": item["projectId"],
                            "workspaceRoot": item["workspaceRoot"],
                            "branchRef": item["branchRef"],
                            "gitBinding": item["gitBinding"],
                        }
                        for item in project_preparations
                    ],
                },
                {
                    "action": "RESUME_EXECUTION_MODE",
                    "tool": "resume_execution_mode",
                },
            ]
        )
        result["automaticHostPreparation"] = {
            "state": "READY",
            "authorizationSource": "AUTOMATIC_EXECUTION_SELECTION",
            "confirmationRequired": False,
            "controllerExecutesGit": False,
            "actions": actions,
        }
    else:
        result["automaticHostPreparation"] = {
            "state": "BLOCKED",
            "authorizationSource": "AUTOMATIC_EXECUTION_SELECTION",
            "confirmationRequired": False,
            "controllerExecutesGit": False,
            "reason": "UNMERGED_CHANGES",
            "nextAction": "RESOLVE_CONFLICTS_OR_WAIT_FOR_CLEAN_WORKSPACE",
        }
    return result


def _automatic_serial_workspace_preparation(
    *,
    control_root: str,
    workspace_root: str,
    hierarchy: dict[str, Any],
) -> dict[str, Any] | None:
    requests = _automatic_workspace_requests(
        control_root=control_root,
        workspace_root=workspace_root,
        hierarchy=hierarchy,
    )
    pending_requests = [
        request
        for request in requests
        if not _current_workspace_satisfies_request(
            request,
            workspace_root,
        )
    ]
    if not pending_requests:
        return None
    return _current_workspace_serial_preparation(
        pending_requests,
        workspace_root,
    )


_SERIAL_TERMINAL_STATUSES = frozenset(
    {"ARCHIVED", "COMPLETED", "CANCELLED", "SUPERSEDED"}
)


def _serial_workspace_release_eligibility(
    repository: SchedulerRepository,
    root_id: str,
) -> dict[str, str] | None:
    """Return the Controller-owned safe boundary for one workspace owner."""

    try:
        run = repository.run(root_id)
    except GatedLoopError as error:
        if error.code == "SCHEDULER_RUN_MISSING":
            return None
        raise
    history = repository.revision_history(root_id)
    if any(
        revision["revision"] > run["deliveryRevision"]
        and revision["status"] == "PREPARED"
        for revision in history["revisions"]
    ):
        return None
    if run["status"] in _SERIAL_TERMINAL_STATUSES:
        return {
            "ownerStatus": run["status"],
            "releaseReason": "RUN_TERMINAL",
        }
    if run["status"] != "ACTIVE":
        return None
    stored = repository.hierarchy(root_id)
    confirmation = next(
        node
        for node in stored["graph"]["nodes"]
        if node["kind"] == "USER_CONFIRMATION"
    )
    confirmation_state = next(
        node
        for node in run["nodes"]
        if node["nodeId"] == confirmation["id"]
    )
    if confirmation_state["status"] != "READY":
        return None
    return {
        "ownerStatus": run["status"],
        "releaseReason": "USER_CONFIRMATION_READY",
    }


def _serial_commit_barrier(
    repository: SchedulerRepository,
    workspace_root: str,
    previous_turn: dict[str, str],
) -> dict[str, Any] | None:
    """Return a fail-closed barrier until the previous Delivery is committed."""

    if repository.workspace_turn_release(previous_turn["rootId"]) is not None:
        return None
    receiver_leases = repository.unexpired_cancelled_receiver_leases(
        previous_turn["rootId"]
    )
    if receiver_leases:
        return {
            "state": "WAITING_FOR_WORKSPACE_COMMIT",
            "ownerRootId": previous_turn["rootId"],
            "ownerStatus": previous_turn["status"],
            "reason": "CANCELLED_RECEIVER_LEASE_ACTIVE",
            "releasePolicy": (
                "OWNER_RECEIVER_RELEASE_COMMIT_CLEAN_AND_SAFE_BOUNDARY"
            ),
            "receiverLeases": receiver_leases,
        }
    previous = repository.hierarchy(previous_turn["rootId"])
    requests = _automatic_workspace_requests(
        control_root=str(repository.root),
        workspace_root=workspace_root,
        hierarchy=previous["hierarchy"],
    )
    turn_start = repository.workspace_turn_start(previous_turn["rootId"])
    start_by_project = {
        item["projectId"]: item
        for item in (
            turn_start.get("projects", [])
            if isinstance(turn_start, dict)
            else []
        )
        if isinstance(item, dict)
        and isinstance(item.get("projectId"), str)
    }
    pending_projects: list[dict[str, Any]] = []
    release_projects: list[dict[str, Any]] = []
    for request in requests:
        target_root = _request_workspace_root(request, workspace_root)
        start = start_by_project.get(request["projectId"])
        if not isinstance(start, dict):
            pending_projects.append(
                {
                    "projectId": request["projectId"],
                    "workspaceRoot": target_root,
                    "state": "TURN_START_EVIDENCE_MISSING",
                    "reason": "TURN_START_COMMIT_UNVERIFIED",
                }
            )
            continue
        try:
            verified = verify_delivery_git_binding(
                target_root,
                request["gitBinding"],
                preparing=False,
            )
            discovery = inspect_delivery_git_workspace(target_root)
        except GatedLoopError as error:
            pending_projects.append(
                {
                    "projectId": request["projectId"],
                    "workspaceRoot": target_root,
                    "state": "WORKSPACE_DRIFTED",
                    "reason": error.code,
                    "details": error.details,
                }
            )
            continue
        working_tree = (
            discovery.get("workingTree", {})
            if isinstance(discovery, dict)
            else {}
        )
        if working_tree.get("clean") is not True:
            pending_projects.append(
                {
                    "projectId": request["projectId"],
                    "workspaceRoot": target_root,
                    "state": "WORKSPACE_DIRTY",
                    "reason": "UNCOMMITTED_CHANGES",
                    "workingTree": working_tree,
                }
            )
            continue
        if verified is None:
            pending_projects.append(
                {
                    "projectId": request["projectId"],
                    "workspaceRoot": target_root,
                    "state": "COMMIT_REQUIRED",
                    "reason": "HEAD_COMMIT_UNVERIFIED",
                    "turnStartCommit": start.get("turnStartCommit"),
                    "headCommit": None,
                    "workingTree": working_tree,
                }
            )
            continue
        commit_range = inspect_business_commit_range(
            target_root,
            str(start.get("turnStartCommit", "")),
            verified["headCommit"],
        )
        if not commit_range["turnStartCommitIsAncestor"]:
            pending_projects.append(
                {
                    "projectId": request["projectId"],
                    "workspaceRoot": target_root,
                    "state": "HISTORY_REWRITTEN",
                    "reason": "TURN_START_COMMIT_NOT_ANCESTOR_OF_HEAD",
                    "turnStartCommit": commit_range["turnStartCommit"],
                    "headCommit": commit_range["headCommit"],
                    "workingTree": working_tree,
                }
            )
            continue
        if not commit_range["businessChangedFiles"]:
            pending_projects.append(
                {
                    "projectId": request["projectId"],
                    "workspaceRoot": target_root,
                    "state": "COMMIT_REQUIRED",
                    "reason": (
                        "HEAD_EQUALS_TURN_START_COMMIT"
                        if commit_range["headCommit"]
                        == commit_range["turnStartCommit"]
                        else "NO_BUSINESS_CHANGES_SINCE_TURN_START"
                    ),
                    "turnStartCommit": commit_range["turnStartCommit"],
                    "headCommit": commit_range["headCommit"],
                    "workingTree": working_tree,
                }
            )
            continue
        release_projects.append(
            {
                "projectId": request["projectId"],
                "workspaceRoot": str(
                    Path(target_root).absolute().resolve(strict=True)
                ),
                "branchRef": request["gitBinding"]["branchRef"],
                "turnStartCommit": commit_range["turnStartCommit"],
                "headCommit": commit_range["headCommit"],
                "businessChangedFiles": commit_range[
                    "businessChangedFiles"
                ],
                "businessTreeFingerprint": commit_range[
                    "businessTreeFingerprint"
                ],
                "workingTreeStateFingerprint": working_tree[
                    "stateFingerprint"
                ],
            }
        )
    if not pending_projects:
        repository.release_serial_workspace_turn(
            previous_turn["rootId"],
            evidence={"projects": release_projects},
        )
        return None
    return {
        "state": "WAITING_FOR_WORKSPACE_COMMIT",
        "ownerRootId": previous_turn["rootId"],
        "ownerStatus": previous_turn["status"],
        "releasePolicy": "OWNER_COMMIT_CLEAN_AND_SAFE_BOUNDARY_THEN_RELEASE",
        "projectBarriers": pending_projects,
    }


def _resolve_serial_workspace_gate(
    repository: SchedulerRepository,
    workspace_root: str,
    root_id: str,
    workspace_turn: dict[str, Any],
) -> dict[str, Any]:
    """Release verified safe owners, otherwise return a stable wait gate."""

    current = workspace_turn
    if current["state"] == "RELEASED":
        return {
            "state": "RELEASED",
            "workspaceTurn": current,
        }
    visited: set[str] = set()
    while True:
        if current["state"] == "ACQUIRED":
            owner_root_id = current["ownerRootId"]
            eligibility = _serial_workspace_release_eligibility(
                repository,
                owner_root_id,
            )
            if eligibility is None:
                return {
                    "state": "ACQUIRED",
                    "workspaceTurn": current,
                }
            commit_barrier = _serial_commit_barrier(
                repository,
                workspace_root,
                {
                    "rootId": owner_root_id,
                    "status": eligibility["ownerStatus"],
                },
            )
            if commit_barrier is not None:
                return {
                    "state": "WAITING_FOR_WORKSPACE_COMMIT",
                    "workspaceTurn": {
                        **current,
                        **commit_barrier,
                    },
                }
            release = repository.workspace_turn_release(owner_root_id)
            return {
                "state": "RELEASED",
                "workspaceTurn": {
                    **current,
                    "state": "RELEASED",
                    "previousOwnerRootId": owner_root_id,
                    "ownerRootId": None,
                    "ownerStatus": None,
                    "position": None,
                    "queueLength": max(current["queueLength"] - 1, 0),
                    "release": release,
                },
            }
        owner_root_id = current["ownerRootId"]
        if owner_root_id in visited:
            return {
                "state": "WAITING_FOR_WORKSPACE_TURN",
                "workspaceTurn": current,
            }
        eligibility = _serial_workspace_release_eligibility(
            repository,
            owner_root_id,
        )
        if eligibility is None:
            return {
                "state": "WAITING_FOR_WORKSPACE_TURN",
                "workspaceTurn": current,
            }
        visited.add(owner_root_id)
        commit_barrier = _serial_commit_barrier(
            repository,
            workspace_root,
            {
                "rootId": owner_root_id,
                "status": eligibility["ownerStatus"],
            },
        )
        if commit_barrier is not None:
            return {
                "state": "WAITING_FOR_WORKSPACE_COMMIT",
                "workspaceTurn": {
                    **current,
                    **commit_barrier,
                },
            }
        current = repository.serial_workspace_turn_state(root_id)


def _serial_turn_for_recorded_selection(
    repository: SchedulerRepository,
    *,
    root_id: str,
    stored: dict[str, Any],
    selection: dict[str, Any],
    workspace_root: str,
) -> dict[str, Any]:
    """Recover a pre-queue AUTOMATIC choice into the current serial queue."""

    try:
        workspace_turn = repository.serial_workspace_turn_state(root_id)
    except GatedLoopError as error:
        if error.code != "SCHEDULER_DELIVERY_WORKSPACE_MISSING":
            raise
        recorded = repository.record_automatic_selection(
            root_id,
            expected_hierarchy_fingerprint=stored[
                "hierarchyFingerprint"
            ],
            expected_graph_fingerprint=stored["graphFingerprint"],
            authorized_project_ids=selection["authorizedProjectIds"],
            confirmed_by=selection["confirmedBy"],
            workspace_key=SchedulerRepository.workspace_key(
                workspace_root
            ),
        )
        workspace_turn = recorded["workspaceTurn"]
    repository.assert_delivery_workspace(root_id, workspace_root)
    return workspace_turn


def _capture_workspace_turn_start(
    requests: list[dict[str, Any]],
    workspace_root: str,
) -> dict[str, Any]:
    projects = []
    for request in requests:
        target_root = _request_workspace_root(request, workspace_root)
        verified = verify_delivery_git_binding(
            target_root,
            request["gitBinding"],
            preparing=False,
        )
        discovery = inspect_delivery_git_workspace(target_root)
        working_tree = (
            discovery.get("workingTree", {})
            if isinstance(discovery, dict)
            else {}
        )
        if working_tree.get("clean") is not True:
            fail(
                "SCHEDULER_WORKSPACE_TURN_DIRTY",
                "A serial workspace turn must start from a clean business "
                "working tree",
                rootId=request.get("rootId"),
                projectId=request["projectId"],
                workspaceRoot=target_root,
                workingTree=working_tree,
            )
        if verified is None:
            fail(
                "SCHEDULER_GIT_CHECKOUT_REQUIRED",
                "A Git-bound serial workspace turn requires a Git checkout",
                projectId=request["projectId"],
            )
        projects.append(
            {
                "projectId": request["projectId"],
                "workspaceRoot": str(
                    Path(target_root).absolute().resolve(strict=True)
                ),
                "workspaceKey": SchedulerRepository.workspace_key(
                    target_root
                ),
                "branchRef": request["gitBinding"]["branchRef"],
                "baseCommit": request["gitBinding"]["baseCommit"],
                "turnStartCommit": verified["headCommit"],
                "workingTreeStateFingerprint": working_tree[
                    "stateFingerprint"
                ],
            }
        )
    return {
        "schemaVersion": 1,
        "strategy": "CURRENT_WORKSPACE_SERIAL",
        "projects": projects,
    }


def _continue_workspace_turn_start(
    requests: list[dict[str, Any]],
    previous_requests: list[dict[str, Any]],
    workspace_root: str,
    previous_turn_start: object,
) -> dict[str, Any] | None:
    """Reuse one Delivery's original turn boundary across a Revision.

    A later Revision supersedes the current Graph run, not the Delivery's
    physical workspace turn. Requiring a new clean boundary here would force
    the user to commit unfinished work from the previous Revision even though
    commits remain separately authorized. Reuse is deliberately exact: a
    project, checkout, branch, or frozen base change falls back to the normal
    clean turn-start capture.
    """

    if not isinstance(previous_turn_start, dict):
        return None
    previous_projects = previous_turn_start.get("projects")
    if not isinstance(previous_projects, list):
        return None
    previous_by_project = {
        item.get("projectId"): item
        for item in previous_projects
        if isinstance(item, dict)
        and isinstance(item.get("projectId"), str)
    }
    previous_request_by_project = {
        request["projectId"]: request
        for request in previous_requests
    }
    project_ids = {request["projectId"] for request in requests}
    if (
        len(previous_by_project) != len(previous_projects)
        or set(previous_by_project) != project_ids
        or set(previous_request_by_project) != project_ids
    ):
        return None

    for request in requests:
        target_root = _request_workspace_root(request, workspace_root)
        resolved_root = str(
            Path(target_root).absolute().resolve(strict=True)
        )
        previous = previous_by_project[request["projectId"]]
        previous_request = previous_request_by_project[
            request["projectId"]
        ]
        if (
            previous_request["gitBinding"] != request["gitBinding"]
            or previous_request["repositoryKey"]
            != request["repositoryKey"]
            or previous_request["coordinatorWorkspace"]
            != request["coordinatorWorkspace"]
            or previous.get("workspaceRoot") != resolved_root
            or previous.get("workspaceKey")
            != SchedulerRepository.workspace_key(target_root)
            or previous.get("branchRef") != request["branchRef"]
            or previous.get("baseCommit")
            != request["gitBinding"]["baseCommit"]
        ):
            return None
        verified = verify_delivery_git_binding(
            target_root,
            request["gitBinding"],
            preparing=False,
        )
        if verified is None:
            fail(
                "SCHEDULER_GIT_CHECKOUT_REQUIRED",
                "A Git-bound serial workspace turn requires a Git checkout",
                projectId=request["projectId"],
            )
        commit_range = inspect_business_commit_range(
            target_root,
            str(previous.get("turnStartCommit", "")),
            verified["headCommit"],
        )
        if not commit_range["turnStartCommitIsAncestor"]:
            fail(
                "SCHEDULER_GIT_TURN_START_INVALID",
                "A Delivery Revision cannot continue after its original "
                "workspace turn history was rewritten",
                projectId=request["projectId"],
                workspaceRoot=resolved_root,
                turnStartCommit=previous.get("turnStartCommit"),
                headCommit=verified["headCommit"],
            )
        discovery = inspect_delivery_git_workspace(target_root)
        working_tree = (
            discovery.get("workingTree", {})
            if isinstance(discovery, dict)
            else {}
        )
        if working_tree.get("hasUnmergedChanges") is True:
            fail(
                "SCHEDULER_WORKSPACE_TURN_DIRTY",
                "A Delivery Revision cannot continue with unresolved Git "
                "conflicts",
                projectId=request["projectId"],
                workspaceRoot=resolved_root,
                workingTree=working_tree,
                nextAction="RESOLVE_CONFLICTS_BEFORE_FREEZING_REVISION",
            )
    return deepcopy(previous_turn_start)


def _workspace_turn_waiting(
    error: GatedLoopError,
) -> dict[str, Any]:
    details = error.details
    return {
        "state": "WAITING_FOR_WORKSPACE_TURN",
        "ownerRootId": details.get("ownerRootId"),
        "ownerStatus": details.get("ownerStatus"),
        "ownerCreatedAt": details.get("ownerCreatedAt"),
        "workspaceKey": details.get("workspaceKey"),
        "queueOrder": details.get("queueOrder"),
        "releasePolicy": (
            "OWNER_COMMIT_CLEAN_AND_SAFE_BOUNDARY_THEN_RELEASE"
        ),
    }


def _delivery_queue_marker(
    serial_gate: dict[str, Any],
    root_id: str,
) -> dict[str, Any]:
    """Project a persisted serial turn as an automatic Delivery queue."""

    workspace_turn = serial_gate["workspaceTurn"]
    return {
        "state": "QUEUED",
        "position": workspace_turn["position"],
        "queueLength": workspace_turn["queueLength"],
        "ownerRootId": workspace_turn["ownerRootId"],
        "ownerStatus": workspace_turn["ownerStatus"],
        "continuation": {
            "automatic": True,
            "tool": "resume_execution_mode",
            "rootId": root_id,
            "confirmationRequired": False,
            "trigger": "OWNER_SAFE_BOUNDARY_COMMIT_CLEAN_AND_RELEASED",
        },
    }



def _verify_frozen_delivery_workspace(
    stored: dict[str, Any],
    workspace_root: str,
) -> list[dict[str, Any]]:
    """Recheck the immutable project/Git scopes before idempotent reuse."""

    delivery = stored["hierarchy"]["delivery"]
    writable_git_scopes: list[dict[str, str]] = []
    verified_projects = verify_delivery_project_scopes(
        workspace_root,
        delivery,
        preparing=False,
    )
    if delivery.get("projectScopes") is None:
        verified_git = verify_delivery_git_binding(
            workspace_root,
            delivery.get("gitBinding"),
            preparing=False,
        )
        if verified_git is not None:
            writable_git_scopes.append(
                {
                    "projectId": delivery["id"],
                    "workspaceRoot": workspace_root,
                }
            )
    elif not verified_projects:
        fail(
            "SCHEDULER_PROJECT_SCOPE_INVALID",
            "The frozen Delivery has no verified project scope",
            rootId=stored["rootId"],
        )
    else:
        writable_git_scopes.extend(
            {
                "projectId": project["id"],
                "workspaceRoot": project["workspaceRoot"],
            }
            for project in verified_projects
            if project["access"] == "READ_WRITE"
            and isinstance(project.get("gitWorkspace"), dict)
        )
    for project in writable_git_scopes:
        discovery = inspect_delivery_git_workspace(
            project["workspaceRoot"]
        )
        working_tree = (
            discovery.get("workingTree", {})
            if isinstance(discovery, dict)
            else {}
        )
        if working_tree.get("clean") is not True:
            fail(
                "SCHEDULER_WORKSPACE_TURN_DIRTY",
                "A frozen Delivery cannot be redispatched while its "
                "business working tree contains unfinished changes",
                rootId=stored["rootId"],
                projectId=project["projectId"],
                workspaceRoot=project["workspaceRoot"],
                workingTree=working_tree,
                nextAction="REVIEW_OR_COMMIT_CURRENT_DELIVERY_CHANGES",
            )
    return verified_projects


def _frozen_serial_workspace_gate(
    repository: SchedulerRepository,
    *,
    stored: dict[str, Any],
    workspace_root: str,
) -> dict[str, Any]:
    """Recheck physical binding and serial ownership for a frozen Run."""

    repository.assert_delivery_workspace(
        stored["rootId"],
        workspace_root,
    )
    return _resolve_serial_workspace_gate(
        repository,
        workspace_root,
        stored["rootId"],
        repository.serial_workspace_turn_state(stored["rootId"]),
    )


def _unbound_manual_serial_workspace_gate(
    repository: SchedulerRepository,
    *,
    root_id: str,
    workspace_root: str,
) -> dict[str, Any]:
    """Release predecessors before an unbound manual handoff switches Git.

    A HANDOFF_READY Delivery cannot join the persisted queue until its frozen
    branch is current and prepare succeeds. Releasing a predecessor at a safe
    terminal or final-confirmation boundary must happen before that branch
    switch, otherwise its commit evidence can no longer be verified in the
    shared checkout.
    """

    while True:
        turns = [
            turn
            for turn in repository.serial_workspace_turns(
                workspace_root,
                include_terminal=True,
            )
            if turn["rootId"] != root_id
            and repository.workspace_turn_release(turn["rootId"]) is None
        ]
        if not turns:
            workspace_key = SchedulerRepository.workspace_key(
                workspace_root
            )
            return {
                "state": "ACQUIRED",
                "workspaceTurn": {
                    "state": "ACQUIRED",
                    "strategy": "CURRENT_WORKSPACE_SERIAL",
                    "workspaceKey": workspace_key,
                    "ownerRootId": root_id,
                    "ownerStatus": "HANDOFF_READY",
                    "requestedRootId": root_id,
                    "position": 1,
                    "queueLength": 1,
                    "releasePolicy": (
                        "OWNER_COMMIT_CLEAN_AND_SAFE_BOUNDARY_THEN_RELEASE"
                    ),
                },
            }
        owner = turns[0]
        workspace_turn = {
            "state": "WAITING_FOR_WORKSPACE_TURN",
            "strategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceKey": owner["workspaceKey"],
            "ownerRootId": owner["rootId"],
            "ownerStatus": owner["status"],
            "requestedRootId": root_id,
            "position": len(turns) + 1,
            "queueLength": len(turns) + 1,
            "releasePolicy": (
                "OWNER_COMMIT_CLEAN_AND_SAFE_BOUNDARY_THEN_RELEASE"
            ),
        }
        eligibility = _serial_workspace_release_eligibility(
            repository,
            owner["rootId"],
        )
        if eligibility is None:
            return {
                "state": "WAITING_FOR_WORKSPACE_TURN",
                "workspaceTurn": workspace_turn,
            }
        commit_barrier = _serial_commit_barrier(
            repository,
            workspace_root,
            {
                "rootId": owner["rootId"],
                "status": eligibility["ownerStatus"],
            },
        )
        if commit_barrier is not None:
            return {
                "state": "WAITING_FOR_WORKSPACE_COMMIT",
                "workspaceTurn": {
                    **workspace_turn,
                    **commit_barrier,
                },
            }


def _manual_serial_workspace_gate(
    repository: SchedulerRepository,
    *,
    stored: dict[str, Any],
    workspace_root: str,
) -> dict[str, Any]:
    """Resolve the same serial gate for bound and unbound manual starts."""

    repository.assert_delivery_workspace(
        stored["rootId"],
        workspace_root,
        allow_unbound_manual=True,
    )
    try:
        workspace_turn = repository.serial_workspace_turn_state(
            stored["rootId"]
        )
    except GatedLoopError as error:
        if error.code != "SCHEDULER_DELIVERY_WORKSPACE_MISSING":
            raise
        return _unbound_manual_serial_workspace_gate(
            repository,
            root_id=stored["rootId"],
            workspace_root=workspace_root,
        )
    return _resolve_serial_workspace_gate(
        repository,
        workspace_root,
        stored["rootId"],
        workspace_turn,
    )


def _manual_workspace_waiting_result(
    *,
    stored: dict[str, Any],
    started_by: str,
    serial_gate: dict[str, Any],
    already_applied: bool,
) -> dict[str, Any]:
    waiting_for_commit = (
        serial_gate["state"] == "WAITING_FOR_WORKSPACE_COMMIT"
    )
    return {
        "rootId": stored["rootId"],
        "status": serial_gate["state"],
        "deliveryStatus": stored["status"],
        "hierarchyFingerprint": stored["hierarchyFingerprint"],
        "graphFingerprint": stored["graphFingerprint"],
        "startedBy": started_by,
        "graphRunCreated": False,
        "manualStartState": serial_gate["state"],
        "manualStartAlreadyApplied": already_applied,
        "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
        "workspaceTurn": serial_gate["workspaceTurn"],
        "nextAction": (
            "WAIT_FOR_WORKSPACE_COMMIT"
            if waiting_for_commit
            else "WAIT_FOR_WORKSPACE_TURN"
        ),
    }


def _frozen_automatic_result(
    repository: SchedulerRepository,
    *,
    stored: dict[str, Any],
    workspace_root: str,
) -> dict[str, Any]:
    """Return a frozen automatic Run only after every runtime guard passes."""

    run = repository.run(stored["rootId"])
    if run["executionMode"] != "active":
        fail(
            "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
            "This Delivery already has a non-automatic Graph run",
            rootId=stored["rootId"],
            executionMode=run["executionMode"],
        )
    serial_gate = _frozen_serial_workspace_gate(
        repository,
        stored=stored,
        workspace_root=workspace_root,
    )
    terminal = run["status"] in _SERIAL_TERMINAL_STATUSES
    if serial_gate["state"] == "RELEASED":
        return {
            **run,
            "selection": "AUTOMATIC",
            "selectionAlreadyApplied": True,
            "confirmationRequired": False,
            "automaticDispatchRequested": False,
            "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceTurn": serial_gate["workspaceTurn"],
            "nextAction": (
                "GRAPH_RUN_ALREADY_TERMINAL"
                if terminal
                else "READ_GRAPH_FRONTIER"
            ),
        }
    if serial_gate["state"] != "ACQUIRED":
        return {
            "rootId": stored["rootId"],
            "status": "QUEUED",
            "deliveryStatus": stored["status"],
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
            "graphFingerprint": stored["graphFingerprint"],
            "selection": "AUTOMATIC",
            "selectionRecorded": True,
            "selectionAlreadyApplied": True,
            "confirmationRequired": False,
            "automaticDispatchRequested": False,
            "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceTurn": serial_gate["workspaceTurn"],
            "deliveryQueue": _delivery_queue_marker(
                serial_gate,
                stored["rootId"],
            ),
            "selectionContinuation": {
                "tool": "resume_execution_mode",
                "confirmationRequired": False,
                "selectionPreserved": True,
            },
            "nextAction": "WAIT_FOR_AUTOMATIC_QUEUE_TURN",
        }
    _verify_frozen_delivery_workspace(stored, workspace_root)
    return {
        **run,
        "selection": "AUTOMATIC",
        "selectionAlreadyApplied": True,
        "confirmationRequired": False,
        "automaticDispatchRequested": not terminal,
        "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
        "workspaceTurn": serial_gate["workspaceTurn"],
        "nextAction": (
            "GRAPH_RUN_ALREADY_TERMINAL"
            if terminal
            else "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH"
        ),
    }


def _inject_remembered_baseline(
    normalized: dict[str, Any],
    repository: SchedulerRepository,
    root_id: str,
) -> bool:
    """Inject a remembered development baseline when the hierarchy omits gitBinding.

    Closes the carry-forward gap: a one-time baseline choice (stored in
    ``delivery_preferences``) is re-supplied to previews, revisions and prepares
    so the Controller never re-asks and the binding survives across revisions.
    Returns True when a binding was injected.
    """

    delivery = normalized.get("delivery")
    if not isinstance(delivery, dict) or delivery.get("gitBinding") is not None:
        return False
    preference = repository.development_preference(root_id)
    if preference is None:
        return False
    delivery["gitBinding"] = validate_git_binding({
        "branchRef": preference["branchRef"],
        "baseRef": preference["baseRef"],
        "baseCommit": preference["baseCommit"],
        "integrationTarget": preference["integrationTarget"],
    })
    return True


def _assert_project_baselines_complete(
    hierarchy: dict[str, Any],
    workspace_root: str,
) -> None:
    """Reject an incomplete multi-repository Git baseline early.

    The current selector intentionally resolves one repository.  A hierarchy
    spanning multiple Git repositories must therefore provide every scoped
    binding explicitly instead of reaching execution-mode selection and
    failing later during prepare/start.
    """

    scopes = hierarchy["delivery"].get("projectScopes")
    if scopes is None:
        return
    git_scopes: list[dict[str, Any]] = []
    for scope in scopes:
        try:
            scope_root = Path(scope["workspaceRoot"]).resolve(strict=True)
        except (FileNotFoundError, OSError):
            # The canonical project-scope verifier owns missing-path errors.
            continue
        if (scope_root / ".git").exists():
            git_scopes.append(scope)
    if len(git_scopes) <= 1:
        return
    incomplete = sorted(
        scope["id"]
        for scope in git_scopes
        if scope.get("gitBinding") is None
    )
    if incomplete:
        fail(
            "SCHEDULER_PROJECT_BASELINE_INCOMPLETE",
            "A multi-repository Delivery must provide an immutable Git "
            "binding for every Git project before execution-mode selection",
            projectIds=incomplete,
            workspaceRoot=str(
                Path(workspace_root).absolute().resolve(strict=True)
            ),
            nextAction="PROVIDE_COMPLETE_PROJECT_GIT_BINDINGS",
        )


def _baseline_discovery(
    normalized: dict[str, Any],
    repository: SchedulerRepository,
    workspace_root: str,
    host_adapter_id: str | None,
    *,
    expected_hierarchy_fingerprint: str,
    expected_graph_fingerprint: str,
    expected_delivery_revision: int,
    interaction_context: str,
) -> dict[str, Any] | None:
    """Discover one Git baseline context without hiding inspection errors."""

    discovery = inspect_delivery_git_workspace(
        workspace_root,
        host_adapter_id=host_adapter_id,
    )
    if discovery is None:
        return None
    candidates = enumerate_local_feature_branches(workspace_root)
    root_id = normalized["delivery"]["id"]
    for branch in candidates:
        usage = repository.git_branch_usage(
            branch["branchRef"],
            repository_key=git_repository_identity(workspace_root),
        )
        branch["inUseBy"] = [
            item["rootId"] for item in usage if item["rootId"] != root_id
        ]
    adoption_state = discovery.get("branchAdoption", {}).get("state")
    default_branch_ref = None
    if adoption_state in {
        "READY",
        "READY_WITH_CONFIRMED_CHANGES",
        "DIRTY_CONFIRMATION_REQUIRED",
    }:
        default_branch_ref = discovery.get("gitWorkspace", {}).get(
            "branchRef"
        )
    git_workspace = discovery.get("gitWorkspace", {})
    provenance = discovery.get("workspaceProvenance", {})
    working_tree = discovery.get("workingTree", {})
    current_branch = git_workspace.get("branchRef")
    stacked_base = None
    if (
        interaction_context == "INITIAL_DELIVERY"
        and git_workspace.get("role") == "UNBOUND_BRANCH"
        and isinstance(current_branch, str)
        and current_branch
        and current_branch not in {"main", "master"}
        and current_branch != provenance.get("baseRef")
        and working_tree.get("clean", False)
    ):
        stacked_base = {
            "branchRef": current_branch,
            "headCommit": git_workspace["headCommit"],
        }
        default_branch_ref = None
    context_value = fingerprint(
        {
            "rootId": root_id,
            "deliveryRevision": expected_delivery_revision,
            "hierarchyFingerprint": expected_hierarchy_fingerprint,
            "graphFingerprint": expected_graph_fingerprint,
            "workspaceRoot": str(
                Path(workspace_root).absolute().resolve(strict=True)
            ),
            "frozenGitBinding": normalized["delivery"].get("gitBinding"),
            "gitWorkspace": discovery.get("gitWorkspace"),
            "workingTree": discovery.get("workingTree"),
            "workspacePreparation": discovery.get("workspacePreparation"),
            "branchAdoption": discovery.get("branchAdoption"),
            "candidates": candidates,
            "stackedBase": stacked_base,
            "interactionContext": interaction_context,
        }
    )
    return {
        "discovery": discovery,
        "candidateBranches": candidates,
        "defaultBranchRef": default_branch_ref,
        "stackedBase": stacked_base,
        "baselineContextFingerprint": context_value,
    }


def _pending_interaction(
    normalized: dict[str, Any],
    repository: SchedulerRepository,
    workspace_root: str,
    host_adapter_id: str | None,
    *,
    expected_hierarchy_fingerprint: str,
    expected_graph_fingerprint: str,
    expected_delivery_revision: int,
    interaction_context: str = "INITIAL_DELIVERY",
) -> dict[str, Any]:
    """Resolve the single Controller-owned interaction for CHOICE_READY."""

    _assert_project_baselines_complete(normalized, workspace_root)
    if normalized["delivery"].get("gitBinding") is None:
        context = _baseline_discovery(
            normalized,
            repository,
            workspace_root,
            host_adapter_id,
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            expected_graph_fingerprint=expected_graph_fingerprint,
            expected_delivery_revision=expected_delivery_revision,
            interaction_context=interaction_context,
        )
        if context is not None:
            return development_baseline_contract(
                host_adapter_id,
                git_binding=None,
                candidate_branches=context["candidateBranches"],
                default_branch_ref=context["defaultBranchRef"],
                expected_hierarchy_fingerprint=(
                    expected_hierarchy_fingerprint
                ),
                expected_graph_fingerprint=expected_graph_fingerprint,
                expected_delivery_revision=expected_delivery_revision,
                baseline_context_fingerprint=context[
                    "baselineContextFingerprint"
                ],
                interaction_context=interaction_context,
                working_tree=context["discovery"].get("workingTree"),
                stacked_base=context["stackedBase"],
            )
    return execution_choice_contract(
        host_adapter_id,
        git_binding=normalized["delivery"].get("gitBinding"),
    )


def _attach_pending_interaction(
    result: dict[str, Any],
    interaction: dict[str, Any],
) -> None:
    result["pendingInteraction"] = interaction
    if interaction["kind"] == "DEVELOPMENT_BASELINE":
        result["developmentBaseline"] = interaction
        result["nextAction"] = "PRESENT_HOST_NATIVE_BASELINE_CHOICE"
    else:
        result["executionChoice"] = interaction
        result["nextAction"] = "PRESENT_HOST_NATIVE_EXECUTION_CHOICE"


def preview_hierarchy(
    *,
    root: str,
    hierarchy: object,
    workspace_root: str | None = None,
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
    root_id = normalized["delivery"]["id"]
    if _inject_remembered_baseline(normalized, repository, root_id):
        hierarchy_value = hierarchy_fingerprint(normalized)
        graph = compile_delivery_graph(
            normalized, hierarchy_fingerprint=hierarchy_value
        )
        graph_value = graph_fingerprint(graph)
    _assert_project_baselines_complete(
        normalized,
        workspace_root or root,
    )
    staged = repository.record_choice_ready(
        normalized,
        graph,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
    )
    artifacts_ready = staged["artifactsReady"]
    recorded_selection = repository.execution_selection(root_id)
    choice_ready = (
        artifacts_ready
        and staged["status"] == "CHOICE_READY"
        and recorded_selection is None
    )
    next_actions = {
        "HANDOFF_READY": (
            "OPEN_FROZEN_BUNDLE_AND_START_MANUAL_HANDOFF_IN_RECEIVING_CLI"
        ),
        "PREPARED": "FREEZE_PREPARED_HIERARCHY",
        "FROZEN": "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH",
        "ABANDONED": "DELIVERY_ABANDONED_NO_FURTHER_ACTION",
    }
    result = {
        "rootId": root_id,
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
            "RESUME_RECORDED_AUTOMATIC_SELECTION_IN_READY_WORKSPACE"
            if recorded_selection is not None
            else next_actions.get(
                staged["status"],
                "REGENERATE_ARTIFACTS_BEFORE_EXECUTION_CHOICE",
            )
        ),
    }
    if artifacts_ready:
        result["humanArtifacts"] = _human_artifacts(normalized)
    if choice_ready:
        interaction = _pending_interaction(
            normalized,
            repository,
            workspace_root or root,
            host_adapter_id,
            expected_hierarchy_fingerprint=hierarchy_value,
            expected_graph_fingerprint=graph_value,
            expected_delivery_revision=staged["deliveryRevision"],
        )
        _attach_pending_interaction(result, interaction)
    if recorded_selection is not None:
        result["executionSelection"] = recorded_selection
    return result


def confirm_development_baseline(
    *,
    root: str,
    root_id: str,
    selection: str,
    expected_hierarchy_fingerprint: str,
    confirmed_by: str,
    branch_name: str | None = None,
    expected_graph_fingerprint: str | None = None,
    expected_delivery_revision: int | None = None,
    baseline_context_fingerprint: str | None = None,
    confirmed_dirty_state_fingerprint: str | None = None,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    host_adapter_id: str | None = None,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Apply one DEVELOPMENT_BASELINE option and re-stage the hierarchy.

    Persists the per-Delivery preference, computes the Git binding read-only,
    re-stages the hierarchy with the binding frozen in (via the idempotent
    ``record_choice_ready`` path), and returns the updated fingerprint plus the
    ``executionChoice``. The Controller performs no Git writes: a
    ``NEW_FROM_MAINLINE`` pins ``baseCommit`` to the current mainline HEAD.
    ``NEW_FROM_CURRENT_BRANCH`` pins it to the clean current feature HEAD and
    makes that parent feature both baseRef and integrationTarget. The host
    creates either branch during workspace preparation.
    """

    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "confirmed_by must identify the confirming human",
        )
    if not isinstance(selection, str) or not selection.strip():
        fail(
            "SCHEDULER_BASELINE_CHOICE_INVALID",
            "selection must be a local branch_ref, NEW_FROM_MAINLINE, or "
            "NEW_FROM_CURRENT_BRANCH",
            selection=selection,
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    if stored["hierarchyFingerprint"] != expected_hierarchy_fingerprint:
        fail(
            "SCHEDULER_BASELINE_CHOICE_STALE",
            "The baseline choice does not match the generated hierarchy",
            rootId=root_id,
            actualHierarchyFingerprint=stored["hierarchyFingerprint"],
        )
    if stored["status"] not in {"CHOICE_READY", "HANDOFF_READY"}:
        fail(
            "SCHEDULER_BASELINE_CHOICE_CONFLICT",
            "A development baseline can only be confirmed while the Delivery "
            "waits for an execution choice or a blocked manual start",
            rootId=root_id,
            status=stored["status"],
        )
    manual_reconfirmation = stored["status"] == "HANDOFF_READY"
    if not manual_reconfirmation and repository.execution_selection(root_id) is not None:
        fail(
            "SCHEDULER_BASELINE_CHOICE_CONFLICT",
            "An execution mode has already been selected for this Delivery",
            rootId=root_id,
        )
    if manual_reconfirmation:
        if (
            expected_delivery_revision != stored["deliveryRevision"]
            or expected_graph_fingerprint != stored["graphFingerprint"]
            or not isinstance(baseline_context_fingerprint, str)
            or not baseline_context_fingerprint
        ):
            fail(
                "SCHEDULER_MANUAL_BASELINE_CONTEXT_STALE",
                "Manual baseline reconfirmation must use the exact revision, "
                "dual fingerprints, and Git context that were presented",
                rootId=root_id,
                actualDeliveryRevision=stored["deliveryRevision"],
                actualGraphFingerprint=stored["graphFingerprint"],
            )
    workspace = workspace_root or root
    context = _baseline_discovery(
        stored["hierarchy"],
        repository,
        workspace,
        host_adapter_id,
        expected_hierarchy_fingerprint=stored["hierarchyFingerprint"],
        expected_graph_fingerprint=stored["graphFingerprint"],
        expected_delivery_revision=stored["deliveryRevision"],
        interaction_context=(
            "MANUAL_HANDOFF_START"
            if manual_reconfirmation
            else "INITIAL_DELIVERY"
        ),
    )
    if context is None:
        fail(
            "SCHEDULER_GIT_CHECKOUT_REQUIRED",
            "A development baseline can only be confirmed in a Git workspace",
            rootId=root_id,
        )
    if (
        baseline_context_fingerprint is not None
        and baseline_context_fingerprint
        != context["baselineContextFingerprint"]
    ):
        fail(
            (
                "SCHEDULER_MANUAL_BASELINE_CONTEXT_STALE"
                if manual_reconfirmation
                else "SCHEDULER_BASELINE_CHOICE_STALE"
            ),
            "The Git baseline context changed after it was presented",
            rootId=root_id,
            expectedBaselineContextFingerprint=(
                baseline_context_fingerprint
            ),
            actualBaselineContextFingerprint=context[
                "baselineContextFingerprint"
            ],
        )
    working_tree = context["discovery"].get("workingTree", {})
    current_branch = context["discovery"].get("gitWorkspace", {}).get(
        "branchRef"
    )
    dirty_current_branch_adoption = (
        manual_reconfirmation
        or selection.strip() == current_branch
        or selection == "NEW_FROM_CURRENT_BRANCH"
    )
    if (
        not working_tree.get("clean", False)
        and dirty_current_branch_adoption
    ):
        actual_dirty = working_tree.get("stateFingerprint")
        if confirmed_dirty_state_fingerprint != actual_dirty:
            fail(
                "SCHEDULER_GIT_DIRTY_CONFIRMATION_REQUIRED",
                "Adopting the current dirty branch requires all current "
                "workspace changes to be explicitly attributed to this "
                "Delivery using the presented state fingerprint",
                dirtyStateFingerprint=actual_dirty,
            )
    if selection in {"NEW_FROM_MAINLINE", "NEW_FROM_CURRENT_BRANCH"}:
        stacked_base = context.get("stackedBase")
        if (
            not isinstance(branch_name, str)
            or not branch_name.strip()
            or branch_name.strip() in {"main", "master"}
            or (
                selection == "NEW_FROM_CURRENT_BRANCH"
                and (
                    stacked_base is None
                    or branch_name.strip() == stacked_base["branchRef"]
                )
            )
        ):
            fail(
                "SCHEDULER_BASELINE_CHOICE_INVALID",
                f"{selection} requires a distinct new Delivery branch name",
                branchName=branch_name,
            )
        chosen_branch = branch_name.strip()
        available = {
            item["branchRef"] for item in context["candidateBranches"]
        }
        if (
            selection == "NEW_FROM_CURRENT_BRANCH"
            and chosen_branch in available
        ):
            fail(
                "SCHEDULER_BASELINE_CHOICE_INVALID",
                "NEW_FROM_CURRENT_BRANCH requires a branch name that does "
                "not already exist",
                branchName=chosen_branch,
            )
        source = selection
        base_ref = (
            stacked_base["branchRef"]
            if selection == "NEW_FROM_CURRENT_BRANCH"
            else None
        )
    else:
        chosen_branch = selection.strip()
        available = {
            item["branchRef"] for item in context["candidateBranches"]
        }
        if chosen_branch not in available:
            fail(
                "SCHEDULER_BASELINE_CHOICE_INVALID",
                "The selected branch is not an available local feature branch",
                selection=chosen_branch,
            )
        source = "LOCAL_BRANCH"
        base_ref = None
    binding = resolve_branch_binding(
        workspace,
        branch_ref=chosen_branch,
        base_ref=base_ref,
    )
    terminal_statuses = {
        "ARCHIVED",
        "COMPLETED",
        "CANCELLED",
        "SUPERSEDED",
    }
    conflicting = [
        item
        for item in repository.git_branch_usage(
            binding["branchRef"],
            repository_key=git_repository_identity(str(workspace)),
        )
        if item["rootId"] != root_id
        and item["status"] not in terminal_statuses
    ]
    if conflicting:
        fail(
            "SCHEDULER_BASELINE_BRANCH_IN_USE",
            "The selected branch is already bound to another active Delivery",
            branchRef=binding["branchRef"],
            conflictingDeliveries=conflicting,
        )
    if manual_reconfirmation:
        hierarchy = deepcopy(stored["hierarchy"])
        previous_binding = hierarchy["delivery"].get("gitBinding")
        if binding == previous_binding:
            preference = repository.record_development_preference(
                root_id,
                binding=binding,
                source=source,
                chosen_by=confirmed_by.strip(),
            )
            return {
                "rootId": root_id,
                "status": "HANDOFF_READY",
                "deliveryRevision": stored["deliveryRevision"],
                "hierarchyFingerprint": stored["hierarchyFingerprint"],
                "graphFingerprint": stored["graphFingerprint"],
                "developmentBaselineConfirmed": preference,
                "baselineConfirmationAlreadyApplied": False,
                "graphRunCreated": False,
                "nextAction": (
                    "RESTORE_CONFIRMED_BRANCH_THEN_RETRY_MANUAL_START"
                ),
            }
        hierarchy["delivery"]["gitBinding"] = binding
        normalized = validate_hierarchy_definition(hierarchy)
        hierarchy_value = hierarchy_fingerprint(normalized)
        graph = compile_delivery_graph(
            normalized,
            hierarchy_fingerprint=hierarchy_value,
        )
        graph_value = graph_fingerprint(graph)
        history = repository.revision_history(root_id)
        current = next(
            item
            for item in history["revisions"]
            if item["revision"] == stored["deliveryRevision"]
        )
        handoff = create_manual_handoff(
            root=root,
            hierarchy=normalized,
            expected_hierarchy_fingerprint=hierarchy_value,
            expected_graph_fingerprint=graph_value,
            authorized_project_ids=current["authorizedProjectIds"],
            confirmed=True,
            confirmed_by=confirmed_by.strip(),
            expected_current_revision=stored["deliveryRevision"],
            continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
            revision_reason=(
                "手动交接启动前重新确认实际开发基线。"
            ),
            explicit_dogfood=explicit_dogfood,
            now=now,
        )
        preference = repository.record_development_preference(
            root_id,
            binding=binding,
            source=source,
            chosen_by=confirmed_by.strip(),
        )
        return {
            **handoff,
            "developmentBaselineConfirmed": preference,
            "baselineConfirmationAlreadyApplied": False,
            "nextAction": "START_MANUAL_HANDOFF_WITH_UPDATED_FINGERPRINTS",
        }

    hierarchy = deepcopy(stored["hierarchy"])
    hierarchy["delivery"]["gitBinding"] = binding
    normalized = validate_hierarchy_definition(hierarchy)
    hierarchy_value = hierarchy_fingerprint(normalized)
    graph = compile_delivery_graph(
        normalized, hierarchy_fingerprint=hierarchy_value
    )
    graph_value = graph_fingerprint(graph)
    repository.record_choice_ready(
        normalized,
        graph,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
    )
    preference = repository.record_development_preference(
        root_id,
        binding=binding,
        source=source,
        chosen_by=confirmed_by.strip(),
    )
    result = {
        "rootId": root_id,
        "status": "CHOICE_READY",
        "hierarchyFingerprint": hierarchy_value,
        "graphFingerprint": graph_value,
        "developmentBaselineConfirmed": preference,
    }
    _attach_pending_interaction(
        result,
        execution_choice_contract(host_adapter_id, git_binding=binding),
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
    receiver_prompt = manual_receiver_prompt(
        relative_path,
        normalized["root"]["skillHints"],
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
    _inject_remembered_baseline(
        normalized, repository, normalized["delivery"]["id"]
    )
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
    if project_scopes is not None:
        result["verifiedProjectScopes"] = verified_projects
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
    _inject_remembered_baseline(normalized, repository, root_id)
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
    workspace_root: str | None = None,
    workspace_turn_start: dict[str, Any] | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
    _execution_mode: str = "active",
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
    if _execution_mode not in {"active", "manual"}:
        fail(
            "SCHEDULER_EXECUTION_MODE_INVALID",
            "Graph execution mode must be active or manual",
            executionMode=_execution_mode,
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    if stored["status"] == "ARCHIVED":
        fail(
            "SCHEDULER_DELIVERY_ARCHIVED",
            "An archived Delivery cannot be frozen again",
            rootId=root_id,
        )
    revision_record = next(
        (
            revision
            for revision in repository.revision_history(root_id)[
                "revisions"
            ]
            if revision["revision"] == expected_delivery_revision
        ),
        None,
    )
    if revision_record is None:
        fail(
            "SCHEDULER_REVISION_MISSING",
            "The requested Delivery revision is missing",
            rootId=root_id,
            deliveryRevision=expected_delivery_revision,
        )
    revision_graph_fingerprint = revision_record["graphFingerprint"]
    actual_workspace = workspace_root or root
    repository.assert_delivery_workspace(root_id, actual_workspace)
    serial_gate = _resolve_serial_workspace_gate(
        repository,
        actual_workspace,
        root_id,
        repository.serial_workspace_turn_state(root_id),
    )
    if serial_gate["state"] != "ACQUIRED":
        if (
            serial_gate["state"] == "RELEASED"
            and stored["status"] == "FROZEN"
            and stored["deliveryRevision"]
            == expected_delivery_revision
        ):
            if (
                revision_record["hierarchyFingerprint"]
                != expected_hierarchy_fingerprint
            ):
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "Hierarchy fingerprint is not current",
                )
            run = repository.run(root_id)
            return {
                **run,
                "confirmedBy": confirmed_by.strip(),
                "graphRunCreated": False,
                "automaticDispatchRequested": False,
                "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
                "workspaceTurn": serial_gate["workspaceTurn"],
                "nextAction": (
                    "GRAPH_RUN_ALREADY_TERMINAL"
                    if run["status"] in _SERIAL_TERMINAL_STATUSES
                    else "READ_GRAPH_FRONTIER"
                ),
            }
        return {
            "rootId": root_id,
            "status": "QUEUED",
            "deliveryStatus": stored["status"],
            "deliveryRevision": expected_delivery_revision,
            "hierarchyFingerprint": expected_hierarchy_fingerprint,
            "graphFingerprint": revision_graph_fingerprint,
            "graphRunCreated": False,
            "automaticDispatchRequested": False,
            "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceTurn": serial_gate["workspaceTurn"],
            "deliveryQueue": _delivery_queue_marker(
                serial_gate,
                root_id,
            ),
            "nextAction": "WAIT_FOR_AUTOMATIC_QUEUE_TURN",
        }
    revision_hierarchy = repository.revision_hierarchy(
        root_id,
        expected_delivery_revision,
    )
    previous_turn_released = (
        repository.workspace_turn_release(root_id) is not None
    )
    if previous_turn_released:
        preparation = _automatic_serial_workspace_preparation(
            control_root=root,
            workspace_root=actual_workspace,
            hierarchy=revision_hierarchy,
        )
        if preparation is not None:
            return {
                "rootId": root_id,
                "status": "PREPARED",
                "deliveryStatus": stored["status"],
                "deliveryRevision": expected_delivery_revision,
                "hierarchyFingerprint": expected_hierarchy_fingerprint,
                "graphFingerprint": revision_graph_fingerprint,
                "graphRunCreated": False,
                "automaticDispatchRequested": False,
                "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
                "workspaceTurn": serial_gate["workspaceTurn"],
                "workspacePreparation": preparation,
                "nextAction": preparation["nextAction"],
            }
    workspace_requests = _automatic_workspace_requests(
        control_root=root,
        workspace_root=actual_workspace,
        hierarchy=revision_hierarchy,
    )
    captured_turn_start = None
    if (
        stored["status"] == "FROZEN"
        and expected_delivery_revision
        == stored["deliveryRevision"] + 1
        and not previous_turn_released
    ):
        captured_turn_start = _continue_workspace_turn_start(
            workspace_requests,
            _automatic_workspace_requests(
                control_root=root,
                workspace_root=actual_workspace,
                hierarchy=stored["hierarchy"],
            ),
            actual_workspace,
            repository.workspace_turn_start(root_id),
        )
    if captured_turn_start is None:
        captured_turn_start = _capture_workspace_turn_start(
            workspace_requests,
            actual_workspace,
        )
    if (
        workspace_turn_start is not None
        and workspace_turn_start != captured_turn_start
    ):
        fail(
            "SCHEDULER_WORKSPACE_TURN_CHANGED",
            "The current workspace changed between turn capture and freeze",
            rootId=root_id,
        )
    freeze_arguments = {
        "expected_delivery_revision": expected_delivery_revision,
        "expected_hierarchy_fingerprint": (
            expected_hierarchy_fingerprint
        ),
        "authorized_project_ids": authorized_project_ids or [],
        "confirmed_by": confirmed_by.strip(),
        "workspace_turn_start": captured_turn_start,
    }
    if _execution_mode == "manual":
        result = repository.freeze_manual_handoff(
            root_id,
            started_by=confirmed_by.strip(),
            **freeze_arguments,
        )
    else:
        result = repository.freeze(
            root_id,
            **freeze_arguments,
        )
    return {
        **result,
        "confirmedBy": confirmed_by.strip(),
        "nextAction": "READ_GRAPH_FRONTIER",
    }


_RECOVERABLE_MANUAL_GIT_ERRORS = frozenset(
    {
        "SCHEDULER_GIT_BASE_INVALID",
        "SCHEDULER_GIT_BINDING_REQUIRED",
        "SCHEDULER_GIT_BRANCH_MISMATCH",
        "SCHEDULER_GIT_DETACHED_HEAD",
        "SCHEDULER_GIT_CHECKOUT_REQUIRED",
    }
)


def _manual_baseline_reconfirmation(
    *,
    stored: dict[str, Any],
    repository: SchedulerRepository,
    workspace_root: str,
    host_adapter_id: str | None,
) -> dict[str, Any] | None:
    """Return a no-write manual-start blocker for recoverable Git drift."""

    delivery = stored["hierarchy"]["delivery"]
    try:
        verified_projects = verify_delivery_project_scopes(
            workspace_root,
            delivery,
            preparing=True,
        )
        if delivery.get("projectScopes") is None:
            verify_delivery_git_binding(
                workspace_root,
                delivery.get("gitBinding"),
                preparing=True,
            )
        elif not verified_projects:
            fail(
                "SCHEDULER_PROJECT_SCOPE_INVALID",
                "The manual handoff has no verified project",
                rootId=stored["rootId"],
            )
        return None
    except GatedLoopError as error:
        if error.code not in _RECOVERABLE_MANUAL_GIT_ERRORS:
            raise
        git_scopes = [
            scope
            for scope in delivery.get("projectScopes", [])
            if scope.get("gitBinding") is not None
        ]
        if len(git_scopes) > 1:
            fail(
                "SCHEDULER_MANUAL_MULTI_PROJECT_BASELINE_"
                "RECONFIRMATION_UNSUPPORTED",
                "Manual Git drift across multiple project scopes requires "
                "a complete explicit handoff revision; the single-workspace "
                "baseline selector cannot safely rewrite every repository",
                rootId=stored["rootId"],
                projectIds=sorted(scope["id"] for scope in git_scopes),
                driftReason=error.code,
                nextAction=(
                    "CREATE_MANUAL_REVISION_WITH_COMPLETE_PROJECT_BINDINGS"
                ),
            )
        context = _baseline_discovery(
            stored["hierarchy"],
            repository,
            workspace_root,
            host_adapter_id,
            expected_hierarchy_fingerprint=stored[
                "hierarchyFingerprint"
            ],
            expected_graph_fingerprint=stored["graphFingerprint"],
            expected_delivery_revision=stored["deliveryRevision"],
            interaction_context="MANUAL_HANDOFF_START",
        )
        if context is None:
            raise
        interaction = development_baseline_contract(
            host_adapter_id,
            git_binding=delivery.get("gitBinding"),
            candidate_branches=context["candidateBranches"],
            default_branch_ref=context["defaultBranchRef"],
            expected_hierarchy_fingerprint=stored[
                "hierarchyFingerprint"
            ],
            expected_graph_fingerprint=stored["graphFingerprint"],
            expected_delivery_revision=stored["deliveryRevision"],
            baseline_context_fingerprint=context[
                "baselineContextFingerprint"
            ],
            interaction_context="MANUAL_HANDOFF_START",
            working_tree=context["discovery"].get("workingTree"),
        )
        result = {
            "rootId": stored["rootId"],
            "deliveryRevision": stored["deliveryRevision"],
            "status": stored["status"],
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
            "graphFingerprint": stored["graphFingerprint"],
            "graphRunCreated": False,
            "manualStartState": (
                "BLOCKED_DEVELOPMENT_BASELINE_CONFIRMATION"
            ),
            "code": (
                "SCHEDULER_MANUAL_BASELINE_RECONFIRMATION_REQUIRED"
            ),
            "gitDrift": {
                "reason": error.code,
                "details": error.details,
            },
        }
        _attach_pending_interaction(result, interaction)
        result["nextAction"] = (
            "PRESENT_HOST_NATIVE_BASELINE_RECONFIRMATION"
        )
        return result


def start_manual_handoff(
    *,
    root: str,
    root_id: str,
    expected_hierarchy_fingerprint: str,
    expected_graph_fingerprint: str,
    started_by: str,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    host_adapter_id: str | None = None,
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
    actual_workspace = workspace_root or root
    if stored["status"] == "FROZEN":
        existing_run = repository.run(root_id)
        if existing_run["executionMode"] != "manual":
            fail(
                "SCHEDULER_MANUAL_HANDOFF_START_CONFLICT",
                "This Delivery already has a non-manual Graph run",
                rootId=root_id,
                executionMode=existing_run["executionMode"],
            )
        serial_gate = _frozen_serial_workspace_gate(
            repository,
            stored=stored,
            workspace_root=actual_workspace,
        )
        if serial_gate["state"] != "ACQUIRED":
            return _manual_workspace_waiting_result(
                stored=stored,
                started_by=started_by.strip(),
                serial_gate=serial_gate,
                already_applied=True,
            )
        _verify_frozen_delivery_workspace(stored, actual_workspace)
        terminal = existing_run["status"] in _SERIAL_TERMINAL_STATUSES
        return {
            **existing_run,
            "graphRunCreated": True,
            "manualStartAlreadyApplied": True,
            "nextAction": (
                "GRAPH_RUN_ALREADY_TERMINAL"
                if terminal
                else "READ_GRAPH_FRONTIER"
            ),
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
    serial_gate = _manual_serial_workspace_gate(
        repository,
        stored=stored,
        workspace_root=actual_workspace,
    )
    if serial_gate["state"] != "ACQUIRED":
        return _manual_workspace_waiting_result(
            stored=stored,
            started_by=started_by.strip(),
            serial_gate=serial_gate,
            already_applied=False,
        )
    if stored["status"] == "HANDOFF_READY":
        blocked = _manual_baseline_reconfirmation(
            stored=stored,
            repository=repository,
            workspace_root=actual_workspace,
            host_adapter_id=host_adapter_id,
        )
        if blocked is not None:
            return blocked
        prepared = prepare_hierarchy(
            root=root,
            hierarchy=stored["hierarchy"],
            workspace_root=actual_workspace,
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
            actual_workspace,
            delivery,
            preparing=False,
        )
        if delivery.get("projectScopes") is None:
            verify_delivery_git_binding(
                actual_workspace,
                delivery.get("gitBinding"),
                preparing=False,
            )
        elif not verified_projects:
            fail(
                "SCHEDULER_PROJECT_SCOPE_INVALID",
                "The interrupted manual adoption has no verified project",
                rootId=root_id,
            )
    prepared_stored = repository.hierarchy(root_id)
    serial_gate = _manual_serial_workspace_gate(
        repository,
        stored=prepared_stored,
        workspace_root=actual_workspace,
    )
    if serial_gate["state"] != "ACQUIRED":
        return _manual_workspace_waiting_result(
            stored=prepared_stored,
            started_by=started_by.strip(),
            serial_gate=serial_gate,
            already_applied=False,
        )
    turn_start = _capture_workspace_turn_start(
        _automatic_workspace_requests(
            control_root=root,
            workspace_root=actual_workspace,
            hierarchy=stored["hierarchy"],
        ),
        actual_workspace,
    )
    try:
        result = repository.freeze_manual_handoff(
            root_id,
            expected_delivery_revision=current["revision"],
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            authorized_project_ids=current["authorizedProjectIds"],
            confirmed_by=current["confirmedBy"].strip(),
            started_by=started_by.strip(),
            workspace_turn_start=turn_start,
        )
    except GatedLoopError as error:
        if error.code != "SCHEDULER_WORKSPACE_TURN_NOT_OWNED":
            raise
        return _manual_workspace_waiting_result(
            stored=repository.hierarchy(root_id),
            started_by=started_by.strip(),
            serial_gate={
                "state": "WAITING_FOR_WORKSPACE_TURN",
                "workspaceTurn": _workspace_turn_waiting(error),
            },
            already_applied=False,
        )
    return {
        **result,
        "startedBy": started_by.strip(),
        "graphRunCreated": True,
        "manualStartAlreadyApplied": False,
        "nextAction": "READ_GRAPH_FRONTIER",
    }


def resume_execution_mode(
    *,
    root: str,
    root_id: str,
    expected_hierarchy_fingerprint: str,
    expected_graph_fingerprint: str,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    host_adapter_id: str | None = None,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Continue one recorded AUTOMATIC choice in the ready workspace."""

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
            "The recorded execution choice does not match the generated "
            "baseline",
            rootId=root_id,
            actualHierarchyFingerprint=stored["hierarchyFingerprint"],
            actualGraphFingerprint=stored["graphFingerprint"],
        )
    actual_workspace = workspace_root or root
    if stored["status"] == "FROZEN":
        return _frozen_automatic_result(
            repository,
            stored=stored,
            workspace_root=actual_workspace,
        )
    selection = repository.execution_selection(root_id)
    if selection is None:
        fail(
            "SCHEDULER_EXECUTION_SELECTION_MISSING",
            "Automatic execution cannot resume without the recorded human "
            "selection",
            rootId=root_id,
        )
    serial_gate = _resolve_serial_workspace_gate(
        repository,
        actual_workspace,
        root_id,
        _serial_turn_for_recorded_selection(
            repository,
            root_id=root_id,
            stored=stored,
            selection=selection,
            workspace_root=actual_workspace,
        ),
    )
    if serial_gate["state"] != "ACQUIRED":
        return {
            "rootId": root_id,
            "status": "QUEUED",
            "deliveryStatus": stored["status"],
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
            "graphFingerprint": stored["graphFingerprint"],
            "selection": "AUTOMATIC",
            "selectionRecorded": True,
            "selectionAlreadyApplied": True,
            "confirmationRequired": False,
            "automaticDispatchRequested": False,
            "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceTurn": serial_gate["workspaceTurn"],
            "deliveryQueue": _delivery_queue_marker(
                serial_gate,
                root_id,
            ),
            "selectionContinuation": {
                "tool": "resume_execution_mode",
                "confirmationRequired": False,
                "selectionPreserved": True,
            },
            "nextAction": "WAIT_FOR_AUTOMATIC_QUEUE_TURN",
        }
    preparation = _automatic_serial_workspace_preparation(
        control_root=root,
        workspace_root=actual_workspace,
        hierarchy=stored["hierarchy"],
    )
    if preparation is not None:
        return {
            "rootId": root_id,
            "status": stored["status"],
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
            "graphFingerprint": stored["graphFingerprint"],
            "selection": "AUTOMATIC",
            "selectionRecorded": True,
            "confirmationRequired": False,
            "automaticDispatchRequested": False,
            "workspacePreparation": preparation,
            "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceTurn": serial_gate["workspaceTurn"],
            "selectionContinuation": {
                "tool": "resume_execution_mode",
                "confirmationRequired": False,
                "selectionPreserved": True,
            },
            "nextAction": preparation["nextAction"],
        }
    if stored["status"] == "CHOICE_READY":
        prepared = prepare_hierarchy(
            root=root,
            hierarchy=stored["hierarchy"],
            workspace_root=actual_workspace,
            explicit_dogfood=explicit_dogfood,
            now=now,
        )
    elif stored["status"] == "PREPARED":
        repository.assert_delivery_workspace(root_id, actual_workspace)
        prepared = {
            "rootId": root_id,
            "deliveryRevision": stored["deliveryRevision"],
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
        }
        verified_projects = verify_delivery_project_scopes(
            actual_workspace,
            stored["hierarchy"]["delivery"],
            preparing=False,
        )
        if stored["hierarchy"]["delivery"].get("projectScopes") is not None:
            prepared["verifiedProjectScopes"] = verified_projects
    else:
        fail(
            "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
            "The Delivery cannot resume the recorded automatic choice",
            rootId=root_id,
            status=stored["status"],
        )
    turn_start = _capture_workspace_turn_start(
        _automatic_workspace_requests(
            control_root=root,
            workspace_root=actual_workspace,
            hierarchy=stored["hierarchy"],
        ),
        actual_workspace,
    )
    try:
        frozen = freeze_hierarchy(
            root=root,
            root_id=root_id,
            expected_delivery_revision=prepared["deliveryRevision"],
            expected_hierarchy_fingerprint=prepared[
                "hierarchyFingerprint"
            ],
            authorized_project_ids=selection["authorizedProjectIds"],
            confirmed=True,
            confirmed_by=selection["confirmedBy"],
            workspace_root=actual_workspace,
            workspace_turn_start=turn_start,
            explicit_dogfood=explicit_dogfood,
            now=now,
        )
    except GatedLoopError as error:
        if error.code != "SCHEDULER_WORKSPACE_TURN_NOT_OWNED":
            raise
        workspace_turn = _workspace_turn_waiting(error)
        serial_gate = {
            "state": "WAITING_FOR_WORKSPACE_TURN",
            "workspaceTurn": workspace_turn,
        }
        return {
            "rootId": root_id,
            "status": "QUEUED",
            "deliveryStatus": "PREPARED",
            "hierarchyFingerprint": prepared[
                "hierarchyFingerprint"
            ],
            "graphFingerprint": expected_graph_fingerprint,
            "selection": "AUTOMATIC",
            "selectionRecorded": True,
            "selectionAlreadyApplied": True,
            "confirmationRequired": False,
            "automaticDispatchRequested": False,
            "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceTurn": workspace_turn,
            "deliveryQueue": _delivery_queue_marker(
                serial_gate,
                root_id,
            ),
            "selectionContinuation": {
                "tool": "resume_execution_mode",
                "confirmationRequired": False,
                "selectionPreserved": True,
            },
            "nextAction": "WAIT_FOR_AUTOMATIC_QUEUE_TURN",
        }
    return {
        **frozen,
        "selection": "AUTOMATIC",
        "selectionAlreadyApplied": False,
        "confirmationRequired": False,
        "automaticDispatchRequested": True,
        "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
        **(
            {"verifiedProjectScopes": prepared["verifiedProjectScopes"]}
            if "verifiedProjectScopes" in prepared
            else {}
        ),
        "nextAction": "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH",
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
    host_adapter_id: str | None = None,
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
        if repository.execution_selection(root_id) is not None:
            fail(
                "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
                "Automatic execution has already been selected; continue "
                "it without another mode confirmation",
                rootId=root_id,
                selected=selection,
            )
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

    selected_strategy = "CURRENT_WORKSPACE_SERIAL"
    if stored["status"] == "FROZEN":
        return _frozen_automatic_result(
            repository,
            stored=stored,
            workspace_root=workspace_root or root,
        )
    if stored["status"] == "HANDOFF_READY":
        fail(
            "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
            "Manual development has already been selected for this choice",
            rootId=root_id,
            status=stored["status"],
            selected=selection,
        )
    if stored["status"] not in {"CHOICE_READY", "PREPARED"}:
        fail(
            "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
            "The Delivery is not waiting for an execution choice",
            rootId=root_id,
            status=stored["status"],
            selected=selection,
        )
    _assert_exact_project_authorization(
        hierarchy,
        authorized_project_ids,
    )
    actual_workspace = workspace_root or root
    workspace_requests = _automatic_workspace_requests(
        control_root=root,
        workspace_root=actual_workspace,
        hierarchy=hierarchy,
    )
    recorded = repository.record_automatic_selection(
        root_id,
        expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
        expected_graph_fingerprint=expected_graph_fingerprint,
        authorized_project_ids=authorized_project_ids or [],
        confirmed_by=confirmed_by.strip(),
        workspace_key=SchedulerRepository.workspace_key(actual_workspace),
    )
    serial_gate = _resolve_serial_workspace_gate(
        repository,
        actual_workspace,
        root_id,
        recorded["workspaceTurn"],
    )
    if serial_gate["state"] != "ACQUIRED":
        return {
            "rootId": root_id,
            "status": "QUEUED",
            "deliveryStatus": stored["status"],
            "selection": "AUTOMATIC",
            "workspaceStrategy": selected_strategy,
            "selectionRecorded": not recorded["selectionAlreadyApplied"],
            "selectionAlreadyApplied": recorded[
                "selectionAlreadyApplied"
            ],
            "automaticDispatchRequested": False,
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
            "graphFingerprint": stored["graphFingerprint"],
            "workspaceTurn": serial_gate["workspaceTurn"],
            "deliveryQueue": _delivery_queue_marker(
                serial_gate,
                root_id,
            ),
            "nextAction": "WAIT_FOR_AUTOMATIC_QUEUE_TURN",
        }

    pending_requests = [
        request
        for request in workspace_requests
        if not _current_workspace_satisfies_request(
            request,
            actual_workspace,
        )
    ]
    if pending_requests:
        preparation = _current_workspace_serial_preparation(
            pending_requests,
            actual_workspace,
        )
        return {
            "rootId": root_id,
            "status": stored["status"],
            "selection": "AUTOMATIC",
            "workspaceStrategy": selected_strategy,
            "selectionRecorded": not recorded["selectionAlreadyApplied"],
            "selectionAlreadyApplied": recorded[
                "selectionAlreadyApplied"
            ],
            "automaticDispatchRequested": False,
            "hierarchyFingerprint": stored[
                "hierarchyFingerprint"
            ],
            "graphFingerprint": stored["graphFingerprint"],
            "workspaceTurn": serial_gate["workspaceTurn"],
            "workspacePreparation": preparation,
            "projectWorkspacePreparations": preparation[
                "projectPreparations"
            ],
            "selectionContinuation": {
                "tool": "resume_execution_mode",
                "selectionPreserved": True,
                "confirmationRequired": False,
            },
            "nextAction": preparation["nextAction"],
        }
    resumed = resume_execution_mode(
        root=root,
        root_id=root_id,
        expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
        expected_graph_fingerprint=expected_graph_fingerprint,
        workspace_root=workspace_root or root,
        explicit_dogfood=explicit_dogfood,
        host_adapter_id=host_adapter_id,
        now=now,
    )
    return {
        **resumed,
        "workspaceStrategy": selected_strategy,
        "selectionRecorded": not recorded["selectionAlreadyApplied"],
        "selectionAlreadyApplied": recorded["selectionAlreadyApplied"],
    }


__all__ = (
    "create_manual_handoff",
    "delivery_revision_history",
    "freeze_hierarchy",
    "prepare_delivery_revision",
    "prepare_hierarchy",
    "preview_hierarchy",
    "resume_execution_mode",
    "select_execution_mode",
    "start_manual_handoff",
    "workspace_status",
)
