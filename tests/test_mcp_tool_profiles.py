from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hdg.dispatch_contracts import RECEIVER_SKILLS, receiver_skill_prompt
from hdg.host_policy import ProjectRootBinding
from hdg.interaction_contract import manual_receiver_prompt
from hdg.loop_contracts import LOOP_KINDS
from hdg.mcp_adapter import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    MODERN_PROTOCOL_VERSION,
    PROTOCOL_VERSION_META_KEY,
    McpConnection,
    handle_message,
)
from hdg.mcp_catalog import (
    ALL_TOOL_PROFILE,
    DISPATCH_TOOL_PROFILE,
    PLANNING_TOOL_PROFILE,
    RECEIVER_TOOL_PROFILE,
    SKILL_TOOL_PROFILES,
    tool_definitions_for_profile,
    tool_names_for_profile,
)
from hdg.mcp_tools import tool_definitions


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "delivery-graph"
SKILLS = ROOT / "skills"

EXPECTED_PROFILE_TOOLS = {
    PLANNING_TOOL_PROFILE: {
        "workspace_status",
        "hierarchy_contract",
        "preview_hierarchy",
        "confirm_development_baseline",
        "select_execution_mode",
        "resume_execution_mode",
        "create_manual_handoff",
        "prepare_hierarchy",
        "prepare_delivery_revision",
        "delivery_revision_history",
        "freeze_hierarchy",
        "unfreeze_task_requirement",
        "refreeze_task_requirement",
        "record_user_confirmation",
        "archive_delivery",
    },
    DISPATCH_TOOL_PROFILE: {
        "workspace_status",
        "resume_execution_mode",
        "start_manual_handoff",
        "plan_dispatch_batch",
        "graph_frontier",
        "graph_status",
        "open_delivery_dashboard",
        "graph_events",
        "advance_graph",
        "rebuild_graph_run",
        "handoff_ready_automatic_task",
        "cancel_graph_run",
    },
    RECEIVER_TOOL_PROFILE: {
        "loop_context",
        "dispatch_loop",
        "heartbeat_loop",
        "report_loop_progress",
        "pause_loop",
        "resume_loop",
        "record_loop_result",
    },
}


def _modern_meta() -> dict[str, object]:
    return {
        PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION,
        CLIENT_CAPABILITIES_META_KEY: {},
        CLIENT_INFO_META_KEY: {
            "name": "profile-contract-test",
            "version": "1.0.0",
        },
    }


def _allowed_tools(path: Path) -> set[str]:
    document = path.read_text(encoding="utf-8")
    frontmatter = document.split("---", 2)[1]
    return {
        line.removeprefix("  - ")
        for line in frontmatter.splitlines()
        if line.startswith("  - ")
    }


class McpToolProfileTests(unittest.TestCase):
    def test_profiles_are_explicit_and_cover_the_scheduler_surface(self) -> None:
        all_names = {tool["name"] for tool in tool_definitions()}

        self.assertEqual(
            SKILL_TOOL_PROFILES,
            {
                "delivery-graph": PLANNING_TOOL_PROFILE,
                "delivery-graph-dispatch": DISPATCH_TOOL_PROFILE,
                "delivery-graph-task": RECEIVER_TOOL_PROFILE,
                "delivery-graph-review": RECEIVER_TOOL_PROFILE,
            },
        )
        for profile, expected in EXPECTED_PROFILE_TOOLS.items():
            with self.subTest(profile=profile):
                self.assertEqual(tool_names_for_profile(profile), expected)
                self.assertEqual(
                    {tool["name"] for tool in tool_definitions_for_profile(profile)},
                    expected,
                )
        self.assertEqual(
            set().union(*EXPECTED_PROFILE_TOOLS.values()),
            all_names,
        )
        self.assertEqual(tool_names_for_profile(ALL_TOOL_PROFILE), all_names)

    def test_tools_list_returns_only_the_connection_profile(self) -> None:
        with TemporaryDirectory() as root:
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root),
                tool_profile=DISPATCH_TOOL_PROFILE,
            )
            response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {"_meta": _modern_meta()},
                },
                connection=connection,
            )

        self.assertEqual(
            {tool["name"] for tool in response["result"]["tools"]},
            EXPECTED_PROFILE_TOOLS[DISPATCH_TOOL_PROFILE],
        )

    def test_tool_call_rejects_a_tool_outside_the_connection_profile(self) -> None:
        with TemporaryDirectory() as root:
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root),
                tool_profile=PLANNING_TOOL_PROFILE,
            )
            response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "dispatch_loop",
                        "arguments": {},
                        "_meta": _modern_meta(),
                    },
                },
                connection=connection,
            )

        self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(
            response["error"]["data"]["code"],
            "MCP_TOOL_OUTSIDE_PROFILE",
        )
        self.assertEqual(
            response["error"]["data"]["details"]["toolProfile"],
            PLANNING_TOOL_PROFILE,
        )

    def test_receiver_prompts_always_route_to_the_role_skill(self) -> None:
        self.assertIn(
            "$delivery-graph-task",
            receiver_skill_prompt("TASK_LOOP", [], host_adapter_id="codex"),
        )
        self.assertIn(
            "$delivery-graph-review",
            receiver_skill_prompt(
                "TASK_REVIEW_LOOP",
                [],
                host_adapter_id="codex",
            ),
        )
        zcode_prompt = receiver_skill_prompt(
            "GROUP_REVIEW_LOOP",
            [],
            host_adapter_id="zcode",
        )
        self.assertIn(
            "当前宿主是 ZCode，先通过原生 Skill tool 按 catalog 名 "
            "`delivery-graph-review` 调用角色 Skill。",
            zcode_prompt,
        )
        self.assertNotIn("Codex", zcode_prompt)
        handoff_prompt = manual_receiver_prompt(
            ".layered-delivery/d-1/handoff-test.md"
        )
        self.assertIn("delivery-graph-dispatch", handoff_prompt)
        self.assertIn("delivery-graph-task", handoff_prompt)
        self.assertIn("delivery-graph-review", handoff_prompt)

    def test_receiver_skill_routes_cover_every_loop_kind(self) -> None:
        self.assertEqual(
            RECEIVER_SKILLS,
            {
                loop_kind: (
                    "delivery-graph-task"
                    if loop_kind == "TASK_LOOP"
                    else "delivery-graph-review"
                )
                for loop_kind in LOOP_KINDS
            },
        )

    def test_plugin_registers_three_profiled_mcp_servers(self) -> None:
        manifests = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (
                PLUGIN / ".codex-plugin" / "plugin.json",
                PLUGIN / ".zcode-plugin" / "plugin.json",
                PLUGIN / ".mcp.json",
            )
        ]
        expected = {
            "delivery-graph": PLANNING_TOOL_PROFILE,
            "delivery-graph-dispatch": DISPATCH_TOOL_PROFILE,
            "delivery-graph-receiver": RECEIVER_TOOL_PROFILE,
        }

        for manifest in manifests:
            with self.subTest(manifest=manifest.get("name", "claude-mcp")):
                self.assertEqual(set(manifest["mcpServers"]), set(expected))
                for server_name, profile in expected.items():
                    args = manifest["mcpServers"][server_name]["args"]
                    self.assertEqual(
                        args[args.index("--tool-profile") + 1],
                        profile,
                    )

    def test_each_skill_declares_only_its_profile_tools(self) -> None:
        protected = {
            "archive_delivery",
            "cancel_graph_run",
            "handoff_ready_automatic_task",
            "rebuild_graph_run",
            "record_user_confirmation",
            "refreeze_task_requirement",
            "unfreeze_task_requirement",
        }
        prefixes = {
            PLANNING_TOOL_PROFILE: (
                "mcp__plugin_delivery-graph_delivery-graph__"
            ),
            DISPATCH_TOOL_PROFILE: (
                "mcp__plugin_delivery-graph_delivery-graph-dispatch__"
            ),
            RECEIVER_TOOL_PROFILE: (
                "mcp__plugin_delivery-graph_delivery-graph-receiver__"
            ),
        }

        for skill_name, profile in SKILL_TOOL_PROFILES.items():
            with self.subTest(skill=skill_name):
                expected = {
                    prefixes[profile] + name
                    for name in EXPECTED_PROFILE_TOOLS[profile] - protected
                }
                self.assertEqual(
                    _allowed_tools(SKILLS / skill_name / "SKILL.md"),
                    expected,
                )

    def test_plugin_payload_contains_all_four_skills(self) -> None:
        expected = set(SKILL_TOOL_PROFILES)
        canonical = {
            path.name
            for path in SKILLS.iterdir()
            if path.is_dir() and path.name in expected
        }
        bundled = {
            path.name
            for path in (PLUGIN / "skills").iterdir()
            if path.is_dir() and path.name in expected
        }

        self.assertEqual(canonical, expected)
        self.assertEqual(bundled, expected)


if __name__ == "__main__":
    unittest.main()
