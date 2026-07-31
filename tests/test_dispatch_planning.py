from __future__ import annotations

from copy import deepcopy
from tempfile import TemporaryDirectory
import unittest

from hdg.dispatch_planning import plan_dispatch_batch
from hdg.errors import GatedLoopError
from hdg.graph_model import loop_node_id, task_review_node_id
from hdg.graph_runtime import (
    dispatch_loop,
    graph_events,
    graph_status,
    record_loop_result,
)
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.mcp_tools import call_tool

from .test_loop_architecture import group_hierarchy, task_hierarchy


def host_executor_inventory(
    *,
    slots: int = 2,
    model_override_supported: bool = True,
) -> list[dict]:
    return [
        {
            "agentId": "codex",
            "displayName": "Codex",
            "capabilities": ["development", "review"],
            "availableSlots": slots,
            "priority": 20,
            "modelOverrideSupported": model_override_supported,
            "models": [
                {
                    "id": "gpt-5.6-sol",
                    "family": "gpt-5.6",
                    "tier": "FRONTIER",
                    "reasoningEffort": "high",
                    "priority": 10,
                },
                {
                    "id": "gpt-5.6-terra",
                    "family": "gpt-5.6",
                    "tier": "BALANCED",
                    "reasoningEffort": "medium",
                    "priority": 20,
                },
            ],
        }
    ]


def diverse_host_executor_inventory() -> list[dict]:
    return [
        *host_executor_inventory(),
        {
            "agentId": "claude-code",
            "displayName": "Claude Code",
            "capabilities": ["review"],
            "availableSlots": 1,
            "priority": 10,
            "modelOverrideSupported": True,
            "models": [
                {
                    "id": "claude-opus",
                    "family": "claude",
                    "tier": "FRONTIER",
                    "reasoningEffort": "high",
                    "priority": 10,
                }
            ],
        },
    ]


def claude_host_executor_inventory() -> list[dict]:
    return [
        {
            "agentId": "claude-code",
            "displayName": "Claude Code",
            "capabilities": ["development", "review"],
            "availableSlots": 2,
            "priority": 20,
            "modelOverrideSupported": True,
            "models": [
                {
                    "id": "claude-sonnet",
                    "family": "claude",
                    "tier": "BALANCED",
                    "reasoningEffort": "medium",
                    "priority": 20,
                },
                {
                    "id": "claude-opus",
                    "family": "claude",
                    "tier": "FRONTIER",
                    "reasoningEffort": "high",
                    "priority": 10,
                },
            ],
        }
    ]


def parallel_group_hierarchy() -> dict:
    hierarchy = deepcopy(group_hierarchy())
    task = hierarchy["root"]["children"][1]["definition"]
    task["execution"]["dependsOn"] = []
    return hierarchy


def success(summary: str) -> dict:
    return {
        "status": "SUCCEEDED",
        "summary": summary,
        "result": {},
    }


def agent_requirement(
    node_id: str,
    *,
    reasoning_class: str = "STANDARD",
    reason: str = "Host Agent classified this Loop for dispatch.",
) -> dict:
    return {
        "nodeId": node_id,
        "reasoningClass": reasoning_class,
        "source": "PLANNING",
        "reason": reason,
    }


class HostDispatchPlanningTests(unittest.TestCase):
    def prepare_and_freeze(self, root: str, hierarchy: dict) -> dict:
        prepared = prepare_hierarchy(root=root, hierarchy=hierarchy)
        freeze_hierarchy(
            root=root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
        )
        return prepared

    def test_plans_parallel_tasks_with_explicit_terra_model(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(
                root,
                parallel_group_hierarchy(),
            )

            plan = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    agent_requirement(loop_node_id("t-api")),
                    agent_requirement(loop_node_id("t-core")),
                ],
            )

            state = graph_status(root=root, root_id=prepared["rootId"])

        self.assertEqual(len(plan["assignments"]), 2)
        self.assertTrue(plan["summary"]["concurrent"])
        self.assertEqual(plan["summary"]["dispatchable"], 2)
        self.assertEqual(plan["summary"]["deferred"], 0)
        self.assertTrue(
            all(
                assignment["agent"]["id"] == "codex"
                and assignment["model"]["id"] == "gpt-5.6-terra"
                and assignment["modelSelection"] == "EXPLICIT_OVERRIDE"
                and assignment["hostDispatchAllowed"]
                for assignment in plan["assignments"]
            )
        )
        self.assertEqual(
            {
                assignment["nodeId"]
                for assignment in plan["assignments"]
            },
            {loop_node_id("t-api"), loop_node_id("t-core")},
        )
        self.assertRegex(plan["planFingerprint"], r"^[0-9a-f]{64}$")
        self.assertTrue(
            all(
                node["status"] == "READY"
                for node in state["nodes"]
                if node["nodeId"] in {
                    loop_node_id("t-api"),
                    loop_node_id("t-core"),
                }
            )
        )

    def test_every_ready_loop_requires_agent_reasoning_analysis(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())

            with self.assertRaises(GatedLoopError) as caught:
                plan_dispatch_batch(
                    root=root,
                    root_id=prepared["rootId"],
                    expected_graph_fingerprint=(
                        prepared["graphFingerprint"]
                    ),
                    executor_inventory=host_executor_inventory(),
                    node_requirements=[],
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DISPATCH_REQUIREMENT_MISSING",
        )

    def test_missing_analysis_uses_current_executor_fallback(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())

            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(
                    model_override_supported=False
                ),
                node_requirements=[],
                current_executor={
                    "agentId": "codex",
                    "modelId": "gpt-5.6-sol",
                },
            )["assignments"][0]

        self.assertEqual(assignment["agent"]["id"], "codex")
        self.assertEqual(assignment["model"]["id"], "gpt-5.6-sol")
        self.assertEqual(assignment["reasoningClass"], "UNCLASSIFIED")
        self.assertEqual(
            assignment["routingBasis"],
            "CURRENT_EXECUTOR_FALLBACK",
        )
        self.assertEqual(
            assignment["modelSelection"],
            "CURRENT_HOST_DEFAULT",
        )

    def test_current_executor_fallback_is_fingerprint_verified_and_audited(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=[],
                current_executor={
                    "agentId": "codex",
                    "modelId": "gpt-5.6-sol",
                },
            )["assignments"][0]

            dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=assignment["nodeId"],
                owner="codex-current-receiver",
                agent_id="codex",
                model_id="gpt-5.6-sol",
                dispatch_mode="AUTO",
                dispatch_reasoning_class="UNCLASSIFIED",
                dispatch_decision_fingerprint=(
                    assignment["decisionFingerprint"]
                ),
                operation_id="op-current-fallback-audit",
            )
            state = graph_status(root=root, root_id=prepared["rootId"])

        claimed = next(
            node
            for node in state["nodes"]
            if node["nodeId"] == assignment["nodeId"]
        )
        self.assertEqual(claimed["dispatchMode"], "AUTO")
        self.assertEqual(
            claimed["dispatchReasoningClass"],
            "UNCLASSIFIED",
        )
        self.assertEqual(
            claimed["dispatchDecisionFingerprint"],
            assignment["decisionFingerprint"],
        )

    def test_current_executor_capacity_is_reserved_for_missing_analysis(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(
                root,
                parallel_group_hierarchy(),
            )

            plan = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(slots=1),
                node_requirements=[
                    agent_requirement(loop_node_id("t-api"))
                ],
                current_executor={
                    "agentId": "codex",
                    "modelId": "gpt-5.6-sol",
                },
            )

        self.assertEqual(
            [assignment["nodeId"] for assignment in plan["assignments"]],
            [loop_node_id("t-core")],
        )
        self.assertEqual(
            plan["assignments"][0]["routingBasis"],
            "CURRENT_EXECUTOR_FALLBACK",
        )

    def test_available_slots_limit_batch_without_claiming_deferred_loop(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(
                root,
                parallel_group_hierarchy(),
            )

            plan = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(slots=1),
                node_requirements=[
                    agent_requirement(loop_node_id("t-api")),
                    agent_requirement(loop_node_id("t-core")),
                ],
            )
            state = graph_status(root=root, root_id=prepared["rootId"])

        self.assertEqual(len(plan["assignments"]), 1)
        self.assertEqual(len(plan["deferred"]), 1)
        self.assertEqual(
            plan["deferred"][0]["code"],
            "NO_HOST_EXECUTOR_CAPACITY",
        )
        self.assertTrue(
            all(
                node["status"] == "READY"
                for node in state["nodes"]
                if node["nodeId"] in {
                    loop_node_id("t-api"),
                    loop_node_id("t-core"),
                }
            )
        )

    def test_model_override_is_required_for_automatic_dispatch(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())

            plan = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(
                    model_override_supported=False,
                ),
                node_requirements=[
                    agent_requirement(loop_node_id("t-service"))
                ],
            )

        self.assertEqual(plan["assignments"], [])
        self.assertEqual(
            plan["deferred"][0]["code"],
            "MODEL_OVERRIDE_UNAVAILABLE",
        )
        self.assertFalse(plan["summary"]["concurrent"])

    def test_high_reasoning_task_uses_frontier_model(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())

            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    {
                        "nodeId": loop_node_id("t-service"),
                        "reasoningClass": "HIGH",
                        "source": "PLANNING",
                        "reason": (
                            "Cross-module architecture and migration risk."
                        ),
                    }
                ],
            )["assignments"][0]

        self.assertEqual(assignment["model"]["id"], "gpt-5.6-sol")
        self.assertEqual(assignment["reasoningClass"], "HIGH")
        self.assertEqual(
            assignment["reasoningRequirement"]["source"],
            "PLANNING",
        )
        self.assertIn(
            "Cross-module architecture",
            assignment["reasoningRequirement"]["reason"],
        )

    def test_model_tiers_are_mapped_by_each_host_inventory(self) -> None:
        cases = (
            (
                [agent_requirement(loop_node_id("t-service"))],
                "claude-sonnet",
                "STANDARD",
            ),
            (
                [
                    {
                        "nodeId": loop_node_id("t-service"),
                        "reasoningClass": "HIGH",
                        "source": "USER_POLICY",
                        "reason": "Require frontier reasoning.",
                    }
                ],
                "claude-opus",
                "HIGH",
            ),
        )
        for requirements, expected_model, reasoning_class in cases:
            with self.subTest(reasoning_class=reasoning_class):
                with TemporaryDirectory() as root:
                    prepared = self.prepare_and_freeze(
                        root,
                        task_hierarchy(),
                    )
                    assignment = plan_dispatch_batch(
                        root=root,
                        root_id=prepared["rootId"],
                        expected_graph_fingerprint=(
                            prepared["graphFingerprint"]
                        ),
                        executor_inventory=(
                            claude_host_executor_inventory()
                        ),
                        node_requirements=requirements,
                    )["assignments"][0]

                self.assertEqual(
                    assignment["model"]["id"],
                    expected_model,
                )
                self.assertEqual(
                    assignment["reasoningClass"],
                    reasoning_class,
                )

    def test_high_reasoning_node_is_deferred_without_frontier_model(
        self,
    ) -> None:
        inventory = host_executor_inventory()
        inventory[0]["models"] = [
            model
            for model in inventory[0]["models"]
            if model["tier"] == "BALANCED"
        ]
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())

            plan = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=inventory,
                node_requirements=[
                    {
                        "nodeId": loop_node_id("t-service"),
                        "reasoningClass": "HIGH",
                        "source": "LOOP_POLICY",
                        "reason": "This Loop requires frontier reasoning.",
                    }
                ],
            )

        self.assertEqual(plan["assignments"], [])
        self.assertEqual(
            plan["deferred"][0]["code"],
            "NO_HIGH_REASONING_MODEL",
        )

    def test_task_uses_balanced_model_and_review_uses_frontier_model(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            task_plan = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    agent_requirement(loop_node_id("t-service"))
                ],
            )
            task_assignment = task_plan["assignments"][0]
            dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=task_assignment["nodeId"],
                owner="codex-task-terra",
                agent_id=task_assignment["agent"]["id"],
                model_id=task_assignment["model"]["id"],
                dispatch_mode="AUTO",
                dispatch_reasoning_class=(
                    task_assignment["reasoningClass"]
                ),
                dispatch_decision_fingerprint=(
                    task_assignment["decisionFingerprint"]
                ),
                operation_id="op-terra-task",
            )
            record_loop_result(
                root=root,
                root_id=prepared["rootId"],
                node_id=task_assignment["nodeId"],
                operation_id="op-terra-task",
                outcome=success("TASK completed with terra."),
            )

            review_plan = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    agent_requirement(
                        task_review_node_id("t-service"),
                        reasoning_class="HIGH",
                        reason=(
                            "Host Agent classified independent Review "
                            "as high reasoning."
                        ),
                    )
                ],
            )

        self.assertEqual(
            task_assignment["model"]["id"],
            "gpt-5.6-terra",
        )
        self.assertEqual(len(review_plan["assignments"]), 1)
        review = review_plan["assignments"][0]
        self.assertEqual(
            review["nodeId"],
            task_review_node_id("t-service"),
        )
        self.assertEqual(review["model"]["id"], "gpt-5.6-sol")
        self.assertEqual(review["role"], "INDEPENDENT_REVIEW")
        self.assertFalse(review["independence"]["modelDiverse"])

    def test_auto_claim_records_actual_model_and_dispatch_decision(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    agent_requirement(loop_node_id("t-service"))
                ],
            )["assignments"][0]

            dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=assignment["nodeId"],
                owner="codex-terra-receiver",
                agent_id=assignment["agent"]["id"],
                model_id=assignment["model"]["id"],
                dispatch_mode="AUTO",
                dispatch_reasoning_class=assignment["reasoningClass"],
                dispatch_decision_fingerprint=(
                    assignment["decisionFingerprint"]
                ),
                operation_id="op-auto-audit",
            )
            state = graph_status(root=root, root_id=prepared["rootId"])
            events = graph_events(root=root, root_id=prepared["rootId"])

        claimed = next(
            node
            for node in state["nodes"]
            if node["nodeId"] == assignment["nodeId"]
        )
        self.assertEqual(claimed["agentId"], "codex")
        self.assertEqual(claimed["modelId"], "gpt-5.6-terra")
        self.assertEqual(claimed["dispatchMode"], "AUTO")
        self.assertEqual(claimed["dispatchReasoningClass"], "STANDARD")
        self.assertEqual(
            claimed["dispatchDecisionFingerprint"],
            assignment["decisionFingerprint"],
        )
        event = next(
            event
            for event in events["events"]
            if event["eventType"] == "LOOP_CLAIMED"
        )
        self.assertEqual(event["payload"]["dispatchMode"], "AUTO")
        self.assertEqual(
            event["payload"]["dispatchReasoningClass"],
            "STANDARD",
        )
        self.assertEqual(
            event["payload"]["dispatchDecisionFingerprint"],
            assignment["decisionFingerprint"],
        )

    def test_auto_claim_requires_a_dispatch_decision_fingerprint(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())

            with self.assertRaises(GatedLoopError) as caught:
                dispatch_loop(
                    root=root,
                    root_id=prepared["rootId"],
                    node_id=loop_node_id("t-service"),
                    owner="codex-terra-receiver",
                    agent_id="codex",
                    model_id="gpt-5.6-terra",
                    dispatch_mode="AUTO",
                    dispatch_reasoning_class="STANDARD",
                    operation_id="op-auto-missing-decision",
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DISPATCH_DECISION_REQUIRED",
        )

    def test_auto_claim_rejects_a_decision_for_another_route(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())

            with self.assertRaises(GatedLoopError) as caught:
                dispatch_loop(
                    root=root,
                    root_id=prepared["rootId"],
                    node_id=loop_node_id("t-service"),
                    owner="codex-terra-receiver",
                    agent_id="codex",
                    model_id="gpt-5.6-terra",
                    dispatch_mode="AUTO",
                    dispatch_reasoning_class="STANDARD",
                    dispatch_decision_fingerprint="f" * 64,
                    operation_id="op-auto-wrong-decision",
                )

            state = graph_status(root=root, root_id=prepared["rootId"])

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DISPATCH_DECISION_MISMATCH",
        )
        task = next(
            node
            for node in state["nodes"]
            if node["nodeId"] == loop_node_id("t-service")
        )
        self.assertEqual(task["status"], "READY")

    def test_review_prefers_a_different_agent_and_model_family(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            task_assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=diverse_host_executor_inventory(),
                node_requirements=[
                    agent_requirement(loop_node_id("t-service"))
                ],
            )["assignments"][0]
            dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=task_assignment["nodeId"],
                owner="codex-task-terra",
                agent_id=task_assignment["agent"]["id"],
                model_id=task_assignment["model"]["id"],
                dispatch_mode="AUTO",
                dispatch_reasoning_class=(
                    task_assignment["reasoningClass"]
                ),
                dispatch_decision_fingerprint=(
                    task_assignment["decisionFingerprint"]
                ),
                operation_id="op-diverse-task",
            )
            record_loop_result(
                root=root,
                root_id=prepared["rootId"],
                node_id=task_assignment["nodeId"],
                operation_id="op-diverse-task",
                outcome=success("TASK completed."),
            )

            review = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=diverse_host_executor_inventory(),
                node_requirements=[
                    agent_requirement(
                        task_review_node_id("t-service"),
                        reasoning_class="HIGH",
                    )
                ],
            )["assignments"][0]

        self.assertEqual(review["agent"]["id"], "claude-code")
        self.assertEqual(review["model"]["id"], "claude-opus")
        self.assertTrue(review["independence"]["agentDiverse"])
        self.assertTrue(review["independence"]["modelDiverse"])

    def test_mcp_tool_returns_a_host_dispatch_plan(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())

            plan = call_tool(
                "plan_dispatch_batch",
                {
                    "root_id": prepared["rootId"],
                    "expected_graph_fingerprint": (
                        prepared["graphFingerprint"]
                    ),
                    "executor_inventory": host_executor_inventory(),
                    "node_requirements": [
                        {
                            "nodeId": loop_node_id("t-service"),
                            "reasoningClass": "HIGH",
                            "source": "USER_POLICY",
                            "reason": "Use frontier reasoning for this TASK.",
                        }
                    ],
                },
                root=root,
            )

        self.assertEqual(plan["binding"], "HOST_NATIVE_DISPATCH_PLAN")
        self.assertEqual(
            plan["assignments"][0]["model"]["id"],
            "gpt-5.6-sol",
        )

    def test_stale_graph_fingerprint_is_rejected(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())

            with self.assertRaises(GatedLoopError) as caught:
                plan_dispatch_batch(
                    root=root,
                    root_id=prepared["rootId"],
                    expected_graph_fingerprint="f" * 64,
                    executor_inventory=host_executor_inventory(),
                    node_requirements=[
                        agent_requirement(loop_node_id("t-service"))
                    ],
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_GRAPH_FINGERPRINT_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
