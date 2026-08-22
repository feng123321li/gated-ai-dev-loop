from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from hdg.agent_profiles import (
    AGENT_PROFILE_CATALOG_FILE,
    built_in_agent_profile_catalog,
    load_agent_profile_catalog,
    profile_for_loop,
    team_plan_for_profile,
    validate_agent_profile_catalog,
)
from hdg.errors import GatedLoopError
from hdg.jsonio import pretty_json
from hdg.storage_schema import ensure_compatible_scheduler_storage


class AgentProfileCatalogTests(unittest.TestCase):
    def test_built_in_catalog_is_versioned_and_deterministic(self) -> None:
        first = built_in_agent_profile_catalog()
        second = built_in_agent_profile_catalog()

        self.assertEqual(first, second)
        self.assertEqual(first["catalogVersion"], 1)
        self.assertEqual(first["configurationSource"], "PLUGIN_BUILT_IN")
        self.assertRegex(first["catalogFingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            set(first["loopRoutes"]),
            {
                "TASK_LOOP",
                "TASK_REVIEW_LOOP",
                "GROUP_REVIEW_LOOP",
                "DELIVERY_REVIEW_LOOP",
            },
        )

    def test_task_profile_builds_owner_only_control_plane_team(self) -> None:
        catalog = built_in_agent_profile_catalog()
        profile = profile_for_loop(catalog, "TASK_LOOP")
        team = team_plan_for_profile(catalog, profile)

        self.assertEqual(profile["id"], "task-implementation")
        self.assertEqual(team["owner"]["profileId"], profile["id"])
        self.assertEqual(
            [item["profileId"] for item in team["helpers"]],
            ["codebase-researcher", "test-runner", "result-checker"],
        )
        self.assertEqual(
            {item["controlPlaneAccess"] for item in team["helpers"]},
            {"NONE"},
        )
        self.assertTrue(team["coordination"]["ownerSubmitsLoopResult"])
        self.assertFalse(team["coordination"]["helpersUseLifecycleTools"])

    def test_project_json_replaces_the_built_in_catalog(self) -> None:
        with TemporaryDirectory() as root:
            custom = built_in_agent_profile_catalog()
            custom.pop("catalogFingerprint")
            custom.pop("configurationSource")
            custom["profiles"][0]["capabilities"].append(
                "domain.billing"
            )
            Path(root, AGENT_PROFILE_CATALOG_FILE).write_text(
                pretty_json(custom),
                encoding="utf-8",
            )

            loaded = load_agent_profile_catalog(root)

        self.assertEqual(loaded["configurationSource"], "PROJECT_JSON")
        self.assertIn(
            "domain.billing",
            profile_for_loop(loaded, "TASK_LOOP")["capabilities"],
        )

    def test_unknown_helper_is_rejected(self) -> None:
        catalog = built_in_agent_profile_catalog()
        catalog.pop("catalogFingerprint")
        catalog.pop("configurationSource")
        catalog["profiles"][0]["helperProfiles"].append("missing")

        with self.assertRaises(GatedLoopError) as caught:
            validate_agent_profile_catalog(catalog)

        self.assertEqual(caught.exception.code, "AGENT_PROFILE_CATALOG_INVALID")

    def test_helper_does_not_expose_unenforced_concurrency_limit(self) -> None:
        catalog = built_in_agent_profile_catalog()
        helper = next(
            profile
            for profile in catalog["profiles"]
            if profile["kind"] == "HELPER"
        )
        self.assertNotIn("maxConcurrent", helper)

        catalog.pop("catalogFingerprint")
        catalog.pop("configurationSource")
        helper["maxConcurrent"] = 1
        with self.assertRaises(GatedLoopError) as caught:
            validate_agent_profile_catalog(catalog)

        self.assertEqual(caught.exception.code, "AGENT_PROFILE_CATALOG_INVALID")

    def test_receiver_role_skill_cannot_bypass_loop_boundary(self) -> None:
        catalog = built_in_agent_profile_catalog()
        catalog.pop("catalogFingerprint")
        catalog.pop("configurationSource")
        catalog["profiles"][0]["roleSkill"] = "delivery-graph-review"

        with self.assertRaises(GatedLoopError) as caught:
            validate_agent_profile_catalog(catalog)

        self.assertEqual(caught.exception.code, "AGENT_PROFILE_CATALOG_INVALID")

    def test_catalog_fingerprint_changes_with_profile_configuration(self) -> None:
        base = built_in_agent_profile_catalog()
        changed = deepcopy(base)
        changed.pop("catalogFingerprint")
        changed.pop("configurationSource")
        changed["profiles"][0]["maxConcurrent"] = 2

        validated = validate_agent_profile_catalog(changed)

        self.assertNotEqual(
            base["catalogFingerprint"],
            validated["catalogFingerprint"],
        )

    def test_documented_project_example_is_a_valid_complete_catalog(self) -> None:
        example = (
            Path(__file__).parents[1]
            / "docs"
            / "examples"
            / AGENT_PROFILE_CATALOG_FILE
        )

        loaded = load_agent_profile_catalog(example.parent)

        self.assertEqual(loaded["configurationSource"], "PROJECT_JSON")
        self.assertEqual(
            profile_for_loop(loaded, "DELIVERY_REVIEW_LOOP")["id"],
            "delivery-review",
        )

    def test_current_schema_adds_profile_binding_columns_non_destructively(
        self,
    ) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE dispatch_reservations("
            "status TEXT NOT NULL, expires_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE node_runs("
            "run_id TEXT, status TEXT, lease_expires_at TEXT)"
        )
        connection.execute(
            "CREATE TABLE graph_events("
            "event_id INTEGER, run_id TEXT, event_type TEXT)"
        )

        ensure_compatible_scheduler_storage(connection)
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(dispatch_reservations)"
            ).fetchall()
        }
        connection.close()

        self.assertTrue(
            {
                "agent_profile_id",
                "agent_catalog_fingerprint",
                "team_plan_fingerprint",
            }.issubset(columns)
        )


if __name__ == "__main__":
    unittest.main()
