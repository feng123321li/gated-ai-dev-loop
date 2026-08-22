from __future__ import annotations

from .scheduler_runtime_support import (
    at,
    dispatch_loop,
    loop_node_id,
    record_loop_result,
    success,
    task_hierarchy,
)
from hdg.entry_routing import route_entry_intent
from hdg.errors import GatedLoopError
from hdg.result_ledger import delivery_result


class SchedulerRuntimeTestsPart16:
    def test_successful_task_result_requires_scope_bound_evidence(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=prepared["rootId"],
            node_id=node_id,
            owner="incomplete-result-agent",
            operation_id="incomplete-result-operation",
            now=at(2),
        )

        with self.assertRaises(GatedLoopError) as caught:
            record_loop_result(
                root=self.root,
                root_id=prepared["rootId"],
                node_id=node_id,
                operation_id="incomplete-result-operation",
                outcome={
                    "status": "SUCCEEDED",
                    "summary": "Implementation completed.",
                    "result": {"evidence": "opaque"},
                },
                now=at(3),
            )

        self.assertEqual(
            caught.exception.code,
            "LOOP_TASK_RESULT_INCOMPLETE",
        )
        self.assertEqual(
            [item["code"] for item in caught.exception.details["gaps"]],
            [
                "TASK_AFFECTED_SCOPES_MISSING",
                "TASK_VERIFICATION_EVIDENCE_MISSING",
            ],
        )

    def test_entry_router_combines_user_intent_with_persisted_run_state(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())

        decision = route_entry_intent(
            root=self.root,
            root_id=prepared["rootId"],
            request_text="继续这个交付",
            workspace_root=self.root,
        )

        self.assertEqual(decision["observedStatus"], "ACTIVE")
        self.assertEqual(decision["intent"], "DISPATCH_ACTIVE")
        self.assertEqual(
            decision["targetSkill"],
            "delivery-graph-dispatch",
        )

    def test_entry_router_detects_ready_user_confirmation_from_run_state(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["delivery"].update(
            {
                "assuranceProfile": "LIGHT",
                "assuranceRationale": "One bounded task with focused checks.",
                "reviewLoop": None,
            }
        )
        hierarchy["root"]["reviewLoop"] = None
        prepared = self.prepare_and_freeze(hierarchy)
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=prepared["rootId"],
            node_id=node_id,
            owner="entry-confirm-agent",
            operation_id="entry-confirm-operation",
            now=at(2),
        )
        record_loop_result(
            root=self.root,
            root_id=prepared["rootId"],
            node_id=node_id,
            operation_id="entry-confirm-operation",
            outcome=success("Focused change completed."),
            now=at(3),
        )

        decision = route_entry_intent(
            root=self.root,
            root_id=prepared["rootId"],
            request_text="确认验收",
            workspace_root=self.root,
        )

        self.assertEqual(decision["intent"], "CONFIRM_REVISION")
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["targetSkill"], "delivery-graph")

    def test_completed_delivery_returns_the_authoritative_result_ledger(
        self,
    ) -> None:
        completed = self.complete_task_delivery("d-result-ledger")

        assembled = delivery_result(
            root=self.root,
            root_id=completed["rootId"],
        )

        self.assertTrue(assembled["completeness"]["complete"])
        self.assertEqual(completed["deliveryResult"], assembled)
        self.assertEqual(
            len(assembled["loopResults"]),
            assembled["completeness"]["summary"]["expectedLoops"],
        )
        self.assertTrue(
            all(
                item["status"] == "SUCCEEDED"
                for item in assembled["loopResults"]
            )
        )
