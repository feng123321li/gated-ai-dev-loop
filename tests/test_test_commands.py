from __future__ import annotations

import unittest

from hdg.test_commands import normalize_test_argv


class TestCommandSafetyTests(unittest.TestCase):
    def test_safe_direct_commands_are_accepted(self) -> None:
        for command in (
            ["python", "-m", "unittest", "discover"],
            ["mvn", "test"],
            ["node", "--test", "tests/example.test.mjs"],
        ):
            self.assertEqual(normalize_test_argv(command), command)

    def test_shells_and_interpreter_strings_are_rejected(self) -> None:
        for command in (
            ["cmd", "/c", "echo unsafe"],
            ["powershell", "-Command", "Write-Output unsafe"],
            ["python", "-c", "print('unsafe')"],
            ["node", "--eval", "console.log('unsafe')"],
            ["npx", "--yes", "bash", "-c", "echo unsafe"],
        ):
            self.assertIsNone(normalize_test_argv(command), command)


if __name__ == "__main__":
    unittest.main()
