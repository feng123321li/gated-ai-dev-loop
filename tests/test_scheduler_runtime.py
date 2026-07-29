from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hdg.errors import GatedLoopError
from hdg.graph_frontier import get_graph_frontier
from hdg.graph_model import loop_node_id, review_node_id
from hdg.graph_runtime import (
    dispatch_loop,
    graph_events,
    graph_status,
    loop_context,
    pause_loop,
    rebuild_graph_run,
    record_loop_result,
    record_user_confirmation,
)
from hdg.mcp_tools import tool_definitions
from hdg.model_core import validate_hierarchy_definition
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import SchedulerRepository

from .test_loop_architecture import (
    capability_hierarchy,
    loop_descriptor,
    node,
    skill_hint,
    task_definition,
    task_hierarchy,
)


def at(minutes: int) -> datetime:
    return datetime(
        2026,
        7,
        29,
        8,
        0,
        tzinfo=timezone.utc,
    ) + timedelta(minutes=minutes)


def success(summary: str = "Loop completed.") -> dict:
    return {
        "status": "SUCCEEDED",
        "summary": summary,
        "result": {"evidence": "opaque-to-scheduler"},
    }


def parallel_hierarchy() -> dict:
    source = capability_hierarchy()
    source["root"]["children"][1]["definition"]["execution"][
        "dependsOn"
    ] = []
    shared = ["project:erp/module:shared"]
    for child in source["root"]["children"]:
        child["definition"]["execution"]["loop"][
            "resourceClaims"
        ] = shared
    return source


class SchedulerRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = self.temporary.name

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare_and_freeze(self, hierarchy: dict) -> dict:
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        return prepared

    def test_task_and_review_are_uniform_loops_until_confirmation(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(2),
        )
        self.assertEqual(
            [item["nodeId"] for item in frontier["readyLoops"]],
            [loop_node_id(root_id)],
        )

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id(root_id),
            owner="agent-1",
            operation_id="op-task-1",
            now=at(3),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id(root_id),
            operation_id="op-task-1",
            outcome=success("Task Loop completed."),
            now=at(4),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(5),
        )
        self.assertEqual(
            [item["nodeId"] for item in frontier["readyLoops"]],
            [review_node_id(root_id)],
        )
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=review_node_id(root_id),
            owner="reviewer-1",
            operation_id="op-review-1",
            now=at(6),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=review_node_id(root_id),
            operation_id="op-review-1",
            outcome=success("Independent review completed."),
            now=at(7),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(8),
        )
        self.assertEqual(
            frontier["actions"],
            [
                {
                    "action": "RECORD_USER_CONFIRMATION",
                    "nodeId": f"confirm:{root_id}",
                }
            ],
        )
        completed = record_user_confirmation(
            root=self.root,
            root_id=root_id,
            confirmed=True,
            confirmed_by="human",
            summary="Accepted.",
            now=at(9),
        )
        self.assertEqual(completed["status"], "COMPLETED")

        event_types = [
            item["eventType"]
            for item in graph_events(
                root=self.root,
                root_id=root_id,
            )["events"]
        ]
        self.assertIn("LOOP_SUCCEEDED", event_types)
        self.assertIn("USER_CONFIRMED", event_types)
        self.assertNotIn("TASK_IMPLEMENTED", event_types)
        self.assertNotIn("GATE_FAILED", event_types)

    def test_task_and_review_select_shared_skill_hints_at_runtime(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["skillHints"] = [
            skill_hint(
                "springboot-tdd",
                "Prefer TDD when the active Loop is a Spring task.",
            )
        ]
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]

        for node_id in (
            loop_node_id(root_id),
            review_node_id(root_id),
        ):
            with self.subTest(node_id=node_id):
                context = loop_context(
                    root=self.root,
                    root_id=root_id,
                    node_id=node_id,
                )
                self.assertEqual(
                    context["skillHints"],
                    hierarchy["skillHints"],
                )
                self.assertTrue(
                    context["rules"]["skillHintsAreAdvisory"]
                )
                self.assertTrue(
                    context["rules"]["selectSkillsAtRuntime"]
                )
                self.assertTrue(
                    context["rules"][
                        "prioritizeApplicableSkillHints"
                    ]
                )

    def test_review_context_contains_all_upstream_loop_results(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(capability_hierarchy())
        root_id = prepared["rootId"]
        for minute, item_id in ((2, "t-api"), (4, "t-core")):
            node_id = loop_node_id(item_id)
            operation_id = f"op-{item_id}"
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                owner="agent",
                operation_id=operation_id,
                now=at(minute),
            )
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id=operation_id,
                outcome=success(f"{item_id} completed."),
                now=at(minute + 1),
            )

        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=review_node_id(root_id),
        )

        self.assertEqual(
            [
                item["nodeId"]
                for item in context["upstreamLoopResults"]
            ],
            [loop_node_id("t-api"), loop_node_id("t-core")],
        )
        self.assertEqual(
            [
                item["outcome"]["summary"]
                for item in context["upstreamLoopResults"]
            ],
            ["t-api completed.", "t-core completed."],
        )

    def test_expired_worker_cannot_pause_or_submit_a_result(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id(root_id)
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent",
            operation_id="op-expired",
            now=at(2),
        )

        with self.assertRaises(GatedLoopError):
            pause_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-expired",
                now=at(40),
            )
        with self.assertRaises(GatedLoopError):
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-expired",
                outcome=success("Too late."),
                now=at(40),
            )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(40),
        )
        self.assertEqual(
            frontier["readyLoops"][0]["attempt"],
            2,
        )

    def test_infrastructure_failure_retries_but_loop_block_does_not(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id(root_id)
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-1",
            operation_id="op-infra-1",
            now=at(2),
        )
        result = record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-infra-1",
            outcome={
                "status": "BLOCKED",
                "summary": "Worker transport failed.",
                "result": {},
            },
            failure_class="RETRYABLE_INFRA",
            now=at(3),
        )
        self.assertTrue(result["retried"])
        self.assertEqual(result["nextAttempt"], 2)
        self.assertEqual(result["schedulerStatus"], "READY")

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-2",
            operation_id="op-domain-2",
            now=at(4),
        )
        result = record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-domain-2",
            outcome={
                "status": "BLOCKED",
                "summary": "Loop needs external authority.",
                "result": {"request": "approve vendor contract"},
            },
            failure_class="EXTERNAL_AUTHORITY",
            now=at(5),
        )
        self.assertFalse(result["retried"])
        self.assertEqual(result["schedulerStatus"], "BLOCKED")
        self.assertEqual(
            graph_status(
                root=self.root,
                root_id=root_id,
            )["status"],
            "BLOCKED",
        )

    def test_resource_claims_serialize_independent_loops(self) -> None:
        prepared = self.prepare_and_freeze(parallel_hierarchy())
        root_id = prepared["rootId"]
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-api"),
            owner="agent-api",
            operation_id="op-api",
            now=at(2),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(3),
        )
        core = next(
            item
            for item in frontier["readyLoops"]
            if item["nodeId"] == loop_node_id("t-core")
        )
        self.assertEqual(
            core["resourceConflicts"],
            [loop_node_id("t-api")],
        )
        self.assertNotIn(
            loop_node_id("t-core"),
            [
                item.get("nodeId")
                for item in frontier["actions"]
                if item["action"] == "DISPATCH_LOOP"
            ],
        )
        with self.assertRaises(GatedLoopError):
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=loop_node_id("t-core"),
                owner="agent-core",
                operation_id="op-core",
                now=at(3),
            )

    def test_projections_are_scheduler_only(self) -> None:
        hierarchy = task_hierarchy()
        hierarchy["skillHints"] = [
            skill_hint("springboot-tdd", "Prefer TDD when applicable.")
        ]
        self.prepare_and_freeze(hierarchy)
        control = Path(self.root) / ".layered-delivery"
        self.assertTrue((control / "overview.md").is_file())
        self.assertTrue((control / "hierarchy.json").is_file())
        self.assertTrue((control / "graph.json").is_file())
        self.assertTrue((control / "state.json").is_file())
        self.assertFalse((control / "development-plan.md").exists())
        overview = (control / "overview.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("springboot-tdd", overview)
        self.assertIn("不预先绑定节点", overview)

    def test_materialized_state_can_be_rebuilt_from_events(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id(root_id)
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-1",
            operation_id="op-rebuild",
            now=at(2),
        )
        repository = SchedulerRepository(self.root)
        run_id = repository.run(root_id)["runId"]
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE node_runs SET status = 'BLOCKED' "
                "WHERE run_id = ? AND node_id = ?",
                (run_id, node_id),
            )

        rebuilt = rebuild_graph_run(
            root=self.root,
            root_id=root_id,
        )

        state = next(
            item
            for item in rebuilt["nodes"]
            if item["nodeId"] == node_id
        )
        self.assertEqual(state["status"], "CLAIMED")
        self.assertGreater(rebuilt["rebuiltFromEvents"], 0)

    def test_loop_cancellation_blocks_the_run_with_a_frontier_action(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id(root_id)
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-1",
            operation_id="op-cancelled-loop",
            now=at(2),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-cancelled-loop",
            outcome={
                "status": "CANCELLED",
                "summary": "Internal Loop was cancelled.",
                "result": {},
            },
            now=at(3),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(4),
        )
        self.assertEqual(frontier["status"], "BLOCKED")
        self.assertIn(
            {
                "action": "RESOLVE_LOOP_CANCELLATION",
                "nodeId": node_id,
            },
            frontier["actions"],
        )


class RemovedCouplingTests(unittest.TestCase):
    def test_old_scope_gate_skill_and_plan_fields_are_rejected(
        self,
    ) -> None:
        source = task_hierarchy()
        definition = source["root"]["definition"]
        for field, value in (
            ("scope", ["src/**"]),
            ("gateLevel", "FULL"),
            ("requiredSkills", []),
            ("developmentPlan", {}),
        ):
            candidate = deepcopy(source)
            candidate["root"]["definition"][field] = value
            with self.subTest(field=field):
                with self.assertRaises(GatedLoopError):
                    validate_hierarchy_definition(candidate)

    def test_mcp_surface_contains_only_outer_scheduler_tools(
        self,
    ) -> None:
        names = {tool["name"] for tool in tool_definitions()}
        self.assertIn("dispatch_loop", names)
        self.assertIn("record_loop_result", names)
        self.assertNotIn("dispatch_task", names)
        self.assertNotIn("gate_item", names)
        self.assertNotIn("record_skill_activation", names)
        self.assertNotIn("record_skill_conformance", names)
        self.assertNotIn("remediate_task", names)


if __name__ == "__main__":
    unittest.main()
