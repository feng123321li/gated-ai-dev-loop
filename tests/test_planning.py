from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hdg.errors import GatedLoopError
from hdg.execution import dispatch_task, list_ready_tasks, record_task_result
from hdg.planning import (
    freeze_hierarchy,
    prepare_hierarchy,
)
from hdg.repository import GovernanceRepository

from .fixtures import hierarchy_definition, task_definition, task_hierarchy, two_task_capability_hierarchy


class PlanningTests(unittest.TestCase):
    def test_plan_tampering_blocks_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            plan = Path(prepared["artifactDir"], "development-plan.md")
            plan.write_text(plan.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
            with self.assertRaises(GatedLoopError) as raised:
                freeze_hierarchy(
                    root=temporary,
                    root_id=prepared["rootId"],
                    expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                    development_mode="active",
                    confirmed=True,
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_HIERARCHY_PLAN_CHANGED")

    def test_reprepare_changes_fingerprint_and_invalidates_old_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            source = task_definition(title="Reviewed Python controller")
            second = prepare_hierarchy(
                root=temporary,
                hierarchy=hierarchy_definition(source),
                host_runtime="codex",
            )
            self.assertTrue(second["revised"])
            self.assertNotEqual(first["hierarchyFingerprint"], second["hierarchyFingerprint"])
            with self.assertRaises(GatedLoopError) as raised:
                freeze_hierarchy(
                    root=temporary,
                    root_id=second["rootId"],
                    expected_hierarchy_fingerprint=first["hierarchyFingerprint"],
                    development_mode="active",
                    confirmed=True,
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_REVISION_CONFLICT")

    def test_reprepare_of_the_same_tree_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="claude-code")
            first_revision = GovernanceRepository(temporary).read_registry()["revision"]
            second = prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            self.assertTrue(second["idempotent"])
            self.assertEqual(second["hierarchyFingerprint"], first["hierarchyFingerprint"])
            self.assertEqual(second["hostAutomation"], first["hostAutomation"])
            self.assertEqual(second["hostAutomation"]["hostRuntime"], "claude-code")
            self.assertEqual(GovernanceRepository(temporary).read_registry()["revision"], first_revision)

    def test_concurrent_prepares_preserve_every_registry_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            definitions = []
            for index in range(4):
                source = task_definition(
                    id=f"t-concurrent-{index}",
                    title=f"Concurrent {index}",
                    scope=[f"src/controller-{index}.py", f"tests/test_controller_{index}.py"],
                )
                source["developmentPlan"]["fileChanges"] = [
                    {"path": f"src/controller-{index}.py", "action": "ADD", "purpose": f"Controller {index}."},
                    {"path": f"tests/test_controller_{index}.py", "action": "ADD", "purpose": f"Tests {index}."},
                ]
                source["developmentPlan"]["interfaces"][0]["location"] = f"src/controller-{index}.py"
                definitions.append(hierarchy_definition(source))
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(
                    lambda source: prepare_hierarchy(root=temporary, hierarchy=source, host_runtime="codex"),
                    definitions,
                ))
            self.assertEqual(len(results), 4)
            registry = GovernanceRepository(temporary).read_registry()
            self.assertEqual(
                {item["id"] for item in registry["workItems"]},
                {source["root"]["definition"]["id"] for source in definitions},
            )

    def test_database_rejects_non_current_schema_without_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            path = Path(temporary, ".layered-delivery", "governance.sqlite3")
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("PRAGMA user_version = 2")
                connection.commit()
            with self.assertRaises(GatedLoopError) as raised:
                GovernanceRepository(temporary).read_registry()
            self.assertEqual(raised.exception.code, "WORK_ITEM_DATABASE_SCHEMA_UNSUPPORTED")

    def test_registry_rejects_unknown_fields_inside_current_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            path = Path(temporary, ".layered-delivery", "governance.sqlite3")
            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute("SELECT entry_json FROM work_items").fetchone()
                entry = json.loads(row[0])
                entry["delivery"] = {"status": "LEGACY"}
                connection.execute(
                    "UPDATE work_items SET entry_json = ? WHERE id = ?",
                    (json.dumps(entry), entry["id"]),
                )
                connection.commit()
            with self.assertRaises(GatedLoopError) as raised:
                GovernanceRepository(temporary).read_registry()
            self.assertEqual(raised.exception.code, "WORK_ITEM_REGISTRY_INVALID")
            with self.assertRaises(GatedLoopError) as prepare_error:
                prepare_hierarchy(
                    root=temporary,
                    hierarchy=task_hierarchy(id="t-unrelated-after-corruption"),
                    host_runtime="codex",
                )
            self.assertEqual(prepare_error.exception.code, "WORK_ITEM_REGISTRY_INVALID")

    def test_invalid_historical_entry_is_isolated_from_new_requirement_and_claimed_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            existing = prepare_hierarchy(
                root=temporary,
                hierarchy=two_task_capability_hierarchy(),
                host_runtime="codex",
            )
            freeze_hierarchy(
                root=temporary,
                root_id=existing["rootId"],
                expected_hierarchy_fingerprint=existing["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )
            dispatch_task(
                root=temporary,
                item_id="t-python-worker",
                owner="developer",
                operation_id="op-existing-worker",
            )

            database = Path(temporary, ".layered-delivery", "governance.sqlite3")
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT entry_json FROM work_items WHERE id = ?",
                    ("t-python-controller",),
                ).fetchone()
                historical_entry = json.loads(row[0])
                historical_entry["latestEvidence"] = {
                    "path": ".hdg-tmp/historical-result.json",
                    "sha256": "0" * 64,
                }
                historical_entry["latestResult"] = {
                    "artifact": {"schemaVersion": 3, "kind": "HISTORICAL_RESULT"},
                    "evidence": {
                        "path": ".hdg-tmp/historical-result.json",
                        "sha256": "0" * 64,
                    },
                    "recordedAt": historical_entry["updatedAt"],
                }
                historical_json = json.dumps(
                    historical_entry,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                connection.execute(
                    "UPDATE work_items SET entry_json = ? WHERE id = ?",
                    (historical_json, "t-python-controller"),
                )
                connection.commit()

            new_definition = task_definition(
                id="t-independent-requirement",
                title="Independent requirement",
                scope=["src/independent.py", "tests/test_independent.py"],
                testCommands=[["python", "-m", "unittest", "tests.test_independent"]],
            )
            new_definition["developmentPlan"]["fileChanges"] = [
                {"path": "src/independent.py", "action": "ADD", "purpose": "Add independent behavior."},
                {"path": "tests/test_independent.py", "action": "ADD", "purpose": "Verify independent behavior."},
            ]
            new_definition["developmentPlan"]["interfaces"][0]["location"] = "src/independent.py"
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=hierarchy_definition(new_definition),
                host_runtime="codex",
            )
            self.assertEqual(prepared["rootId"], "t-independent-requirement")
            with self.assertRaises(GatedLoopError) as isolated:
                dispatch_task(
                    root=temporary,
                    item_id="t-python-controller",
                    owner="developer",
                    operation_id="op-isolated-controller",
                )
            self.assertEqual(
                isolated.exception.code,
                "WORK_ITEM_ENTRY_READ_ONLY_ISOLATED",
            )
            self.assertEqual(list_ready_tasks(root=temporary, work_item_id=existing["rootId"]), [])
            freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )

            result = {
                "schemaVersion": 3,
                "kind": "TASK_RESULT",
                "taskId": "t-python-worker",
                "operationId": "op-existing-worker",
                "status": "IMPLEMENTED",
                "summary": "The already claimed sibling completed independently.",
                "changedFiles": ["src/worker.py", "tests/test_worker.py"],
                "tests": [{
                    "argv": ["python", "-m", "unittest", "tests.test_worker"],
                    "exitCode": 0,
                    "testsRun": 1,
                }],
                "blockers": [],
                "failure": None,
            }
            recorded = record_task_result(
                root=temporary,
                item_id="t-python-worker",
                operation_id="op-existing-worker",
                status="IMPLEMENTED",
                evidence=result,
            )
            self.assertEqual(recorded["status"], "IMPLEMENTED")

            with closing(sqlite3.connect(database)) as connection:
                preserved = connection.execute(
                    "SELECT entry_json FROM work_items WHERE id = ?",
                    ("t-python-controller",),
                ).fetchone()[0]
                worker = json.loads(connection.execute(
                    "SELECT entry_json FROM work_items WHERE id = ?",
                    ("t-python-worker",),
                ).fetchone()[0])
            self.assertEqual(preserved, historical_json)
            self.assertEqual(worker["status"], "IMPLEMENTED")
            overview = Path(
                temporary,
                ".layered-delivery",
                "workspace-overview.md",
            ).read_text(encoding="utf-8")
            self.assertIn("只读隔离", overview)
            self.assertIn("t-python-controller", overview)

            with self.assertRaises(GatedLoopError) as raised:
                GovernanceRepository(temporary).read_registry()
            self.assertEqual(raised.exception.code, "WORK_ITEM_REGISTRY_INVALID")

    def test_development_mode_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )
            database = Path(temporary, ".layered-delivery", "governance.sqlite3")
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT entry_json FROM work_items WHERE id = ?",
                    (prepared["rootId"],),
                ).fetchone()
                entry = json.loads(row[0])
                entry["developmentMode"]["legacyMode"] = True
                connection.execute(
                    "UPDATE work_items SET entry_json = ? WHERE id = ?",
                    (json.dumps(entry), prepared["rootId"]),
                )
                connection.commit()
            with self.assertRaises(GatedLoopError) as raised:
                GovernanceRepository(temporary).read_registry()
            self.assertEqual(raised.exception.code, "WORK_ITEM_REGISTRY_INVALID")

    def test_self_hosting_project_blocks_mutation_without_dogfood(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "pyproject.toml").write_text(
                '[project]\nname = "layered-delivery"\n',
                encoding="utf-8",
            )
            with self.assertRaises(GatedLoopError) as raised:
                prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            self.assertEqual(raised.exception.code, "SELF_HOSTING_DOGFOOD_REQUIRED")
            self.assertFalse(Path(temporary, ".layered-delivery").exists())


if __name__ == "__main__":
    unittest.main()
