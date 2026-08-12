from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest

from hdg import planning
from hdg.errors import GatedLoopError
from hdg.git_binding import (
    git_repository_identity,
    verify_delivery_project_scopes,
)
from hdg.mcp_tools import call_tool
from hdg.repository import SchedulerRepository
from hdg.workspace_identity import (
    legacy_path_workspace_key,
)

from .test_scheduler_contracts import (
    bind_delivery_to_git,
    isolated_task_hierarchy,
)


def _git(workspace: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def _repository(path: Path) -> str:
    path.mkdir()
    _git(path, "init", "--initial-branch=main")
    _git(path, "config", "user.name", "Workspace Identity Tests")
    _git(
        path,
        "config",
        "user.email",
        "workspace-identity-tests@example.invalid",
    )
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "Initial baseline")
    return _git(path, "rev-parse", "HEAD")


class WorkspaceCoordinationTests(unittest.TestCase):
    def test_legacy_path_binding_remains_discoverable_in_place(self) -> None:
        with TemporaryDirectory() as root:
            workspace = Path(root, "workspace")
            workspace.mkdir()
            hierarchy = isolated_task_hierarchy(
                "d-legacy-workspace",
                "t-legacy-workspace",
            )
            prepared = planning.prepare_hierarchy(
                root=str(workspace),
                hierarchy=hierarchy,
                workspace_root=str(workspace),
            )
            planning.freeze_hierarchy(
                root=str(workspace),
                root_id=prepared["rootId"],
                expected_delivery_revision=1,
                expected_hierarchy_fingerprint=prepared[
                    "hierarchyFingerprint"
                ],
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
            )
            database = workspace / ".layered-delivery" / "scheduler.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE delivery_workspaces SET workspace_key = ? "
                    "WHERE root_id = ?",
                    (
                        legacy_path_workspace_key(workspace),
                        prepared["rootId"],
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            status = call_tool(
                "workspace_status",
                {"root_id": prepared["rootId"]},
                root=str(workspace),
                workspace_root=str(workspace),
            )

            self.assertEqual(status["status"], "ACTIVE")
            self.assertEqual(
                status["workspaceIsolation"]["identityVersion"],
                "PATH_V1",
            )

    def test_git_workspace_identity_survives_linked_worktree_move(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            _repository(repository)
            original = Path(root, "delivery-original")
            moved = Path(root, "delivery-moved")
            _git(
                repository,
                "worktree",
                "add",
                "-b",
                "feature/stable-delivery",
                str(original),
                "main",
            )
            original_key = SchedulerRepository.workspace_key(original)

            _git(
                repository,
                "worktree",
                "move",
                str(original),
                str(moved),
            )

            self.assertEqual(
                SchedulerRepository.workspace_key(moved),
                original_key,
            )

    def test_workspace_identity_survives_branch_switch_in_same_checkout(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            _repository(repository)
            main_key = SchedulerRepository.workspace_key(repository)

            _git(
                repository,
                "switch",
                "-c",
                "feature/same-physical-checkout",
            )

            self.assertEqual(
                SchedulerRepository.workspace_key(repository),
                main_key,
            )

    def test_workspace_identity_distinguishes_independent_clone_slots(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            original = Path(root, "repository-original")
            clone = Path(root, "repository-clone")
            _repository(original)
            original_key = SchedulerRepository.workspace_key(original)

            _git(Path(root), "clone", str(original), str(clone))

            self.assertNotEqual(
                SchedulerRepository.workspace_key(clone),
                original_key,
            )
            original_checkout = Path(root, "original-slots", "delivery")
            clone_checkout = Path(root, "clone-slots", "delivery")
            original_checkout.parent.mkdir()
            clone_checkout.parent.mkdir()
            for repository, checkout in (
                (original, original_checkout),
                (clone, clone_checkout),
            ):
                _git(
                    repository,
                    "worktree",
                    "add",
                    "-b",
                    "feature/matching-slot",
                    str(checkout),
                    "main",
                )

            self.assertNotEqual(
                SchedulerRepository.workspace_key(original_checkout),
                SchedulerRepository.workspace_key(clone_checkout),
            )


    def test_existing_linked_checkouts_keep_distinct_physical_identities(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            _repository(repository)
            first = Path(root, "delivery-first")
            second = Path(root, "delivery-second")
            for branch_ref, checkout in (
                ("feature/first-delivery", first),
                ("feature/second-delivery", second),
            ):
                _git(
                    repository,
                    "worktree",
                    "add",
                    "-b",
                    branch_ref,
                    str(checkout),
                    "main",
                )

            self.assertNotEqual(
                SchedulerRepository.workspace_key(first),
                SchedulerRepository.workspace_key(second),
            )

    def test_branch_usage_is_scoped_to_the_git_repository(self) -> None:
        with TemporaryDirectory() as root:
            primary = Path(root, "primary")
            secondary = Path(root, "secondary")
            primary_base = _repository(primary)
            secondary_base = _repository(secondary)
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-scoped", "t-scoped"),
                branch_ref="feature/primary-only",
                base_commit=primary_base,
            )
            hierarchy["delivery"]["projectScopes"] = [
                {
                    "id": "primary",
                    "workspaceRoot": str(primary),
                    "access": "READ_WRITE",
                    "gitBinding": deepcopy(
                        hierarchy["delivery"]["gitBinding"]
                    ),
                },
                {
                    "id": "secondary",
                    "workspaceRoot": str(secondary),
                    "access": "READ_ONLY",
                    "gitBinding": {
                        "branchRef": "feature/shared-name",
                        "baseRef": "main",
                        "baseCommit": secondary_base,
                        "integrationTarget": "main",
                    },
                },
            ]
            call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(primary),
                workspace_root=str(primary),
                trusted_host_adapter="codex",
            )
            scheduler = SchedulerRepository(str(primary))

            self.assertEqual(
                scheduler.git_branch_usage(
                    "feature/shared-name",
                    repository_key=git_repository_identity(str(primary)),
                ),
                [],
            )
            secondary_usage = scheduler.git_branch_usage(
                "feature/shared-name",
                repository_key=git_repository_identity(str(secondary)),
            )
            self.assertEqual(
                [item["rootId"] for item in secondary_usage],
                ["d-scoped"],
            )

    def test_secondary_scope_does_not_jump_to_a_linked_checkout(self) -> None:
        with TemporaryDirectory() as root:
            primary = Path(root, "primary")
            secondary = Path(root, "secondary")
            primary_base = _repository(primary)
            secondary_base = _repository(secondary)
            branch_ref = "feature/current-workspace-only"
            _git(primary, "switch", "-c", branch_ref)
            linked_secondary = Path(root, "secondary-linked")
            _git(
                secondary,
                "worktree",
                "add",
                "-b",
                branch_ref,
                str(linked_secondary),
                "main",
            )
            delivery = {
                "id": "d-current-workspace-only",
                "gitBinding": {
                    "branchRef": branch_ref,
                    "baseRef": "main",
                    "baseCommit": primary_base,
                    "integrationTarget": "main",
                },
                "projectScopes": [
                    {
                        "id": "primary",
                        "workspaceRoot": str(primary),
                        "access": "READ_WRITE",
                        "gitBinding": {
                            "branchRef": branch_ref,
                            "baseRef": "main",
                            "baseCommit": primary_base,
                            "integrationTarget": "main",
                        },
                    },
                    {
                        "id": "secondary",
                        "workspaceRoot": str(secondary),
                        "access": "READ_WRITE",
                        "gitBinding": {
                            "branchRef": branch_ref,
                            "baseRef": "main",
                            "baseCommit": secondary_base,
                            "integrationTarget": "main",
                        },
                    },
                ],
            }

            with self.assertRaises(GatedLoopError) as caught:
                verify_delivery_project_scopes(
                    str(primary),
                    delivery,
                    preparing=True,
                )

            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_BRANCH_MISMATCH",
            )
            self.assertEqual(
                _git(secondary, "branch", "--show-current"),
                "main",
            )
            self.assertEqual(
                _git(linked_secondary, "branch", "--show-current"),
                branch_ref,
            )

    def test_shared_workspace_dirty_state_is_not_implicitly_attributed(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            _repository(repository)
            workspace = Path(root, "shared-workspace")
            branch_ref = "feature/shared-dirty"
            _git(
                repository,
                "worktree",
                "add",
                "-b",
                branch_ref,
                str(workspace),
                "main",
            )
            first = isolated_task_hierarchy(
                "d-shared-dirty-first",
                "t-shared-dirty-first",
            )
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": first},
                root=str(repository),
                workspace_root=str(workspace),
                trusted_host_adapter="codex",
            )
            confirmed = call_tool(
                "confirm_development_baseline",
                {
                    "root_id": "d-shared-dirty-first",
                    "selection": branch_ref,
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(workspace),
                trusted_host_adapter="codex",
            )
            call_tool(
                "select_execution_mode",
                {
                    "root_id": "d-shared-dirty-first",
                    "selection": "AUTOMATIC",
                    "expected_hierarchy_fingerprint": confirmed[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": confirmed[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(workspace),
                trusted_host_adapter="codex",
            )
            (workspace / "unattributed.txt").write_text(
                "belongs to an unknown Delivery\n",
                encoding="utf-8",
            )
            second_preview = call_tool(
                "preview_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-shared-dirty-second",
                        "t-shared-dirty-second",
                    )
                },
                root=str(repository),
                workspace_root=str(workspace),
                trusted_host_adapter="codex",
            )

            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "confirm_development_baseline",
                    {
                        "root_id": "d-shared-dirty-second",
                        "selection": branch_ref,
                        "expected_hierarchy_fingerprint": second_preview[
                            "hierarchyFingerprint"
                        ],
                        "confirmed_by": "human",
                    },
                    root=str(repository),
                    workspace_root=str(workspace),
                    trusted_host_adapter="codex",
                )

            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_DIRTY_CONFIRMATION_REQUIRED",
            )
            self.assertIn(
                "dirtyStateFingerprint",
                caught.exception.details,
            )
            first_status = call_tool(
                "workspace_status",
                {"root_id": "d-shared-dirty-first"},
                root=str(repository),
                workspace_root=str(workspace),
                trusted_host_adapter="codex",
            )
            self.assertEqual(first_status["status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
