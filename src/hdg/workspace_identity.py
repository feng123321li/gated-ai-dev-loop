from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from .git_binding import git_physical_checkout_identity
from .jsonio import fingerprint


GIT_BRANCH_IDENTITY_PREFIX = "git-branch:v1:"
GIT_CHECKOUT_IDENTITY_PREFIX = "git-checkout:v2:"
PATH_IDENTITY_PREFIX = "path:v1:"


@dataclass(frozen=True)
class WorkspaceIdentity:
    """Stable identity for one governed Delivery workspace.

    Git worktrees are identified by repository history lineage, one local
    repository instance, and their physical checkout slot. Switching branches
    in one checkout preserves the identity, while separate clones and linked
    worktrees remain distinct. Non-Git workspaces retain path isolation.
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
    git_identity = git_physical_checkout_identity(str(workspace))
    if git_identity is not None:
        material = {
            "repositoryKey": git_identity["repositoryKey"],
            "repositoryInstanceKey": git_identity[
                "repositoryInstanceKey"
            ],
            "checkoutSlot": git_identity["checkoutSlot"],
        }
        if "branchRef" in git_identity:
            material["branchRef"] = git_identity["branchRef"]
        return WorkspaceIdentity(
            key=git_checkout_workspace_key(
                material["repositoryKey"],
                material["repositoryInstanceKey"],
                material["checkoutSlot"],
            ),
            kind="GIT_CHECKOUT",
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
    if key.startswith(GIT_CHECKOUT_IDENTITY_PREFIX):
        return "GIT_CHECKOUT_V2"
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


def git_checkout_workspace_key(
    repository_key: str,
    repository_instance_key: str,
    checkout_slot: str,
) -> str:
    return GIT_CHECKOUT_IDENTITY_PREFIX + fingerprint(
        {
            "repositoryKey": repository_key,
            "repositoryInstanceKey": repository_instance_key,
            "checkoutSlot": checkout_slot,
        }
    )


__all__ = (
    "GIT_BRANCH_IDENTITY_PREFIX",
    "GIT_CHECKOUT_IDENTITY_PREFIX",
    "PATH_IDENTITY_PREFIX",
    "WorkspaceIdentity",
    "git_branch_workspace_key",
    "git_checkout_workspace_key",
    "legacy_path_workspace_key",
    "workspace_identity",
    "workspace_identity_version",
)
