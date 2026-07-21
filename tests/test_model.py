from __future__ import annotations

from copy import deepcopy
import unittest

from hdg.errors import GatedLoopError
from hdg.evidence import evidence_record, valid_evidence_record, valid_gate_artifact
from hdg.jsonio import fingerprint
from hdg.model import (
    render_development_plan,
    resolve_self_hosting_policy,
    scope_patterns_overlap,
    validate_work_item_definition,
    work_item_baseline_fingerprint,
    work_item_child_contract_fingerprint,
    work_item_contract_fingerprint,
)

from .fixtures import capability_definition, delivery_definition, task_definition


class WorkItemModelTests(unittest.TestCase):
    def test_task_is_normalized_and_fingerprinted(self) -> None:
        definition = validate_work_item_definition(task_definition())
        self.assertEqual(definition["authorityKind"], "EXECUTION")
        self.assertRegex(work_item_baseline_fingerprint(definition), r"^[a-f0-9]{64}$")

    def test_all_hierarchy_shapes_render_distinct_plans(self) -> None:
        for source, heading in (
            (task_definition(), "## 文件改动"),
            (capability_definition(), "## Task 开发内容"),
            (delivery_definition(), "## Capability 开发内容"),
        ):
            definition = validate_work_item_definition(source)
            fingerprint = work_item_baseline_fingerprint(definition)
            plan = render_development_plan(
                definition,
                {
                    "baselineFingerprint": fingerprint,
                    "review": {"status": "WAITING_FOR_HUMAN_REVIEW"},
                },
            )
            self.assertIn(heading, plan)

    def test_development_plan_is_required(self) -> None:
        source = task_definition()
        del source["developmentPlan"]
        with self.assertRaises(GatedLoopError) as raised:
            validate_work_item_definition(source)
        self.assertEqual(raised.exception.code, "WORK_ITEM_DEFINITION_INVALID")

    def test_non_current_schema_is_rejected(self) -> None:
        for version in (1, 2, 999):
            with self.subTest(version=version):
                source = task_definition(schemaVersion=version)
                with self.assertRaises(GatedLoopError) as raised:
                    validate_work_item_definition(source)
                self.assertEqual(raised.exception.code, "WORK_ITEM_SCHEMA_INVALID")

    def test_gate_evidence_rejects_unknown_fields_and_unplanned_files(self) -> None:
        definition = validate_work_item_definition(task_definition())
        entry = {
            "id": definition["id"],
            "baselineFingerprint": work_item_baseline_fingerprint(definition),
        }
        artifact = {
            "schemaVersion": 3,
            "kind": "WORK_ITEM_GATE",
            "workItemId": definition["id"],
            "baselineFingerprint": entry["baselineFingerprint"],
            "verdict": "PASS",
            "summary": "Current v3 evidence.",
            "scope": {
                "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                "outOfScopeFiles": [],
            },
            "acceptance": [{"id": "A-001", "status": "PASS", "evidence": "Verified."}],
            "tests": [{
                "argv": ["python", "-m", "unittest", "tests.test_controller"],
                "exitCode": 0,
                "testsRun": 1,
                "summary": "Passed.",
            }],
            "findings": {"p0": [], "p1": [], "p2": []},
        }
        self.assertTrue(valid_gate_artifact(artifact, entry, definition))
        with_unknown = deepcopy(artifact)
        with_unknown["legacyGate"] = True
        self.assertFalse(valid_gate_artifact(with_unknown, entry, definition))
        outside_plan = deepcopy(artifact)
        outside_plan["scope"]["changedFiles"].append("src/unplanned.py")
        self.assertFalse(valid_gate_artifact(outside_plan, entry, definition))

    def test_evidence_record_is_controller_computed_digest_only(self) -> None:
        artifact = {"schemaVersion": 3, "kind": "USER_CONFIRMATION", "decision": "CONFIRMED"}
        record = evidence_record(artifact)

        self.assertEqual(record, {"sha256": fingerprint(artifact)})
        self.assertTrue(valid_evidence_record(record))
        self.assertFalse(valid_evidence_record({"path": ".hdg-tmp/evidence.json", **record}))

    def test_light_is_valid_only_for_task(self) -> None:
        self.assertEqual(validate_work_item_definition(task_definition())["gateLevel"], "LIGHT")
        with self.assertRaises(GatedLoopError) as raised:
            validate_work_item_definition(capability_definition(gateLevel="LIGHT"))
        self.assertEqual(raised.exception.code, "WORK_ITEM_GATE_LEVEL_INVALID")

    def test_root_dependencies_require_an_aggregation_level(self) -> None:
        source = task_definition()
        source["execution"]["dependsOn"] = ["t-provider"]
        with self.assertRaises(GatedLoopError) as raised:
            validate_work_item_definition(source)
        self.assertEqual(raised.exception.code, "WORK_ITEM_DEPENDENCY_INVALID")

    def test_scope_rejects_mid_path_globs_and_plan_files_outside_scope(self) -> None:
        with self.assertRaises(GatedLoopError) as raised:
            validate_work_item_definition(task_definition(scope=["src/**/controller.*"]))
        self.assertEqual(raised.exception.code, "WORK_ITEM_SCOPE_INVALID")
        source = task_definition()
        source["developmentPlan"]["fileChanges"][0]["path"] = "outside/controller.py"
        with self.assertRaises(GatedLoopError) as raised:
            validate_work_item_definition(source)
        self.assertEqual(raised.exception.code, "WORK_ITEM_DEVELOPMENT_PLAN_INVALID")

    def test_contract_fingerprint_ignores_scope_order(self) -> None:
        first = validate_work_item_definition(task_definition())
        second_source = task_definition()
        second_source["scope"].reverse()
        second_source["developmentPlan"]["fileChanges"].reverse()
        second = validate_work_item_definition(second_source)
        self.assertEqual(work_item_contract_fingerprint(first), work_item_contract_fingerprint(second))

    def test_child_fingerprint_ignores_unrelated_sibling(self) -> None:
        original = validate_work_item_definition(capability_definition())
        source = capability_definition()
        source["children"].append({
            "id": "t-docs",
            "kind": "TASK",
            "title": "Document controller",
            "requirementIds": ["R-001"],
            "acceptanceIds": ["A-001"],
        })
        source["developmentPlan"]["childPlans"].append({
            "id": "t-docs",
            "purpose": "Document the controller.",
            "deliverables": ["Verified documentation."],
            "requirementIds": ["R-001"],
            "acceptanceIds": ["A-001"],
            "dependsOn": [],
        })
        source["developmentPlan"]["deliveryWaves"].append({
            "order": 2,
            "name": "Wave 2",
            "childIds": ["t-docs"],
            "exitCriteria": "Documentation is verified.",
        })
        revised = validate_work_item_definition(source)
        self.assertEqual(
            work_item_child_contract_fingerprint(original, "t-python-controller"),
            work_item_child_contract_fingerprint(revised, "t-python-controller"),
        )

    def test_coordination_plan_rejects_dependency_cycle_and_bad_wave_order(self) -> None:
        source = capability_definition()
        source["children"].append({
            "id": "t-docs",
            "kind": "TASK",
            "title": "Document controller",
            "requirementIds": ["R-001"],
            "acceptanceIds": ["A-001"],
        })
        source["developmentPlan"]["childPlans"].append({
            "id": "t-docs",
            "purpose": "Document the controller.",
            "deliverables": ["Verified documentation."],
            "requirementIds": ["R-001"],
            "acceptanceIds": ["A-001"],
            "dependsOn": ["t-python-controller"],
        })
        source["developmentPlan"]["childPlans"][0]["dependsOn"] = ["t-docs"]
        source["developmentPlan"]["deliveryWaves"].append({
            "order": 2,
            "name": "Wave 2",
            "childIds": ["t-docs"],
            "exitCriteria": "Documentation is verified.",
        })
        with self.assertRaises(GatedLoopError) as raised:
            validate_work_item_definition(source)
        self.assertEqual(raised.exception.code, "WORK_ITEM_DEPENDENCY_CYCLE")

    def test_scope_overlap_uses_exact_and_directory_prefixes(self) -> None:
        self.assertTrue(scope_patterns_overlap(["src/**"], ["src/controller.py"]))
        self.assertFalse(scope_patterns_overlap(["src/a.py"], ["src/b.py"]))

    def test_self_hosting_requires_explicit_dogfood(self) -> None:
        policy = resolve_self_hosting_policy(project_name="layered-delivery")
        self.assertFalse(policy["createsRuntimePackage"])
        self.assertTrue(resolve_self_hosting_policy(
            project_name="layered-delivery", explicit_dogfood=True
        )["createsRuntimePackage"])


if __name__ == "__main__":
    unittest.main()
