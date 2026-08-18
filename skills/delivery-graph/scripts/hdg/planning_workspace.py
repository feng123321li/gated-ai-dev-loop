from __future__ import annotations

from .planning_common import (
    Any,
    GatedLoopError,
    Path,
    SchedulerRepository,
    compile_delivery_graph,
    fail,
    git_repository_identity,
    graph_fingerprint,
    hierarchy_fingerprint,
    inspect_business_commit_range,
    inspect_delivery_git_workspace,
    iter_hierarchy_nodes,
    task_baseline_relative_path,
    task_has_database_projection,
    task_has_interface_projection,
    validate_git_binding,
    validate_hierarchy_definition,
    verify_delivery_git_binding,
    work_item_projection_relative_path,
)


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
    *,
    selection: str = "AUTOMATIC",
) -> dict[str, Any]:
    if selection not in {"AUTOMATIC", "MANUAL"}:
        fail(
            "SCHEDULER_EXECUTION_CHOICE_INVALID",
            "Workspace preparation requires AUTOMATIC or MANUAL",
            selection=selection,
        )
    manual = selection == "MANUAL"
    resume_action = (
        "START_MANUAL_HANDOFF" if manual else "RESUME_EXECUTION_MODE"
    )
    resume_tool = (
        "start_manual_handoff" if manual else "resume_execution_mode"
    )
    preparation_suffix = (
        "START_MANUAL_HANDOFF" if manual else "RESUME_EXECUTION"
    )
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
                else f"HOST_STASH_PREPARE_BRANCH_THEN_{preparation_suffix}"
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
            f"HOST_STASH_PREPARE_BRANCH_THEN_{preparation_suffix}"
            if stash_available
            else "RESOLVE_CONFLICTS_OR_KEEP_CHANGES_AND_WAIT"
        )
        if dirty
        else f"PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_{preparation_suffix}"
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
                "kind": f"{selection}_DIRTY_WORKSPACE_PREPARATION",
                "action": "STASH_AND_RUN",
                "confirmationRequired": False,
                "authorizationSource": f"{selection}_EXECUTION_SELECTION",
                "fallbackAction": "KEEP_CHANGES_AND_WAIT",
                "hostAction": {
                    "label": "暂存现有改动后运行",
                    "description": (
                        f"{selection} 选择已授权宿主机械准备 workspace；宿主"
                        "精确复核工作树指纹，stash 已跟踪、暂存和未跟踪"
                        "业务改动；工作树变干净后创建或切换 Delivery 分支，"
                        f"再调用 {resume_tool}。"
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
                        f"HOST_STASH_PREPARE_BRANCH_THEN_{preparation_suffix}"
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
                "kind": f"{selection}_DIRTY_WORKSPACE_PREPARATION",
                "action": "KEEP_CHANGES_AND_WAIT",
                "confirmationRequired": False,
                "authorizationSource": f"{selection}_EXECUTION_SELECTION",
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
                    "action": resume_action,
                    "tool": resume_tool,
                },
            ]
        )
        result[
            "manualHostPreparation"
            if manual
            else "automaticHostPreparation"
        ] = {
            "state": "READY",
            "authorizationSource": f"{selection}_EXECUTION_SELECTION",
            "confirmationRequired": False,
            "controllerExecutesGit": False,
            "actions": actions,
        }
    else:
        result[
            "manualHostPreparation"
            if manual
            else "automaticHostPreparation"
        ] = {
            "state": "BLOCKED",
            "authorizationSource": f"{selection}_EXECUTION_SELECTION",
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


def _manual_serial_workspace_preparation(
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
        selection="MANUAL",
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
