from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hdg.agent_discovery import discover_available_agents
from hdg.agent_recommendation import (
    recommend_graph_executors,
    recommend_executors,
)
from hdg.graph_model import (
    compile_delivery_graph,
    graph_fingerprint,
)
from hdg.model_core import (
    hierarchy_fingerprint,
    validate_hierarchy_definition,
)
from hdg.mcp_tools import call_tool
from hdg.planning import prepare_hierarchy
from hdg.planning import freeze_hierarchy
from hdg.dispatch_planning import plan_dispatch_batch
from hdg.repository import SchedulerRepository

from .test_loop_architecture import group_hierarchy


def executable_lookup(commands: dict[str, str]):
    def lookup(command: str) -> str | None:
        return commands.get(command)

    return lookup


def version_lookup(versions: dict[str, str]):
    def lookup(executable: str) -> str | None:
        return versions.get(executable)

    return lookup


def discovered_agent(
    agent_id: str,
    *,
    display_name: str | None = None,
    model_id: str | None = None,
    priority: int = 0,
    capabilities: tuple[str, ...] = ("development", "review"),
) -> dict:
    return {
        "id": agent_id,
        "displayName": display_name or agent_id,
        "command": agent_id,
        "version": "1.0.0",
        "source": "AUTO",
        "capabilities": list(capabilities),
        "priority": priority,
        "model": {
            "id": model_id,
            "provider": None,
            "reasoningEffort": None,
            "source": "UNRESOLVED" if model_id is None else "PROFILE",
        },
    }


def host_executor_inventory(agent_id: str) -> list[dict]:
    if agent_id == "codex":
        models = [
            ("gpt-5.6-luna", "EFFICIENT", "low", 30),
            ("gpt-5.6-terra", "BALANCED", "medium", 20),
            ("gpt-5.6-sol", "FRONTIER", "high", 10),
        ]
        display_name = "Codex"
    else:
        models = [
            ("claude-haiku", "EFFICIENT", "low", 30),
            ("claude-sonnet", "BALANCED", "medium", 20),
            ("claude-opus", "FRONTIER", "high", 10),
        ]
        display_name = "Claude Code"
    return [
        {
            "agentId": agent_id,
            "adapterId": agent_id,
            "displayName": display_name,
            "dispatchTransport": "HOST_NATIVE",
            "capabilities": ["development", "review"],
            "availableSlots": 4,
            "priority": 20,
            "nativeModelSelectionSupported": True,
            "models": [
                {
                    "id": model_id,
                    "family": agent_id,
                    "tier": tier,
                    "reasoningEffort": effort,
                    "priority": priority,
                }
                for model_id, tier, effort, priority in models
            ],
        }
    ]


def graph_requirements(graph: dict) -> list[dict]:
    return [
        {
            "nodeId": node["id"],
            "reasoningClass": (
                "ROUTINE" if node["kind"] == "TASK_LOOP" else "HIGH"
            ),
            "source": "PLANNING",
            "reason": "按工作类型选择当前执行 Agent 的原生模型档位。",
        }
        for node in graph["nodes"]
        if node["kind"].endswith("_LOOP")
    ]


class AgentDiscoveryTests(unittest.TestCase):
    def test_discovers_codex_and_ccswitch_model_without_secrets(self) -> None:
        with TemporaryDirectory() as directory:
            home = Path(directory)
            codex_home = home / ".codex"
            claude_home = home / ".claude"
            codex_home.mkdir()
            claude_home.mkdir()
            (codex_home / "config.toml").write_text(
                'model = "gpt-5.6-sol"\n'
                'model_reasoning_effort = "xhigh"\n'
                'api_key = "must-not-leak"\n',
                encoding="utf-8",
            )
            (claude_home / "settings.json").write_text(
                json.dumps(
                    {
                        "env": {
                            "ANTHROPIC_MODEL": "glm-5",
                            "ANTHROPIC_AUTH_TOKEN": "must-not-leak",
                            "ANTHROPIC_BASE_URL": "https://private.invalid",
                        }
                    }
                ),
                encoding="utf-8",
            )
            agents = discover_available_agents(
                home=home,
                environ={},
                which=executable_lookup(
                    {
                        "codex": "bin/codex",
                        "claude": "bin/claude",
                    }
                ),
                version_reader=version_lookup(
                    {
                        "bin/codex": "codex-cli 0.145.0",
                        "bin/claude": "2.1.220",
                    }
                ),
            )

        by_id = {agent["id"]: agent for agent in agents["agents"]}
        self.assertEqual(set(by_id), {"claude-code", "codex"})
        self.assertEqual(by_id["codex"]["model"]["id"], "gpt-5.6-sol")
        self.assertEqual(
            by_id["codex"]["model"]["reasoningEffort"],
            "xhigh",
        )
        self.assertEqual(by_id["claude-code"]["model"]["id"], "glm-5")
        self.assertEqual(
            by_id["claude-code"]["model"]["provider"],
            "zhipu",
        )
        serialized = json.dumps(agents, ensure_ascii=False)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("private.invalid", serialized)
        self.assertNotIn(str(home), serialized)

    def test_ccswitch_model_changes_are_observed_on_each_discovery(self) -> None:
        with TemporaryDirectory() as directory:
            home = Path(directory)
            claude_home = home / ".claude"
            claude_home.mkdir()
            settings = claude_home / "settings.json"
            lookup = executable_lookup({"claude": "bin/claude"})
            versions = version_lookup({"bin/claude": "2.1.220"})

            settings.write_text(
                json.dumps({"env": {"ANTHROPIC_MODEL": "glm-5"}}),
                encoding="utf-8",
            )
            first = discover_available_agents(
                home=home,
                environ={},
                which=lookup,
                version_reader=versions,
            )
            settings.write_text(
                json.dumps(
                    {"env": {"ANTHROPIC_MODEL": "deepseek-reasoner"}}
                ),
                encoding="utf-8",
            )
            second = discover_available_agents(
                home=home,
                environ={},
                which=lookup,
                version_reader=versions,
            )

        self.assertEqual(first["agents"][0]["model"]["id"], "glm-5")
        self.assertEqual(
            second["agents"][0]["model"]["id"],
            "deepseek-reasoner",
        )
        self.assertEqual(
            second["agents"][0]["model"]["provider"],
            "deepseek",
        )

    def test_user_profile_supports_arbitrary_agent_and_model(self) -> None:
        with TemporaryDirectory() as directory:
            home = Path(directory)
            profile_path = home / "agents.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "profiles": [
                            {
                                "id": "team-terminal",
                                "displayName": "Team Terminal",
                                "command": "team-agent",
                                "model": "custom-deepseek-v3",
                                "reasoningEffort": "high",
                                "capabilities": [
                                    "development",
                                    "review",
                                ],
                                "priority": 40,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = discover_available_agents(
                home=home,
                environ={
                    "LAYERED_DELIVERY_AGENT_PROFILES": str(profile_path)
                },
                which=executable_lookup(
                    {"team-agent": "bin/team-agent"}
                ),
                version_reader=version_lookup(
                    {"bin/team-agent": "team-agent 9.1"}
                ),
            )

        self.assertEqual(len(result["agents"]), 1)
        profile = result["agents"][0]
        self.assertEqual(profile["id"], "team-terminal")
        self.assertEqual(profile["model"]["id"], "custom-deepseek-v3")
        self.assertEqual(profile["priority"], 40)
        self.assertEqual(profile["source"], "USER_PROFILE")


class AgentRecommendationTests(unittest.TestCase):
    @staticmethod
    def graph() -> dict:
        hierarchy = validate_hierarchy_definition(group_hierarchy())
        return compile_delivery_graph(
            hierarchy,
            hierarchy_fingerprint=hierarchy_fingerprint(hierarchy),
        )

    def test_recommends_each_task_and_review_without_dispatching(self) -> None:
        graph = self.graph()
        result = recommend_graph_executors(
            graph,
            [
                discovered_agent(
                    "codex",
                    display_name="Codex",
                    model_id="gpt-5.6-terra",
                    priority=20,
                ),
                discovered_agent(
                    "claude-code",
                    display_name="Claude Code",
                    model_id="glm-5",
                    priority=10,
                ),
            ],
        )

        loop_nodes = {
            node["id"]
            for node in graph["nodes"]
            if node["kind"].endswith("_LOOP")
        }
        recommendations = {
            item["nodeId"]: item
            for item in result["recommendations"]
        }
        self.assertEqual(set(recommendations), loop_nodes)
        for recommendation in recommendations.values():
            self.assertEqual(recommendation["binding"], "ADVISORY")
            self.assertFalse(recommendation["dispatchAllowed"])
            self.assertTrue(recommendation["reasons"])
            self.assertEqual(
                recommendation["recommended"]["availabilityScope"],
                "LOCAL_TERMINAL",
            )
            self.assertEqual(
                recommendation["recommended"]["dispatchTransport"],
                "EXTERNAL_PROCESS",
            )
            self.assertFalse(
                recommendation["recommended"]["hostDispatchEligible"]
            )

        task_agents = {
            recommendation["recommended"]["agentId"]
            for recommendation in recommendations.values()
            if recommendation["role"] == "DEVELOPMENT"
        }
        review_agents = {
            recommendation["recommended"]["agentId"]
            for recommendation in recommendations.values()
            if recommendation["role"] == "INDEPENDENT_REVIEW"
        }
        self.assertEqual(task_agents, {"codex"})
        self.assertEqual(review_agents, {"claude-code"})
        self.assertTrue(
            all(
                recommendation["independence"]["satisfied"]
                for recommendation in recommendations.values()
                if recommendation["role"] == "INDEPENDENT_REVIEW"
            )
        )

    def test_single_agent_review_reports_unsatisfied_independence(self) -> None:
        result = recommend_graph_executors(
            self.graph(),
            [discovered_agent("codex", model_id="gpt-5.6-sol")],
        )
        reviews = [
            recommendation
            for recommendation in result["recommendations"]
            if recommendation["role"] == "INDEPENDENT_REVIEW"
        ]

        self.assertTrue(reviews)
        self.assertTrue(
            all(
                recommendation["recommended"]["agentId"] == "codex"
                for recommendation in reviews
            )
        )
        self.assertTrue(
            all(
                not recommendation["independence"]["satisfied"]
                for recommendation in reviews
            )
        )
        self.assertTrue(
            all(
                recommendation["confidence"] == "LOW"
                for recommendation in reviews
            )
        )

    def test_equal_task_candidates_use_medium_confidence_fallback(self) -> None:
        result = recommend_graph_executors(
            self.graph(),
            [
                discovered_agent(
                    "claude-code",
                    model_id="glm-5",
                ),
                discovered_agent(
                    "codex",
                    model_id="gpt-5.6-sol",
                ),
            ],
        )
        tasks = [
            recommendation
            for recommendation in result["recommendations"]
            if recommendation["role"] == "DEVELOPMENT"
        ]

        self.assertTrue(tasks)
        self.assertTrue(
            all(
                recommendation["confidence"] == "MEDIUM"
                for recommendation in tasks
            )
        )
        self.assertTrue(
            all(
                "STABLE_FALLBACK_ORDER"
                in {
                    reason["code"]
                    for reason in recommendation["reasons"]
                }
                for recommendation in tasks
            )
        )

    def test_recommendation_does_not_interpret_or_expose_loop_payload(self) -> None:
        graph = self.graph()
        changed = deepcopy(graph)
        for node in changed["nodes"]:
            if node["loop"] is not None:
                node["loop"]["payload"] = {
                    "model": "force-another-agent",
                    "secret": "do-not-expose",
                }
        agents = [discovered_agent("codex", model_id="current-model")]

        original = recommend_graph_executors(graph, agents)
        modified = recommend_graph_executors(changed, agents)

        self.assertEqual(original, modified)
        self.assertNotIn(
            "do-not-expose",
            json.dumps(modified, ensure_ascii=False),
        )

    def test_repository_operation_recommends_prepared_graph_live(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=group_hierarchy(),
            )
            graph = SchedulerRepository(root).hierarchy(
                prepared["rootId"]
            )["graph"]
            result = recommend_executors(
                root=root,
                root_id=prepared["rootId"],
                recommendation_mode="AUTOMATIC",
                executor_inventory=host_executor_inventory("codex"),
                node_requirements=graph_requirements(graph),
                host_native_agent_ids=("codex",),
                host_adapter_id="codex",
            )

        self.assertEqual(
            result["graphFingerprint"],
            prepared["graphFingerprint"],
        )
        self.assertEqual(
            result["recommendationPolicy"]["selectionScope"],
            "CURRENT_EXECUTION_AGENT_ONLY",
        )
        self.assertFalse(
            result["recommendationPolicy"][
                "crossAgentRecommendationAllowed"
            ]
        )
        self.assertTrue(result["recommendations"])
        self.assertEqual(
            {
                item["recommended"]["agentId"]
                for item in result["recommendations"]
            },
            {"codex"},
        )
        task_models = {
            item["recommended"]["model"]["id"]
            for item in result["recommendations"]
            if item["role"] == "DEVELOPMENT"
        }
        review_models = {
            item["recommended"]["model"]["id"]
            for item in result["recommendations"]
            if item["role"] == "INDEPENDENT_REVIEW"
        }
        self.assertEqual(task_models, {"gpt-5.6-luna"})
        self.assertEqual(review_models, {"gpt-5.6-sol"})
        self.assertRegex(graph_fingerprint(self.graph()), r"^[0-9a-f]{64}$")

    def test_automatic_recommendation_matches_later_dispatch_route(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=group_hierarchy(),
            )
            stored = SchedulerRepository(root).hierarchy(
                prepared["rootId"]
            )
            requirements = graph_requirements(stored["graph"])
            preview = recommend_executors(
                root=root,
                root_id=prepared["rootId"],
                recommendation_mode="AUTOMATIC",
                executor_inventory=host_executor_inventory("codex"),
                node_requirements=requirements,
                host_native_agent_ids=("codex",),
                host_adapter_id="codex",
            )
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                execution_mode="active",
                confirmed=True,
                confirmed_by="human",
            )
            task_preview = next(
                item
                for item in preview["recommendations"]
                if item["role"] == "DEVELOPMENT"
                and item["nodeId"]
                in {
                    requirement["nodeId"]
                    for requirement in requirements
                }
            )
            plan = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory("codex"),
                node_requirements=[
                    requirement
                    for requirement in requirements
                    if requirement["nodeId"] == task_preview["nodeId"]
                ],
                host_native_agent_ids=("codex",),
                host_adapter_id="codex",
            )

        assignment = plan["assignments"][0]
        self.assertEqual(
            assignment["agent"]["id"],
            task_preview["recommended"]["agentId"],
        )
        self.assertEqual(
            assignment["model"]["id"],
            task_preview["recommended"]["model"]["id"],
        )
        self.assertEqual(
            assignment["decisionFingerprint"],
            task_preview["decisionFingerprint"],
        )

    def test_claude_automatic_recommendation_uses_native_roles_only(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=group_hierarchy(),
            )
            graph = SchedulerRepository(root).hierarchy(
                prepared["rootId"]
            )["graph"]
            result = recommend_executors(
                root=root,
                root_id=prepared["rootId"],
                recommendation_mode="AUTOMATIC",
                executor_inventory=host_executor_inventory("claude-code"),
                node_requirements=graph_requirements(graph),
                host_native_agent_ids=("claude-code",),
                host_adapter_id="claude-code",
            )

        self.assertEqual(
            {
                item["recommended"]["agentId"]
                for item in result["recommendations"]
            },
            {"claude-code"},
        )
        self.assertEqual(
            {
                item["recommended"]["model"]["id"]
                for item in result["recommendations"]
                if item["role"] == "DEVELOPMENT"
            },
            {"claude-haiku"},
        )
        self.assertNotIn("actualModelId", json.dumps(result))

    def test_manual_receiver_can_regenerate_native_model_list_read_only(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=group_hierarchy(),
            )
            graph = SchedulerRepository(root).hierarchy(
                prepared["rootId"]
            )["graph"]
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                execution_mode="manual",
                confirmed=True,
                confirmed_by="human",
            )
            result = recommend_executors(
                root=root,
                root_id=prepared["rootId"],
                recommendation_mode="AUTOMATIC",
                executor_inventory=host_executor_inventory("codex"),
                node_requirements=graph_requirements(graph),
                host_native_agent_ids=("codex",),
                host_adapter_id="codex",
            )

        self.assertEqual(result["executionMode"], "manual")
        self.assertFalse(
            result["recommendationPolicy"]["automaticDispatchAllowed"]
        )
        self.assertEqual(
            result["recommendationPolicy"]["use"],
            "RECEIVER_LOCAL_EXECUTION_PREVIEW",
        )

    def test_manual_handoff_recommends_a_different_development_agent(self) -> None:
        discovery = {
            "agents": [
                discovered_agent(
                    "codex",
                    model_id="gpt-5.6-terra",
                ),
                discovered_agent(
                    "claude-code",
                    model_id="glm-5.2",
                ),
            ],
            "warnings": [],
            "discoveryFingerprint": "f" * 64,
        }
        with TemporaryDirectory() as root:
            prepared = call_tool(
                "prepare_hierarchy",
                {"hierarchy": group_hierarchy()},
                root=root,
            )
            with patch(
                "hdg.agent_recommendation.discover_available_agents",
                return_value=discovery,
            ):
                agents = call_tool(
                    "available_agents",
                    {},
                    root=root,
                )
                advice = call_tool(
                    "recommend_executors",
                    {
                        "root_id": prepared["rootId"],
                        "recommendation_mode": "MANUAL_HANDOFF",
                        "manual_development_agent_id": "claude-code",
                    },
                    root=root,
                    host_adapter_id="codex",
                )

        self.assertEqual(agents, discovery)
        self.assertTrue(advice["recommendations"])
        self.assertTrue(
            all(
                item["recommended"]["agentId"] == "claude-code"
                and not item["dispatchAllowed"]
                for item in advice["recommendations"]
                if item["role"] == "DEVELOPMENT"
            )
        )
        self.assertEqual(
            advice["recommendationPolicy"]["binding"],
            "MANUAL_HANDOFF_ADVISORY",
        )
        self.assertTrue(
            advice["recommendationPolicy"][
                "crossAgentRecommendationAllowed"
            ]
        )
        self.assertFalse(
            advice["recommendationPolicy"]["automaticDispatchAllowed"]
        )


if __name__ == "__main__":
    unittest.main()
