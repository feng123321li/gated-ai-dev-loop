from __future__ import annotations

import unittest

from hdg.mcp_tools import tool_definitions
from scripts.mcp_dynamic_catalog_demo import (
    DynamicCatalogRegistry,
    run_reference_demo,
)


class McpDynamicCatalogDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = tool_definitions()
        self.registry = DynamicCatalogRegistry(self.tools)

    def test_reconnect_publishes_only_to_the_next_turn(self) -> None:
        self.registry.fail_attempt(
            server="delivery-graph",
            attempt=1,
            reason="SPAWN_FAILED",
        )
        first = self.registry.snapshot_for_turn(
            server="delivery-graph",
            workspace="G:/workspace/alpha",
            session_id="session-alpha",
            turn_id="turn-1",
            agent_role="primary",
        )
        self.registry.publish_catalog(
            server="delivery-graph",
            attempt=2,
            tools=self.tools,
        )
        second = self.registry.snapshot_for_turn(
            server="delivery-graph",
            workspace="G:/workspace/alpha",
            session_id="session-alpha",
            turn_id="turn-2",
            agent_role="primary",
        )

        self.assertEqual(first["status"], "PLUGIN_MCP_UNAVAILABLE")
        self.assertEqual(first["toolCount"], 0)
        self.assertEqual(second["status"], "REGISTERED")
        self.assertEqual(second["toolCount"], 32)
        self.assertEqual(first["toolCount"], 0)
        self.assertEqual(first["sessionId"], second["sessionId"])

    def test_partial_catalog_is_rejected_atomically(self) -> None:
        result = self.registry.publish_catalog(
            server="delivery-graph",
            attempt=1,
            tools=self.tools[:-1],
        )
        turn = self.registry.snapshot_for_turn(
            server="delivery-graph",
            workspace="G:/workspace/alpha",
            session_id="session-alpha",
            turn_id="turn-1",
            agent_role="primary",
        )

        self.assertFalse(result["published"])
        self.assertEqual(result["status"], "PARTIAL_REGISTRATION")
        self.assertEqual(turn["status"], "PLUGIN_MCP_UNAVAILABLE")
        self.assertEqual(turn["toolCount"], 0)

    def test_child_created_after_reconnect_reads_latest_catalog(self) -> None:
        self.registry.publish_catalog(
            server="delivery-graph",
            attempt=1,
            tools=self.tools,
        )
        child = self.registry.snapshot_for_turn(
            server="delivery-graph",
            workspace="G:/workspace/alpha",
            session_id="session-alpha",
            turn_id="child-turn-1",
            agent_role="child",
        )

        self.assertEqual(child["status"], "REGISTERED")
        self.assertEqual(child["catalogGeneration"], 1)
        self.assertEqual(child["toolCount"], 32)

    def test_reference_demo_covers_workspaces_and_agent_roles(self) -> None:
        demo = run_reference_demo()

        self.assertEqual(demo["architecture"], "EXTERNAL_SUPERVISOR_PER_TURN")
        self.assertEqual(
            {item["workspace"] for item in demo["turnMatrix"]},
            {"G:/workspace/alpha", "G:/workspace/beta"},
        )
        self.assertEqual(
            {item["agentRole"] for item in demo["turnMatrix"]},
            {"primary", "child"},
        )
        self.assertTrue(demo["assertions"]["sameSessionRecovered"])
        self.assertTrue(demo["assertions"]["activeTurnSnapshotImmutable"])
        self.assertTrue(demo["assertions"]["allNextTurnsRegistered"])
        self.assertFalse(demo["safety"]["modelInvocationStarted"])
        self.assertFalse(demo["safety"]["mcpToolCallAttempted"])
        self.assertFalse(demo["safety"]["governanceWriteAttempted"])


if __name__ == "__main__":
    unittest.main()
