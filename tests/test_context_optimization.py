from __future__ import annotations

import tempfile
import unittest

from hdg.acceptance import accept_work_item
from hdg.errors import GatedLoopError
from hdg.execution import dispatch_task
from hdg.graph_runtime import get_evidence_contract
from hdg.mcp_tools import call_tool
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import GovernanceRepository
from hdg.execution import record_task_result

from .fixtures import capability_hierarchy, task_hierarchy


class ContextOptimizationTests(unittest.TestCase):
    @staticmethod
    def _compact_light_task() -> dict[str, object]:
        return {
            "schemaVersion": 3,
            "compactLightTask": {
                "id": "t-compact-light",
                "title": "Compact LIGHT task",
                "goal": "Generate a small client module and verify it.",
                "scope": ["src/client/**", "tests/test_client.py"],
                "requirements": [
                    {
                        "id": "R-001",
                        "text": "The generated client must be importable.",
                    },
                ],
                "acceptance": [
                    {
                        "id": "A-001",
                        "requirementIds": ["R-001"],
                        "expectedResult": "The client import test passes.",
                    },
                ],
                "testCommands": [
                    ["python", "-m", "unittest", "tests.test_client"],
                ],
                "fileChanges": [
                    {
                        "path": "tests/test_client.py",
                        "action": "ADD",
                        "purpose": "Verify generated clients.",
                    },
                ],
                "generatedFileRoots": [
                    {
                        "path": "src/client/**",
                        "purpose": "Contain ADD-only generated client files.",
                    },
                ],
                "logic": ["Generate deterministic client source files."],
                "requiredSkills": [],
            },
        }

    def test_compact_light_input_hydrates_one_canonical_v3_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=self._compact_light_task(),
                host_runtime="codex",
            )

            self.assertEqual(prepared["inputMode"], "COMPACT_LIGHT_TASK")
            repository = GovernanceRepository(temporary)
            registry = repository.read_registry()
            entry = repository.item_by_id(registry, prepared["rootId"])
            definition = repository.read_package(registry, entry)[0]
            self.assertEqual(definition["schemaVersion"], 3)
            self.assertEqual(definition["kind"], "TASK")
            self.assertEqual(definition["gateLevel"], "LIGHT")
            self.assertEqual(
                definition["developmentPlan"]["generatedFileRoots"],
                [{
                    "path": "src/client/**",
                    "purpose": "Contain ADD-only generated client files.",
                }],
            )
            self.assertEqual(
                definition["developmentPlan"]["interfaces"],
                [],
            )

    def test_compact_task_supports_full_and_light_gate_levels(self) -> None:
        for gate_level in ("LIGHT", "FULL"):
            with self.subTest(gate_level=gate_level):
                compact = self._compact_light_task()["compactLightTask"]
                hierarchy = {
                    "schemaVersion": 3,
                    "compactTask": {
                        **compact,
                        "id": f"t-compact-{gate_level.lower()}",
                        "gateLevel": gate_level,
                    },
                }
                with tempfile.TemporaryDirectory() as temporary:
                    prepared = prepare_hierarchy(
                        root=temporary,
                        hierarchy=hierarchy,
                        host_runtime="codex",
                    )
                    repository = GovernanceRepository(temporary)
                    registry = repository.read_registry()
                    entry = repository.item_by_id(
                        registry,
                        prepared["rootId"],
                    )
                    definition = repository.read_package(
                        registry,
                        entry,
                    )[0]

                self.assertEqual(prepared["inputMode"], "COMPACT_TASK")
                self.assertEqual(definition["gateLevel"], gate_level)
                self.assertEqual(definition["schemaVersion"], 3)
                self.assertEqual(definition["kind"], "TASK")

    def test_generated_roots_require_explicit_add_only_file_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=self._compact_light_task(),
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
            dispatch_task(
                root=temporary,
                item_id=prepared["rootId"],
                owner="developer",
                operation_id="op-generated-files",
            )
            result_delta = {
                "summary": "Generated the client and its test.",
                "changedFiles": [
                    "src/client/generated_api.py",
                    "tests/test_client.py",
                ],
                "tests": [{
                    "commandIndex": 0,
                    "exitCode": 0,
                }],
            }
            with self.assertRaises(GatedLoopError) as raised:
                record_task_result(
                    root=temporary,
                    item_id=prepared["rootId"],
                    operation_id="op-generated-files",
                    status="IMPLEMENTED",
                    evidence={"evidenceDelta": result_delta},
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_RESULT_EVIDENCE_INVALID",
            )

            result_delta["generatedFiles"] = [
                "src/client/generated_api.py",
            ]
            record_task_result(
                root=temporary,
                item_id=prepared["rootId"],
                operation_id="op-generated-files",
                status="IMPLEMENTED",
                evidence={"evidenceDelta": result_delta},
            )
            accepted = accept_work_item(
                root=temporary,
                item_id=prepared["rootId"],
                evidence={
                    "evidenceDelta": {
                        "verdict": "PASS",
                        "summary": "Generated files and tests passed.",
                        "changedFiles": [
                            "src/client/generated_api.py",
                            "tests/test_client.py",
                        ],
                        "generatedFiles": [
                            "src/client/generated_api.py",
                        ],
                        "outOfScopeFiles": [],
                        "acceptance": [{
                            "id": "A-001",
                            "status": "PASS",
                            "evidence": "The generated client imported.",
                        }],
                        "tests": [{
                            "commandIndex": 0,
                            "exitCode": 0,
                            "summary": "Client import test passed.",
                        }],
                        "findings": {"p0": [], "p1": [], "p2": []},
                    },
                },
            )
            self.assertEqual(accepted["status"], "VERIFIED")

    def test_dispatch_returns_only_compact_worker_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hierarchy = capability_hierarchy()
            task_id = hierarchy["root"]["children"][0]["definition"]["id"]
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

            dispatched = dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer",
                operation_id="op-compact-context",
            )

            self.assertEqual(dispatched["contextMode"], "COMPACT")
            self.assertLess(
                len(repr(dispatched).encode("utf-8")),
                6000,
            )
            self.assertNotIn("handoffPrompt", dispatched)
            self.assertNotIn("parentContracts", dispatched)
            self.assertNotIn("requiredSkillPolicy", dispatched)
            self.assertNotIn(
                "developmentPlan",
                dispatched["context"],
            )
            self.assertEqual(
                dispatched["context"]["task"]["id"],
                task_id,
            )
            self.assertIn(
                "authorizedFileChanges",
                dispatched["context"],
            )
            self.assertIn(
                "resultEvidenceContractRef",
                dispatched["context"],
            )
            self.assertEqual(
                dispatched["humanArtifacts"]["developmentPlan"],
                (
                    ".layered-delivery/work-items/c-python-runtime/"
                    "children/t-python-controller/development-plan.md"
                ),
            )
            diagnostic = call_tool(
                "task_context",
                {"item_id": task_id},
                root=temporary,
            )
            self.assertEqual(diagnostic["contextMode"], "COMPACT")
            self.assertLess(len(repr(diagnostic).encode("utf-8")), 6000)
            self.assertNotIn("requiredSkillPolicy", diagnostic)
            self.assertEqual(
                dispatched["humanArtifacts"]["developmentHandoff"],
                (
                    ".layered-delivery/work-items/c-python-runtime/"
                    "children/t-python-controller/development-handoff.md"
                ),
            )

    def test_mcp_frontier_is_compact_delta_aware_and_transition_chained(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            frozen = call_tool(
                "freeze_hierarchy",
                {
                    "item_id": prepared["rootId"],
                    "expected_hierarchy_fingerprint": prepared[
                        "hierarchyFingerprint"
                    ],
                    "development_mode": "active",
                },
                root=temporary,
            )

            first = frozen["nextFrontier"]
            self.assertEqual(first["responseMode"], "COMPACT")
            self.assertEqual(
                first["dispatchPlan"]["dispatchTaskIds"],
                [prepared["rootId"]],
            )
            self.assertNotIn("blocked", first)
            self.assertIsInstance(first["frontierRevision"], int)

            unchanged = call_tool(
                "graph_frontier",
                {
                    "item_id": prepared["rootId"],
                    "response_mode": "compact",
                    "since_revision": first["frontierRevision"],
                    "include_blocked_details": False,
                },
                root=temporary,
            )
            self.assertTrue(unchanged["unchanged"])
            self.assertEqual(
                unchanged["frontierRevision"],
                first["frontierRevision"],
            )
            self.assertNotIn("actions", unchanged)

            dispatched = call_tool(
                "dispatch_task",
                {
                    "item_id": prepared["rootId"],
                    "owner": "developer",
                    "operation_id": "op-next-frontier",
                },
                root=temporary,
            )
            self.assertEqual(
                dispatched["nextFrontier"]["summary"]["claimed"],
                1,
            )
            self.assertEqual(
                dispatched["nextFrontier"]["responseMode"],
                "COMPACT",
            )

    def test_delta_evidence_hydrates_and_stores_canonical_v3_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
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
            dispatch_task(
                root=temporary,
                item_id=prepared["rootId"],
                owner="developer",
                operation_id="op-delta-evidence",
            )

            result_contract = get_evidence_contract(
                root=temporary,
                work_item_id=prepared["rootId"],
                contract_kind="result",
            )["evidenceContract"]
            self.assertEqual(result_contract["submissionMode"], "DELTA")
            self.assertNotIn("artifactTemplates", result_contract)
            self.assertLess(len(repr(result_contract)), 3000)

            record_task_result(
                root=temporary,
                item_id=prepared["rootId"],
                operation_id="op-delta-evidence",
                status="IMPLEMENTED",
                evidence={
                    "evidenceDelta": {
                        "summary": "Implemented the frozen controller.",
                        "changedFiles": [
                            "src/controller.py",
                            "tests/test_controller.py",
                        ],
                        "tests": [{
                            "commandIndex": 0,
                            "exitCode": 0,
                            "testsRun": 1,
                        }],
                    },
                },
            )
            repository = GovernanceRepository(temporary)
            registry = repository.read_registry()
            stored_result = repository.item_by_id(
                registry,
                prepared["rootId"],
            )["latestResult"]["artifact"]
            self.assertEqual(stored_result["schemaVersion"], 3)
            self.assertEqual(stored_result["kind"], "TASK_RESULT")
            self.assertEqual(
                stored_result["tests"][0]["argv"],
                ["python", "-m", "unittest", "tests.test_controller"],
            )

            gate_contract = get_evidence_contract(
                root=temporary,
                work_item_id=prepared["rootId"],
                contract_kind="gate",
            )["evidenceContract"]
            self.assertEqual(gate_contract["submissionMode"], "DELTA")
            self.assertNotIn("artifactTemplate", gate_contract)
            self.assertLess(len(repr(gate_contract)), 3500)

            accepted = accept_work_item(
                root=temporary,
                item_id=prepared["rootId"],
                evidence={
                    "evidenceDelta": {
                        "verdict": "PASS",
                        "summary": "All frozen acceptance checks passed.",
                        "changedFiles": [
                            "src/controller.py",
                            "tests/test_controller.py",
                        ],
                        "outOfScopeFiles": [],
                        "acceptance": [{
                            "id": "A-001",
                            "status": "PASS",
                            "evidence": "The controller test passed.",
                        }],
                        "tests": [{
                            "commandIndex": 0,
                            "exitCode": 0,
                            "summary": "The frozen unittest command passed.",
                        }],
                        "findings": {"p0": [], "p1": [], "p2": []},
                    },
                },
            )
            self.assertEqual(accepted["gate"]["artifact"]["schemaVersion"], 3)
            self.assertEqual(
                accepted["gate"]["artifact"]["kind"],
                "WORK_ITEM_GATE",
            )


if __name__ == "__main__":
    unittest.main()
