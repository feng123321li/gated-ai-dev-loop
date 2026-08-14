from __future__ import annotations

from .git_binding_common import (
    Any,
    Path,
    PurePosixPath,
    _git,
    _git_output,
    fail,
    fingerprint,
    os,
)
from .git_binding_inspection import verify_delivery_git_binding


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
