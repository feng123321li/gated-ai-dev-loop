from __future__ import annotations

from pathlib import Path
import unittest

from hdg.controller import CONTROLLER_OPERATIONS
from hdg import dispatch_contracts
from hdg.mcp_tools import tool_definitions


ROOT = Path(__file__).resolve().parents[1]


class RemovedAgentDiscoveryBoundaryTests(unittest.TestCase):
    def test_agent_discovery_is_absent_from_all_runtime_layers(self) -> None:
        self.assertNotIn("available_agents", CONTROLLER_OPERATIONS)
        self.assertNotIn(
            "available_agents",
            {tool["name"] for tool in tool_definitions()},
        )
        for relative_path in (
            "src/hdg/agent_discovery.py",
            "skills/delivery-graph/scripts/hdg/agent_discovery.py",
            "plugins/delivery-graph/skills/delivery-graph/"
            "scripts/hdg/agent_discovery.py",
        ):
            with self.subTest(path=relative_path):
                self.assertFalse(Path(ROOT, relative_path).exists())
        for relative_path in (
            "plugins/delivery-graph/.codex-plugin/plugin.json",
            "plugins/delivery-graph/.claude-plugin/plugin.json",
        ):
            with self.subTest(manifest=relative_path):
                self.assertNotIn(
                    "agent-discovery",
                    Path(ROOT, relative_path).read_text(encoding="utf-8"),
                )

    def test_model_routing_discovery_symbols_are_removed(self) -> None:
        self.assertNotIn("recommend_executors", CONTROLLER_OPERATIONS)
        self.assertNotIn(
            "recommend_executors",
            {tool["name"] for tool in tool_definitions()},
        )
        self.assertFalse(hasattr(dispatch_contracts, "DISPATCH_TRANSPORTS"))


if __name__ == "__main__":
    unittest.main()
