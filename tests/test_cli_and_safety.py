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
        self.assertIn("record-interaction", help_text)
        self.assertIn("interaction-log", help_text)
        self.assertIn("--development-mode active|manual", help_text)
        self.assertNotIn("select-development-mode", help_text)
        self.assertIn("retry-item --item <id> --expected-baseline <sha256>", help_text)
        self.assertNotIn("retry-item --item <id> --expected-baseline <sha256> --confirmed", help_text)
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

    def test_freeze_requires_mode_in_the_same_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stderr = io.StringIO()
            code = run_cli(
                [
                    "freeze-hierarchy",
                    "--item", "t-example",
                    "--expected-hierarchy", "0" * 64,
                    "--confirmed",
                    "--json",
                ],
                cwd=temporary,
                stderr=stderr,
            )
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "OPTION_REQUIRED")

    def test_interaction_can_be_recorded_and_listed_from_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepare_stdout = io.StringIO()
            run_cli(
                ["prepare-hierarchy", "--definition", "-", "--host-runtime", "codex", "--json"],
                cwd=temporary,
                stdin=io.StringIO(json.dumps(task_hierarchy())),
                stdout=prepare_stdout,
            )
            item_id = json.loads(prepare_stdout.getvalue())["result"]["rootId"]
            interaction = {
                "schemaVersion": 3,
                "sessionId": "cli-session",
                "actor": "AGENT",
                "eventType": "AGENT_UPDATE",
                "summary": "已完成 SQLite 状态检查。",
                "operationId": None,
                "hostRuntime": "codex",
            }
            stdout = io.StringIO()
            self.assertEqual(
                run_cli(
                    ["record-interaction", "--item", item_id, "--interaction", "-", "--json"],
                    cwd=temporary,
                    stdin=io.StringIO(json.dumps(interaction)),
                    stdout=stdout,
                ),
                0,
            )
            self.assertEqual(json.loads(stdout.getvalue())["result"]["eventType"], "AGENT_UPDATE")
            stdout = io.StringIO()
            self.assertEqual(
                run_cli(["interaction-log", "--item", item_id, "--json"], cwd=temporary, stdout=stdout),
                0,
            )
            self.assertEqual(json.loads(stdout.getvalue())["result"][-1]["summary"], "已完成 SQLite 状态检查。")

    def test_removed_mode_selection_command_is_rejected(self) -> None:
        stderr = io.StringIO()
        code = run_cli(["select-development-mode", "--json"], stderr=stderr)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "UNKNOWN_COMMAND")

    @unittest.skipUnless(os.name == "nt", "Windows drive semantics")
    def test_cross_volume_input_is_rejected(self) -> None:
        with self.assertRaises(GatedLoopError) as raised:
            safe_path("G:\\workspace", "C:\\Temp\\definition.json")
        self.assertEqual(raised.exception.code, "PATH_CROSS_VOLUME")


if __name__ == "__main__":
    unittest.main()
