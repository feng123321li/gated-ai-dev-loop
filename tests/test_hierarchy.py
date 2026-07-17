from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from hdg.acceptance import accept_work_item, record_acceptance
from hdg.execution import dispatch_task, record_task_result, select_development_mode
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import GovernanceRepository

from .fixtures import delivery_hierarchy


class HierarchyFlowTests(unittest.TestCase):
    @staticmethod
    def _write_evidence(root: str, name: str, value: dict) -> dict[str, str]:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        Path(root, name).write_bytes(data)
        return {"path": name, "sha256": hashlib.sha256(data).hexdigest()}

    def _gate(self, root: str, prepared: dict, command: list[str], changed_files: list[str], name: str) -> dict:
        evidence = self._write_evidence(root, name, {
            "schemaVersion": 3,
            "kind": "WORK_ITEM_GATE",
            "workItemId": prepared["id"],
            "baselineFingerprint": prepared["baselineFingerprint"],
            "verdict": "PASS",
            "summary": "The current frozen level passed.",
            "scope": {"changedFiles": changed_files, "outOfScopeFiles": []},
            "acceptance": [{"id": "A-001", "status": "PASS", "evidence": "Verified."}],
            "tests": [{"argv": command, "exitCode": 0, "testsRun": 1, "summary": "Passed."}],
            "findings": {"p0": [], "p1": [], "p2": []},
        })
        return accept_work_item(root=root, item_id=prepared["id"], evidence=evidence)

    def _prepare_and_freeze(self, root: str) -> dict:
        prepared = prepare_hierarchy(root=root, hierarchy=delivery_hierarchy(), host_runtime="claude-code")
        freeze_hierarchy(
            root=root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
            confirmed=True,
        )
        return prepared

    @staticmethod
    def _prepared_item(prepared: dict, item_id: str) -> dict:
        return {
            "id": item_id,
            "baselineFingerprint": prepared["baselineFingerprints"][item_id],
        }

    def test_delivery_capability_task_hierarchy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            delivery = self._prepared_item(prepared, "d-python-governance")
            capability = self._prepared_item(prepared, "c-python-runtime")
            task = self._prepared_item(prepared, "t-python-controller")
            registry = GovernanceRepository(temporary).read_registry()
            by_id = {item["id"]: item for item in registry["workItems"]}
            self.assertEqual(by_id[task["id"]]["parentId"], capability["id"])
            self.assertEqual(by_id[capability["id"]]["parentId"], delivery["id"])
            self.assertIsNone(by_id[delivery["id"]]["parentId"])

    def test_delivery_capability_task_full_completion_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            delivery = self._prepared_item(prepared, "d-python-governance")
            capability = self._prepared_item(prepared, "c-python-runtime")
            task = self._prepared_item(prepared, "t-python-controller")
            select_development_mode(
                root=temporary,
                item_id=task["id"],
                mode="active",
                expected_baseline_fingerprint=task["baselineFingerprint"],
                confirmed=True,
            )
            dispatch_task(root=temporary, item_id=task["id"], owner="developer", operation_id="op-nested")
            result = self._write_evidence(temporary, "nested-task-result.json", {
                "schemaVersion": 3,
                "kind": "TASK_RESULT",
                "taskId": task["id"],
                "operationId": "op-nested",
                "status": "IMPLEMENTED",
                "summary": "Implemented the nested Task.",
                "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                "tests": [{
                    "argv": ["python", "-m", "unittest", "tests.test_controller"],
                    "exitCode": 0,
                    "testsRun": 1,
                }],
                "blockers": [],
            })
            record_task_result(
                root=temporary,
                item_id=task["id"],
                operation_id="op-nested",
                status="IMPLEMENTED",
                evidence=result,
                strict_evidence=True,
            )
            self.assertEqual(self._gate(
                temporary,
                task,
                ["python", "-m", "unittest", "tests.test_controller"],
                ["src/controller.py", "tests/test_controller.py"],
                "nested-task-gate.json",
            )["status"], "VERIFIED")
            self.assertEqual(self._gate(
                temporary,
                capability,
                ["python", "-m", "unittest", "discover"],
                [],
                "capability-gate.json",
            )["status"], "VERIFIED")
            self.assertEqual(self._gate(
                temporary,
                delivery,
                ["python", "-m", "unittest", "discover"],
                [],
                "delivery-gate.json",
            )["status"], "VERIFIED")
            review = self._write_evidence(temporary, "delivery-review.json", {
                "schemaVersion": 3,
                "kind": "INDEPENDENT_REVIEW",
                "reviewer": "fresh-reviewer",
                "isolation": "FRESH_READ_ONLY",
                "verdict": "PASS",
                "findings": {"p0": 0, "p1": 0},
            })
            record_acceptance(
                root=temporary,
                item_id=delivery["id"],
                action="INDEPENDENT_REVIEW_PASS",
                evidence=review,
            )
            confirmation = self._write_evidence(temporary, "delivery-confirmation.json", {
                "schemaVersion": 3,
                "kind": "USER_CONFIRMATION",
                "confirmedBy": "user",
                "decision": "CONFIRMED",
            })
            completed = record_acceptance(
                root=temporary,
                item_id=delivery["id"],
                action="USER_CONFIRMED",
                evidence=confirmation,
            )
            self.assertEqual(completed["acceptance"]["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
