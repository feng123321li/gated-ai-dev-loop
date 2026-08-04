from __future__ import annotations

import unittest
from unittest.mock import patch

from hdg.agent_discovery import available_agents
from hdg.controller import CONTROLLER_OPERATIONS
from hdg.mcp_tools import tool_definitions


class AgentDiscoveryBoundaryTests(unittest.TestCase):
    def test_available_agents_is_diagnostic_only(self) -> None:
        discovered = {
            "agents": [{"id": "codex", "displayName": "Codex"}],
            "summary": {"available": 1},
        }
        with patch(
            "hdg.agent_discovery.discover_available_agents",
            return_value=discovered,
        ) as discovery:
            result = available_agents(root="C:/workspace")

        self.assertEqual(result, discovered)
        discovery.assert_called_once_with()

    def test_model_recommendation_operation_is_removed(self) -> None:
        self.assertNotIn("recommend_executors", CONTROLLER_OPERATIONS)
        self.assertNotIn(
            "recommend_executors",
            {tool["name"] for tool in tool_definitions()},
        )


if __name__ == "__main__":
    unittest.main()
