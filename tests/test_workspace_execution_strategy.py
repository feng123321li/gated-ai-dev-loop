from __future__ import annotations

from pathlib import Path
import subprocess
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


def _resume(
    repository: Path,
    confirmed: dict,
) -> dict:
    return call_tool(
        "resume_execution_mode",
        {
            "root_id": confirmed["rootId"],
            "expected_hierarchy_fingerprint": confirmed[
                "hierarchyFingerprint"
            ],
            "expected_graph_fingerprint": confirmed[
                "graphFingerprint"
            ],
        },
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
            host_preparation = selected["workspacePreparation"][
                "automaticHostPreparation"
            ]
            self.assertEqual(host_preparation["state"], "READY")
            self.assertFalse(host_preparation["confirmationRequired"])
            self.assertEqual(
                [item["action"] for item in host_preparation["actions"]],
                [
                    "CREATE_OR_SWITCH_DELIVERY_BRANCH",
                    "RESUME_EXECUTION_MODE",
                ],
            )
            scheduler = SchedulerRepository(str(repository))
            self.assertEqual(
                scheduler.execution_selection("d-serial-waiting")[
                    "selection"
                ],
                "AUTOMATIC",
            )

    def test_dirty_unrelated_changes_offer_stash_or_wait_before_branch_switch(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            (repository / "README.md").write_text(
                "# staged unrelated change\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "README.md")
            (repository / "untracked-note.txt").write_text(
                "untracked unrelated change\n",
                encoding="utf-8",
            )
            branch_ref = "feature/d-stash-before-run"

            confirmed = _confirm_new_branch(
                repository,
                "d-stash-before-run",
                "t-stash-before-run",
                branch_ref,
            )
            selected = _select(repository, confirmed)

            self.assertEqual(selected["status"], "CHOICE_READY")
            self.assertTrue(selected["selectionRecorded"])
            self.assertFalse(selected["automaticDispatchRequested"])
            self.assertEqual(
                selected["nextAction"],
                "HOST_STASH_PREPARE_BRANCH_THEN_RESUME_EXECUTION",
            )
            self.assertEqual(
                git_command(repository, "branch", "--show-current"),
                "main",
            )
            self.assertEqual(
                len(
                    git_command(
                        repository,
                        "status",
                        "--porcelain",
                        "--",
                        ".",
                        ":(exclude).layered-delivery",
                        ":(exclude).layered-delivery/**",
                    ).splitlines()
                ),
                2,
            )

            preparation = selected["workspacePreparation"]
            project = preparation["projectPreparations"][0]
            working_tree = project["workingTree"]
            self.assertTrue(working_tree["hasStagedChanges"])
            self.assertTrue(working_tree["hasUntrackedChanges"])
            self.assertFalse(working_tree["hasUnmergedChanges"])
            handling = preparation["workspaceChangeHandling"]
            self.assertEqual(
                handling["kind"],
                "AUTOMATIC_DIRTY_WORKSPACE_PREPARATION",
            )
            self.assertEqual(handling["action"], "STASH_AND_RUN")
            self.assertFalse(handling["confirmationRequired"])
            self.assertEqual(
                handling["authorizationSource"],
                "AUTOMATIC_EXECUTION_SELECTION",
            )
            self.assertEqual(
                handling["fallbackAction"],
                "KEEP_CHANGES_AND_WAIT",
            )
            stash = handling["hostAction"]
            self.assertEqual(stash["owner"], "HOST")
            self.assertFalse(stash["controllerExecutesGit"])
            self.assertEqual(
                stash["expectedProjects"],
                [
                    {
                        "projectId": "d-stash-before-run",
                        "workspaceRoot": str(repository.resolve()),
                        "workingTreeStateFingerprint": working_tree[
                            "stateFingerprint"
                        ],
                    }
                ],
            )
            self.assertTrue(stash["stashPolicy"]["includeUntracked"])
            self.assertEqual(
                stash["stashPolicy"]["pathspec"],
                [
                    ".",
                    ":(exclude).layered-delivery",
                    ":(exclude).layered-delivery/**",
                ],
            )
            self.assertTrue(stash["restorePolicy"]["restoreIndex"])
            self.assertFalse(
                handling["preservedUnrelatedChanges"]["supported"]
            )
            self.assertEqual(
                handling["preservedUnrelatedChanges"]["reason"],
                "DELIVERY_TURN_MUST_START_CLEAN",
            )
            self.assertEqual(
                [
                    item["action"]
                    for item in preparation["automaticHostPreparation"][
                        "actions"
                    ]
                ],
                [
                    "STASH_BUSINESS_CHANGES",
                    "CREATE_OR_SWITCH_DELIVERY_BRANCH",
                    "RESUME_EXECUTION_MODE",
                ],
            )

            git_command(
                repository,
                "stash",
                "push",
                "--include-untracked",
                "--message",
                "delivery-graph:d-stash-before-run",
                "--",
                ".",
                ":(exclude).layered-delivery",
                ":(exclude).layered-delivery/**",
            )
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
            git_command(repository, "switch", "-c", branch_ref, "main")

            resumed = _resume(repository, confirmed)

            self.assertEqual(resumed["status"], "ACTIVE")
            self.assertTrue(resumed["automaticDispatchRequested"])
            self.assertIn(
                "delivery-graph:d-stash-before-run",
                git_command(repository, "stash", "list"),
            )

    def test_dirty_current_branch_adoption_still_requires_attribution(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            branch_ref = "feature/d-owned-dirty"
            git_command(repository, "switch", "-c", branch_ref)
            (repository / "owned.txt").write_text(
                "delivery-owned change\n",
                encoding="utf-8",
            )
            preview = _preview(
                repository,
                "d-owned-dirty",
                "t-owned-dirty",
            )

            with self.assertRaises(GatedLoopError) as missing:
                call_tool(
                    "confirm_development_baseline",
                    {
                        "root_id": "d-owned-dirty",
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
            self.assertEqual(
                missing.exception.code,
                "SCHEDULER_GIT_DIRTY_CONFIRMATION_REQUIRED",
            )

            confirmed = call_tool(
                "confirm_development_baseline",
                {
                    "root_id": "d-owned-dirty",
                    "selection": branch_ref,
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "confirmed_dirty_state_fingerprint": preview[
                        "developmentBaseline"
                    ][
                        "workingTree"
                    ]["stateFingerprint"],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(confirmed["rootId"], "d-owned-dirty")

    def test_unmerged_workspace_does_not_offer_executable_stash(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            git_command(repository, "switch", "-c", "conflict-side")
            (repository / "README.md").write_text(
                "side branch\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "README.md")
            git_command(repository, "commit", "-m", "Side change")
            git_command(repository, "switch", "main")
            (repository / "README.md").write_text(
                "main branch\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "README.md")
            git_command(repository, "commit", "-m", "Main change")
            with self.assertRaises(subprocess.CalledProcessError):
                git_command(repository, "merge", "conflict-side")

            confirmed = _confirm_new_branch(
                repository,
                "d-conflicted-before-run",
                "t-conflicted-before-run",
                "feature/d-conflicted-before-run",
            )
            selected = _select(repository, confirmed)

            preparation = selected["workspacePreparation"]
            self.assertEqual(
                selected["nextAction"],
                "RESOLVE_CONFLICTS_OR_KEEP_CHANGES_AND_WAIT",
            )
            self.assertTrue(
                preparation["projectPreparations"][0]["workingTree"][
                    "hasUnmergedChanges"
                ]
            )
            handling = preparation["workspaceChangeHandling"]
            self.assertEqual(handling["action"], "KEEP_CHANGES_AND_WAIT")
            self.assertEqual(
                handling["blockedAutomaticAction"],
                "STASH_AND_RUN",
            )
            self.assertEqual(
                handling["blockedReason"],
                "UNMERGED_CHANGES",
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
            self.assertEqual(second_waiting["status"], "QUEUED")
            self.assertEqual(
                second_waiting["deliveryQueue"],
                {
                    "state": "QUEUED",
                    "position": 2,
                    "queueLength": 2,
                    "ownerRootId": "d-serial-first",
                    "ownerStatus": "ACTIVE",
                    "continuation": {
                        "automatic": True,
                        "tool": "resume_execution_mode",
                        "rootId": "d-serial-second",
                        "confirmationRequired": False,
                        "trigger": (
                            "OWNER_TERMINAL_COMMIT_CLEAN_AND_RELEASED"
                        ),
                    },
                },
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
            queued_status = call_tool(
                "workspace_status",
                {"root_id": "d-serial-second"},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(queued_status["status"], "QUEUED")
            self.assertEqual(
                queued_status["deliveryQueue"]["position"],
                2,
            )
            self.assertIn(
                "排队中（等待自动调度）",
                (
                    repository
                    / ".layered-delivery"
                    / "d-serial-second"
                    / "overview.md"
                ).read_text(encoding="utf-8"),
            )
            self.assertIn(
                "排队中（等待自动调度）",
                (
                    repository / ".layered-delivery" / "overview.md"
                ).read_text(encoding="utf-8"),
            )

            implementation = repository / "serial-first.txt"
            implementation.write_text(
                "uncommitted implementation\n",
                encoding="utf-8",
            )
            dirty_waiting = _resume(repository, second)

            self.assertTrue(
                _is_waiting_for_workspace_turn(dirty_waiting),
                dirty_waiting,
            )
            self.assertFalse(
                dirty_waiting["automaticDispatchRequested"]
            )
            self.assertNotIn(
                "STASH_AND_RUN",
                str(dirty_waiting),
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
            committed_but_active = _resume(repository, second)

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

            branch_preparation = _resume(repository, second)

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
            second_active = _resume(repository, second)

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
