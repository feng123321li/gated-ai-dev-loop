from __future__ import annotations

from .scheduler_contracts_support import (
    Path,
    TemporaryDirectory,
    inspect_frozen_git_workspace_provenance,
    subprocess,
    unittest,
)


class StaleBaseRebaseAdvisoryTests(unittest.TestCase):
    """workspace_status reports a workspace rebase advisory for a stale base."""

    @staticmethod
    def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, (args, result.stderr)
        return result

    def _repo(self) -> tuple[Path, str]:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name)
        self._git(repo, "-c", "init.defaultBranch=main", "init")
        self._git(repo, "config", "user.email", "t@t")
        self._git(repo, "config", "user.name", "t")
        (repo / "a").write_text("a", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-m", "c0")
        c0 = self._git(repo, "rev-parse", "main").stdout.strip()
        self._git(repo, "switch", "-c", "feature/x")
        (repo / "b").write_text("b", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-m", "c1")
        return repo, c0

    @staticmethod
    def _binding(c0: str) -> dict:
        return {
            "branchRef": "feature/x",
            "baseRef": "main",
            "baseCommit": c0,
            "integrationTarget": "main",
        }

    def test_no_advisory_when_main_unchanged(self) -> None:
        repo, c0 = self._repo()
        result = inspect_frozen_git_workspace_provenance(
            str(repo), self._binding(c0)
        )
        self.assertNotIn("workspaceRebase", result)

    def test_advisory_when_main_advanced(self) -> None:
        repo, c0 = self._repo()
        self._git(repo, "switch", "main")
        (repo / "c").write_text("c", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-m", "c2")
        c2 = self._git(repo, "rev-parse", "main").stdout.strip()
        self._git(repo, "switch", "feature/x")
        result = inspect_frozen_git_workspace_provenance(
            str(repo), self._binding(c0)
        )
        advisory = result["workspaceRebase"]
        self.assertTrue(advisory["required"])
        self.assertEqual(advisory["frozenBaseCommit"], c0)
        self.assertEqual(advisory["currentBaseCommit"], c2)
        self.assertEqual(advisory["integrationTarget"], "main")
