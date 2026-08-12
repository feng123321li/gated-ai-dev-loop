from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
from typing import Any

from .errors import fail
from .jsonio import fingerprint
from .model_core import validate_git_binding


GIT_TIMEOUT_SECONDS = 10
MAX_WORKSPACE_DIFF_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_SCOPE_FILES = 10_000
MAX_EVIDENCE_SCOPE_TOTAL_FILES = 20_000
_DELIVERY_PATHSPEC = (
    ".",
    ":(exclude).layered-delivery",
    ":(exclude).layered-delivery/**",
)
_GIT_CHANGE_STATUS = {
    "A": "ADDED",
    "B": "BROKEN",
    "C": "COPIED",
    "D": "DELETED",
    "M": "MODIFIED",
    "R": "RENAMED",
    "T": "TYPE_CHANGED",
    "U": "UNMERGED",
    "X": "UNKNOWN",
}


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
            "Git could not inspect the Delivery workspace",
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


def _checkout_topology(workspace: Path) -> str:
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
        "LINKED_CHECKOUT"
        if git_dir != common_dir
        else "PRIMARY_CHECKOUT"
    )


def _working_tree_state(workspace: Path) -> dict[str, Any]:
    porcelain = _git(
        workspace,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        *_DELIVERY_PATHSPEC,
    ).stdout
    changes = porcelain.splitlines() if porcelain else []
    changed_paths: set[str] = set()
    for arguments in (
        (
            "diff",
            "--name-only",
            "-z",
            "--",
            *_DELIVERY_PATHSPEC,
        ),
        (
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--",
            *_DELIVERY_PATHSPEC,
        ),
        (
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *_DELIVERY_PATHSPEC,
        ),
    ):
        output = _git(workspace, *arguments).stdout
        changed_paths.update(path for path in output.split("\0") if path)

    content_state: list[dict[str, Any]] = []
    for relative_path in sorted(changed_paths):
        candidate = workspace / relative_path
        workspace_blob = None
        if os.path.lexists(candidate):
            workspace_blob = _git_output(
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
                "workspaceBlob": workspace_blob,
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


def _evidence_scope_paths(value: object) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    normalized: list[str] = []
    for raw in value:
        if (
            not isinstance(raw, str)
            or not raw
            or "\\" in raw
            or any(character in raw for character in ("\0", "\r", "\n"))
        ):
            return None
        path = PurePosixPath(raw)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.parts[0] == ".layered-delivery"
        ):
            return None
        normalized.append(path.as_posix())
    return sorted(set(normalized))


def _file_state(candidate: Path) -> dict[str, Any] | None:
    try:
        before = os.lstat(candidate)
    except FileNotFoundError:
        return {"kind": "MISSING"}
    if stat.S_ISLNK(before.st_mode):
        target = os.readlink(candidate)
        after = os.lstat(candidate)
        if (
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
        ):
            return None
        return {
            "kind": "SYMLINK",
            "mode": stat.S_IMODE(before.st_mode),
            "targetFingerprint": fingerprint(target),
        }
    if not stat.S_ISREG(before.st_mode):
        return None
    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = os.lstat(candidate)
    if (
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    ):
        return None
    return {
        "kind": "FILE",
        "mode": stat.S_IMODE(before.st_mode),
        "size": before.st_size,
        "contentSha256": digest.hexdigest(),
    }


def _path_covers_file(declared_path: str, relative_path: str) -> bool:
    return relative_path == declared_path or relative_path.startswith(
        f"{declared_path}/"
    )


def _evidence_scope_states(
    workspace: Path,
    scopes: list[tuple[str, list[str]]],
) -> dict[str, dict[str, Any] | None]:
    all_paths = sorted(
        {
            path
            for _, declared_paths in scopes
            for path in declared_paths
        }
    )
    pathspecs = [f":(literal){path}" for path in all_paths]
    raw_index_state = _git(
        workspace,
        "ls-files",
        "--stage",
        "-z",
        "--",
        *pathspecs,
    ).stdout
    index_by_file: dict[str, list[str]] = {}
    files: set[str] = set()
    for entry in raw_index_state.split("\0"):
        if not entry:
            continue
        _metadata, separator, relative_path = entry.partition("\t")
        if not separator:
            return {scope_id: None for scope_id, _ in scopes}
        files.add(relative_path)
        index_by_file.setdefault(relative_path, []).append(entry)
    files.update(
        path
        for path in _git(
            workspace,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *pathspecs,
        ).stdout.split("\0")
        if path
    )
    files = {
        path
        for path in files
        if path != ".layered-delivery"
        and not path.startswith(".layered-delivery/")
    }
    if len(files) > MAX_EVIDENCE_SCOPE_TOTAL_FILES:
        return {scope_id: None for scope_id, _ in scopes}
    content_by_file: dict[str, dict[str, Any]] = {}
    for relative_path in sorted(files):
        candidate = _safe_untracked_candidate(workspace, relative_path)
        file_state = _file_state(candidate)
        if file_state is None:
            return {scope_id: None for scope_id, _ in scopes}
        content_by_file[relative_path] = {
            "path": relative_path,
            "workspaceState": file_state,
        }
    results: dict[str, dict[str, Any] | None] = {}
    for scope_id, declared_paths in scopes:
        scope_files = sorted(
            relative_path
            for relative_path in files
            if any(
                _path_covers_file(declared_path, relative_path)
                for declared_path in declared_paths
            )
        )
        if len(scope_files) > MAX_EVIDENCE_SCOPE_FILES:
            results[scope_id] = None
            continue
        content_state = [
            content_by_file[relative_path]
            for relative_path in scope_files
        ]
        index_entries = sorted(
            entry
            for relative_path in scope_files
            for entry in index_by_file.get(relative_path, [])
        )
        results[scope_id] = {
            "fileCount": len(content_state),
            "stateFingerprint": fingerprint(
                {
                    "kind": "EVIDENCE_RELEVANT_PATHS_V1",
                    "declaredPaths": declared_paths,
                    "indexEntries": index_entries,
                    "contentState": content_state,
                }
            ),
        }
    return results


def _branch_checkout_count(workspace: Path, branch_ref: str) -> int:
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


def _workspace_provenance(
    workspace: Path,
    *,
    topology: str,
    selection: MainlineSelection,
    base_commit: str,
    host_adapter_id: str | None,
) -> dict[str, Any]:
    strategy = "CURRENT_WORKSPACE_SERIAL"
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


def _git_change_entries(
    workspace: Path,
    base_commit: str,
    head_commit: str | None = None,
) -> list[dict[str, str]]:
    revisions = (
        (base_commit,)
        if head_commit is None
        else (base_commit, head_commit)
    )
    output = _git(
        workspace,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--name-status",
        "-z",
        *revisions,
        "--",
        *_DELIVERY_PATHSPEC,
    ).stdout
    tokens = output.split("\0")
    if tokens and not tokens[-1]:
        tokens.pop()
    entries: list[dict[str, str]] = []
    position = 0
    while position < len(tokens):
        status_token = tokens[position]
        position += 1
        if not status_token or position >= len(tokens):
            fail(
                "SCHEDULER_GIT_DIFF_INVALID",
                "Git returned an invalid changed-file snapshot",
            )
        status_code = status_token[0]
        first_path = tokens[position]
        position += 1
        item = {
            "path": first_path,
            "status": _GIT_CHANGE_STATUS.get(status_code, "UNKNOWN"),
            "statusCode": status_code,
        }
        if status_code in {"C", "R"}:
            if position >= len(tokens):
                fail(
                    "SCHEDULER_GIT_DIFF_INVALID",
                    "Git returned an invalid renamed-file snapshot",
                )
            item["previousPath"] = first_path
            item["path"] = tokens[position]
            position += 1
        entries.append(item)
    return entries


def inspect_business_commit_range(
    workspace_root: str,
    turn_start_commit: str,
    head_commit: str,
) -> dict[str, Any]:
    """Describe net business changes in a serial workspace turn.

    Control-plane files under .layered-delivery never count as Delivery
    output. The explicit two-commit comparison also rejects rewritten history
    instead of treating any different HEAD as proof that the turn committed
    work.
    """

    workspace = Path(workspace_root).absolute().resolve(strict=True)
    resolved_start = _commit(
        workspace,
        turn_start_commit,
        code="SCHEDULER_GIT_TURN_START_INVALID",
        message="Serial workspace turnStartCommit does not exist",
    )
    resolved_head = _commit(
        workspace,
        head_commit,
        code="SCHEDULER_GIT_HEAD_INVALID",
        message="Serial workspace turn HEAD does not exist",
    )
    ancestor = _git(
        workspace,
        "merge-base",
        "--is-ancestor",
        resolved_start,
        resolved_head,
        accepted=(0, 1),
    ).returncode == 0
    changed_files = _git_change_entries(
        workspace,
        resolved_start,
        resolved_head,
    )
    tree_output = _git(
        workspace,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        resolved_head,
    ).stdout
    business_tree_entries = []
    for entry in tree_output.split("\0"):
        if not entry:
            continue
        _metadata, separator, relative_path = entry.partition("\t")
        if not separator:
            fail(
                "SCHEDULER_GIT_DIFF_INVALID",
                "Git returned an invalid business tree entry",
            )
        if (
            relative_path == ".layered-delivery"
            or relative_path.startswith(".layered-delivery/")
        ):
            continue
        business_tree_entries.append(entry)
    return {
        "turnStartCommit": resolved_start,
        "headCommit": resolved_head,
        "turnStartCommitIsAncestor": ancestor,
        "businessChangedFiles": changed_files,
        "businessTreeFingerprint": fingerprint(
            {
                "kind": "GIT_BUSINESS_TREE_V1",
                "entries": business_tree_entries,
            }
        ),
    }


def _safe_untracked_candidate(workspace: Path, relative_path: str) -> Path:
    posix_path = PurePosixPath(relative_path)
    if (
        posix_path.is_absolute()
        or not posix_path.parts
        or any(part in {"", ".", ".."} for part in posix_path.parts)
    ):
        fail(
            "SCHEDULER_GIT_DIFF_INVALID",
            "Git returned an unsafe untracked path",
        )
    candidate = workspace.joinpath(*posix_path.parts)
    try:
        common = os.path.commonpath(
            (str(workspace), str(candidate.absolute()))
        )
    except ValueError:
        common = ""
    if os.path.normcase(common) != os.path.normcase(str(workspace)):
        fail(
            "SCHEDULER_GIT_DIFF_INVALID",
            "Git returned an untracked path outside the Delivery workspace",
        )
    return candidate


def _untracked_file_patch(workspace: Path, relative_path: str) -> str:
    candidate = _safe_untracked_candidate(workspace, relative_path)
    try:
        metadata = os.lstat(candidate)
    except OSError:
        fail(
            "SCHEDULER_GIT_DIFF_CHANGED",
            "An untracked file changed while its snapshot was captured",
            path=relative_path,
        )
    if stat.S_ISLNK(metadata.st_mode):
        content = os.readlink(candidate).encode("utf-8")
        mode = "120000"
    elif stat.S_ISREG(metadata.st_mode):
        mode = "100755" if metadata.st_mode & stat.S_IXUSR else "100644"
        if metadata.st_size > MAX_WORKSPACE_DIFF_BYTES:
            return "\n".join(
                [
                    f"diff --git a/{relative_path} b/{relative_path}",
                    f"new file mode {mode}",
                    (
                        "Content omitted from this snapshot because the "
                        f"untracked file exceeds {MAX_WORKSPACE_DIFF_BYTES} "
                        "bytes."
                    ),
                ]
            )
        try:
            content = candidate.read_bytes()
        except OSError:
            fail(
                "SCHEDULER_GIT_DIFF_CHANGED",
                "An untracked file changed while its snapshot was captured",
                path=relative_path,
            )
    else:
        return "\n".join(
            [
                f"diff --git a/{relative_path} b/{relative_path}",
                "Content omitted because the untracked path is not a file.",
            ]
        )
    header = [
        f"diff --git a/{relative_path} b/{relative_path}",
        f"new file mode {mode}",
    ]
    if b"\0" in content:
        return "\n".join(
            [
                *header,
                f"Binary files /dev/null and b/{relative_path} differ",
            ]
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return "\n".join(
            [
                *header,
                (
                    "File content is not UTF-8 text; the binary snapshot "
                    "is omitted."
                ),
            ]
        )
    unified = list(
        difflib.unified_diff(
            [],
            text.splitlines(),
            fromfile="/dev/null",
            tofile=f"b/{relative_path}",
            lineterm="",
        )
    )
    if not unified:
        unified = ["--- /dev/null", f"+++ b/{relative_path}"]
    return "\n".join([*header, *unified])


def _bounded_workspace_diff(value: str) -> tuple[str, bool, int]:
    encoded = value.encode("utf-8")
    byte_count = len(encoded)
    if byte_count <= MAX_WORKSPACE_DIFF_BYTES:
        return value, False, byte_count
    truncated = encoded[:MAX_WORKSPACE_DIFF_BYTES].decode(
        "utf-8",
        errors="ignore",
    )
    return (
        truncated.rstrip()
        + "\n\n... Controller workspace snapshot truncated at "
        + f"{MAX_WORKSPACE_DIFF_BYTES} UTF-8 bytes.\n",
        True,
        byte_count,
    )


def capture_verified_workspace_changes(
    verified_project_scopes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Capture reviewable Git snapshots for verified writable scopes.

    The evidence compares each current workspace with its Delivery's frozen
    baseCommit. It deliberately describes a workspace snapshot, not exclusive
    ownership by the submitting Loop, TASK, or Delivery; several control-plane
    Deliveries may share one physical workspace.
    """

    snapshots: list[dict[str, Any]] = []
    for scope in sorted(
        verified_project_scopes,
        key=lambda item: str(item.get("id", "")),
    ):
        binding = scope.get("gitBinding")
        if scope.get("access") != "READ_WRITE" or binding is None:
            continue
        workspace = Path(scope["workspaceRoot"]).absolute().resolve(
            strict=True
        )
        normalized_binding = validate_git_binding(binding)
        git_workspace = verify_delivery_git_binding(
            str(workspace),
            normalized_binding,
            preparing=False,
        )
        if git_workspace is None:
            fail(
                "SCHEDULER_GIT_CHECKOUT_REQUIRED",
                "Workspace change evidence requires a Git checkout",
            )
        base_commit = normalized_binding["baseCommit"]
        initial_working_tree = _working_tree_state(workspace)
        changed_files = _git_change_entries(workspace, base_commit)
        known_paths = {item["path"] for item in changed_files}
        untracked_paths = sorted(
            path
            for path in _git(
                workspace,
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                *_DELIVERY_PATHSPEC,
            ).stdout.split("\0")
            if path and path not in known_paths
        )
        changed_files.extend(
            {
                "path": path,
                "status": "UNTRACKED",
                "statusCode": "?",
            }
            for path in untracked_paths
        )
        changed_files.sort(
            key=lambda item: (
                item["path"],
                item.get("previousPath", ""),
            )
        )
        tracked_diff = _git(
            workspace,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--no-renames",
            "--unified=3",
            base_commit,
            "--",
            *_DELIVERY_PATHSPEC,
        ).stdout.rstrip()
        patch_parts = [tracked_diff] if tracked_diff else []
        patch_parts.extend(
            _untracked_file_patch(workspace, path)
            for path in untracked_paths
        )
        full_diff = "\n\n".join(
            part.rstrip() for part in patch_parts if part
        )
        if full_diff:
            full_diff += "\n"
        final_git_workspace = verify_delivery_git_binding(
            str(workspace),
            normalized_binding,
            preparing=False,
        )
        final_working_tree = _working_tree_state(workspace)
        if (
            final_git_workspace is None
            or final_git_workspace["headCommit"]
            != git_workspace["headCommit"]
            or final_working_tree["stateFingerprint"]
            != initial_working_tree["stateFingerprint"]
        ):
            fail(
                "SCHEDULER_GIT_DIFF_CHANGED",
                "The Delivery workspace changed while its result snapshot "
                "was captured",
                projectId=scope["id"],
            )
        rendered_diff, diff_truncated, diff_byte_count = (
            _bounded_workspace_diff(full_diff)
        )
        snapshots.append(
            {
                "projectId": scope["id"],
                "workspaceRoot": str(workspace),
                "baseCommit": base_commit,
                "headCommit": git_workspace["headCommit"],
                "workingTreeStateFingerprint": initial_working_tree[
                    "stateFingerprint"
                ],
                "evidenceKind": "WORKSPACE_CHANGE_SNAPSHOT",
                "comparison": (
                    "FROZEN_BASE_COMMIT_TO_CURRENT_WORKSPACE"
                ),
                "attribution": (
                    "NOT_EXCLUSIVE_TO_DELIVERY_TASK_OR_LOOP"
                ),
                "changedFiles": changed_files,
                "diff": rendered_diff,
                "diffTruncated": diff_truncated,
                "diffByteCount": diff_byte_count,
                "snapshotFingerprint": fingerprint(
                    {
                        "projectId": scope["id"],
                        "workspaceRoot": str(workspace),
                        "baseCommit": base_commit,
                        "headCommit": git_workspace["headCommit"],
                        "workingTreeStateFingerprint": (
                            initial_working_tree["stateFingerprint"]
                        ),
                        "changedFiles": changed_files,
                        "diff": full_diff,
                    }
                ),
            }
        )
    return snapshots


def capture_verified_workspace_state(
    verified_project_scopes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Capture a lightweight, conservative state binding for evidence reuse."""

    snapshots: list[dict[str, Any]] = []
    for scope in sorted(
        verified_project_scopes,
        key=lambda item: str(item.get("id", "")),
    ):
        if scope.get("access") != "READ_WRITE":
            continue
        project_id = str(scope.get("id", ""))
        binding = scope.get("gitBinding")
        if binding is None:
            snapshots.append(
                {
                    "projectId": project_id,
                    "bindingState": "UNBOUND",
                }
            )
            continue
        workspace = Path(scope["workspaceRoot"]).absolute().resolve(
            strict=True
        )
        normalized_binding = validate_git_binding(binding)
        initial_git = verify_delivery_git_binding(
            str(workspace),
            normalized_binding,
            preparing=False,
        )
        initial_tree = _working_tree_state(workspace)
        final_git = verify_delivery_git_binding(
            str(workspace),
            normalized_binding,
            preparing=False,
        )
        final_tree = _working_tree_state(workspace)
        if (
            initial_git is None
            or final_git is None
            or initial_git["headCommit"] != final_git["headCommit"]
            or initial_tree["stateFingerprint"]
            != final_tree["stateFingerprint"]
        ):
            snapshots.append(
                {
                    "projectId": project_id,
                    "bindingState": "UNSTABLE",
                }
            )
            continue
        snapshots.append(
            {
                "projectId": project_id,
                "bindingState": "BOUND",
                "headCommit": initial_git["headCommit"],
                "workingTreeStateFingerprint": initial_tree[
                    "stateFingerprint"
                ],
            }
        )
    return snapshots


def capture_verified_evidence_scope_state(
    verified_project_scopes: list[dict[str, Any]],
    affected_scopes: object,
) -> list[dict[str, Any]]:
    """Bind declared relevant paths without invalidating on unrelated edits."""

    if not isinstance(affected_scopes, list):
        return []
    projects = {
        str(scope.get("id", "")): scope
        for scope in verified_project_scopes
        if scope.get("access") == "READ_WRITE"
    }
    snapshots: list[dict[str, Any]] = []
    valid_by_project: dict[
        str,
        list[tuple[dict[str, Any], list[str]]],
    ] = {}
    for affected in affected_scopes:
        if not isinstance(affected, dict):
            continue
        scope_id = affected.get("scopeId")
        project_id = affected.get("projectId")
        declared_paths = _evidence_scope_paths(affected.get("paths"))
        snapshot: dict[str, Any] = {
            "scopeId": scope_id,
            "projectId": project_id,
            "paths": declared_paths or [],
        }
        project = projects.get(project_id) if isinstance(project_id, str) else None
        if (
            not isinstance(scope_id, str)
            or not scope_id
            or project is None
            or project.get("gitBinding") is None
            or declared_paths is None
        ):
            snapshot["bindingState"] = "UNBOUND"
            snapshots.append(snapshot)
            continue
        valid_by_project.setdefault(project_id, []).append(
            (snapshot, declared_paths)
        )
    for project_id, project_scopes in sorted(valid_by_project.items()):
        project = projects[project_id]
        workspace = Path(project["workspaceRoot"]).absolute().resolve(strict=True)
        binding = validate_git_binding(project["gitBinding"])
        initial_git = verify_delivery_git_binding(
            str(workspace),
            binding,
            preparing=False,
        )
        scope_paths = [
            (str(snapshot["scopeId"]), declared_paths)
            for snapshot, declared_paths in project_scopes
        ]
        initial_states = _evidence_scope_states(workspace, scope_paths)
        final_git = verify_delivery_git_binding(
            str(workspace),
            binding,
            preparing=False,
        )
        final_states = _evidence_scope_states(workspace, scope_paths)
        git_stable = (
            initial_git is not None
            and final_git is not None
            and initial_git["headCommit"] == final_git["headCommit"]
        )
        for snapshot, _declared_paths in project_scopes:
            scope_id = str(snapshot["scopeId"])
            initial_state = initial_states.get(scope_id)
            final_state = final_states.get(scope_id)
            if initial_state is None or final_state is None:
                snapshot["bindingState"] = "UNBOUND"
            elif (
                not git_stable
                or initial_state["stateFingerprint"]
                != final_state["stateFingerprint"]
            ):
                snapshot["bindingState"] = "UNSTABLE"
            else:
                snapshot.update(
                    {
                        "bindingState": "BOUND",
                        "stateFingerprint": initial_state[
                            "stateFingerprint"
                        ],
                        "fileCount": initial_state["fileCount"],
                    }
                )
            snapshots.append(snapshot)
    return sorted(
        snapshots,
        key=lambda item: (str(item.get("projectId")), str(item.get("scopeId"))),
    )


def _git_common_directory(workspace: Path) -> Path | None:
    if not (workspace / ".git").exists():
        return None
    common = Path(_git_output(workspace, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = workspace / common
    return common.resolve(strict=True)


def git_repository_identity(workspace_root: str) -> str | None:
    """Return a stable local identity shared by all checkouts of one repo."""

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


def _git_branch_identity(
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


def git_physical_checkout_identity(
    workspace_root: str,
) -> dict[str, str] | None:
    """Return one local repository's branch-independent checkout slot."""

    workspace = Path(workspace_root).absolute().resolve(strict=True)
    common = _git_common_directory(workspace)
    repository_key = git_repository_lineage_identity(str(workspace))
    repository_instance_key = git_repository_identity(str(workspace))
    if (
        common is None
        or repository_key is None
        or repository_instance_key is None
    ):
        return None
    git_directory = Path(
        _git_output(workspace, "rev-parse", "--git-dir")
    )
    if not git_directory.is_absolute():
        git_directory = workspace / git_directory
    git_directory = git_directory.resolve(strict=True)
    if git_directory == common:
        checkout_slot = "PRIMARY"
    else:
        worktrees_directory = (common / "worktrees").resolve(strict=True)
        try:
            relative_slot = git_directory.relative_to(worktrees_directory)
        except ValueError:
            fail(
                "SCHEDULER_GIT_CHECKOUT_MISMATCH",
                "Git checkout metadata is outside the repository worktree "
                "administration directory",
            )
        if len(relative_slot.parts) != 1:
            fail(
                "SCHEDULER_GIT_CHECKOUT_MISMATCH",
                "Git checkout metadata does not identify one worktree slot",
            )
        checkout_slot = PurePosixPath(
            ".git",
            "worktrees",
            os.path.normcase(relative_slot.name),
        ).as_posix()
    identity = {
        "repositoryKey": repository_key,
        "repositoryInstanceKey": repository_instance_key,
        "checkoutSlot": checkout_slot,
    }
    branch_identity = _git_branch_identity(str(workspace))
    if branch_identity is not None:
        identity["branchRef"] = branch_identity["branchRef"]
    return identity


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
            item["workspaceBindingSource"] = "CURRENT_REPOSITORY_CHECKOUT"
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
    "capture_verified_evidence_scope_state",
    "capture_verified_workspace_state",
    "capture_verified_workspace_changes",
    "enumerate_local_feature_branches",
    "git_physical_checkout_identity",
    "git_repository_identity",
    "git_repository_lineage_identity",
    "inspect_delivery_git_workspace",
    "inspect_frozen_git_workspace_provenance",
    "resolve_branch_binding",
    "verify_delivery_git_binding",
    "verify_delivery_project_scopes",
    "verify_runtime_delivery_project_scopes",
)
