from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hdg.mcp_tools import call_tool
from hdg.repository import SchedulerRepository

from .test_scheduler_contracts import git_command
from .test_workspace_execution_strategy import (
    _confirm_existing_branch,
    _confirm_new_branch,
    _is_waiting_for_workspace_commit,
    _repository,
    _select,
)


def _terminal_first_turn(
    repository: Path,
    case: str,
    *,
    preexisting_commit: bool = False,
) -> tuple[dict, str, str]:
    first_id = f"d-{case}-first"
    second_id = f"d-{case}-second"
    first_branch = f"feature/{first_id}"
    second_branch = f"feature/{second_id}"
    git_command(repository, "switch", "-c", first_branch)
    if preexisting_commit:
        (repository / "before-turn.txt").write_text(
            "committed before the workspace turn\n",
            encoding="utf-8",
        )
        git_command(repository, "add", "before-turn.txt")
        git_command(repository, "commit", "-m", "Commit before turn start")
    first = _confirm_existing_branch(
        repository,
        first_id,
        f"t-{case}-first",
        first_branch,
    )
    second = _confirm_new_branch(
        repository,
        second_id,
        f"t-{case}-second",
        second_branch,
    )
    active = _select(repository, first)
    if active["status"] != "ACTIVE":
        raise AssertionError(active)
    turn_start = SchedulerRepository(
        str(repository)
    ).workspace_turn_start(first_id)
    start_commit = turn_start["projects"][0]["turnStartCommit"]
    cancelled = call_tool(
        "cancel_graph_run",
        {
            "root_id": first_id,
            "cancelled_by": "human",
            "reason": "Exercise the serial commit release gate.",
        },
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )
    if cancelled["status"] != "CANCELLED":
        raise AssertionError(cancelled)
    return second, first_id, start_commit


def _barrier_reason(result: dict) -> str:
    return result["workspaceTurn"]["projectBarriers"][0]["reason"]


class SerialWorkspaceCommitGateTests(unittest.TestCase):
    def test_allow_empty_commit_does_not_release_turn(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            second, first_id, _ = _terminal_first_turn(
                repository,
                "allow-empty",
            )
            git_command(
                repository,
                "commit",
                "--allow-empty",
                "-m",
                "Empty bookkeeping commit",
            )

            waiting = _select(repository, second)

            self.assertTrue(
                _is_waiting_for_workspace_commit(waiting),
                waiting,
            )
            self.assertEqual(
                _barrier_reason(waiting),
                "NO_BUSINESS_CHANGES_SINCE_TURN_START",
            )
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(first_id)
            )

    def test_control_directory_only_commit_does_not_release_turn(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            second, first_id, _ = _terminal_first_turn(
                repository,
                "control-only",
            )
            control_file = (
                repository / ".layered-delivery" / "control-only.txt"
            )
            control_file.parent.mkdir(exist_ok=True)
            control_file.write_text("control plane only\n", encoding="utf-8")
            git_command(
                repository,
                "add",
                "-f",
                ".layered-delivery/control-only.txt",
            )
            git_command(
                repository,
                "commit",
                "-m",
                "Commit only controller state",
            )

            waiting = _select(repository, second)

            self.assertTrue(
                _is_waiting_for_workspace_commit(waiting),
                waiting,
            )
            self.assertEqual(
                _barrier_reason(waiting),
                "NO_BUSINESS_CHANGES_SINCE_TURN_START",
            )
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(first_id)
            )

    def test_rewritten_history_does_not_release_turn(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, base_commit = _repository(Path(temporary))
            second, first_id, start_commit = _terminal_first_turn(
                repository,
                "rewritten-history",
                preexisting_commit=True,
            )
            self.assertNotEqual(start_commit, base_commit)
            git_command(repository, "reset", "--hard", base_commit)
            (repository / "after-rewrite.txt").write_text(
                "business change on rewritten history\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "after-rewrite.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Rewrite turn history with a business change",
            )

            waiting = _select(repository, second)

            self.assertTrue(
                _is_waiting_for_workspace_commit(waiting),
                waiting,
            )
            self.assertEqual(
                _barrier_reason(waiting),
                "TURN_START_COMMIT_NOT_ANCESTOR_OF_HEAD",
            )
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(first_id)
            )

    def test_business_commit_releases_with_changed_files_and_tree_evidence(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            second, first_id, start_commit = _terminal_first_turn(
                repository,
                "business-change",
            )
            (repository / "business-result.txt").write_text(
                "reviewable business result\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "business-result.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Commit business result",
            )
            head_commit = git_command(repository, "rev-parse", "HEAD")

            successor = _select(repository, second)

            self.assertFalse(
                _is_waiting_for_workspace_commit(successor),
                successor,
            )
            release = SchedulerRepository(
                str(repository)
            ).workspace_turn_release(first_id)
            self.assertIsNotNone(release)
            project = release["projects"][0]
            self.assertEqual(project["turnStartCommit"], start_commit)
            self.assertEqual(project["headCommit"], head_commit)
            self.assertEqual(
                project["businessChangedFiles"],
                [
                    {
                        "path": "business-result.txt",
                        "status": "ADDED",
                        "statusCode": "A",
                    }
                ],
            )
            self.assertRegex(
                project["businessTreeFingerprint"],
                r"^[0-9a-f]{64}$",
            )


if __name__ == "__main__":
    unittest.main()
