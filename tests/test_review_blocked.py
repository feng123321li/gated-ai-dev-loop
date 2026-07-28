from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from hdg import mcp_tools, operations
from hdg.acceptance import accept_work_item, record_acceptance
from hdg.errors import GatedLoopError
from hdg.execution import dispatch_task, record_task_result
from hdg.graph_runtime import (
    get_evidence_contract,
    get_graph_frontier,
    get_graph_replay,
)
from hdg.planning import freeze_hierarchy, prepare_hierarchy, retry_work_item

from .fixtures import task_hierarchy
from .skill_helpers import (
    activate_required_skills,
    conform_required_skills,
)


FINAL_REVIEW_SKILL = {
    "name": "source-command-python-review",
    "stages": ["FINAL_REVIEW"],
    "purpose": "Run the complete independent Python review before user confirmation.",
}


def _result(task_id: str, operation_id: str) -> dict:
    return {
        "schemaVersion": 3,
        "kind": "TASK_RESULT",
        "taskId": task_id,
        "operationId": operation_id,
        "status": "IMPLEMENTED",
        "summary": "Implemented the frozen controller and ran its regression command.",
        "changedFiles": ["src/controller.py", "tests/test_controller.py"],
        "tests": [{
            "argv": ["python", "-m", "unittest", "tests.test_controller"],
            "exitCode": 0,
            "testsRun": 1,
        }],
        "blockers": [],
        "failure": None,
    }


def _gate(task_id: str, baseline: str) -> dict:
    return {
        "schemaVersion": 3,
        "kind": "WORK_ITEM_GATE",
        "workItemId": task_id,
        "baselineFingerprint": baseline,
        "verdict": "PASS",
        "summary": "The gate verified the frozen acceptance contract.",
        "scope": {
            "changedFiles": ["src/controller.py", "tests/test_controller.py"],
            "outOfScopeFiles": [],
        },
        "acceptance": [{
            "id": "A-001",
            "requirementIds": ["R-001"],
            "status": "PASS",
            "evidence": "The frozen command completed successfully.",
        }],
        "tests": [{
            "argv": ["python", "-m", "unittest", "tests.test_controller"],
            "exitCode": 0,
            "testsRun": 1,
            "summary": "The frozen unittest command passed.",
        }],
        "findings": {"p0": [], "p1": [], "p2": []},
    }


def _skill_usage(status: str, evidence: str) -> list[dict[str, str]]:
    return [{
        "name": FINAL_REVIEW_SKILL["name"],
        "stage": "FINAL_REVIEW",
        "status": status,
        "evidence": evidence,
    }]


class ReviewBlockedTests(unittest.TestCase):
    def _ready_for_review(self, root: str) -> tuple[str, str]:
        prepared = prepare_hierarchy(
            root=root,
            hierarchy=task_hierarchy(requiredSkills=[FINAL_REVIEW_SKILL]),
            host_runtime="codex",
            available_skills={
                "root": [FINAL_REVIEW_SKILL["name"]],
                "project": [],
            },
        )
        task_id = prepared["rootId"]
        freeze_hierarchy(
            root=root,
            root_id=task_id,
            expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
            development_mode="active",
            confirmed=True,
        )
        dispatch_task(
            root=root,
            item_id=task_id,
            owner="developer",
            operation_id="op-review-blocked",
        )
        record_task_result(
            root=root,
            item_id=task_id,
            operation_id="op-review-blocked",
            status="IMPLEMENTED",
            evidence=_result(task_id, "op-review-blocked"),
        )
        accept_work_item(
            root=root,
            item_id=task_id,
            evidence=_gate(
                task_id,
                prepared["baselineFingerprints"][task_id],
            ),
        )
        return task_id, prepared["baselineFingerprints"][task_id]

    def test_unavailable_final_review_skill_is_persisted_and_recoverable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_id, baseline = self._ready_for_review(temporary)

            contract = get_evidence_contract(
                root=temporary,
                work_item_id=task_id,
                contract_kind="review",
            )["evidenceContract"]
            self.assertEqual(
                contract["actionOptions"]["REVIEW_BLOCKED"]["verdict"],
                "BLOCKED",
            )
            self.assertEqual(
                contract["actionOptions"]["REVIEW_BLOCKED"]["skillUsage"],
                _skill_usage("BLOCKED", "<CONCRETE_UNAVAILABILITY_REASON>"),
            )

            blocked_artifact = {
                "schemaVersion": 3,
                "kind": "INDEPENDENT_REVIEW",
                "reviewer": "fresh-reviewer",
                "isolation": "FRESH_READ_ONLY",
                "verdict": "BLOCKED",
                "summary": (
                    "The required Skill is absent from the host Skill catalog."
                ),
                "skillUsage": _skill_usage(
                    "BLOCKED",
                    "Host Skill discovery returned no canonical source-command-python-review entry.",
                ),
            }
            blocked_receipts = activate_required_skills(
                temporary,
                task_id,
                "FINAL_REVIEW",
                execution_id="review-blocked-attempt-1",
                executor_id="fresh-reviewer",
                blocked=True,
            )
            conform_required_skills(
                temporary,
                task_id,
                blocked_receipts,
                blocked=True,
            )
            blocked = record_acceptance(
                root=temporary,
                item_id=task_id,
                action="REVIEW_BLOCKED",
                evidence=blocked_artifact,
            )
            self.assertEqual(blocked["acceptance"]["status"], "REVIEW_BLOCKED")
            self.assertEqual(
                blocked["acceptance"]["review"]["artifact"]["skillUsage"][0][
                    "status"
                ],
                "BLOCKED",
            )

            replay = get_graph_replay(
                root=temporary,
                work_item_id=task_id,
            )
            review_node = next(
                node
                for node in replay["nodes"]
                if node["kind"] == "ROOT_REVIEW"
            )
            self.assertEqual(review_node["status"], "BLOCKED")
            self.assertEqual(review_node["lastTransition"], "REVIEW_BLOCKED")
            self.assertEqual(
                review_node["failureClass"],
                "EXTERNAL_AUTHORITY",
            )

            frontier = get_graph_frontier(
                root=temporary,
                work_item_id=task_id,
            )
            review_blocker = next(
                item
                for item in frontier["blocked"]
                if item["nodeKind"] == "ROOT_REVIEW"
            )
            self.assertEqual(
                review_blocker["recommendedAction"],
                "REQUEST_USER_AUTHORITY",
            )
            self.assertEqual(
                review_blocker["recoveryAction"],
                "RETRY_ITEM_AFTER_SKILL_AVAILABLE",
            )
            self.assertEqual(
                review_blocker["blockedSkillUsage"],
                blocked_artifact["skillUsage"],
            )
            self.assertEqual(
                review_blocker["mcpCall"],
                {
                    "tool": "retry_item",
                    "arguments": {
                        "item_id": task_id,
                        "expected_baseline_fingerprint": baseline,
                    },
                },
            )

            retried = retry_work_item(
                root=temporary,
                item_id=task_id,
                expected_baseline_fingerprint=baseline,
            )
            self.assertEqual(
                retried["acceptance"]["status"],
                "WAITING_FOR_INDEPENDENT_REVIEW",
            )
            retry_frontier = get_graph_frontier(
                root=temporary,
                work_item_id=task_id,
            )
            self.assertTrue(
                any(
                    action["action"] == "REQUEST_REVIEW"
                    for action in retry_frontier["actions"]
                )
            )

            passed_artifact = {
                "schemaVersion": 3,
                "kind": "INDEPENDENT_REVIEW",
                "reviewer": "fresh-reviewer",
                "isolation": "FRESH_READ_ONLY",
                "verdict": "PASS",
                "findings": {"p0": 0, "p1": 0},
                "skillUsage": _skill_usage(
                    "APPLIED",
                    "Applied the complete fresh read-only review and found no P0 or P1 issues.",
                ),
            }
            passed_receipts = activate_required_skills(
                temporary,
                task_id,
                "FINAL_REVIEW",
                execution_id="review-pass-attempt-2",
                executor_id="fresh-reviewer",
            )
            conform_required_skills(
                temporary,
                task_id,
                passed_receipts,
            )
            passed = record_acceptance(
                root=temporary,
                item_id=task_id,
                action="INDEPENDENT_REVIEW_PASS",
                evidence=passed_artifact,
            )
            self.assertEqual(
                passed["acceptance"]["status"],
                "WAITING_FOR_USER_CONFIRMATION",
            )

    def test_review_blocked_requires_a_concrete_blocked_skill_usage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_id, _ = self._ready_for_review(temporary)
            artifact = {
                "schemaVersion": 3,
                "kind": "INDEPENDENT_REVIEW",
                "reviewer": "fresh-reviewer",
                "isolation": "FRESH_READ_ONLY",
                "verdict": "BLOCKED",
                "summary": "The final review Skill could not be activated.",
                "skillUsage": _skill_usage(
                    "APPLIED",
                    "This incorrectly claims the unavailable Skill was applied.",
                ),
            }
            with self.assertRaises(GatedLoopError) as raised:
                record_acceptance(
                    root=temporary,
                    item_id=task_id,
                    action="REVIEW_BLOCKED",
                    evidence=artifact,
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_ACCEPTANCE_EVIDENCE_INVALID",
            )

    def test_review_blocked_rejects_controller_placeholder_reasons(
        self,
    ) -> None:
        for placeholder_field in ("summary", "skillUsage"):
            with (
                self.subTest(placeholder_field=placeholder_field),
                tempfile.TemporaryDirectory() as temporary,
            ):
                task_id, _ = self._ready_for_review(temporary)
                artifact = {
                    "schemaVersion": 3,
                    "kind": "INDEPENDENT_REVIEW",
                    "reviewer": "fresh-reviewer",
                    "isolation": "FRESH_READ_ONLY",
                    "verdict": "BLOCKED",
                    "summary": (
                        "<CONCRETE_UNAVAILABILITY_REASON>"
                        if placeholder_field == "summary"
                        else "The required Skill is unavailable in this host."
                    ),
                    "skillUsage": _skill_usage(
                        "BLOCKED",
                        (
                            "<CONCRETE_UNAVAILABILITY_REASON>"
                            if placeholder_field == "skillUsage"
                            else (
                                "Host Skill discovery did not return the "
                                "frozen canonical Skill name."
                            )
                        ),
                    ),
                }
                with self.assertRaises(GatedLoopError) as raised:
                    record_acceptance(
                        root=temporary,
                        item_id=task_id,
                        action="REVIEW_BLOCKED",
                        evidence=artifact,
                    )
                self.assertEqual(
                    raised.exception.code,
                    "WORK_ITEM_ACCEPTANCE_EVIDENCE_INVALID",
                )

    def test_mcp_routes_review_blocked_through_the_shared_operation(
        self,
    ) -> None:
        artifact = {
            "schemaVersion": 3,
            "kind": "INDEPENDENT_REVIEW",
            "reviewer": "fresh-reviewer",
            "isolation": "FRESH_READ_ONLY",
            "verdict": "BLOCKED",
            "summary": "The required Skill is unavailable in this host.",
            "skillUsage": _skill_usage(
                "BLOCKED",
                "Host Skill discovery did not return the frozen canonical Skill name.",
            ),
        }
        tool = next(
            item
            for item in mcp_tools.tool_definitions()
            if item["name"] == "record_independent_review_blocked"
        )
        self.assertEqual(
            set(tool["inputSchema"]["properties"]),
            {"item_id", "evidence"},
        )
        with (
            patch.object(
                operations,
                "record_acceptance",
                return_value={"action": "REVIEW_BLOCKED"},
            ) as record,
            patch.object(
                operations,
                "get_graph_frontier",
                return_value={"responseMode": "COMPACT"},
            ),
        ):
            operation_result = operations.execute_operation(
                "record_independent_review_blocked",
                {"item_id": "root-one", "evidence": artifact},
                context=operations.OperationContext(root="C:/fixed-project"),
            )
        self.assertEqual(operation_result["action"], "REVIEW_BLOCKED")
        self.assertEqual(
            operation_result["nextFrontier"],
            {"responseMode": "COMPACT"},
        )
        self.assertEqual(record.call_args.kwargs["action"], "REVIEW_BLOCKED")


if __name__ == "__main__":
    unittest.main()
