from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Any

from .errors import fail
from .jsonio import fingerprint
from .model_core import validate_git_binding


GIT_TIMEOUT_SECONDS = 10
EXCLUSIVE_PRIMARY_HOST_ADAPTERS = frozenset()
HOST_NATIVE_LINKED_WORKTREE_ADAPTERS = frozenset(
    {"claude-code", "codex"}
)


@dataclass(frozen=True)
class MainlineSelection:
    branch_ref: str
    head_commit: str
    source: str


def _git(
    workspace: Path,
    *arguments: str,
    accepted: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        completed = subprocess.run(
            ["git", "-C", str(workspace), *arguments],
            capture_output=True,
            env=environment,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        fail(
            "SCHEDULER_GIT_UNAVAILABLE",
            "Git is required to verify this Delivery binding",
        )
    except subprocess.TimeoutExpired:
        fail(
            "SCHEDULER_GIT_UNAVAILABLE",
            "Git binding verification timed out",
        )
    if completed.returncode not in accepted:
        fail(
            "SCHEDULER_GIT_COMMAND_FAILED",
            "Git could not inspect the Delivery worktree",
            gitArguments=list(arguments),
            returnCode=completed.returncode,
        )
    return completed


def _git_output(workspace: Path, *arguments: str) -> str:
    return _git(workspace, *arguments).stdout.strip()


def _commit(
    workspace: Path,
    revision: str,
    *,
    code: str,
    message: str,
) -> str:
    completed = _git(
        workspace,
        "rev-parse",
        "--verify",
        f"{revision}^{{commit}}",
        accepted=(0, 128),
    )
    if completed.returncode != 0:
        fail(code, message, revision=revision)
    return completed.stdout.strip()


def _optional_commit(workspace: Path, revision: str) -> str | None:
    completed = _git(
        workspace,
        "rev-parse",
        "--verify",
        f"{revision}^{{commit}}",
        accepted=(0, 1, 128),
    )
    return (
        completed.stdout.strip()
        if completed.returncode == 0
        else None
    )


def _origin_default_branch(
    workspace: Path,
) -> tuple[str, str] | None:
    symbolic = _git(
        workspace,
        "symbolic-ref",
        "--quiet",
        "refs/remotes/origin/HEAD",
        accepted=(0, 1, 128),
    )
    if symbolic.returncode != 0:
        return None
    full_ref = symbolic.stdout.strip()
    prefix = "refs/remotes/origin/"
    if not full_ref.startswith(prefix):
        return None
    branch_ref = full_ref.removeprefix(prefix)
    commit = _optional_commit(workspace, full_ref)
    if commit is None:
        return None
    return branch_ref, commit


def _select_mainline(
    workspace: Path,
    base_ref: str | None,
) -> MainlineSelection:
    remote_default = _origin_default_branch(workspace)
    if base_ref is None and remote_default is not None:
        return MainlineSelection(
            branch_ref=remote_default[0],
            head_commit=remote_default[1],
            source="ORIGIN_HEAD",
        )
    if base_ref is not None:
        remote_commit = _optional_commit(
            workspace,
            f"refs/remotes/origin/{base_ref}",
        )
        if remote_commit is not None:
            return MainlineSelection(
                branch_ref=base_ref,
                head_commit=remote_commit,
                source="HOST_SELECTED",
            )
        local_commit = _optional_commit(
            workspace,
            f"refs/heads/{base_ref}",
        )
        if local_commit is not None:
            return MainlineSelection(
                branch_ref=base_ref,
                head_commit=local_commit,
                source="HOST_SELECTED",
            )
        fail(
            "SCHEDULER_GIT_BASE_INVALID",
            "Selected mainline branch does not exist locally or as an "
            "origin tracking ref",
            baseRef=base_ref,
        )
    for candidate in ("main", "master"):
        candidate_commit = _optional_commit(
            workspace,
            f"refs/heads/{candidate}",
        )
        if candidate_commit is not None:
            return MainlineSelection(
                branch_ref=candidate,
                head_commit=candidate_commit,
                source=f"LOCAL_{candidate.upper()}_FALLBACK",
            )
    fail(
        "SCHEDULER_GIT_BASE_INVALID",
        "No valid origin default branch, local main, or local master exists",
    )


def _merge_base(
    workspace: Path,
    head_commit: str,
    base_commit: str,
    *,
    branch_ref: str | None,
    base_ref: str,
) -> str:
    merge_base = _git(
        workspace,
        "merge-base",
        head_commit,
        base_commit,
        accepted=(0, 1),
    )
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        fail(
            "SCHEDULER_GIT_BASE_INVALID",
            "Delivery workspace does not share a base with the mainline "
            "branch",
            branchRef=branch_ref,
            baseRef=base_ref,
        )
    return merge_base.stdout.strip()


def _mainline_commits(
    workspace: Path,
    base_ref: str,
) -> tuple[str, ...]:
    revisions = (
        f"refs/heads/{base_ref}",
        f"refs/remotes/origin/{base_ref}",
    )
    commits = tuple(
        dict.fromkeys(
            commit
            for revision in revisions
            if (commit := _optional_commit(workspace, revision)) is not None
        )
    )
    if not commits:
        fail(
            "SCHEDULER_GIT_BASE_INVALID",
            "Delivery baseRef does not resolve to a local branch or origin "
            "tracking branch",
            baseRef=base_ref,
        )
    return commits


def _absolute_git_path(workspace: Path, *arguments: str) -> Path:
    value = Path(_git_output(workspace, *arguments))
    if not value.is_absolute():
        value = workspace / value
    return value.resolve(strict=True)


def _worktree_topology(workspace: Path) -> str:
    git_dir = _absolute_git_path(
        workspace,
        "rev-parse",
        "--absolute-git-dir",
    )
    common_dir = _absolute_git_path(
        workspace,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
    )
    return (
        "LINKED_WORKTREE"
        if git_dir != common_dir
        else "PRIMARY_WORKTREE"
    )


def _working_tree_state(workspace: Path) -> dict[str, Any]:
    pathspec = (
        ".",
        ":(exclude).layered-delivery",
        ":(exclude).layered-delivery/**",
    )
    porcelain = _git(
        workspace,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        *pathspec,
    ).stdout
    changes = porcelain.splitlines() if porcelain else []
    changed_paths: set[str] = set()
    for arguments in (
        ("diff", "--name-only", "-z", "--", *pathspec),
        ("diff", "--cached", "--name-only", "-z", "--", *pathspec),
        (
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *pathspec,
        ),
    ):
        output = _git(workspace, *arguments).stdout
        changed_paths.update(path for path in output.split("\0") if path)

    content_state: list[dict[str, Any]] = []
    for relative_path in sorted(changed_paths):
        candidate = workspace / relative_path
        worktree_blob = None
        if os.path.lexists(candidate):
            worktree_blob = _git_output(
                workspace,
                "hash-object",
                "--no-filters",
                "--",
                relative_path,
            )
        index_state = _git(
            workspace,
            "ls-files",
            "--stage",
            "-z",
            "--",
            relative_path,
        ).stdout
        content_state.append(
            {
                "path": relative_path,
                "worktreeBlob": worktree_blob,
                "indexState": index_state,
            }
        )
    return {
        "clean": not changes,
        "changeCount": len(changes),
        "stateFingerprint": fingerprint(
            {
                "porcelain": porcelain,
                "contentState": content_state,
            }
        ),
    }


def _branch_worktree_count(workspace: Path, branch_ref: str) -> int:
    expected = f"branch refs/heads/{branch_ref}"
    return sum(
        line == expected
        for line in _git_output(
            workspace,
            "worktree",
            "list",
            "--porcelain",
        ).splitlines()
    )


def _worktree_provenance(
    workspace: Path,
    *,
    topology: str,
    selection: MainlineSelection,
    base_commit: str,
    host_adapter_id: str | None,
) -> dict[str, Any]:
    if topology == "LINKED_WORKTREE":
        strategy = "HOST_NATIVE_LINKED_WORKTREE"
    elif host_adapter_id in HOST_NATIVE_LINKED_WORKTREE_ADAPTERS:
        strategy = "HOST_NATIVE_LINKED_WORKTREE"
    else:
        strategy = "PRIMARY_CHECKOUT"
    return {
        "strategy": strategy,
        "hostAdapterId": host_adapter_id,
        "workspaceRoot": str(workspace),
        "topology": topology,
        "selectionSource": selection.source,
        "baseRef": selection.branch_ref,
        "baseCommit": base_commit,
        "baseHeadCommit": selection.head_commit,
        "integrationTarget": selection.branch_ref,
    }


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
            "SCHEDULER_GIT_WORKTREE_MISMATCH",
            "Delivery workspace must be the Git worktree root",
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
        message="Delivery worktree HEAD is not a commit",
    )
    selection = _select_mainline(
        workspace,
        base_ref,
    )
    topology = _worktree_topology(workspace)
    working_tree = _working_tree_state(workspace)
    exclusive_primary = (
        topology == "PRIMARY_WORKTREE"
        and host_adapter_id in EXCLUSIVE_PRIMARY_HOST_ADAPTERS
    )
    if topology == "PRIMARY_WORKTREE" and not exclusive_primary:
        if symbolic.returncode == 0:
            full_ref = symbolic.stdout.strip()
            if not full_ref.startswith("refs/heads/"):
                fail(
                    "SCHEDULER_GIT_BRANCH_MISMATCH",
                    "Delivery worktree must use a local branch",
                )
            branch_ref = full_ref.removeprefix("refs/heads/")
            git_workspace = {
                "branchRef": branch_ref,
                "headCommit": head_commit,
                "role": (
                    "MAINLINE"
                    if branch_ref == selection.branch_ref
                    or branch_ref in {"main", "master"}
                    else "UNBOUND_BRANCH"
                ),
            }
        else:
            git_workspace = {
                "role": "DETACHED_PRIMARY",
                "headCommit": head_commit,
            }
        return {
            "gitWorkspace": git_workspace,
            "worktreeSetup": {
                "state": "DEDICATED_WORKTREE_REQUIRED",
                "owner": "HOST",
                "nextAction": "CREATE_INDEPENDENT_WORKTREE_TASK",
                "baseRef": selection.branch_ref,
                "baseCommit": selection.head_commit,
                "integrationTarget": selection.branch_ref,
            },
            "worktreeProvenance": _worktree_provenance(
                workspace,
                topology=topology,
                selection=selection,
                base_commit=selection.head_commit,
                host_adapter_id=host_adapter_id,
            ),
            "workingTree": working_tree,
        }
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
                "role": (
                    "DETACHED_PRIMARY"
                    if exclusive_primary
                    else "DETACHED_WORKTREE"
                ),
                "headCommit": head_commit,
            },
            "worktreeSetup": {
                "state": "FEATURE_BRANCH_REQUIRED",
                "owner": "HOST",
                "nextAction": "CREATE_DELIVERY_FEATURE_BRANCH",
                "baseRef": selection.branch_ref,
                "baseCommit": base_commit,
                "integrationTarget": selection.branch_ref,
            },
            "worktreeProvenance": _worktree_provenance(
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
            "Delivery worktree must use a local feature branch",
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
        result["worktreeSetup"] = {
            "state": "FEATURE_BRANCH_REQUIRED",
            "owner": "HOST",
            "nextAction": "CREATE_DELIVERY_FEATURE_BRANCH",
            "baseRef": selection.branch_ref,
            "baseCommit": selection.head_commit,
            "integrationTarget": selection.branch_ref,
        }
        result["worktreeProvenance"] = _worktree_provenance(
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
    result["gitWorkspace"]["role"] = "DELIVERY_FEATURE"
    binding = {
        "branchRef": branch_ref,
        "baseRef": selection.branch_ref,
        "baseCommit": base_commit,
        "integrationTarget": selection.branch_ref,
    }
    result["worktreeProvenance"] = _worktree_provenance(
        workspace,
        topology=topology,
        selection=selection,
        base_commit=base_commit,
        host_adapter_id=host_adapter_id,
    )
    branch_worktree_count = _branch_worktree_count(workspace, branch_ref)
    if branch_worktree_count > 1:
        result["branchAdoption"] = {
            "state": "BRANCH_IN_USE_BY_OTHER_WORKTREE",
            "nextAction": "CREATE_DELIVERY_FEATURE_BRANCH",
            "workingTreeClean": working_tree["clean"],
            "conflictingWorktreeCount": branch_worktree_count,
        }
        return result
    if working_tree["clean"]:
        if confirmed_dirty_state_fingerprint is not None:
            fail(
                "SCHEDULER_GIT_DIRTY_CONFIRMATION_INVALID",
                "The worktree is clean and has no dirty state to confirm",
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
            "The worktree changed after its dirty state was presented",
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
    """Report current worktree provenance for a frozen Git binding."""

    workspace = Path(workspace_root).absolute().resolve(strict=True)
    normalized = validate_git_binding(binding)
    selected = _select_mainline(workspace, normalized["baseRef"])
    selection = MainlineSelection(
        branch_ref=selected.branch_ref,
        head_commit=selected.head_commit,
        source="FROZEN_GIT_BINDING",
    )
    provenance = _worktree_provenance(
        workspace,
        topology=_worktree_topology(workspace),
        selection=selection,
        base_commit=normalized["baseCommit"],
        host_adapter_id=host_adapter_id,
    )
    provenance["integrationTarget"] = normalized["integrationTarget"]
    result: dict[str, Any] = {
        "worktreeProvenance": provenance,
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
        # rebases the worktree onto the current base, then prepares a
        # Delivery revision to re-pin baseCommit. The controller does no git.
        result["worktreeRebase"] = {
            "required": True,
            "frozenBaseCommit": frozen_base,
            "currentBaseCommit": current_head,
            "integrationTarget": normalized["integrationTarget"],
            "nextAction": (
                "REBASE_DELIVERY_WORKTREE_ONTO_CURRENT_BASE_THEN_"
                "PREPARE_DELIVERY_REVISION"
            ),
        }
    return result


def enumerate_local_feature_branches(workspace_root: str) -> list[dict[str, Any]]:
    """List local feature branches adoptable as a Delivery baseline.

    Enumerates ``refs/heads/`` only (never remote refs), excludes the selected
    mainline plus ``main``/``master``, and annotates each branch with its fork
    point off the mainline and its current worktree count. Read-only: the
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
        worktree_count = _branch_worktree_count(workspace, name)
        candidates.append(
            {
                "branchRef": name,
                "headCommit": head_commit,
                "baseRef": mainline_ref,
                "baseCommit": merge_base.stdout.strip(),
                "integrationTarget": mainline_ref,
                "worktreeCount": worktree_count,
                "adoptable": worktree_count <= 1,
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
                "SCHEDULER_GIT_WORKTREE_REQUIRED",
                "A Delivery Git binding requires a Git worktree",
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
            "SCHEDULER_GIT_WORKTREE_MISMATCH",
            "Delivery workspace must be the Git worktree root",
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
            "Delivery worktree must remain on its bound feature branch",
        )
    actual_full_ref = symbolic.stdout.strip()
    expected_full_ref = f"refs/heads/{normalized['branchRef']}"
    if actual_full_ref != expected_full_ref:
        fail(
            "SCHEDULER_GIT_BRANCH_MISMATCH",
            "Delivery worktree is checked out on another branch",
            expectedBranchRef=normalized["branchRef"],
            actualBranchRef=(
                actual_full_ref.removeprefix("refs/heads/")
            ),
        )
    head_commit = _commit(
        workspace,
        "HEAD",
        code="SCHEDULER_GIT_HEAD_INVALID",
        message="Delivery worktree HEAD is not a commit",
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


def _git_common_directory(workspace: Path) -> Path | None:
    if not (workspace / ".git").exists():
        return None
    common = Path(_git_output(workspace, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = workspace / common
    return common.resolve(strict=True)


def git_repository_identity(workspace_root: str) -> str | None:
    """Return a stable local identity shared by all worktrees of one repo."""

    workspace = Path(workspace_root).absolute().resolve(strict=True)
    common = _git_common_directory(workspace)
    if common is None:
        return None
    return fingerprint(
        {"gitCommonDirectory": os.path.normcase(str(common))}
    )


def git_repository_lineage_identity(workspace_root: str) -> str | None:
    """Return a path-independent identity for the history containing HEAD."""

    workspace = Path(workspace_root).absolute().resolve(strict=True)
    if _git_common_directory(workspace) is None:
        return None
    roots_result = _git(
        workspace,
        "rev-list",
        "--max-parents=0",
        "HEAD",
        accepted=(0, 128),
    )
    root_commits = sorted(
        {
            commit.strip().lower()
            for commit in roots_result.stdout.splitlines()
            if commit.strip()
        }
    )
    if roots_result.returncode != 0 or not root_commits:
        return None
    return fingerprint({"rootCommits": root_commits})


def git_worktree_identity(
    workspace_root: str,
) -> dict[str, str] | None:
    """Return a path-independent repository lineage + branch identity."""

    workspace = Path(workspace_root).absolute().resolve(strict=True)
    repository_key = git_repository_lineage_identity(str(workspace))
    if repository_key is None:
        return None
    symbolic = _git(
        workspace,
        "symbolic-ref",
        "--quiet",
        "HEAD",
        accepted=(0, 1),
    )
    if symbolic.returncode != 0:
        return None
    full_ref = symbolic.stdout.strip()
    prefix = "refs/heads/"
    if not full_ref.startswith(prefix) or full_ref == prefix:
        return None
    return {
        "repositoryKey": repository_key,
        "branchRef": full_ref.removeprefix(prefix),
    }


def _branch_worktrees(
    repository_workspace: Path,
    branch_ref: str,
) -> list[Path]:
    output = _git_output(
        repository_workspace,
        "worktree",
        "list",
        "--porcelain",
    )
    expected_ref = f"refs/heads/{branch_ref}"
    worktrees: list[Path] = []
    current: Path | None = None
    for line in [*output.splitlines(), ""]:
        if line.startswith("worktree "):
            current = Path(line.removeprefix("worktree ")).resolve(
                strict=True
            )
        elif line == f"branch {expected_ref}" and current is not None:
            worktrees.append(current)
        elif not line:
            current = None
    return worktrees


def find_delivery_linked_worktree(
    workspace_root: str,
    binding: object,
) -> str | None:
    """Find the unique existing linked worktree for a Delivery branch."""

    if binding is None:
        return None
    normalized = validate_git_binding(binding)
    workspace = Path(workspace_root).absolute().resolve(strict=True)
    matches = [
        candidate
        for candidate in _branch_worktrees(
            workspace,
            normalized["branchRef"],
        )
        if candidate != workspace
    ]
    if len(matches) != 1:
        return None
    verify_delivery_git_binding(
        str(matches[0]),
        normalized,
        preparing=True,
    )
    return str(matches[0])


def verify_delivery_project_scopes(
    workspace_root: str,
    delivery: dict[str, Any],
    *,
    preparing: bool,
) -> list[dict[str, Any]]:
    """Verify the exact project roots frozen into one Delivery revision."""

    scopes = delivery.get("projectScopes")
    if scopes is None:
        return []
    primary_root = Path(workspace_root).absolute().resolve(strict=True)
    resolved_scopes: list[tuple[dict[str, Any], Path]] = []
    for scope in scopes:
        try:
            project_root = Path(scope["workspaceRoot"]).resolve(
                strict=True
            )
        except (FileNotFoundError, OSError):
            fail(
                "SCHEDULER_PROJECT_SCOPE_INVALID",
                "A project workspace root must exist before prepare",
                projectId=scope["id"],
                workspaceRoot=scope["workspaceRoot"],
            )
        if not project_root.is_dir():
            fail(
                "SCHEDULER_PROJECT_SCOPE_INVALID",
                "A project workspace root must be a directory",
                projectId=scope["id"],
                workspaceRoot=str(project_root),
            )
        resolved_scopes.append((scope, project_root))

    direct_primary = [
        index
        for index, (_scope, project_root) in enumerate(resolved_scopes)
        if project_root == primary_root
    ]
    repository_primary: list[int] = []
    if not direct_primary:
        primary_common = _git_common_directory(primary_root)
        if primary_common is not None:
            repository_primary = [
                index
                for index, (_scope, project_root) in enumerate(
                    resolved_scopes
                )
                if _git_common_directory(project_root) == primary_common
            ]
    primary_matches = direct_primary or repository_primary
    if len(primary_matches) != 1:
        fail(
            "SCHEDULER_PROJECT_SCOPE_INVALID",
            "projectScopes must identify exactly one current Delivery "
            "repository workspace",
            workspaceRoot=str(primary_root),
            matchingProjectIds=[
                resolved_scopes[index][0]["id"]
                for index in primary_matches
            ],
        )
    primary_index = primary_matches[0]

    verified: list[dict[str, Any]] = []
    for index, (scope, declared_root) in enumerate(resolved_scopes):
        project_root = declared_root
        scope_binding = scope.get("gitBinding")
        if index == primary_index:
            project_root = primary_root
        elif scope_binding is not None:
            branch_roots = _branch_worktrees(
                declared_root,
                scope_binding["branchRef"],
            )
            if len(branch_roots) == 1:
                project_root = branch_roots[0]
            elif len(branch_roots) > 1:
                fail(
                    "SCHEDULER_PROJECT_SCOPE_INVALID",
                    "A project feature branch resolves to multiple worktrees",
                    projectId=scope["id"],
                    branchRef=scope_binding["branchRef"],
                    workspaceRoots=[str(item) for item in branch_roots],
                )
        if project_root == primary_root:
            delivery_binding = delivery.get("gitBinding")
            if (
                scope_binding is not None
                and delivery_binding is not None
                and scope_binding != delivery_binding
            ):
                fail(
                    "SCHEDULER_PROJECT_SCOPE_INVALID",
                    "The primary project Git binding must match the "
                    "Delivery Git binding",
                    projectId=scope["id"],
                )
            if scope_binding is None:
                scope_binding = delivery_binding
        git_workspace = verify_delivery_git_binding(
            str(project_root),
            scope_binding,
            preparing=preparing,
        )
        item = {
            "id": scope["id"],
            "workspaceRoot": str(project_root),
            "access": scope["access"],
        }
        if project_root != declared_root:
            item["declaredWorkspaceRoot"] = str(declared_root)
            item["workspaceBindingSource"] = (
                "SAME_REPOSITORY_LINKED_WORKTREE"
            )
        if scope_binding is not None:
            item["gitBinding"] = scope_binding
        if git_workspace is not None:
            item["gitWorkspace"] = git_workspace
        verified.append(item)
    return sorted(verified, key=lambda item: item["id"])


def verify_runtime_delivery_project_scopes(
    workspace_root: str,
    delivery: dict[str, Any],
    *,
    preparing: bool,
) -> list[dict[str, Any]]:
    """Verify runtime scopes, including the implicit single-project scope.

    ``projectScopes`` is optional in the frozen hierarchy because a normal
    single-repository Delivery is already anchored by ``delivery.gitBinding``.
    Runtime receivers still need an explicit, verified authorization scope,
    so synthesize that primary scope without changing the frozen schema.
    """

    if delivery.get("projectScopes") is not None:
        return verify_delivery_project_scopes(
            workspace_root,
            delivery,
            preparing=preparing,
        )

    primary_root = Path(workspace_root).absolute().resolve(strict=True)
    binding = delivery.get("gitBinding")
    git_workspace = verify_delivery_git_binding(
        str(primary_root),
        binding,
        preparing=preparing,
    )
    primary_scope: dict[str, Any] = {
        "id": "primary",
        "workspaceRoot": str(primary_root),
        "access": "READ_WRITE",
        "scopeSource": (
            "DELIVERY_GIT_BINDING"
            if binding is not None
            else "DELIVERY_WORKSPACE"
        ),
    }
    if binding is not None:
        primary_scope["gitBinding"] = binding
    if git_workspace is not None:
        primary_scope["gitWorkspace"] = git_workspace
    return [primary_scope]


__all__ = (
    "enumerate_local_feature_branches",
    "find_delivery_linked_worktree",
    "git_repository_identity",
    "git_repository_lineage_identity",
    "git_worktree_identity",
    "inspect_delivery_git_workspace",
    "inspect_frozen_git_workspace_provenance",
    "resolve_branch_binding",
    "verify_delivery_git_binding",
    "verify_delivery_project_scopes",
    "verify_runtime_delivery_project_scopes",
)
