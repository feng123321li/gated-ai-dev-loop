from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hdg.acceptance import accept_work_item, record_acceptance
from hdg.errors import GatedLoopError
from hdg.execution import dispatch_task, list_ready_tasks, record_task_result
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.remediation import record_validation_remediation
from hdg.repository import GovernanceRepository

from .fixtures import delivery_hierarchy, task_hierarchy


class ValidationRemediationTests(unittest.TestCase):
    @staticmethod
    def _prepare_implemented_task(root: str) -> tuple[dict, str, str]:
        prepared = prepare_hierarchy(root=root, hierarchy=task_hierarchy(), host_runtime="codex")
        task_id = prepared["rootId"]
        baseline = prepared["baselineFingerprints"][task_id]
        freeze_hierarchy(
            root=root,
            root_id=task_id,
            expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
            development_mode="active",
            confirmed=True,
        )
        dispatch_task(root=root, item_id=task_id, owner="developer", operation_id="op-initial")
        record_task_result(
            root=root,
            item_id=task_id,
            operation_id="op-initial",
            status="IMPLEMENTED",
            evidence={
                "schemaVersion": 3,
                "kind": "TASK_RESULT",
                "taskId": task_id,
                "operationId": "op-initial",
                "status": "IMPLEMENTED",
                "summary": "Implemented the original frozen plan.",
                "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                "tests": [{
                    "argv": ["python", "-m", "unittest", "tests.test_controller"],
                    "exitCode": 0,
                    "testsRun": 1,
                }],
                "blockers": [],
            },
        )
        return prepared, task_id, baseline

    @staticmethod
    def _gate(root: str, task_id: str, baseline: str, changed_files: list[str]) -> dict:
        return accept_work_item(
            root=root,
            item_id=task_id,
            evidence={
                "schemaVersion": 3,
                "kind": "WORK_ITEM_GATE",
                "workItemId": task_id,
                "baselineFingerprint": baseline,
                "verdict": "PASS",
                "summary": "The original acceptance contract passed.",
                "scope": {"changedFiles": changed_files, "outOfScopeFiles": []},
                "acceptance": [{"id": "A-001", "status": "PASS", "evidence": "Verified."}],
                "tests": [{
                    "argv": ["python", "-m", "unittest", "tests.test_controller"],
                    "exitCode": 0,
                    "testsRun": 1,
                    "summary": "Passed.",
                }],
                "findings": {"p0": [], "p1": [], "p2": []},
            },
        )

    @staticmethod
    def _remediation(task_id: str, baseline: str) -> dict:
        return {
            "schemaVersion": 3,
            "kind": "VALIDATION_REMEDIATION",
            "taskId": task_id,
            "baselineFingerprint": baseline,
            "source": "INDEPENDENT_REVIEW",
            "summary": "Align the public documentation with the already frozen behavior.",
            "acceptanceIds": ["A-001"],
            "fileChanges": [{
                "path": "src/controller_docs.py",
                "action": "MODIFY",
                "purpose": "Correct documentation for the existing accepted behavior.",
            }],
            "assertions": {
                "goalUnchanged": True,
                "requirementsUnchanged": True,
                "acceptanceUnchanged": True,
                "interfacesUnchanged": True,
                "dataContractUnchanged": True,
                "testCommandsUnchanged": True,
                "topologyUnchanged": True,
                "externalAuthorityUnchanged": True,
            },
        }

    def test_validation_remediation_reuses_original_task_and_authorizes_added_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, task_id, baseline = self._prepare_implemented_task(temporary)
            self.assertEqual(
                self._gate(
                    temporary,
                    task_id,
                    baseline,
                    ["src/controller.py", "tests/test_controller.py"],
                )["status"],
                "VERIFIED",
            )
            package = Path(
                temporary,
                ".hierarchical-delivery-governance",
                "work-items",
                task_id,
            )
            frozen_plan = (package / "development-plan.md").read_bytes()
            frozen_baseline = (package / "baseline.md").read_bytes()

            remediated = record_validation_remediation(
                root=temporary,
                item_id=task_id,
                expected_baseline_fingerprint=baseline,
                evidence=self._remediation(task_id, baseline),
            )

            self.assertEqual(remediated["id"], task_id)
            self.assertEqual(remediated["status"], "FROZEN")
            self.assertEqual(remediated["baselineFingerprint"], baseline)
            registry = GovernanceRepository(temporary).read_registry()
            revision = registry["revision"]
            repeated = record_validation_remediation(
                root=temporary,
                item_id=task_id,
                expected_baseline_fingerprint=baseline,
                evidence=self._remediation(task_id, baseline),
            )
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(GovernanceRepository(temporary).read_registry()["revision"], revision)
            self.assertEqual([item["id"] for item in registry["workItems"]], [task_id])
            entry = registry["workItems"][0]
            self.assertEqual(entry["gate"], {"status": "NOT_RUN", "evidence": None})
            self.assertEqual(entry["acceptance"]["status"], "NOT_READY")
            self.assertEqual(list_ready_tasks(root=temporary, work_item_id=task_id), [task_id])
            self.assertEqual((package / "development-plan.md").read_bytes(), frozen_plan)
            self.assertEqual((package / "baseline.md").read_bytes(), frozen_baseline)

            dispatched = dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer",
                operation_id="op-remediation",
            )
            self.assertEqual(
                [item["path"] for item in dispatched["task"]["authorizedFileChanges"]],
                ["src/controller.py", "src/controller_docs.py", "tests/test_controller.py"],
            )
            self.assertEqual(len(dispatched["task"]["validationRemediations"]), 1)
            record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-remediation",
                status="IMPLEMENTED",
                evidence={
                    "schemaVersion": 3,
                    "kind": "TASK_RESULT",
                    "taskId": task_id,
                    "operationId": "op-remediation",
                    "status": "IMPLEMENTED",
                    "summary": "Applied the validation remediation to the original Task.",
                    "changedFiles": ["src/controller_docs.py"],
                    "tests": [{
                        "argv": ["python", "-m", "unittest", "tests.test_controller"],
                        "exitCode": 0,
                        "testsRun": 1,
                    }],
                    "blockers": [],
                },
            )
            self.assertEqual(
                self._gate(temporary, task_id, baseline, ["src/controller_docs.py"])["status"],
                "VERIFIED",
            )

            development_review = (package / "development-review.md").read_text(encoding="utf-8")
            acceptance_report = (package / "acceptance-report.md").read_text(encoding="utf-8")
            interaction_log = (package / "interaction-log.md").read_text(encoding="utf-8")
            for rendered in (development_review, acceptance_report):
                self.assertIn("验证修正", rendered)
                self.assertIn("src/controller_docs.py", rendered)
                self.assertIn("未授权文件：无", rendered)
            self.assertIn("VALIDATION_REMEDIATION", interaction_log)

    def test_completed_requirement_cannot_be_remediated_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, task_id, baseline = self._prepare_implemented_task(temporary)
            self._gate(
                temporary,
                task_id,
                baseline,
                ["src/controller.py", "tests/test_controller.py"],
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

            with self.assertRaises(GatedLoopError) as raised:
                record_validation_remediation(
                    root=temporary,
                    item_id=task_id,
                    expected_baseline_fingerprint=baseline,
                    evidence=self._remediation(task_id, baseline),
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_REMEDIATION_COMPLETED")

    def test_validation_remediation_rejects_contract_changes_and_duplicate_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, task_id, baseline = self._prepare_implemented_task(temporary)
            changed_contract = self._remediation(task_id, baseline)
            changed_contract["assertions"]["interfacesUnchanged"] = False
            with self.assertRaises(GatedLoopError) as raised:
                record_validation_remediation(
                    root=temporary,
                    item_id=task_id,
                    expected_baseline_fingerprint=baseline,
                    evidence=changed_contract,
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_REMEDIATION_EVIDENCE_INVALID")

            duplicate = self._remediation(task_id, baseline)
            duplicate["fileChanges"][0]["path"] = "src/controller.py"
            with self.assertRaises(GatedLoopError) as raised:
                record_validation_remediation(
                    root=temporary,
                    item_id=task_id,
                    expected_baseline_fingerprint=baseline,
                    evidence=duplicate,
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_REMEDIATION_FILE_ALREADY_AUTHORIZED")

    def test_child_remediation_invalidates_verified_ancestor_gates_without_new_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=delivery_hierarchy(),
                host_runtime="codex",
            )
            freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )
            task_id = "t-python-controller"
            capability_id = "c-python-runtime"
            delivery_id = "d-python-governance"
            task_baseline = prepared["baselineFingerprints"][task_id]
            dispatch_task(root=temporary, item_id=task_id, owner="developer", operation_id="op-child")
            record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-child",
                status="IMPLEMENTED",
                evidence={
                    "schemaVersion": 3,
                    "kind": "TASK_RESULT",
                    "taskId": task_id,
                    "operationId": "op-child",
                    "status": "IMPLEMENTED",
                    "summary": "Implemented the child Task.",
                    "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                    "tests": [{
                        "argv": ["python", "-m", "unittest", "tests.test_controller"],
                        "exitCode": 0,
                        "testsRun": 1,
                    }],
                    "blockers": [],
                },
            )

            def gate(item_id: str, command: list[str], changed_files: list[str]) -> None:
                accept_work_item(
                    root=temporary,
                    item_id=item_id,
                    evidence={
                        "schemaVersion": 3,
                        "kind": "WORK_ITEM_GATE",
                        "workItemId": item_id,
                        "baselineFingerprint": prepared["baselineFingerprints"][item_id],
                        "verdict": "PASS",
                        "summary": "The current hierarchy level passed.",
                        "scope": {"changedFiles": changed_files, "outOfScopeFiles": []},
                        "acceptance": [{"id": "A-001", "status": "PASS", "evidence": "Verified."}],
                        "tests": [{
                            "argv": command,
                            "exitCode": 0,
                            "testsRun": 1,
                            "summary": "Passed.",
                        }],
                        "findings": {"p0": [], "p1": [], "p2": []},
                    },
                )

            gate(task_id, ["python", "-m", "unittest", "tests.test_controller"], [
                "src/controller.py",
                "tests/test_controller.py",
            ])
            gate(capability_id, ["python", "-m", "unittest", "discover"], [])
            gate(delivery_id, ["python", "-m", "unittest", "discover"], [])

            record_validation_remediation(
                root=temporary,
                item_id=task_id,
                expected_baseline_fingerprint=task_baseline,
                evidence=self._remediation(task_id, task_baseline),
            )
            registry = GovernanceRepository(temporary).read_registry()
            by_id = {item["id"]: item for item in registry["workItems"]}
            self.assertEqual(set(by_id), {task_id, capability_id, delivery_id})
            for item_id in (task_id, capability_id, delivery_id):
                self.assertEqual(by_id[item_id]["status"], "FROZEN")
                self.assertEqual(by_id[item_id]["gate"], {"status": "NOT_RUN", "evidence": None})
            self.assertEqual(by_id[delivery_id]["acceptance"]["status"], "NOT_READY")


if __name__ == "__main__":
    unittest.main()
