from __future__ import annotations

import unittest

from hdg.errors import GatedLoopError
from hdg.graph_model import (
    compile_delivery_graph,
    confirmation_node_id,
    execution_node_id,
    gate_node_id,
    graph_fingerprint,
    graph_summary,
    review_node_id,
    validate_delivery_graph,
)
from hdg.graph_runtime import critical_path
from hdg.model import hierarchy_fingerprint, validate_hierarchy_definition

from .fixtures import task_hierarchy, two_task_capability_hierarchy


class DeliveryGraphModelTests(unittest.TestCase):
    @staticmethod
    def _compile(source: dict) -> dict:
        hierarchy = validate_hierarchy_definition(source)
        return compile_delivery_graph(
            hierarchy,
            hierarchy_fingerprint=hierarchy_fingerprint(hierarchy),
        )

    def test_root_task_compiles_to_execution_and_governance_nodes(self) -> None:
        graph = self._compile(task_hierarchy())
        task_id = "t-python-controller"
        by_id = {node["id"]: node for node in graph["nodes"]}

        self.assertEqual(
            set(by_id),
            {
                execution_node_id(task_id),
                gate_node_id(task_id),
                review_node_id(task_id),
                confirmation_node_id(task_id),
            },
        )
        self.assertEqual(by_id[execution_node_id(task_id)]["planes"], ["EXECUTION"])
        self.assertEqual(
            by_id[gate_node_id(task_id)]["planes"],
            ["EXECUTION", "GOVERNANCE"],
        )
        self.assertEqual(by_id[review_node_id(task_id)]["planes"], ["GOVERNANCE"])
        self.assertEqual(
            [(edge["source"], edge["target"], edge["kind"]) for edge in graph["edges"]],
            [
                (gate_node_id(task_id), review_node_id(task_id), "REQUIRES_PASS"),
                (review_node_id(task_id), confirmation_node_id(task_id), "REQUIRES_PASS"),
                (execution_node_id(task_id), gate_node_id(task_id), "ON_SUCCESS"),
            ],
        )
        self.assertEqual(validate_delivery_graph(graph), graph)
        self.assertRegex(graph_fingerprint(graph), r"^[0-9a-f]{64}$")
        self.assertEqual(
            graph_summary(graph),
            {"nodes": 4, "edges": 3, "taskExecutions": 1, "gateNodes": 1, "reviewNodes": 2},
        )

    def test_task_dependencies_and_parent_gate_compile_to_typed_edges(self) -> None:
        source = two_task_capability_hierarchy()
        capability = source["root"]["definition"]
        worker = next(
            child["definition"]
            for child in source["root"]["children"]
            if child["definition"]["id"] == "t-python-worker"
        )
        worker["execution"]["dependsOn"] = ["t-python-controller"]
        next(
            child
            for child in capability["developmentPlan"]["childPlans"]
            if child["id"] == "t-python-worker"
        )["dependsOn"] = ["t-python-controller"]

        graph = self._compile(source)
        edges = {
            (edge["source"], edge["target"], edge["kind"], edge["joinGroup"])
            for edge in graph["edges"]
        }
        self.assertIn(
            (
                gate_node_id("t-python-controller"),
                execution_node_id("t-python-worker"),
                "REQUIRES_PASS",
                None,
            ),
            edges,
        )
        self.assertIn(
            (
                gate_node_id("t-python-controller"),
                gate_node_id("c-python-runtime"),
                "ALL_OF",
                "join:c-python-runtime:children",
            ),
            edges,
        )
        self.assertIn(
            (
                gate_node_id("t-python-worker"),
                gate_node_id("c-python-runtime"),
                "ALL_OF",
                "join:c-python-runtime:children",
            ),
            edges,
        )

    def test_graph_validation_rejects_cycles_and_unknown_fields(self) -> None:
        graph = self._compile(task_hierarchy())
        graph["edges"].append(
            {
                "id": "edge:cycle",
                "source": confirmation_node_id("t-python-controller"),
                "target": execution_node_id("t-python-controller"),
                "kind": "REQUIRES_PASS",
                "plane": "GOVERNANCE",
                "joinGroup": None,
            }
        )
        graph["edges"].sort(key=lambda item: item["id"])
        with self.assertRaises(GatedLoopError) as raised:
            validate_delivery_graph(graph)
        self.assertEqual(raised.exception.code, "DELIVERY_GRAPH_CYCLE")

        graph = self._compile(task_hierarchy())
        graph["nodes"][0]["unexpected"] = True
        with self.assertRaises(GatedLoopError) as raised:
            validate_delivery_graph(graph)
        self.assertEqual(raised.exception.code, "DELIVERY_GRAPH_NODE_INVALID")

    def test_critical_path_identifies_the_next_fan_in_join(self) -> None:
        graph = self._compile(two_task_capability_hierarchy())
        nodes = [
            {"id": node["id"], "status": "PENDING"}
            for node in graph["nodes"]
        ]
        path = critical_path(graph, nodes)
        self.assertEqual(path["remainingNodes"], 5)
        self.assertEqual(path["nextJoinNodeId"], gate_node_id("c-python-runtime"))
        self.assertEqual(path["nodeIds"][-3:], [
            gate_node_id("c-python-runtime"),
            review_node_id("c-python-runtime"),
            confirmation_node_id("c-python-runtime"),
        ])


if __name__ == "__main__":
    unittest.main()
