from __future__ import annotations

from tempfile import TemporaryDirectory
import unittest

from hdg.assurance_recommendation import recommend_assurance_profile
from hdg.mcp_tools import READ_ONLY_TOOLS, call_tool, tool_definitions


class AssuranceRecommendationTests(unittest.TestCase):
    def test_recommends_light_only_for_single_local_low_risk_task(self) -> None:
        result = recommend_assurance_profile(
            task_summary="Rename one local label and run its focused test.",
            root_task_count=1,
            project_count=1,
            change_scope="LOCAL",
            risk_factors=[],
            verification_plan="TARGETED",
            risk_level="LOW",
        )

        self.assertEqual(result["recommendedProfile"], "LIGHT")
        self.assertTrue(result["lightEligible"])
        self.assertTrue(result["deterministic"])
        self.assertEqual(result["ruleVersion"], "assurance-v1")
        self.assertEqual(result["blockingRules"], [])

    def test_any_light_blocker_deterministically_recommends_standard(self) -> None:
        cases = (
            {"root_task_count": 2},
            {"project_count": 2},
            {"change_scope": "MULTI_MODULE"},
            {"risk_factors": ["PUBLIC_CONTRACT"]},
            {"verification_plan": "UNKNOWN"},
            {"risk_level": "MEDIUM"},
        )
        defaults = {
            "task_summary": "A bounded change.",
            "root_task_count": 1,
            "project_count": 1,
            "change_scope": "LOCAL",
            "risk_factors": [],
            "verification_plan": "TARGETED",
            "risk_level": "LOW",
        }
        for override in cases:
            with self.subTest(override=override):
                result = recommend_assurance_profile(**(defaults | override))
                self.assertEqual(result["recommendedProfile"], "STANDARD")
                self.assertFalse(result["lightEligible"])
                self.assertTrue(result["blockingRules"])

    def test_tool_is_read_only_and_exposed_through_controller(self) -> None:
        tools = {item["name"]: item for item in tool_definitions()}
        self.assertIn("recommend_assurance_profile", tools)
        self.assertIn("recommend_assurance_profile", READ_ONLY_TOOLS)
        self.assertTrue(
            tools["recommend_assurance_profile"]["annotations"]["readOnlyHint"]
        )
        with TemporaryDirectory() as temporary:
            result = call_tool(
                "recommend_assurance_profile",
                {
                    "task_summary": "One local documentation correction.",
                    "root_task_count": 1,
                    "project_count": 1,
                    "change_scope": "LOCAL",
                    "risk_factors": [],
                    "verification_plan": "TARGETED",
                    "risk_level": "LOW",
                },
                root=temporary,
            )
        self.assertEqual(result["recommendedProfile"], "LIGHT")

    def test_tool_schema_rejects_unknown_classification_values(self) -> None:
        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(Exception, "supported string"):
                call_tool(
                    "recommend_assurance_profile",
                    {
                        "task_summary": "One task.",
                        "root_task_count": 1,
                        "project_count": 1,
                        "change_scope": "SMALLISH",
                        "risk_factors": [],
                        "verification_plan": "TARGETED",
                        "risk_level": "LOW",
                    },
                    root=temporary,
                )


if __name__ == "__main__":
    unittest.main()
