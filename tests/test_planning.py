from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hdg.errors import GatedLoopError
from hdg.planning import (
    freeze_hierarchy,
    prepare_hierarchy,
)
from hdg.repository import GovernanceRepository

from .fixtures import hierarchy_definition, task_definition, task_hierarchy


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
            first = prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            first_revision = GovernanceRepository(temporary).read_registry()["revision"]
            second = prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            self.assertTrue(second["idempotent"])
            self.assertEqual(second["hierarchyFingerprint"], first["hierarchyFingerprint"])
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

    def test_registry_rejects_unknown_historical_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            path = Path(temporary, ".hierarchical-delivery-governance", "work-item-registry.json")
            registry = json.loads(path.read_text(encoding="utf-8"))
            registry["obsoleteCompatibility"] = []
            path.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaises(GatedLoopError) as raised:
                GovernanceRepository(temporary).read_registry()
            self.assertEqual(raised.exception.code, "WORK_ITEM_REGISTRY_INVALID")

    def test_registry_rejects_unknown_fields_inside_current_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            path = Path(temporary, ".hierarchical-delivery-governance", "work-item-registry.json")
            registry = json.loads(path.read_text(encoding="utf-8"))
            registry["workItems"][0]["delivery"] = {"status": "LEGACY"}
            path.write_text(json.dumps(registry), encoding="utf-8")
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
            mode_path = Path(
                temporary,
                ".hierarchical-delivery-governance",
                "work-items",
                prepared["rootId"],
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
                prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            self.assertEqual(raised.exception.code, "SELF_HOSTING_DOGFOOD_REQUIRED")
            self.assertFalse(Path(temporary, ".hierarchical-delivery-governance").exists())


if __name__ == "__main__":
    unittest.main()
