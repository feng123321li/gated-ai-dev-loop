from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from hdg.acceptance import accept_work_item, record_acceptance
from hdg.errors import GatedLoopError
from hdg.execution import dispatch_task, record_task_result
from hdg.graph_runtime import get_evidence_contract, get_graph_frontier
from hdg.model import (
    render_development_plan,
    render_work_item_baseline,
    validate_hierarchy_definition,
    validate_work_item_definition,
    work_item_baseline_fingerprint,
)
from hdg.planning import freeze_hierarchy, prepare_hierarchy

from .fixtures import capability_hierarchy, task_definition, task_hierarchy
from .skill_helpers import (
    activate_required_skills,
    conform_required_skills,
)


REQUIRED_SKILLS = [
    {
        "name": "tdd-workflow",
        "stages": ["DEVELOPMENT", "GATE"],
        "purpose": "Use the complete red-green-refactor workflow and verify its tests at the gate.",
    },
    {
        "name": "source-command-python-review",
        "stages": ["FINAL_REVIEW"],
        "purpose": "Run the complete independent Python review workflow before user confirmation.",
    },
]


def _skill_usage(name: str, stage: str, evidence: str) -> dict[str, str]:
    return {
        "name": name,
        "stage": stage,
        "status": "APPLIED",
        "evidence": evidence,
    }


def _task_result(task_id: str, operation_id: str) -> dict:
    return {
        "schemaVersion": 3,
        "kind": "TASK_RESULT",
        "taskId": task_id,
        "operationId": operation_id,
        "status": "IMPLEMENTED",
        "summary": "Implemented the frozen controller and verified its regression command.",
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
        "summary": "The internal gate verified the frozen acceptance contract.",
        "scope": {
            "changedFiles": ["src/controller.py", "tests/test_controller.py"],
            "outOfScopeFiles": [],
        },
        "acceptance": [{
            "id": "A-001",
            "requirementIds": ["R-001"],
            "status": "PASS",
            "evidence": "The frozen Python command completed successfully.",
        }],
        "tests": [{
            "argv": ["python", "-m", "unittest", "tests.test_controller"],
            "exitCode": 0,
            "testsRun": 1,
            "summary": "The frozen unittest command passed.",
        }],
        "findings": {"p0": [], "p1": [], "p2": []},
    }


class RequiredSkillContractTests(unittest.TestCase):
    def test_required_skills_are_strict_frozen_baseline_fields(self) -> None:
        source = task_definition(requiredSkills=REQUIRED_SKILLS)
        definition = validate_work_item_definition(source)

        self.assertEqual(
            definition["requiredSkills"],
            sorted(REQUIRED_SKILLS, key=lambda item: item["name"]),
        )
        baseline = render_work_item_baseline(definition)
        plan = render_development_plan(
            definition,
            {
                "baselineFingerprint": work_item_baseline_fingerprint(definition),
                "review": {"status": "WAITING_FOR_HUMAN_REVIEW"},
            },
        )
        self.assertIn("Required Skills", baseline)
        self.assertIn("tdd-workflow", baseline)
        self.assertIn("必须使用的 Skills", plan)
        self.assertIn("FINAL_REVIEW", plan)

        changed = deepcopy(source)
        changed["requiredSkills"][0]["purpose"] = (
            "Use the complete TDD workflow and preserve its detailed evidence."
        )
        self.assertNotEqual(
            work_item_baseline_fingerprint(definition),
            work_item_baseline_fingerprint(
                validate_work_item_definition(changed)
            ),
        )

    def test_required_skill_names_and_stages_are_portable_and_strict(self) -> None:
        missing = task_definition()
        del missing["requiredSkills"]
        normalized = validate_work_item_definition(missing)
        self.assertEqual(normalized["requiredSkills"], [])
        self.assertEqual(
            normalized,
            validate_work_item_definition(task_definition(requiredSkills=[])),
        )

        invalid_name = task_definition(requiredSkills=[{
            "name": "/tdd-workflow",
            "stages": ["DEVELOPMENT"],
            "purpose": "Use the complete TDD workflow.",
        }])
        with self.assertRaises(GatedLoopError) as raised:
            validate_work_item_definition(invalid_name)
        self.assertEqual(
            raised.exception.code,
            "WORK_ITEM_REQUIRED_SKILL_INVALID",
        )

        duplicate = task_definition(requiredSkills=[
            {
                "name": "tdd-workflow",
                "stages": ["DEVELOPMENT"],
                "purpose": "Apply the complete TDD workflow.",
            },
            {
                "name": "tdd-workflow",
                "stages": ["GATE"],
                "purpose": "Audit the complete TDD workflow.",
            },
        ])
        with self.assertRaises(GatedLoopError) as raised:
            validate_work_item_definition(duplicate)
        self.assertEqual(
            raised.exception.code,
            "WORK_ITEM_REQUIRED_SKILL_INVALID",
        )

        invalid_stage = task_definition(requiredSkills=[{
            "name": "tdd-workflow",
            "stages": ["USER_CONFIRMATION"],
            "purpose": "Use the complete TDD workflow.",
        }])
        with self.assertRaises(GatedLoopError) as raised:
            validate_work_item_definition(invalid_stage)
        self.assertEqual(
            raised.exception.code,
            "WORK_ITEM_REQUIRED_SKILL_INVALID",
        )

    def test_hierarchy_without_required_skills_normalizes_every_node_to_empty(self) -> None:
        hierarchy = capability_hierarchy()
        del hierarchy["root"]["definition"]["requiredSkills"]
        del hierarchy["root"]["children"][0]["definition"]["requiredSkills"]

        normalized = validate_hierarchy_definition(hierarchy)

        self.assertEqual(normalized["root"]["definition"]["requiredSkills"], [])
        self.assertEqual(
            normalized["root"]["children"][0]["definition"]["requiredSkills"],
            [],
        )

    def test_required_skills_flow_through_execution_gate_and_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(requiredSkills=REQUIRED_SKILLS),
                host_runtime="codex",
            )
            task_id = prepared["rootId"]
            freeze_hierarchy(
                root=temporary,
                root_id=task_id,
                expected_hierarchy_fingerprint=prepared[
                    "hierarchyFingerprint"
                ],
                development_mode="active",
                confirmed=True,
            )

            frontier = get_graph_frontier(
                root=temporary,
                work_item_id=task_id,
            )
            dispatch_action = next(
                action
                for action in frontier["actions"]
                if action["action"] == "DISPATCH_TASK"
            )
            self.assertEqual(
                [item["name"] for item in dispatch_action["requiredSkills"]],
                ["tdd-workflow"],
            )
            self.assertEqual(
                dispatch_action["requiredSkills"][0]["declaredBy"],
                [task_id],
            )

            development_receipts = activate_required_skills(
                temporary,
                task_id,
                "DEVELOPMENT",
                execution_id="op-required-skills",
                executor_id="developer",
            )
            context = dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer",
                operation_id="op-required-skills",
            )
            self.assertEqual(
                [item["stage"] for item in context["requiredSkills"]],
                ["DEVELOPMENT", "GATE", "FINAL_REVIEW"],
            )
            self.assertEqual(
                context["requiredSkillPolicy"]["activation"],
                "EXPLICIT_NATIVE_SKILL_INVOCATION_REQUIRED",
            )

            result_contract = get_evidence_contract(
                root=temporary,
                work_item_id=task_id,
                contract_kind="result",
            )["evidenceContract"]
            self.assertEqual(
                result_contract["artifactTemplates"]["IMPLEMENTED"][
                    "skillUsage"
                ],
                [{
                    "name": "tdd-workflow",
                    "stage": "DEVELOPMENT",
                    "status": "APPLIED",
                    "evidence": "<CONCRETE_APPLICATION_EVIDENCE>",
                }],
            )

            missing_skill_usage = _task_result(
                task_id,
                "op-required-skills",
            )
            with self.assertRaises(GatedLoopError) as raised:
                record_task_result(
                    root=temporary,
                    item_id=task_id,
                    operation_id="op-required-skills",
                    status="IMPLEMENTED",
                    evidence=missing_skill_usage,
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_RESULT_EVIDENCE_INVALID",
            )

            result = deepcopy(missing_skill_usage)
            result["skillUsage"] = [_skill_usage(
                "tdd-workflow",
                "DEVELOPMENT",
                "Applied red-green-refactor and used the frozen unittest as the regression checkpoint.",
            )]
            conform_required_skills(
                temporary,
                task_id,
                development_receipts,
            )
            record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-required-skills",
                status="IMPLEMENTED",
                evidence=result,
            )

            frontier = get_graph_frontier(
                root=temporary,
                work_item_id=task_id,
            )
            gate_action = next(
                action
                for action in frontier["actions"]
                if action["action"] == "RUN_GATE"
            )
            self.assertEqual(
                [item["name"] for item in gate_action["requiredSkills"]],
                ["tdd-workflow"],
            )

            gate_contract = get_evidence_contract(
                root=temporary,
                work_item_id=task_id,
                contract_kind="gate",
            )["evidenceContract"]
            self.assertEqual(
                gate_contract["artifactTemplate"]["skillUsage"][0]["stage"],
                "GATE",
            )

            missing_gate_usage = _gate(
                task_id,
                prepared["baselineFingerprints"][task_id],
            )
            gate_receipts = activate_required_skills(
                temporary,
                task_id,
                "GATE",
                execution_id="gate-required-skills",
                executor_id="gate-reviewer",
            )
            conform_required_skills(
                temporary,
                task_id,
                gate_receipts,
            )
            with self.assertRaises(GatedLoopError) as raised:
                accept_work_item(
                    root=temporary,
                    item_id=task_id,
                    evidence=missing_gate_usage,
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_GATE_EVIDENCE_INVALID",
            )

            gate = deepcopy(missing_gate_usage)
            gate["skillUsage"] = [_skill_usage(
                "tdd-workflow",
                "GATE",
                "Applied the Skill gate checklist to scope, frozen tests, acceptance trace, and P0/P1 findings.",
            )]
            accepted = accept_work_item(
                root=temporary,
                item_id=task_id,
                evidence=gate,
            )
            report = Path(
                temporary,
                accepted["acceptanceReport"]["markdownPath"],
            ).read_text(encoding="utf-8")
            self.assertIn("## 实际开发 Skill 调用", report)
            self.assertIn("op-required-skills", report)
            self.assertIn(
                "Applied red-green-refactor and used the frozen unittest as the regression checkpoint.",
                report,
            )
            self.assertIn("## Skill 使用审计", report)
            self.assertIn("tdd-workflow", report)
            self.assertIn("APPLIED", report)
            self.assertIn("acceptance trace", report)

            frontier = get_graph_frontier(
                root=temporary,
                work_item_id=task_id,
            )
            review_action = next(
                action
                for action in frontier["actions"]
                if action["action"] == "REQUEST_REVIEW"
            )
            self.assertEqual(
                [item["name"] for item in review_action["requiredSkills"]],
                ["source-command-python-review"],
            )
            review_contract = get_evidence_contract(
                root=temporary,
                work_item_id=task_id,
                contract_kind="review",
            )["evidenceContract"]
            self.assertEqual(
                review_contract["actionOptions"][
                    "INDEPENDENT_REVIEW_PASS"
                ]["skillUsage"][0]["stage"],
                "FINAL_REVIEW",
            )

            missing_review_usage = {
                "schemaVersion": 3,
                "kind": "INDEPENDENT_REVIEW",
                "reviewer": "fresh-reviewer",
                "isolation": "FRESH_READ_ONLY",
                "verdict": "PASS",
                "findings": {"p0": 0, "p1": 0},
            }
            review_receipts = activate_required_skills(
                temporary,
                task_id,
                "FINAL_REVIEW",
                execution_id="review-required-skills",
                executor_id="fresh-reviewer",
            )
            conform_required_skills(
                temporary,
                task_id,
                review_receipts,
            )
            with self.assertRaises(GatedLoopError) as raised:
                record_acceptance(
                    root=temporary,
                    item_id=task_id,
                    action="INDEPENDENT_REVIEW_PASS",
                    evidence=missing_review_usage,
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_ACCEPTANCE_EVIDENCE_INVALID",
            )

            review = deepcopy(missing_review_usage)
            review["skillUsage"] = [_skill_usage(
                "source-command-python-review",
                "FINAL_REVIEW",
                "Applied the complete fresh read-only Python review and found no P0 or P1 issues.",
            )]
            reviewed = record_acceptance(
                root=temporary,
                item_id=task_id,
                action="INDEPENDENT_REVIEW_PASS",
                evidence=review,
            )
            self.assertEqual(
                reviewed["acceptance"]["status"],
                "WAITING_FOR_USER_CONFIRMATION",
            )

    def test_controller_skill_evidence_placeholders_are_rejected(self) -> None:
        placeholder = "<CONCRETE_APPLICATION_EVIDENCE>"
        for stage, expected_code in (
            ("DEVELOPMENT", "WORK_ITEM_RESULT_EVIDENCE_INVALID"),
            ("GATE", "WORK_ITEM_GATE_EVIDENCE_INVALID"),
            ("FINAL_REVIEW", "WORK_ITEM_ACCEPTANCE_EVIDENCE_INVALID"),
        ):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                prepared = prepare_hierarchy(
                    root=temporary,
                    hierarchy=task_hierarchy(requiredSkills=REQUIRED_SKILLS),
                    host_runtime="codex",
                )
                task_id = prepared["rootId"]
                freeze_hierarchy(
                    root=temporary,
                    root_id=task_id,
                    expected_hierarchy_fingerprint=prepared[
                        "hierarchyFingerprint"
                    ],
                    development_mode="active",
                    confirmed=True,
                )
                operation_id = f"op-placeholder-{stage.casefold()}"
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

                result = _task_result(task_id, operation_id)
                result["skillUsage"] = [_skill_usage(
                    "tdd-workflow",
                    "DEVELOPMENT",
                    (
                        placeholder
                        if stage == "DEVELOPMENT"
                        else (
                            "Applied the complete red-green-refactor workflow "
                            "to the frozen regression command."
                        )
                    ),
                )]
                if stage == "DEVELOPMENT":
                    with self.assertRaises(GatedLoopError) as raised:
                        record_task_result(
                            root=temporary,
                            item_id=task_id,
                            operation_id=operation_id,
                            status="IMPLEMENTED",
                            evidence=result,
                        )
                    self.assertEqual(raised.exception.code, expected_code)
                    continue
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
                    evidence=result,
                )

                gate = _gate(
                    task_id,
                    prepared["baselineFingerprints"][task_id],
                )
                gate["skillUsage"] = [_skill_usage(
                    "tdd-workflow",
                    "GATE",
                    (
                        placeholder
                        if stage == "GATE"
                        else (
                            "Applied the complete gate workflow to scope, "
                            "tests, acceptance trace, and findings."
                        )
                    ),
                )]
                gate_receipts = activate_required_skills(
                    temporary,
                    task_id,
                    "GATE",
                    execution_id=f"gate-placeholder-{stage.casefold()}",
                    executor_id="gate-reviewer",
                )
                conform_required_skills(
                    temporary,
                    task_id,
                    gate_receipts,
                )
                if stage == "GATE":
                    with self.assertRaises(GatedLoopError) as raised:
                        accept_work_item(
                            root=temporary,
                            item_id=task_id,
                            evidence=gate,
                        )
                    self.assertEqual(raised.exception.code, expected_code)
                    continue
                accept_work_item(
                    root=temporary,
                    item_id=task_id,
                    evidence=gate,
                )

                review_receipts = activate_required_skills(
                    temporary,
                    task_id,
                    "FINAL_REVIEW",
                    execution_id="review-placeholder-final",
                    executor_id="fresh-reviewer",
                )
                conform_required_skills(
                    temporary,
                    task_id,
                    review_receipts,
                )
                review = {
                    "schemaVersion": 3,
                    "kind": "INDEPENDENT_REVIEW",
                    "reviewer": "fresh-reviewer",
                    "isolation": "FRESH_READ_ONLY",
                    "verdict": "PASS",
                    "findings": {"p0": 0, "p1": 0},
                    "skillUsage": [_skill_usage(
                        "source-command-python-review",
                        "FINAL_REVIEW",
                        placeholder,
                    )],
                }
                with self.assertRaises(GatedLoopError) as raised:
                    record_acceptance(
                        root=temporary,
                        item_id=task_id,
                        action="INDEPENDENT_REVIEW_PASS",
                        evidence=review,
                    )
                self.assertEqual(raised.exception.code, expected_code)

    def test_root_required_skills_are_inherited_by_descendant_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hierarchy = capability_hierarchy()
            hierarchy["root"]["definition"]["requiredSkills"] = [{
                "name": "tdd-workflow",
                "stages": ["DEVELOPMENT"],
                "purpose": "Apply the complete TDD workflow to every child Task.",
            }]
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=hierarchy,
                host_runtime="codex",
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

            frontier = get_graph_frontier(
                root=temporary,
                work_item_id=prepared["rootId"],
            )
            action = next(
                item
                for item in frontier["actions"]
                if item["action"] == "DISPATCH_TASK"
            )
            self.assertEqual(
                action["requiredSkills"],
                [{
                    "name": "tdd-workflow",
                    "stage": "DEVELOPMENT",
                    "declaredBy": ["c-python-runtime"],
                    "purposes": [
                        "Apply the complete TDD workflow to every child Task."
                    ],
                }],
            )

    def test_final_review_skills_must_be_declared_on_the_root(self) -> None:
        hierarchy = capability_hierarchy()
        hierarchy["root"]["children"][0]["definition"][
            "requiredSkills"
        ] = [{
            "name": "source-command-python-review",
            "stages": ["FINAL_REVIEW"],
            "purpose": "Apply the complete final Python review workflow.",
        }]

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(GatedLoopError) as raised:
                prepare_hierarchy(
                    root=temporary,
                    hierarchy=hierarchy,
                    host_runtime="codex",
                )
        self.assertEqual(
            raised.exception.code,
            "WORK_ITEM_REQUIRED_SKILL_INVALID",
        )

    def test_final_review_skill_cannot_be_bypassed_by_human_review_record(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(requiredSkills=REQUIRED_SKILLS),
                host_runtime="codex",
            )
            task_id = prepared["rootId"]
            freeze_hierarchy(
                root=temporary,
                root_id=task_id,
                expected_hierarchy_fingerprint=prepared[
                    "hierarchyFingerprint"
                ],
                development_mode="active",
                confirmed=True,
            )
            development_receipts = activate_required_skills(
                temporary,
                task_id,
                "DEVELOPMENT",
                execution_id="op-human-bypass",
                executor_id="developer",
            )
            dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer",
                operation_id="op-human-bypass",
            )
            result = _task_result(task_id, "op-human-bypass")
            result["skillUsage"] = [_skill_usage(
                "tdd-workflow",
                "DEVELOPMENT",
                "Applied the complete TDD workflow and recorded the regression checkpoint.",
            )]
            conform_required_skills(
                temporary,
                task_id,
                development_receipts,
            )
            record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-human-bypass",
                status="IMPLEMENTED",
                evidence=result,
            )
            gate = _gate(
                task_id,
                prepared["baselineFingerprints"][task_id],
            )
            gate["skillUsage"] = [_skill_usage(
                "tdd-workflow",
                "GATE",
                "Applied the complete gate checklist to the frozen scope, tests, and acceptance evidence.",
            )]
            gate_receipts = activate_required_skills(
                temporary,
                task_id,
                "GATE",
                execution_id="gate-human-bypass",
                executor_id="gate-reviewer",
            )
            conform_required_skills(
                temporary,
                task_id,
                gate_receipts,
            )
            accept_work_item(
                root=temporary,
                item_id=task_id,
                evidence=gate,
            )

            with self.assertRaises(GatedLoopError) as raised:
                record_acceptance(
                    root=temporary,
                    item_id=task_id,
                    action="HUMAN_REVIEW_ACCEPTED",
                    evidence={
                        "schemaVersion": 3,
                        "kind": "HUMAN_REVIEW",
                        "reviewer": "user",
                        "verdict": "ACCEPTED",
                    },
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_REQUIRED_SKILL_NOT_APPLIED",
            )


if __name__ == "__main__":
    unittest.main()
