from __future__ import annotations

import unittest

from hdg.entry_routing import decide_entry_route
from hdg.errors import GatedLoopError


class EntryRoutingTests(unittest.TestCase):
    def test_explicit_new_requirement_does_not_reuse_an_active_root(self) -> None:
        decision = decide_entry_route(
            request_text="这是一个新需求，请新建交付。",
            workspace_state={"status": "ACTIVE", "rootId": "d-old"},
        )

        self.assertEqual(decision["intent"], "NEW_DELIVERY")
        self.assertEqual(decision["targetSkill"], "delivery-graph")
        self.assertIsNone(decision["rootId"])
        self.assertTrue(decision["allowed"])
        self.assertIn(
            "EXPLICIT_NEW_DELIVERY",
            decision["reasonCodes"],
        )

    def test_continue_routes_by_authoritative_delivery_state(self) -> None:
        active = decide_entry_route(
            request_text="继续这个交付",
            workspace_state={"status": "ACTIVE", "rootId": "d-active"},
        )
        completed = decide_entry_route(
            request_text="继续这个交付",
            workspace_state={
                "status": "COMPLETED",
                "rootId": "d-completed",
            },
        )

        self.assertEqual(active["intent"], "DISPATCH_ACTIVE")
        self.assertEqual(
            active["targetSkill"],
            "delivery-graph-dispatch",
        )
        self.assertEqual(active["rootId"], "d-active")
        self.assertEqual(completed["intent"], "CONTINUE_DELIVERY")
        self.assertEqual(completed["targetSkill"], "delivery-graph")

    def test_continue_execution_does_not_become_paused_resume(self) -> None:
        for status in ("QUEUED", "HANDOFF_READY"):
            with self.subTest(status=status):
                decision = decide_entry_route(
                    request_text="继续执行",
                    workspace_state={
                        "status": status,
                        "rootId": "d-existing",
                    },
                )

                self.assertEqual(decision["intent"], "DISPATCH_ACTIVE")
                self.assertTrue(decision["allowed"])
                self.assertEqual(
                    decision["targetSkill"],
                    "delivery-graph-dispatch",
                )

    def test_bare_recovery_phrase_routes_paused_delivery(self) -> None:
        decision = decide_entry_route(
            request_text="恢复",
            workspace_state={"status": "PAUSED", "rootId": "d-paused"},
        )

        self.assertEqual(decision["intent"], "RESUME_PAUSED")
        self.assertTrue(decision["allowed"])

    def test_resume_and_close_fail_closed_on_state_conflict(self) -> None:
        resume = decide_entry_route(
            request_text="恢复执行",
            workspace_state={"status": "PAUSED", "rootId": "d-paused"},
        )
        close = decide_entry_route(
            request_text="关闭交付",
            workspace_state={"status": "ACTIVE", "rootId": "d-active"},
        )

        self.assertEqual(resume["intent"], "RESUME_PAUSED")
        self.assertTrue(resume["allowed"])
        self.assertEqual(
            resume["targetSkill"],
            "delivery-graph-dispatch",
        )
        self.assertEqual(close["intent"], "CLOSE_DELIVERY")
        self.assertFalse(close["allowed"])
        self.assertTrue(close["requiresClarification"])
        self.assertIn("ROUTE_STATE_CONFLICT", close["reasonCodes"])

    def test_status_query_uses_dispatch_only_for_runtime_states(self) -> None:
        active = decide_entry_route(
            request_text="查看当前进度",
            workspace_state={"status": "BLOCKED", "rootId": "d-runtime"},
        )
        prepared = decide_entry_route(
            request_text="查看当前状态",
            workspace_state={"status": "PREPARED", "rootId": "d-plan"},
        )

        self.assertEqual(active["intent"], "QUERY_STATUS")
        self.assertEqual(
            active["targetSkill"],
            "delivery-graph-dispatch",
        )
        self.assertEqual(prepared["targetSkill"], "delivery-graph")

    def test_lifecycle_routes_require_their_authoritative_gate(self) -> None:
        replan = decide_entry_route(
            request_text="修改需求",
            workspace_state={"status": "ACTIVE", "rootId": "d-active"},
        )
        confirmation = decide_entry_route(
            request_text="确认验收",
            workspace_state={
                "status": "ACTIVE",
                "rootId": "d-ready",
                "nextAction": "RECORD_USER_CONFIRMATION",
            },
        )
        close = decide_entry_route(
            request_text="关闭交付",
            workspace_state={"status": "COMPLETED", "rootId": "d-done"},
        )
        archive = decide_entry_route(
            request_text="归档",
            workspace_state={
                "status": "COMPLETED",
                "rootId": "d-closed",
                "deliveryClosure": "CLOSED",
            },
        )

        self.assertEqual(replan["intent"], "REPLAN")
        self.assertEqual(confirmation["intent"], "CONFIRM_REVISION")
        self.assertEqual(close["intent"], "CLOSE_DELIVERY")
        self.assertEqual(archive["intent"], "ARCHIVE_DELIVERY")
        self.assertTrue(
            all(item["allowed"] for item in (replan, confirmation, close, archive))
        )

    def test_invalid_entry_input_fails_before_model_fallback(self) -> None:
        with self.assertRaises(GatedLoopError) as caught:
            decide_entry_route(
                request_text=" ",
                workspace_state={"status": "ABSENT"},
            )
        self.assertEqual(caught.exception.code, "ENTRY_ROUTE_INVALID")

        with self.assertRaises(GatedLoopError) as caught:
            decide_entry_route(
                request_text="继续",
                workspace_state=[],
            )
        self.assertEqual(caught.exception.code, "ENTRY_ROUTE_INVALID")

    def test_multiple_candidates_require_deterministic_selection(self) -> None:
        decision = decide_entry_route(
            request_text="继续处理",
            workspace_state={
                "status": "DELIVERY_SELECTION_REQUIRED",
                "candidateDeliveries": [
                    {"rootId": "d-one"},
                    {"rootId": "d-two"},
                ],
            },
        )

        self.assertEqual(decision["intent"], "SELECT_DELIVERY")
        self.assertFalse(decision["allowed"])
        self.assertTrue(decision["requiresClarification"])
        self.assertEqual(decision["candidateRootIds"], ["d-one", "d-two"])

    def test_unrecognized_language_is_explicitly_ambiguous(self) -> None:
        decision = decide_entry_route(
            request_text="帮我处理一下这个事情",
            workspace_state={"status": "ABSENT"},
        )

        self.assertEqual(decision["intent"], "AMBIGUOUS")
        self.assertIsNone(decision["targetSkill"])
        self.assertFalse(decision["allowed"])
        self.assertTrue(decision["requiresClarification"])
        self.assertEqual(
            decision["fallback"],
            "MODEL_CLASSIFICATION_OR_USER_CONFIRMATION",
        )
        self.assertIn("supervisorRouting", decision)
        self.assertFalse(decision["supervisorRouting"]["enabled"])
        self.assertEqual(
            decision["supervisorRouting"]["boundary"]["toolAccess"],
            "NONE",
        )

    def test_negated_lifecycle_actions_do_not_override_positive_intent(
        self,
    ) -> None:
        cases = (
            (
                "不要关闭交付，继续优化",
                {"status": "COMPLETED", "rootId": "d-completed"},
                "CONTINUE_DELIVERY",
            ),
            (
                "不要归档，查看状态",
                {
                    "status": "COMPLETED",
                    "rootId": "d-closed",
                    "deliveryClosure": "CLOSED",
                },
                "QUERY_STATUS",
            ),
            (
                "不要确认完成，继续执行",
                {
                    "status": "ACTIVE",
                    "rootId": "d-active",
                    "nextAction": "RECORD_USER_CONFIRMATION",
                },
                "DISPATCH_ACTIVE",
            ),
        )

        for request_text, workspace_state, expected_intent in cases:
            with self.subTest(request_text=request_text):
                decision = decide_entry_route(
                    request_text=request_text,
                    workspace_state=workspace_state,
                )
                self.assertEqual(decision["intent"], expected_intent)
                self.assertTrue(decision["allowed"])

    def test_english_negation_and_word_boundaries_are_deterministic(
        self,
    ) -> None:
        negated = decide_entry_route(
            request_text="don't close delivery; continue",
            workspace_state={"status": "COMPLETED", "rootId": "d-done"},
        )
        unrelated = decide_entry_route(
            request_text="describe progressive delivery",
            workspace_state={"status": "ACTIVE", "rootId": "d-active"},
        )

        self.assertEqual(negated["intent"], "CONTINUE_DELIVERY")
        self.assertEqual(unrelated["intent"], "AMBIGUOUS")
        self.assertIn(
            "NO_HIGH_CONFIDENCE_ENTRY_RULE",
            unrelated["reasonCodes"],
        )

    def test_only_negated_action_requires_clarification(self) -> None:
        decision = decide_entry_route(
            request_text="不要关闭交付",
            workspace_state={"status": "COMPLETED", "rootId": "d-done"},
        )

        self.assertEqual(decision["intent"], "AMBIGUOUS")
        self.assertFalse(decision["allowed"])
        self.assertTrue(decision["requiresClarification"])
        self.assertIn("NEGATED_ENTRY_ACTION", decision["reasonCodes"])

    def test_multiple_positive_actions_require_clarification(self) -> None:
        decision = decide_entry_route(
            request_text="关闭交付，然后归档",
            workspace_state={
                "status": "COMPLETED",
                "rootId": "d-closed",
                "deliveryClosure": "CLOSED",
            },
        )

        self.assertEqual(decision["intent"], "AMBIGUOUS")
        self.assertFalse(decision["allowed"])
        self.assertTrue(decision["requiresClarification"])
        self.assertIn("MULTIPLE_ENTRY_INTENTS", decision["reasonCodes"])
        self.assertEqual(decision["routerVersion"], 4)


if __name__ == "__main__":
    unittest.main()
