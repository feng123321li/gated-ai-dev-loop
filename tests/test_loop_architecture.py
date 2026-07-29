from __future__ import annotations

from copy import deepcopy
import unittest

from hdg.errors import GatedLoopError
from hdg.graph_model import (
    compile_delivery_graph,
    confirmation_node_id,
    graph_fingerprint,
    graph_summary,
    join_node_id,
    loop_node_id,
    review_node_id,
    validate_delivery_graph,
)
from hdg.loop_contracts import (
    validate_loop_descriptor,
    validate_loop_outcome,
)
from hdg.model import (
    hierarchy_fingerprint,
    validate_hierarchy_definition,
)


def loop_descriptor(
    ref: str = "project/java-service-loop@1",
    *,
    claims: list[str] | None = None,
) -> dict:
    return {
        "ref": ref,
        "payload": {
            "goal": "Deliver one observable result.",
            "acceptance": ["The loop returns verified evidence."],
        },
        "resourceClaims": claims or [],
    }


def skill_hint(
    name: str,
    purpose: str = "Prefer this Skill when it fits the active Loop.",
) -> dict:
    return {
        "name": name,
        "purpose": purpose,
    }


def task_definition(
    *,
    item_id: str = "t-service",
    parent_id: str | None = None,
    depends_on: list[str] | None = None,
    claims: list[str] | None = None,
) -> dict:
    return {
        "schemaVersion": 3,
        "id": item_id,
        "kind": "TASK",
        "parentId": parent_id,
        "title": f"Run {item_id}",
        "summary": "Schedule one opaque Task Loop.",
        "execution": {
            "dependsOn": depends_on or [],
            "loop": loop_descriptor(claims=claims),
        },
    }


def node(definition: dict, children: list[dict] | None = None) -> dict:
    return {"definition": definition, "children": children or []}


def task_hierarchy() -> dict:
    return {
        "schemaVersion": 3,
        "skillHints": [],
        "reviewLoop": loop_descriptor(
            "root/independent-review-loop@1",
        ),
        "root": node(task_definition()),
    }


def capability_hierarchy() -> dict:
    capability = {
        "schemaVersion": 3,
        "id": "c-service",
        "kind": "CAPABILITY",
        "parentId": None,
        "title": "Coordinate service loops",
        "summary": "Join two schedulable Task Loops.",
        "decomposition": {"dependsOn": []},
        "children": [
            {"id": "t-api", "kind": "TASK", "title": "Run t-api"},
            {"id": "t-core", "kind": "TASK", "title": "Run t-core"},
        ],
    }
    return {
        "schemaVersion": 3,
        "skillHints": [],
        "reviewLoop": loop_descriptor(
            "root/independent-review-loop@1",
        ),
        "root": node(
            capability,
            [
                node(
                    task_definition(
                        item_id="t-api",
                        parent_id="c-service",
                        claims=["project:erp/module:api"],
                    )
                ),
                node(
                    task_definition(
                        item_id="t-core",
                        parent_id="c-service",
                        depends_on=["t-api"],
                        claims=["project:erp/module:core"],
                    )
                ),
            ],
        ),
    }


class LoopContractTests(unittest.TestCase):
    def test_loop_descriptor_is_opaque_but_resource_claims_are_normalized(
        self,
    ) -> None:
        source = loop_descriptor(
            claims=[
                "project:erp/module:core",
                "project:erp/module:api",
            ],
        )

        descriptor = validate_loop_descriptor(source)

        self.assertEqual(
            descriptor["resourceClaims"],
            [
                "project:erp/module:api",
                "project:erp/module:core",
            ],
        )
        self.assertEqual(descriptor["payload"], source["payload"])

    def test_loop_descriptor_rejects_duplicate_or_unsafe_claims(self) -> None:
        duplicate = loop_descriptor(claims=["module:core", "module:core"])
        with self.assertRaises(GatedLoopError):
            validate_loop_descriptor(duplicate)

        unsafe = loop_descriptor(claims=["../outside"])
        with self.assertRaises(GatedLoopError):
            validate_loop_descriptor(unsafe)

    def test_loop_outcome_exposes_only_scheduler_terminal_semantics(
        self,
    ) -> None:
        outcome = validate_loop_outcome(
            {
                "status": "SUCCEEDED",
                "summary": "The internal loop completed.",
                "result": {
                    "tests": {"passed": 12},
                    "skills": ["project-java-loop"],
                },
            }
        )
        self.assertEqual(outcome["status"], "SUCCEEDED")
        self.assertEqual(outcome["result"]["tests"]["passed"], 12)

        invalid = deepcopy(outcome)
        invalid["status"] = "GATE_FAILED"
        with self.assertRaises(GatedLoopError):
            validate_loop_outcome(invalid)


class SchedulerGraphTests(unittest.TestCase):
    @staticmethod
    def compile(source: dict) -> dict:
        hierarchy = validate_hierarchy_definition(source)
        return compile_delivery_graph(
            hierarchy,
            hierarchy_fingerprint=hierarchy_fingerprint(hierarchy),
        )

    def test_root_task_compiles_to_one_task_loop_and_one_review_loop(
        self,
    ) -> None:
        graph = self.compile(task_hierarchy())
        task_id = "t-service"

        self.assertEqual(
            {item["id"] for item in graph["nodes"]},
            {
                loop_node_id(task_id),
                review_node_id(task_id),
                confirmation_node_id(task_id),
            },
        )
        self.assertEqual(
            graph_summary(graph),
            {
                "nodes": 3,
                "edges": 2,
                "taskLoops": 1,
                "joinNodes": 0,
                "reviewLoops": 1,
                "confirmationNodes": 1,
                "runtimeTransitions": len(graph["runtime"]["transitions"]),
            },
        )
        self.assertNotIn(
            "TASK_GATE",
            {item["kind"] for item in graph["nodes"]},
        )
        self.assertEqual(validate_delivery_graph(graph), graph)
        self.assertRegex(graph_fingerprint(graph), r"^[0-9a-f]{64}$")

    def test_skill_hints_are_shared_late_bound_input_not_graph_nodes(
        self,
    ) -> None:
        source = task_hierarchy()
        source["skillHints"] = [
            skill_hint("springboot-tdd", "Prefer TDD when applicable."),
            skill_hint(
                "java-coding-standards",
                "Prefer Java conventions when applicable.",
            ),
        ]

        hierarchy = validate_hierarchy_definition(source)
        graph = compile_delivery_graph(
            hierarchy,
            hierarchy_fingerprint=hierarchy_fingerprint(hierarchy),
        )

        self.assertEqual(
            [hint["name"] for hint in hierarchy["skillHints"]],
            ["java-coding-standards", "springboot-tdd"],
        )
        self.assertTrue(
            all("skillHints" not in node for node in graph["nodes"])
        )
        self.assertTrue(
            all(
                "requiredSkills" not in node
                for node in graph["nodes"]
            )
        )

    def test_skill_hints_must_be_unique_and_well_formed(self) -> None:
        duplicate = task_hierarchy()
        duplicate["skillHints"] = [
            skill_hint("springboot-tdd"),
            skill_hint("springboot-tdd", "Another purpose."),
        ]
        with self.assertRaises(GatedLoopError):
            validate_hierarchy_definition(duplicate)

        missing = task_hierarchy()
        del missing["skillHints"]
        with self.assertRaises(GatedLoopError):
            validate_hierarchy_definition(missing)

    def test_parent_child_summary_must_match_materialized_title(
        self,
    ) -> None:
        source = capability_hierarchy()
        source["root"]["definition"]["children"][0][
            "title"
        ] = "A conflicting title"

        with self.assertRaises(GatedLoopError):
            validate_hierarchy_definition(source)

    def test_capability_uses_a_join_and_task_dependency_targets_loops(
        self,
    ) -> None:
        graph = self.compile(capability_hierarchy())
        edges = {
            (item["source"], item["target"], item["kind"])
            for item in graph["edges"]
        }

        self.assertIn(
            (
                loop_node_id("t-api"),
                loop_node_id("t-core"),
                "REQUIRES_SUCCESS",
            ),
            edges,
        )
        self.assertIn(
            (
                loop_node_id("t-api"),
                join_node_id("c-service"),
                "ALL_OF",
            ),
            edges,
        )
        self.assertIn(
            (
                loop_node_id("t-core"),
                join_node_id("c-service"),
                "ALL_OF",
            ),
            edges,
        )

    def test_graph_runtime_retries_only_infrastructure_failures(self) -> None:
        graph = self.compile(task_hierarchy())
        policy = graph["runtime"]

        self.assertEqual(
            policy["retryPolicy"]["automaticFailureClasses"],
            ["RETRYABLE_INFRA", "WORKER_LOST"],
        )
        events = {
            item["eventType"]
            for item in policy["transitions"]
        }
        self.assertIn("LOOP_SUCCEEDED", events)
        self.assertIn("LOOP_BLOCKED", events)
        self.assertIn("LOOP_REPLAN_REQUIRED", events)
        self.assertNotIn("GATE_FAILED", events)
        self.assertNotIn("TASK_IMPLEMENTED", events)


if __name__ == "__main__":
    unittest.main()
