from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import fail
from .fs_safe import atomic_write, safe_path
from .git_binding import (
    _branch_worktree_count,
    find_delivery_linked_worktree,
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
        selection = repository.execution_selection(selected_root_id)
        if selection is not None:
            result["executionSelection"] = selection
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
            if selection is not None:
                recorded_setup = _automatic_workspace_setup(
                    workspace_root=workspace_root or root,
                    hierarchy=stored["hierarchy"],
                    host_adapter_id=host_adapter_id,
                )
                if recorded_setup is not None:
                    result["worktreeSetup"] = recorded_setup
                    result["nextAction"] = recorded_setup["nextAction"]
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


def _automatic_workspace_setup(
    *,
    workspace_root: str,
    hierarchy: dict[str, Any],
    host_adapter_id: str | None,
) -> dict[str, Any] | None:
    git_binding = hierarchy["delivery"].get("gitBinding")
    discovery = inspect_delivery_git_workspace(
        workspace_root,
        base_ref=(
            git_binding.get("baseRef")
            if isinstance(git_binding, dict)
            else None
        ),
        host_adapter_id=host_adapter_id,
    )
    if not isinstance(discovery, dict):
        return None
    setup = discovery.get("worktreeSetup")
    if not isinstance(setup, dict):
        return None
    result = {
        **setup,
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
    }
    if setup.get("state") != "DEDICATED_WORKTREE_REQUIRED":
        return result

    delivery_id = hierarchy["delivery"]["id"]
    hierarchy_value = hierarchy_fingerprint(hierarchy)
    graph = compile_delivery_graph(
        hierarchy,
        hierarchy_fingerprint=hierarchy_value,
    )
    graph_value = graph_fingerprint(graph)
    host_operations = {
        "claude-code": "CREATE_CLAUDE_BACKGROUND_DELIVERY_AGENT",
        "codex": "CREATE_CODEX_PROJECT_TASK",
    }
    host_operation = host_operations.get(
        host_adapter_id or "",
        "CREATE_HOST_NATIVE_WORKTREE_TASK",
    )
    expected_binding = hierarchy["delivery"].get("gitBinding")
    existing_worktree = find_delivery_linked_worktree(
        workspace_root,
        expected_binding,
    )
    worktree_name = (
        "ld-"
        + "".join(
            character if character.isalnum() else "-"
            for character in delivery_id.casefold()
        ).strip("-")[:48]
        + "-"
        + hierarchy_value[:10]
    )
    prompt = (
        f"Coordinate automatic Delivery {delivery_id} in the stable "
        "Delivery worktree supplied by the host. Call workspace_status"
        f"(root_id={delivery_id}), complete only the returned host-owned "
        "feature-branch action when one is still required, then call "
        f"resume_execution_mode(root_id={delivery_id}, "
        f"expected_hierarchy_fingerprint={hierarchy_value}, "
        f"expected_graph_fingerprint={graph_value}). Do not ask for "
        "execution mode again. Remain the background Delivery coordinator: "
        "consume graph_frontier, create independent native receiver Agents "
        "for each reservation, and keep this same worktree for TASK and "
        "Review Loops. Never start another top-level CLI session and never "
        "use EnterWorktree inside a receiver. The main conversation only "
        "monitors progress from the preserved coordinator checkout."
    )
    result["hostDispatch"] = {
        "action": "CREATE_HOST_NATIVE_WORKTREE_TASK",
        "hostAdapterId": host_adapter_id,
        "hostOperation": host_operation,
        "launchPolicy": "IMMEDIATE",
        "environment": "worktree",
        "deliveryId": delivery_id,
        "title": f"Delivery {delivery_id}",
        "idempotencyKey": (
            f"delivery-worktree:{delivery_id}:{hierarchy_value}"
        ),
        "prompt": prompt,
        "baseRef": setup["baseRef"],
        "baseCommit": setup["baseCommit"],
        "integrationTarget": setup["integrationTarget"],
        "manualDirectoryChangeRequired": False,
        "coordinatorCheckoutPolicy": "PRESERVE_CURRENT_CHECKOUT",
        "stableDeliveryWorkspace": True,
        "requiresNewTopLevelSession": False,
        "manualSessionLaunchAllowed": False,
        "sameSessionEnterWorktreeSupported": True,
        "mainConversationRole": "MONITOR_ONLY",
        "worktreeName": worktree_name,
        "existingWorktreeRoot": existing_worktree,
        "agentDispatch": (
            {
                "agentType": (
                    "layered-delivery:delivery-coordinator"
                ),
                "name": worktree_name,
                "runInBackground": True,
                "reusePolicy": "RESUME_BY_NAME",
                "workspaceEntry": (
                    "ENTER_EXISTING_WORKTREE"
                    if existing_worktree is not None
                    else "CREATE_LINKED_WORKTREE_THEN_ENTER"
                ),
                "returnMainConversationToCoordinatorCheckout": True,
            }
            if host_adapter_id == "claude-code"
            else {
                "taskEnvironment": "worktree",
                "runInBackground": True,
                "reusePolicy": "RESUME_PROJECT_TASK",
            }
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
    return result


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
    recorded_selection = repository.execution_selection(
        normalized["delivery"]["id"]
    )
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
            else "RESUME_RECORDED_AUTOMATIC_SELECTION_IN_READY_WORKTREE"
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
        result["executionChoice"] = execution_choice_contract(
            host_adapter_id
        )
    if recorded_selection is not None:
        result["executionSelection"] = recorded_selection
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
        workspace_root=actual_workspace,
        hierarchy=stored["hierarchy"],
        host_adapter_id=host_adapter_id,
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
    _assert_automatic_git_branch_available(hierarchy, workspace_root or root)
    repository.record_automatic_selection(
        root_id,
        expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
        expected_graph_fingerprint=expected_graph_fingerprint,
        authorized_project_ids=authorized_project_ids or [],
        confirmed_by=confirmed_by.strip(),
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
    )
    return {
        **resumed,
        "selectionRecorded": True,
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
