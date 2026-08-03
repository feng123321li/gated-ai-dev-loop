from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from hdg.dispatch_planning import plan_dispatch_batch
from hdg.errors import GatedLoopError
from hdg.graph_frontier import get_graph_frontier
from hdg.graph_model import loop_node_id, task_review_node_id
from hdg.graph_runtime import (
    advance_graph,
    attest_loop_receiver,
    claim_codex_subagent_receiver,
    dispatch_loop,
    graph_events,
    graph_status,
    heartbeat_loop,
    rebuild_graph_run,
    record_loop_result,
)
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.mcp_tools import call_tool
from hdg.orchestrator_config import OrchestratorConfig
from hdg.repository import SchedulerRepository

from .test_loop_architecture import group_hierarchy, task_hierarchy


def host_executor_inventory(
    *,
    slots: int = 2,
    model_override_supported: bool = True,
    dispatch_transport: str = "HOST_NATIVE",
) -> list[dict]:
    return [
        {
            "agentId": "codex",
            "displayName": "Codex",
            "dispatchTransport": dispatch_transport,
            "capabilities": ["development", "review"],
            "availableSlots": slots,
            "priority": 20,
            "modelOverrideSupported": model_override_supported,
            "models": [
                {
                    "id": "gpt-5.6-luna",
                    "family": "gpt-5.6",
                    "tier": "EFFICIENT",
                    "reasoningEffort": "low",
                    "priority": 30,
                },
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
            "dispatchTransport": "HOST_NATIVE",
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
            "dispatchTransport": "HOST_NATIVE",
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
            execution_mode="active",
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
                and assignment["dispatchTransport"] == "HOST_NATIVE"
                and assignment["modelSelection"] == "EXPLICIT_OVERRIDE"
                and assignment["hostDispatchAllowed"]
                and assignment["hostTaskName"]
                == "ld_"
                + assignment["dispatchReservationId"].replace("-", "")
                and assignment["contextInput"]["hostTaskName"]
                == assignment["hostTaskName"]
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

    def test_default_orchestrator_policy_is_safe_and_visible(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())

            policy = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    agent_requirement(loop_node_id("t-service"))
                ],
            )["dispatchPolicy"]

        self.assertTrue(policy["automaticOrchestration"])
        self.assertTrue(policy["autoSelectModel"])
        self.assertFalse(policy["allowCrossAdapterDispatch"])
        self.assertEqual(policy["allowedAdapters"], ["codex", "claude-code"])
        self.assertEqual(policy["maxConcurrentExecutors"], 4)
        self.assertEqual(policy["quotaExhaustionPolicy"], "PAUSE_AND_RESUME")
        self.assertTrue(policy["preferDifferentAdapterForReview"])

    def test_configuration_can_disable_automatic_orchestration(self) -> None:
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
                    node_requirements=[
                        agent_requirement(loop_node_id("t-service"))
                    ],
                    orchestrator_config=OrchestratorConfig(
                        automatic_orchestration=False
                    ),
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_AUTOMATIC_ORCHESTRATION_DISABLED",
        )

    def test_disabling_model_selection_uses_current_executor(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())

            plan = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    agent_requirement(
                        loop_node_id("t-service"),
                        reasoning_class="HIGH",
                    )
                ],
                current_executor={
                    "agentId": "codex",
                    "modelId": "gpt-5.6-terra",
                },
                orchestrator_config=OrchestratorConfig(
                    auto_select_model=False
                ),
            )

        assignment = plan["assignments"][0]
        self.assertEqual(assignment["model"]["id"], "gpt-5.6-terra")
        self.assertEqual(assignment["reasoningClass"], "UNCLASSIFIED")
        self.assertEqual(
            assignment["modelSelection"],
            "CURRENT_HOST_DEFAULT",
        )

    def test_configured_max_concurrency_is_reserved_atomically(self) -> None:
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
                orchestrator_config=OrchestratorConfig(
                    max_concurrent_executors=1
                ),
            )

        self.assertEqual(len(plan["assignments"]), 1)
        self.assertEqual(len(plan["deferred"]), 1)
        self.assertEqual(
            plan["deferred"][0]["code"],
            "ORCHESTRATOR_CAPACITY_RESERVED",
        )

    def test_unlisted_native_adapter_is_rejected(self) -> None:
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
                    node_requirements=[
                        agent_requirement(loop_node_id("t-service"))
                    ],
                    orchestrator_config=OrchestratorConfig(
                        allowed_adapters=("claude-code",)
                    ),
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_ADAPTER_NOT_ALLOWED",
        )

    def test_adapter_allowlist_is_independent_from_agent_id(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            inventory = host_executor_inventory()
            inventory[0]["agentId"] = "codex-worker"
            inventory[0]["adapterId"] = "codex"

            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=inventory,
                node_requirements=[
                    agent_requirement(loop_node_id("t-service"))
                ],
                host_adapter_id="codex",
            )["assignments"][0]

        self.assertEqual(assignment["agent"]["id"], "codex-worker")
        self.assertEqual(assignment["agent"]["adapterId"], "codex")

    def test_cross_delivery_plans_share_atomic_host_slots(self) -> None:
        with TemporaryDirectory() as root:
            prepared_deliveries = []
            for delivery_id, task_id in (
                ("d-first", "t-first"),
                ("d-second", "t-second"),
            ):
                workspace = Path(root, f"worktree-{delivery_id}")
                workspace.mkdir()
                hierarchy = task_hierarchy()
                hierarchy["delivery"]["id"] = delivery_id
                hierarchy["root"]["definition"]["id"] = task_id
                prepared = prepare_hierarchy(
                    root=root,
                    hierarchy=hierarchy,
                    workspace_root=str(workspace),
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
                prepared_deliveries.append((prepared, task_id))

            first, first_task = prepared_deliveries[0]
            second, second_task = prepared_deliveries[1]
            first_plan = plan_dispatch_batch(
                root=root,
                root_id=first["rootId"],
                expected_graph_fingerprint=first["graphFingerprint"],
                executor_inventory=host_executor_inventory(slots=1),
                node_requirements=[
                    agent_requirement(loop_node_id(first_task))
                ],
            )
            first_assignment = first_plan["assignments"][0]
            dispatch_loop(
                root=root,
                root_id=first["rootId"],
                node_id=first_assignment["nodeId"],
                owner="codex-first-receiver",
                agent_id=first_assignment["agent"]["id"],
                model_id=first_assignment["model"]["id"],
                receiver_context_id="codex-first-context",
                dispatch_mode="AUTO",
                dispatch_transport=first_assignment["dispatchTransport"],
                dispatch_reservation_id=first_assignment[
                    "dispatchReservationId"
                ],
                dispatch_reasoning_class=first_assignment[
                    "reasoningClass"
                ],
                dispatch_decision_fingerprint=first_assignment[
                    "decisionFingerprint"
                ],
                operation_id="op-first-claimed-slot",
            )
            second_plan = plan_dispatch_batch(
                root=root,
                root_id=second["rootId"],
                expected_graph_fingerprint=second["graphFingerprint"],
                executor_inventory=host_executor_inventory(slots=1),
                node_requirements=[
                    agent_requirement(loop_node_id(second_task))
                ],
            )

        self.assertEqual(first_plan["summary"]["dispatchable"], 1)
        self.assertEqual(second_plan["summary"]["dispatchable"], 0)
        self.assertEqual(
            second_plan["deferred"][0]["code"],
            "DISPATCH_AGENT_CAPACITY_RESERVED",
        )

    def test_trusted_host_adapter_limits_native_agent_inventory(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            arguments = {
                "root_id": prepared["rootId"],
                "expected_graph_fingerprint": prepared[
                    "graphFingerprint"
                ],
                "executor_inventory": claude_host_executor_inventory(),
                "node_requirements": [
                    agent_requirement(loop_node_id("t-service"))
                ],
            }
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "plan_dispatch_batch",
                    arguments,
                    root=root,
                    trusted_host_adapter="codex",
                    client_info={"name": "claude-code", "version": "1"},
                )
            with self.assertRaises(GatedLoopError) as missing:
                call_tool(
                    "plan_dispatch_batch",
                    arguments,
                    root=root,
                    client_info={"name": "claude-code", "version": "1"},
                )
            claude_plan = call_tool(
                "plan_dispatch_batch",
                arguments,
                root=root,
                trusted_host_adapter="claude-code",
                client_info={"name": "codex", "version": "1"},
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_HOST_NATIVE_INVENTORY_MISMATCH",
        )
        self.assertEqual(
            missing.exception.code,
            "SCHEDULER_HOST_NATIVE_INVENTORY_MISMATCH",
        )
        self.assertEqual(
            claude_plan["assignments"][0]["agent"]["id"],
            "claude-code",
        )

    def test_mcp_dispatch_consumes_host_attestation_once(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
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
            node_id = loop_node_id("t-service")
            arguments = {
                "root_id": prepared["rootId"],
                "node_id": node_id,
                "owner": "claude-child",
                "agent_id": "claude-code",
                "model_id": "claude-sonnet",
                "dispatch_mode": "MANUAL",
                "receiver_context_id": "agent-child-1",
                "operation_id": "op-attested-child",
            }
            with self.assertRaises(GatedLoopError) as missing:
                call_tool(
                    "dispatch_loop",
                    {
                        **arguments,
                        "receiver_attestation_id": "missing-attestation",
                    },
                    root=root,
                    trusted_host_adapter="claude-code",
                )
            attestation = attest_loop_receiver(
                root=root,
                root_id=prepared["rootId"],
                node_id=node_id,
                receiver_context_id="agent-child-1",
                parent_context_id="session-parent",
                host_adapter_id="claude-code",
            )
            claimed = call_tool(
                "dispatch_loop",
                {
                    **arguments,
                    "receiver_attestation_id": attestation[
                        "receiverAttestationId"
                    ],
                },
                root=root,
                trusted_host_adapter="claude-code",
            )
            with self.assertRaises(GatedLoopError) as replayed:
                call_tool(
                    "dispatch_loop",
                    {
                        **arguments,
                        "operation_id": "op-attestation-replay",
                        "receiver_attestation_id": attestation[
                            "receiverAttestationId"
                        ],
                    },
                    root=root,
                    trusted_host_adapter="claude-code",
                )

        self.assertEqual(
            missing.exception.code,
            "SCHEDULER_RECEIVER_ATTESTATION_MISSING",
        )
        self.assertEqual(claimed["receiverContextId"], "agent-child-1")
        self.assertEqual(
            replayed.exception.code,
            "SCHEDULER_RECEIVER_ATTESTATION_CONSUMED",
        )

    def test_codex_identity_cannot_cross_delivery_workspaces(self) -> None:
        with TemporaryDirectory() as root:
            deliveries = []
            for delivery_id, task_id in (
                ("d-codex-first", "t-codex-first"),
                ("d-codex-second", "t-codex-second"),
            ):
                workspace = Path(root, f"workspace-{delivery_id}")
                workspace.mkdir()
                hierarchy = task_hierarchy()
                hierarchy["delivery"]["id"] = delivery_id
                hierarchy["root"]["definition"]["id"] = task_id
                prepared = prepare_hierarchy(
                    root=root,
                    hierarchy=hierarchy,
                    workspace_root=str(workspace),
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
                assignment = plan_dispatch_batch(
                    root=root,
                    root_id=prepared["rootId"],
                    expected_graph_fingerprint=prepared[
                        "graphFingerprint"
                    ],
                    executor_inventory=host_executor_inventory(),
                    node_requirements=[
                        agent_requirement(loop_node_id(task_id))
                    ],
                )["assignments"][0]
                deliveries.append(
                    (prepared, assignment, str(workspace))
                )

            first, first_assignment, first_workspace = deliveries[0]
            second, second_assignment, _second_workspace = deliveries[1]
            claim_codex_subagent_receiver(
                root=root,
                root_id=first["rootId"],
                workspace_root=first_workspace,
                receiver_context_id="codex-first-child",
                parent_context_id="codex-orchestrator",
                model_id=first_assignment["model"]["id"],
                dispatch_reservation_id=first_assignment[
                    "dispatchReservationId"
                ],
            )
            with self.assertRaises(GatedLoopError) as crossed:
                claim_codex_subagent_receiver(
                    root=root,
                    root_id=second["rootId"],
                    workspace_root=first_workspace,
                    receiver_context_id="codex-first-child",
                    parent_context_id="codex-orchestrator",
                    model_id=second_assignment["model"]["id"],
                    dispatch_reservation_id=second_assignment[
                        "dispatchReservationId"
                    ],
                )

        self.assertEqual(
            crossed.exception.code,
            "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
        )

    def test_parallel_codex_children_cannot_swap_reservations(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(
                root,
                parallel_group_hierarchy(),
            )
            assignments = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared[
                    "graphFingerprint"
                ],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    agent_requirement(loop_node_id("t-api")),
                    agent_requirement(loop_node_id("t-core")),
                ],
            )["assignments"]
            claimed = []
            for index, assignment in enumerate(assignments):
                claimed.append(
                    claim_codex_subagent_receiver(
                        root=root,
                        root_id=prepared["rootId"],
                        workspace_root=root,
                        receiver_context_id=f"codex-child-{index}",
                        parent_context_id="codex-orchestrator",
                        model_id=assignment["model"]["id"],
                        dispatch_reservation_id=assignment[
                            "dispatchReservationId"
                        ],
                    )
                )

            _first_assignment, second_assignment = assignments
            with self.assertRaises(GatedLoopError) as swapped:
                claim_codex_subagent_receiver(
                    root=root,
                    root_id=prepared["rootId"],
                    workspace_root=root,
                    receiver_context_id="codex-child-0",
                    parent_context_id="codex-orchestrator",
                    model_id=second_assignment["model"]["id"],
                    dispatch_reservation_id=second_assignment[
                        "dispatchReservationId"
                    ],
                )

        self.assertEqual(
            swapped.exception.code,
            "SCHEDULER_CODEX_RECEIVER_RESERVATION_MISSING",
        )
        self.assertEqual(
            {item["nodeId"] for item in claimed},
            {assignment["nodeId"] for assignment in assignments},
        )
        self.assertTrue(all(item["receiverAttested"] for item in claimed))

    def test_first_successful_codex_claim_pins_orchestrator_parent(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(
                root,
                parallel_group_hierarchy(),
            )
            assignments = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared[
                    "graphFingerprint"
                ],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    agent_requirement(loop_node_id("t-api")),
                    agent_requirement(loop_node_id("t-core")),
                ],
            )["assignments"]
            first, second = assignments
            claim_codex_subagent_receiver(
                root=root,
                root_id=prepared["rootId"],
                workspace_root=root,
                receiver_context_id="codex-first-child",
                parent_context_id="codex-orchestrator",
                model_id=first["model"]["id"],
                dispatch_reservation_id=first[
                    "dispatchReservationId"
                ],
            )
            with self.assertRaises(GatedLoopError) as untrusted:
                claim_codex_subagent_receiver(
                    root=root,
                    root_id=prepared["rootId"],
                    workspace_root=root,
                    receiver_context_id="codex-wrong-parent-child",
                    parent_context_id="another-codex-session",
                    model_id=second["model"]["id"],
                    dispatch_reservation_id=second[
                        "dispatchReservationId"
                    ],
                )
            accepted = claim_codex_subagent_receiver(
                root=root,
                root_id=prepared["rootId"],
                workspace_root=root,
                receiver_context_id="codex-second-child",
                parent_context_id="codex-orchestrator",
                model_id=second["model"]["id"],
                dispatch_reservation_id=second[
                    "dispatchReservationId"
                ],
            )

        self.assertEqual(
            untrusted.exception.code,
            "SCHEDULER_RECEIVER_PARENT_UNTRUSTED",
        )
        self.assertEqual(accepted["nodeId"], second["nodeId"])

    def test_worker_lost_retry_rotates_dead_claude_orchestrator_root(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            first_assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=claude_host_executor_inventory(),
                node_requirements=[
                    agent_requirement(loop_node_id("t-service"))
                ],
            )["assignments"][0]
            first_attestation = attest_loop_receiver(
                root=root,
                root_id=prepared["rootId"],
                node_id=first_assignment["nodeId"],
                receiver_context_id="claude-first-child",
                parent_context_id="claude-dead-session",
                host_adapter_id="claude-code",
                dispatch_reservation_id=first_assignment[
                    "dispatchReservationId"
                ],
            )
            first_claim = dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=first_assignment["nodeId"],
                owner="claude-first-child",
                operation_id="claude-first-operation",
                agent_id="claude-code",
                model_id=first_assignment["model"]["id"],
                receiver_context_id="claude-first-child",
                receiver_attestation_id=first_attestation[
                    "receiverAttestationId"
                ],
                dispatch_mode="AUTO",
                dispatch_transport="HOST_NATIVE",
                dispatch_reservation_id=first_assignment[
                    "dispatchReservationId"
                ],
                dispatch_reasoning_class=first_assignment[
                    "reasoningClass"
                ],
                dispatch_decision_fingerprint=first_assignment[
                    "decisionFingerprint"
                ],
                host_adapter_id="claude-code",
                require_receiver_attestation=True,
            )
            retry_at = datetime.fromisoformat(
                first_claim["leaseExpiresAt"].replace("Z", "+00:00")
            ) + timedelta(seconds=1)
            advanced = advance_graph(
                root=root,
                root_id=prepared["rootId"],
                now=retry_at,
            )
            retried = next(
                item
                for item in advanced["nodes"]
                if item["nodeId"] == first_assignment["nodeId"]
                and item["attempt"] == 2
            )
            self.assertEqual(retried["status"], "READY")

            second_assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=claude_host_executor_inventory(),
                node_requirements=[
                    agent_requirement(first_assignment["nodeId"])
                ],
                now=retry_at + timedelta(seconds=1),
            )["assignments"][0]
            with self.assertRaises(GatedLoopError) as cross_adapter:
                attest_loop_receiver(
                    root=root,
                    root_id=prepared["rootId"],
                    node_id=second_assignment["nodeId"],
                    receiver_context_id="codex-cross-adapter-child",
                    parent_context_id="codex-recovery-session",
                    host_adapter_id="codex",
                    dispatch_reservation_id=second_assignment[
                        "dispatchReservationId"
                    ],
                    now=retry_at + timedelta(seconds=2),
                )
            second_attestation = attest_loop_receiver(
                root=root,
                root_id=prepared["rootId"],
                node_id=second_assignment["nodeId"],
                receiver_context_id="claude-recovery-child",
                parent_context_id="claude-recovery-session",
                host_adapter_id="claude-code",
                dispatch_reservation_id=second_assignment[
                    "dispatchReservationId"
                ],
                now=retry_at + timedelta(seconds=3),
            )
            recovered = dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=second_assignment["nodeId"],
                owner="claude-recovery-child",
                operation_id="claude-recovery-operation",
                agent_id="claude-code",
                model_id=second_assignment["model"]["id"],
                receiver_context_id="claude-recovery-child",
                receiver_attestation_id=second_attestation[
                    "receiverAttestationId"
                ],
                dispatch_mode="AUTO",
                dispatch_transport="HOST_NATIVE",
                dispatch_reservation_id=second_assignment[
                    "dispatchReservationId"
                ],
                dispatch_reasoning_class=second_assignment[
                    "reasoningClass"
                ],
                dispatch_decision_fingerprint=second_assignment[
                    "decisionFingerprint"
                ],
                host_adapter_id="claude-code",
                require_receiver_attestation=True,
                now=retry_at + timedelta(seconds=4),
            )
            events = graph_events(
                root=root,
                root_id=prepared["rootId"],
            )["events"]
            rotations = [
                event
                for event in events
                if event["eventType"] == "RECEIVER_ROOT_ROTATED"
            ]
            rebuilt = rebuild_graph_run(
                root=root,
                root_id=prepared["rootId"],
            )

        self.assertEqual(recovered["nodeId"], second_assignment["nodeId"])
        self.assertEqual(
            cross_adapter.exception.code,
            "SCHEDULER_RECEIVER_PARENT_UNTRUSTED",
        )
        self.assertEqual(len(rotations), 1)
        self.assertEqual(rotations[0]["attempt"], 2)
        self.assertEqual(
            rotations[0]["payload"]["reason"],
            "WORKER_LOST_RETRY",
        )
        self.assertNotIn(
            "claude-dead-session",
            str(rotations[0]["payload"]),
        )
        rebuilt_attempt = next(
            item
            for item in rebuilt["nodes"]
            if item["nodeId"] == second_assignment["nodeId"]
            and item["attempt"] == 2
        )
        self.assertEqual(rebuilt_attempt["status"], "CLAIMED")

    def test_worker_lost_retry_cannot_rotate_while_another_loop_is_claimed(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(
                root,
                parallel_group_hierarchy(),
            )
            assignments = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    agent_requirement(loop_node_id("t-api")),
                    agent_requirement(loop_node_id("t-core")),
                ],
            )["assignments"]
            first, second = assignments
            first_claim = claim_codex_subagent_receiver(
                root=root,
                root_id=prepared["rootId"],
                workspace_root=root,
                receiver_context_id="codex-first-worker",
                parent_context_id="codex-original-session",
                model_id=first["model"]["id"],
                dispatch_reservation_id=first["dispatchReservationId"],
            )
            second_claim = claim_codex_subagent_receiver(
                root=root,
                root_id=prepared["rootId"],
                workspace_root=root,
                receiver_context_id="codex-second-worker",
                parent_context_id="codex-original-session",
                model_id=second["model"]["id"],
                dispatch_reservation_id=second["dispatchReservationId"],
            )
            first_expiry = datetime.fromisoformat(
                first_claim["leaseExpiresAt"].replace("Z", "+00:00")
            )
            heartbeat_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=second["nodeId"],
                operation_id=second_claim["operationId"],
                now=first_expiry - timedelta(seconds=1),
            )
            retry_at = first_expiry + timedelta(seconds=1)
            advance_graph(
                root=root,
                root_id=prepared["rootId"],
                now=retry_at,
            )
            retry_assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=[agent_requirement(first["nodeId"])],
                now=retry_at + timedelta(seconds=1),
            )["assignments"][0]
            with self.assertRaises(GatedLoopError) as caught:
                claim_codex_subagent_receiver(
                    root=root,
                    root_id=prepared["rootId"],
                    workspace_root=root,
                    receiver_context_id="codex-takeover-worker",
                    parent_context_id="codex-takeover-session",
                    model_id=retry_assignment["model"]["id"],
                    dispatch_reservation_id=retry_assignment[
                        "dispatchReservationId"
                    ],
                    now=retry_at + timedelta(seconds=2),
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_RECEIVER_PARENT_UNTRUSTED",
        )

    def test_codex_claim_recovers_after_projection_failure(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared[
                    "graphFingerprint"
                ],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    agent_requirement(loop_node_id("t-service"))
                ],
            )["assignments"][0]
            with mock.patch.object(
                SchedulerRepository,
                "write_projections",
                side_effect=OSError("projection unavailable"),
            ):
                claimed = claim_codex_subagent_receiver(
                    root=root,
                    root_id=prepared["rootId"],
                    workspace_root=root,
                    receiver_context_id="codex-projection-child",
                    parent_context_id="codex-orchestrator",
                    model_id=assignment["model"]["id"],
                    dispatch_reservation_id=assignment[
                        "dispatchReservationId"
                    ],
                )
            replayed = claim_codex_subagent_receiver(
                root=root,
                root_id=prepared["rootId"],
                workspace_root=root,
                receiver_context_id="codex-projection-child",
                parent_context_id="codex-orchestrator",
                model_id=assignment["model"]["id"],
                dispatch_reservation_id=assignment[
                    "dispatchReservationId"
                ],
            )

        self.assertEqual(claimed["nodeId"], assignment["nodeId"])
        self.assertEqual(claimed["operationId"], replayed["operationId"])
        self.assertTrue(claimed["receiverAttested"])

    def test_failed_codex_claim_transaction_does_not_pin_root(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared[
                    "graphFingerprint"
                ],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    agent_requirement(loop_node_id("t-service"))
                ],
            )["assignments"][0]
            repository = SchedulerRepository(root)
            with self.assertRaises(RuntimeError):
                with repository.transaction() as connection:
                    reservation = connection.execute(
                        "SELECT * FROM dispatch_reservations "
                        "WHERE reservation_id = ?",
                        (assignment["dispatchReservationId"],),
                    ).fetchone()
                    identity = repository.issue_host_receiver_identity(
                        connection,
                        run_id=reservation["run_id"],
                        root_id=prepared["rootId"],
                        node_id=reservation["node_id"],
                        attempt=reservation["attempt"],
                        reservation_id=reservation["reservation_id"],
                        host_adapter_id="codex",
                        agent_id="codex",
                        model_id=reservation["model_id"],
                        receiver_context_id="codex-rolled-back-child",
                        parent_context_id="codex-rolled-back-parent",
                        at=reservation["reserved_at"],
                    )
                    repository.consume_receiver_attestation(
                        connection,
                        attestation_id=identity,
                        run_id=reservation["run_id"],
                        root_id=prepared["rootId"],
                        node_id=reservation["node_id"],
                        attempt=reservation["attempt"],
                        receiver_context_id="codex-rolled-back-child",
                        host_adapter_id="codex",
                        agent_id="codex",
                        model_id=reservation["model_id"],
                        reservation_id=reservation["reservation_id"],
                        operation_id="codex-rolled-back-operation",
                        at=reservation["reserved_at"],
                    )
                    raise RuntimeError("force claim rollback")
            with repository.transaction() as connection:
                root_count = connection.execute(
                    "SELECT COUNT(*) FROM run_receiver_roots"
                ).fetchone()[0]
                identity_count = connection.execute(
                    "SELECT COUNT(*) FROM host_receiver_identities"
                ).fetchone()[0]

        self.assertEqual(root_count, 0)
        self.assertEqual(identity_count, 0)

    def test_unconsumed_claude_attestation_does_not_pin_root(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
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
            attest_loop_receiver(
                root=root,
                root_id=prepared["rootId"],
                node_id=loop_node_id("t-service"),
                receiver_context_id="claude-unconsumed-child",
                parent_context_id="claude-unconsumed-session",
                host_adapter_id="claude-code",
            )
            repository = SchedulerRepository(root)
            with repository.read() as connection:
                root_count = connection.execute(
                    "SELECT COUNT(*) FROM run_receiver_roots"
                ).fetchone()[0]

        self.assertEqual(root_count, 0)

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
                dispatch_transport=assignment["dispatchTransport"],
                dispatch_reservation_id=assignment[
                    "dispatchReservationId"
                ],
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

    def test_external_process_executor_is_deferred_without_claim(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())

            plan = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(
                    dispatch_transport="EXTERNAL_PROCESS",
                ),
                node_requirements=[
                    agent_requirement(loop_node_id("t-service"))
                ],
            )
            state = graph_status(root=root, root_id=prepared["rootId"])

        self.assertEqual(plan["assignments"], [])
        self.assertEqual(
            plan["deferred"][0]["code"],
            "UNSAFE_EXECUTOR_TRANSPORT",
        )
        self.assertFalse(plan["deferred"][0]["claimCreated"])
        self.assertFalse(
            plan["dispatchPolicy"]["externalProcessLaunchAllowed"]
        )
        task = next(
            node
            for node in state["nodes"]
            if node["nodeId"] == loop_node_id("t-service")
        )
        self.assertEqual(task["status"], "READY")

    def test_dispatch_plan_reserves_before_host_agent_creation(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            requirements = [
                agent_requirement(loop_node_id("t-service"))
            ]

            first = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=requirements,
            )
            frontier = get_graph_frontier(
                root=root,
                root_id=prepared["rootId"],
            )
            second = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=requirements,
            )

        assignment = first["assignments"][0]
        self.assertRegex(
            assignment["dispatchReservationId"],
            r"^[0-9a-f-]{36}$",
        )
        self.assertTrue(assignment["reservationExpiresAt"])
        wait = next(
            action
            for action in frontier["actions"]
            if action["nodeId"] == assignment["nodeId"]
        )
        self.assertEqual(wait["action"], "WAIT_FOR_DISPATCH_RECEIVER")
        self.assertEqual(
            wait["dispatchReservationId"],
            assignment["dispatchReservationId"],
        )
        self.assertEqual(second["assignments"], [])
        self.assertEqual(
            second["deferred"][0]["code"],
            "DISPATCH_ALREADY_RESERVED",
        )

    def test_dispatch_reservation_holds_resource_before_claim(self) -> None:
        hierarchy = parallel_group_hierarchy()
        for child in hierarchy["root"]["children"]:
            child["definition"]["execution"]["loop"][
                "resourceClaims"
            ] = ["database:shared"]
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, hierarchy)
            first = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    agent_requirement(loop_node_id("t-api"))
                ],
            )["assignments"][0]
            other_node = (
                loop_node_id("t-core")
                if first["nodeId"] == loop_node_id("t-api")
                else loop_node_id("t-api")
            )

            with self.assertRaises(GatedLoopError) as caught:
                dispatch_loop(
                    root=root,
                    root_id=prepared["rootId"],
                    node_id=other_node,
                    owner="manual-competing-receiver",
                    agent_id="codex",
                    model_id="gpt-5.6-terra",
                    dispatch_mode="MANUAL",
                    operation_id="op-manual-competing-reservation",
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_EXECUTION_MODE_MISMATCH",
        )

    def test_active_execution_rejects_manual_or_implicit_claims(self) -> None:
        for dispatch_mode in (None, "MANUAL"):
            with self.subTest(dispatch_mode=dispatch_mode):
                with TemporaryDirectory() as root:
                    prepared = self.prepare_and_freeze(
                        root,
                        task_hierarchy(),
                    )
                    with self.assertRaises(GatedLoopError) as caught:
                        dispatch_loop(
                            root=root,
                            root_id=prepared["rootId"],
                            node_id=loop_node_id("t-service"),
                            owner="unplanned-receiver",
                            agent_id="claude-code",
                            model_id="unplanned-model",
                            dispatch_mode=dispatch_mode,
                            operation_id=(
                                f"op-unplanned-{dispatch_mode or 'none'}"
                            ),
                        )
                    self.assertEqual(
                        caught.exception.code,
                        "SCHEDULER_EXECUTION_MODE_MISMATCH",
                    )

    def test_review_rejects_the_upstream_receiving_context(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
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
            task_node_id = loop_node_id("t-service")
            review_node_id = task_review_node_id("t-service")
            dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=task_node_id,
                owner="task-agent",
                receiver_context_id="context-shared",
                operation_id="op-task-context",
            )
            record_loop_result(
                root=root,
                root_id=prepared["rootId"],
                node_id=task_node_id,
                operation_id="op-task-context",
                outcome=success("implemented"),
            )

            with self.assertRaises(GatedLoopError) as caught:
                dispatch_loop(
                    root=root,
                    root_id=prepared["rootId"],
                    node_id=review_node_id,
                    owner="review-agent",
                    receiver_context_id="context-shared",
                    operation_id="op-review-context",
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_REVIEW_CONTEXT_NOT_INDEPENDENT",
        )

    def test_receiver_attestation_rejects_non_orchestrator_parent(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
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
            task_node_id = loop_node_id("t-service")
            task_attestation = attest_loop_receiver(
                root=root,
                root_id=prepared["rootId"],
                node_id=task_node_id,
                receiver_context_id="context-implementation",
                parent_context_id="context-orchestrator",
                host_adapter_id="claude-code",
            )
            call_tool(
                "dispatch_loop",
                {
                    "root_id": prepared["rootId"],
                    "node_id": task_node_id,
                    "owner": "claude-implementation",
                    "agent_id": "claude-code",
                    "model_id": "claude-sonnet",
                    "dispatch_mode": "MANUAL",
                    "receiver_context_id": "context-implementation",
                    "receiver_attestation_id": task_attestation[
                        "receiverAttestationId"
                    ],
                    "operation_id": "op-attested-implementation",
                },
                root=root,
                trusted_host_adapter="claude-code",
            )
            record_loop_result(
                root=root,
                root_id=prepared["rootId"],
                node_id=task_node_id,
                operation_id="op-attested-implementation",
                outcome=success("implemented"),
            )
            review_node_id = task_review_node_id("t-service")
            with self.assertRaises(GatedLoopError) as caught:
                attest_loop_receiver(
                    root=root,
                    root_id=prepared["rootId"],
                    node_id=review_node_id,
                    receiver_context_id="context-review-child",
                    parent_context_id="context-intermediate-child",
                    host_adapter_id="claude-code",
                )
            with self.assertRaises(GatedLoopError) as cross_adapter:
                attest_loop_receiver(
                    root=root,
                    root_id=prepared["rootId"],
                    node_id=review_node_id,
                    receiver_context_id="context-codex-review",
                    parent_context_id="context-codex-orchestrator",
                    host_adapter_id="codex",
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_RECEIVER_PARENT_UNTRUSTED",
        )
        self.assertEqual(
            cross_adapter.exception.code,
            "SCHEDULER_RECEIVER_PARENT_UNTRUSTED",
        )

    def test_review_ignores_external_codex_and_reports_context_only(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            task_assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=claude_host_executor_inventory(),
                node_requirements=[
                    agent_requirement(loop_node_id("t-service"))
                ],
            )["assignments"][0]
            dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=task_assignment["nodeId"],
                owner="claude-task-receiver",
                agent_id=task_assignment["agent"]["id"],
                model_id=task_assignment["model"]["id"],
                dispatch_mode="AUTO",
                dispatch_transport=task_assignment[
                    "dispatchTransport"
                ],
                dispatch_reservation_id=task_assignment[
                    "dispatchReservationId"
                ],
                dispatch_reasoning_class=(
                    task_assignment["reasoningClass"]
                ),
                dispatch_decision_fingerprint=(
                    task_assignment["decisionFingerprint"]
                ),
                operation_id="op-claude-native-task",
            )
            record_loop_result(
                root=root,
                root_id=prepared["rootId"],
                node_id=task_assignment["nodeId"],
                operation_id="op-claude-native-task",
                outcome=success("TASK completed by native Claude."),
            )
            review_plan = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=[
                    *claude_host_executor_inventory(),
                    *host_executor_inventory(
                        dispatch_transport="EXTERNAL_PROCESS",
                    ),
                ],
                node_requirements=[
                    agent_requirement(
                        task_review_node_id("t-service"),
                        reasoning_class="HIGH",
                    )
                ],
            )

        self.assertEqual(len(review_plan["assignments"]), 1)
        review = review_plan["assignments"][0]
        self.assertEqual(review["agent"]["id"], "claude-code")
        self.assertEqual(review["dispatchTransport"], "HOST_NATIVE")
        self.assertFalse(review["independence"]["agentDiverse"])
        self.assertFalse(review["independence"]["modelDiverse"])
        self.assertEqual(
            review["independence"]["diversityLevel"],
            "CONTEXT_ONLY",
        )
        self.assertIn(
            "REVIEW_HETEROGENEOUS_INDEPENDENCE_UNSATISFIED",
            {reason["code"] for reason in review["reasons"]},
        )

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

    def test_routine_task_uses_efficient_model_and_can_be_claimed(
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
                    agent_requirement(
                        loop_node_id("t-service"),
                        reasoning_class="ROUTINE",
                        reason=(
                            "The change is explicit, repeatable, and has "
                            "a deterministic verification path."
                        ),
                    )
                ],
            )["assignments"][0]

            dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=assignment["nodeId"],
                owner="codex-luna-receiver",
                agent_id=assignment["agent"]["id"],
                model_id=assignment["model"]["id"],
                dispatch_mode="AUTO",
                dispatch_transport=assignment["dispatchTransport"],
                dispatch_reservation_id=assignment[
                    "dispatchReservationId"
                ],
                dispatch_reasoning_class=assignment["reasoningClass"],
                dispatch_decision_fingerprint=(
                    assignment["decisionFingerprint"]
                ),
                operation_id="op-routine-luna",
            )
            state = graph_status(root=root, root_id=prepared["rootId"])

        claimed = next(
            node
            for node in state["nodes"]
            if node["nodeId"] == assignment["nodeId"]
        )
        self.assertEqual(assignment["model"]["id"], "gpt-5.6-luna")
        self.assertEqual(assignment["model"]["tier"], "EFFICIENT")
        self.assertEqual(assignment["reasoningClass"], "ROUTINE")
        self.assertEqual(claimed["modelId"], "gpt-5.6-luna")
        self.assertEqual(claimed["dispatchReasoningClass"], "ROUTINE")

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
                dispatch_transport=task_assignment[
                    "dispatchTransport"
                ],
                dispatch_reservation_id=task_assignment[
                    "dispatchReservationId"
                ],
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
                dispatch_transport=assignment["dispatchTransport"],
                dispatch_reservation_id=assignment[
                    "dispatchReservationId"
                ],
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
        self.assertEqual(claimed["dispatchTransport"], "HOST_NATIVE")
        self.assertEqual(
            claimed["dispatchReservationId"],
            assignment["dispatchReservationId"],
        )
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
            event["payload"]["dispatchTransport"],
            "HOST_NATIVE",
        )
        self.assertEqual(
            event["payload"]["dispatchReservationId"],
            assignment["dispatchReservationId"],
        )
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
                    dispatch_transport="HOST_NATIVE",
                    dispatch_reservation_id="wrong-route-reservation",
                    dispatch_reasoning_class="STANDARD",
                    operation_id="op-auto-missing-decision",
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DISPATCH_DECISION_REQUIRED",
        )

    def test_auto_claim_requires_the_planned_reservation(self) -> None:
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

            with self.assertRaises(GatedLoopError) as caught:
                dispatch_loop(
                    root=root,
                    root_id=prepared["rootId"],
                    node_id=assignment["nodeId"],
                    owner="codex-terra-without-ticket",
                    agent_id=assignment["agent"]["id"],
                    model_id=assignment["model"]["id"],
                    dispatch_mode="AUTO",
                    dispatch_transport=assignment[
                        "dispatchTransport"
                    ],
                    dispatch_reasoning_class=assignment[
                        "reasoningClass"
                    ],
                    dispatch_decision_fingerprint=assignment[
                        "decisionFingerprint"
                    ],
                    operation_id="op-auto-missing-reservation",
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DISPATCH_RESERVATION_REQUIRED",
        )

    def test_auto_claim_rejects_external_process_transport(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())

            with self.assertRaises(GatedLoopError) as caught:
                dispatch_loop(
                    root=root,
                    root_id=prepared["rootId"],
                    node_id=loop_node_id("t-service"),
                    owner="external-codex-receiver",
                    agent_id="codex",
                    model_id="gpt-5.6-sol",
                    dispatch_mode="AUTO",
                    dispatch_transport="EXTERNAL_PROCESS",
                    dispatch_reasoning_class="HIGH",
                    dispatch_decision_fingerprint="f" * 64,
                    operation_id="op-auto-external-process",
                )

            state = graph_status(root=root, root_id=prepared["rootId"])

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DISPATCH_TRANSPORT_REQUIRED",
        )
        task = next(
            node
            for node in state["nodes"]
            if node["nodeId"] == loop_node_id("t-service")
        )
        self.assertEqual(task["status"], "READY")

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
                    dispatch_transport="HOST_NATIVE",
                    dispatch_reservation_id="wrong-route-reservation",
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

    def test_auto_claim_rejects_effective_model_mismatch(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            assignment = plan_dispatch_batch(
                root=root,
                root_id=prepared["rootId"],
                expected_graph_fingerprint=prepared["graphFingerprint"],
                executor_inventory=host_executor_inventory(),
                node_requirements=[
                    agent_requirement(
                        loop_node_id("t-service"),
                        reasoning_class="HIGH",
                    )
                ],
            )["assignments"][0]

            with self.assertRaises(GatedLoopError) as caught:
                dispatch_loop(
                    root=root,
                    root_id=prepared["rootId"],
                    node_id=assignment["nodeId"],
                    owner="codex-effective-gpt-5",
                    agent_id=assignment["agent"]["id"],
                    model_id="gpt-5",
                    dispatch_mode="AUTO",
                    dispatch_transport=assignment[
                        "dispatchTransport"
                    ],
                    dispatch_reservation_id=assignment[
                        "dispatchReservationId"
                    ],
                    dispatch_reasoning_class=assignment[
                        "reasoningClass"
                    ],
                    dispatch_decision_fingerprint=assignment[
                        "decisionFingerprint"
                    ],
                    operation_id="op-auto-effective-model-mismatch",
                )
            state = graph_status(root=root, root_id=prepared["rootId"])

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DISPATCH_DECISION_MISMATCH",
        )
        task = next(
            node
            for node in state["nodes"]
            if node["nodeId"] == assignment["nodeId"]
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
                orchestrator_config=OrchestratorConfig(
                    allow_cross_adapter_dispatch=True
                ),
            )["assignments"][0]
            dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=task_assignment["nodeId"],
                owner="codex-task-terra",
                agent_id=task_assignment["agent"]["id"],
                model_id=task_assignment["model"]["id"],
                dispatch_mode="AUTO",
                dispatch_transport=task_assignment[
                    "dispatchTransport"
                ],
                dispatch_reservation_id=task_assignment[
                    "dispatchReservationId"
                ],
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
                orchestrator_config=OrchestratorConfig(
                    allow_cross_adapter_dispatch=True
                ),
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
                trusted_host_adapter="codex",
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
