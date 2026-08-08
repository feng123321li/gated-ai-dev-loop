from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hdg.errors import GatedLoopError
from hdg.mcp_tools import call_tool
from hdg.repository import SchedulerRepository

from .test_scheduler_contracts import (
    git_command,
    git_delivery_checkout,
    isolated_task_hierarchy,
)


def git_binding(
    *,
    branch_ref: str,
    base_commit: str,
    base_ref: str = "main",
) -> dict[str, str]:
    return {
        "branchRef": branch_ref,
        "baseRef": base_ref,
        "baseCommit": base_commit,
        "integrationTarget": base_ref,
    }


class PendingInteractionTests(unittest.TestCase):
    def test_clean_git_preview_and_status_restore_baseline_interaction(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            _repository, worktree, _base_commit, _branch_ref = (
                git_delivery_checkout(root, delivery_id="d-pending-clean")
            )
            preview = call_tool(
                "preview_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-pending-clean",
                        "t-pending-clean",
                    )
                },
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )

            self.assertEqual(preview["status"], "CHOICE_READY")
            self.assertEqual(
                preview["pendingInteraction"]["kind"],
                "DEVELOPMENT_BASELINE",
            )
            self.assertEqual(
                preview["developmentBaseline"],
                preview["pendingInteraction"],
            )
            self.assertNotIn("executionChoice", preview)

            status = call_tool(
                "workspace_status",
                {"root_id": "d-pending-clean"},
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )

            self.assertEqual(status["status"], "CHOICE_READY")
            self.assertEqual(
                status["pendingInteraction"]["kind"],
                "DEVELOPMENT_BASELINE",
            )
            self.assertEqual(
                status["developmentBaseline"],
                status["pendingInteraction"],
            )
            self.assertNotIn("executionChoice", status)

    def test_confirmed_baseline_advances_pending_interaction_to_execution_mode(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            _repository, worktree, _base_commit, branch_ref = (
                git_delivery_checkout(root, delivery_id="d-pending-confirm")
            )
            preview = call_tool(
                "preview_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-pending-confirm",
                        "t-pending-confirm",
                    )
                },
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )

            confirmed = call_tool(
                "confirm_development_baseline",
                {
                    "root_id": "d-pending-confirm",
                    "selection": branch_ref,
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "confirmed_by": "human",
                },
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )

            self.assertEqual(confirmed["status"], "CHOICE_READY")
            self.assertEqual(
                confirmed["pendingInteraction"]["kind"],
                "EXECUTION_MODE",
            )
            self.assertEqual(
                confirmed["executionChoice"],
                confirmed["pendingInteraction"],
            )
            self.assertNotIn("developmentBaseline", confirmed)

    def test_invalid_new_branch_does_not_poison_preference_or_choice_ready_state(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            _repository, worktree, _base_commit, _branch_ref = (
                git_delivery_checkout(
                    root,
                    delivery_id="d-pending-invalid-branch",
                )
            )
            hierarchy = isolated_task_hierarchy(
                "d-pending-invalid-branch",
                "t-pending-invalid-branch",
            )
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )
            interaction = preview["pendingInteraction"]

            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "confirm_development_baseline",
                    {
                        "root_id": "d-pending-invalid-branch",
                        "selection": "NEW_FROM_MAINLINE",
                        "branch_name": "bad..branch",
                        "expected_delivery_revision": 1,
                        "expected_hierarchy_fingerprint": preview[
                            "hierarchyFingerprint"
                        ],
                        "expected_graph_fingerprint": preview[
                            "graphFingerprint"
                        ],
                        "baseline_context_fingerprint": interaction[
                            "baselineContextFingerprint"
                        ],
                        "confirmed_by": "human",
                    },
                    root=str(worktree),
                    workspace_root=str(worktree),
                    trusted_host_adapter="codex",
                )

            self.assertEqual(
                caught.exception.code,
                "DELIVERY_GIT_BINDING_INVALID",
            )
            state = SchedulerRepository(str(worktree))
            self.assertIsNone(
                state.development_preference("d-pending-invalid-branch")
            )
            stored = state.hierarchy("d-pending-invalid-branch")
            self.assertEqual(stored["status"], "CHOICE_READY")
            self.assertNotIn(
                "gitBinding",
                stored["hierarchy"]["delivery"],
            )

            retried = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )
            self.assertEqual(retried["status"], "CHOICE_READY")
            self.assertEqual(
                retried["pendingInteraction"]["kind"],
                "DEVELOPMENT_BASELINE",
            )

    def test_non_git_preview_has_execution_mode_pending_interaction(self) -> None:
        with TemporaryDirectory() as root:
            preview = call_tool(
                "preview_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-pending-non-git",
                        "t-pending-non-git",
                    )
                },
                root=root,
                workspace_root=root,
                trusted_host_adapter="codex",
            )

            self.assertEqual(
                preview["pendingInteraction"]["kind"],
                "EXECUTION_MODE",
            )
            self.assertEqual(
                preview["executionChoice"],
                preview["pendingInteraction"],
            )
            self.assertNotIn("developmentBaseline", preview)

    def test_controller_git_error_during_baseline_discovery_is_not_downgraded(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            expected = GatedLoopError(
                "SCHEDULER_GIT_DISCOVERY_TEST_FAILURE",
                "synthetic Git discovery failure",
            )
            with patch(
                "hdg.planning.inspect_delivery_git_workspace",
                side_effect=expected,
            ):
                with self.assertRaises(GatedLoopError) as caught:
                    call_tool(
                        "preview_hierarchy",
                        {
                            "hierarchy": isolated_task_hierarchy(
                                "d-pending-git-error",
                                "t-pending-git-error",
                            )
                        },
                        root=root,
                        workspace_root=root,
                    )

            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_DISCOVERY_TEST_FAILURE",
            )

    def test_unexpected_baseline_discovery_error_is_not_downgraded(self) -> None:
        with TemporaryDirectory() as root:
            with patch(
                "hdg.planning.inspect_delivery_git_workspace",
                side_effect=RuntimeError("synthetic implementation failure"),
            ):
                with self.assertRaises(RuntimeError):
                    call_tool(
                        "preview_hierarchy",
                        {
                            "hierarchy": isolated_task_hierarchy(
                                "d-pending-unexpected-error",
                                "t-pending-unexpected-error",
                            )
                        },
                        root=root,
                        workspace_root=root,
                    )

    def test_dirty_git_without_binding_still_requires_baseline_interaction(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            _repository, worktree, _base_commit, _branch_ref = (
                git_delivery_checkout(root, delivery_id="d-pending-dirty")
            )
            Path(worktree, "uncommitted.txt").write_text(
                "pending change\n",
                encoding="utf-8",
            )

            preview = call_tool(
                "preview_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-pending-dirty",
                        "t-pending-dirty",
                    )
                },
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )

            self.assertEqual(
                preview["pendingInteraction"]["kind"],
                "DEVELOPMENT_BASELINE",
            )
            self.assertEqual(
                preview["developmentBaseline"],
                preview["pendingInteraction"],
            )
            self.assertNotIn("executionChoice", preview)

    def test_tracked_dirty_content_change_invalidates_presented_state_fingerprint(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            _repository, worktree, _base_commit, _branch_ref = (
                git_delivery_checkout(
                    root,
                    delivery_id="d-pending-dirty-content",
                )
            )
            tracked = Path(worktree, "tracked.txt")
            tracked.write_text("committed\n", encoding="utf-8")
            git_command(worktree, "add", "tracked.txt")
            git_command(worktree, "commit", "-m", "Add tracked file")
            tracked.write_text("first dirty content\n", encoding="utf-8")

            preview = call_tool(
                "preview_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-pending-dirty-content",
                        "t-pending-dirty-content",
                    )
                },
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )
            first = preview["pendingInteraction"][
                "dirtyStateFingerprint"
            ]

            tracked.write_text("second dirty content\n", encoding="utf-8")
            status = call_tool(
                "workspace_status",
                {"root_id": "d-pending-dirty-content"},
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )
            second = status["pendingInteraction"][
                "dirtyStateFingerprint"
            ]

            self.assertNotEqual(first, second)

    def test_secondary_git_scope_without_binding_is_rejected_during_preview(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            primary_container = Path(root, "primary")
            secondary_container = Path(root, "secondary")
            primary_container.mkdir()
            secondary_container.mkdir()
            (
                _primary_repository,
                primary_worktree,
                primary_base_commit,
                primary_branch_ref,
            ) = git_delivery_checkout(
                str(primary_container),
                delivery_id="d-pending-multi",
            )
            (
                _secondary_repository,
                secondary_worktree,
                _secondary_base_commit,
                secondary_branch_ref,
            ) = git_delivery_checkout(
                str(secondary_container),
                delivery_id="d-pending-multi",
            )
            self.assertEqual(primary_branch_ref, secondary_branch_ref)

            hierarchy = isolated_task_hierarchy(
                "d-pending-multi",
                "t-pending-multi",
            )
            primary_binding = git_binding(
                branch_ref=primary_branch_ref,
                base_commit=primary_base_commit,
            )
            hierarchy["delivery"]["gitBinding"] = deepcopy(primary_binding)
            hierarchy["delivery"]["projectScopes"] = [
                {
                    "id": "primary",
                    "workspaceRoot": str(primary_worktree.resolve()),
                    "access": "READ_WRITE",
                    "gitBinding": deepcopy(primary_binding),
                },
                {
                    "id": "secondary",
                    "workspaceRoot": str(secondary_worktree.resolve()),
                    "access": "READ_WRITE",
                },
            ]

            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "preview_hierarchy",
                    {"hierarchy": hierarchy},
                    root=str(primary_worktree),
                    workspace_root=str(primary_worktree),
                    trusted_host_adapter="claude-code",
                )

            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_PROJECT_BASELINE_INCOMPLETE",
            )


if __name__ == "__main__":
    unittest.main()
