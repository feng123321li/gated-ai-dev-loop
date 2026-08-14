from __future__ import annotations

from .git_binding_common import (
    Any,
    MainlineSelection,
    Path,
    _branch_checkout_count,
    _checkout_topology,
    _commit,
    _git,
    _git_output,
    _mainline_commits,
    _merge_base,
    _optional_commit,
    _select_mainline,
    _working_tree_state,
    _workspace_provenance,
    fail,
    validate_git_binding,
)


def inspect_delivery_git_workspace(
    workspace_root: str,
    *,
    base_ref: str | None = None,
    confirmed_dirty_state_fingerprint: str | None = None,
    host_adapter_id: str | None = None,
) -> dict[str, Any] | None:
    """Discover the current feature branch and suggest a frozen binding."""

    workspace = Path(workspace_root).absolute().resolve(strict=True)
    if not (workspace / ".git").exists():
        return None
    top_level = Path(
        _git_output(workspace, "rev-parse", "--show-toplevel")
    ).resolve(strict=True)
    if top_level != workspace:
        fail(
            "SCHEDULER_GIT_CHECKOUT_MISMATCH",
            "Delivery workspace must be the Git checkout root",
        )
    symbolic = _git(
        workspace,
        "symbolic-ref",
        "--quiet",
        "HEAD",
        accepted=(0, 1),
    )
    head_commit = _commit(
        workspace,
        "HEAD",
        code="SCHEDULER_GIT_HEAD_INVALID",
        message="Delivery workspace HEAD is not a commit",
    )
    selection = _select_mainline(
        workspace,
        base_ref,
    )
    topology = _checkout_topology(workspace)
    working_tree = _working_tree_state(workspace)
    if symbolic.returncode != 0:
        base_commit = _merge_base(
            workspace,
            head_commit,
            selection.head_commit,
            branch_ref=None,
            base_ref=selection.branch_ref,
        )
        return {
            "gitWorkspace": {
                "role": "DETACHED_WORKSPACE",
                "headCommit": head_commit,
            },
            "workspacePreparation": {
                "state": "FEATURE_BRANCH_REQUIRED",
                "owner": "HOST",
                "nextAction": "CREATE_DELIVERY_FEATURE_BRANCH",
                "baseRef": selection.branch_ref,
                "baseCommit": base_commit,
                "integrationTarget": selection.branch_ref,
            },
            "workspaceProvenance": _workspace_provenance(
                workspace,
                topology=topology,
                selection=selection,
                base_commit=base_commit,
                host_adapter_id=host_adapter_id,
            ),
            "workingTree": working_tree,
        }
    full_ref = symbolic.stdout.strip()
    if not full_ref.startswith("refs/heads/"):
        fail(
            "SCHEDULER_GIT_BRANCH_MISMATCH",
            "Delivery workspace must use a local feature branch",
        )
    branch_ref = full_ref.removeprefix("refs/heads/")
    result: dict[str, Any] = {
        "gitWorkspace": {
            "branchRef": branch_ref,
            "headCommit": head_commit,
        },
        "workingTree": working_tree,
    }
    if branch_ref == selection.branch_ref or branch_ref in {"main", "master"}:
        result["gitWorkspace"]["role"] = "MAINLINE"
        result["workspacePreparation"] = {
            "state": "FEATURE_BRANCH_REQUIRED",
            "owner": "HOST",
            "nextAction": "CREATE_DELIVERY_FEATURE_BRANCH",
            "baseRef": selection.branch_ref,
            "baseCommit": selection.head_commit,
            "integrationTarget": selection.branch_ref,
        }
        result["workspaceProvenance"] = _workspace_provenance(
            workspace,
            topology=topology,
            selection=selection,
            base_commit=selection.head_commit,
            host_adapter_id=host_adapter_id,
        )
        return result
    base_commit = _merge_base(
        workspace,
        head_commit,
        selection.head_commit,
        branch_ref=branch_ref,
        base_ref=selection.branch_ref,
    )
    result["gitWorkspace"]["role"] = "UNBOUND_BRANCH"
    binding = {
        "branchRef": branch_ref,
        "baseRef": selection.branch_ref,
        "baseCommit": base_commit,
        "integrationTarget": selection.branch_ref,
    }
    result["workspaceProvenance"] = _workspace_provenance(
        workspace,
        topology=topology,
        selection=selection,
        base_commit=base_commit,
        host_adapter_id=host_adapter_id,
    )
    branch_checkout_count = _branch_checkout_count(workspace, branch_ref)
    if branch_checkout_count > 1:
        result["branchAdoption"] = {
            "state": "BRANCH_IN_USE_BY_OTHER_CHECKOUT",
            "nextAction": "CREATE_DELIVERY_FEATURE_BRANCH",
            "workingTreeClean": working_tree["clean"],
            "conflictingCheckoutCount": branch_checkout_count,
        }
        return result
    if working_tree["clean"]:
        if confirmed_dirty_state_fingerprint is not None:
            fail(
                "SCHEDULER_GIT_DIRTY_CONFIRMATION_INVALID",
                "The workspace is clean and has no dirty state to confirm",
            )
        result["branchAdoption"] = {
            "state": "READY",
            "nextAction": "USE_SUGGESTED_GIT_BINDING",
            "workingTreeClean": True,
        }
        result["suggestedGitBinding"] = binding
        return result
    dirty_fingerprint = working_tree["stateFingerprint"]
    if confirmed_dirty_state_fingerprint is None:
        result["branchAdoption"] = {
            "state": "DIRTY_CONFIRMATION_REQUIRED",
            "nextAction": "CONFIRM_CURRENT_DIFF_BELONGS_TO_DELIVERY",
            "workingTreeClean": False,
            "dirtyStateFingerprint": dirty_fingerprint,
        }
        result["candidateGitBinding"] = binding
        return result
    if confirmed_dirty_state_fingerprint != dirty_fingerprint:
        fail(
            "SCHEDULER_GIT_DIRTY_STATE_CHANGED",
            "The workspace changed after its dirty state was presented",
            expectedDirtyStateFingerprint=(
                confirmed_dirty_state_fingerprint
            ),
            actualDirtyStateFingerprint=dirty_fingerprint,
        )
    result["branchAdoption"] = {
        "state": "READY_WITH_CONFIRMED_CHANGES",
        "nextAction": "USE_SUGGESTED_GIT_BINDING",
        "workingTreeClean": False,
        "dirtyStateFingerprint": dirty_fingerprint,
    }
    result["suggestedGitBinding"] = binding
    return result

def inspect_frozen_git_workspace_provenance(
    workspace_root: str,
    binding: object,
    *,
    host_adapter_id: str | None = None,
) -> dict[str, Any]:
    """Report current workspace provenance for a frozen Git binding."""

    workspace = Path(workspace_root).absolute().resolve(strict=True)
    normalized = validate_git_binding(binding)
    selected = _select_mainline(workspace, normalized["baseRef"])
    selection = MainlineSelection(
        branch_ref=selected.branch_ref,
        head_commit=selected.head_commit,
        source="FROZEN_GIT_BINDING",
    )
    provenance = _workspace_provenance(
        workspace,
        topology=_checkout_topology(workspace),
        selection=selection,
        base_commit=normalized["baseCommit"],
        host_adapter_id=host_adapter_id,
    )
    provenance["integrationTarget"] = normalized["integrationTarget"]
    result: dict[str, Any] = {
        "workspaceProvenance": provenance,
        "workingTree": _working_tree_state(workspace),
    }
    frozen_base = normalized["baseCommit"]
    current_head = selected.head_commit
    if (
        current_head != frozen_base
        and _git(
            workspace,
            "merge-base",
            "--is-ancestor",
            frozen_base,
            current_head,
            accepted=(0, 1),
        ).returncode
        == 0
    ):
        # The frozen base has fallen behind the integration target (another
        # Delivery merged past it). Surface a recoverable advisory; the host
        # rebases the Delivery branch onto the current base, then prepares a
        # Delivery revision to re-pin baseCommit. The controller does no git.
        result["workspaceRebase"] = {
            "required": True,
            "frozenBaseCommit": frozen_base,
            "currentBaseCommit": current_head,
            "integrationTarget": normalized["integrationTarget"],
            "nextAction": (
                "REBASE_DELIVERY_BRANCH_ONTO_CURRENT_BASE_THEN_"
                "PREPARE_DELIVERY_REVISION"
            ),
        }
    return result

def enumerate_local_feature_branches(workspace_root: str) -> list[dict[str, Any]]:
    """List local feature branches adoptable as a Delivery baseline.

    Enumerates ``refs/heads/`` only (never remote refs), excludes the selected
    mainline plus ``main``/``master``, and annotates each branch with its fork
    point off the mainline and its current checkout count. Read-only: the
    controller performs no Git writes; this feeds the ``DEVELOPMENT_BASELINE``
    selector.
    """

    workspace = Path(workspace_root).absolute().resolve(strict=True)
    selection = _select_mainline(workspace, None)
    mainline_ref = selection.branch_ref
    mainline_head = selection.head_commit
    raw = _git_output(
        workspace,
        "for-each-ref",
        "--format=%(refname:short)|%(objectname)",
        "refs/heads/",
    )
    candidates: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line:
            continue
        # objectname is pure hex (no '|'), so rpartition survives a '|' in a
        # branch name.
        name, _, head_commit = line.rpartition("|")
        if not head_commit:
            continue
        if name == mainline_ref or name in {"main", "master"}:
            continue
        merge_base = _git(
            workspace,
            "merge-base",
            head_commit,
            mainline_head,
            accepted=(0, 1),
        )
        if merge_base.returncode != 0 or not merge_base.stdout.strip():
            # Shares no history with the mainline; not a valid fork point.
            continue
        checkout_count = _branch_checkout_count(workspace, name)
        candidates.append(
            {
                "branchRef": name,
                "headCommit": head_commit,
                "baseRef": mainline_ref,
                "baseCommit": merge_base.stdout.strip(),
                "integrationTarget": mainline_ref,
                "checkoutCount": checkout_count,
                "adoptable": checkout_count <= 1,
            }
        )
    candidates.sort(key=lambda item: item["branchRef"])
    return candidates

def resolve_branch_binding(
    workspace_root: str,
    *,
    branch_ref: str,
    base_ref: str | None = None,
) -> dict[str, str]:
    """Compute the frozen Git binding for a chosen development baseline.

    ``branch_ref`` may name an existing local branch (its merge-base with the
    selected base becomes ``baseCommit``) or a brand-new branch the host will
    create from that base (``baseCommit`` is pinned to the selected base HEAD).
    The base is normally main/master but may be an explicitly confirmed parent
    feature branch for a stacked Delivery.
    Read-only: the controller never creates branches or worktrees.
    """

    workspace = Path(workspace_root).absolute().resolve(strict=True)
    selection = _select_mainline(workspace, base_ref)
    existing = _optional_commit(workspace, f"refs/heads/{branch_ref}")
    if existing is not None:
        base_commit = _merge_base(
            workspace,
            existing,
            selection.head_commit,
            branch_ref=branch_ref,
            base_ref=selection.branch_ref,
        )
    else:
        base_commit = selection.head_commit
    return {
        "branchRef": branch_ref,
        "baseRef": selection.branch_ref,
        "baseCommit": base_commit,
        "integrationTarget": selection.branch_ref,
    }

def verify_delivery_git_binding(
    workspace_root: str,
    binding: object,
    *,
    preparing: bool,
) -> dict[str, Any] | None:
    """Verify one immutable Delivery binding using read-only local Git."""

    workspace = Path(workspace_root).absolute().resolve(strict=True)
    has_git_metadata = (workspace / ".git").exists()
    if not has_git_metadata:
        if binding is not None:
            fail(
                "SCHEDULER_GIT_CHECKOUT_REQUIRED",
                "A Delivery Git binding requires a Git checkout",
            )
        return None
    if binding is None:
        fail(
            "SCHEDULER_GIT_BINDING_REQUIRED",
            "A Git Delivery must declare its feature branch and immutable "
            "base binding",
        )
    normalized = validate_git_binding(binding)
    top_level = Path(
        _git_output(workspace, "rev-parse", "--show-toplevel")
    ).resolve(strict=True)
    if top_level != workspace:
        fail(
            "SCHEDULER_GIT_CHECKOUT_MISMATCH",
            "Delivery workspace must be the Git checkout root",
        )
    symbolic = _git(
        workspace,
        "symbolic-ref",
        "--quiet",
        "HEAD",
        accepted=(0, 1),
    )
    if symbolic.returncode != 0:
        fail(
            "SCHEDULER_GIT_DETACHED_HEAD",
            "Delivery workspace must remain on its bound feature branch",
        )
    actual_full_ref = symbolic.stdout.strip()
    expected_full_ref = f"refs/heads/{normalized['branchRef']}"
    if actual_full_ref != expected_full_ref:
        fail(
            "SCHEDULER_GIT_BRANCH_MISMATCH",
            "Delivery workspace is checked out on another branch",
            expectedBranchRef=normalized["branchRef"],
            actualBranchRef=(
                actual_full_ref.removeprefix("refs/heads/")
            ),
        )
    head_commit = _commit(
        workspace,
        "HEAD",
        code="SCHEDULER_GIT_HEAD_INVALID",
        message="Delivery workspace HEAD is not a commit",
    )
    base_commit = _commit(
        workspace,
        normalized["baseCommit"],
        code="SCHEDULER_GIT_BASE_INVALID",
        message="Delivery baseCommit does not exist",
    )
    if base_commit != normalized["baseCommit"]:
        fail(
            "SCHEDULER_GIT_BASE_INVALID",
            "baseCommit must be the repository's full immutable object ID",
            baseCommit=normalized["baseCommit"],
            resolvedCommit=base_commit,
        )
    ancestor = _git(
        workspace,
        "merge-base",
        "--is-ancestor",
        base_commit,
        head_commit,
        accepted=(0, 1),
    )
    if ancestor.returncode != 0:
        fail(
            "SCHEDULER_GIT_BASE_INVALID",
            "Delivery feature branch does not inherit baseCommit",
            baseCommit=base_commit,
            headCommit=head_commit,
        )
    mainline_commits = _mainline_commits(
        workspace,
        normalized["baseRef"],
    )
    mainline_contains_base = any(
        _git(
            workspace,
            "merge-base",
            "--is-ancestor",
            base_commit,
            mainline_commit,
            accepted=(0, 1),
        ).returncode
        == 0
        for mainline_commit in mainline_commits
    )
    if not mainline_contains_base:
        fail(
            "SCHEDULER_GIT_BASE_INVALID",
            "No local or origin baseRef contains baseCommit",
            baseCommit=base_commit,
            baseRef=normalized["baseRef"],
        )
    if preparing:
        merge_bases = tuple(
            dict.fromkeys(
                _git_output(
                    workspace,
                    "merge-base",
                    head_commit,
                    mainline_commit,
                )
                for mainline_commit in mainline_commits
            )
        )
        if base_commit not in merge_bases:
            fail(
                "SCHEDULER_GIT_BASE_INVALID",
                "baseCommit must be the Delivery branch fork point from "
                "baseRef when the Delivery is prepared",
                expectedBaseCommits=list(merge_bases),
                actualBaseCommit=base_commit,
            )
    return {
        "branchRef": normalized["branchRef"],
        "headCommit": head_commit,
        "baseRef": normalized["baseRef"],
        "baseCommit": base_commit,
        "baseCommitIsAncestor": True,
        "integrationTarget": normalized["integrationTarget"],
    }
