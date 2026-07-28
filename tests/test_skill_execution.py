from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from hdg.acceptance import accept_work_item
from hdg.errors import GatedLoopError
from hdg.execution import dispatch_task, record_task_result
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.skill_execution import (
    record_skill_activation,
    record_skill_conformance,
)

from .fixtures import task_hierarchy
from .test_required_skills import _gate, _skill_usage, _task_result


REQUIRED_SKILLS = [{
    "name": "tdd-workflow",
    "stages": ["DEVELOPMENT", "GATE"],
    "purpose": "Execute red-green-refactor and verify the same workflow at the gate.",
}]


def _prepare(
    root: str,
    host_runtime: str,
    required_skills: list[dict] | None = None,
) -> tuple[str, str]:
    prepared = prepare_hierarchy(
        root=root,
        hierarchy=task_hierarchy(
            requiredSkills=required_skills or REQUIRED_SKILLS
        ),
        host_runtime=host_runtime,
    )
    item_id = prepared["rootId"]
    freeze_hierarchy(
        root=root,
        root_id=item_id,
        expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
        development_mode="active",
        confirmed=True,
    )
    return item_id, prepared["baselineFingerprints"][item_id]


def _activation(
    *,
    mechanism: str,
    execution_id: str,
    executor_id: str = "developer",
    native_invocation_id: str = "native-invocation-1",
) -> dict:
    return {
        "sessionId": "session-1",
        "executorId": executor_id,
        "executionId": execution_id,
        "nativeInvocationId": native_invocation_id,
        "mechanism": mechanism,
        "status": "INVOKED",
        "summary": "Explicitly invoked the required Skill in the current stage executor context.",
    }


def _conformance(summary: str) -> dict:
    return {
        "status": "PASS",
        "summary": summary,
        "checks": [{
            "name": "complete-workflow",
            "status": "PASS",
            "evidence": "Verified the Skill workflow against the actual diff and executed tests.",
        }],
    }


class SkillExecutionContractTests(unittest.TestCase):
    def test_any_frozen_catalog_skill_name_is_enforced_without_whitelist(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _prepare(
                root,
                "codex",
                [{
                    "name": "erp-dubbo-api-generator",
                    "stages": ["DEVELOPMENT"],
                    "purpose": (
                        "Apply the complete project API generation contract."
                    ),
                }],
            )
            with self.assertRaises(GatedLoopError) as raised:
                record_skill_activation(
                    root=root,
                    item_id=item_id,
                    stage="DEVELOPMENT",
                    skill_name="tdd-workflow",
                    activation=_activation(
                        mechanism="CODEX_EXPLICIT_SKILL",
                        execution_id="op-erp-generator",
                    ),
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_SKILL_ACTIVATION_INVALID",
            )

            record_skill_activation(
                root=root,
                item_id=item_id,
                stage="DEVELOPMENT",
                skill_name="erp-dubbo-api-generator",
                activation=_activation(
                    mechanism="CODEX_EXPLICIT_SKILL",
                    execution_id="op-erp-generator",
                    native_invocation_id="codex-erp-generator-turn",
                ),
            )
            dispatched = dispatch_task(
                root=root,
                item_id=item_id,
                owner="developer",
                operation_id="op-erp-generator",
            )
            self.assertEqual(dispatched["status"], "CLAIMED")

    def test_one_native_invocation_cannot_cover_two_required_skills(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _prepare(
                root,
                "codex",
                [
                    {
                        "name": "erp-dubbo-api-generator",
                        "stages": ["DEVELOPMENT"],
                        "purpose": "Apply the complete generator workflow.",
                    },
                    {
                        "name": "java-coding-standards",
                        "stages": ["DEVELOPMENT"],
                        "purpose": "Apply the complete Java standards.",
                    },
                ],
            )
            activation = _activation(
                mechanism="CODEX_EXPLICIT_SKILL",
                execution_id="op-two-skills",
                native_invocation_id="codex-shared-turn",
            )
            record_skill_activation(
                root=root,
                item_id=item_id,
                stage="DEVELOPMENT",
                skill_name="erp-dubbo-api-generator",
                activation=activation,
            )
            with self.assertRaises(GatedLoopError) as raised:
                record_skill_activation(
                    root=root,
                    item_id=item_id,
                    stage="DEVELOPMENT",
                    skill_name="java-coding-standards",
                    activation=activation,
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_SKILL_ACTIVATION_REUSED",
            )

    def test_codex_requires_explicit_native_activation_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _prepare(root, "codex")

            with self.assertRaises(GatedLoopError) as raised:
                dispatch_task(
                    root=root,
                    item_id=item_id,
                    owner="developer",
                    operation_id="op-codex-native",
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_REQUIRED_SKILL_ACTIVATION_MISSING",
            )

            with self.assertRaises(GatedLoopError) as raised:
                record_skill_activation(
                    root=root,
                    item_id=item_id,
                    stage="DEVELOPMENT",
                    skill_name="tdd-workflow",
                    activation=_activation(
                        mechanism="SKILL_FILE_LOAD",
                        execution_id="op-codex-native",
                    ),
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_SKILL_ACTIVATION_INVALID",
            )

            receipt = record_skill_activation(
                root=root,
                item_id=item_id,
                stage="DEVELOPMENT",
                skill_name="tdd-workflow",
                activation=_activation(
                    mechanism="CODEX_EXPLICIT_SKILL",
                    execution_id="op-codex-native",
                    native_invocation_id="codex-turn-1",
                ),
            )
            self.assertEqual(receipt["hostRuntime"], "codex")
            self.assertEqual(receipt["mechanism"], "CODEX_EXPLICIT_SKILL")
            self.assertEqual(len(receipt["activationReceiptId"]), 64)

            context = dispatch_task(
                root=root,
                item_id=item_id,
                owner="developer",
                operation_id="op-codex-native",
            )
            self.assertEqual(context["status"], "CLAIMED")
            self.assertEqual(
                context["requiredSkillPolicy"]["activation"],
                "EXPLICIT_NATIVE_SKILL_INVOCATION_REQUIRED",
            )
            self.assertEqual(
                context["requiredSkillPolicy"]["loadingOnly"],
                "REJECTED",
            )

    def test_claude_requires_skill_tool_activation_not_file_read(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _prepare(root, "claude-code")

            for invalid_mechanism in (
                "SKILL_FILE_LOAD",
                "CODEX_EXPLICIT_SKILL",
            ):
                with self.subTest(mechanism=invalid_mechanism):
                    with self.assertRaises(GatedLoopError) as raised:
                        record_skill_activation(
                            root=root,
                            item_id=item_id,
                            stage="DEVELOPMENT",
                            skill_name="tdd-workflow",
                            activation=_activation(
                                mechanism=invalid_mechanism,
                                execution_id="op-claude-native",
                            ),
                        )
                    self.assertEqual(
                        raised.exception.code,
                        "WORK_ITEM_SKILL_ACTIVATION_INVALID",
                    )

            receipt = record_skill_activation(
                root=root,
                item_id=item_id,
                stage="DEVELOPMENT",
                skill_name="tdd-workflow",
                activation=_activation(
                    mechanism="CLAUDE_SKILL_TOOL",
                    execution_id="op-claude-native",
                    native_invocation_id="toolu-skill-1",
                ),
            )
            self.assertEqual(receipt["hostRuntime"], "claude-code")
            dispatch_task(
                root=root,
                item_id=item_id,
                owner="developer",
                operation_id="op-claude-native",
            )

    def test_implemented_and_gate_pass_require_graph_bound_conformance(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, baseline = _prepare(root, "codex")
            development_receipt = record_skill_activation(
                root=root,
                item_id=item_id,
                stage="DEVELOPMENT",
                skill_name="tdd-workflow",
                activation=_activation(
                    mechanism="CODEX_EXPLICIT_SKILL",
                    execution_id="op-conformance",
                    native_invocation_id="codex-turn-1",
                ),
            )
            dispatch_task(
                root=root,
                item_id=item_id,
                owner="developer",
                operation_id="op-conformance",
            )
            result = _task_result(item_id, "op-conformance")
            result["skillUsage"] = [_skill_usage(
                "tdd-workflow",
                "DEVELOPMENT",
                "Applied the full red-green-refactor workflow to the implementation.",
            )]

            with self.assertRaises(GatedLoopError) as raised:
                record_task_result(
                    root=root,
                    item_id=item_id,
                    operation_id="op-conformance",
                    status="IMPLEMENTED",
                    evidence=result,
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_REQUIRED_SKILL_CONFORMANCE_MISSING",
            )

            development_conformance = record_skill_conformance(
                root=root,
                item_id=item_id,
                activation_receipt_id=development_receipt[
                    "activationReceiptId"
                ],
                conformance=_conformance(
                    "The development executor verified red-green-refactor against the actual changes.",
                ),
            )
            self.assertEqual(development_conformance["status"], "PASS")
            record_task_result(
                root=root,
                item_id=item_id,
                operation_id="op-conformance",
                status="IMPLEMENTED",
                evidence=result,
            )

            gate = _gate(item_id, baseline)
            gate["skillUsage"] = [_skill_usage(
                "tdd-workflow",
                "GATE",
                "Applied the complete Skill gate checklist to the real diff and tests.",
            )]
            with self.assertRaises(GatedLoopError) as raised:
                accept_work_item(
                    root=root,
                    item_id=item_id,
                    evidence=gate,
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_REQUIRED_SKILL_ACTIVATION_MISSING",
            )

            gate_receipt = record_skill_activation(
                root=root,
                item_id=item_id,
                stage="GATE",
                skill_name="tdd-workflow",
                activation=_activation(
                    mechanism="CODEX_EXPLICIT_SKILL",
                    execution_id="gate-conformance",
                    executor_id="gate-reviewer",
                    native_invocation_id="codex-gate-turn-1",
                ),
            )
            record_skill_conformance(
                root=root,
                item_id=item_id,
                activation_receipt_id=gate_receipt["activationReceiptId"],
                conformance=_conformance(
                    "The gate executor verified the Skill completion criteria against the actual artifact.",
                ),
            )
            accepted = accept_work_item(
                root=root,
                item_id=item_id,
                evidence=gate,
            )
            report = Path(
                root,
                accepted["acceptanceReport"]["markdownPath"],
            ).read_text(encoding="utf-8")
            self.assertIn("## 实际 Skill 原生调用与符合性", report)
            self.assertIn("CODEX_EXPLICIT_SKILL", report)
            self.assertIn("codex-turn-1", report)
            self.assertIn("codex-gate-turn-1", report)
            self.assertIn("complete-workflow", report)
            self.assertIn("符合性通过", report)

    def test_skill_usage_text_cannot_substitute_for_activation_or_conformance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _prepare(root, "codex")
            result = _task_result(item_id, "op-self-attested")
            result["skillUsage"] = [_skill_usage(
                "tdd-workflow",
                "DEVELOPMENT",
                (
                    "Claimed that the Skill was invoked and all checks passed, "
                    "but supplied no graph-bound native activation receipt."
                ),
            )]

            with self.assertRaises(GatedLoopError) as raised:
                dispatch_task(
                    root=root,
                    item_id=item_id,
                    owner="developer",
                    operation_id="op-self-attested",
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_REQUIRED_SKILL_ACTIVATION_MISSING",
            )

            untouched = deepcopy(result)
            self.assertEqual(untouched["skillUsage"], result["skillUsage"])


if __name__ == "__main__":
    unittest.main()
