from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from hdg.acceptance import accept_work_item, record_acceptance
from hdg.execution import dispatch_task, record_task_result, select_development_mode
from hdg.planning import freeze_work_item, prepare_work_item
from hdg.repository import GovernanceRepository

from .fixtures import task_definition


def write_evidence(root: str, name: str, value: dict) -> dict[str, str]:
    path = Path(root, name)
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(data)
    return {"path": name, "sha256": hashlib.sha256(data).hexdigest()}


class AcceptanceFlowTests(unittest.TestCase):
    def test_task_reaches_completed_with_distinct_review_and_confirmation(self) -> None:
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
            dispatch_task(
                root=temporary,
                item_id=prepared["id"],
                owner="developer",
                operation_id="op-001",
            )
            result = write_evidence(temporary, "task-result.json", {
                "schemaVersion": 3,
                "kind": "TASK_RESULT",
                "taskId": prepared["id"],
                "operationId": "op-001",
                "status": "IMPLEMENTED",
                "summary": "Implemented the frozen Python controller.",
                "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                "tests": [{"argv": ["python", "-m", "unittest", "tests.test_controller"], "exitCode": 0, "testsRun": 1}],
                "blockers": [],
            })
            record_task_result(
                root=temporary,
                item_id=prepared["id"],
                operation_id="op-001",
                status="IMPLEMENTED",
                evidence=result,
                strict_evidence=True,
            )
            gate = write_evidence(temporary, "gate.json", {
                "schemaVersion": 3,
                "kind": "WORK_ITEM_GATE",
                "workItemId": prepared["id"],
                "baselineFingerprint": prepared["baselineFingerprint"],
                "verdict": "PASS",
                "summary": "All frozen acceptance checks passed.",
                "scope": {
                    "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                    "outOfScopeFiles": [],
                },
                "acceptance": [{"id": "A-001", "status": "PASS", "evidence": "Unit test passed."}],
                "tests": [{
                    "argv": ["python", "-m", "unittest", "tests.test_controller"],
                    "exitCode": 0,
                    "testsRun": 1,
                    "summary": "One test passed.",
                }],
                "findings": {"p0": [], "p1": [], "p2": []},
            })
            accepted = accept_work_item(root=temporary, item_id=prepared["id"], evidence=gate)
            self.assertEqual(accepted["status"], "VERIFIED")

            review = write_evidence(temporary, "review.json", {
                "schemaVersion": 3,
                "kind": "INDEPENDENT_REVIEW",
                "reviewer": "fresh-reviewer",
                "isolation": "FRESH_READ_ONLY",
                "verdict": "PASS",
                "findings": {"p0": 0, "p1": 0},
            })
            record_acceptance(
                root=temporary,
                item_id=prepared["id"],
                action="INDEPENDENT_REVIEW_PASS",
                evidence=review,
            )
            confirmation = write_evidence(temporary, "confirmation.json", {
                "schemaVersion": 3,
                "kind": "USER_CONFIRMATION",
                "confirmedBy": "user",
                "decision": "CONFIRMED",
            })
            completed = record_acceptance(
                root=temporary,
                item_id=prepared["id"],
                action="USER_CONFIRMED",
                evidence=confirmation,
            )
            self.assertEqual(completed["acceptance"]["status"], "COMPLETED")
            registry = GovernanceRepository(temporary).read_registry()
            self.assertEqual(registry["workItems"][0]["acceptance"]["status"], "COMPLETED")
            self.assertTrue(Path(temporary, completed["acceptanceReport"]["markdownPath"]).is_file())


if __name__ == "__main__":
    unittest.main()
