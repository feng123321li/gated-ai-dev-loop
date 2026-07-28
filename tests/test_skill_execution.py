from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from hdg.acceptance import accept_work_item
from hdg.constants import SCHEMA_VERSION
from hdg.errors import GatedLoopError
from hdg.execution import dispatch_task, record_task_result
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.skill_execution import (
    is_skill_lifecycle_event_valid,
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
NATIVE_MECHANISM = "HOST_NATIVE_SKILL"


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
    def test_planning_and_execution_hosts_are_independent(self) -> None:
        combinations = (
            ("claude-code", "claude-code"),
            ("claude-code", "codex"),
            ("codex", "cursor"),
            ("cursor", "windsurf"),
        )
        for planning_host, execution_host in combinations:
            with self.subTest(
                planning_host=planning_host,
                execution_host=execution_host,
            ), tempfile.TemporaryDirectory() as root:
                item_id, _ = _prepare(root, planning_host)
                operation_id = (
                    f"op-{planning_host.replace('-', '_')}-to-"
                    f"{execution_host.replace('-', '_')}"
                )

                receipt = record_skill_activation(
                    root=root,
                    item_id=item_id,
                    stage="DEVELOPMENT",
                    skill_name="tdd-workflow",
                    activation=_activation(
                        mechanism=NATIVE_MECHANISM,
                        execution_id=operation_id,
                        native_invocation_id=f"native-{operation_id}",
                    ),
                    execution_host_runtime=execution_host,
                )

                self.assertEqual(receipt["hostRuntime"], execution_host)
                self.assertEqual(receipt["mechanism"], NATIVE_MECHANISM)
                dispatched = dispatch_task(
                    root=root,
                    item_id=item_id,
                    owner="developer",
                    operation_id=operation_id,
                )
                self.assertEqual(dispatched["status"], "CLAIMED")
                self.assertEqual(
                    dispatched["requiredSkillPolicy"]["hostBinding"],
                    "CURRENT_STAGE_EXECUTION_HOST",
                )
                self.assertEqual(
                    dispatched["requiredSkillPolicy"]["planningHost"],
                    "AUDIT_ONLY_NOT_EXECUTION_CONSTRAINT",
                )
                self.assertEqual(
                    dispatched["requiredSkillPolicy"]["mechanism"],
                    NATIVE_MECHANISM,
                )

    def test_new_activation_rejects_a_host_specific_legacy_mechanism(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _prepare(root, "codex")

            with self.assertRaises(GatedLoopError) as raised:
                record_skill_activation(
                    root=root,
                    item_id=item_id,
                    stage="DEVELOPMENT",
                    skill_name="tdd-workflow",
                    activation=_activation(
                        mechanism="CODEX_EXPLICIT_SKILL",
                        execution_id="op-cursor-after-codex-plan",
                    ),
                    execution_host_runtime="cursor",
                )

            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_SKILL_ACTIVATION_INVALID",
            )
            self.assertEqual(
                raised.exception.details["expectedMechanism"],
                NATIVE_MECHANISM,
            )

    def test_existing_schema_v3_host_specific_receipts_remain_valid(
        self,
    ) -> None:
        pairs = (
            ("claude-code", "CLAUDE_SKILL_TOOL"),
            ("codex", "CODEX_EXPLICIT_SKILL"),
        )
        for host_runtime, mechanism in pairs:
            with self.subTest(
                host_runtime=host_runtime,
                mechanism=mechanism,
            ):
                payload = {
                    "schemaVersion": SCHEMA_VERSION,
                    "kind": "SKILL_ACTIVATION",
                    "workItemId": "t-example",
                    "skillName": "tdd-workflow",
                    "stage": "DEVELOPMENT",
                    "hostRuntime": host_runtime,
                    **_activation(
                        mechanism=mechanism,
                        execution_id="operation-existing",
                    ),
                }
                self.assertTrue(is_skill_lifecycle_event_valid({
                    "eventType": "SKILL_ACTIVATED",
                    "nodeId": "execute:t-example",
                    "attempt": 1,
                    "operationId": "operation-existing",
                    "payload": payload,
                }))
                payload["hostRuntime"] = "cursor"
                self.assertFalse(is_skill_lifecycle_event_valid({
                    "eventType": "SKILL_ACTIVATED",
                    "nodeId": "execute:t-example",
                    "attempt": 1,
                    "operationId": "operation-existing",
                    "payload": payload,
                }))

    def test_direct_lifecycle_api_requires_the_current_execution_host(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _prepare(root, "codex")
            with self.assertRaises(GatedLoopError) as raised:
                record_skill_activation(
                    root=root,
                    item_id=item_id,
                    stage="DEVELOPMENT",
                    skill_name="tdd-workflow",
                    activation=_activation(
                        mechanism=NATIVE_MECHANISM,
                        execution_id="operation-without-host",
                    ),
                )
            self.assertEqual(
                raised.exception.code,
                "HOST_RUNTIME_REQUIRED",
            )

        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _prepare(root, "codex")
            receipt = record_skill_activation(
                root=root,
                item_id=item_id,
                stage="DEVELOPMENT",
                skill_name="tdd-workflow",
                activation=_activation(
                    mechanism=NATIVE_MECHANISM,
                    execution_id="operation-conformance-without-host",
                ),
                execution_host_runtime="cursor",
            )
            dispatch_task(
                root=root,
                item_id=item_id,
                owner="developer",
                operation_id="operation-conformance-without-host",
            )
            with self.assertRaises(GatedLoopError) as raised:
                record_skill_conformance(
                    root=root,
                    item_id=item_id,
                    activation_receipt_id=receipt["activationReceiptId"],
                    conformance=_conformance(
                        "Checked the current output without a host identity.",
                    ),
                )
            self.assertEqual(
                raised.exception.code,
                "HOST_RUNTIME_REQUIRED",
            )

    def test_conformance_must_use_the_activation_execution_host(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _prepare(root, "codex")
            receipt = record_skill_activation(
                root=root,
                item_id=item_id,
                stage="DEVELOPMENT",
                skill_name="tdd-workflow",
                activation=_activation(
                    mechanism=NATIVE_MECHANISM,
                    execution_id="op-claude-conformance",
                ),
                execution_host_runtime="claude-code",
            )
            dispatch_task(
                root=root,
                item_id=item_id,
                owner="developer",
                operation_id="op-claude-conformance",
            )

            with self.assertRaises(GatedLoopError) as raised:
                record_skill_conformance(
                    root=root,
                    item_id=item_id,
                    activation_receipt_id=receipt["activationReceiptId"],
                    conformance=_conformance(
                        "The current executor checked the complete workflow.",
                    ),
                    execution_host_runtime="codex",
                )

            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_SKILL_CONFORMANCE_HOST_MISMATCH",
            )

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
                        mechanism=NATIVE_MECHANISM,
                        execution_id="op-erp-generator",
                    ),
                    execution_host_runtime="codex",
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
                    mechanism=NATIVE_MECHANISM,
                    execution_id="op-erp-generator",
                    native_invocation_id="agent-erp-generator-turn",
                ),
                execution_host_runtime="codex",
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
                mechanism=NATIVE_MECHANISM,
                execution_id="op-two-skills",
                native_invocation_id="agent-shared-turn",
            )
            record_skill_activation(
                root=root,
                item_id=item_id,
                stage="DEVELOPMENT",
                skill_name="erp-dubbo-api-generator",
                activation=activation,
                execution_host_runtime="codex",
            )
            with self.assertRaises(GatedLoopError) as raised:
                record_skill_activation(
                    root=root,
                    item_id=item_id,
                    stage="DEVELOPMENT",
                    skill_name="java-coding-standards",
                    activation=activation,
                    execution_host_runtime="codex",
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_SKILL_ACTIVATION_REUSED",
            )

    def test_any_agent_automatically_records_native_activation_before_dispatch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _prepare(root, "cursor")

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
            self.assertEqual(
                raised.exception.details["authorizationSource"],
                "FROZEN_REQUIRED_SKILLS",
            )
            self.assertFalse(
                raised.exception.details["userActionRequired"],
            )
            self.assertEqual(
                raised.exception.details["recoveryAction"],
                "EXECUTION_ADAPTER_AUTO_INVOKE",
            )
            self.assertNotIn(
                "ask the user",
                str(raised.exception).casefold(),
            )

            with self.assertRaises(GatedLoopError) as raised:
                record_skill_activation(
                    root=root,
                    item_id=item_id,
                    stage="DEVELOPMENT",
                    skill_name="tdd-workflow",
                    activation=_activation(
                        mechanism="SKILL_FILE_LOAD",
                        execution_id="op-cursor-native",
                    ),
                    execution_host_runtime="cursor",
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
                    mechanism=NATIVE_MECHANISM,
                    execution_id="op-cursor-native",
                    native_invocation_id="cursor-turn-1",
                ),
                execution_host_runtime="cursor",
            )
            self.assertEqual(receipt["hostRuntime"], "cursor")
            self.assertEqual(receipt["mechanism"], NATIVE_MECHANISM)
            self.assertEqual(len(receipt["activationReceiptId"]), 64)

            context = dispatch_task(
                root=root,
                item_id=item_id,
                owner="developer",
                operation_id="op-cursor-native",
            )
            self.assertEqual(context["status"], "CLAIMED")
            self.assertEqual(
                context["requiredSkillPolicy"]["activation"],
                "CURRENT_EXECUTOR_NATIVE_SKILL_INVOCATION_REQUIRED",
            )
            self.assertEqual(
                context["requiredSkillPolicy"]["authorization"],
                "FROZEN_REQUIRED_SKILLS",
            )
            self.assertEqual(
                context["requiredSkillPolicy"]["invocation"],
                "EXECUTION_ADAPTER_AUTOMATIC",
            )
            self.assertEqual(
                context["requiredSkillPolicy"]["repeatUserPrompt"],
                "FORBIDDEN_AFTER_FREEZE",
            )
            self.assertEqual(
                context["requiredSkillPolicy"]["loadingOnly"],
                "REJECTED",
            )

    def test_host_specific_legacy_mechanisms_cannot_create_new_receipts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _prepare(root, "claude-code")

            for invalid_mechanism in (
                "SKILL_FILE_LOAD",
                "CODEX_EXPLICIT_SKILL",
                "CLAUDE_SKILL_TOOL",
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
                            execution_host_runtime="claude-code",
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
                    mechanism=NATIVE_MECHANISM,
                    execution_id="op-claude-native",
                    native_invocation_id="toolu-skill-1",
                ),
                execution_host_runtime="claude-code",
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
                    mechanism=NATIVE_MECHANISM,
                    execution_id="op-conformance",
                    native_invocation_id="agent-turn-1",
                ),
                execution_host_runtime="codex",
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
                execution_host_runtime="codex",
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
                    mechanism=NATIVE_MECHANISM,
                    execution_id="gate-conformance",
                    executor_id="gate-reviewer",
                    native_invocation_id="agent-gate-turn-1",
                ),
                execution_host_runtime="codex",
            )
            record_skill_conformance(
                root=root,
                item_id=item_id,
                activation_receipt_id=gate_receipt["activationReceiptId"],
                conformance=_conformance(
                    "The gate executor verified the Skill completion criteria against the actual artifact.",
                ),
                execution_host_runtime="codex",
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
            self.assertIn(NATIVE_MECHANISM, report)
            self.assertIn("agent-turn-1", report)
            self.assertIn("agent-gate-turn-1", report)
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
