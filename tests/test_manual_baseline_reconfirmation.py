from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hdg.errors import GatedLoopError
from hdg.mcp_tools import call_tool
from hdg.repository import SchedulerRepository

from .test_scheduler_contracts import (
    bind_delivery_to_git,
    git_command,
    git_delivery_checkout,
    isolated_task_hierarchy,
)


def manual_handoff_with_receiving_drift(
    root: str,
    *,
    delivery_id: str,
) -> tuple[Path, Path, str, dict]:
    repository, planned_worktree, base_commit, planned_branch = (
        git_delivery_checkout(root, delivery_id=delivery_id)
    )
    hierarchy = bind_delivery_to_git(
        isolated_task_hierarchy(delivery_id, f"t-{delivery_id}"),
        branch_ref=planned_branch,
        base_commit=base_commit,
    )
    preview = call_tool(
        "preview_hierarchy",
        {"hierarchy": hierarchy},
        root=str(repository),
        workspace_root=str(planned_worktree),
        trusted_host_adapter="codex",
    )
    handoff = call_tool(
        "select_execution_mode",
        {
            "root_id": delivery_id,
            "selection": "MANUAL",
            "expected_hierarchy_fingerprint": preview[
                "hierarchyFingerprint"
            ],
            "expected_graph_fingerprint": preview["graphFingerprint"],
            "authorized_project_ids": [],
            "confirmed_by": "human",
        },
        root=str(repository),
        workspace_root=str(planned_worktree),
        trusted_host_adapter="codex",
    )

    receiving_branch = f"feature/{delivery_id}-receiving"
    receiving_worktree = Path(root, "worktrees", f"{delivery_id}-receiving")
    git_command(
        repository,
        "worktree",
        "add",
        "-b",
        receiving_branch,
        str(receiving_worktree),
        "main",
    )
    return repository, receiving_worktree, receiving_branch, handoff


def start_manual(
    repository: Path,
    receiving_worktree: Path,
    handoff: dict,
) -> dict:
    return call_tool(
        "start_manual_handoff",
        {
            "root_id": handoff["rootId"],
            "expected_hierarchy_fingerprint": handoff[
                "hierarchyFingerprint"
            ],
            "expected_graph_fingerprint": handoff["graphFingerprint"],
            "started_by": "manual-orchestrator",
        },
        root=str(repository),
        workspace_root=str(receiving_worktree),
        trusted_host_adapter="codex",
    )


class ManualBaselineReconfirmationTests(unittest.TestCase):
    def assert_no_run_or_workspace_binding(
        self,
        repository: Path,
        root_id: str,
    ) -> None:
        state = SchedulerRepository(str(repository))
        with state.read() as connection:
            run_count = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE root_id = ?",
                (root_id,),
            ).fetchone()[0]
            workspace_count = connection.execute(
                "SELECT COUNT(*) FROM delivery_workspaces "
                "WHERE root_id = ?",
                (root_id,),
            ).fetchone()[0]
        self.assertEqual(run_count, 0)
        self.assertEqual(workspace_count, 0)

    def test_wrong_receiving_branch_blocks_without_writing_control_state(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, receiving, receiving_branch, handoff = (
                manual_handoff_with_receiving_drift(
                    root,
                    delivery_id="d-manual-drift",
                )
            )

            blocked = start_manual(repository, receiving, handoff)

            self.assertEqual(blocked["status"], "HANDOFF_READY")
            self.assertEqual(
                blocked["manualStartState"],
                "BLOCKED_DEVELOPMENT_BASELINE_CONFIRMATION",
            )
            interaction = blocked["pendingInteraction"]
            self.assertEqual(interaction["kind"], "DEVELOPMENT_BASELINE")
            self.assertEqual(
                interaction["interactionContext"],
                "MANUAL_HANDOFF_START",
            )
            self.assertIn(
                receiving_branch,
                {option["id"] for option in interaction["options"]},
            )
            self.assertFalse(blocked["graphRunCreated"])
            self.assertEqual(blocked["deliveryRevision"], 1)
            self.assertEqual(
                blocked["hierarchyFingerprint"],
                handoff["hierarchyFingerprint"],
            )
            self.assertEqual(
                blocked["graphFingerprint"],
                handoff["graphFingerprint"],
            )
            self.assert_no_run_or_workspace_binding(
                repository,
                handoff["rootId"],
            )
            history = SchedulerRepository(str(repository)).revision_history(
                handoff["rootId"]
            )
            self.assertEqual(history["currentRevision"], 1)
            self.assertEqual(
                [item["status"] for item in history["revisions"]],
                ["HANDOFF_READY"],
            )

    def test_reconfirm_current_branch_creates_revision_two_then_starts(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, receiving, receiving_branch, handoff = (
                manual_handoff_with_receiving_drift(
                    root,
                    delivery_id="d-manual-reconfirm",
                )
            )
            blocked = start_manual(repository, receiving, handoff)
            interaction = blocked["pendingInteraction"]

            confirmed = call_tool(
                "confirm_development_baseline",
                {
                    "root_id": handoff["rootId"],
                    "selection": receiving_branch,
                    "expected_delivery_revision": 1,
                    "expected_hierarchy_fingerprint": handoff[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": handoff[
                        "graphFingerprint"
                    ],
                    "baseline_context_fingerprint": interaction[
                        "baselineContextFingerprint"
                    ],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(receiving),
                trusted_host_adapter="codex",
            )

            self.assertEqual(confirmed["status"], "HANDOFF_READY")
            self.assertEqual(confirmed["previousRevision"], 1)
            self.assertEqual(confirmed["deliveryRevision"], 2)
            self.assertNotEqual(
                confirmed["hierarchyFingerprint"],
                handoff["hierarchyFingerprint"],
            )
            self.assertNotEqual(
                confirmed["graphFingerprint"],
                handoff["graphFingerprint"],
            )
            self.assertNotEqual(
                confirmed["manualHandoff"]["path"],
                handoff["manualHandoff"]["path"],
            )
            self.assertTrue(
                Path(
                    repository,
                    confirmed["manualHandoff"]["path"],
                ).is_file()
            )
            history = SchedulerRepository(str(repository)).revision_history(
                handoff["rootId"]
            )
            self.assertEqual(history["currentRevision"], 2)
            self.assertEqual(
                [item["status"] for item in history["revisions"]],
                ["SUPERSEDED", "HANDOFF_READY"],
            )

            started = start_manual(repository, receiving, confirmed)

            self.assertEqual(started["status"], "ACTIVE")
            self.assertEqual(started["executionMode"], "manual")
            self.assertTrue(started["graphRunCreated"])
            self.assertFalse(started["manualStartAlreadyApplied"])

    def test_changed_git_context_rejects_stale_confirmation(self) -> None:
        with TemporaryDirectory() as root:
            repository, receiving, receiving_branch, handoff = (
                manual_handoff_with_receiving_drift(
                    root,
                    delivery_id="d-manual-context",
                )
            )
            blocked = start_manual(repository, receiving, handoff)
            context_fingerprint = blocked["pendingInteraction"][
                "baselineContextFingerprint"
            ]
            Path(receiving, "context-changed.txt").write_text(
                "change the branch head after presenting the interaction\n",
                encoding="utf-8",
            )
            git_command(receiving, "add", "context-changed.txt")
            git_command(receiving, "commit", "-m", "Change receiving HEAD")

            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "confirm_development_baseline",
                    {
                        "root_id": handoff["rootId"],
                        "selection": receiving_branch,
                        "expected_delivery_revision": 1,
                        "expected_hierarchy_fingerprint": handoff[
                            "hierarchyFingerprint"
                        ],
                        "expected_graph_fingerprint": handoff[
                            "graphFingerprint"
                        ],
                        "baseline_context_fingerprint": context_fingerprint,
                        "confirmed_by": "human",
                    },
                    root=str(repository),
                    workspace_root=str(receiving),
                    trusted_host_adapter="codex",
                )

            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_MANUAL_BASELINE_CONTEXT_STALE",
            )
            self.assert_no_run_or_workspace_binding(
                repository,
                handoff["rootId"],
            )

    def test_repeated_blocked_start_keeps_context_fingerprint_stable(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, receiving, _receiving_branch, handoff = (
                manual_handoff_with_receiving_drift(
                    root,
                    delivery_id="d-manual-repeat",
                )
            )

            first = start_manual(repository, receiving, handoff)
            second = start_manual(repository, receiving, handoff)

            self.assertEqual(
                first["pendingInteraction"]["baselineContextFingerprint"],
                second["pendingInteraction"]["baselineContextFingerprint"],
            )
            self.assertEqual(
                first["pendingInteraction"],
                second["pendingInteraction"],
            )
            self.assert_no_run_or_workspace_binding(
                repository,
                handoff["rootId"],
            )
            history = SchedulerRepository(str(repository)).revision_history(
                handoff["rootId"]
            )
            self.assertEqual(history["currentRevision"], 1)

    def test_multi_project_manual_drift_fails_closed_before_reconfirmation(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            primary_container = Path(root, "primary")
            secondary_container = Path(root, "secondary")
            primary_container.mkdir()
            secondary_container.mkdir()
            (
                primary_repository,
                primary_worktree,
                primary_base_commit,
                primary_branch,
            ) = git_delivery_checkout(
                str(primary_container),
                delivery_id="d-manual-multi-drift",
            )
            (
                _secondary_repository,
                secondary_worktree,
                secondary_base_commit,
                secondary_branch,
            ) = git_delivery_checkout(
                str(secondary_container),
                delivery_id="d-manual-multi-drift",
            )
            self.assertEqual(primary_branch, secondary_branch)

            primary_binding = {
                "branchRef": primary_branch,
                "baseRef": "main",
                "baseCommit": primary_base_commit,
                "integrationTarget": "main",
            }
            secondary_binding = {
                "branchRef": secondary_branch,
                "baseRef": "main",
                "baseCommit": secondary_base_commit,
                "integrationTarget": "main",
            }
            hierarchy = isolated_task_hierarchy(
                "d-manual-multi-drift",
                "t-d-manual-multi-drift",
            )
            hierarchy["delivery"]["gitBinding"] = deepcopy(
                primary_binding
            )
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
                    "gitBinding": deepcopy(secondary_binding),
                },
            ]
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(primary_worktree),
                workspace_root=str(primary_worktree),
                trusted_host_adapter="codex",
            )
            handoff = call_tool(
                "select_execution_mode",
                {
                    "root_id": "d-manual-multi-drift",
                    "selection": "MANUAL",
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": preview[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": ["primary", "secondary"],
                    "confirmed_by": "human",
                },
                root=str(primary_worktree),
                workspace_root=str(primary_worktree),
                trusted_host_adapter="codex",
            )
            receiving_branch = "feature/d-manual-multi-drift-receiving"
            receiving_worktree = Path(root, "receiving")
            git_command(
                primary_repository,
                "worktree",
                "add",
                "-b",
                receiving_branch,
                str(receiving_worktree),
                "main",
            )

            with self.assertRaises(GatedLoopError) as caught:
                start_manual(
                    primary_worktree,
                    receiving_worktree,
                    handoff,
                )

            self.assertEqual(
                caught.exception.code,
                (
                    "SCHEDULER_MANUAL_MULTI_PROJECT_BASELINE_"
                    "RECONFIRMATION_UNSUPPORTED"
                ),
            )
            self.assert_no_run_or_workspace_binding(
                primary_worktree,
                handoff["rootId"],
            )
            history = SchedulerRepository(
                str(primary_worktree)
            ).revision_history(handoff["rootId"])
            self.assertEqual(history["currentRevision"], 1)
            self.assertEqual(
                [item["status"] for item in history["revisions"]],
                ["HANDOFF_READY"],
            )


if __name__ == "__main__":
    unittest.main()
