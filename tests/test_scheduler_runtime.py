from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread, current_thread
import unittest
from unittest.mock import patch

from hdg.errors import GatedLoopError
from hdg.graph_frontier import get_graph_frontier
from hdg.graph_model import (
    group_review_node_id,
    loop_node_id,
    review_node_id,
)
from hdg.graph_runtime import (
    cancel_graph_run,
    dispatch_loop,
    graph_events,
    graph_status,
    loop_context,
    pause_loop,
    rebuild_graph_run,
    record_loop_result,
    record_user_confirmation,
    resume_loop,
)
from hdg.loop_contracts import loop_execution_policy
from hdg.mcp_tools import tool_definitions
from hdg.model_core import validate_hierarchy_definition
from hdg.model_rendering import (
    PROJECTION_TEMPLATES,
    PROJECTION_TEMPLATE_VERSION,
    TASK_BASELINE_DIRECTORY,
)
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import SchedulerRepository

from .test_loop_architecture import (
    group_hierarchy,
    loop_descriptor,
    node,
    recursive_hierarchy,
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
    source = group_hierarchy()
    source["root"]["children"][1]["definition"]["execution"][
        "dependsOn"
    ] = []
    shared = ["project:erp/module:shared"]
    for child in source["root"]["children"]:
        child["definition"]["execution"]["loop"][
            "resourceClaims"
        ] = shared
    return source


def disjoint_parallel_hierarchy() -> dict:
    source = group_hierarchy()
    source["root"]["children"][1]["definition"]["execution"][
        "dependsOn"
    ] = []
    return source


def hierarchy_nodes(hierarchy: dict) -> list[dict]:
    pending = [hierarchy["root"]]
    result = []
    while pending:
        current = pending.pop()
        result.append(current)
        pending.extend(reversed(current["children"]))
    return result


def auditable_recursive_hierarchy() -> dict:
    source = recursive_hierarchy()
    for current in hierarchy_nodes(source):
        definition = current["definition"]
        item_id = definition["id"]
        definition["summary"] = f"Audit summary for {item_id}."
        if definition["kind"] == "TASK":
            loop = definition["execution"]["loop"]
            loop["ref"] = f"audit/task/{item_id}@1"
            loop["resourceClaims"] = [
                f"project:audit/task:{item_id}"
            ]
            loop["payload"] = {
                "rawAuditMarker": f"raw-task-payload::{item_id}",
                "nested": {"workItemId": item_id},
            }
        else:
            review = current["reviewLoop"]
            review["ref"] = f"audit/group-review/{item_id}@1"
            review["resourceClaims"] = [
                f"project:audit/group:{item_id}"
            ]
            review["payload"] = {
                "rawAuditMarker": f"raw-group-review::{item_id}",
                "nested": {"workItemId": item_id},
            }
    delivery = source["delivery"]
    delivery["summary"] = "Audit summary for the complete Delivery."
    delivery_review = delivery["reviewLoop"]
    delivery_review["ref"] = "audit/delivery-review/d-recursive@1"
    delivery_review["resourceClaims"] = [
        "project:audit/delivery:d-recursive"
    ]
    delivery_review["payload"] = {
        "rawAuditMarker": "raw-delivery-review::d-recursive",
        "nested": {"deliveryId": "d-recursive"},
    }
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
            [loop_node_id("t-service")],
        )

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-service"),
            owner="agent-1",
            operation_id="op-task-1",
            now=at(3),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-service"),
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
        terminal_before = graph_status(
            root=self.root,
            root_id=root_id,
        )
        terminal_frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(20),
        )
        terminal_after = graph_status(
            root=self.root,
            root_id=root_id,
        )
        self.assertEqual(terminal_frontier["status"], "COMPLETED")
        self.assertEqual(terminal_frontier["actions"], [])
        self.assertEqual(
            terminal_after["updatedAt"],
            terminal_before["updatedAt"],
        )
        self.assertEqual(
            terminal_after["completedAt"],
            terminal_before["completedAt"],
        )
        completed_overview = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / "overview.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "结果状态（outcome）：已成功（SUCCEEDED）",
            completed_overview,
        )

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
        hierarchy["root"]["skillHints"] = [
            skill_hint(
                "springboot-tdd",
                "Prefer TDD when the active Loop is a Spring task.",
            )
        ]
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]

        for node_id in (
            loop_node_id("t-service"),
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
                    hierarchy["root"]["skillHints"],
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

    def test_recursive_review_context_contains_all_upstream_loop_results(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(group_hierarchy())
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

        group_review_id = group_review_node_id("g-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=group_review_id,
            owner="group-reviewer",
            operation_id="op-group-review",
            now=at(6),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=group_review_id,
            operation_id="op-group-review",
            outcome=success("g-service review completed."),
            now=at(7),
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
            [
                loop_node_id("t-api"),
                loop_node_id("t-core"),
                group_review_id,
            ],
        )
        self.assertEqual(
            [
                item["outcome"]["summary"]
                for item in context["upstreamLoopResults"]
            ],
            [
                "t-api completed.",
                "t-core completed.",
                "g-service review completed.",
            ],
        )

    def test_reviews_progress_recursively_from_groups_to_delivery(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(recursive_hierarchy())
        root_id = prepared["rootId"]
        minute = 2

        def complete(node_id: str) -> None:
            nonlocal minute
            operation_id = f"op-{node_id.replace(':', '-')}"
            frontier = get_graph_frontier(
                root=self.root,
                root_id=root_id,
                now=at(minute),
            )
            self.assertIn(
                node_id,
                [item["nodeId"] for item in frontier["readyLoops"]],
            )
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                owner="recursive-agent",
                operation_id=operation_id,
                now=at(minute),
            )
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id=operation_id,
                outcome=success(f"{node_id} completed."),
                now=at(minute + 1),
            )
            minute += 2

        ordered_loops = [
            loop_node_id("t-bootstrap"),
            loop_node_id("t-model"),
            loop_node_id("t-repository"),
            group_review_node_id("g-domain"),
            loop_node_id("t-api"),
            group_review_node_id("g-backend"),
            loop_node_id("t-e2e"),
            group_review_node_id("g-quality"),
            loop_node_id("t-docs"),
            group_review_node_id("g-root"),
        ]
        for node_id in ordered_loops:
            complete(node_id)

        delivery_review_id = review_node_id(root_id)
        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=delivery_review_id,
        )
        self.assertEqual(
            {
                item["nodeId"]
                for item in context["upstreamLoopResults"]
            },
            set(ordered_loops),
        )
        complete(delivery_review_id)
        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(minute),
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

    def test_expired_worker_cannot_pause_or_submit_a_result(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
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

    def test_loop_capacity_handoff_reuses_the_frozen_graph(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        policy = loop_execution_policy()

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
        self.assertEqual(
            dispatch_action["executionPolicy"],
            policy,
        )

        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
        )
        self.assertEqual(context["executionPolicy"], policy)
        self.assertEqual(
            context["humanArtifacts"]["taskBaseline"],
            (
                f".layered-delivery/{root_id}/"
                f"{TASK_BASELINE_DIRECTORY}/t-service.md"
            ),
        )
        self.assertTrue(
            context["rules"]["coordinatorMustNotExecuteLoopInline"]
        )
        self.assertTrue(
            context["rules"][
                "capacityPressureMustPauseAndHandoff"
            ]
        )

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-original",
            operation_id="op-original",
            now=at(3),
        )
        paused = pause_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-original",
            now=at(4),
        )
        self.assertEqual(paused["status"], "PAUSED")
        self.assertEqual(paused["executionPolicy"], policy)
        self.assertEqual(
            paused["handoff"]["resumeSequence"],
            [
                "graph_frontier",
                "resume_loop",
                "graph_frontier",
                "loop_context",
                "dispatch_loop",
            ],
        )
        self.assertTrue(paused["handoff"]["reuseFrozenGraph"])
        self.assertFalse(paused["handoff"]["reprepare"])
        self.assertFalse(paused["handoff"]["refreeze"])

        paused_frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(5),
        )
        self.assertEqual(
            [item["nodeId"] for item in paused_frontier["pausedLoops"]],
            [node_id],
        )
        self.assertIn(
            {
                "action": "RESUME_LOOP_IN_INDEPENDENT_CONTEXT",
                "nodeId": node_id,
                "executionPolicy": policy,
            },
            paused_frontier["actions"],
        )

        resumed = resume_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            now=at(6),
        )
        self.assertEqual(resumed["status"], "READY")
        self.assertEqual(resumed["executionPolicy"], policy)
        self.assertIn("REDISPATCH", resumed["nextAction"])
        ready_frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(7),
        )
        self.assertEqual(ready_frontier["pausedLoops"], [])
        self.assertIn(
            node_id,
            [
                action["nodeId"]
                for action in ready_frontier["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )

    def test_infrastructure_failure_retries_but_loop_block_does_not(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
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

    def test_initial_frontier_reserves_shared_resources_deterministically(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(parallel_hierarchy())
        frontier = get_graph_frontier(
            root=self.root,
            root_id=prepared["rootId"],
            now=at(2),
        )

        dispatch_actions = [
            item
            for item in frontier["actions"]
            if item["action"] == "DISPATCH_LOOP"
        ]
        self.assertEqual(
            dispatch_actions,
            [
                {
                    "action": "DISPATCH_LOOP",
                    "nodeId": loop_node_id("t-api"),
                    "loopRef": "project/java-service-loop@1",
                    "executionPolicy": loop_execution_policy(),
                }
            ],
        )
        ready = {
            item["nodeId"]: item
            for item in frontier["readyLoops"]
        }
        self.assertEqual(
            ready[loop_node_id("t-api")]["resourceConflicts"],
            [],
        )
        self.assertEqual(
            ready[loop_node_id("t-core")]["resourceConflicts"],
            [loop_node_id("t-api")],
        )

    def test_initial_frontier_dispatches_disjoint_ready_loops(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(
            disjoint_parallel_hierarchy()
        )
        frontier = get_graph_frontier(
            root=self.root,
            root_id=prepared["rootId"],
            now=at(2),
        )

        self.assertEqual(
            [
                item["nodeId"]
                for item in frontier["actions"]
                if item["action"] == "DISPATCH_LOOP"
            ],
            [
                loop_node_id("t-api"),
                loop_node_id("t-core"),
            ],
        )
        self.assertTrue(
            all(
                not item["resourceConflicts"]
                for item in frontier["readyLoops"]
            )
        )

    def test_replan_required_suppresses_new_dispatches(self) -> None:
        prepared = self.prepare_and_freeze(
            disjoint_parallel_hierarchy()
        )
        root_id = prepared["rootId"]
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-api"),
            owner="agent-api",
            operation_id="op-api-replan",
            now=at(2),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-api"),
            operation_id="op-api-replan",
            outcome={
                "status": "REPLAN_REQUIRED",
                "summary": "The frozen topology must change.",
                "result": {"reason": "new dependency"},
            },
            now=at(3),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(4),
        )

        self.assertEqual(
            frontier["actions"],
            [
                {
                    "action": "REPLAN_HIERARCHY",
                    "nodeId": loop_node_id("t-api"),
                }
            ],
        )
        self.assertIn(
            loop_node_id("t-core"),
            [item["nodeId"] for item in frontier["readyLoops"]],
        )
        with self.assertRaises(GatedLoopError) as caught:
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=loop_node_id("t-core"),
                owner="agent-core",
                operation_id="op-stale-frontier",
                now=at(5),
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_REPLAN_REQUIRED",
        )

    def test_prepare_projection_is_namespaced_and_auditable(
        self,
    ) -> None:
        hierarchy = auditable_recursive_hierarchy()
        hierarchy["root"]["skillHints"] = [
            skill_hint("springboot-tdd", "Prefer TDD when applicable.")
        ]
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        control = Path(self.root) / ".layered-delivery"
        projections = control / prepared["rootId"]
        artifact_prefix = f".layered-delivery/{prepared['rootId']}"
        nodes = hierarchy_nodes(hierarchy)
        task_nodes = [
            current
            for current in nodes
            if current["definition"]["kind"] == "TASK"
        ]
        expected_task_baselines = {
            current["definition"]["id"]: (
                f"{artifact_prefix}/{TASK_BASELINE_DIRECTORY}/"
                f"{current['definition']['id']}.md"
            )
            for current in task_nodes
        }

        self.assertEqual(
            prepared["humanArtifacts"],
            {
                "overview": f"{artifact_prefix}/overview.md",
                "hierarchy": f"{artifact_prefix}/hierarchy.json",
                "graph": f"{artifact_prefix}/graph.json",
                "state": f"{artifact_prefix}/state.json",
                "taskBaselines": expected_task_baselines,
            },
        )
        self.assertTrue((control / "scheduler.db").is_file())
        self.assertTrue((projections / "overview.md").is_file())
        self.assertTrue((projections / "hierarchy.json").is_file())
        self.assertTrue((projections / "graph.json").is_file())
        self.assertFalse((projections / "state.json").exists())
        for filename in (
            "overview.md",
            "hierarchy.json",
            "graph.json",
            "state.json",
        ):
            self.assertFalse((control / filename).exists())
        overview = (projections / "overview.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("hierarchyFingerprint", overview)
        self.assertIn(prepared["hierarchyFingerprint"], overview)
        self.assertIn("graphFingerprint", overview)
        self.assertIn(prepared["graphFingerprint"], overview)
        self.assertIn(
            "层级状态（hierarchyStatus）：待冻结（PREPARED）",
            overview,
        )
        self.assertIn(
            "运行状态（runStatus）：未启动（NOT_STARTED）",
            overview,
        )
        self.assertIn("## GROUP/TASK 清单", overview)
        self.assertIn(
            "| 路径 | 类型 | 父级 | 同级依赖（dependsOn） | "
            "当前状态 | 标题 | TASK baseline |",
            overview,
        )
        self.assertIn("递归分组（GROUP）", overview)
        self.assertIn("任务 Loop（TASK）", overview)
        self.assertIn("springboot-tdd", overview)
        self.assertIn("不预先绑定节点", overview)
        self.assertIn(hierarchy["delivery"]["summary"], overview)

        baseline_root = projections / TASK_BASELINE_DIRECTORY
        self.assertTrue(baseline_root.is_dir())
        self.assertEqual(
            {
                path.name
                for path in baseline_root.iterdir()
                if path.is_file()
            },
            {
                f"{current['definition']['id']}.md"
                for current in task_nodes
            },
        )
        for current in nodes:
            definition = current["definition"]
            item_id = definition["id"]
            with self.subTest(work_item_id=item_id):
                self.assertIn(item_id, overview)
                if definition["kind"] == "TASK":
                    loop = definition["execution"]["loop"]
                    baseline = (
                        baseline_root / f"{item_id}.md"
                    ).read_text(encoding="utf-8")
                    self.assertIn(
                        f"投影模板版本：{PROJECTION_TEMPLATE_VERSION}",
                        baseline,
                    )
                    self.assertIn(
                        prepared["hierarchyFingerprint"],
                        baseline,
                    )
                    self.assertIn(
                        prepared["graphFingerprint"],
                        baseline,
                    )
                    self.assertIn(definition["summary"], baseline)
                    for dependency in definition["execution"][
                        "dependsOn"
                    ]:
                        self.assertIn(dependency, baseline)
                    self.assertIn(loop["ref"], baseline)
                    self.assertIn(
                        loop["resourceClaims"][0],
                        baseline,
                    )
                    self.assertIn(
                        loop["payload"]["rawAuditMarker"],
                        baseline,
                    )
                    self.assertIn("springboot-tdd", baseline)
                    self.assertNotIn(definition["summary"], overview)
                    self.assertNotIn(loop["ref"], overview)
                    self.assertNotIn(
                        loop["payload"]["rawAuditMarker"],
                        overview,
                    )
                else:
                    self.assertIn(definition["summary"], overview)
                    for dependency in definition["decomposition"][
                        "dependsOn"
                    ]:
                        self.assertIn(dependency, overview)
                    review = current["reviewLoop"]
                    self.assertIn(review["ref"], overview)
                    self.assertIn(
                        review["resourceClaims"][0],
                        overview,
                    )
                    self.assertIn(
                        review["payload"]["rawAuditMarker"],
                        overview,
                    )

        delivery_review = hierarchy["delivery"]["reviewLoop"]
        self.assertIn(delivery_review["ref"], overview)
        self.assertIn(
            delivery_review["resourceClaims"][0],
            overview,
        )
        self.assertIn(
            delivery_review["payload"]["rawAuditMarker"],
            overview,
        )
        self.assertIn('"rawAuditMarker"', overview)

        work_item_ids = {
            current["definition"]["id"]
            for current in nodes
        }
        for item_id in work_item_ids:
            self.assertFalse((control / item_id).exists())
            self.assertFalse((projections / item_id).exists())
        self.assertFalse(
            any(
                path.name == "development-plan.md"
                for path in control.rglob("*")
            )
        )

    def test_projection_set_is_fixed_and_rebuilt_from_sqlite(
        self,
    ) -> None:
        self.assertEqual(
            set(PROJECTION_TEMPLATES),
            {
                "hierarchy.json",
                "graph.json",
                "state.json",
                "overview.md",
            },
        )
        self.assertGreaterEqual(PROJECTION_TEMPLATE_VERSION, 2)
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=auditable_recursive_hierarchy(),
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
        projection_root = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
        )
        filenames = set(PROJECTION_TEMPLATES)
        original = {
            filename: (projection_root / filename).read_bytes()
            for filename in filenames
        }
        baseline_root = projection_root / TASK_BASELINE_DIRECTORY
        original_baselines = {
            path.name: path.read_bytes()
            for path in baseline_root.iterdir()
            if path.is_file()
        }
        for filename in filenames:
            (projection_root / filename).write_text(
                f"agent-authored replacement: {filename}\n",
                encoding="utf-8",
            )
        for filename in original_baselines:
            (baseline_root / filename).write_text(
                f"agent-authored replacement: {filename}\n",
                encoding="utf-8",
            )
        (baseline_root / "stale-agent-file.md").write_text(
            "not controller data\n",
            encoding="utf-8",
        )

        repository = SchedulerRepository(self.root)
        repository.write_projections(prepared["rootId"])

        rebuilt = {
            filename: (projection_root / filename).read_bytes()
            for filename in filenames
        }
        rebuilt_baselines = {
            path.name: path.read_bytes()
            for path in baseline_root.iterdir()
            if path.is_file()
        }
        self.assertEqual(rebuilt, original)
        self.assertEqual(rebuilt_baselines, original_baselines)
        self.assertNotIn(
            "stale-agent-file.md",
            rebuilt_baselines,
        )
        shutil.rmtree(baseline_root)
        baseline_root.write_text(
            "agent replaced the controller directory\n",
            encoding="utf-8",
        )
        repository.write_projections(prepared["rootId"])
        self.assertTrue(baseline_root.is_dir())
        self.assertEqual(
            {
                path.name: path.read_bytes()
                for path in baseline_root.iterdir()
                if path.is_file()
            },
            original_baselines,
        )
        stored = repository.hierarchy(prepared["rootId"])
        self.assertEqual(
            json.loads(rebuilt["hierarchy.json"]),
            stored["hierarchy"],
        )
        self.assertEqual(
            json.loads(rebuilt["graph.json"]),
            stored["graph"],
        )
        self.assertEqual(
            json.loads(rebuilt["state.json"]),
            repository.run(prepared["rootId"]),
        )
        self.assertIn(
            f"投影模板版本：{PROJECTION_TEMPLATE_VERSION}",
            rebuilt["overview.md"].decode("utf-8"),
        )

    def test_reprepare_replaces_the_exact_task_baseline_set(
        self,
    ) -> None:
        original_hierarchy = task_hierarchy()
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=original_hierarchy,
            now=at(0),
        )
        baseline_root = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
            / TASK_BASELINE_DIRECTORY
        )
        self.assertTrue((baseline_root / "t-service.md").is_file())

        replacement = task_hierarchy()
        replacement["root"]["definition"]["id"] = "t-replacement"
        replacement["root"]["definition"]["title"] = "Replacement task"
        replacement["root"]["definition"]["summary"] = (
            "Execute the replacement Task Loop."
        )
        updated = prepare_hierarchy(
            root=self.root,
            hierarchy=replacement,
            now=at(1),
        )

        self.assertEqual(updated["rootId"], prepared["rootId"])
        self.assertFalse((baseline_root / "t-service.md").exists())
        replacement_baseline = baseline_root / "t-replacement.md"
        self.assertTrue(replacement_baseline.is_file())
        self.assertIn(
            "Execute the replacement Task Loop.",
            replacement_baseline.read_text(encoding="utf-8"),
        )

    def test_concurrent_disjoint_dispatch_projection_does_not_regress(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(
            disjoint_parallel_hierarchy()
        )
        root_id = prepared["rootId"]
        earlier_waiting = Event()
        release_earlier = Event()
        later_finished = Event()
        clock_lock = Lock()
        errors: list[BaseException] = []
        expected_machine_time = (
            at(3).isoformat().replace("+00:00", "Z")
        )
        clock_values = iter(
            [
                at(2).isoformat().replace("+00:00", "Z"),
                expected_machine_time,
            ]
        )
        original_transaction = SchedulerRepository.transaction

        def ordered_timestamp(now: object = None) -> str:
            del now
            with clock_lock:
                return next(clock_values)

        @contextmanager
        def coordinated_transaction(
            repository: SchedulerRepository,
        ):
            if current_thread().name == "earlier-dispatch":
                earlier_waiting.set()
                if not release_earlier.wait(timeout=5):
                    raise AssertionError(
                        "Timed out releasing the earlier dispatch"
                    )
            with original_transaction(repository) as connection:
                yield connection

        def claim(
            *,
            item_id: str,
            operation_id: str,
            finished: Event | None = None,
        ) -> None:
            try:
                dispatch_loop(
                    root=self.root,
                    root_id=root_id,
                    node_id=loop_node_id(item_id),
                    owner=current_thread().name,
                    operation_id=operation_id,
                )
            except BaseException as error:
                errors.append(error)
            finally:
                if finished is not None:
                    finished.set()

        with (
            patch(
                "hdg.graph_runtime.timestamp",
                new=ordered_timestamp,
            ),
            patch.object(
                SchedulerRepository,
                "transaction",
                new=coordinated_transaction,
            ),
        ):
            earlier = Thread(
                target=claim,
                kwargs={
                    "item_id": "t-api",
                    "operation_id": "op-concurrent-earlier",
                },
                name="earlier-dispatch",
            )
            later = Thread(
                target=claim,
                kwargs={
                    "item_id": "t-core",
                    "operation_id": "op-concurrent-later",
                    "finished": later_finished,
                },
                name="later-dispatch",
            )
            earlier.start()
            self.assertTrue(earlier_waiting.wait(timeout=5))
            later.start()
            try:
                self.assertTrue(later_finished.wait(timeout=5))
            finally:
                release_earlier.set()
            earlier.join(timeout=5)
            later.join(timeout=5)

        self.assertFalse(earlier.is_alive())
        self.assertFalse(later.is_alive())
        self.assertEqual(errors, [])

        run = SchedulerRepository(self.root).run(root_id)
        claimed_at = {
            node["nodeId"]: node["claimedAt"]
            for node in run["nodes"]
            if node["nodeId"]
            in {
                loop_node_id("t-api"),
                loop_node_id("t-core"),
            }
        }
        self.assertEqual(
            set(claimed_at),
            {
                loop_node_id("t-api"),
                loop_node_id("t-core"),
            },
        )
        self.assertTrue(all(claimed_at.values()))
        self.assertEqual(run["updatedAt"], expected_machine_time)
        run_updated = datetime.fromisoformat(
            run["updatedAt"].replace("Z", "+00:00")
        )
        claimed_times = [
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            for value in claimed_at.values()
        ]
        self.assertGreaterEqual(run_updated, max(claimed_times))

        projection_root = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
        )
        state = json.loads(
            (projection_root / "state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["updatedAt"], expected_machine_time)
        human_time = at(3).astimezone(
            timezone(timedelta(hours=8))
        ).isoformat(timespec="seconds")
        overview = (projection_root / "overview.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            [
                line
                for line in overview.splitlines()
                if line.startswith("- 更新时间（UTC+8）：")
            ],
            [f"- 更新时间（UTC+8）：{human_time}"],
        )

    def test_delivery_ids_retain_separate_requirement_projections(
        self,
    ) -> None:
        first = task_hierarchy()
        second = deepcopy(first)
        second["delivery"].update(
            {
                "id": "d-secondary",
                "title": "第二个交付需求",
                "summary": "保留独立的需求投影目录。",
            }
        )

        first_prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=first,
            now=at(0),
        )
        second_prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=second,
            now=at(1),
        )

        control = Path(self.root) / ".layered-delivery"
        first_overview = (
            control / first_prepared["rootId"] / "overview.md"
        )
        second_overview = (
            control / second_prepared["rootId"] / "overview.md"
        )
        self.assertTrue(first_overview.is_file())
        self.assertTrue(second_overview.is_file())
        self.assertIn(
            first_prepared["rootId"],
            first_overview.read_text(encoding="utf-8"),
        )
        self.assertIn(
            second_prepared["rootId"],
            second_overview.read_text(encoding="utf-8"),
        )

    def test_frozen_projection_contains_runtime_progress(self) -> None:
        hierarchy = auditable_recursive_hierarchy()
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        frozen = freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        projections = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
        )
        overview = (projections / "overview.md").read_text(
            encoding="utf-8"
        )

        self.assertEqual(frozen["status"], "ACTIVE")
        self.assertTrue((projections / "state.json").is_file())
        self.assertIn("ACTIVE", overview)
        statuses = {
            state["status"]
            for state in frozen["nodes"]
        }
        self.assertIn("READY", statuses)
        self.assertIn("PENDING", statuses)
        lines = overview.splitlines()
        for state in frozen["nodes"]:
            with self.subTest(node_id=state["nodeId"]):
                self.assertTrue(
                    any(
                        state["nodeId"] in line
                        and state["status"] in line
                        for line in lines
                    ),
                    (
                        f"overview must pair {state['nodeId']} with "
                        f"{state['status']}"
                ),
            )

    def test_projection_labels_statuses_and_times_are_localized(
        self,
    ) -> None:
        prepared_at = datetime(
            2026,
            1,
            1,
            0,
            0,
            tzinfo=timezone.utc,
        )
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=task_hierarchy(),
            now=prepared_at,
        )
        overview_path = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
            / "overview.md"
        )
        prepared_overview = overview_path.read_text(encoding="utf-8")

        self.assertIn(
            "更新时间（UTC+8）：2026-01-01T08:00:00+08:00",
            prepared_overview,
        )
        self.assertNotIn(
            "更新时间（UTC+8）：2026-01-01T00:00:00Z",
            prepared_overview,
        )
        self.assertIn(
            "层级状态（hierarchyStatus）：待冻结（PREPARED）",
            prepared_overview,
        )
        self.assertIn("任务 Loop（TASK）", prepared_overview)

        freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=prepared_at + timedelta(minutes=1),
        )
        dispatch_loop(
            root=self.root,
            root_id=prepared["rootId"],
            node_id=loop_node_id("t-service"),
            owner="agent-local-time",
            operation_id="op-local-time",
            now=prepared_at + timedelta(minutes=2),
        )
        active_overview = overview_path.read_text(encoding="utf-8")

        self.assertIn(
            "层级状态（hierarchyStatus）：已冻结（FROZEN）",
            active_overview,
        )
        self.assertIn(
            "运行状态（runStatus）：运行中（ACTIVE）",
            active_overview,
        )
        self.assertIn("执行中（CLAIMED）", active_overview)
        for expected in (
            "认领时间（UTC+8）：2026-01-01T08:02:00+08:00",
            "最近心跳（UTC+8）：2026-01-01T08:02:00+08:00",
            "租约到期（UTC+8）：2026-01-01T08:32:00+08:00",
        ):
            self.assertIn(expected, active_overview)
        self.assertNotRegex(
            active_overview,
            r"2026-01-01T\d{2}:\d{2}:\d{2}Z",
        )

    def test_materialized_state_can_be_rebuilt_from_events(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
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

    def test_rebuild_does_not_overwrite_a_concurrent_claim(self) -> None:
        prepared = self.prepare_and_freeze(
            disjoint_parallel_hierarchy()
        )
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-api")
        operation_id = "op-during-rebuild"
        snapshot_captured = Event()
        release_snapshot = Event()
        dispatch_finished = Event()
        errors: list[BaseException] = []
        original_events = SchedulerRepository.events

        def held_event_snapshot(
            repository: SchedulerRepository,
            *args: object,
            **kwargs: object,
        ) -> list[dict]:
            page = original_events(repository, *args, **kwargs)
            if (
                current_thread().name == "rebuild-thread"
                and not snapshot_captured.is_set()
            ):
                snapshot_captured.set()
                if not release_snapshot.wait(timeout=5):
                    raise AssertionError(
                        "Timed out releasing the rebuild event snapshot"
                    )
            return page

        def rebuild() -> None:
            try:
                rebuild_graph_run(root=self.root, root_id=root_id)
            except BaseException as error:
                errors.append(error)

        def claim() -> None:
            try:
                dispatch_loop(
                    root=self.root,
                    root_id=root_id,
                    node_id=node_id,
                    owner="concurrent-agent",
                    operation_id=operation_id,
                    now=at(2),
                )
            except BaseException as error:
                errors.append(error)
            finally:
                dispatch_finished.set()

        with patch.object(
            SchedulerRepository,
            "events",
            new=held_event_snapshot,
        ):
            rebuild_thread = Thread(
                target=rebuild,
                name="rebuild-thread",
            )
            dispatch_thread = Thread(
                target=claim,
                name="dispatch-during-rebuild",
            )
            rebuild_thread.start()
            self.assertTrue(snapshot_captured.wait(timeout=5))
            dispatch_thread.start()
            try:
                dispatch_finished.wait(timeout=1)
            finally:
                release_snapshot.set()
            rebuild_thread.join(timeout=5)
            dispatch_thread.join(timeout=5)

        self.assertFalse(rebuild_thread.is_alive())
        self.assertFalse(dispatch_thread.is_alive())
        self.assertEqual(errors, [])

        events = graph_events(root=self.root, root_id=root_id)["events"]
        claim_event = next(
            event
            for event in events
            if event["eventType"] == "LOOP_CLAIMED"
            and event["operationId"] == operation_id
        )
        run = graph_status(root=self.root, root_id=root_id)
        state = next(
            item
            for item in run["nodes"]
            if item["nodeId"] == node_id
        )

        self.assertEqual(state["status"], "CLAIMED")
        self.assertEqual(state["operationId"], operation_id)
        self.assertEqual(
            state["claimedAt"],
            claim_event["recordedAt"],
        )
        self.assertGreaterEqual(
            datetime.fromisoformat(
                run["updatedAt"].replace("Z", "+00:00")
            ),
            datetime.fromisoformat(
                claim_event["recordedAt"].replace("Z", "+00:00")
            ),
        )

    def test_loop_cancellation_blocks_the_run_with_a_frontier_action(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
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

    def test_cancelled_graph_is_a_stable_terminal_frontier(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        cancelled = cancel_graph_run(
            root=self.root,
            root_id=root_id,
            cancelled_by="human",
            reason="Requirement withdrawn.",
            now=at(2),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(20),
        )
        after = graph_status(root=self.root, root_id=root_id)

        self.assertEqual(frontier["status"], "CANCELLED")
        self.assertEqual(frontier["actions"], [])
        self.assertEqual(frontier["blockedLoops"], [])
        self.assertEqual(after["status"], "CANCELLED")
        self.assertEqual(after["updatedAt"], cancelled["updatedAt"])
        self.assertEqual(after["cancelledAt"], cancelled["cancelledAt"])


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
        tools = tool_definitions()
        names = {tool["name"] for tool in tools}
        self.assertIn("dispatch_loop", names)
        self.assertIn("record_loop_result", names)
        self.assertNotIn("dispatch_task", names)
        self.assertNotIn("gate_item", names)
        self.assertNotIn("record_skill_activation", names)
        self.assertNotIn("record_skill_conformance", names)
        self.assertNotIn("remediate_task", names)
        self.assertTrue(
            {
                "execute_sql",
                "query_sqlite",
                "write_projection",
                "refresh_projections",
            }.isdisjoint(names)
        )
        forbidden_arguments = {
            "sql",
            "query",
            "template",
            "filename",
            "content",
            "projection",
        }
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertNotIn("sql", tool["name"].lower())
                self.assertNotIn(
                    "projection",
                    tool["name"].lower(),
                )
                self.assertTrue(
                    forbidden_arguments.isdisjoint(
                        tool["inputSchema"]["properties"]
                    )
                )


if __name__ == "__main__":
    unittest.main()
