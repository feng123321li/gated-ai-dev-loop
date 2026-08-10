from __future__ import annotations

import os
from typing import Any, Callable

from .errors import GatedLoopError, fail
from .workspace_identity import (
    git_branch_workspace_key,
    workspace_identity,
    workspace_identity_version,
)


class DeliveryWorkspaceStore:
    """Own Delivery-to-workspace persistence, discovery, and isolation."""

    def __init__(
        self,
        repository: Any,
        *,
        governance_directory: str,
        validate_stored_definition: Callable[[Any], tuple[dict, dict]],
        timestamp_fn: Callable[[object], str],
    ) -> None:
        self.repository = repository
        self.governance_directory = governance_directory
        self.validate_stored_definition = validate_stored_definition
        self.timestamp_fn = timestamp_fn

    def binding(self, root_id: str) -> dict[str, Any]:
        with self.repository.read() as connection:
            row = connection.execute(
                "SELECT workspace_key FROM delivery_workspaces "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()
        if row is None:
            fail(
                "SCHEDULER_DELIVERY_WORKSPACE_MISSING",
                f"Delivery workspace binding is missing: {root_id}",
            )
        return {
            "mode": "DEDICATED_CONVERSATION_WORKSPACE",
            "workspaceKey": row["workspace_key"],
            "identityVersion": workspace_identity_version(
                row["workspace_key"]
            ),
        }

    def assert_bound(
        self,
        root_id: str,
        workspace_root: str | os.PathLike[str],
        *,
        allow_unbound_manual: bool = False,
        allow_unbound_choice: bool = False,
    ) -> None:
        if not self.repository.database_path.is_file():
            fail(
                "SCHEDULER_STATE_ABSENT",
                "No Delivery Graph scheduler state exists",
            )
        identity = workspace_identity(workspace_root)
        if allow_unbound_manual or allow_unbound_choice:
            with self.repository.read() as connection:
                manual = connection.execute(
                    "SELECT h.status, w.workspace_key "
                    "FROM hierarchies h "
                    "LEFT JOIN delivery_workspaces w "
                    "ON w.root_id = h.root_id "
                    "WHERE h.root_id = ?",
                    (root_id,),
                ).fetchone()
            if (
                manual is not None
                and manual["status"]
                in (
                    {"HANDOFF_READY"}
                    if not allow_unbound_choice
                    else {"CHOICE_READY", "HANDOFF_READY"}
                )
                and manual["workspace_key"] is None
            ):
                return
        binding = self.binding(root_id)
        if binding["workspaceKey"] == identity.legacy_path_key:
            self._upgrade_legacy_binding_in_place(
                root_id,
                legacy_workspace_key=identity.legacy_path_key,
                stable_workspace_key=identity.key,
            )
            return
        if binding["workspaceKey"] != identity.key:
            if identity.kind == "GIT_BRANCH":
                stored = self.repository.hierarchy(root_id)
                git_binding = stored["hierarchy"]["delivery"].get(
                    "gitBinding"
                )
                expected_branch = (
                    git_binding.get("branchRef")
                    if isinstance(git_binding, dict)
                    else None
                )
                if (
                    isinstance(expected_branch, str)
                    and binding["workspaceKey"]
                    == git_branch_workspace_key(
                        identity.material["repositoryKey"],
                        expected_branch,
                    )
                ):
                    fail(
                        "SCHEDULER_GIT_BRANCH_MISMATCH",
                        "Delivery worktree is checked out on another branch",
                        expectedBranchRef=expected_branch,
                        actualBranchRef=identity.material["branchRef"],
                    )
            fail(
                "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
                "This Delivery belongs to another conversation workspace",
                rootId=root_id,
                workspaceKey=binding["workspaceKey"],
            )

    def _upgrade_legacy_binding_in_place(
        self,
        root_id: str,
        *,
        legacy_workspace_key: str,
        stable_workspace_key: str,
    ) -> None:
        """Normalize a proven in-place v0 path binding to stable v1."""

        updated_at = self.timestamp_fn(self.repository.now)
        with self.repository.transaction() as connection:
            current_binding = connection.execute(
                "SELECT workspace_key FROM delivery_workspaces "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()
            if current_binding is None:
                fail(
                    "SCHEDULER_DELIVERY_WORKSPACE_MISSING",
                    f"Delivery workspace binding is missing: {root_id}",
                )
            if current_binding["workspace_key"] == stable_workspace_key:
                return
            if current_binding["workspace_key"] != legacy_workspace_key:
                fail(
                    "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
                    "Delivery workspace changed during identity upgrade",
                    rootId=root_id,
                )
            occupied = connection.execute(
                "SELECT r.root_id FROM delivery_workspaces w "
                "JOIN runs r ON r.root_id = w.root_id "
                "WHERE w.workspace_key = ? AND r.root_id != ? "
                "AND r.status NOT IN "
                "('COMPLETED', 'CANCELLED', 'SUPERSEDED') LIMIT 1",
                (stable_workspace_key, root_id),
            ).fetchone()
            if occupied is not None:
                fail(
                    "SCHEDULER_DELIVERY_WORKSPACE_OCCUPIED",
                    "Another unfinished Delivery owns this stable workspace",
                    occupiedRootId=occupied["root_id"],
                )
            connection.execute(
                "UPDATE delivery_workspaces "
                "SET workspace_key = ?, updated_at = ? "
                "WHERE root_id = ?",
                (stable_workspace_key, updated_at, root_id),
            )

    def status(
        self,
        *,
        root_id: str | None = None,
        workspace_root: str | os.PathLike[str] | None = None,
    ) -> dict[str, Any]:
        repository = self.repository
        repository._assert_no_legacy_state()
        if not repository.database_path.is_file():
            return {
                "status": "ABSENT",
                "controlRoot": self.governance_directory,
            }
        with repository.read() as connection:
            rows = connection.execute(
                "SELECT * FROM hierarchies ORDER BY updated_at DESC"
            ).fetchall()
            if not rows:
                return {
                    "status": "ABSENT",
                    "controlRoot": self.governance_directory,
                }
            current_identity = (
                workspace_identity(workspace_root)
                if workspace_root is not None
                else None
            )
            workspace_key = (
                current_identity.key
                if current_identity is not None
                else None
            )
            candidates = (
                [row for row in rows if row["status"] != "ARCHIVED"]
                if root_id is None
                else rows
            )
            if root_id is not None:
                candidates = [
                    row for row in candidates if row["root_id"] == root_id
                ]
            if current_identity is not None:
                candidate_keys = {
                    current_identity.key,
                    current_identity.legacy_path_key,
                }
                placeholders = ", ".join("?" for _item in candidate_keys)
                bound_ids = {
                    row["root_id"]
                    for row in connection.execute(
                        "SELECT root_id FROM delivery_workspaces "
                        f"WHERE workspace_key IN ({placeholders})",
                        tuple(sorted(candidate_keys)),
                    ).fetchall()
                }
                candidates = [
                    row
                    for row in candidates
                    if row["root_id"] in bound_ids
                    or row["status"] in {"CHOICE_READY", "HANDOFF_READY"}
                ]
            if root_id is None:
                active_ids = {
                    row["root_id"]
                    for row in connection.execute(
                        "SELECT root_id FROM runs "
                        "WHERE status NOT IN "
                        "('COMPLETED', 'CANCELLED', 'SUPERSEDED')"
                    ).fetchall()
                }
                active_candidates = [
                    row for row in candidates if row["root_id"] in active_ids
                ]
                if active_candidates:
                    candidates = active_candidates
            if not candidates:
                return {
                    "status": "ABSENT",
                    "controlRoot": self.governance_directory,
                    "workspaceIsolation": (
                        {
                            "mode": "DEDICATED_CONVERSATION_WORKSPACE",
                            "workspaceKey": workspace_key,
                            "identityVersion": (
                                workspace_identity_version(workspace_key)
                                if workspace_key is not None
                                else None
                            ),
                        }
                        if workspace_key is not None
                        else None
                    ),
                }
            latest = candidates[0]
            latest_hierarchy, _ = self.validate_stored_definition(latest)
            run = connection.execute(
                "SELECT status, execution_mode FROM runs "
                "WHERE root_id = ? AND revision = ?",
                (latest["root_id"], latest["revision"]),
            ).fetchone()
            if latest["status"] == "ARCHIVED":
                revision = connection.execute(
                    "SELECT status FROM delivery_revisions "
                    "WHERE root_id = ? AND revision = ?",
                    (latest["root_id"], latest["revision"]),
                ).fetchone()
                if (
                    run is None
                    or run["status"] != "COMPLETED"
                    or revision is None
                    or revision["status"] != "ARCHIVED"
                ):
                    fail(
                        "SCHEDULER_STATE_INVALID",
                        "Archived Delivery state is inconsistent",
                        rootId=latest["root_id"],
                    )
        state = (
            latest["status"]
            if latest["status"] in {"ARCHIVED", "PREPARED"}
            else run["status"] if run is not None else latest["status"]
        )
        projection_issues: list[dict[str, str]] = []
        for row in rows:
            projection_root_id = row["root_id"]
            try:
                repository.write_projections(
                    projection_root_id,
                    refresh_workspace_overview=False,
                )
            except (GatedLoopError, OSError) as error:
                if projection_root_id == latest["root_id"]:
                    raise
                projection_issues.append(
                    {
                        "rootId": projection_root_id,
                        "code": (
                            error.code
                            if isinstance(error, GatedLoopError)
                            else "SCHEDULER_PROJECTION_REFRESH_FAILED"
                        ),
                        "message": (
                            error.message
                            if isinstance(error, GatedLoopError)
                            else "Controller could not refresh this Delivery projection"
                        ),
                    }
                )
        repository.write_workspace_overview()
        result: dict[str, Any] = {
            "status": "PREPARED" if state == "PREPARED" else state,
            "rootId": latest["root_id"],
            "deliveryRevision": latest["revision"],
            "controlRoot": self.governance_directory,
        }
        if run is not None:
            result["executionMode"] = run["execution_mode"]
        if state == "ARCHIVED":
            result["archivedAt"] = latest["updated_at"]
            result["runStatus"] = run["status"]
            result["nextAction"] = "START_NEW_DELIVERY"
        if workspace_root is not None:
            if state == "CHOICE_READY":
                result["workspaceIsolation"] = {
                    "mode": "UNBOUND_EXECUTION_CHOICE",
                    "workspaceKey": None,
                }
            elif state == "HANDOFF_READY":
                result["workspaceIsolation"] = {
                    "mode": "UNBOUND_MANUAL_HANDOFF",
                    "workspaceKey": None,
                }
            else:
                result["workspaceIsolation"] = self.binding(
                    latest["root_id"]
                )
        if projection_issues:
            result["projectionIssues"] = projection_issues
        git_binding = latest_hierarchy["delivery"].get("gitBinding")
        if git_binding is not None:
            result["gitBinding"] = git_binding
        result["projectScopes"] = latest_hierarchy["delivery"].get(
            "projectScopes",
            [],
        )
        return result


__all__ = ("DeliveryWorkspaceStore",)
