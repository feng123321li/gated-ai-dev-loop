from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hdg.errors import GatedLoopError
from hdg.mcp_tools import call_tool
from hdg.repository import SchedulerRepository

from .test_scheduler_contracts import git_command, isolated_task_hierarchy


def _repository(root: Path) -> tuple[Path, str]:
    repository = root / "repository"
    repository.mkdir()
    git_command(repository, "init", "--initial-branch=main")
    git_command(repository, "config", "user.name", "Scheduler Tests")
    git_command(
        repository,
        "config",
        "user.email",
        "scheduler-tests@example.invalid",
    )
    (repository / "README.md").write_text(
        "# workspace strategy fixture\n",
        encoding="utf-8",
    )
    git_command(repository, "add", "README.md")
    git_command(repository, "commit", "-m", "Initial main baseline")
    return repository, git_command(repository, "rev-parse", "HEAD")


def _preview(
    repository: Path,
    delivery_id: str,
    task_id: str,
) -> dict:
    return call_tool(
        "preview_hierarchy",
        {
            "hierarchy": isolated_task_hierarchy(
                delivery_id,
                task_id,
            )
        },
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )


def _confirm_existing_branch(
    repository: Path,
    delivery_id: str,
    task_id: str,
    branch_ref: str,
) -> dict:
    preview = _preview(repository, delivery_id, task_id)
    return call_tool(
        "confirm_development_baseline",
        {
            "root_id": delivery_id,
            "selection": branch_ref,
            "expected_hierarchy_fingerprint": preview[
                "hierarchyFingerprint"
            ],
            "confirmed_by": "human",
        },
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )


def _confirm_new_branch(
    repository: Path,
    delivery_id: str,
    task_id: str,
    branch_ref: str,
) -> dict:
    preview = _preview(repository, delivery_id, task_id)
    return call_tool(
        "confirm_development_baseline",
        {
            "root_id": delivery_id,
            "selection": "NEW_FROM_MAINLINE",
            "branch_name": branch_ref,
            "expected_hierarchy_fingerprint": preview[
                "hierarchyFingerprint"
            ],
            "confirmed_by": "human",
        },
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )


def _select(
    repository: Path,
    confirmed: dict,
) -> dict:
    arguments = {
        "root_id": confirmed["rootId"],
        "selection": "AUTOMATIC",
        "expected_hierarchy_fingerprint": confirmed[
            "hierarchyFingerprint"
        ],
        "expected_graph_fingerprint": confirmed["graphFingerprint"],
        "authorized_project_ids": [],
        "confirmed_by": "human",
    }
    return call_tool(
        "select_execution_mode",
        arguments,
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )


def _is_waiting_for_workspace_turn(result: dict) -> bool:
    preparation = result.get("workspacePreparation")
    turn = result.get("workspaceTurn")
    values = {
        result.get("status"),
        result.get("nextAction"),
        preparation.get("state") if isinstance(preparation, dict) else None,
        preparation.get("nextAction")
        if isinstance(preparation, dict)
        else None,
        turn.get("state") if isinstance(turn, dict) else None,
        turn.get("nextAction") if isinstance(turn, dict) else None,
    }
    return bool(
        values
        & {
            "WAITING_FOR_WORKSPACE_TURN",
            "WAIT_FOR_WORKSPACE_TURN",
            "WAIT_FOR_CURRENT_WORKSPACE_TURN",
        }
    )


def _is_waiting_for_workspace_commit(result: dict) -> bool:
    preparation = result.get("workspacePreparation")
    turn = result.get("workspaceTurn")
    commit_gate = result.get("workspaceCommitGate")
    values = {
        result.get("status"),
        result.get("nextAction"),
        preparation.get("state") if isinstance(preparation, dict) else None,
        preparation.get("nextAction")
        if isinstance(preparation, dict)
        else None,
        turn.get("state") if isinstance(turn, dict) else None,
        turn.get("nextAction") if isinstance(turn, dict) else None,
        (
            commit_gate.get("state")
            if isinstance(commit_gate, dict)
            else None
        ),
        (
            commit_gate.get("nextAction")
            if isinstance(commit_gate, dict)
            else None
        ),
    }
    return bool(
        values
        & {
            "WAITING_FOR_WORKSPACE_COMMIT",
            "WAIT_FOR_WORKSPACE_COMMIT",
        }
    )


class WorkspaceExecutionStrategyTests(unittest.TestCase):
    def test_automatic_defaults_to_current_workspace_serial(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            branch_ref = "feature/d-serial-ready"
            git_command(repository, "switch", "-c", branch_ref)
            confirmed = _confirm_existing_branch(
                repository,
                "d-serial-ready",
                "t-serial-ready",
                branch_ref,
            )

            selected = _select(repository, confirmed)

            self.assertEqual(selected["status"], "ACTIVE")
            self.assertEqual(
                selected["workspaceStrategy"],
                "CURRENT_WORKSPACE_SERIAL",
            )

    def test_serial_choice_waits_for_current_delivery_branch(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            confirmed = _confirm_new_branch(
                repository,
                "d-serial-waiting",
                "t-serial-waiting",
                "feature/d-serial-waiting",
            )

            selected = _select(repository, confirmed)

            self.assertEqual(selected["status"], "CHOICE_READY")
            self.assertTrue(selected["selectionRecorded"])
            self.assertEqual(
                selected["workspacePreparation"]["strategy"],
                "CURRENT_WORKSPACE_SERIAL",
            )
            self.assertNotIn("worktreeSetup", selected)
            self.assertNotIn("projectWorktreeSetup", selected)
            self.assertNotIn(
                "controllerCreatesWorktree",
                selected["workspacePreparation"],
            )
            self.assertEqual(
                selected["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            )
            scheduler = SchedulerRepository(str(repository))
            self.assertEqual(
                scheduler.execution_selection("d-serial-waiting")[
                    "selection"
                ],
                "AUTOMATIC",
            )

    def test_parallel_and_linked_worktree_inputs_are_removed(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            confirmed = _confirm_new_branch(
                repository,
                "d-no-parallel",
                "t-no-parallel",
                "feature/d-no-parallel",
            )
            common = {
                "root_id": confirmed["rootId"],
                "expected_hierarchy_fingerprint": confirmed[
                    "hierarchyFingerprint"
                ],
                "expected_graph_fingerprint": confirmed[
                    "graphFingerprint"
                ],
                "authorized_project_ids": [],
                "confirmed_by": "human",
            }
            rejected_arguments = (
                {**common, "selection": "AUTOMATIC_PARALLEL"},
                {
                    **common,
                    "selection": "AUTOMATIC",
                    "workspace_strategy": "LINKED_WORKTREE_PARALLEL",
                },
            )

            for arguments in rejected_arguments:
                with self.subTest(arguments=arguments):
                    with self.assertRaises(GatedLoopError) as rejected:
                        call_tool(
                            "select_execution_mode",
                            arguments,
                            root=str(repository),
                            workspace_root=str(repository),
                            trusted_host_adapter="codex",
                        )
                    self.assertEqual(
                        rejected.exception.code,
                        "MCP_TOOL_ARGUMENT_INVALID",
                    )
            scheduler = SchedulerRepository(str(repository))
            self.assertIsNone(
                scheduler.execution_selection("d-no-parallel")
            )
            with self.assertRaises(GatedLoopError) as missing:
                scheduler.run("d-no-parallel")
            self.assertEqual(
                missing.exception.code,
                "SCHEDULER_RUN_MISSING",
            )

    def test_serial_delivery_waits_for_turn_before_switch_and_activation(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, base_commit = _repository(Path(temporary))
            first_branch = "feature/d-serial-first"
            second_branch = "feature/d-serial-second"
            git_command(repository, "switch", "-c", first_branch)
            first = _confirm_existing_branch(
                repository,
                "d-serial-first",
                "t-serial-first",
                first_branch,
            )
            second = _confirm_new_branch(
                repository,
                "d-serial-second",
                "t-serial-second",
                second_branch,
            )
            first_active = _select(repository, first)
            self.assertEqual(first_active["status"], "ACTIVE")

            second_waiting = _select(repository, second)

            scheduler = SchedulerRepository(str(repository))

            self.assertEqual(
                second_waiting["workspaceStrategy"],
                "CURRENT_WORKSPACE_SERIAL",
            )
            self.assertFalse(
                second_waiting["automaticDispatchRequested"]
            )
            self.assertTrue(second_waiting["selectionRecorded"])
            self.assertTrue(
                _is_waiting_for_workspace_turn(second_waiting),
                second_waiting,
            )
            self.assertEqual(
                SchedulerRepository(
                    str(repository)
                ).execution_selection("d-serial-second")[
                    "selection"
                ],
                "AUTOMATIC",
            )
            rootless = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(
                rootless["status"],
                "DELIVERY_SELECTION_REQUIRED",
            )
            self.assertEqual(
                {
                    item["rootId"]
                    for item in rootless["candidateDeliveries"]
                },
                {"d-serial-first", "d-serial-second"},
            )
            with self.assertRaises(GatedLoopError) as missing:
                SchedulerRepository(str(repository)).run(
                    "d-serial-second"
                )
            self.assertEqual(
                missing.exception.code,
                "SCHEDULER_RUN_MISSING",
            )

            implementation = repository / "serial-first.txt"
            implementation.write_text(
                "uncommitted implementation\n",
                encoding="utf-8",
            )
            dirty_waiting = _select(repository, second)

            self.assertTrue(
                _is_waiting_for_workspace_turn(dirty_waiting),
                dirty_waiting,
            )
            self.assertFalse(
                dirty_waiting["automaticDispatchRequested"]
            )
            self.assertTrue(
                git_command(
                    repository,
                    "status",
                    "--porcelain",
                    "--",
                    ".",
                    ":(exclude).layered-delivery",
                    ":(exclude).layered-delivery/**",
                )
            )
            with self.assertRaises(GatedLoopError) as dirty_missing:
                scheduler.run("d-serial-second")
            self.assertEqual(
                dirty_missing.exception.code,
                "SCHEDULER_RUN_MISSING",
            )

            git_command(repository, "add", "serial-first.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Complete first serial workspace turn",
            )
            first_commit = git_command(repository, "rev-parse", "HEAD")
            self.assertNotEqual(first_commit, base_commit)
            self.assertEqual(
                git_command(
                    repository,
                    "status",
                    "--porcelain",
                    "--",
                    ".",
                    ":(exclude).layered-delivery",
                    ":(exclude).layered-delivery/**",
                ),
                "",
            )
            self.assertEqual(
                git_command(
                    repository,
                    "show",
                    "HEAD:serial-first.txt",
                ),
                "uncommitted implementation",
            )
            committed_but_active = _select(repository, second)

            self.assertTrue(
                _is_waiting_for_workspace_turn(committed_but_active),
                committed_but_active,
            )
            self.assertFalse(
                committed_but_active["automaticDispatchRequested"]
            )
            with self.assertRaises(GatedLoopError) as active_missing:
                scheduler.run("d-serial-second")
            self.assertEqual(
                active_missing.exception.code,
                "SCHEDULER_RUN_MISSING",
            )

            cancelled = call_tool(
                "cancel_graph_run",
                {
                    "root_id": "d-serial-first",
                    "cancelled_by": "human",
                    "reason": "Release the serial workspace turn.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(cancelled["status"], "CANCELLED")

            branch_preparation = _select(repository, second)

            self.assertFalse(
                _is_waiting_for_workspace_turn(branch_preparation),
                branch_preparation,
            )
            self.assertFalse(
                branch_preparation["automaticDispatchRequested"]
            )
            self.assertEqual(
                branch_preparation["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            )
            git_command(
                repository,
                "switch",
                "-c",
                second_branch,
                "main",
            )
            second_active = _select(repository, second)

            self.assertEqual(second_active["status"], "ACTIVE")
            self.assertEqual(
                second_active["workspaceStrategy"],
                "CURRENT_WORKSPACE_SERIAL",
            )
            self.assertEqual(
                scheduler.run("d-serial-first")["status"],
                "CANCELLED",
            )
            self.assertEqual(
                scheduler.run("d-serial-second")["status"],
                "ACTIVE",
            )
    def test_terminal_turn_without_business_commit_keeps_successor_waiting(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, base_commit = _repository(Path(temporary))
            first_branch = "feature/d-commit-gate-first"
            second_branch = "feature/d-commit-gate-second"
            git_command(repository, "switch", "-c", first_branch)
            first = _confirm_existing_branch(
                repository,
                "d-commit-gate-first",
                "t-commit-gate-first",
                first_branch,
            )
            second = _confirm_new_branch(
                repository,
                "d-commit-gate-second",
                "t-commit-gate-second",
                second_branch,
            )
            first_active = _select(repository, first)
            self.assertEqual(first_active["status"], "ACTIVE")
            self.assertEqual(
                git_command(repository, "rev-parse", "HEAD"),
                base_commit,
            )
            self.assertEqual(
                git_command(
                    repository,
                    "diff",
                    "--name-only",
                    base_commit,
                    "HEAD",
                    "--",
                    ".",
                    ":(exclude).layered-delivery",
                    ":(exclude).layered-delivery/**",
                ),
                "",
            )
            cancelled = call_tool(
                "cancel_graph_run",
                {
                    "root_id": "d-commit-gate-first",
                    "cancelled_by": "human",
                    "reason": "Release a turn with no business commit.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(cancelled["status"], "CANCELLED")

            waiting_for_commit = _select(repository, second)

            self.assertTrue(
                _is_waiting_for_workspace_commit(waiting_for_commit),
                waiting_for_commit,
            )
            self.assertFalse(
                waiting_for_commit["automaticDispatchRequested"]
            )
            scheduler = SchedulerRepository(str(repository))
            with self.assertRaises(GatedLoopError) as missing:
                scheduler.run("d-commit-gate-second")
            self.assertEqual(
                missing.exception.code,
                "SCHEDULER_RUN_MISSING",
            )

            implementation = repository / "commit-gate-first.txt"
            implementation.write_text(
                "verified committed result\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "commit-gate-first.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Create verifiable serial turn result",
            )
            first_commit = git_command(repository, "rev-parse", "HEAD")
            self.assertNotEqual(first_commit, base_commit)
            self.assertEqual(
                git_command(
                    repository,
                    "status",
                    "--porcelain",
                    "--",
                    ".",
                    ":(exclude).layered-delivery",
                    ":(exclude).layered-delivery/**",
                ),
                "",
            )
            self.assertEqual(
                git_command(
                    repository,
                    "show",
                    "HEAD:commit-gate-first.txt",
                ),
                "verified committed result",
            )

            branch_preparation = _select(repository, second)

            self.assertFalse(
                _is_waiting_for_workspace_commit(branch_preparation),
                branch_preparation,
            )
            self.assertEqual(
                branch_preparation["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            )
            self.assertFalse(
                branch_preparation["automaticDispatchRequested"]
            )
            with self.assertRaises(GatedLoopError) as still_missing:
                scheduler.run("d-commit-gate-second")
            self.assertEqual(
                still_missing.exception.code,
                "SCHEDULER_RUN_MISSING",
            )

    def test_deliveries_cannot_share_branch(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            branch_ref = "feature/d-shared"
            _confirm_new_branch(
                repository,
                "d-shared-first",
                "t-shared-first",
                branch_ref,
            )

            with self.assertRaises(GatedLoopError) as conflicting:
                _confirm_new_branch(
                    repository,
                    "d-shared-second",
                    "t-shared-second",
                    branch_ref,
                )

            self.assertEqual(
                conflicting.exception.code,
                "SCHEDULER_BASELINE_BRANCH_IN_USE",
            )
            self.assertEqual(
                conflicting.exception.details[
                    "conflictingDeliveries"
                ][0]["rootId"],
                "d-shared-first",
            )
            scheduler = SchedulerRepository(str(repository))
            for root_id in ("d-shared-first", "d-shared-second"):
                with self.assertRaises(GatedLoopError) as missing:
                    scheduler.run(root_id)
                self.assertEqual(
                    missing.exception.code,
                    "SCHEDULER_RUN_MISSING",
                )


if __name__ == "__main__":
    unittest.main()
