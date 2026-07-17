from __future__ import annotations

import io
import json
import os
import tempfile
import unittest

from hdg.cli import run_cli
from hdg.errors import GatedLoopError
from hdg.fs_safe import safe_path

from .fixtures import task_hierarchy


class CliAndSafetyTests(unittest.TestCase):
    def test_help_is_python_only_and_has_no_legacy_commands(self) -> None:
        output = io.StringIO()
        self.assertEqual(run_cli(["--help"], stdout=output), 0)
        help_text = output.getvalue()
        self.assertIn("python -X utf8 <skill-root>/scripts/hdg.py", help_text)
        self.assertIn("--json", help_text)
        self.assertIn("prepare-hierarchy", help_text)
        self.assertIn("freeze-hierarchy", help_text)
        self.assertNotIn("prepare-item", help_text)
        self.assertNotIn("freeze-item", help_text)
        self.assertNotIn("promote-item", help_text)
        self.assertNotIn("revise-item", help_text)
        self.assertNotIn("upgrade-registry", help_text)
        self.assertNotIn("delivery-item", help_text)
        self.assertNotIn("hdg.mjs", help_text)

    def test_definition_can_be_read_from_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stdout = io.StringIO()
            stderr = io.StringIO()
            code = run_cli(
                ["prepare-hierarchy", "--definition", "-", "--host-runtime", "codex", "--json"],
                cwd=temporary,
                stdin=io.StringIO(json.dumps(task_hierarchy())),
                stdout=stdout,
                stderr=stderr,
            )
            self.assertEqual(code, 0, stderr.getvalue())
            self.assertTrue(json.loads(stdout.getvalue())["ok"])

    @unittest.skipUnless(os.name == "nt", "Windows drive semantics")
    def test_cross_volume_input_is_rejected(self) -> None:
        with self.assertRaises(GatedLoopError) as raised:
            safe_path("G:\\workspace", "C:\\Temp\\definition.json")
        self.assertEqual(raised.exception.code, "PATH_CROSS_VOLUME")


if __name__ == "__main__":
    unittest.main()
