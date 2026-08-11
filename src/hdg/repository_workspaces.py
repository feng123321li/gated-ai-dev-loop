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
            "mode": "MULTI_DELIVERY_WORKSPACE",
            "workspaceKey": row["workspace_key"],
            "identityVersion": workspace_identity_version(
                row["workspace_key"]
            ),
        }

    def serial_turns(
        self,
        workspace_root: str | os.PathLike[str],
        *,
        exclude_root_id: str | None = None,
        include_terminal: bool = False,
    ) -> list[dict[str, str]]:
        """Return unfinished Delivery turns bound to one physical workspace."""

        repository = self.repository
        repository._assert_no_legacy_state()
        if not repository.database_path.is_file():
            return []
        identity = workspace_identity(workspace_root)
        candidate_keys = {identity.key, identity.legacy_path_key}
        current_branch = identity.material.get("branchRef")
        if (
            identity.kind == "GIT_CHECKOUT"
            and isinstance(current_branch, str)
        ):
            candidate_keys.add(
                git_branch_workspace_key(
                    identity.material["repositoryKey"],
                    current_branch,
                )
            )
        placeholders = ", ".join("?" for _item in candidate_keys)
        with repository.read() as connection:
            rows = connection.execute(
                "SELECT w.root_id, w.workspace_key, w.created_at, "
                "h.status AS hierarchy_status, r.status AS run_status "
                "FROM delivery_workspaces w "
                "JOIN hierarchies h ON h.root_id = w.root_id "
                "LEFT JOIN runs r "
                "ON r.root_id = h.root_id AND r.revision = h.revision "
                f"WHERE w.workspace_key IN ({placeholders}) "
                "ORDER BY w.created_at ASC, w.root_id ASC",
                tuple(sorted(candidate_keys)),
            ).fetchall()
        terminal_statuses = {
            "ARCHIVED",
            "COMPLETED",
            "CANCELLED",
            "SUPERSEDED",
        }
        turns = []
        for row in rows:
            if row["root_id"] == exclude_root_id:
                continue
            effective_status = (
                row["run_status"]
                if row["run_status"] is not None
                else row["hierarchy_status"]
            )
            if (
                effective_status in terminal_statuses
                and not include_terminal
            ):
                continue
            turns.append(
                {
                    "rootId": row["root_id"],
                    "status": effective_status,
                    "workspaceKey": row["workspace_key"],
                    "createdAt": row["created_at"],
                }
            )
        return turns

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
            self._upgrade_binding_in_place(
                root_id,
                previous_workspace_key=identity.legacy_path_key,
                stable_workspace_key=identity.key,
            )
            return
        if binding["workspaceKey"] == identity.key:
            return
        if (
            identity.kind == "GIT_CHECKOUT"
            and workspace_identity_version(binding["workspaceKey"])
            == "GIT_BRANCH_V1"
        ):
            stored = self.repository.hierarchy(root_id)
            delivery = stored["hierarchy"]["delivery"]
            branch_refs = {
                candidate["branchRef"]
                for candidate in [
                    delivery.get("gitBinding"),
                    *[
                        scope.get("gitBinding")
                        for scope in delivery.get("projectScopes", [])
                    ],
                ]
                if isinstance(candidate, dict)
                and isinstance(candidate.get("branchRef"), str)
            }
            matching_branches = sorted(
                branch_ref
                for branch_ref in branch_refs
                if binding["workspaceKey"]
                == git_branch_workspace_key(
                    identity.material["repositoryKey"],
                    branch_ref,
                )
            )
            if len(matching_branches) == 1:
                expected_branch = matching_branches[0]
                actual_branch = identity.material.get("branchRef")
                if actual_branch != expected_branch:
                    fail(
                        "SCHEDULER_GIT_BRANCH_MISMATCH",
                        "Delivery worktree is checked out on another branch",
                        expectedBranchRef=expected_branch,
                        actualBranchRef=actual_branch,
                    )
                self._upgrade_binding_in_place(
                    root_id,
                    previous_workspace_key=binding["workspaceKey"],
                    stable_workspace_key=identity.key,
                )
                return
        if binding["workspaceKey"] != identity.key:
            fail(
                "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
                "This Delivery belongs to another conversation workspace",
                rootId=root_id,
                workspaceKey=binding["workspaceKey"],
            )

    def _upgrade_binding_in_place(
        self,
        root_id: str,
        *,
        previous_workspace_key: str,
        stable_workspace_key: str,
    ) -> None:
        """Normalize a proven prior identity to the current stable identity."""

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
            if current_binding["workspace_key"] != previous_workspace_key:
                fail(
                    "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
                    "Delivery workspace changed during identity upgrade",
                    rootId=root_id,
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
        current_identity = (
            workspace_identity(workspace_root)
            if workspace_root is not None
            else None
        )
        if current_identity is not None:
            prior_keys = {current_identity.legacy_path_key}
            current_branch = current_identity.material.get("branchRef")
            if (
                current_identity.kind == "GIT_CHECKOUT"
                and isinstance(current_branch, str)
            ):
                prior_keys.add(
                    git_branch_workspace_key(
                        current_identity.material["repositoryKey"],
                        current_branch,
                    )
                )
            placeholders = ", ".join("?" for _item in prior_keys)
            with repository.read() as connection:
                upgrade_root_ids = [
                    row["root_id"]
                    for row in connection.execute(
                        "SELECT root_id FROM delivery_workspaces "
                        f"WHERE workspace_key IN ({placeholders})",
                        tuple(sorted(prior_keys)),
                    ).fetchall()
                ]
            for upgrade_root_id in upgrade_root_ids:
                self.assert_bound(upgrade_root_id, workspace_root)
        with repository.read() as connection:
            rows = connection.execute(
                "SELECT * FROM hierarchies ORDER BY updated_at DESC, root_id ASC"
            ).fetchall()
            if not rows:
                return {
                    "status": "ABSENT",
                    "controlRoot": self.governance_directory,
                }
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
            elif root_id is None:
                bound_ids = {
                    row["root_id"]
                    for row in connection.execute(
                        "SELECT root_id FROM delivery_workspaces"
                    ).fetchall()
                }
            else:
                bound_ids = set()
            if current_identity is not None or root_id is None:
                candidates = [
                    row
                    for row in candidates
                    if row["root_id"] in bound_ids
                    or (
                        root_id is not None
                        and row["status"]
                        in {"CHOICE_READY", "HANDOFF_READY"}
                    )
                ]
            if not candidates:
                return {
                    "status": "ABSENT",
                    "controlRoot": self.governance_directory,
                    "workspaceIsolation": (
                        {
                            "mode": "MULTI_DELIVERY_WORKSPACE",
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
            evaluated_candidates = []
            for candidate in candidates:
                candidate_run = connection.execute(
                    "SELECT status, execution_mode FROM runs "
                    "WHERE root_id = ? AND revision = ?",
                    (candidate["root_id"], candidate["revision"]),
                ).fetchone()
                candidate_state = (
                    candidate["status"]
                    if candidate["status"] in {"ARCHIVED", "PREPARED"}
                    else (
                        candidate_run["status"]
                        if candidate_run is not None
                        else candidate["status"]
                    )
                )
                evaluated_candidates.append(
                    (candidate, candidate_run, candidate_state)
                )
            unfinished_candidates = [
                item
                for item in evaluated_candidates
                if item[2]
                not in {
                    "ARCHIVED",
                    "COMPLETED",
                    "CANCELLED",
                    "SUPERSEDED",
                }
            ]
            if root_id is None and len(unfinished_candidates) > 1:
                candidate_deliveries = []
                for candidate, _candidate_run, candidate_state in (
                    unfinished_candidates
                ):
                    candidate_hierarchy, _ = (
                        self.validate_stored_definition(candidate)
                    )
                    delivery = candidate_hierarchy["delivery"]
                    summary = {
                        "rootId": candidate["root_id"],
                        "deliveryRevision": candidate["revision"],
                        "status": candidate_state,
                        "title": delivery["title"],
                        "updatedAt": candidate["updated_at"],
                    }
                    requirement_key = delivery.get("requirementKey")
                    if requirement_key is not None:
                        summary["requirementKey"] = requirement_key
                    candidate_deliveries.append(summary)
                return {
                    "status": "DELIVERY_SELECTION_REQUIRED",
                    "controlRoot": self.governance_directory,
                    "workspaceIsolation": (
                        {
                            "mode": "MULTI_DELIVERY_WORKSPACE",
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
                    "candidateDeliveries": candidate_deliveries,
                    "canCreateDelivery": True,
                    "nextAction": (
                        "CALL_WORKSPACE_STATUS_WITH_ROOT_ID_"
                        "OR_PREVIEW_NEW_DELIVERY"
                    ),
                }
            selected = (
                unfinished_candidates[0]
                if root_id is None and unfinished_candidates
                else evaluated_candidates[0]
            )
            latest, run, state = selected
            latest_hierarchy, _ = self.validate_stored_definition(latest)
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
