from __future__ import annotations

from copy import deepcopy
from tempfile import TemporaryDirectory
import unittest

from hdg.dispatch_planning import plan_dispatch_batch
from hdg.errors import GatedLoopError
from hdg.graph_runtime import (
    authorize_codex_subagent_operation,
    claim_codex_subagent_receiver,
    dispatch_loop,
    graph_events,
    graph_status,
    record_loop_result,
)
from hdg.planning import freeze_hierarchy, prepare_hierarchy

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

    def test_codex_claim_authorizes_receiver_not_observed_model(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            assignment = self.plan(root, prepared)["assignments"][0]

            claimed = claim_codex_subagent_receiver(
                root=root,
                root_id=prepared["rootId"],
                workspace_root=root,
                receiver_context_id="codex-v5-child",
                parent_context_id="codex-v5-parent",
                actual_model_id="host-observed-model-a",
                dispatch_reservation_id=assignment[
                    "dispatchReservationId"
                ],
            )
            authorized = authorize_codex_subagent_operation(
                root=root,
                root_id=prepared["rootId"],
                node_id=assignment["nodeId"],
                workspace_root=root,
                receiver_context_id="codex-v5-child",
                parent_context_id="codex-v5-parent",
                dispatch_reservation_id=assignment[
                    "dispatchReservationId"
                ],
            )

        self.assertEqual(claimed["agentId"], "codex")
        self.assertNotIn("modelId", claimed)
        self.assertEqual(
            claimed["actualModelId"],
            "host-observed-model-a",
        )
        self.assertNotIn("dispatchReasoningClass", claimed)
        self.assertEqual(authorized["operationId"], claimed["operationId"])

    def test_new_orchestrator_session_can_dispatch_next_review_at_idle_frontier(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(root, task_hierarchy())
            implementation = self.plan(root, prepared)["assignments"][0]
            claimed = claim_codex_subagent_receiver(
                root=root,
                root_id=prepared["rootId"],
                workspace_root=root,
                receiver_context_id="implementation-child",
                parent_context_id="orchestrator-session-one",
                actual_model_id="host-model",
                dispatch_reservation_id=implementation[
                    "dispatchReservationId"
                ],
            )
            record_loop_result(
                root=root,
                root_id=prepared["rootId"],
                node_id=implementation["nodeId"],
                operation_id=claimed["operationId"],
                outcome={
                    "status": "SUCCEEDED",
                    "summary": "Implementation completed.",
                    "result": {},
                },
            )

            review = self.plan(root, prepared)["assignments"][0]
            review_claim = claim_codex_subagent_receiver(
                root=root,
                root_id=prepared["rootId"],
                workspace_root=root,
                receiver_context_id="review-child",
                parent_context_id="orchestrator-session-two",
                actual_model_id="host-model",
                dispatch_reservation_id=review[
                    "dispatchReservationId"
                ],
            )

            self.assertEqual(review_claim["nodeId"], review["nodeId"])
            rotation = next(
                event
                for event in graph_events(
                    root=root,
                    root_id=prepared["rootId"],
                )["events"]
                if event["eventType"] == "RECEIVER_ROOT_ROTATED"
            )
            self.assertEqual(
                rotation["payload"]["reason"],
                "IDLE_FRONTIER_HANDOFF",
            )

    def test_new_orchestrator_session_cannot_take_over_active_claims(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = self.prepare_and_freeze(
                root,
                parallel_group_hierarchy(),
            )
            assignments = self.plan(root, prepared)["assignments"]
            claim_codex_subagent_receiver(
                root=root,
                root_id=prepared["rootId"],
                workspace_root=root,
                receiver_context_id="first-child",
                parent_context_id="orchestrator-session-one",
                actual_model_id="host-model",
                dispatch_reservation_id=assignments[0][
                    "dispatchReservationId"
                ],
            )

            with self.assertRaises(GatedLoopError) as caught:
                claim_codex_subagent_receiver(
                    root=root,
                    root_id=prepared["rootId"],
                    workspace_root=root,
                    receiver_context_id="second-child",
                    parent_context_id="orchestrator-session-two",
                    actual_model_id="host-model",
                    dispatch_reservation_id=assignments[1][
                        "dispatchReservationId"
                    ],
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_RECEIVER_PARENT_UNTRUSTED",
        )

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

    def test_auto_claim_uses_v5_receiver_fingerprint_without_model(
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
                operation_id="operation-v5",
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
                require_receiver_attestation=False,
            )
            state = graph_status(
                root=root,
                root_id=prepared["rootId"],
            )

        self.assertEqual(claimed["status"], "CLAIMED")
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
                    operation_id="operation-v5",
                    agent_id="codex",
                    dispatch_mode="AUTO",
                    dispatch_transport=assignment["dispatchTransport"],
                    dispatch_reservation_id=assignment[
                        "dispatchReservationId"
                    ],
                    dispatch_decision_fingerprint="0" * 64,
                    host_adapter_id="codex",
                    host_native_agent_ids=("codex",),
                    require_receiver_attestation=False,
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
