from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import GatedLoopError, fail
from .fs_safe import atomic_write, safe_path
from .git_binding import (
    _branch_worktree_count,
    enumerate_local_feature_branches,
    find_delivery_linked_worktree,
    git_repository_identity,
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
    task_has_interface_projection,
    work_item_projection_relative_path,
)
from .repository import (
    GOVERNANCE_DIRECTORY,
    SchedulerRepository,
    WORKTREE_SETUP_HEARTBEAT_SECONDS,
    WORKTREE_SETUP_LEASE_SECONDS,
    WORKTREE_SETUP_POLL_SECONDS,
)


WORKTREE_SETUP_PHASES = frozenset(
    {
        "STARTING",
        "CREATING_DIRECTORY",
        "CREATING_BRANCH",
        "CREATING_WORKTREE",
        "CHECKING_OUT",
        "VERIFYING",
        "RECONCILING",
        "FAILED",
    }
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
    selected_root_id = result.get("rootId")
    if isinstance(selected_root_id, str):
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
        if selection is not None:
            recorded_setup = _automatic_workspace_setup(
                control_root=root,
                workspace_root=workspace_root or root,
                hierarchy=stored["hierarchy"],
                host_adapter_id=host_adapter_id,
                repository=repository,
            )
            if recorded_setup is not None:
                result["worktreeSetup"] = recorded_setup
                result["nextAction"] = recorded_setup["nextAction"]
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
            and discovery.get("worktreeSetup") is not None
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


def _safe_worktree_component(value: str, *, limit: int = 48) -> str:
    return "".join(
        character if character.isalnum() else "-"
        for character in value.casefold()
    ).strip("-")[:limit]


def _automatic_worktree_requests(
    *,
    control_root: str,
    workspace_root: str,
    hierarchy: dict[str, Any],
) -> list[dict[str, Any]]:
    """Describe the repository/branch reservations for one Delivery."""

    delivery = hierarchy["delivery"]
    delivery_id = delivery["id"]
    hierarchy_value = hierarchy_fingerprint(hierarchy)
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
                "SCHEDULER_GIT_WORKTREE_REQUIRED",
                "A Git-bound project scope requires a Git repository",
                projectId=candidate["projectId"],
                workspaceRoot=candidate["repositoryRoot"],
            )
        project_id = candidate["projectId"]
        multi_project = scopes is not None
        name_base = _safe_worktree_component(delivery_id)
        if multi_project:
            name_base += "-" + _safe_worktree_component(project_id, limit=24)
        worktree_name = (
            f"ld-{name_base[:72]}-{hierarchy_value[:10]}"
        )
        idempotency_key = (
            f"delivery-worktree:{delivery_id}:"
            + (f"{project_id}:" if multi_project else "")
            + hierarchy_value
        )
        binding = validate_git_binding(candidate["gitBinding"])
        reservation_id = fingerprint(
            {
                "rootId": delivery_id,
                "projectId": project_id,
                "repositoryKey": repository_key,
                "branchRef": binding["branchRef"],
                "hierarchyFingerprint": hierarchy_value,
            }
        )
        requests.append(
            {
                **candidate,
                "gitBinding": binding,
                "repositoryKey": repository_key,
                "branchRef": binding["branchRef"],
                "coordinatorWorkspace": repository_key == workspace_key,
                "worktreeName": worktree_name,
                "idempotencyKey": idempotency_key,
                "reservationId": reservation_id,
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


def _worktree_setup_progress(
    reservation: dict[str, Any] | None,
    *,
    ready: bool = False,
) -> dict[str, Any]:
    state = reservation or {}
    status = "READY" if ready else str(state.get("status") or "PENDING")
    health_by_status = {
        "PENDING": "QUEUED",
        "IN_PROGRESS": "ACTIVE",
        "READY": "READY",
        "FAILED": "FAILED",
        "EXPIRED": "STALE",
        "RELEASED": "RELEASED",
        "SUPERSEDED": "SUPERSEDED",
    }
    result = {
        "status": status,
        "health": health_by_status.get(status, "UNKNOWN"),
        "attempt": int(state.get("attempt") or 1),
        "phase": "READY" if ready else (state.get("phase") or "QUEUED"),
        "summaryZh": (
            "精确 worktree 已由 Controller 验证"
            if ready
            else (state.get("summaryZh") or "等待宿主创建 worktree")
        ),
        "progressPercent": (
            100
            if ready
            else (
                state.get("progressPercent")
                if state.get("progressPercent") is not None
                else 0
            )
        ),
        "issuedAt": state.get("issuedAt"),
        "lastReportedAt": state.get("lastReportedAt"),
        "leaseExpiresAt": None if ready else state.get("leaseExpiresAt"),
        "heartbeatIntervalSeconds": WORKTREE_SETUP_HEARTBEAT_SECONDS,
        "leaseSeconds": WORKTREE_SETUP_LEASE_SECONDS,
    }
    if state.get("failureCode") is not None:
        result["failureCode"] = state["failureCode"]
    if state.get("failureMessageZh") is not None:
        result["failureMessageZh"] = state["failureMessageZh"]
    return result


def _worktree_progress_monitor(
    project_setups: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = [
        {
            "projectId": setup["projectId"],
            **setup["setupProgress"],
        }
        for setup in project_setups
    ]
    alerts = []
    for row in rows:
        if row["health"] == "STALE":
            alerts.append(
                {
                    "projectId": row["projectId"],
                    "code": "WORKTREE_SETUP_LEASE_EXPIRED",
                    "messageZh": (
                        "创建心跳已超时；核对旧宿主和残留路径后再申请重试"
                    ),
                }
            )
        elif row["health"] == "FAILED":
            alerts.append(
                {
                    "projectId": row["projectId"],
                    "code": row.get(
                        "failureCode",
                        "WORKTREE_SETUP_FAILED",
                    ),
                    "messageZh": row.get(
                        "failureMessageZh",
                        "worktree 创建失败；必须先完成残留状态核对",
                    ),
                }
            )
    return {
        "recommendedPollSeconds": WORKTREE_SETUP_POLL_SECONDS,
        "rows": rows,
        "alerts": alerts,
    }


def _project_worktree_setup(
    *,
    request: dict[str, Any],
    workspace_root: str,
    delivery_id: str,
    hierarchy_value: str,
    graph_value: str,
    host_adapter_id: str | None,
    reservation_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Describe one exact project worktree without performing Git writes."""

    binding = request["gitBinding"]
    repository_root = request["repositoryRoot"]
    current_root = Path(workspace_root).absolute().resolve(strict=True)
    exact_worktree = find_delivery_linked_worktree(
        repository_root,
        binding,
    )
    coordinator = bool(request["coordinatorWorkspace"])
    dispatch_already_issued = bool(
        (reservation_state or {}).get("dispatchAlreadyIssued", False)
    )
    if exact_worktree is not None and (
        not coordinator
        or Path(exact_worktree).resolve(strict=True) == current_root
    ):
        return {
            **request,
            "state": "READY",
            "owner": "CONTROLLER_VERIFIED",
            "nextAction": "NONE",
            "workspaceRoot": exact_worktree,
            "setupProgress": _worktree_setup_progress(
                reservation_state,
                ready=True,
            ),
        }

    inspected_root = (
        workspace_root
        if coordinator
        else repository_root
    )
    discovery = inspect_delivery_git_workspace(
        inspected_root,
        base_ref=binding["baseRef"],
        host_adapter_id=host_adapter_id,
    )
    if not isinstance(discovery, dict):
        fail(
            "SCHEDULER_GIT_WORKTREE_REQUIRED",
            "A Git-bound project scope requires a Git worktree",
            projectId=request["projectId"],
        )
    setup = discovery.get("worktreeSetup")
    actual_branch = discovery.get("gitWorkspace", {}).get("branchRef")
    if (
        not isinstance(setup, dict)
        and isinstance(actual_branch, str)
        and actual_branch != binding["branchRef"]
    ):
        working_tree = discovery.get("workingTree", {})
        setup = {
            "state": (
                "FROZEN_DELIVERY_BRANCH_REQUIRED"
                if working_tree.get("clean")
                else "FROZEN_DELIVERY_BRANCH_DIRTY"
            ),
            "owner": "HOST",
            "nextAction": (
                "CHECKOUT_FROZEN_DELIVERY_BRANCH"
                if working_tree.get("clean")
                else "REVIEW_CHANGES_BEFORE_FROZEN_BRANCH_CHECKOUT"
            ),
            "actualBranchRef": actual_branch,
            "baseRef": binding["baseRef"],
            "baseCommit": binding["baseCommit"],
            "integrationTarget": binding["integrationTarget"],
            "workingTree": working_tree,
        }
    elif not isinstance(setup, dict):
        verify_delivery_git_binding(
            inspected_root,
            binding,
            preparing=True,
        )
        return {
            **request,
            "state": "READY",
            "owner": "CONTROLLER_VERIFIED",
            "nextAction": "NONE",
            "workspaceRoot": str(
                Path(inspected_root).absolute().resolve(strict=True)
            ),
        }
    else:
        setup = dict(setup)

    reservation_status = (reservation_state or {}).get("status")
    if reservation_status == "EXPIRED":
        setup.update(
            {
                "state": "WORKTREE_SETUP_LEASE_EXPIRED",
                "owner": "HOST",
                "nextAction": "RECONCILE_EXPIRED_WORKTREE_SETUP",
            }
        )
    elif reservation_status == "FAILED":
        setup.update(
            {
                "state": "WORKTREE_SETUP_FAILED",
                "owner": "HOST",
                "nextAction": "RECONCILE_FAILED_WORKTREE_SETUP",
            }
        )

    setup.update(
        {
            "projectId": request["projectId"],
            "access": request["access"],
            "repositoryRoot": repository_root,
            "repositoryKey": request["repositoryKey"],
            "coordinatorWorkspace": coordinator,
            "branchRef": binding["branchRef"],
            "gitBinding": binding,
            "strategy": discovery.get("worktreeProvenance", {}).get(
                "strategy",
                "HOST_NATIVE_LINKED_WORKTREE",
            ),
            "resumeAction": (
                "CALL_WORKSPACE_STATUS_THEN_RESUME_EXECUTION_MODE"
            ),
            "resumeTool": "resume_execution_mode",
            "controllerCreatesWorktree": False,
            "selectionPreserved": True,
            "reservationId": request["reservationId"],
            "setupAttempt": int(
                (reservation_state or {}).get("attempt") or 1
            ),
            "setupProgress": _worktree_setup_progress(
                reservation_state,
            ),
        }
    )
    existing_worktree = exact_worktree
    host_operations = {
        "claude-code": "CREATE_CLAUDE_BACKGROUND_DELIVERY_AGENT",
        "codex": "CREATE_CODEX_PROJECT_TASK",
    }
    if coordinator:
        host_operation = host_operations.get(
            host_adapter_id or "",
            "CREATE_HOST_NATIVE_WORKTREE_TASK",
        )
    else:
        host_operation = "PREPARE_PROJECT_LINKED_WORKTREE"
    launch_policy = (
        "BLOCKED"
        if setup["state"]
        in {
            "FROZEN_DELIVERY_BRANCH_DIRTY",
            "WORKTREE_SETUP_FAILED",
            "WORKTREE_SETUP_LEASE_EXPIRED",
        }
        else (
            "DO_NOT_REISSUE"
            if dispatch_already_issued
            and coordinator
            and setup["state"] == "DEDICATED_WORKTREE_REQUIRED"
            else (
                "CONTINUE_EXISTING_WORKTREE_TASK"
                if dispatch_already_issued
                else "IMMEDIATE"
            )
        )
    )
    prompt = (
        f"Prepare project {request['projectId']} for automatic Delivery "
        f"{delivery_id} using exact branch {binding['branchRef']} from "
        f"frozen base {binding['baseCommit']}. Do not invent or reuse a "
        "different branch. Before the host action call "
        f"report_worktree_setup for reservation {request['reservationId']} "
        f"attempt {int((reservation_state or {}).get('attempt') or 1)} with "
        "STARTED, then report each phase or heartbeat within 30 seconds; "
        "report FAILED on an error and never retry until the Controller "
        "grants a reconciled new attempt. Call workspace_status"
        f"(root_id={delivery_id}) after the host-owned worktree action. "
    )
    if coordinator:
        prompt += (
            "Then complete every pending projectWorktreeSetup returned by "
            "the Controller and call "
            f"resume_execution_mode(root_id={delivery_id}, "
            f"expected_hierarchy_fingerprint={hierarchy_value}, "
            f"expected_graph_fingerprint={graph_value}). Remain the single "
            "background Delivery coordinator and report all project "
            "progress to the shared control root. Never start another "
            "top-level CLI session."
        )
    else:
        prompt += (
            "Return the prepared workspace to the existing Delivery "
            "coordinator; do not start another coordinator."
        )
    setup["hostDispatch"] = {
        "action": (
            "CREATE_HOST_NATIVE_WORKTREE_TASK"
            if coordinator
            else "PREPARE_PROJECT_LINKED_WORKTREE"
        ),
        "hostAdapterId": host_adapter_id,
        "hostOperation": host_operation,
        "launchPolicy": launch_policy,
        "dispatchAlreadyIssued": dispatch_already_issued,
        "environment": "worktree",
        "deliveryId": delivery_id,
        "projectId": request["projectId"],
        "repositoryRoot": repository_root,
        "repositoryKey": request["repositoryKey"],
        "branchRef": binding["branchRef"],
        "gitBinding": binding,
        "title": f"Delivery {delivery_id} / {request['projectId']}",
        "idempotencyKey": request["idempotencyKey"],
        "reservationId": request["reservationId"],
        "setupAttempt": int(
            (reservation_state or {}).get("attempt") or 1
        ),
        "prompt": prompt,
        "baseRef": binding["baseRef"],
        "baseCommit": binding["baseCommit"],
        "integrationTarget": binding["integrationTarget"],
        "manualDirectoryChangeRequired": False,
        "coordinatorCheckoutPolicy": "PRESERVE_CURRENT_CHECKOUT",
        "stableDeliveryWorkspace": True,
        "requiresNewTopLevelSession": False,
        "manualSessionLaunchAllowed": False,
        "sameSessionEnterWorktreeSupported": True,
        "mainConversationRole": "MONITOR_ONLY",
        "worktreeName": request["worktreeName"],
        "existingWorktreeRoot": existing_worktree,
        "launchCoordinator": coordinator,
        "progressReporting": {
            "tool": "report_worktree_setup",
            "reservationId": request["reservationId"],
            "projectId": request["projectId"],
            "expectedAttempt": int(
                (reservation_state or {}).get("attempt") or 1
            ),
            "heartbeatIntervalSeconds": WORKTREE_SETUP_HEARTBEAT_SECONDS,
            "leaseSeconds": WORKTREE_SETUP_LEASE_SECONDS,
            "reportAt": [
                "STARTED",
                "CREATING_DIRECTORY",
                "CREATING_BRANCH",
                "CREATING_WORKTREE",
                "CHECKING_OUT",
                "VERIFYING",
                "FAILED",
            ],
            "progressRenewsLease": True,
        },
        "agentDispatch": (
            {
                "agentType": "delivery-graph:delivery-coordinator",
                "name": request["worktreeName"],
                "runInBackground": True,
                "reusePolicy": "RESUME_BY_NAME",
                "workspaceEntry": (
                    "ENTER_EXISTING_WORKTREE"
                    if existing_worktree is not None
                    else "CREATE_LINKED_WORKTREE_THEN_ENTER"
                ),
                "returnMainConversationToCoordinatorCheckout": True,
            }
            if host_adapter_id == "claude-code" and coordinator
            else (
                {
                    "taskEnvironment": "worktree",
                    "runInBackground": True,
                    "reusePolicy": "RESUME_PROJECT_TASK",
                }
                if coordinator
                else {
                    "taskEnvironment": "worktree",
                    "runInBackground": False,
                    "reusePolicy": "PREPARE_ONCE",
                }
            )
        ),
        "continuation": {
            "firstTool": "workspace_status",
            "thenTool": "resume_execution_mode",
            "rootId": delivery_id,
            "expectedHierarchyFingerprint": hierarchy_value,
            "expectedGraphFingerprint": graph_value,
            "confirmationRequired": False,
        },
    }
    return setup


def _automatic_workspace_setup(
    *,
    control_root: str,
    workspace_root: str,
    hierarchy: dict[str, Any],
    host_adapter_id: str | None,
    repository: SchedulerRepository,
    reservation_states: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    requests = _automatic_worktree_requests(
        control_root=control_root,
        workspace_root=workspace_root,
        hierarchy=hierarchy,
    )
    if not requests:
        return None
    delivery_id = hierarchy["delivery"]["id"]
    hierarchy_value = hierarchy_fingerprint(hierarchy)
    graph = compile_delivery_graph(
        hierarchy,
        hierarchy_fingerprint=hierarchy_value,
    )
    graph_value = graph_fingerprint(graph)
    states = {
        item["projectId"]: item
        for item in (
            reservation_states
            if reservation_states is not None
            else repository.worktree_setup_reservations(delivery_id)
        )
    }
    project_setups = [
        _project_worktree_setup(
            request=request,
            workspace_root=workspace_root,
            delivery_id=delivery_id,
            hierarchy_value=hierarchy_value,
            graph_value=graph_value,
            host_adapter_id=host_adapter_id,
            reservation_state=states.get(request["projectId"]),
        )
        for request in requests
    ]
    progress_monitor = _worktree_progress_monitor(project_setups)
    for setup in project_setups:
        setup["progressMonitor"] = progress_monitor
    ready_project_ids = [
        item["projectId"]
        for item in project_setups
        if item["state"] == "READY"
    ]
    repository.mark_worktree_setups_ready(
        delivery_id,
        ready_project_ids,
    )
    pending = [
        item for item in project_setups if item["state"] != "READY"
    ]
    if not pending:
        return None
    if len(project_setups) == 1:
        result = pending[0]
        dispatch = result.get("hostDispatch", {})
        if (
            result["state"] == "DEDICATED_WORKTREE_REQUIRED"
            and dispatch.get("dispatchAlreadyIssued")
        ):
            result["nextAction"] = "WAIT_FOR_EXISTING_WORKTREE_SETUP"
        return result

    coordinator_pending = next(
        (
            item
            for item in pending
            if item["coordinatorWorkspace"]
        ),
        None,
    )
    actionable = any(
        item["state"] != "DEDICATED_WORKTREE_REQUIRED"
        or not item["coordinatorWorkspace"]
        or not item.get("hostDispatch", {}).get("dispatchAlreadyIssued")
        for item in pending
    )
    primary_pending = next(
        (
            item
            for item in pending
            if item["coordinatorWorkspace"]
        ),
        pending[0],
    )
    result = {
        "state": "PROJECT_WORKTREES_REQUIRED",
        "owner": "HOST",
        "nextAction": (
            "CREATE_REQUIRED_PROJECT_WORKTREES"
            if actionable
            and not (
                coordinator_pending is not None
                and coordinator_pending.get("hostDispatch", {}).get(
                    "dispatchAlreadyIssued"
                )
            )
            else "WAIT_FOR_EXISTING_WORKTREE_SETUP"
        ),
        "strategy": "HOST_NATIVE_LINKED_WORKTREE",
        "resumeAction": "CALL_WORKSPACE_STATUS_THEN_RESUME_EXECUTION_MODE",
        "resumeTool": "resume_execution_mode",
        "controllerCreatesWorktree": False,
        "selectionPreserved": True,
        "progressControlRoot": str(
            Path(control_root).absolute().resolve(strict=True)
        ),
        "sharedProgressControlPlane": True,
        "readyProjectIds": ready_project_ids,
        "pendingProjectIds": [item["projectId"] for item in pending],
        "projectWorktreeSetups": project_setups,
        "progressMonitor": progress_monitor,
        "hostDispatch": primary_pending["hostDispatch"],
    }
    return result


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
            "worktreeSetup": discovery.get("worktreeSetup"),
            "branchAdoption": discovery.get("branchAdoption"),
            "candidates": candidates,
        }
    )
    return {
        "discovery": discovery,
        "candidateBranches": candidates,
        "defaultBranchRef": default_branch_ref,
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
            "RESUME_RECORDED_AUTOMATIC_SELECTION_IN_READY_WORKTREE"
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
    ``NEW_FROM_MAINLINE`` choice pins ``baseCommit`` to the current mainline
    HEAD and the host creates the branch during worktree setup.
    """

    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "confirmed_by must identify the confirming human",
        )
    if not isinstance(selection, str) or not selection.strip():
        fail(
            "SCHEDULER_BASELINE_CHOICE_INVALID",
            "selection must be a local branch_ref or NEW_FROM_MAINLINE",
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
    )
    if context is None:
        fail(
            "SCHEDULER_GIT_WORKTREE_REQUIRED",
            "A development baseline can only be confirmed in a Git worktree",
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
    if not working_tree.get("clean", False):
        actual_dirty = working_tree.get("stateFingerprint")
        if confirmed_dirty_state_fingerprint != actual_dirty:
            fail(
                "SCHEDULER_GIT_DIRTY_CONFIRMATION_REQUIRED",
                "All current worktree changes must be explicitly attributed "
                "to this Delivery using the presented state fingerprint",
                dirtyStateFingerprint=actual_dirty,
            )
    if selection == "NEW_FROM_MAINLINE":
        if (
            not isinstance(branch_name, str)
            or not branch_name.strip()
            or branch_name.strip() in {"main", "master"}
        ):
            fail(
                "SCHEDULER_BASELINE_CHOICE_INVALID",
                "NEW_FROM_MAINLINE requires a new branch name that is not "
                "main or master",
                branchName=branch_name,
            )
        chosen_branch = branch_name.strip()
        source = "NEW_FROM_MAINLINE"
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
    binding = resolve_branch_binding(workspace, branch_ref=chosen_branch)
    terminal_statuses = {"COMPLETED", "CANCELLED", "SUPERSEDED"}
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


_RECOVERABLE_MANUAL_GIT_ERRORS = frozenset(
    {
        "SCHEDULER_GIT_BASE_INVALID",
        "SCHEDULER_GIT_BINDING_REQUIRED",
        "SCHEDULER_GIT_BRANCH_MISMATCH",
        "SCHEDULER_GIT_DETACHED_HEAD",
        "SCHEDULER_GIT_WORKTREE_REQUIRED",
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
        blocked = _manual_baseline_reconfirmation(
            stored=stored,
            repository=repository,
            workspace_root=workspace_root or root,
            host_adapter_id=host_adapter_id,
        )
        if blocked is not None:
            return blocked
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


def report_worktree_setup(
    *,
    root: str,
    root_id: str,
    project_id: str,
    reservation_id: str,
    expected_attempt: int,
    event: str,
    phase: str,
    summary_zh: str,
    progress_percent: int | None = None,
    failure_code: str | None = None,
    retry_request_id: str | None = None,
    confirmed_previous_attempt_stopped: bool = False,
    confirmed_partial_state_reconciled: bool = False,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    host_adapter_id: str | None = None,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Report host worktree setup progress or grant one reconciled retry."""

    for field_name, value, maximum in (
        ("root_id", root_id, 192),
        ("project_id", project_id, 192),
        ("reservation_id", reservation_id, 128),
        ("summary_zh", summary_zh, 500),
    ):
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > maximum
        ):
            fail(
                "SCHEDULER_WORKTREE_SETUP_REPORT_INVALID",
                f"{field_name} must be a non-empty bounded string",
                field=field_name,
            )
    if (
        not isinstance(expected_attempt, int)
        or isinstance(expected_attempt, bool)
        or expected_attempt < 1
    ):
        fail(
            "SCHEDULER_WORKTREE_SETUP_REPORT_INVALID",
            "expected_attempt must be a positive integer",
        )
    if event not in {"STARTED", "PROGRESS", "FAILED", "RETRY_CONFIRMED"}:
        fail(
            "SCHEDULER_WORKTREE_SETUP_EVENT_INVALID",
            "event must be STARTED, PROGRESS, FAILED, or RETRY_CONFIRMED",
            event=event,
        )
    if phase not in WORKTREE_SETUP_PHASES:
        fail(
            "SCHEDULER_WORKTREE_SETUP_PHASE_INVALID",
            "phase is not a supported worktree setup phase",
            phase=phase,
            allowed=sorted(WORKTREE_SETUP_PHASES),
        )
    if progress_percent is not None and (
        not isinstance(progress_percent, int)
        or isinstance(progress_percent, bool)
        or not 0 <= progress_percent <= 100
    ):
        fail(
            "SCHEDULER_WORKTREE_SETUP_REPORT_INVALID",
            "progress_percent must be an integer from 0 through 100",
        )
    if event == "FAILED" and (
        not isinstance(failure_code, str)
        or not failure_code.strip()
        or len(failure_code) > 96
    ):
        fail(
            "SCHEDULER_WORKTREE_SETUP_REPORT_INVALID",
            "FAILED requires a bounded failure_code",
        )
    if event == "RETRY_CONFIRMED" and (
        not isinstance(retry_request_id, str)
        or not retry_request_id.strip()
        or len(retry_request_id) > 128
    ):
        fail(
            "SCHEDULER_WORKTREE_SETUP_REPORT_INVALID",
            "RETRY_CONFIRMED requires a bounded retry_request_id for safe "
            "response replay",
        )

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    selection = repository.execution_selection(root_id)
    if selection is None or stored["status"] not in {
        "CHOICE_READY",
        "PREPARED",
    }:
        fail(
            "SCHEDULER_WORKTREE_SETUP_REPORT_CONFLICT",
            "Worktree setup progress requires a pending AUTOMATIC selection",
            rootId=root_id,
            status=stored["status"],
        )
    # Persist lease expiry before an invalid late progress call can roll back
    # its own transaction while reporting the stale-attempt error.
    repository.worktree_setup_reservations(root_id)
    updated = repository.report_worktree_setup(
        root_id,
        project_id=project_id.strip(),
        reservation_id=reservation_id.strip(),
        expected_attempt=expected_attempt,
        event=event,
        phase=phase,
        summary_zh=summary_zh.strip(),
        progress_percent=progress_percent,
        failure_code=(
            failure_code.strip()
            if isinstance(failure_code, str)
            else None
        ),
        confirmed_previous_attempt_stopped=(
            confirmed_previous_attempt_stopped is True
        ),
        confirmed_partial_state_reconciled=(
            confirmed_partial_state_reconciled is True
        ),
        retry_request_id=(
            retry_request_id.strip()
            if isinstance(retry_request_id, str)
            else None
        ),
    )
    reservation_states = repository.worktree_setup_reservations(root_id)
    reservation_states = [
        updated if item["projectId"] == project_id else item
        for item in reservation_states
    ]
    setup = _automatic_workspace_setup(
        control_root=root,
        workspace_root=workspace_root or root,
        hierarchy=stored["hierarchy"],
        host_adapter_id=host_adapter_id,
        repository=repository,
        reservation_states=reservation_states,
    )
    result = {
        "rootId": root_id,
        "projectId": project_id,
        "reservationId": reservation_id,
        "setupAttempt": updated["attempt"],
        "event": event,
        "setupProgress": _worktree_setup_progress(updated),
        "retryDispatchGranted": updated["retryDispatchGranted"],
        "retryRequestReplayed": updated["retryRequestReplayed"],
        "retryRequestId": updated.get("retryRequestId"),
        "selectionPreserved": True,
    }
    if setup is None:
        latest = next(
            (
                item
                for item in repository.worktree_setup_reservations(root_id)
                if item["projectId"] == project_id
            ),
            updated,
        )
        result["setupProgress"] = _worktree_setup_progress(
            latest,
            ready=latest.get("status") == "READY",
        )
        result["nextAction"] = (
            "CALL_WORKSPACE_STATUS_THEN_RESUME_EXECUTION_MODE"
        )
    else:
        result["worktreeSetup"] = setup
        result["nextAction"] = setup["nextAction"]
    return result


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
    _worktree_reservation_states: list[dict[str, Any]] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Continue one recorded AUTOMATIC choice in the ready worktree."""

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
    if stored["status"] == "FROZEN":
        run = repository.run(root_id)
        return {
            **run,
            "selection": "AUTOMATIC",
            "selectionAlreadyApplied": True,
            "confirmationRequired": False,
            "automaticDispatchRequested": True,
            "nextAction": "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH",
        }
    selection = repository.execution_selection(root_id)
    if selection is None:
        fail(
            "SCHEDULER_EXECUTION_SELECTION_MISSING",
            "Automatic execution cannot resume without the recorded human "
            "selection",
            rootId=root_id,
        )
    actual_workspace = workspace_root or root
    setup = _automatic_workspace_setup(
        control_root=root,
        workspace_root=actual_workspace,
        hierarchy=stored["hierarchy"],
        host_adapter_id=host_adapter_id,
        repository=repository,
        reservation_states=_worktree_reservation_states,
    )
    if setup is not None:
        return {
            "rootId": root_id,
            "status": stored["status"],
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
            "graphFingerprint": stored["graphFingerprint"],
            "selection": "AUTOMATIC",
            "selectionRecorded": True,
            "confirmationRequired": False,
            "automaticDispatchRequested": False,
            "worktreeSetup": setup,
            "selectionContinuation": {
                "tool": "resume_execution_mode",
                "confirmationRequired": False,
                "selectionPreserved": True,
            },
            "nextAction": setup["nextAction"],
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
    frozen = freeze_hierarchy(
        root=root,
        root_id=root_id,
        expected_delivery_revision=prepared["deliveryRevision"],
        expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
        authorized_project_ids=selection["authorizedProjectIds"],
        confirmed=True,
        confirmed_by=selection["confirmedBy"],
        explicit_dogfood=explicit_dogfood,
        now=now,
    )
    return {
        **frozen,
        "selection": "AUTOMATIC",
        "selectionAlreadyApplied": False,
        "confirmationRequired": False,
        "automaticDispatchRequested": True,
        **(
            {"verifiedProjectScopes": prepared["verifiedProjectScopes"]}
            if "verifiedProjectScopes" in prepared
            else {}
        ),
        "nextAction": "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH",
    }


def _assert_automatic_git_branch_available(
    hierarchy: dict[str, Any],
    workspace_root: str,
) -> None:
    """Refuse AUTOMATIC dispatch when a frozen branchRef is already checked
    out by a worktree this Delivery cannot adopt (e.g. the primary checkout).

    git forbids two worktrees on the same branch, so a Delivery that freezes
    a branchRef already held by the primary can never create its dedicated
    worktree and would stall the coordinator. Catch it before dispatch.
    """
    binding = hierarchy.get("delivery", {}).get("gitBinding")
    if not isinstance(binding, dict):
        return
    branch_ref = binding.get("branchRef")
    if not isinstance(branch_ref, str) or not branch_ref.strip():
        return
    workspace = Path(workspace_root).absolute().resolve(strict=True)
    if _branch_worktree_count(workspace, branch_ref) < 1:
        return
    if find_delivery_linked_worktree(workspace_root, binding) is not None:
        return  # an adoptable worktree on this branch already exists
    fail(
        "SCHEDULER_GIT_BRANCH_IN_USE_BY_OTHER_WORKTREE",
        "The frozen branchRef is already checked out by another worktree "
        "(commonly the primary checkout), so the AUTOMATIC dedicated "
        "worktree cannot be created on it. Use a new branch for this "
        "Delivery, or omit gitBinding so the Controller can suggest one.",
        branchRef=branch_ref,
    )


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
    worktree_requests = _automatic_worktree_requests(
        control_root=root,
        workspace_root=actual_workspace,
        hierarchy=hierarchy,
    )
    for request in worktree_requests:
        _assert_automatic_git_branch_available(
            {"delivery": {"gitBinding": request["gitBinding"]}},
            request["repositoryRoot"],
        )
    recorded = repository.record_automatic_selection(
        root_id,
        expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
        expected_graph_fingerprint=expected_graph_fingerprint,
        authorized_project_ids=authorized_project_ids or [],
        confirmed_by=confirmed_by.strip(),
        worktree_requests=worktree_requests,
    )
    resumed = resume_execution_mode(
        root=root,
        root_id=root_id,
        expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
        expected_graph_fingerprint=expected_graph_fingerprint,
        workspace_root=workspace_root or root,
        explicit_dogfood=explicit_dogfood,
        host_adapter_id=host_adapter_id,
        now=now,
        _worktree_reservation_states=recorded["worktreeReservations"],
    )
    return {
        **resumed,
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
    "report_worktree_setup",
    "resume_execution_mode",
    "select_execution_mode",
    "start_manual_handoff",
    "workspace_status",
)
