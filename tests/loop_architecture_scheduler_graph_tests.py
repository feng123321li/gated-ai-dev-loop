from __future__ import annotations

from .loop_architecture_support import (
    GatedLoopError,
    compile_delivery_graph,
    confirmation_node_id,
    graph_assurance_profile,
    graph_fingerprint,
    graph_summary,
    group_hierarchy,
    group_review_node_id,
    hierarchy_fingerprint,
    join_node_id,
    loop_descriptor,
    loop_node_id,
    node,
    recursive_hierarchy,
    review_node_id,
    skill_hint,
    task_definition,
    task_hierarchy,
    unittest,
    validate_delivery_graph,
    validate_hierarchy_definition,
)


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
        self.assertEqual(graph_assurance_profile(graph), "STANDARD")
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

    def test_light_delivery_compiles_to_task_and_confirmation_only(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["delivery"].update(
            {
                "assuranceProfile": "LIGHT",
                "assuranceRationale": (
                    "The actual diff is confined to one internal helper and "
                    "has no interface, data, permission, or deployment impact."
                ),
                "reviewLoop": None,
            }
        )
        hierarchy["root"]["reviewLoop"] = None

        graph = self.compile(hierarchy)

        self.assertEqual(graph_assurance_profile(graph), "LIGHT")
        self.assertEqual(
            {item["id"] for item in graph["nodes"]},
            {
                loop_node_id("t-service"),
                confirmation_node_id("d-service"),
            },
        )
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph_summary(graph)["reviewLoops"], 0)

    def test_light_delivery_rejects_group_or_review_loops(self) -> None:
        group = group_hierarchy()
        group["delivery"].update(
            {
                "assuranceProfile": "LIGHT",
                "assuranceRationale": "The requested change appears small.",
                "reviewLoop": None,
            }
        )
        with self.assertRaises(GatedLoopError) as caught:
            validate_hierarchy_definition(group)
        self.assertEqual(caught.exception.code, "DELIVERY_ASSURANCE_INVALID")

        task = task_hierarchy()
        task["delivery"].update(
            {
                "assuranceProfile": "LIGHT",
                "assuranceRationale": "The actual change is locally scoped.",
                "reviewLoop": None,
            }
        )
        with self.assertRaises(GatedLoopError) as caught:
            validate_hierarchy_definition(task)
        self.assertEqual(caught.exception.code, "DELIVERY_ASSURANCE_INVALID")

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

    def test_group_without_seam_review_uses_join_as_its_terminal(self) -> None:
        source = group_hierarchy()
        source["root"]["reviewLoop"] = None

        graph = self.compile(source)
        edges = {
            (item["source"], item["target"], item["kind"])
            for item in graph["edges"]
        }

        self.assertNotIn(
            group_review_node_id("g-service"),
            {item["id"] for item in graph["nodes"]},
        )
        self.assertIn(
            (
                join_node_id("g-service"),
                review_node_id("d-service"),
                "REQUIRES_SUCCESS",
            ),
            edges,
        )
        self.assertEqual(graph_summary(graph)["reviewLoops"], 3)

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
