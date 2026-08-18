from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hdg.errors import GatedLoopError
from hdg.mcp_tools import call_tool
from hdg.repository import SchedulerRepository

from .test_scheduler_contracts import git_command, isolated_task_hierarchy
from .test_workspace_execution_strategy import (
    _confirm_existing_branch,
    _confirm_new_branch,
    _repository,
    _select,
)


def _select_manual(repository: Path, confirmed: dict) -> dict:
    return call_tool(
        "select_execution_mode",
        {
            "root_id": confirmed["rootId"],
            "selection": "MANUAL",
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
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )


def _start_manual(repository: Path, handoff: dict) -> dict:
    return call_tool(
        "start_manual_handoff",
        {
            "root_id": handoff["rootId"],
            "expected_hierarchy_fingerprint": handoff[
                "hierarchyFingerprint"
            ],
            "expected_graph_fingerprint": handoff[
                "graphFingerprint"
            ],
            "started_by": "manual-agent",
        },
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )


class FrozenExecutionGuardTests(unittest.TestCase):
    def test_frozen_resume_and_manual_start_reject_the_wrong_branch(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            automatic_branch = "feature/d-frozen-auto-branch"
            git_command(repository, "switch", "-c", automatic_branch)
            automatic = _confirm_existing_branch(
                repository,
                "d-frozen-auto-branch",
                "t-frozen-auto-branch",
                automatic_branch,
            )
            _select(repository, automatic)
            git_command(repository, "switch", "main")

            with self.assertRaises(GatedLoopError) as rejected:
                call_tool(
                    "resume_execution_mode",
                    {
                        "root_id": automatic["rootId"],
                        "expected_hierarchy_fingerprint": automatic[
                            "hierarchyFingerprint"
                        ],
                        "expected_graph_fingerprint": automatic[
                            "graphFingerprint"
                        ],
                    },
                    root=str(repository),
                    workspace_root=str(repository),
                    trusted_host_adapter="codex",
                )
            self.assertEqual(
                rejected.exception.code,
                "SCHEDULER_GIT_BRANCH_MISMATCH",
            )

        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            manual_branch = "feature/d-frozen-manual-branch"
            git_command(repository, "switch", "-c", manual_branch)
            manual = _confirm_existing_branch(
                repository,
                "d-frozen-manual-branch",
                "t-frozen-manual-branch",
                manual_branch,
            )
            handoff = _select_manual(repository, manual)
            started = _start_manual(repository, handoff)
            self.assertEqual(started["status"], "ACTIVE")
            git_command(repository, "switch", "main")

            with self.assertRaises(GatedLoopError) as rejected:
                _start_manual(repository, handoff)
            self.assertEqual(
                rejected.exception.code,
                "SCHEDULER_GIT_BRANCH_MISMATCH",
            )

    def test_frozen_automatic_fast_paths_reject_dirty_business_tree(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            branch_ref = "feature/d-frozen-dirty"
            git_command(repository, "switch", "-c", branch_ref)
            confirmed = _confirm_existing_branch(
                repository,
                "d-frozen-dirty",
                "t-frozen-dirty",
                branch_ref,
            )
            active = _select(repository, confirmed)
            self.assertEqual(active["status"], "ACTIVE")
            (repository / "unfinished.txt").write_text(
                "unfinished receiver output\n",
                encoding="utf-8",
            )

            with self.assertRaises(GatedLoopError) as rejected:
                _select(repository, confirmed)
            self.assertEqual(
                rejected.exception.code,
                "SCHEDULER_WORKSPACE_TURN_DIRTY",
            )

    def test_terminal_automatic_run_never_requests_dispatch(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            confirmed = call_tool(
                "prepare_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-terminal-dispatch",
                        "t-terminal-dispatch",
                    )
                },
                root=str(workspace),
                workspace_root=str(workspace),
                trusted_host_adapter="codex",
            )
            SchedulerRepository(str(workspace)).freeze(
                confirmed["rootId"],
                expected_delivery_revision=confirmed["deliveryRevision"],
                expected_hierarchy_fingerprint=confirmed[
                    "hierarchyFingerprint"
                ],
                authorized_project_ids=[],
                confirmed_by="human",
            )
            cancelled = call_tool(
                "cancel_graph_run",
                {
                    "root_id": confirmed["rootId"],
                    "cancelled_by": "human",
                    "reason": "Exercise terminal idempotency.",
                },
                root=str(workspace),
                workspace_root=str(workspace),
                trusted_host_adapter="codex",
            )
            self.assertEqual(cancelled["status"], "CANCELLED")

            result = _select(workspace, confirmed)
            self.assertEqual(result["status"], "CANCELLED")
            self.assertFalse(result["automaticDispatchRequested"])
            self.assertNotEqual(
                result["nextAction"],
                "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH",
            )

    def test_legacy_double_active_returns_waiting_for_non_owner(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            first_hierarchy = isolated_task_hierarchy(
                "d-legacy-active-first",
                "t-legacy-active-first",
            )
            second_hierarchy = isolated_task_hierarchy(
                "d-legacy-active-second",
                "t-legacy-active-second",
            )
            first = call_tool(
                "prepare_hierarchy",
                {"hierarchy": first_hierarchy},
                root=str(workspace),
                workspace_root=str(workspace),
            )
            second = call_tool(
                "prepare_hierarchy",
                {"hierarchy": second_hierarchy},
                root=str(workspace),
                workspace_root=str(workspace),
            )
            scheduler = SchedulerRepository(str(workspace))
            scheduler.freeze(
                first["rootId"],
                expected_delivery_revision=first["deliveryRevision"],
                expected_hierarchy_fingerprint=first[
                    "hierarchyFingerprint"
                ],
                authorized_project_ids=[],
                confirmed_by="human",
            )
            call_tool(
                "cancel_graph_run",
                {
                    "root_id": first["rootId"],
                    "cancelled_by": "human",
                    "reason": "Create a released legacy fixture.",
                },
                root=str(workspace),
                workspace_root=str(workspace),
            )
            scheduler.release_serial_workspace_turn(
                first["rootId"],
                evidence={"reason": "legacy-fixture"},
            )
            scheduler.freeze(
                second["rootId"],
                expected_delivery_revision=second["deliveryRevision"],
                expected_hierarchy_fingerprint=second[
                    "hierarchyFingerprint"
                ],
                authorized_project_ids=[],
                confirmed_by="human",
            )
            with scheduler.transaction() as connection:
                first_run = connection.execute(
                    "SELECT run_id FROM runs WHERE root_id = ?",
                    (first["rootId"],),
                ).fetchone()
                connection.execute(
                    "DELETE FROM graph_events WHERE run_id = ? "
                    "AND event_type = 'WORKSPACE_TURN_RELEASED'",
                    (first_run["run_id"],),
                )
                connection.execute(
                    "UPDATE runs SET status = 'ACTIVE' WHERE root_id = ?",
                    (first["rootId"],),
                )

            result = call_tool(
                "select_execution_mode",
                {
                    "root_id": second["rootId"],
                    "selection": "AUTOMATIC",
                    "expected_hierarchy_fingerprint": second[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": second[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=str(workspace),
                workspace_root=str(workspace),
            )

            self.assertEqual(result["status"], "QUEUED")
            self.assertEqual(result["deliveryQueue"]["state"], "QUEUED")
            self.assertFalse(result["automaticDispatchRequested"])
            self.assertEqual(
                result["workspaceTurn"]["ownerRootId"],
                first["rootId"],
            )

    def test_terminal_automatic_owner_releases_before_manual_switch(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            first_branch = "feature/d-auto-before-manual"
            manual_branch = "feature/d-manual-successor"
            git_command(repository, "switch", "-c", first_branch)
            first = _confirm_existing_branch(
                repository,
                "d-auto-before-manual",
                "t-auto-before-manual",
                first_branch,
            )
            first_active = _select(repository, first)
            self.assertEqual(first_active["status"], "ACTIVE")
            manual = _confirm_new_branch(
                repository,
                "d-manual-successor",
                "t-manual-successor",
                manual_branch,
            )
            handoff = _select_manual(repository, manual)
            self.assertEqual(handoff["status"], "QUEUED")
            self.assertEqual(handoff["deliveryStatus"], "HANDOFF_READY")
            self.assertEqual(
                handoff["deliveryQueue"]["continuation"]["tool"],
                "start_manual_handoff",
            )

            active_wait = _start_manual(repository, handoff)
            self.assertEqual(
                active_wait["status"],
                "QUEUED",
            )
            self.assertEqual(
                active_wait["manualStartState"],
                "WAITING_FOR_WORKSPACE_TURN",
            )
            self.assertIn("deliveryQueue", active_wait)
            manual_overview = (
                repository
                / ".layered-delivery"
                / "d-manual-successor"
                / "overview.md"
            ).read_text(encoding="utf-8")
            self.assertIn(
                "排队中（等待工作区串行调度）",
                manual_overview,
            )
            self.assertEqual(
                active_wait["workspaceTurn"]["ownerRootId"],
                first["rootId"],
            )

            implementation = repository / "automatic-result.txt"
            implementation.write_text("committed\n", encoding="utf-8")
            git_command(repository, "add", "automatic-result.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Complete automatic predecessor",
            )
            call_tool(
                "cancel_graph_run",
                {
                    "root_id": first["rootId"],
                    "cancelled_by": "human",
                    "reason": "Hand the workspace to the manual Delivery.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            branch_setup = _start_manual(repository, handoff)
            self.assertEqual(
                branch_setup["workspacePreparation"]["state"],
                "CURRENT_WORKSPACE_PREPARATION_REQUIRED",
            )
            self.assertEqual(
                branch_setup["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_START_MANUAL_HANDOFF",
            )
            scheduler = SchedulerRepository(str(repository))
            self.assertIsNotNone(
                scheduler.workspace_turn_release(first["rootId"])
            )

            git_command(
                repository,
                "switch",
                "-c",
                manual_branch,
                "main",
            )
            started = _start_manual(repository, handoff)

            self.assertEqual(started["status"], "ACTIVE")
            self.assertEqual(started["executionMode"], "manual")
            self.assertTrue(started["graphRunCreated"])


if __name__ == "__main__":
    unittest.main()
