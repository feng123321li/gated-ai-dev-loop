from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hdg.acceptance import accept_work_item
from hdg.errors import GatedLoopError
from hdg.execution import (
    dispatch_task,
    heartbeat_task,
    pause_task,
    record_task_result,
    resume_task,
)
from hdg.graph_model import execution_node_id, gate_node_id
from hdg.graph_runtime import (
    advance_graph,
    cancel_graph_run,
    get_evidence_contract,
    get_graph_frontier,
    get_graph_status,
    list_graph_events,
)
from hdg.planning import freeze_hierarchy, prepare_hierarchy, retry_work_item

from .fixtures import task_hierarchy
from .skill_helpers import (
    activate_required_skills,
    conform_required_skills,
)


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

    @staticmethod
    def _implemented_result(task_id: str, operation_id: str) -> dict:
        return {
            "schemaVersion": 3,
            "kind": "TASK_RESULT",
            "taskId": task_id,
            "operationId": operation_id,
            "status": "IMPLEMENTED",
            "summary": "The implementation and frozen regression completed.",
            "changedFiles": ["src/controller.py", "tests/test_controller.py"],
            "tests": [{
                "argv": ["python", "-m", "unittest", "tests.test_controller"],
                "exitCode": 0,
                "testsRun": 1,
            }],
            "blockers": [],
            "failure": None,
        }

    @staticmethod
    def _failed_gate(task_id: str, baseline: str, attempt: int) -> dict:
        return {
            "schemaVersion": 3,
            "kind": "WORK_ITEM_GATE",
            "workItemId": task_id,
            "baselineFingerprint": baseline,
            "verdict": "FAIL",
            "summary": f"Gate attempt {attempt} found a blocking P1 issue.",
            "scope": {
                "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                "outOfScopeFiles": [],
            },
            "acceptance": [{
                "id": "A-001",
                "requirementIds": ["R-001"],
                "status": "FAIL",
                "evidence": f"Blocking behavior remains after attempt {attempt}.",
            }],
            "tests": [{
                "argv": ["python", "-m", "unittest", "tests.test_controller"],
                "exitCode": 1,
                "testsRun": 1,
                "summary": "The gate regression failed.",
            }],
            "findings": {
                "p0": [],
                "p1": [f"P1 finding from gate attempt {attempt}."],
                "p2": [],
            },
        }

    def test_runtime_policy_and_bilingual_transition_flow_are_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            artifact_dir = Path(prepared["artifactDir"])
            self.assertEqual(
                prepared["humanArtifacts"]["stateTransitionGraph"],
                ".layered-delivery/state-transition-graph.md",
            )
            transition_graph = Path(
                temporary,
                ".layered-delivery",
                "state-transition-graph.md",
            ).read_text(
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

    def test_task_gate_failure_returns_to_execution_and_honors_retry_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            task_id = prepared["rootId"]
            baseline = prepared["baselineFingerprints"][task_id]

            for attempt in (1, 2, 3):
                operation_id = f"op-gate-remediation-{attempt}"
                dispatch_task(
                    root=temporary,
                    item_id=task_id,
                    owner="developer",
                    operation_id=operation_id,
                    now=self.START + timedelta(minutes=attempt * 2),
                )
                record_task_result(
                    root=temporary,
                    item_id=task_id,
                    operation_id=operation_id,
                    status="IMPLEMENTED",
                    evidence={
                        "schemaVersion": 3,
                        "kind": "TASK_RESULT",
                        "taskId": task_id,
                        "operationId": operation_id,
                        "status": "IMPLEMENTED",
                        "summary": f"Implemented gate remediation attempt {attempt}.",
                        "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                        "tests": [{
                            "argv": ["python", "-m", "unittest", "tests.test_controller"],
                            "exitCode": 0,
                            "testsRun": 1,
                        }],
                        "blockers": [],
                        "failure": None,
                    },
                    now=self.START + timedelta(minutes=attempt * 2, seconds=30),
                )
                accept_work_item(
                    root=temporary,
                    item_id=task_id,
                    evidence=self._failed_gate(task_id, baseline, attempt),
                    now=self.START + timedelta(minutes=attempt * 2 + 1),
                )

                gate_block = next(
                    item
                    for item in get_graph_frontier(
                        root=temporary,
                        work_item_id=task_id,
                    )["blocked"]
                    if item["nodeId"] == gate_node_id(task_id)
                )
                expected_action = "RETRY_NODE" if attempt < 3 else "REQUEST_INTERVENTION"
                self.assertEqual(gate_block["recommendedAction"], expected_action)

                if attempt < 3:
                    retried = retry_work_item(
                        root=temporary,
                        item_id=task_id,
                        expected_baseline_fingerprint=baseline,
                        now=self.START + timedelta(minutes=attempt * 2 + 1, seconds=30),
                    )
                    self.assertEqual(
                        {item["nodeId"] for item in retried["graphAttempts"]},
                        {execution_node_id(task_id), gate_node_id(task_id)},
                    )
                    states = {
                        item["id"]: item
                        for item in get_graph_status(
                            root=temporary,
                            work_item_id=task_id,
                        )["nodes"]
                    }
                    self.assertEqual(
                        (states[execution_node_id(task_id)]["attempt"],
                         states[execution_node_id(task_id)]["status"]),
                        (attempt + 1, "READY"),
                    )
                    self.assertEqual(
                        (states[gate_node_id(task_id)]["attempt"],
                         states[gate_node_id(task_id)]["status"]),
                        (attempt + 1, "PENDING"),
                    )
                    self.assertEqual(
                        [
                            action["action"]
                            for action in get_graph_frontier(
                                root=temporary,
                                work_item_id=task_id,
                            )["actions"]
                        ],
                        ["DISPATCH_TASK"],
                    )
                else:
                    with self.assertRaises(GatedLoopError) as raised:
                        retry_work_item(
                            root=temporary,
                            item_id=task_id,
                            expected_baseline_fingerprint=baseline,
                            now=self.START + timedelta(minutes=attempt * 2 + 1, seconds=30),
                        )
                    self.assertEqual(
                        raised.exception.code,
                        "WORK_ITEM_RETRY_EXHAUSTED",
                    )

            self.assertEqual(
                [
                    event["eventType"]
                    for event in list_graph_events(
                        root=temporary,
                        work_item_id=task_id,
                    )
                ].count("GRAPH_INVALIDATED"),
                2,
            )

    def test_blocked_required_gate_skill_routes_to_manual_intervention(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hierarchy = task_hierarchy()
            hierarchy["root"]["definition"]["requiredSkills"] = [{
                "name": "tdd-workflow",
                "stages": ["GATE"],
                "purpose": "Apply the complete gate verification workflow.",
            }]
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=hierarchy,
                host_runtime="codex",
                now=self.START,
            )
            task_id = prepared["rootId"]
            freeze_hierarchy(
                root=temporary,
                root_id=task_id,
                expected_hierarchy_fingerprint=prepared[
                    "hierarchyFingerprint"
                ],
                development_mode="active",
                confirmed=True,
                now=self.START,
            )
            dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer",
                operation_id="op-gate-skill-blocked",
                now=self.START + timedelta(minutes=1),
            )
            record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-gate-skill-blocked",
                status="IMPLEMENTED",
                evidence=self._implemented_result(
                    task_id,
                    "op-gate-skill-blocked",
                ),
                now=self.START + timedelta(minutes=2),
            )
            gate = self._failed_gate(
                task_id,
                prepared["baselineFingerprints"][task_id],
                1,
            )
            blocked_reason = (
                "The required Skill could not complete because its isolated "
                "review runtime was unavailable."
            )
            gate["skillUsage"] = [{
                "name": "tdd-workflow",
                "stage": "GATE",
                "status": "BLOCKED",
                "evidence": blocked_reason,
            }]
            blocked_receipts = activate_required_skills(
                temporary,
                task_id,
                "GATE",
                execution_id="gate-skill-blocked",
                executor_id="gate-reviewer",
                blocked=True,
                now=self.START + timedelta(minutes=2, seconds=30),
            )
            conform_required_skills(
                temporary,
                task_id,
                blocked_receipts,
                blocked=True,
                now=self.START + timedelta(minutes=2, seconds=45),
            )

            accept_work_item(
                root=temporary,
                item_id=task_id,
                evidence=gate,
                now=self.START + timedelta(minutes=3),
            )

            gate_block = next(
                item
                for item in get_graph_frontier(
                    root=temporary,
                    work_item_id=task_id,
                )["blocked"]
                if item["nodeId"] == gate_node_id(task_id)
            )
            self.assertEqual(gate_block["failureClass"], "NON_RETRYABLE")
            self.assertEqual(
                gate_block["recommendedAction"],
                "REQUEST_INTERVENTION",
            )
            self.assertEqual(
                gate_block["blockedSkillUsage"],
                [{
                    "name": "tdd-workflow",
                    "stage": "GATE",
                    "status": "BLOCKED",
                    "evidence": blocked_reason,
                }],
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

    def test_remediation_route_emits_the_exact_evidence_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            task_id = prepared["rootId"]
            dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer",
                operation_id="op-remediation-required",
                now=self.START + timedelta(minutes=1),
            )
            record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-remediation-required",
                status="BLOCKED",
                evidence=self._blocked_result(
                    task_id,
                    "op-remediation-required",
                    "REMEDIATION_REQUIRED",
                ),
                now=self.START + timedelta(minutes=2),
            )

            blocked = next(
                item
                for item in get_graph_frontier(
                    root=temporary,
                    work_item_id=task_id,
                )["blocked"]
                if item["nodeId"] == execution_node_id(task_id)
            )
            self.assertEqual(blocked["recommendedAction"], "SUBMIT_REMEDIATION")
            self.assertEqual(
                blocked["commandHint"],
                f"remediate-task --item {task_id} "
                f"--expected-baseline {prepared['baselineFingerprints'][task_id]} "
                "--evidence -",
            )
            self.assertEqual(
                blocked["evidenceContractRef"],
                {
                    "artifactKind": "VALIDATION_REMEDIATION",
                    "commandHint": (
                        f"evidence-contract --item {task_id} --kind remediation"
                    ),
                },
            )
            contract = get_evidence_contract(
                root=temporary,
                work_item_id=task_id,
                contract_kind="remediation",
            )["evidenceContract"]
            self.assertEqual(contract["taskId"], task_id)
            self.assertEqual(
                contract["constraints"]["sourceValues"],
                ["INDEPENDENT_REVIEW", "REGRESSION", "TASK_GATE", "USER_ACCEPTANCE"],
            )
            self.assertEqual(
                contract["constraints"]["alreadyAuthorizedFiles"],
                ["src/controller.py", "tests/test_controller.py"],
            )

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
            self.assertEqual(
                claimed["leasePolicy"],
                {
                    "leaseSeconds": 30 * 60,
                    "heartbeatIntervalSeconds": 5 * 60,
                    "graceSeconds": 2 * 60,
                    "heartbeatDueAt": self._at(
                        self.START + timedelta(minutes=5)
                    ),
                    "leaseExpiresAt": self._at(
                        self.START + timedelta(minutes=30)
                    ),
                    "hardExpiresAt": self._at(
                        self.START + timedelta(minutes=32)
                    ),
                    "responsibleParty": "EXECUTION_ADAPTER",
                    "commandHint": (
                        f"heartbeat-task --item {task_id} --operation op-lease"
                    ),
                },
            )
            dashboard = Path(
                prepared["artifactDir"],
                "frontier.md",
            ).read_text(encoding="utf-8")
            self.assertIn(
                "JUST_IN_TIME_ON_WORKER_START",
                dashboard,
            )
            self.assertIn(
                "## 执行中与心跳计划 / In Flight and Heartbeat Schedule",
                dashboard,
            )
            self.assertIn(
                self._at(self.START + timedelta(minutes=5)),
                dashboard,
            )
            waiting = get_graph_frontier(
                root=temporary,
                work_item_id=task_id,
                now=self.START + timedelta(minutes=4, seconds=59),
            )
            self.assertEqual(waiting["actions"], [])
            self.assertEqual(
                waiting["nextWakeAt"],
                self._at(self.START + timedelta(minutes=5)),
            )
            self.assertEqual(
                waiting["inFlight"][0]["heartbeatDueAt"],
                self._at(self.START + timedelta(minutes=5)),
            )
            due = get_graph_frontier(
                root=temporary,
                work_item_id=task_id,
                now=self.START + timedelta(minutes=5),
            )
            heartbeat_action = due["actions"][0]
            self.assertEqual(heartbeat_action["action"], "HEARTBEAT_TASK")
            self.assertEqual(heartbeat_action["urgency"], "NORMAL")
            self.assertEqual(
                heartbeat_action["hardExpiresAt"],
                self._at(self.START + timedelta(minutes=32)),
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
            self.assertEqual(
                heartbeat["leasePolicy"]["heartbeatDueAt"],
                self._at(self.START + timedelta(minutes=15)),
            )
            overdue = get_graph_frontier(
                root=temporary,
                work_item_id=task_id,
                now=self.START + timedelta(minutes=40, seconds=30),
            )
            self.assertEqual(overdue["actions"][0]["urgency"], "OVERDUE")
            self.assertEqual(
                advance_graph(
                    root=temporary,
                    work_item_id=task_id,
                    now=self.START + timedelta(minutes=41),
                )["decisions"],
                [],
            )
            record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-lease",
                status="IMPLEMENTED",
                evidence=self._implemented_result(task_id, "op-lease"),
                now=self.START + timedelta(minutes=41),
            )
            self.assertEqual(
                next(
                    item
                    for item in get_graph_status(
                        root=temporary,
                        work_item_id=task_id,
                    )["nodes"]
                    if item["id"] == execution_node_id(task_id)
                )["status"],
                "SUCCEEDED",
            )

    def test_hard_expiry_reaps_claim_and_fences_reused_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            task_id = prepared["rootId"]
            dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer",
                operation_id="op-expired",
                now=self.START,
            )
            expired_frontier = get_graph_frontier(
                root=temporary,
                work_item_id=task_id,
                now=self.START + timedelta(minutes=32),
            )
            self.assertNotIn(
                execution_node_id(task_id),
                {
                    item["nodeId"]
                    for item in expired_frontier["blocked"]
                },
            )
            self.assertEqual(
                expired_frontier["actions"],
                [{
                    "nodeId": execution_node_id(task_id),
                    "nodeKind": "TASK_EXECUTION",
                    "action": "ADVANCE_GRAPH",
                    "workItemId": task_id,
                    "attempt": 1,
                    "operationId": "op-expired",
                    "parallelGroup": None,
                    "readyBecause": ["claim-hard-expired"],
                    "critical": True,
                    "commandHint": f"advance-graph --item {task_id}",
                    "transition": "CLAIM_LEASE_EXPIRED",
                    "routeCondition": "ON_WORKER_LOST",
                    "failureClass": "WORKER_LOST",
                    "hardExpiresAt": self._at(
                        self.START + timedelta(minutes=32)
                    ),
                    "maxAttempts": 3,
                    "remainingAttempts": 2,
                    "retryExhausted": False,
                }],
            )
            advanced = advance_graph(
                root=temporary,
                work_item_id=task_id,
                now=self.START + timedelta(minutes=32),
            )
            self.assertEqual(advanced["decisions"][0]["failureClass"], "WORKER_LOST")
            self.assertEqual(advanced["decisions"][0]["action"], "RETRY_NODE")
            with self.assertRaises(GatedLoopError) as raised:
                record_task_result(
                    root=temporary,
                    item_id=task_id,
                    operation_id="op-expired",
                    status="IMPLEMENTED",
                    evidence=self._implemented_result(task_id, "op-expired"),
                    now=self.START + timedelta(minutes=32),
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_OPERATION_INVALID")
            with self.assertRaises(GatedLoopError) as raised:
                dispatch_task(
                    root=temporary,
                    item_id=task_id,
                    owner="developer",
                    operation_id="op-expired",
                    now=self.START + timedelta(minutes=32, seconds=1),
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_OPERATION_REUSED",
            )
            retry_frontier = get_graph_frontier(
                root=temporary,
                work_item_id=task_id,
                now=self.START + timedelta(minutes=32, seconds=1),
            )
            self.assertEqual(
                retry_frontier["dispatchPlan"]["dispatchTaskIds"],
                [task_id],
            )
            dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer",
                operation_id="op-recovered",
                now=self.START + timedelta(minutes=32, seconds=2),
            )
            result_contract = get_evidence_contract(
                root=temporary,
                work_item_id=task_id,
                contract_kind="result",
            )["evidenceContract"]
            self.assertEqual(
                result_contract["artifactTemplates"]["IMPLEMENTED"]["operationId"],
                "op-recovered",
            )
            recorded = record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-recovered",
                status="IMPLEMENTED",
                evidence=self._implemented_result(task_id, "op-recovered"),
                now=self.START + timedelta(minutes=32, seconds=3),
            )
            self.assertEqual(recorded["status"], "IMPLEMENTED")
            state = next(
                item for item in get_graph_status(
                    root=temporary,
                    work_item_id=task_id,
                )["nodes"]
                if item["id"] == execution_node_id(task_id)
            )
            self.assertEqual((state["attempt"], state["status"]), (2, "SUCCEEDED"))
            event_types = [event["eventType"] for event in list_graph_events(
                root=temporary,
                work_item_id=task_id,
            )]
            self.assertIn("CLAIM_LEASE_EXPIRED", event_types)
            self.assertIn("NODE_RETRY_SCHEDULED", event_types)
            self.assertEqual(event_types[-1], "TASK_IMPLEMENTED")


if __name__ == "__main__":
    unittest.main()
