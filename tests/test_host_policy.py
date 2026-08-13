from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hdg.errors import GatedLoopError
from hdg.host_policy import _resolve_project_root


TESTS_DIR = Path(__file__).resolve().parent


class ResolveProjectRootTests(unittest.TestCase):
    def test_unexpanded_placeholder_falls_back_to_cwd(self) -> None:
        # A host such as ZCode may surface HDG_PROJECT_ROOT with the manifest
        # placeholder (e.g. ${CLAUDE_PROJECT_DIR}) still unexpanded. Startup
        # must not fail on the literal path; it must fall back to the cwd.
        env = {"HDG_PROJECT_ROOT": "${CLAUDE_PROJECT_DIR}"}
        with patch.dict(os.environ, env, clear=False), patch(
            "hdg.host_policy.os.getcwd", return_value=str(TESTS_DIR)
        ):
            resolved = _resolve_project_root(None)
        self.assertEqual(Path(resolved), TESTS_DIR)

    def test_real_env_root_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"HDG_PROJECT_ROOT": tmp}, clear=False
        ):
            resolved = _resolve_project_root(None)
        self.assertEqual(Path(resolved), Path(tmp).resolve())

    def test_empty_env_falls_back_to_cwd(self) -> None:
        with patch.dict(
            os.environ, {"HDG_PROJECT_ROOT": ""}, clear=False
        ), patch("hdg.host_policy.os.getcwd", return_value=str(TESTS_DIR)):
            resolved = _resolve_project_root(None)
        self.assertEqual(Path(resolved), TESTS_DIR)

    def test_explicit_root_takes_precedence_over_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"HDG_PROJECT_ROOT": "${CLAUDE_PROJECT_DIR}"}, clear=False
        ):
            resolved = _resolve_project_root(tmp)
        self.assertEqual(Path(resolved), Path(tmp).resolve())

    def test_nonexistent_env_root_still_raises(self) -> None:
        missing = str(Path(tempfile.gettempdir()) / "hdg_missing_root_anchor")
        self.assertFalse(Path(missing).exists())
        with patch.dict(os.environ, {"HDG_PROJECT_ROOT": missing}, clear=False):
            with self.assertRaises(GatedLoopError) as ctx:
                _resolve_project_root(None)
        self.assertEqual(ctx.exception.code, "PROJECT_ROOT_INVALID")


if __name__ == "__main__":
    unittest.main()
