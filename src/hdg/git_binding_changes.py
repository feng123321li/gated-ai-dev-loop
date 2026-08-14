from __future__ import annotations

from .git_binding_common import (
    Any,
    MAX_WORKSPACE_DIFF_BYTES,
    Path,
    _DELIVERY_PATHSPEC,
    _GIT_CHANGE_STATUS,
    _commit,
    _evidence_scope_paths,
    _evidence_scope_states,
    _git,
    _safe_untracked_candidate,
    _working_tree_state,
    difflib,
    fail,
    fingerprint,
    os,
    stat,
    validate_git_binding,
)
from .git_binding_inspection import verify_delivery_git_binding


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
