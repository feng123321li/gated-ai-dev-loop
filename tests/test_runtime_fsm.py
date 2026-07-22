from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hdg.errors import GatedLoopError
from hdg.execution import (
    dispatch_task,
    heartbeat_task,
    pause_task,
    record_task_result,
    resume_task,
)
from hdg.graph_model import execution_node_id
from hdg.graph_runtime import (
    advance_graph,
    cancel_graph_run,
    get_graph_frontier,
    get_graph_status,
    list_graph_events,
)
from hdg.planning import freeze_hierarchy, prepare_hierarchy

from .fixtures import task_hierarchy


class RuntimeFsmTests(unittest.TestCase):
    START = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)

    @staticmethod
    def _at(value: datetime) -> str:
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _prepare_and_freeze(self, root: str) -> dict:
        prepared = prepare_hierarchy(
            root=root,
            hierarchy=task_hierarchy(),
            host_runtime="codex",
            now=self.START,
        )
        frozen = freeze_hierarchy(
            root=root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
            development_mode="active",
            confirmed=True,
            now=self.START,
        )
        return {**prepared, "frozen": frozen}

    @staticmethod
    def _blocked_result(
        task_id: str,
        operation_id: str,
        failure_class: str,
    ) -> dict:
        return {
            "schemaVersion": 3,
            "kind": "TASK_RESULT",
            "taskId": task_id,
            "operationId": operation_id,
            "status": "BLOCKED",
            "summary": "The structured runtime failure blocked this attempt.",
            "changedFiles": ["src/controller.py"],
            "tests": [{
                "argv": ["python", "-m", "unittest", "tests.test_controller"],
                "exitCode": 1,
                "testsRun": 1,
            }],
            "blockers": ["Structured failure"],
            "failure": {
                "class": failure_class,
                "code": "REGRESSION_FAILURE",
                "summary": "A regression prevented the attempt from completing.",
            },
        }

    def test_runtime_policy_and_bilingual_transition_flow_are_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            artifact_dir = Path(prepared["artifactDir"])
            self.assertEqual(
                prepared["humanArtifacts"]["stateTransitionGraph"],
                f".layered-delivery/work-items/{prepared['rootId']}/state-transition-graph.md",
            )
            transition_graph = (artifact_dir / "state-transition-graph.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("# 状态迁移图 / State Transition Graph", transition_graph)
            self.assertIn("## 开发执行流程 / Development Execution Flow", transition_graph)
            self.assertIn("失败分类 / Failure Classification", transition_graph)
            self.assertIn("尝试耗尽 / Retry Exhausted", transition_graph)
            self.assertIn("暂停 / Paused", transition_graph)
            self.assertIn("取消 / Cancelled", transition_graph)
            self.assertIn("```mermaid", transition_graph)

            plan = (artifact_dir / "development-plan.md").read_text(encoding="utf-8")
            self.assertIn("## 运行时策略 / Runtime Policy", plan)
            self.assertIn("最大尝试次数 / Max attempts", plan)
            status = get_graph_status(root=temporary, work_item_id=prepared["rootId"])
            self.assertEqual(status["runtime"]["retryPolicy"]["maxAttempts"], 3)
            self.assertGreater(len(status["runtime"]["transitions"]), 8)

    def test_retryable_failure_automatically_retries_then_exhausts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            task_id = prepared["rootId"]
            for attempt in (1, 2, 3):
                operation_id = f"op-attempt-{attempt}"
                dispatch_task(
                    root=temporary,
                    item_id=task_id,
                    owner="developer",
                    operation_id=operation_id,
                    now=self.START + timedelta(minutes=attempt),
                )
                result = record_task_result(
                    root=temporary,
                    item_id=task_id,
                    operation_id=operation_id,
                    status="BLOCKED",
                    evidence=self._blocked_result(task_id, operation_id, "RETRYABLE"),
                    now=self.START + timedelta(minutes=attempt, seconds=30),
                )
                if attempt < 3:
                    self.assertEqual(result["routingDecision"]["action"], "RETRY_NODE")
                    self.assertTrue(result["routingDecision"]["automatic"])
                    self.assertEqual(result["routingDecision"]["nextAttempt"], attempt + 1)
                    state = next(
                        item for item in get_graph_status(
                            root=temporary,
                            work_item_id=task_id,
                        )["nodes"]
                        if item["id"] == execution_node_id(task_id)
                    )
                    self.assertEqual((state["attempt"], state["status"]), (attempt + 1, "READY"))
                else:
                    self.assertEqual(result["routingDecision"]["action"], "BLOCK_RUN")
                    self.assertTrue(result["routingDecision"]["retryExhausted"])

            status = get_graph_status(root=temporary, work_item_id=task_id)
            node = next(
                item for item in status["nodes"]
                if item["id"] == execution_node_id(task_id)
            )
            self.assertEqual(status["run"]["status"], "BLOCKED")
            self.assertEqual(node["attempt"], 3)
            self.assertEqual(node["status"], "BLOCKED")
            self.assertTrue(node["retryExhausted"])
            frontier_node = next(
                item for item in get_graph_frontier(
                    root=temporary,
                    work_item_id=task_id,
                )["blocked"]
                if item["nodeId"] == execution_node_id(task_id)
            )
            self.assertEqual(frontier_node["remainingAttempts"], 0)
            self.assertEqual(frontier_node["recommendedAction"], "REQUEST_INTERVENTION")
            self.assertEqual(
                [event["eventType"] for event in list_graph_events(
                    root=temporary,
                    work_item_id=task_id,
                )].count("NODE_RETRY_SCHEDULED"),
                2,
            )
            self.assertEqual(
                [event["eventType"] for event in list_graph_events(
                    root=temporary,
                    work_item_id=task_id,
                )][-1],
                "RETRY_EXHAUSTED",
            )

    def test_contract_change_is_classified_but_not_automatically_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            task_id = prepared["rootId"]
            dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer",
                operation_id="op-contract",
                now=self.START + timedelta(minutes=1),
            )
            result = record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-contract",
                status="BLOCKED",
                evidence=self._blocked_result(task_id, "op-contract", "CONTRACT_CHANGE"),
                now=self.START + timedelta(minutes=2),
            )
            self.assertEqual(result["routingDecision"]["action"], "REQUEST_REVIEW")
            self.assertFalse(result["routingDecision"]["automatic"])
            state = next(
                item for item in get_graph_status(
                    root=temporary,
                    work_item_id=task_id,
                )["nodes"]
                if item["id"] == execution_node_id(task_id)
            )
            self.assertEqual((state["attempt"], state["status"]), (1, "BLOCKED"))
            blocked = next(
                item for item in get_graph_frontier(
                    root=temporary,
                    work_item_id=task_id,
                )["blocked"]
                if item["nodeId"] == execution_node_id(task_id)
            )
            self.assertEqual(blocked["failureClass"], "CONTRACT_CHANGE")
            self.assertEqual(blocked["recommendedAction"], "REQUEST_REVIEW")

    def test_pause_resume_and_cancel_are_explicit_fsm_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            task_id = prepared["rootId"]
            dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer",
                operation_id="op-pause",
                now=self.START + timedelta(minutes=1),
            )
            paused = pause_task(
                root=temporary,
                item_id=task_id,
                operation_id="op-pause",
                now=self.START + timedelta(minutes=2),
            )
            self.assertEqual(paused["nodeStatus"], "PAUSED")
            frontier = get_graph_frontier(root=temporary, work_item_id=task_id)
            self.assertEqual(frontier["actions"][0]["action"], "RESUME_TASK")
            resumed = resume_task(
                root=temporary,
                item_id=task_id,
                now=self.START + timedelta(minutes=3),
            )
            self.assertEqual(resumed["nodeStatus"], "READY")
            dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer-two",
                operation_id="op-after-resume",
                now=self.START + timedelta(minutes=4),
            )
            pause_task(
                root=temporary,
                item_id=task_id,
                operation_id="op-after-resume",
                now=self.START + timedelta(minutes=5),
            )
            with self.assertRaises(GatedLoopError) as raised:
                cancel_graph_run(
                    root=temporary,
                    work_item_id=task_id,
                    confirmed=False,
                )
            self.assertEqual(raised.exception.code, "CONFIRMATION_REQUIRED")
            cancelled = cancel_graph_run(
                root=temporary,
                work_item_id=task_id,
                confirmed=True,
                now=self.START + timedelta(minutes=6),
            )
            self.assertEqual(cancelled["status"], "CANCELLED")
            self.assertEqual(
                get_graph_frontier(root=temporary, work_item_id=task_id)["actions"],
                [],
            )
            with self.assertRaises(GatedLoopError) as raised:
                dispatch_task(
                    root=temporary,
                    item_id=task_id,
                    owner="developer",
                    operation_id="op-cancelled",
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_NOT_READY")

    def test_heartbeat_extends_lease_and_expired_claim_recovers_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            task_id = prepared["rootId"]
            claimed = dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer",
                operation_id="op-lease",
                now=self.START,
            )
            self.assertEqual(
                claimed["claim"]["leaseExpiresAt"],
                self._at(self.START + timedelta(minutes=30)),
            )
            heartbeat = heartbeat_task(
                root=temporary,
                item_id=task_id,
                operation_id="op-lease",
                now=self.START + timedelta(minutes=10),
            )
            self.assertEqual(
                heartbeat["claim"]["leaseExpiresAt"],
                self._at(self.START + timedelta(minutes=40)),
            )
            with self.assertRaises(GatedLoopError) as raised:
                record_task_result(
                    root=temporary,
                    item_id=task_id,
                    operation_id="op-lease",
                    status="BLOCKED",
                    evidence=self._blocked_result(task_id, "op-lease", "RETRYABLE"),
                    now=self.START + timedelta(minutes=41),
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_CLAIM_EXPIRED")

            advanced = advance_graph(
                root=temporary,
                work_item_id=task_id,
                now=self.START + timedelta(minutes=41),
            )
            self.assertEqual(advanced["decisions"][0]["failureClass"], "WORKER_LOST")
            self.assertEqual(advanced["decisions"][0]["action"], "RETRY_NODE")
            state = next(
                item for item in get_graph_status(
                    root=temporary,
                    work_item_id=task_id,
                )["nodes"]
                if item["id"] == execution_node_id(task_id)
            )
            self.assertEqual((state["attempt"], state["status"]), (2, "READY"))
            event_types = [event["eventType"] for event in list_graph_events(
                root=temporary,
                work_item_id=task_id,
            )]
            self.assertIn("TASK_HEARTBEAT", event_types)
            self.assertIn("CLAIM_LEASE_EXPIRED", event_types)
            self.assertEqual(event_types[-1], "NODE_RETRY_SCHEDULED")


if __name__ == "__main__":
    unittest.main()
