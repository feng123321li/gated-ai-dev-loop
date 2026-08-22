from __future__ import annotations

from .planning_common import (
    Any,
    Path,
    SchedulerRepository,
    development_baseline_contract,
    enumerate_local_feature_branches,
    execution_choice_contract,
    fail,
    fingerprint,
    git_repository_identity,
    inspect_delivery_git_workspace,
    validate_git_binding,
)


def _inject_remembered_baseline(
    normalized: dict[str, Any],
    repository: SchedulerRepository,
    root_id: str,
) -> bool:
    """Inject a remembered baseline when the hierarchy omits gitBinding."""

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
    """Reject an incomplete multi-repository Git baseline early."""

    scopes = hierarchy["delivery"].get("projectScopes")
    if scopes is None:
        return
    git_scopes: list[dict[str, Any]] = []
    for scope in scopes:
        try:
            scope_root = Path(scope["workspaceRoot"]).resolve(strict=True)
        except (FileNotFoundError, OSError):
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
                expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
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


__all__ = (
    "_assert_project_baselines_complete",
    "_attach_pending_interaction",
    "_baseline_discovery",
    "_inject_remembered_baseline",
    "_pending_interaction",
)
