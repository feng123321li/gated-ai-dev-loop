from __future__ import annotations

import unittest

from hdg.entry_routing import decide_entry_route


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


if __name__ == "__main__":
    unittest.main()
