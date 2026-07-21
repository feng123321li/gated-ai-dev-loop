from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from hdg.acceptance import accept_work_item, record_acceptance
from hdg.cli import run_cli
from hdg.errors import GatedLoopError
from hdg.execution import dispatch_task, record_task_result
from hdg.graph_model import execution_node_id, gate_node_id
from hdg.graph_runtime import get_graph_frontier, get_graph_status, list_graph_events
from hdg.planning import freeze_hierarchy, prepare_hierarchy, retry_work_item

from .fixtures import task_hierarchy


class DeliveryGraphRuntimeTests(unittest.TestCase):
    @staticmethod
    def _prepare_and_freeze(root: str) -> dict:
        prepared = prepare_hierarchy(
            root=root,
            hierarchy=task_hierarchy(),
            host_runtime="codex",
        )
        frozen = freeze_hierarchy(
            root=root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
            development_mode="active",
            confirmed=True,
        )
        return {**prepared, "frozen": frozen}

    @staticmethod
    def _task_result(task_id: str, operation_id: str, status: str = "IMPLEMENTED") -> dict:
        return {
            "schemaVersion": 3,
            "kind": "TASK_RESULT",
            "taskId": task_id,
            "operationId": operation_id,
            "status": status,
            "summary": "Graph runtime task result.",
            "changedFiles": ["src/controller.py", "tests/test_controller.py"],
            "tests": [{
                "argv": ["python", "-m", "unittest", "tests.test_controller"],
                "exitCode": 0 if status == "IMPLEMENTED" else 1,
                "testsRun": 1,
            }],
            "blockers": [] if status == "IMPLEMENTED" else ["Regression failure"],
        }

    @staticmethod
    def _gate(task_id: str, baseline: str) -> dict:
        return {
            "schemaVersion": 3,
            "kind": "WORK_ITEM_GATE",
            "workItemId": task_id,
            "baselineFingerprint": baseline,
            "verdict": "PASS",
            "summary": "Graph gate passed.",
            "scope": {
                "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                "outOfScopeFiles": [],
            },
            "acceptance": [{"id": "A-001", "status": "PASS", "evidence": "Verified."}],
            "tests": [{
                "argv": ["python", "-m", "unittest", "tests.test_controller"],
                "exitCode": 0,
                "testsRun": 1,
                "summary": "Passed.",
            }],
            "findings": {"p0": [], "p1": [], "p2": []},
        }

    def test_prepare_persists_compiled_graph_and_projects_bilingual_views(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            self.assertRegex(prepared["graphFingerprint"], r"^[0-9a-f]{64}$")
            self.assertEqual(prepared["graphSummary"]["taskExecutions"], 1)
            package = Path(prepared["artifactDir"])
            graph_markdown = (package / "execution-graph.md").read_text(encoding="utf-8")
            self.assertIn("# 交付图 / Delivery Graph", graph_markdown)
            self.assertIn("## 执行图 / Execution Graph", graph_markdown)
            self.assertIn("## 治理图 / Governance Graph", graph_markdown)

            database = Path(temporary, ".layered-delivery", "governance.sqlite3")
            with closing(sqlite3.connect(database)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(
                    {"graph_definitions", "graph_nodes", "graph_edges", "graph_runs", "node_runs", "graph_events"}
                    .issubset(tables)
                )
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM graph_definitions").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM graph_runs").fetchone()[0], 0)

    def test_freeze_rejects_a_tampered_graph_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            Path(prepared["artifactDir"], "execution-graph.md").write_text(
                "tampered graph\n",
                encoding="utf-8",
            )
            with self.assertRaises(GatedLoopError) as raised:
                freeze_hierarchy(
                    root=temporary,
                    root_id=prepared["rootId"],
                    expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                    development_mode="active",
                    confirmed=True,
                )
            self.assertEqual(raised.exception.code, "DELIVERY_GRAPH_PROJECTION_CHANGED")

    def test_frontier_advances_from_task_to_gate_review_and_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            task_id = prepared["rootId"]
            frozen = prepared["frozen"]
            self.assertEqual(frozen["graphFingerprint"], prepared["graphFingerprint"])
            self.assertRegex(frozen["graphRun"]["runId"], r"^run-[a-z0-9-]+$")

            frontier = get_graph_frontier(root=temporary, work_item_id=task_id)
            self.assertEqual(frontier["actions"][0]["action"], "DISPATCH_TASK")
            self.assertEqual(frontier["actions"][0]["nodeId"], execution_node_id(task_id))

            dispatch_task(root=temporary, item_id=task_id, owner="developer", operation_id="op-graph")
            status = get_graph_status(root=temporary, work_item_id=task_id)
            execute = next(node for node in status["nodes"] if node["id"] == execution_node_id(task_id))
            self.assertEqual(execute["status"], "CLAIMED")
            self.assertEqual(execute["operationId"], "op-graph")

            record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-graph",
                status="IMPLEMENTED",
                evidence=self._task_result(task_id, "op-graph"),
            )
            frontier = get_graph_frontier(root=temporary, work_item_id=task_id)
            self.assertEqual(
                [(action["action"], action["nodeId"]) for action in frontier["actions"]],
                [("RUN_GATE", gate_node_id(task_id))],
            )

            accept_work_item(
                root=temporary,
                item_id=task_id,
                evidence=self._gate(task_id, prepared["baselineFingerprints"][task_id]),
            )
            self.assertEqual(
                get_graph_frontier(root=temporary, work_item_id=task_id)["actions"][0]["action"],
                "REQUEST_REVIEW",
            )
            record_acceptance(
                root=temporary,
                item_id=task_id,
                action="INDEPENDENT_REVIEW_PASS",
                evidence={
                    "schemaVersion": 3,
                    "kind": "INDEPENDENT_REVIEW",
                    "reviewer": "fresh-reviewer",
                    "isolation": "FRESH_READ_ONLY",
                    "verdict": "PASS",
                    "findings": {"p0": 0, "p1": 0},
                },
            )
            self.assertEqual(
                get_graph_frontier(root=temporary, work_item_id=task_id)["actions"][0]["action"],
                "REQUEST_USER_CONFIRMATION",
            )
            record_acceptance(
                root=temporary,
                item_id=task_id,
                action="USER_CONFIRMED",
                evidence={
                    "schemaVersion": 3,
                    "kind": "USER_CONFIRMATION",
                    "confirmedBy": "user",
                    "decision": "CONFIRMED",
                },
            )
            completed = get_graph_status(root=temporary, work_item_id=task_id)
            self.assertEqual(completed["run"]["status"], "COMPLETED")
            self.assertEqual(get_graph_frontier(root=temporary, work_item_id=task_id)["actions"], [])
            event_types = [event["eventType"] for event in list_graph_events(root=temporary, work_item_id=task_id)]
            self.assertEqual(
                event_types,
                [
                    "GRAPH_RUN_STARTED",
                    "TASK_CLAIMED",
                    "TASK_IMPLEMENTED",
                    "GATE_PASSED",
                    "REVIEW_PASSED",
                    "USER_CONFIRMED",
                ],
            )
            timeline = Path(prepared["artifactDir"], "run-timeline.md").read_text(encoding="utf-8")
            self.assertIn("# 运行时间线 / Run Timeline", timeline)
            self.assertIn("USER_CONFIRMED", timeline)

    def test_retry_creates_a_new_node_attempt_without_changing_the_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            task_id = prepared["rootId"]
            dispatch_task(root=temporary, item_id=task_id, owner="developer", operation_id="op-blocked")
            record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-blocked",
                status="BLOCKED",
                evidence=self._task_result(task_id, "op-blocked", status="BLOCKED"),
            )
            before = get_graph_status(root=temporary, work_item_id=task_id)
            before_execute = next(node for node in before["nodes"] if node["id"] == execution_node_id(task_id))
            self.assertEqual(before_execute["attempt"], 1)
            self.assertEqual(before_execute["status"], "BLOCKED")

            retry_work_item(
                root=temporary,
                item_id=task_id,
                expected_baseline_fingerprint=prepared["baselineFingerprints"][task_id],
            )
            after = get_graph_status(root=temporary, work_item_id=task_id)
            after_execute = next(node for node in after["nodes"] if node["id"] == execution_node_id(task_id))
            self.assertEqual(after["graphFingerprint"], before["graphFingerprint"])
            self.assertEqual(after_execute["attempt"], 2)
            self.assertEqual(after_execute["status"], "READY")

    def test_graph_commands_are_available_through_the_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            for command in ("graph-status", "graph-frontier", "graph-events"):
                stdout = io.StringIO()
                stderr = io.StringIO()
                code = run_cli(
                    [command, "--item", prepared["rootId"], "--json"],
                    cwd=temporary,
                    stdout=stdout,
                    stderr=stderr,
                )
                self.assertEqual(code, 0, stderr.getvalue())
                self.assertTrue(json.loads(stdout.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
