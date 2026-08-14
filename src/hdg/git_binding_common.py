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
    status_pairs = {
        line[:2]
        for line in changes
        if len(line) >= 2
    }
    unmerged_pairs = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
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
    result = {
        "clean": not changes,
        "changeCount": len(changes),
        "stateFingerprint": fingerprint(
            {
                "porcelain": porcelain,
                "contentState": content_state,
            }
        ),
    }
    if changes:
        result.update(
            {
                "hasStagedChanges": any(
                    pair[0] not in {" ", "?"}
                    for pair in status_pairs
                ),
                "hasUntrackedChanges": "??" in status_pairs,
                "hasUnmergedChanges": bool(
                    status_pairs & unmerged_pairs
                ),
            }
        )
    return result

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
