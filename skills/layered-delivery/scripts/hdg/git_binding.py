from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from .errors import fail
from .model_core import validate_git_binding


GIT_TIMEOUT_SECONDS = 10


def _git(
    workspace: Path,
    *arguments: str,
    accepted: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(workspace), *arguments],
            capture_output=True,
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


def inspect_delivery_git_workspace(
    workspace_root: str,
    *,
    base_ref: str | None = None,
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
    if symbolic.returncode != 0:
        fail(
            "SCHEDULER_GIT_DETACHED_HEAD",
            "A Git Delivery must be prepared on a feature branch",
        )
    full_ref = symbolic.stdout.strip()
    if not full_ref.startswith("refs/heads/"):
        fail(
            "SCHEDULER_GIT_BRANCH_MISMATCH",
            "Delivery worktree must use a local feature branch",
        )
    branch_ref = full_ref.removeprefix("refs/heads/")
    head_commit = _commit(
        workspace,
        "HEAD",
        code="SCHEDULER_GIT_HEAD_INVALID",
        message="Delivery worktree HEAD is not a commit",
    )
    result: dict[str, Any] = {
        "gitWorkspace": {
            "branchRef": branch_ref,
            "headCommit": head_commit,
        }
    }
    selected_base_ref = base_ref
    base_ref_commit = None
    if selected_base_ref is None:
        if branch_ref in {"main", "master"}:
            selected_base_ref = branch_ref
            base_ref_commit = head_commit
        else:
            for candidate in ("main", "master"):
                candidate_commit = _optional_commit(
                    workspace,
                    f"refs/heads/{candidate}",
                )
                if candidate_commit is not None:
                    selected_base_ref = candidate
                    base_ref_commit = candidate_commit
                    break
    if selected_base_ref is None:
        fail(
            "SCHEDULER_GIT_BASE_INVALID",
            "Neither main nor master exists as a local mainline branch",
        )
    if branch_ref == selected_base_ref:
        result["gitWorkspace"]["role"] = "MAINLINE"
        return result
    if base_ref_commit is None:
        base_ref_commit = _commit(
            workspace,
            f"refs/heads/{selected_base_ref}",
            code="SCHEDULER_GIT_BASE_INVALID",
            message="Selected mainline branch does not exist",
        )
    merge_base = _git(
        workspace,
        "merge-base",
        head_commit,
        base_ref_commit,
        accepted=(0, 1),
    )
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        fail(
            "SCHEDULER_GIT_BASE_INVALID",
            "Feature branch does not share a base with the mainline branch",
            branchRef=branch_ref,
            baseRef=selected_base_ref,
        )
    result["gitWorkspace"]["role"] = "DELIVERY_FEATURE"
    result["suggestedGitBinding"] = {
        "branchRef": branch_ref,
        "baseRef": selected_base_ref,
        "baseCommit": merge_base.stdout.strip(),
        "integrationTarget": selected_base_ref,
    }
    return result


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
            "A Git Delivery must declare its feature branch and mainline "
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
    base_ref_commit = _commit(
        workspace,
        f"refs/heads/{normalized['baseRef']}",
        code="SCHEDULER_GIT_BASE_INVALID",
        message="Delivery baseRef does not resolve to a local branch",
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
    mainline_ancestor = _git(
        workspace,
        "merge-base",
        "--is-ancestor",
        base_commit,
        base_ref_commit,
        accepted=(0, 1),
    )
    if mainline_ancestor.returncode != 0:
        fail(
            "SCHEDULER_GIT_BASE_INVALID",
            "Delivery mainline no longer contains baseCommit",
            baseCommit=base_commit,
            baseRef=normalized["baseRef"],
        )
    if preparing:
        merge_base = _git_output(
            workspace,
            "merge-base",
            head_commit,
            base_ref_commit,
        )
        if merge_base != base_commit:
            fail(
                "SCHEDULER_GIT_BASE_INVALID",
                "baseCommit must be the feature branch fork point from "
                "baseRef when the Delivery is prepared",
                expectedBaseCommit=merge_base,
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


__all__ = (
    "inspect_delivery_git_workspace",
    "verify_delivery_git_binding",
)
