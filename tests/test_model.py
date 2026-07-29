from __future__ import annotations

from copy import deepcopy
import unittest

from hdg.errors import GatedLoopError
from hdg.evidence import (
    evidence_record,
    gate_evidence_contract,
    hydrate_gate_evidence,
    valid_evidence_record,
    valid_gate_artifact,
)
from hdg.jsonio import fingerprint
from hdg.model import (
    render_development_plan,
    resolve_self_hosting_policy,
    scope_patterns_overlap,
    validate_hierarchy_definition,
    validate_work_item_definition,
    work_item_baseline_fingerprint,
    work_item_child_contract_fingerprint,
    work_item_contract_fingerprint,
)

from .fixtures import capability_definition, delivery_definition, task_definition


class WorkItemModelTests(unittest.TestCase):
    def assert_shape_details(
        self,
        error: GatedLoopError,
        *,
        field: str,
        required: set[str],
        optional: set[str],
        actual: set[str],
    ) -> None:
        self.assertEqual(error.details["field"], field)
        self.assertEqual(set(error.details["requiredKeys"]), required)
        self.assertEqual(set(error.details["optionalKeys"]), optional)
        self.assertEqual(set(error.details["actualKeys"]), actual)
        self.assertEqual(
            set(error.details["missingKeys"]),
            required - actual,
        )
        self.assertEqual(
            set(error.details["unknownKeys"]),
            actual - required - optional,
        )

    def test_hierarchy_shape_error_reports_the_complete_key_diff(self) -> None:
        source = {"schemaVersion": 3, "delivery": {}}

        with self.assertRaises(GatedLoopError) as raised:
            validate_hierarchy_definition(source)

        self.assertEqual(
            raised.exception.code,
            "WORK_ITEM_HIERARCHY_INVALID",
        )
        self.assert_shape_details(
            raised.exception,
            field="hierarchy",
            required={"schemaVersion", "root"},
            optional=set(),
            actual={"schemaVersion", "delivery"},
        )

    def test_nested_shape_errors_report_field_and_all_expected_keys(
        self,
    ) -> None:
        cases = []

        node = {"schemaVersion": 3, "root": {"definition": {}}}
        cases.append((
            node,
            "root",
            {"definition", "children"},
            set(),
            {"definition"},
        ))

        execution = task_definition()
        execution["execution"]["summary"] = "unknown"
        cases.append((
            execution,
            "definition.execution",
            {"dependsOn", "inputs", "outputs"},
            set(),
            {"dependsOn", "inputs", "outputs", "summary"},
        ))

        plan = task_definition()
        plan["developmentPlan"]["summary"] = "unknown"
        cases.append((
            plan,
            "definition.developmentPlan",
            {
                "purpose",
                "scenarios",
                "fileChanges",
                "interfaces",
                "logic",
                "dataAndTransactions",
                "compatibility",
                "testPlan",
                "reviewPoints",
            },
            {"generatedFileRoots"},
            set(plan["developmentPlan"]),
        ))

        scenario = task_definition()
        scenario["developmentPlan"]["scenarios"][0]["summary"] = "unknown"
        cases.append((
            scenario,
            "definition.developmentPlan.scenarios[0]",
            {"kind", "title", "description", "requirementIds"},
            set(),
            set(scenario["developmentPlan"]["scenarios"][0]),
        ))

        for source, field, required, optional, actual in cases:
            with self.subTest(field=field):
                with self.assertRaises(GatedLoopError) as raised:
                    if "root" in source:
                        validate_hierarchy_definition(source)
                    else:
                        validate_work_item_definition(source)
                self.assert_shape_details(
                    raised.exception,
                    field=field,
                    required=required,
                    optional=optional,
                    actual=actual,
                )

    def test_invalid_record_enum_reports_all_allowed_values(self) -> None:
        source = task_definition()
        source["developmentPlan"]["scenarios"][0]["kind"] = "UNKNOWN"

        with self.assertRaises(GatedLoopError) as raised:
            validate_work_item_definition(source)

        self.assertEqual(
            raised.exception.details["field"],
            "definition.developmentPlan.scenarios[0].kind",
        )
        self.assertIn("API", raised.exception.details["allowed"])
        self.assertIn("OTHER", raised.exception.details["allowed"])

    def test_task_is_normalized_and_fingerprinted(self) -> None:
        definition = validate_work_item_definition(task_definition())
        self.assertEqual(definition["authorityKind"], "EXECUTION")
        self.assertRegex(work_item_baseline_fingerprint(definition), r"^[a-f0-9]{64}$")

    def test_all_hierarchy_shapes_render_distinct_plans(self) -> None:
        for source, heading in (
            (task_definition(), "## 文件改动"),
            (capability_definition(), "## 任务开发内容"),
            (delivery_definition(), "## 能力开发内容"),
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

    def test_each_requirement_requires_an_independent_acceptance_criterion(self) -> None:
        source = task_definition()
        source["requirements"] = [
            {"id": "R-001", "text": "The controller must run on Python."},
            {"id": "R-002", "text": "The controller must preserve command semantics."},
        ]
        source["acceptance"] = [{
            "id": "A-001",
            "requirementIds": ["R-001", "R-002"],
            "expectedResult": "The controller works as expected.",
        }]
        source["developmentPlan"]["scenarios"][0]["requirementIds"] = ["R-001", "R-002"]
        source["developmentPlan"]["interfaces"][0]["requirementIds"] = ["R-001", "R-002"]

        with self.assertRaises(GatedLoopError) as raised:
            validate_work_item_definition(source)

        self.assertEqual(raised.exception.code, "WORK_ITEM_TRACE_INVALID")
        self.assertIn("R-001, R-002", str(raised.exception))

    def test_cross_requirement_acceptance_is_allowed_after_independent_coverage(self) -> None:
        source = task_definition()
        source["requirements"] = [
            {"id": "R-001", "text": "The controller must run on Python."},
            {"id": "R-002", "text": "The controller must preserve command semantics."},
        ]
        source["acceptance"] = [
            {
                "id": "A-001",
                "requirementIds": ["R-001"],
                "expectedResult": "The Python entry point starts without third-party packages.",
            },
            {
                "id": "A-002",
                "requirementIds": ["R-002"],
                "expectedResult": "The frozen command contract passes its regression suite.",
            },
            {
                "id": "A-003",
                "requirementIds": ["R-001", "R-002"],
                "expectedResult": "The Python entry point passes the command compatibility suite.",
            },
        ]
        source["developmentPlan"]["scenarios"][0]["requirementIds"] = ["R-001", "R-002"]
        source["developmentPlan"]["interfaces"][0]["requirementIds"] = ["R-001", "R-002"]
        source["developmentPlan"]["testPlan"][0]["acceptanceIds"] = ["A-001", "A-002", "A-003"]

        definition = validate_work_item_definition(source)

        self.assertEqual(
            [item["id"] for item in definition["acceptance"]],
            ["A-001", "A-002", "A-003"],
        )

    def test_gate_contract_exposes_requirement_trace_for_each_criterion(self) -> None:
        definition = validate_work_item_definition(task_definition())
        entry = {
            "id": definition["id"],
            "baselineFingerprint": work_item_baseline_fingerprint(definition),
        }

        contract = gate_evidence_contract(entry, definition)

        self.assertEqual(
            contract["constraints"]["acceptanceExpectedResults"],
            {
                "A-001": (
                    "The frozen Python command completes successfully."
                ),
            },
        )
        self.assertEqual(
            contract["evidenceDeltaTemplate"]["acceptance"][0],
            {
                "id": "A-001",
                "status": "<PASS_OR_FAIL>",
                "evidence": "<REQUIRED_NON_EMPTY_STRING>",
            },
        )
        hydrated = hydrate_gate_evidence(
            {
                "evidenceDelta": {
                    **contract["evidenceDeltaTemplate"],
                    "verdict": "PASS",
                },
            },
            entry=entry,
            definition=definition,
        )
        self.assertEqual(
            hydrated["acceptance"][0]["requirementIds"],
            ["R-001"],
        )

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
            "acceptance": [{
                "id": "A-001",
                "requirementIds": ["R-001"],
                "status": "PASS",
                "evidence": "Verified.",
            }],
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
        wrong_trace = deepcopy(artifact)
        wrong_trace["acceptance"][0]["requirementIds"] = ["R-999"]
        self.assertFalse(valid_gate_artifact(wrong_trace, entry, definition))

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
