from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hdg.repository import SchedulerRepository

from .test_scheduler_contracts import git_command
from .test_serial_workspace_commit_gate import _terminal_first_turn
from .test_workspace_execution_strategy import (
    _is_waiting_for_workspace_commit,
    _repository,
    _select,
)


class SerialWorkspaceCommitEvidenceTests(unittest.TestCase):
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
            git_command(repository, "commit", "-m", "Commit business result")
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
