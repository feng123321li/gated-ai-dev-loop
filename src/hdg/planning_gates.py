from __future__ import annotations

from .planning_common import (
    Any,
    GatedLoopError,
    Path,
    SchedulerRepository,
    deepcopy,
    development_baseline_contract,
    enumerate_local_feature_branches,
    execution_choice_contract,
    fail,
    fingerprint,
    git_repository_identity,
    inspect_business_commit_range,
    inspect_delivery_git_workspace,
    validate_git_binding,
    verify_delivery_git_binding,
    verify_delivery_project_scopes,
)
from .planning_workspace import (
    _SERIAL_TERMINAL_STATUSES,
    _request_workspace_root,
    _serial_commit_barrier,
    _serial_workspace_release_eligibility,
)


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
                    "state": commit_barrier["state"],
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
                "state": commit_barrier["state"],
                "workspaceTurn": {
                    **current,
                    **commit_barrier,
                },
            }
        current = repository.serial_workspace_turn_state(root_id)


def _serial_workspace_release_handshake(
    repository: SchedulerRepository,
    workspace_root: str,
    root_id: str,
) -> dict[str, Any]:
    """Resolve and describe the only legal action after a release boundary."""

    resolved = _resolve_serial_workspace_gate(
        repository,
        workspace_root,
        root_id,
        repository.serial_workspace_turn_state(root_id),
    )
    workspace_turn = resolved["workspaceTurn"]
    if resolved["state"] == "RELEASED":
        release = workspace_turn.get("release")
        if not isinstance(release, dict):
            release = repository.workspace_turn_release(root_id) or {}
        return {
            "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceTurn": workspace_turn,
            "workspaceRelease": {
                "state": "RELEASED",
                "releaseReason": release.get("releaseReason"),
                "releasedAt": release.get("releasedAt"),
                "nextAction": (
                    "WORKSPACE_RELEASED_BRANCH_SWITCH_ALLOWED"
                ),
            },
            "nextAction": "WORKSPACE_RELEASED_BRANCH_SWITCH_ALLOWED",
        }
    project_barriers = workspace_turn.get("projectBarriers")
    project_reasons = [
        barrier.get("reason")
        for barrier in (
            project_barriers
            if isinstance(project_barriers, list)
            else []
        )
        if isinstance(barrier, dict)
        and isinstance(barrier.get("reason"), str)
    ]
    reason = workspace_turn.get("reason")
    if not isinstance(reason, str):
        reason = (
            project_reasons[0]
            if len(set(project_reasons)) == 1 and project_reasons
            else (
                "PROJECT_RELEASE_BARRIERS"
                if project_reasons
                else "RUN_NOT_AT_RELEASE_BOUNDARY"
            )
        )
    if resolved["state"] == "WAITING_FOR_WORKSPACE_QUIESCENCE":
        next_action = "QUIESCE_RECEIVERS_AND_RECHECK_RELEASE"
    elif any(
        isinstance(barrier, dict)
        and barrier.get("state") == "WORKSPACE_DRIFTED"
        for barrier in (
            project_barriers
            if isinstance(project_barriers, list)
            else []
        )
    ):
        next_action = "RESTORE_FROZEN_WORKSPACE_AND_RECHECK_RELEASE"
    elif resolved["state"] == "WAITING_FOR_WORKSPACE_COMMIT":
        next_action = (
            "COMMIT_CLEAN_FROZEN_WORKSPACE_AND_RECHECK_RELEASE"
        )
    else:
        next_action = "REACH_SAFE_RELEASE_BOUNDARY_AND_RECHECK"
    return {
        "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
        "workspaceTurn": workspace_turn,
        "workspaceRelease": {
            "state": "PENDING",
            "reason": reason,
            "gateState": resolved["state"],
            "nextAction": next_action,
        },
        "nextAction": next_action,
    }

def _serial_turn_for_recorded_selection(
    repository: SchedulerRepository,
    *,
    root_id: str,
    stored: dict[str, Any],
    selection: dict[str, Any],
    workspace_root: str,
) -> dict[str, Any]:
    """Resolve a recorded execution choice in the current serial queue."""

    try:
        workspace_turn = repository.serial_workspace_turn_state(root_id)
    except GatedLoopError as error:
        if error.code != "SCHEDULER_DELIVERY_WORKSPACE_MISSING":
            raise
        if selection["selection"] != "AUTOMATIC":
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
    *,
    selection: str = "AUTOMATIC",
) -> dict[str, Any]:
    """Project a persisted serial turn as a Delivery execution queue."""

    workspace_turn = serial_gate["workspaceTurn"]
    automatic = selection == "AUTOMATIC"
    return {
        "state": "QUEUED",
        "position": workspace_turn["position"],
        "queueLength": workspace_turn["queueLength"],
        "ownerRootId": workspace_turn["ownerRootId"],
        "ownerStatus": workspace_turn["ownerStatus"],
        "continuation": {
            "automatic": automatic,
            "tool": (
                "resume_execution_mode"
                if automatic
                else "start_manual_handoff"
            ),
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
                "state": commit_barrier["state"],
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
        "status": "QUEUED",
        "deliveryStatus": stored["status"],
        "hierarchyFingerprint": stored["hierarchyFingerprint"],
        "graphFingerprint": stored["graphFingerprint"],
        "startedBy": started_by,
        "graphRunCreated": False,
        "manualStartState": serial_gate["state"],
        "manualStartAlreadyApplied": already_applied,
        "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
        "workspaceTurn": serial_gate["workspaceTurn"],
        "deliveryQueue": _delivery_queue_marker(
            serial_gate,
            stored["rootId"],
            selection="MANUAL",
        ),
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
