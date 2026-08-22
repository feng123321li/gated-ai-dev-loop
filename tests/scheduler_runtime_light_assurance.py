from __future__ import annotations

from .scheduler_runtime_support import (
    at,
    get_graph_frontier,
    loop_context,
    loop_node_id,
    task_hierarchy,
)


class SchedulerRuntimeLightAssuranceTests:
    def test_light_assurance_keeps_safety_but_reduces_process_reporting(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["delivery"].update(
            {
                "assuranceProfile": "LIGHT",
                "assuranceRationale": (
                    "The actual change is confined to one internal helper "
                    "with targeted tests and no boundary impact."
                ),
                "reviewLoop": None,
            }
        )
        hierarchy["root"]["reviewLoop"] = None
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(2),
        )
        dispatch_action = next(
            action
            for action in frontier["actions"]
            if action["action"] == "DISPATCH_LOOP"
        )
        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
        )
        execution_policy = context["executionPolicy"]
        completion_policy = context["completionPolicy"]

        self.assertEqual(execution_policy["assuranceProfile"], "LIGHT")
        self.assertEqual(
            execution_policy["reviewTopology"],
            "NO_INDEPENDENT_REVIEW_LOOPS",
        )
        self.assertEqual(
            execution_policy["progressReporting"]["reportAt"],
            ["ISSUE_FOUND", "FINAL_VERIFICATION"],
        )
        self.assertTrue(
            execution_policy["progressReporting"][
                "shortLoopMayReportOnlyFinal"
            ]
        )
        self.assertTrue(
            execution_policy["progressReporting"][
                "initialHeartbeatRequiredBeforeWork"
            ]
        )
        self.assertFalse(
            execution_policy["progressReporting"][
                "shortLoopMayCompleteWithoutExplicitHeartbeat"
            ]
        )
        self.assertEqual(execution_policy["contextIsolation"], "REQUIRED")
        self.assertEqual(dispatch_action["executionPolicy"], execution_policy)
        self.assertEqual(
            completion_policy["verificationScope"],
            "TARGETED_FOR_DECLARED_CHANGE",
        )
        self.assertEqual(
            completion_policy["reviewCycle"],
            "FOCUSED_REVIEW_RESOLVE_VERIFY_AND_REREVIEW_IF_NEEDED",
        )
        self.assertEqual(
            completion_policy["reviewFindings"]["p0p1"],
            "RESOLVE_AND_REREVIEW_BEFORE_SUCCEEDED",
        )
