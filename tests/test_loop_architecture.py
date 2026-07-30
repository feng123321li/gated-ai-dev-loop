from __future__ import annotations

from copy import deepcopy
import unittest

from hdg.errors import GatedLoopError
from hdg.graph_model import (
    compile_delivery_graph,
    confirmation_node_id,
    graph_fingerprint,
    graph_summary,
    group_review_node_id,
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
    return {"name": name, "purpose": purpose}


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
        "summary": "Schedule one opaque TASK Loop.",
        "execution": {
            "dependsOn": depends_on or [],
            "loop": loop_descriptor(claims=claims),
        },
    }


def group_definition(
    *,
    item_id: str,
    parent_id: str | None,
    children: list[dict],
    depends_on: list[str] | None = None,
) -> dict:
    return {
        "schemaVersion": 3,
        "id": item_id,
        "kind": "GROUP",
        "parentId": parent_id,
        "title": f"Coordinate {item_id}",
        "summary": "Join and review direct GROUP/TASK children.",
        "decomposition": {"dependsOn": depends_on or []},
        "children": [
            {
                "id": child["definition"]["id"],
                "kind": child["definition"]["kind"],
                "title": child["definition"]["title"],
            }
            for child in children
        ],
    }


def node(
    definition: dict,
    children: list[dict] | None = None,
    *,
    review_loop: dict | None = None,
) -> dict:
    if definition["kind"] == "TASK":
        return {
            "definition": definition,
            "reviewLoop": review_loop
            or loop_descriptor("task/independent-review-loop@1"),
            "children": children or [],
        }
    return {
        "definition": definition,
        "reviewLoop": review_loop
        or loop_descriptor("group/independent-review-loop@1"),
        "children": children or [],
    }


def delivery(root: dict, *, delivery_id: str = "d-service") -> dict:
    return {
        "delivery": {
            "id": delivery_id,
            "title": f"Deliver {delivery_id}",
            "summary": "Complete and independently review the Delivery.",
            "reviewLoop": loop_descriptor(
                "delivery/independent-review-loop@1"
            ),
        },
        "root": {
            "schemaVersion": 3,
            "skillHints": [],
            **root,
        },
    }


def task_hierarchy() -> dict:
    return delivery(node(task_definition()))


def group_hierarchy() -> dict:
    children = [
        node(
            task_definition(
                item_id="t-api",
                parent_id="g-service",
                claims=["project:erp/module:api"],
            )
        ),
        node(
            task_definition(
                item_id="t-core",
                parent_id="g-service",
                depends_on=["t-api"],
                claims=["project:erp/module:core"],
            )
        ),
    ]
    return delivery(
        node(
            group_definition(
                item_id="g-service",
                parent_id=None,
                children=children,
            ),
            children,
        )
    )


def recursive_hierarchy() -> dict:
    domain_tasks = [
        node(
            task_definition(
                item_id="t-model",
                parent_id="g-domain",
            )
        ),
        node(
            task_definition(
                item_id="t-repository",
                parent_id="g-domain",
                depends_on=["t-model"],
            )
        ),
    ]
    domain = node(
        group_definition(
            item_id="g-domain",
            parent_id="g-backend",
            children=domain_tasks,
        ),
        domain_tasks,
    )
    api = node(
        task_definition(
            item_id="t-api",
            parent_id="g-backend",
            depends_on=["g-domain"],
        )
    )
    backend_children = [domain, api]
    backend = node(
        group_definition(
            item_id="g-backend",
            parent_id="g-root",
            children=backend_children,
            depends_on=["t-bootstrap"],
        ),
        backend_children,
    )
    quality_task = node(
        task_definition(item_id="t-e2e", parent_id="g-quality")
    )
    quality = node(
        group_definition(
            item_id="g-quality",
            parent_id="g-root",
            children=[quality_task],
            depends_on=["g-backend"],
        ),
        [quality_task],
    )
    root_children = [
        node(task_definition(item_id="t-bootstrap", parent_id="g-root")),
        backend,
        quality,
        node(
            task_definition(
                item_id="t-docs",
                parent_id="g-root",
                depends_on=["g-quality"],
            )
        ),
    ]
    return delivery(
        node(
            group_definition(
                item_id="g-root",
                parent_id=None,
                children=root_children,
            ),
            root_children,
        ),
        delivery_id="d-recursive",
    )


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

    def test_root_task_compiles_to_delivery_review_and_confirmation(
        self,
    ) -> None:
        graph = self.compile(task_hierarchy())

        self.assertEqual(
            {item["id"] for item in graph["nodes"]},
            {
                loop_node_id("t-service"),
                "review:task:t-service",
                review_node_id("d-service"),
                confirmation_node_id("d-service"),
            },
        )
        self.assertEqual(
            graph_summary(graph),
            {
                "nodes": 4,
                "edges": 3,
                "taskLoops": 1,
                "joinNodes": 0,
                "reviewLoops": 2,
                "confirmationNodes": 1,
                "runtimeTransitions": len(graph["runtime"]["transitions"]),
            },
        )
        self.assertEqual(validate_delivery_graph(graph), graph)
        self.assertRegex(graph_fingerprint(graph), r"^[0-9a-f]{64}$")

    def test_root_task_may_add_review_without_a_group(self) -> None:
        source = task_hierarchy()
        source["root"]["reviewLoop"] = loop_descriptor(
            "task/independent-review-loop@1"
        )

        graph = self.compile(source)
        task_loop = loop_node_id("t-service")
        task_review = "review:task:t-service"
        delivery_review = review_node_id("d-service")
        edges = {
            (item["source"], item["target"], item["kind"])
            for item in graph["edges"]
        }

        self.assertEqual(
            {item["id"] for item in graph["nodes"]},
            {
                task_loop,
                task_review,
                delivery_review,
                confirmation_node_id("d-service"),
            },
        )
        self.assertLessEqual(
            {
                (task_loop, task_review, "REQUIRES_SUCCESS"),
                (task_review, delivery_review, "REQUIRES_SUCCESS"),
            },
            edges,
        )

    def test_group_review_is_required(self) -> None:
        source = group_hierarchy()
        source["root"]["reviewLoop"] = None

        with self.assertRaises(GatedLoopError) as caught:
            self.compile(source)
        self.assertEqual(
            caught.exception.code,
            "WORK_ITEM_GROUP_REVIEW_REQUIRED",
        )

    def test_task_dependency_waits_for_configured_task_review(self) -> None:
        source = group_hierarchy()
        source["root"]["children"][0]["reviewLoop"] = loop_descriptor(
            "task/independent-review-loop@1"
        )

        graph = self.compile(source)
        edges = {
            (item["source"], item["target"], item["kind"])
            for item in graph["edges"]
        }

        self.assertIn(
            (
                "review:task:t-api",
                loop_node_id("t-core"),
                "REQUIRES_SUCCESS",
            ),
            edges,
        )
        self.assertNotIn(
            (
                loop_node_id("t-api"),
                loop_node_id("t-core"),
                "REQUIRES_SUCCESS",
            ),
            edges,
        )
        self.assertIn(
            (
                "review:task:t-api",
                join_node_id("g-service"),
                "ALL_OF",
            ),
            edges,
        )

    def test_group_compiles_to_join_then_recursive_review(self) -> None:
        graph = self.compile(group_hierarchy())
        edges = {
            (item["source"], item["target"], item["kind"])
            for item in graph["edges"]
        }

        self.assertIn(
            (
                "review:task:t-api",
                loop_node_id("t-core"),
                "REQUIRES_SUCCESS",
            ),
            edges,
        )
        for task_id in ("t-api", "t-core"):
            self.assertIn(
                (
                    f"review:task:{task_id}",
                    join_node_id("g-service"),
                    "ALL_OF",
                ),
                edges,
            )
        self.assertIn(
            (
                join_node_id("g-service"),
                group_review_node_id("g-service"),
                "REQUIRES_SUCCESS",
            ),
            edges,
        )
        self.assertIn(
            (
                group_review_node_id("g-service"),
                review_node_id("d-service"),
                "REQUIRES_SUCCESS",
            ),
            edges,
        )

    def test_recursive_mixed_dependencies_gate_subtree_entries(self) -> None:
        graph = self.compile(recursive_hierarchy())
        edges = {
            (item["source"], item["target"], item["kind"])
            for item in graph["edges"]
        }

        expected = {
            (
                "review:task:t-model",
                loop_node_id("t-repository"),
                "REQUIRES_SUCCESS",
            ),
            (
                group_review_node_id("g-domain"),
                loop_node_id("t-api"),
                "REQUIRES_SUCCESS",
            ),
            (
                "review:task:t-bootstrap",
                loop_node_id("t-model"),
                "REQUIRES_SUCCESS",
            ),
            (
                group_review_node_id("g-backend"),
                loop_node_id("t-e2e"),
                "REQUIRES_SUCCESS",
            ),
            (
                group_review_node_id("g-quality"),
                loop_node_id("t-docs"),
                "REQUIRES_SUCCESS",
            ),
        }
        self.assertLessEqual(expected, edges)
        self.assertEqual(graph_summary(graph)["joinNodes"], 4)
        self.assertEqual(graph_summary(graph)["reviewLoops"], 11)

    def test_multi_group_multi_task_reviews_each_task_and_group(self) -> None:
        source = recursive_hierarchy()
        root = source["root"]
        backend = root["children"][1]
        domain = backend["children"][0]
        quality = root["children"][2]

        graph = self.compile(source)
        node_ids = {item["id"] for item in graph["nodes"]}
        edges = {
            (item["source"], item["target"], item["kind"])
            for item in graph["edges"]
        }

        self.assertLessEqual(
            {
                "review:task:t-model",
                "review:task:t-repository",
                "review:task:t-api",
                "review:task:t-bootstrap",
                "review:task:t-e2e",
                "review:task:t-docs",
                group_review_node_id("g-root"),
                group_review_node_id("g-backend"),
                group_review_node_id("g-domain"),
                group_review_node_id("g-quality"),
            },
            node_ids,
        )
        self.assertLessEqual(
            {
                (
                    loop_node_id("t-model"),
                    "review:task:t-model",
                    "REQUIRES_SUCCESS",
                ),
                (
                    "review:task:t-model",
                    loop_node_id("t-repository"),
                    "REQUIRES_SUCCESS",
                ),
                (
                    group_review_node_id("g-domain"),
                    loop_node_id("t-api"),
                    "REQUIRES_SUCCESS",
                ),
                (
                    group_review_node_id("g-backend"),
                    loop_node_id("t-e2e"),
                    "REQUIRES_SUCCESS",
                ),
                (
                    "review:task:t-e2e",
                    join_node_id("g-quality"),
                    "ALL_OF",
                ),
                (
                    group_review_node_id("g-quality"),
                    loop_node_id("t-docs"),
                    "REQUIRES_SUCCESS",
                ),
                (
                    group_review_node_id("g-root"),
                    review_node_id("d-recursive"),
                    "REQUIRES_SUCCESS",
                ),
            },
            edges,
        )

    def test_skill_hints_are_shared_late_bound_input_not_graph_nodes(
        self,
    ) -> None:
        source = task_hierarchy()
        source["root"]["skillHints"] = [
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
            [
                hint["name"]
                for hint in hierarchy["root"]["skillHints"]
            ],
            ["java-coding-standards", "springboot-tdd"],
        )
        self.assertTrue(
            all("skillHints" not in graph_node for graph_node in graph["nodes"])
        )

    def test_skill_hints_must_be_unique_and_well_formed(self) -> None:
        duplicate = task_hierarchy()
        duplicate["root"]["skillHints"] = [
            skill_hint("springboot-tdd"),
            skill_hint("springboot-tdd", "Another purpose."),
        ]
        with self.assertRaises(GatedLoopError):
            validate_hierarchy_definition(duplicate)

        missing = task_hierarchy()
        del missing["root"]["skillHints"]
        with self.assertRaises(GatedLoopError):
            validate_hierarchy_definition(missing)

    def test_parent_child_summary_must_match_materialized_title(
        self,
    ) -> None:
        source = group_hierarchy()
        source["root"]["definition"]["children"][0][
            "title"
        ] = "A conflicting title"

        with self.assertRaises(GatedLoopError):
            validate_hierarchy_definition(source)

    def test_invalid_recursive_shapes_and_dependencies_are_rejected(
        self,
    ) -> None:
        empty_group = group_hierarchy()
        empty_group["root"]["definition"]["children"] = []
        empty_group["root"]["children"] = []

        task_with_child = task_hierarchy()
        task_with_child["root"]["children"] = [
            node(task_definition(item_id="t-invalid", parent_id="t-service"))
        ]

        non_sibling = group_hierarchy()
        non_sibling["root"]["children"][1]["definition"]["execution"][
            "dependsOn"
        ] = ["g-outside"]

        cycle = group_hierarchy()
        cycle["root"]["children"][0]["definition"]["execution"][
            "dependsOn"
        ] = ["t-core"]

        legacy = task_hierarchy()
        legacy["root"]["definition"]["kind"] = "CAPABILITY"

        for invalid in (
            empty_group,
            task_with_child,
            non_sibling,
            cycle,
            legacy,
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(GatedLoopError):
                    validate_hierarchy_definition(invalid)

    def test_graph_runtime_retries_only_infrastructure_failures(self) -> None:
        graph = self.compile(task_hierarchy())
        policy = graph["runtime"]

        self.assertEqual(
            policy["retryPolicy"]["automaticFailureClasses"],
            ["RETRYABLE_INFRA", "WORKER_LOST"],
        )
        events = {item["eventType"] for item in policy["transitions"]}
        self.assertIn("LOOP_SUCCEEDED", events)
        self.assertIn("LOOP_BLOCKED", events)
        self.assertIn("LOOP_REPLAN_REQUIRED", events)
        self.assertNotIn("GATE_FAILED", events)
        self.assertNotIn("TASK_IMPLEMENTED", events)
        transitions = {
            item["eventType"]: item
            for item in policy["transitions"]
        }
        self.assertEqual(
            (
                transitions["NODE_READY"]["fromStates"],
                transitions["NODE_READY"]["toStates"],
            ),
            (["PENDING"], ["READY"]),
        )
        self.assertEqual(
            (
                transitions["JOIN_COMPLETED"]["fromStates"],
                transitions["JOIN_COMPLETED"]["toStates"],
            ),
            (["PENDING"], ["SUCCEEDED"]),
        )
        self.assertEqual(
            (
                transitions["NODE_RESUMED"]["fromStates"],
                transitions["NODE_RESUMED"]["toStates"],
            ),
            (["PAUSED"], ["PENDING"]),
        )
        self.assertEqual(
            (
                transitions["NODE_AUTO_RESUMED"]["fromStates"],
                transitions["NODE_AUTO_RESUMED"]["toStates"],
                transitions["NODE_AUTO_RESUMED"]["automatic"],
            ),
            (["PAUSED"], ["PENDING"], True),
        )


if __name__ == "__main__":
    unittest.main()
