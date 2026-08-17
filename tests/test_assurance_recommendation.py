from __future__ import annotations

import unittest

from hdg.controller import CONTROLLER_OPERATIONS
from hdg.mcp_tools import READ_ONLY_TOOLS, tool_definitions


class RemovedAssuranceRecommendationTests(unittest.TestCase):
    def test_planning_surface_has_no_agent_risk_recommendation(self) -> None:
        names = {item["name"] for item in tool_definitions()}

        self.assertNotIn("recommend_assurance_profile", names)
        self.assertNotIn("recommend_assurance_profile", READ_ONLY_TOOLS)
        self.assertNotIn(
            "recommend_assurance_profile",
            CONTROLLER_OPERATIONS,
        )


if __name__ == "__main__":
    unittest.main()
