from __future__ import annotations

from .workspace_execution_strategy_support import (
    Path,
    SchedulerRepository,
    TemporaryDirectory,
    _confirm_existing_branch,
    _confirm_new_branch,
    _repository,
    _select,
    call_tool,
    git_command,
)


class WorkspaceExecutionStrategyTestsPart3:
    def test_manual_handoff_uses_the_same_queue_and_workspace_preparation(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            first_branch = "feature/d-manual-queue-owner"
            second_branch = "feature/d-manual-queue-waiter"
            git_command(repository, "switch", "-c", first_branch)
            first = _confirm_existing_branch(
                repository,
                "d-manual-queue-owner",
                "t-manual-queue-owner",
                first_branch,
            )
            second = _confirm_new_branch(
                repository,
                "d-manual-queue-waiter",
                "t-manual-queue-waiter",
                second_branch,
            )
            self.assertEqual(_select(repository, first)["status"], "ACTIVE")
            handoff = call_tool(
                "select_execution_mode",
                {
                    "root_id": second["rootId"],
                    "selection": "MANUAL",
                    "expected_hierarchy_fingerprint": second[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": second[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(handoff["status"], "QUEUED")
            self.assertEqual(
                handoff["deliveryQueue"]["continuation"]["tool"],
                "start_manual_handoff",
            )
            self.assertEqual(
                SchedulerRepository(str(repository)).execution_selection(
                    second["rootId"]
                )["selection"],
                "MANUAL",
            )

            implementation = repository / "manual-queue-owner.txt"
            implementation.write_text(
                "completed owner work\n",
                encoding="utf-8",
            )
            git_command(repository, "add", implementation.name)
            git_command(
                repository,
                "commit",
                "-m",
                "Complete manual queue owner fixture",
            )
            cancelled = call_tool(
                "cancel_graph_run",
                {
                    "root_id": first["rootId"],
                    "cancelled_by": "human",
                    "reason": "Release the shared queue owner.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(cancelled["status"], "CANCELLED")
            preparation = call_tool(
                "start_manual_handoff",
                {
                    "root_id": second["rootId"],
                    "expected_hierarchy_fingerprint": handoff[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": handoff[
                        "graphFingerprint"
                    ],
                    "started_by": "manual-receiver",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(
                preparation["workspacePreparation"]["state"],
                "CURRENT_WORKSPACE_PREPARATION_REQUIRED",
            )
            self.assertEqual(
                preparation["workspacePreparation"][
                    "manualHostPreparation"
                ]["actions"][-1],
                {
                    "action": "START_MANUAL_HANDOFF",
                    "tool": "start_manual_handoff",
                },
            )
            self.assertEqual(
                preparation["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_START_MANUAL_HANDOFF",
            )

            git_command(
                repository,
                "switch",
                "-c",
                second_branch,
                "main",
            )
            started = call_tool(
                "start_manual_handoff",
                {
                    "root_id": second["rootId"],
                    "expected_hierarchy_fingerprint": handoff[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": handoff[
                        "graphFingerprint"
                    ],
                    "started_by": "manual-receiver",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(started["status"], "ACTIVE")
            self.assertEqual(started["executionMode"], "manual")
