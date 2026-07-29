from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from hdg import mcp_server
from hdg.errors import GatedLoopError
from hdg.hierarchy_contract import hierarchy_contract
from hdg.mcp_tools import call_tool
from hdg.planning import prepare_hierarchy


class HierarchyContractTests(unittest.TestCase):
    def test_contract_examples_are_directly_accepted_by_prepare_hierarchy(
        self,
    ) -> None:
        cases = (
            ("TASK", "COMPACT_TASK"),
            ("TASK", "FULL_HIERARCHY"),
            ("CAPABILITY", "FULL_HIERARCHY"),
            ("DELIVERY", "FULL_HIERARCHY"),
        )
        for root_kind, input_mode in cases:
            with self.subTest(root_kind=root_kind, input_mode=input_mode):
                contract = hierarchy_contract(
                    root_kind=root_kind,
                    input_mode=input_mode,
                )
                self.assertEqual(contract["schemaVersion"], 3)
                self.assertEqual(contract["rootKind"], root_kind)
                self.assertEqual(contract["inputMode"], input_mode)
                self.assertEqual(
                    set(contract),
                    {
                        "schemaVersion",
                        "rootKind",
                        "inputMode",
                        "inputSchema",
                        "example",
                        "invariants",
                    },
                )
                self.assertEqual(
                    contract["inputSchema"]["additionalProperties"],
                    False,
                )
                with tempfile.TemporaryDirectory() as temporary:
                    prepared = prepare_hierarchy(
                        root=temporary,
                        hierarchy=deepcopy(contract["example"]),
                        host_runtime="codex",
                    )
                self.assertEqual(
                    prepared["inputMode"],
                    input_mode,
                )

    def test_contract_exposes_strict_nested_full_hierarchy_shapes(self) -> None:
        contract = hierarchy_contract(
            root_kind="DELIVERY",
            input_mode="FULL_HIERARCHY",
        )
        schema = contract["inputSchema"]
        root = schema["properties"]["root"]
        delivery = root["properties"]["definition"]
        capability_node = root["properties"]["children"]["items"]
        capability = capability_node["properties"]["definition"]
        task_node = capability_node["properties"]["children"]["items"]
        task = task_node["properties"]["definition"]

        for definition, kind in (
            (delivery, "DELIVERY"),
            (capability, "CAPABILITY"),
            (task, "TASK"),
        ):
            self.assertEqual(definition["additionalProperties"], False)
            self.assertEqual(
                definition["properties"]["kind"]["const"],
                kind,
            )
            self.assertIn("developmentPlan", definition["required"])
        self.assertEqual(
            set(task["properties"]["execution"]["required"]),
            {"dependsOn", "inputs", "outputs"},
        )
        self.assertEqual(
            set(
                task["properties"]["developmentPlan"]["required"]
            ),
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
        )
        self.assertIn(
            "generatedFileRoots",
            task["properties"]["developmentPlan"]["properties"],
        )

    def test_contract_rejects_compact_mode_for_coordination_roots(self) -> None:
        with self.assertRaises(GatedLoopError) as raised:
            hierarchy_contract(
                root_kind="CAPABILITY",
                input_mode="COMPACT_TASK",
            )

        self.assertEqual(
            raised.exception.code,
            "WORK_ITEM_HIERARCHY_CONTRACT_INVALID",
        )
        self.assertEqual(
            raised.exception.details["allowedInputModes"],
            ["FULL_HIERARCHY"],
        )

    def test_mcp_contract_tool_is_read_only_and_does_not_create_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = call_tool(
                "hierarchy_contract",
                {
                    "root_kind": "TASK",
                    "input_mode": "COMPACT_TASK",
                },
                root=temporary,
            )

            self.assertEqual(result["rootKind"], "TASK")
            self.assertEqual(result["inputMode"], "COMPACT_TASK")
            self.assertFalse(
                Path(temporary, ".layered-delivery").exists(),
            )

    def test_mcp_protocol_returns_the_on_demand_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = mcp_server.ServerSession(
                project_root=(
                    mcp_server.ProjectRootBinding.from_startup(temporary)
                ),
                initialize_requested=True,
                initialized=True,
            )
            response = mcp_server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "hierarchy_contract",
                        "arguments": {
                            "root_kind": "DELIVERY",
                            "input_mode": "FULL_HIERARCHY",
                        },
                    },
                },
                session=session,
            )

        self.assertIsNotNone(response)
        assert response is not None
        result = response["result"]
        self.assertIs(result["isError"], False)
        contract = result["structuredContent"]["result"]
        self.assertEqual(contract["rootKind"], "DELIVERY")
        self.assertEqual(contract["inputMode"], "FULL_HIERARCHY")
        self.assertEqual(
            contract["example"]["root"]["definition"]["kind"],
            "DELIVERY",
        )


if __name__ == "__main__":
    unittest.main()
