from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory
import unittest

from hdg.dispatch_planning import plan_dispatch_batch
from hdg.errors import GatedLoopError
from hdg.graph_frontier import get_graph_frontier
from hdg.graph_runtime import (
    dispatch_loop,
    graph_status,
)
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import SchedulerRepository

from .test_loop_architecture import (
    group_hierarchy,
    skill_hint,
    task_hierarchy,
)


def parallel_group_hierarchy() -> dict:
    hierarchy = deepcopy(group_hierarchy())
    hierarchy["root"]["children"][1]["definition"]["execution"][
        "dependsOn"
    ] = []
    return hierarchy


class HostDispatchPlanningTests(unittest.TestCase):
    def prepare_and_freeze(self, root: str, hierarchy: dict) -> dict:
        prepared = prepare_hierarchy(root=root, hierarchy=hierarchy)
        freeze_hierarchy(
            root=root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=prepared[
                "hierarchyFingerprint"
            ],
            confirmed=True,
            confirmed_by="human",
        )
        return prepared

    def plan(
        self,
        root: str,
        prepared: dict,
        *,
        adapter_id: str = "codex",
        receiver_agent_id: str = "codex",
        max_concurrent_executors: int = 4,
    ) -> dict:
        return plan_dispatch_batch(
            root=root,
            root_id=prepared["rootId"],
            expected_graph_fingerprint=prepared["graphFingerprint"],
            host_adapter_id=adapter_id,
            host_native_agent_ids=(receiver_agent_id,),
            max_concurrent_executors=max_concurrent_executors,
        )

    def test_trusted_host_reserves_without_model_recommendation_inputs(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            plan = self.plan(root, prepared)

        self.assertEqual(len(plan["assignments"]), 1)
        self.assertRegex(
            plan["assignments"][0]["dispatchReservationId"],
            r"^[0-9a-f-]{36}$",
        )
        self.assertEqual(plan["nextAction"], "CREATE_INDEPENDENT_RECEIVERS")
        self.assertEqual(
            plan["postActionWait"],
            {
                "mode": "HOST_NATIVE_EVENT_OR_RESERVATION_DEADLINE",
                "interruptOn": [
                    "NATIVE_RECEIVER_CLAIMED",
                    "NATIVE_RECEIVER_COMPLETED",
                    "NATIVE_RECEIVER_NEEDS_ATTENTION",
                    "NATIVE_RECEIVER_START_FAILED",
                ],
                "onInterrupt": "CALL_GRAPH_FRONTIER_ONCE",
                "deadline": plan["assignments"][0][
                    "reservationExpiresAt"
                ],
                "onDeadline": "CALL_GRAPH_FRONTIER_ONCE",
                "doNotPollBackToBack": True,
            },
        )
        self.assertTrue(
            plan["assignments"][0]["independence"]["required"]
        )
        self.assertNotIn("skillHints", plan["assignments"][0])
        self.assertNotIn("receiverPrompt", plan["assignments"][0])
        self.assertNotIn("currentSessionTaskNodeIds", plan)
        self.assertNotIn("currentSessionTasks", plan["summary"])

    def test_ready_legacy_hook_state_replans_without_migration(self) -> None:
        retired_tables = (
            "host_workspace_attestations",
            "receiver_attestations",
            "host_receiver_identities",
            "run_receiver_roots",
        )
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            repository = SchedulerRepository(root)
            with repository.transaction() as connection:
                for table_name in retired_tables:
                    connection.execute(
                        f"CREATE TABLE {table_name} (legacy_id TEXT)"
                    )
            plan = self.plan(root, prepared)

        self.assertEqual(len(plan["assignments"]), 1)
        self.assertTrue(
            plan["assignments"][0]["independence"]["required"]
        )

    def test_receiver_assignment_inherits_current_host_model_policy(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            assignment = self.plan(root, prepared)["assignments"][0]

        self.assertEqual(assignment["hostAdapterId"], "codex")
        self.assertEqual(assignment["receiverAgentId"], "codex")
        self.assertEqual(
            assignment["modelPolicy"],
            "CURRENT_HOST_INHERIT",
        )
        for removed in (
            "model",
            "reasoningClass",
            "modelSelection",
            "routeReview",
        ):
            self.assertNotIn(removed, assignment)

    def test_receiver_assignment_carries_advisory_native_skill_prompt(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["root"]["skillHints"] = [
            skill_hint(
                "springboot-tdd",
                "Prefer TDD for an applicable Spring Boot Loop.",
            )
        ]
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, hierarchy)
            assignment = self.plan(root, prepared)["assignments"][0]

        self.assertEqual(
            assignment["skillHints"],
            hierarchy["root"]["skillHints"],
        )
        self.assertIn("`$springboot-tdd`", assignment["receiverPrompt"])
        self.assertIn("适用且当前宿主可用", assignment["receiverPrompt"])
        self.assertIn("应在当前相应阶段优先原生触发", assignment["receiverPrompt"])
        self.assertIn("多数在 TASK 阶段使用", assignment["receiverPrompt"])
        self.assertIn("才跳过", assignment["receiverPrompt"])
        self.assertNotIn("必须执行", assignment["receiverPrompt"])

    def test_claude_receiver_uses_native_skill_tool_wording(self) -> None:
        hierarchy = task_hierarchy()
        hierarchy["root"]["skillHints"] = [
            skill_hint(
                "springboot-tdd",
                "Prefer TDD for an applicable Spring Boot Loop.",
            )
        ]
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, hierarchy)
            assignment = self.plan(
                root,
                prepared,
                adapter_id="claude-code",
                receiver_agent_id="claude-code",
            )["assignments"][0]

        self.assertIn("Skill tool", assignment["receiverPrompt"])
        self.assertIn("`springboot-tdd`", assignment["receiverPrompt"])
        self.assertNotIn("`$springboot-tdd`", assignment["receiverPrompt"])

    def test_registered_adapter_is_the_only_extension_boundary(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            with self.assertRaises(GatedLoopError) as caught:
                self.plan(
                    root,
                    prepared,
                    adapter_id="grok",
                    receiver_agent_id="grok",
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_HOST_NATIVE_INVENTORY_MISMATCH",
        )

    def test_host_cannot_claim_another_adapters_receiver(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            with self.assertRaises(GatedLoopError) as caught:
                self.plan(
                    root,
                    prepared,
                    adapter_id="codex",
                    receiver_agent_id="claude-code",
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_HOST_NATIVE_INVENTORY_MISMATCH",
        )

    def test_second_dispatcher_cannot_reserve_the_same_loop(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            first = self.plan(root, prepared)
            second = self.plan(root, prepared)

        self.assertEqual(len(first["assignments"]), 1)
        self.assertEqual(second["assignments"], [])
        self.assertEqual(
            second["deferred"][0]["code"],
            "DISPATCH_ALREADY_RESERVED",
        )

    def test_reserved_receiver_waits_for_event_or_reservation_deadline(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            assignment = self.plan(root, prepared)["assignments"][0]
            frontier = get_graph_frontier(
                root=root,
                root_id=prepared["rootId"],
            )

        self.assertIn(
            "WAIT_FOR_DISPATCH_RECEIVER",
            {action["action"] for action in frontier["actions"]},
        )
        self.assertEqual(
            frontier["nextWakeAt"],
            assignment["reservationExpiresAt"],
        )
        directive = frontier["progressMonitor"]["waitDirective"]
        self.assertEqual(
            directive["mode"],
            "HOST_NATIVE_EVENT_OR_DEADLINE",
        )
        self.assertEqual(directive["pollTool"], "graph_frontier")
        self.assertEqual(
            directive["pollNotBefore"],
            assignment["reservationExpiresAt"],
        )
        self.assertEqual(
            directive["onTimeout"],
            "CALL_GRAPH_FRONTIER_ONCE",
        )
        self.assertEqual(
            directive["nextWakeAt"],
            assignment["reservationExpiresAt"],
        )
        self.assertFalse(directive["consumeActionsBeforeWaiting"])

    def test_reservation_expires_at_the_exact_deadline(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            assignment = self.plan(root, prepared)["assignments"][0]
            expiry = datetime.fromisoformat(
                assignment["reservationExpiresAt"].replace("Z", "+00:00")
            )
            with self.assertRaises(GatedLoopError) as caught:
                dispatch_loop(
                    root=root,
                    root_id=prepared["rootId"],
                    node_id=assignment["nodeId"],
                    owner="receiver-at-deadline",
                    receiver_context_id="receiver-at-deadline",
                    operation_id="operation-at-deadline",
                    agent_id="codex",
                    dispatch_mode="AUTO",
                    dispatch_transport=assignment["dispatchTransport"],
                    dispatch_reservation_id=assignment[
                        "dispatchReservationId"
                    ],
                    dispatch_decision_fingerprint=assignment[
                        "decisionFingerprint"
                    ],
                    host_adapter_id="codex",
                    host_native_agent_ids=("codex",),
                    now=expiry,
                )
            frontier = get_graph_frontier(
                root=root,
                root_id=prepared["rootId"],
                now=expiry,
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DISPATCH_RESERVATION_EXPIRED",
        )
        self.assertNotIn(
            "WAIT_FOR_DISPATCH_RECEIVER",
            {action["action"] for action in frontier["actions"]},
        )
        self.assertIn(
            "DISPATCH_LOOP",
            {action["action"] for action in frontier["actions"]},
        )

    def test_claimed_reservation_expiry_does_not_remain_as_wake_deadline(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            assignment = self.plan(root, prepared)["assignments"][0]
            reservation_expiry = datetime.fromisoformat(
                assignment["reservationExpiresAt"].replace("Z", "+00:00")
            )
            claimed = dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=assignment["nodeId"],
                owner="receiver-before-deadline",
                receiver_context_id="receiver-before-deadline",
                operation_id="operation-before-deadline",
                agent_id="codex",
                dispatch_mode="AUTO",
                dispatch_transport=assignment["dispatchTransport"],
                dispatch_reservation_id=assignment[
                    "dispatchReservationId"
                ],
                dispatch_decision_fingerprint=assignment[
                    "decisionFingerprint"
                ],
                host_adapter_id="codex",
                host_native_agent_ids=("codex",),
                now=reservation_expiry - timedelta(seconds=1),
            )
            after_reservation_expiry = reservation_expiry + timedelta(
                seconds=1
            )
            repository = SchedulerRepository(root)
            with repository.read() as connection:
                reservations = repository.active_dispatch_reservations(
                    connection,
                    at=after_reservation_expiry.isoformat().replace(
                        "+00:00",
                        "Z",
                    ),
                )
            frontier = get_graph_frontier(
                root=root,
                root_id=prepared["rootId"],
                now=after_reservation_expiry,
            )

        self.assertEqual(reservations[0]["reservationStatus"], "CLAIMED")
        self.assertEqual(frontier["nextWakeAt"], claimed["leaseExpiresAt"])
        self.assertNotEqual(
            frontier["nextWakeAt"],
            assignment["reservationExpiresAt"],
        )
        self.assertNotIn(
            "WAIT_FOR_DISPATCH_RECEIVER",
            {action["action"] for action in frontier["actions"]},
        )
        self.assertEqual(
            frontier["progressMonitor"]["waitDirective"]["nextWakeAt"],
            claimed["leaseExpiresAt"],
        )

    def test_reservation_queries_compare_mixed_iso_precision_by_time(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            assignment = self.plan(root, prepared)["assignments"][0]
            repository = SchedulerRepository(root)
            at_whole_second = "2030-01-01T00:00:00Z"
            expires_fractionally_later = "2030-01-01T00:00:00.100000Z"
            with repository.transaction() as connection:
                connection.execute(
                    "UPDATE dispatch_reservations SET expires_at = ? "
                    "WHERE reservation_id = ?",
                    (
                        expires_fractionally_later,
                        assignment["dispatchReservationId"],
                    ),
                )
            with repository.transaction() as connection:
                active = repository.active_dispatch_reservations(
                    connection,
                    at=at_whole_second,
                )
                repository.expire_dispatch_reservations(
                    connection,
                    at=at_whole_second,
                )
                status_before_expiry = connection.execute(
                    "SELECT status FROM dispatch_reservations "
                    "WHERE reservation_id = ?",
                    (assignment["dispatchReservationId"],),
                ).fetchone()["status"]
                repository.expire_dispatch_reservations(
                    connection,
                    at=expires_fractionally_later,
                )
                status_at_expiry = connection.execute(
                    "SELECT status FROM dispatch_reservations "
                    "WHERE reservation_id = ?",
                    (assignment["dispatchReservationId"],),
                ).fetchone()["status"]

        self.assertEqual(len(active), 1)
        self.assertEqual(status_before_expiry, "RESERVED")
        self.assertEqual(status_at_expiry, "EXPIRED")

    def test_global_concurrency_limit_is_reserved_atomically(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(
                root,
                parallel_group_hierarchy(),
            )
            plan = self.plan(
                root,
                prepared,
                max_concurrent_executors=1,
            )

        self.assertEqual(len(plan["assignments"]), 1)
        self.assertEqual(len(plan["deferred"]), 1)
        self.assertEqual(
            plan["dispatchPolicy"]["maxConcurrentExecutors"],
            1,
        )

    def test_auto_claim_uses_reserved_receiver_fingerprint_without_model(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            assignment = self.plan(root, prepared)["assignments"][0]
            claimed = dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=assignment["nodeId"],
                owner="receiver-context",
                receiver_context_id="receiver-context",
                operation_id="operation-hookless",
                agent_id="codex",
                dispatch_mode="AUTO",
                dispatch_transport=assignment["dispatchTransport"],
                dispatch_reservation_id=assignment[
                    "dispatchReservationId"
                ],
                dispatch_decision_fingerprint=assignment[
                    "decisionFingerprint"
                ],
                host_adapter_id="codex",
                host_native_agent_ids=("codex",),
            )
            replayed = dispatch_loop(
                root=root,
                root_id=prepared["rootId"],
                node_id=assignment["nodeId"],
                owner="receiver-context",
                receiver_context_id="receiver-context",
                operation_id="operation-hookless",
                agent_id="codex",
                dispatch_mode="AUTO",
                dispatch_transport=assignment["dispatchTransport"],
                dispatch_reservation_id=assignment[
                    "dispatchReservationId"
                ],
                dispatch_decision_fingerprint=assignment[
                    "decisionFingerprint"
                ],
                host_adapter_id="codex",
                host_native_agent_ids=("codex",),
            )
            with self.assertRaises(GatedLoopError) as replay_caught:
                dispatch_loop(
                    root=root,
                    root_id=prepared["rootId"],
                    node_id=assignment["nodeId"],
                    owner="receiver-context",
                    receiver_context_id="receiver-context",
                    operation_id="operation-hookless",
                    agent_id="codex",
                    actual_model_id="different-model",
                    dispatch_mode="AUTO",
                    dispatch_transport=assignment["dispatchTransport"],
                    dispatch_reservation_id=assignment[
                        "dispatchReservationId"
                    ],
                    dispatch_decision_fingerprint=assignment[
                        "decisionFingerprint"
                    ],
                    host_adapter_id="codex",
                    host_native_agent_ids=("codex",),
                )
            state = graph_status(
                root=root,
                root_id=prepared["rootId"],
            )

        self.assertEqual(claimed["status"], "CLAIMED")
        self.assertFalse(claimed["dispatchReplayed"])
        self.assertTrue(replayed["dispatchReplayed"])
        self.assertEqual(replayed["operationId"], claimed["operationId"])
        self.assertEqual(
            replay_caught.exception.code,
            "SCHEDULER_DISPATCH_REPLAY_MISMATCH",
        )
        self.assertNotIn("modelId", claimed)
        self.assertNotIn("modelId", state["nodes"][0])

    def test_auto_claim_rejects_tampered_decision_fingerprint(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            assignment = self.plan(root, prepared)["assignments"][0]
            with self.assertRaises(GatedLoopError) as caught:
                dispatch_loop(
                    root=root,
                    root_id=prepared["rootId"],
                    node_id=assignment["nodeId"],
                    owner="receiver-context",
                    receiver_context_id="receiver-context",
                    operation_id="operation-hookless",
                    agent_id="codex",
                    dispatch_mode="AUTO",
                    dispatch_transport=assignment["dispatchTransport"],
                    dispatch_reservation_id=assignment[
                        "dispatchReservationId"
                    ],
                    dispatch_decision_fingerprint="0" * 64,
                    host_adapter_id="codex",
                    host_native_agent_ids=("codex",),
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DISPATCH_DECISION_MISMATCH",
        )

    def test_stale_graph_fingerprint_is_rejected(self) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            with self.assertRaises(GatedLoopError) as caught:
                plan_dispatch_batch(
                    root=root,
                    root_id=prepared["rootId"],
                    expected_graph_fingerprint="0" * 64,
                    host_adapter_id="codex",
                    host_native_agent_ids=("codex",),
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_GRAPH_FINGERPRINT_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
