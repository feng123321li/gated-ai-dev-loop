from __future__ import annotations

from copy import deepcopy
from tempfile import TemporaryDirectory
import unittest

from hdg.dispatch_planning import plan_dispatch_batch
from hdg.errors import GatedLoopError
from hdg.graph_runtime import (
    dispatch_loop,
    graph_status,
)
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import SchedulerRepository

from .test_loop_architecture import group_hierarchy, task_hierarchy


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
        self.assertTrue(
            plan["assignments"][0]["independence"]["required"]
        )
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
