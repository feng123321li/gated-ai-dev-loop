from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hdg.errors import GatedLoopError
from hdg.execution import select_development_mode
from hdg.planning import (
    freeze_work_item,
    prepare_work_item,
    promote_work_item,
    revise_work_item,
)
from hdg.repository import GovernanceRepository

from .fixtures import capability_definition, task_definition


class PlanningTests(unittest.TestCase):
    def test_review_tampering_blocks_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_work_item(root=temporary, definition=task_definition(), host_runtime="codex")
            review = Path(prepared["artifactDir"], "development-review.md")
            review.write_text(review.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
            with self.assertRaises(GatedLoopError) as raised:
                freeze_work_item(
                    root=temporary,
                    item_id=prepared["id"],
                    expected_baseline_fingerprint=prepared["baselineFingerprint"],
                    confirmed=True,
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_PACKAGE_CHANGED")

    def test_reprepare_changes_fingerprint_and_invalidates_old_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = prepare_work_item(root=temporary, definition=task_definition(), host_runtime="codex")
            source = task_definition(title="Reviewed Python controller")
            second = prepare_work_item(root=temporary, definition=source, host_runtime="codex")
            self.assertTrue(second["revised"])
            self.assertNotEqual(first["baselineFingerprint"], second["baselineFingerprint"])
            with self.assertRaises(GatedLoopError) as raised:
                freeze_work_item(
                    root=temporary,
                    item_id=second["id"],
                    expected_baseline_fingerprint=first["baselineFingerprint"],
                    confirmed=True,
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_REVISION_CONFLICT")

    def test_revision_removes_confirmed_development_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_work_item(root=temporary, definition=task_definition(), host_runtime="codex")
            freeze_work_item(
                root=temporary,
                item_id=prepared["id"],
                expected_baseline_fingerprint=prepared["baselineFingerprint"],
                confirmed=True,
            )
            select_development_mode(
                root=temporary,
                item_id=prepared["id"],
                mode="manual",
                expected_baseline_fingerprint=prepared["baselineFingerprint"],
                confirmed=True,
            )
            source = task_definition(title="Revised Python controller")
            revised = revise_work_item(
                root=temporary,
                definition=source,
                expected_baseline_fingerprint=prepared["baselineFingerprint"],
                confirmed=True,
            )
            self.assertEqual(revised["status"], "WAITING_FOR_DEVELOPMENT_MODE_SELECTION")
            self.assertFalse(Path(temporary, ".hierarchical-delivery-governance", "work-items", prepared["id"], "development-mode.json").exists())

    def test_root_task_can_be_promoted_under_frozen_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = prepare_work_item(root=temporary, definition=task_definition(), host_runtime="codex")
            freeze_work_item(
                root=temporary,
                item_id=task["id"],
                expected_baseline_fingerprint=task["baselineFingerprint"],
                confirmed=True,
            )
            capability = prepare_work_item(root=temporary, definition=capability_definition(), host_runtime="codex")
            freeze_work_item(
                root=temporary,
                item_id=capability["id"],
                expected_baseline_fingerprint=capability["baselineFingerprint"],
                confirmed=True,
            )
            promoted = promote_work_item(
                root=temporary,
                item_id=task["id"],
                parent_id=capability["id"],
                expected_baseline_fingerprint=task["baselineFingerprint"],
                expected_parent_baseline_fingerprint=capability["baselineFingerprint"],
                confirmed=True,
            )
            self.assertEqual(promoted["parentId"], capability["id"])
            registry = GovernanceRepository(temporary).read_registry()
            self.assertEqual(len(registry["promotionHistory"]), 1)
            self.assertEqual(registry["promotionHistory"][0]["schemaVersion"], 3)

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
                definitions.append(source)
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(
                    lambda source: prepare_work_item(root=temporary, definition=source, host_runtime="codex"),
                    definitions,
                ))
            self.assertEqual(len(results), 4)
            registry = GovernanceRepository(temporary).read_registry()
            self.assertEqual({item["id"] for item in registry["workItems"]}, {source["id"] for source in definitions})

    def test_registry_rejects_unknown_historical_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepare_work_item(root=temporary, definition=task_definition(), host_runtime="codex")
            path = Path(temporary, ".hierarchical-delivery-governance", "work-item-registry.json")
            registry = json.loads(path.read_text(encoding="utf-8"))
            registry["obsoleteCompatibility"] = []
            path.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaises(GatedLoopError) as raised:
                GovernanceRepository(temporary).read_registry()
            self.assertEqual(raised.exception.code, "WORK_ITEM_REGISTRY_INVALID")

    def test_registry_rejects_unknown_fields_inside_current_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepare_work_item(root=temporary, definition=task_definition(), host_runtime="codex")
            path = Path(temporary, ".hierarchical-delivery-governance", "work-item-registry.json")
            registry = json.loads(path.read_text(encoding="utf-8"))
            registry["workItems"][0]["delivery"] = {"status": "LEGACY"}
            path.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaises(GatedLoopError) as raised:
                GovernanceRepository(temporary).read_registry()
            self.assertEqual(raised.exception.code, "WORK_ITEM_REGISTRY_INVALID")

    def test_development_mode_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_work_item(root=temporary, definition=task_definition(), host_runtime="codex")
            freeze_work_item(
                root=temporary,
                item_id=prepared["id"],
                expected_baseline_fingerprint=prepared["baselineFingerprint"],
                confirmed=True,
            )
            select_development_mode(
                root=temporary,
                item_id=prepared["id"],
                mode="active",
                expected_baseline_fingerprint=prepared["baselineFingerprint"],
                confirmed=True,
            )
            mode_path = Path(
                temporary,
                ".hierarchical-delivery-governance",
                "work-items",
                prepared["id"],
                "development-mode.json",
            )
            mode = json.loads(mode_path.read_text(encoding="utf-8"))
            mode["legacyMode"] = True
            mode_path.write_text(json.dumps(mode), encoding="utf-8")
            with self.assertRaises(GatedLoopError) as raised:
                GovernanceRepository(temporary).read_registry()
            self.assertEqual(raised.exception.code, "WORK_ITEM_DEVELOPMENT_MODE_CHANGED")

    def test_self_hosting_project_blocks_mutation_without_dogfood(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "pyproject.toml").write_text(
                '[project]\nname = "hierarchical-delivery-governance"\n',
                encoding="utf-8",
            )
            with self.assertRaises(GatedLoopError) as raised:
                prepare_work_item(root=temporary, definition=task_definition(), host_runtime="codex")
            self.assertEqual(raised.exception.code, "SELF_HOSTING_DOGFOOD_REQUIRED")
            self.assertFalse(Path(temporary, ".hierarchical-delivery-governance").exists())


if __name__ == "__main__":
    unittest.main()
