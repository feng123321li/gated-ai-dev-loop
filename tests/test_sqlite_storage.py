from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from hdg.acceptance import record_work_item_gate
from hdg.errors import GatedLoopError
from hdg.execution import dispatch_task, record_task_result
from hdg.interactions import list_interactions, record_interaction
from hdg.jsonio import fingerprint
from hdg.planning import freeze_hierarchy, prepare_hierarchy, refresh_work_item_projections

from .fixtures import task_hierarchy


class SQLiteStorageTests(unittest.TestCase):
    @staticmethod
    def _governance_root(root: str) -> Path:
        return Path(root, ".layered-delivery")

    def assert_no_persisted_json(self, root: str) -> None:
        json_files = list(self._governance_root(root).rglob("*.json"))
        self.assertEqual(json_files, [])

    def test_sqlite_is_the_only_machine_authority_and_markdown_is_projected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            governance = self._governance_root(temporary)
            database = governance / "governance.sqlite3"

            self.assertTrue(database.is_file())
            self.assert_no_persisted_json(temporary)
            with closing(sqlite3.connect(database)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue({
                    "workspace",
                    "work_items",
                    "hierarchies",
                    "task_contexts",
                    "reports",
                    "interaction_events",
                }.issubset(tables))
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM work_items").fetchone()[0], 1)

            events = list_interactions(root=temporary, item_id=prepared["rootId"])
            self.assertEqual(events[0]["eventType"], "HIERARCHY_PLAN_AND_MODE_CONFIRMATION")
            interaction_log = Path(prepared["artifactDir"], "interaction-log.md")
            self.assertTrue(interaction_log.is_file())
            self.assertIn("层级方案与方式确认", interaction_log.read_text(encoding="utf-8"))

    def test_context_results_and_reports_are_stored_in_sqlite_without_json_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )
            dispatch_task(
                root=temporary,
                item_id=prepared["rootId"],
                owner="developer",
                operation_id="op-sqlite",
            )
            result_artifact = {
                "schemaVersion": 3,
                "kind": "TASK_RESULT",
                "taskId": prepared["rootId"],
                "operationId": "op-sqlite",
                "status": "IMPLEMENTED",
                "summary": "Stored directly in SQLite.",
                "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                "tests": [{
                    "argv": ["python", "-m", "unittest", "tests.test_controller"],
                    "exitCode": 0,
                    "testsRun": 1,
                }],
                "blockers": [],
            }
            record_task_result(
                root=temporary,
                item_id=prepared["rootId"],
                operation_id="op-sqlite",
                status="IMPLEMENTED",
                evidence=result_artifact,
            )
            gate_artifact = {
                "schemaVersion": 3,
                "kind": "WORK_ITEM_GATE",
                "workItemId": prepared["rootId"],
                "baselineFingerprint": prepared["baselineFingerprints"][prepared["rootId"]],
                "verdict": "PASS",
                "summary": "Validated and stored directly in SQLite.",
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
            record_work_item_gate(
                root=temporary,
                item_id=prepared["rootId"],
                status="PASS",
                evidence=gate_artifact,
            )

            database = self._governance_root(temporary) / "governance.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM task_contexts").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 2)
                entry = json.loads(
                    connection.execute(
                        "SELECT entry_json FROM work_items WHERE id = ?", (prepared["rootId"],)
                    ).fetchone()[0]
                )
            self.assertEqual(entry["latestResult"]["artifact"], result_artifact)
            self.assertEqual(entry["latestResult"]["evidence"], {"sha256": fingerprint(result_artifact)})
            self.assertEqual(entry["gate"]["artifact"], gate_artifact)
            self.assertEqual(entry["gate"]["evidence"], {"sha256": fingerprint(gate_artifact)})
            self.assert_no_persisted_json(temporary)
            package = Path(prepared["artifactDir"])
            self.assertTrue((package / "development-handoff.md").is_file())
            self.assertTrue((package / "development-review.md").is_file())
            self.assertTrue((package / "acceptance-report.md").is_file())

    def test_ai_interaction_summary_is_append_only_and_queryable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            recorded = record_interaction(
                root=temporary,
                item_id=prepared["rootId"],
                interaction={
                    "schemaVersion": 3,
                    "sessionId": "session-sqlite",
                    "actor": "USER",
                    "eventType": "USER_INSTRUCTION",
                    "summary": "确认使用 SQLite 作为唯一机器权威。",
                    "operationId": None,
                    "hostRuntime": "codex",
                },
            )

            self.assertEqual(recorded["summary"], "确认使用 SQLite 作为唯一机器权威。")
            events = list_interactions(root=temporary, item_id=prepared["rootId"])
            self.assertEqual(events[-1]["eventType"], "USER_INSTRUCTION")
            self.assertEqual(events[-1]["sessionId"], "session-sqlite")
            self.assertEqual(events[-1]["actor"], "USER")
            log = Path(prepared["artifactDir"], "interaction-log.md").read_text(encoding="utf-8")
            self.assertIn("确认使用 SQLite 作为唯一机器权威", log)

    def test_legacy_json_workspace_is_rejected_without_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            governance = self._governance_root(temporary)
            governance.mkdir()
            (governance / "work-item-registry.json").write_text("{}", encoding="utf-8")

            with self.assertRaises(GatedLoopError) as raised:
                prepare_hierarchy(
                    root=temporary,
                    hierarchy=task_hierarchy(),
                    host_runtime="codex",
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_STORAGE_UNSUPPORTED")
            self.assertFalse((governance / "governance.sqlite3").exists())

    def test_deleting_a_requirement_directory_does_not_delete_sqlite_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            package = Path(prepared["artifactDir"])
            shutil.rmtree(package)

            refresh_work_item_projections(root=temporary)

            self.assertTrue((package / "development-plan.md").is_file())
            self.assertTrue((package / "baseline.md").is_file())
            self.assertTrue((package / "progress.md").is_file())
            with closing(sqlite3.connect(self._governance_root(temporary) / "governance.sqlite3")) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM work_items").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
