from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest

from hdg import planning
from hdg.git_binding import (
    git_repository_identity,
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

if __name__ == "__main__":
    unittest.main()
