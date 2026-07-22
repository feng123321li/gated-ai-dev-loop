from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hdg.acceptance import accept_work_item, record_acceptance
from hdg.execution import dispatch_task, record_task_result
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import GovernanceRepository

from .fixtures import task_hierarchy


class AcceptanceFlowTests(unittest.TestCase):
    def test_blocked_task_can_retry_without_mid_development_human_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )
            task_id = prepared["rootId"]
            dispatch_task(root=temporary, item_id=task_id, owner="developer", operation_id="op-retry")
            blocked = {
                "schemaVersion": 3,
                "kind": "TASK_RESULT",
                "taskId": task_id,
                "operationId": "op-retry",
                "status": "BLOCKED",
                "summary": "Regression test failed and requires another implementation loop.",
                "changedFiles": ["src/controller.py"],
                "tests": [{"argv": ["python", "-m", "unittest"], "exitCode": 1, "testsRun": 1}],
                "blockers": ["Regression failure"],
                "failure": {
                    "class": "RETRYABLE",
                    "code": "REGRESSION_FAILURE",
                    "summary": "The regression can be retried within the frozen contract.",
                },
            }
            result = record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-retry",
                status="BLOCKED",
                evidence=blocked,
            )

            self.assertEqual(result["routingDecision"]["action"], "RETRY_NODE")
            self.assertEqual(result["status"], "BLOCKED")

    def test_task_reaches_completed_with_distinct_review_and_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(root=temporary, hierarchy=task_hierarchy(), host_runtime="codex")
            freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )
            task_id = prepared["rootId"]
            baseline = prepared["baselineFingerprints"][task_id]
            dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer",
                operation_id="op-001",
            )
            result = {
                "schemaVersion": 3,
                "kind": "TASK_RESULT",
                "taskId": task_id,
                "operationId": "op-001",
                "status": "IMPLEMENTED",
                "summary": "Implemented the frozen Python controller.",
                "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                "tests": [{"argv": ["python", "-m", "unittest", "tests.test_controller"], "exitCode": 0, "testsRun": 1}],
                "blockers": [],
                "failure": None,
            }
            result_record = record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-001",
                status="IMPLEMENTED",
                evidence=result,
            )
            development_review_path = Path(temporary, result_record["developmentReview"]["markdownPath"])
            self.assertTrue(development_review_path.is_file())
            development_review = development_review_path.read_text(encoding="utf-8")
            self.assertIn("# 开发复核", development_review)
            self.assertIn("计划文件", development_review)
            self.assertIn("实际文件", development_review)
            self.assertIn("不代表门禁通过", development_review)
            self.assertFalse(Path(temporary, ".layered-delivery", "work-items", task_id, "acceptance-report.md").exists())
            gate = {
                "schemaVersion": 3,
                "kind": "WORK_ITEM_GATE",
                "workItemId": task_id,
                "baselineFingerprint": baseline,
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
            }
            accepted = accept_work_item(root=temporary, item_id=task_id, evidence=gate)
            self.assertEqual(accepted["status"], "VERIFIED")

            review = {
                "schemaVersion": 3,
                "kind": "INDEPENDENT_REVIEW",
                "reviewer": "fresh-reviewer",
                "isolation": "FRESH_READ_ONLY",
                "verdict": "PASS",
                "findings": {"p0": 0, "p1": 0},
            }
            record_acceptance(
                root=temporary,
                item_id=task_id,
                action="INDEPENDENT_REVIEW_PASS",
                evidence=review,
            )
            confirmation = {
                "schemaVersion": 3,
                "kind": "USER_CONFIRMATION",
                "confirmedBy": "user",
                "decision": "CONFIRMED",
            }
            completed = record_acceptance(
                root=temporary,
                item_id=task_id,
                action="USER_CONFIRMED",
                evidence=confirmation,
            )
            self.assertEqual(completed["acceptance"]["status"], "COMPLETED")
            registry = GovernanceRepository(temporary).read_registry()
            self.assertEqual(registry["workItems"][0]["acceptance"]["status"], "COMPLETED")
            self.assertTrue(Path(temporary, completed["acceptanceReport"]["markdownPath"]).is_file())
            monthly_root = Path(
                temporary,
                ".layered-delivery",
                "workspace-overview",
            )
            monthly_index = next(monthly_root.glob("*.md"))
            monthly_overview = (
                monthly_root / monthly_index.stem / f"{task_id}.md"
            ).read_text(encoding="utf-8")
            self.assertNotIn("- 需求完成日期（本机时区）：未完成", monthly_overview)
            self.assertRegex(
                monthly_overview,
                r"- 需求完成日期（本机时区）：\d{4}-\d{2}-\d{2}",
            )


if __name__ == "__main__":
    unittest.main()
