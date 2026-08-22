from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hdg.controller import ControllerContext, LayeredDeliveryController


class ControllerTimingTests(unittest.TestCase):
    @staticmethod
    def _context(root: str) -> ControllerContext:
        return ControllerContext(project_root=root, workspace_root=root)

    def test_timing_is_silent_by_default(self) -> None:
        controller = LayeredDeliveryController(
            {"probe": lambda **_: {"status": "OK"}}
        )

        with TemporaryDirectory() as root, redirect_stderr(io.StringIO()) as stderr:
            result = controller.execute("probe", {}, context=self._context(root))

        self.assertEqual(result, {"status": "OK"})
        self.assertEqual(stderr.getvalue(), "")

    def test_opt_in_timing_emits_success_without_changing_result(self) -> None:
        controller = LayeredDeliveryController(
            {"probe": lambda **_: {"status": "OK"}}
        )

        with (
            TemporaryDirectory() as root,
            patch.dict("os.environ", {"HDG_TIMING": "1"}),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            result = controller.execute("probe", {}, context=self._context(root))

        event = json.loads(stderr.getvalue())
        self.assertEqual(result, {"status": "OK"})
        self.assertEqual(event["event"], "controller.timing")
        self.assertEqual(event["command"], "probe")
        self.assertTrue(event["ok"])
        self.assertGreaterEqual(event["totalMs"], 0)
        self.assertEqual(event["stages"][0]["name"], "controller.execute")

    def test_opt_in_timing_emits_failure_and_preserves_exception(self) -> None:
        def fail_operation(**_: object) -> dict[str, object]:
            raise RuntimeError("expected failure")

        controller = LayeredDeliveryController({"probe": fail_operation})

        with (
            TemporaryDirectory() as root,
            patch.dict("os.environ", {"HDG_TIMING": "true"}),
            redirect_stderr(io.StringIO()) as stderr,
            self.assertRaisesRegex(RuntimeError, "expected failure"),
        ):
            controller.execute("probe", {}, context=self._context(root))

        event = json.loads(stderr.getvalue())
        self.assertFalse(event["ok"])
        self.assertEqual(event["command"], "probe")
        self.assertNotIn("expected failure", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
