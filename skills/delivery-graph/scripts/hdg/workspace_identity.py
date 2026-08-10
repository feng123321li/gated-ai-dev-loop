from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from .git_binding import git_worktree_identity
from .jsonio import fingerprint


GIT_BRANCH_IDENTITY_PREFIX = "git-branch:v1:"
PATH_IDENTITY_PREFIX = "path:v1:"


@dataclass(frozen=True)
class WorkspaceIdentity:
    """Stable identity for one governed Delivery workspace.

    Git worktrees are identified by their repository history lineage and the
    checked-out local branch. Moving or recreating the repository/worktree
    does not change that identity. Non-Git workspaces retain path isolation.
    """

    key: str
    kind: str
    legacy_path_key: str
    material: dict[str, Any]


def _resolved_workspace(
    workspace_root: str | os.PathLike[str],
) -> Path:
    return Path(workspace_root).absolute().resolve(strict=True)


def legacy_path_workspace_key(
    workspace_root: str | os.PathLike[str],
) -> str:
    workspace = _resolved_workspace(workspace_root)
    return fingerprint({"workspace": os.path.normcase(str(workspace))})


def workspace_identity(
    workspace_root: str | os.PathLike[str],
) -> WorkspaceIdentity:
    workspace = _resolved_workspace(workspace_root)
    legacy_key = legacy_path_workspace_key(workspace)
    git_identity = git_worktree_identity(str(workspace))
    if git_identity is not None:
        material = {
            "repositoryKey": git_identity["repositoryKey"],
            "branchRef": git_identity["branchRef"],
        }
        return WorkspaceIdentity(
            key=git_branch_workspace_key(
                material["repositoryKey"],
                material["branchRef"],
            ),
            kind="GIT_BRANCH",
            legacy_path_key=legacy_key,
            material=material,
        )
    material = {"workspace": os.path.normcase(str(workspace))}
    return WorkspaceIdentity(
        key=PATH_IDENTITY_PREFIX + fingerprint(material),
        kind="PATH",
        legacy_path_key=legacy_key,
        material=material,
    )


def workspace_identity_version(key: str) -> str:
    if key.startswith(GIT_BRANCH_IDENTITY_PREFIX):
        return "GIT_BRANCH_V1"
    if key.startswith(PATH_IDENTITY_PREFIX):
        return "PATH_V1"
    return "LEGACY_PATH_V0"


def git_branch_workspace_key(
    repository_key: str,
    branch_ref: str,
) -> str:
    return GIT_BRANCH_IDENTITY_PREFIX + fingerprint(
        {
            "repositoryKey": repository_key,
            "branchRef": branch_ref,
        }
    )


__all__ = (
    "GIT_BRANCH_IDENTITY_PREFIX",
    "PATH_IDENTITY_PREFIX",
    "WorkspaceIdentity",
    "git_branch_workspace_key",
    "legacy_path_workspace_key",
    "workspace_identity",
    "workspace_identity_version",
)
