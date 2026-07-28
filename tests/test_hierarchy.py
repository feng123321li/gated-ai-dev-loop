from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hdg.acceptance import accept_work_item, record_acceptance
from hdg.execution import dispatch_task, record_task_result
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import GovernanceRepository

from .fixtures import delivery_hierarchy, two_task_capability_hierarchy
from .skill_helpers import (
    activate_required_skills,
    conform_required_skills,
)


class HierarchyFlowTests(unittest.TestCase):
    def _gate(self, root: str, prepared: dict, command: list[str], changed_files: list[str]) -> dict:
        evidence = {
            "schemaVersion": 3,
            "kind": "WORK_ITEM_GATE",
            "workItemId": prepared["id"],
            "baselineFingerprint": prepared["baselineFingerprint"],
            "verdict": "PASS",
            "summary": "The current frozen level passed.",
            "scope": {"changedFiles": changed_files, "outOfScopeFiles": []},
            "acceptance": [{
                "id": "A-001",
                "requirementIds": ["R-001"],
                "status": "PASS",
                "evidence": "Verified.",
            }],
            "tests": [{"argv": command, "exitCode": 0, "testsRun": 1, "summary": "Passed."}],
            "findings": {"p0": [], "p1": [], "p2": []},
        }
        return accept_work_item(root=root, item_id=prepared["id"], evidence=evidence)

    def _prepare_and_freeze(
        self,
        root: str,
        *,
        hierarchy: dict | None = None,
    ) -> dict:
        selected_hierarchy = hierarchy or delivery_hierarchy()
        available_skills: set[str] = set()

        def collect(node: dict) -> None:
            available_skills.update(
                item["name"]
                for item in node["definition"].get("requiredSkills", [])
            )
            for child in node["children"]:
                collect(child)

        collect(selected_hierarchy["root"])
        prepared = prepare_hierarchy(
            root=root,
            hierarchy=selected_hierarchy,
            host_runtime="claude-code",
            available_skills={
                "root": sorted(available_skills),
                "project": [],
            },
        )
        freeze_hierarchy(
            root=root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
            development_mode="active",
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
            hierarchy = delivery_hierarchy()
            hierarchy["root"]["definition"]["requiredSkills"] = [{
                "name": "tdd-workflow",
                "stages": ["DEVELOPMENT"],
                "purpose": "Use the complete TDD workflow in every descendant Task.",
            }]
            prepared = self._prepare_and_freeze(
                temporary,
                hierarchy=hierarchy,
            )
            delivery = self._prepared_item(prepared, "d-python-governance")
            capability = self._prepared_item(prepared, "c-python-runtime")
            task = self._prepared_item(prepared, "t-python-controller")
            development_receipts = activate_required_skills(
                temporary,
                task["id"],
                "DEVELOPMENT",
                execution_id="op-nested",
                executor_id="developer",
            )
            dispatch_task(root=temporary, item_id=task["id"], owner="developer", operation_id="op-nested")
            result = {
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
                "failure": None,
                "skillUsage": [{
                    "name": "tdd-workflow",
                    "stage": "DEVELOPMENT",
                    "status": "APPLIED",
                    "evidence": (
                        "Applied red-green-refactor to the nested controller "
                        "and reran its frozen regression command."
                    ),
                }],
            }
            conform_required_skills(
                temporary,
                task["id"],
                development_receipts,
            )
            record_task_result(
                root=temporary,
                item_id=task["id"],
                operation_id="op-nested",
                status="IMPLEMENTED",
                evidence=result,
            )
            self.assertEqual(self._gate(
                temporary,
                task,
                ["python", "-m", "unittest", "tests.test_controller"],
                ["src/controller.py", "tests/test_controller.py"],
            )["status"], "VERIFIED")
            self.assertEqual(self._gate(
                temporary,
                capability,
                ["python", "-m", "unittest", "discover"],
                [],
            )["status"], "VERIFIED")
            self.assertEqual(self._gate(
                temporary,
                delivery,
                ["python", "-m", "unittest", "discover"],
                [],
            )["status"], "VERIFIED")
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
                item_id=delivery["id"],
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
                item_id=delivery["id"],
                action="USER_CONFIRMED",
                evidence=confirmation,
            )
            self.assertEqual(completed["acceptance"]["status"], "COMPLETED")
            report = Path(
                temporary,
                completed["acceptanceReport"]["markdownPath"],
            ).read_text(encoding="utf-8")
            self.assertIn("## 实际开发 Skill 调用", report)
            self.assertIn("t-python-controller", report)
            self.assertIn("op-nested", report)
            self.assertIn(
                "Applied red-green-refactor to the nested controller",
                report,
            )
            repository = GovernanceRepository(temporary)
            registry = repository.read_operational_registry()
            root_entry = repository.item_by_id(
                registry,
                delivery["id"],
            )
            usage = repository.actual_development_skill_usage(
                registry,
                root_entry,
            )
            self.assertEqual(len(usage), 1)
            self.assertEqual(
                {
                    "taskId": usage[0]["taskId"],
                    "taskTitle": usage[0]["taskTitle"],
                    "operationId": usage[0]["operationId"],
                    "resultStatus": usage[0]["resultStatus"],
                },
                {
                    "taskId": "t-python-controller",
                    "taskTitle": "Python controller",
                    "operationId": "op-nested",
                    "resultStatus": "IMPLEMENTED",
                },
            )
            self.assertEqual(
                usage[0]["resultEvidence"],
                repository.item_by_id(
                    registry,
                    "t-python-controller",
                )["latestResult"]["evidence"],
            )

    def test_capability_report_keeps_each_task_skill_call_separate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hierarchy = two_task_capability_hierarchy()
            hierarchy["root"]["definition"]["requiredSkills"] = [{
                "name": "tdd-workflow",
                "stages": ["DEVELOPMENT"],
                "purpose": "Use the complete TDD workflow in every Task.",
            }]
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=hierarchy,
                host_runtime="codex",
                available_skills={
                    "root": ["tdd-workflow"],
                    "project": [],
                },
            )
            freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared[
                    "hierarchyFingerprint"
                ],
                development_mode="active",
                confirmed=True,
            )
            task_specs = [
                (
                    "t-python-controller",
                    "op-controller",
                    ["python", "-m", "unittest", "tests.test_controller"],
                    ["src/controller.py", "tests/test_controller.py"],
                ),
                (
                    "t-python-worker",
                    "op-worker",
                    ["python", "-m", "unittest", "tests.test_worker"],
                    ["src/worker.py", "tests/test_worker.py"],
                ),
            ]
            for task_id, operation_id, command, changed_files in task_specs:
                development_receipts = activate_required_skills(
                    temporary,
                    task_id,
                    "DEVELOPMENT",
                    execution_id=operation_id,
                    executor_id="developer",
                )
                dispatch_task(
                    root=temporary,
                    item_id=task_id,
                    owner="developer",
                    operation_id=operation_id,
                )
                conform_required_skills(
                    temporary,
                    task_id,
                    development_receipts,
                )
                record_task_result(
                    root=temporary,
                    item_id=task_id,
                    operation_id=operation_id,
                    status="IMPLEMENTED",
                    evidence={
                        "schemaVersion": 3,
                        "kind": "TASK_RESULT",
                        "taskId": task_id,
                        "operationId": operation_id,
                        "status": "IMPLEMENTED",
                        "summary": f"Implemented {task_id}.",
                        "changedFiles": changed_files,
                        "tests": [{
                            "argv": command,
                            "exitCode": 0,
                            "testsRun": 1,
                        }],
                        "blockers": [],
                        "failure": None,
                        "skillUsage": [{
                            "name": "tdd-workflow",
                            "stage": "DEVELOPMENT",
                            "status": "APPLIED",
                            "evidence": (
                                f"Applied red-green-refactor in {task_id} "
                                "and reran its frozen regression command."
                            ),
                        }],
                    },
                )
                self._gate(
                    temporary,
                    self._prepared_item(prepared, task_id),
                    command,
                    changed_files,
                )

            capability = self._prepared_item(
                prepared,
                prepared["rootId"],
            )
            accepted = self._gate(
                temporary,
                capability,
                ["python", "-m", "unittest", "discover"],
                [],
            )
            report = Path(
                temporary,
                accepted["acceptanceReport"]["markdownPath"],
            ).read_text(encoding="utf-8")
            self.assertIn("`t-python-controller` Python controller", report)
            self.assertIn("`op-controller`", report)
            self.assertIn("`t-python-worker` Python worker", report)
            self.assertIn("`op-worker`", report)
            self.assertEqual(
                report.count(
                    "| `tdd-workflow` | `DEVELOPMENT` | `APPLIED` |"
                ),
                2,
            )

            repository = GovernanceRepository(temporary)
            registry = repository.read_operational_registry()
            root_entry = repository.item_by_id(
                registry,
                prepared["rootId"],
            )
            usage = repository.actual_development_skill_usage(
                registry,
                root_entry,
            )
            self.assertEqual(
                [
                    (record["taskId"], record["operationId"])
                    for record in usage
                ],
                [
                    ("t-python-controller", "op-controller"),
                    ("t-python-worker", "op-worker"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
