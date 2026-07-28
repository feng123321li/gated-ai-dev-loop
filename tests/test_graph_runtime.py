from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from hdg.acceptance import accept_work_item, record_acceptance
from hdg.errors import GatedLoopError
from hdg.evidence import (
    evidence_record,
    hydrate_gate_evidence,
    valid_gate_artifact,
)
from hdg.execution import dispatch_task, record_task_result
from hdg.graph_model import (
    confirmation_node_id,
    execution_node_id,
    gate_node_id,
    review_node_id,
)
from hdg.graph_runtime import (
    get_evidence_contract,
    get_graph_frontier,
    get_graph_replay,
    get_graph_status,
    list_graph_events,
    rebuild_graph_run,
)
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import GovernanceRepository

from .fixtures import task_hierarchy, two_task_capability_hierarchy


class DeliveryGraphRuntimeTests(unittest.TestCase):
    @staticmethod
    def _prepare_and_freeze(root: str) -> dict:
        prepared = prepare_hierarchy(
            root=root,
            hierarchy=task_hierarchy(),
            host_runtime="codex",
        )
        frozen = freeze_hierarchy(
            root=root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
            development_mode="active",
            confirmed=True,
        )
        return {**prepared, "frozen": frozen}

    @staticmethod
    def _task_result(task_id: str, operation_id: str, status: str = "IMPLEMENTED") -> dict:
        return {
            "schemaVersion": 3,
            "kind": "TASK_RESULT",
            "taskId": task_id,
            "operationId": operation_id,
            "status": status,
            "summary": "Graph runtime task result.",
            "changedFiles": ["src/controller.py", "tests/test_controller.py"],
            "tests": [{
                "argv": ["python", "-m", "unittest", "tests.test_controller"],
                "exitCode": 0 if status == "IMPLEMENTED" else 1,
                "testsRun": 1,
            }],
            "blockers": [] if status == "IMPLEMENTED" else ["Regression failure"],
            "failure": None if status == "IMPLEMENTED" else {
                "class": "RETRYABLE",
                "code": "REGRESSION_FAILURE",
                "summary": "The regression can be retried within the frozen contract.",
            },
        }

    @staticmethod
    def _gate(task_id: str, baseline: str) -> dict:
        return {
            "schemaVersion": 3,
            "kind": "WORK_ITEM_GATE",
            "workItemId": task_id,
            "baselineFingerprint": baseline,
            "verdict": "PASS",
            "summary": "Graph gate passed.",
            "scope": {
                "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                "outOfScopeFiles": [],
            },
            "acceptance": [{
                "id": "A-001",
                "requirementIds": ["R-001"],
                "status": "PASS",
                "evidence": "Verified.",
            }],
            "tests": [{
                "argv": ["python", "-m", "unittest", "tests.test_controller"],
                "exitCode": 0,
                "testsRun": 1,
                "summary": "Passed.",
            }],
            "findings": {"p0": [], "p1": [], "p2": []},
        }

    @staticmethod
    def _review() -> dict:
        return {
            "schemaVersion": 3,
            "kind": "INDEPENDENT_REVIEW",
            "reviewer": "fresh-reviewer",
            "isolation": "FRESH_READ_ONLY",
            "verdict": "PASS",
            "findings": {"p0": 0, "p1": 0},
        }

    @staticmethod
    def _confirmation() -> dict:
        return {
            "schemaVersion": 3,
            "kind": "USER_CONFIRMATION",
            "confirmedBy": "user",
            "decision": "CONFIRMED",
        }

    def _complete_root_task(self, root: str, prepared: dict) -> dict[str, dict]:
        task_id = prepared["rootId"]
        task_result = self._task_result(task_id, "op-complete")
        gate = self._gate(task_id, prepared["baselineFingerprints"][task_id])
        review = self._review()
        confirmation = self._confirmation()
        dispatch_task(root=root, item_id=task_id, owner="developer", operation_id="op-complete")
        record_task_result(
            root=root,
            item_id=task_id,
            operation_id="op-complete",
            status="IMPLEMENTED",
            evidence=task_result,
        )
        accept_work_item(root=root, item_id=task_id, evidence=gate)
        record_acceptance(
            root=root,
            item_id=task_id,
            action="INDEPENDENT_REVIEW_PASS",
            evidence=review,
        )
        record_acceptance(
            root=root,
            item_id=task_id,
            action="USER_CONFIRMED",
            evidence=confirmation,
        )
        return {
            "taskResult": task_result,
            "gate": gate,
            "review": review,
            "confirmation": confirmation,
        }

    def test_prepare_persists_compiled_graph_and_projects_chinese_views(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            self.assertRegex(prepared["graphFingerprint"], r"^[0-9a-f]{64}$")
            self.assertEqual(prepared["graphSummary"]["taskExecutions"], 1)
            package = Path(prepared["artifactDir"])
            graph_markdown = (package / "execution-graph.md").read_text(encoding="utf-8")
            self.assertIn("# 交付图", graph_markdown)
            self.assertIn("## 执行图", graph_markdown)
            self.assertIn("## 治理图", graph_markdown)
            self.assertIn("![执行图](assets/execution-graph.svg)", graph_markdown)
            self.assertIn("![治理图](assets/governance-graph.svg)", graph_markdown)
            self.assertIn("<summary>查看 Mermaid 源图</summary>", graph_markdown)
            self.assertNotIn(" / Delivery Graph", graph_markdown)
            governance = Path(temporary, ".layered-delivery")
            self.assertEqual(
                prepared["humanArtifacts"]["stateTransitionGraph"],
                ".layered-delivery/state-transition-graph.md",
            )
            self.assertFalse((package / "state-transition-graph.md").exists())
            state_markdown = (governance / "state-transition-graph.md").read_text(encoding="utf-8")
            workspace_overview = (governance / "workspace-overview.md").read_text(encoding="utf-8")
            self.assertIn(
                "[状态迁移图](state-transition-graph.md)",
                workspace_overview,
            )
            self.assertIn(
                "![开发执行流程](assets/development-flow.svg)",
                state_markdown,
            )
            self.assertIn(
                "![节点有限状态机](assets/node-state-machine.svg)",
                state_markdown,
            )
            for relative_path in (
                "assets/execution-graph.svg",
                "assets/governance-graph.svg",
            ):
                visual = package / relative_path
                self.assertTrue(visual.is_file())
                root_element = ET.fromstring(visual.read_text(encoding="utf-8"))
                self.assertEqual(root_element.tag, "{http://www.w3.org/2000/svg}svg")
            for relative_path in (
                "assets/development-flow.svg",
                "assets/node-state-machine.svg",
            ):
                self.assertFalse((package / relative_path).exists())
                visual = governance / relative_path
                self.assertTrue(visual.is_file())
                root_element = ET.fromstring(visual.read_text(encoding="utf-8"))
                self.assertEqual(root_element.tag, "{http://www.w3.org/2000/svg}svg")

            database = Path(temporary, ".layered-delivery", "governance.sqlite3")
            with closing(sqlite3.connect(database)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(
                    {
                        "graph_definitions", "graph_nodes", "graph_edges", "graph_runs",
                        "node_runs", "graph_events", "graph_evidence",
                    }
                    .issubset(tables)
                )
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM graph_definitions").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM graph_runs").fetchone()[0], 0)

    def test_multiple_requirements_share_one_workspace_state_transition_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            shared = Path(temporary, ".layered-delivery", "state-transition-graph.md")
            first_contents = shared.read_text(encoding="utf-8")

            second = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(id="t-second-controller"),
                host_runtime="codex",
            )

            self.assertEqual(shared.read_text(encoding="utf-8"), first_contents)
            self.assertEqual(
                first["humanArtifacts"]["stateTransitionGraph"],
                second["humanArtifacts"]["stateTransitionGraph"],
            )
            self.assertFalse(Path(first["artifactDir"], "state-transition-graph.md").exists())
            self.assertFalse(Path(second["artifactDir"], "state-transition-graph.md").exists())

    def test_freeze_rejects_a_tampered_graph_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            Path(prepared["artifactDir"], "execution-graph.md").write_text(
                "tampered graph\n",
                encoding="utf-8",
            )
            with self.assertRaises(GatedLoopError) as raised:
                freeze_hierarchy(
                    root=temporary,
                    root_id=prepared["rootId"],
                    expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                    development_mode="active",
                    confirmed=True,
                )
            self.assertEqual(raised.exception.code, "DELIVERY_GRAPH_PROJECTION_CHANGED")

    def test_freeze_rejects_a_tampered_shared_state_transition_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            Path(
                temporary,
                ".layered-delivery",
                "state-transition-graph.md",
            ).write_text("tampered runtime policy\n", encoding="utf-8")

            with self.assertRaises(GatedLoopError) as raised:
                freeze_hierarchy(
                    root=temporary,
                    root_id=prepared["rootId"],
                    expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                    development_mode="active",
                    confirmed=True,
                )

            self.assertEqual(raised.exception.code, "DELIVERY_GRAPH_PROJECTION_CHANGED")

    def test_freeze_rejects_a_tampered_shared_runtime_visual_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            Path(
                temporary,
                ".layered-delivery",
                "assets",
                "node-state-machine.svg",
            ).write_text("<svg>tampered</svg>\n", encoding="utf-8")

            with self.assertRaises(GatedLoopError) as raised:
                freeze_hierarchy(
                    root=temporary,
                    root_id=prepared["rootId"],
                    expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                    development_mode="active",
                    confirmed=True,
                )

            self.assertEqual(raised.exception.code, "DELIVERY_GRAPH_PROJECTION_CHANGED")

    def test_freeze_rejects_a_tampered_graph_visual_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            Path(
                prepared["artifactDir"],
                "assets",
                "execution-graph.svg",
            ).write_text("<svg>tampered</svg>\n", encoding="utf-8")
            with self.assertRaises(GatedLoopError) as raised:
                freeze_hierarchy(
                    root=temporary,
                    root_id=prepared["rootId"],
                    expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                    development_mode="active",
                    confirmed=True,
                )
            self.assertEqual(raised.exception.code, "DELIVERY_GRAPH_PROJECTION_CHANGED")

    def test_frontier_advances_from_task_to_gate_review_and_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            task_id = prepared["rootId"]
            frozen = prepared["frozen"]
            self.assertEqual(frozen["graphFingerprint"], prepared["graphFingerprint"])
            self.assertRegex(frozen["graphRun"]["runId"], r"^run-[a-z0-9-]+$")

            frontier = get_graph_frontier(root=temporary, work_item_id=task_id)
            self.assertEqual(frontier["actions"][0]["action"], "DISPATCH_TASK")
            self.assertEqual(frontier["actions"][0]["nodeId"], execution_node_id(task_id))

            dispatched = dispatch_task(
                root=temporary,
                item_id=task_id,
                owner="developer",
                operation_id="op-graph",
            )
            self.assertEqual(
                dispatched["context"]["resultEvidenceContractRef"],
                {
                    "artifactKind": "TASK_RESULT",
                    "mcpCall": {
                        "tool": "evidence_contract",
                        "arguments": {
                            "item_id": task_id,
                            "contract_kind": "result",
                        },
                    },
                },
            )
            handoff = (
                Path(temporary)
                / dispatched["humanArtifacts"]["developmentHandoff"]
            ).read_text(encoding="utf-8")
            self.assertNotIn(
                '"kind": "TASK_RESULT"',
                handoff,
            )
            self.assertNotIn("evidence-contract --item", handoff)
            self.assertIn(
                '"tool": "evidence_contract"',
                handoff,
            )
            result_contract_response = get_evidence_contract(
                root=temporary,
                work_item_id=task_id,
                contract_kind="result",
            )
            self.assertEqual(
                result_contract_response["submitMcpCalls"],
                [{
                    "tool": "task_result",
                    "arguments": {
                        "item_id": task_id,
                        "operation_id": "op-graph",
                        "status": "<IMPLEMENTED_OR_BLOCKED>",
                        "evidence": "<evidence>",
                    },
                }],
            )
            result_contract = result_contract_response["evidenceContract"]
            self.assertEqual(result_contract["artifactKind"], "TASK_RESULT")
            self.assertEqual(result_contract["operationId"], "op-graph")
            self.assertEqual(result_contract["submissionMode"], "DELTA")
            self.assertNotIn("artifactTemplates", result_contract)
            self.assertEqual(
                result_contract["constraints"]["testCommandIndexes"],
                [0],
            )
            self.assertEqual(
                result_contract["constraints"]["authorizedChangedFiles"],
                ["src/controller.py", "tests/test_controller.py"],
            )
            status = get_graph_status(root=temporary, work_item_id=task_id)
            execute = next(node for node in status["nodes"] if node["id"] == execution_node_id(task_id))
            self.assertEqual(execute["status"], "CLAIMED")
            self.assertEqual(execute["operationId"], "op-graph")

            invalid_result = self._task_result(task_id, "op-graph")
            invalid_result.pop("failure")
            invalid_result["tests"][0]["exitCode"] = "0"
            with self.assertRaises(GatedLoopError) as raised:
                record_task_result(
                    root=temporary,
                    item_id=task_id,
                    operation_id="op-graph",
                    status="IMPLEMENTED",
                    evidence=invalid_result,
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_RESULT_EVIDENCE_INVALID",
            )
            self.assertIn(
                "missing top-level keys: failure",
                raised.exception.details["issues"],
            )
            self.assertIn(
                "tests[0].exitCode must be an integer",
                raised.exception.details["issues"],
            )
            self.assertEqual(
                raised.exception.details["evidenceContract"],
                result_contract,
            )

            record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-graph",
                status="IMPLEMENTED",
                evidence=self._task_result(task_id, "op-graph"),
            )
            frontier = get_graph_frontier(root=temporary, work_item_id=task_id)
            self.assertNotIn("artifactTemplate", json.dumps(frontier))
            self.assertEqual(
                [(action["action"], action["nodeId"]) for action in frontier["actions"]],
                [("RUN_GATE", gate_node_id(task_id))],
            )
            gate_action = frontier["actions"][0]
            self.assertEqual(
                gate_action["evidenceContractRef"],
                {
                    "artifactKind": "WORK_ITEM_GATE",
                    "mcpCall": {
                        "tool": "evidence_contract",
                        "arguments": {
                            "item_id": task_id,
                            "contract_kind": "gate",
                        },
                    },
                },
            )
            gate_contract_response = get_evidence_contract(
                root=temporary,
                work_item_id=task_id,
                contract_kind="gate",
            )
            self.assertEqual(
                gate_contract_response["submitMcpCalls"],
                [{
                    "tool": "accept_item",
                    "arguments": {
                        "item_id": task_id,
                        "evidence": "<evidence>",
                    },
                }],
            )
            gate_contract = gate_contract_response["evidenceContract"]
            self.assertEqual(gate_contract["artifactKind"], "WORK_ITEM_GATE")
            self.assertEqual(gate_contract["submissionMode"], "DELTA")
            self.assertNotIn("artifactTemplate", gate_contract)
            self.assertEqual(
                gate_contract["constraints"]["acceptanceIds"],
                ["A-001"],
            )
            self.assertEqual(
                gate_contract["constraints"]["testCommandIndexes"],
                [0],
            )
            self.assertEqual(
                gate_contract["constraints"]["allowedChangedFiles"],
                ["src/controller.py", "tests/test_controller.py"],
            )
            fillable_delta = deepcopy(
                gate_contract["evidenceDeltaTemplate"]
            )
            fillable_delta["verdict"] = "PASS"
            fillable_delta["summary"] = (
                "The emitted gate evidence contract was filled directly."
            )
            fillable_delta["changedFiles"] = [
                "src/controller.py",
                "tests/test_controller.py",
            ]
            fillable_delta["acceptance"][0].update({
                "status": "PASS",
                "evidence": "The frozen acceptance criterion passed.",
            })
            fillable_delta["tests"][0].update({
                "exitCode": 0,
                "summary": "The frozen test command passed.",
            })
            registry = GovernanceRepository(temporary).read_registry()
            entry = next(item for item in registry["workItems"] if item["id"] == task_id)
            definition = GovernanceRepository(temporary).read_package(registry, entry)[0]
            fillable_gate = hydrate_gate_evidence(
                {"evidenceDelta": fillable_delta},
                entry=entry,
                definition=definition,
            )
            self.assertTrue(valid_gate_artifact(fillable_gate, entry, definition))
            invalid_gate = {"evidenceDelta": deepcopy(fillable_delta)}
            invalid_gate["evidenceDelta"].pop("tests")
            with self.assertRaises(GatedLoopError) as raised:
                accept_work_item(
                    root=temporary,
                    item_id=task_id,
                    evidence=invalid_gate,
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_GATE_EVIDENCE_INVALID",
            )
            self.assertIn(
                (
                    "tests argv must contain one exact match for every "
                    "frozen testCommand"
                ),
                raised.exception.details["issues"],
            )
            self.assertEqual(
                raised.exception.details["evidenceContract"],
                gate_contract,
            )

            accept_work_item(
                root=temporary,
                item_id=task_id,
                evidence=self._gate(task_id, prepared["baselineFingerprints"][task_id]),
            )
            review_action = get_graph_frontier(
                root=temporary,
                work_item_id=task_id,
            )["actions"][0]
            self.assertEqual(review_action["action"], "REQUEST_REVIEW")
            self.assertEqual(
                review_action["evidenceContractRef"]["mcpCall"],
                {
                    "tool": "evidence_contract",
                    "arguments": {
                        "item_id": task_id,
                        "contract_kind": "review",
                    },
                },
            )
            self.assertEqual(
                review_action["remediationContractRef"],
                {
                    "artifactKind": "VALIDATION_REMEDIATION",
                    "mcpCall": {
                        "tool": "evidence_contract",
                        "arguments": {
                            "item_id": "<original-task-id>",
                            "contract_kind": "remediation",
                        },
                    },
                },
            )
            review_contract_response = get_evidence_contract(
                root=temporary,
                work_item_id=task_id,
                contract_kind="review",
            )
            self.assertEqual(
                {
                    call["tool"]
                    for call in review_contract_response["submitMcpCalls"]
                },
                {
                    "record_independent_review_pass",
                    "record_human_review_acceptance",
                },
            )
            review_contract = review_contract_response["evidenceContract"]
            self.assertEqual(
                set(review_contract["actionOptions"]),
                {"INDEPENDENT_REVIEW_PASS", "HUMAN_REVIEW_ACCEPTED"},
            )
            remediation_contract_response = get_evidence_contract(
                root=temporary,
                work_item_id=task_id,
                contract_kind="remediation",
            )
            self.assertEqual(
                remediation_contract_response["submitMcpCalls"][0]["tool"],
                "remediate_task",
            )
            remediation_contract = remediation_contract_response["evidenceContract"]
            self.assertEqual(
                remediation_contract["constraints"]["acceptanceIds"],
                ["A-001"],
            )
            self.assertEqual(
                remediation_contract["constraints"]["alreadyAuthorizedFiles"],
                ["src/controller.py", "tests/test_controller.py"],
            )
            record_acceptance(
                root=temporary,
                item_id=task_id,
                action="INDEPENDENT_REVIEW_PASS",
                evidence={
                    "schemaVersion": 3,
                    "kind": "INDEPENDENT_REVIEW",
                    "reviewer": "fresh-reviewer",
                    "isolation": "FRESH_READ_ONLY",
                    "verdict": "PASS",
                    "findings": {"p0": 0, "p1": 0},
                },
            )
            confirmation_action = get_graph_frontier(
                root=temporary,
                work_item_id=task_id,
            )["actions"][0]
            self.assertEqual(
                confirmation_action["action"],
                "REQUEST_USER_CONFIRMATION",
            )
            confirmation_contract = get_evidence_contract(
                root=temporary,
                work_item_id=task_id,
                contract_kind="confirmation",
            )
            self.assertEqual(
                confirmation_contract["submitMcpCalls"],
                [{
                    "tool": "record_user_confirmation",
                    "arguments": {
                        "item_id": task_id,
                        "evidence": "<evidence>",
                    },
                }],
            )
            self.assertEqual(
                confirmation_contract["evidenceContract"]["artifactTemplate"],
                {
                    "schemaVersion": 3,
                    "kind": "USER_CONFIRMATION",
                    "confirmedBy": "<REQUIRED_NON_EMPTY_STRING>",
                    "decision": "CONFIRMED",
                },
            )
            record_acceptance(
                root=temporary,
                item_id=task_id,
                action="USER_CONFIRMED",
                evidence={
                    "schemaVersion": 3,
                    "kind": "USER_CONFIRMATION",
                    "confirmedBy": "user",
                    "decision": "CONFIRMED",
                },
            )
            completed = get_graph_status(root=temporary, work_item_id=task_id)
            self.assertEqual(completed["run"]["status"], "COMPLETED")
            self.assertEqual(get_graph_frontier(root=temporary, work_item_id=task_id)["actions"], [])
            event_types = [event["eventType"] for event in list_graph_events(root=temporary, work_item_id=task_id)]
            self.assertEqual(
                event_types,
                [
                    "GRAPH_RUN_STARTED",
                    "TASK_CLAIMED",
                    "TASK_IMPLEMENTED",
                    "GATE_PASSED",
                    "REVIEW_PASSED",
                    "USER_CONFIRMED",
                ],
            )
            events = list_graph_events(root=temporary, work_item_id=task_id)
            self.assertTrue(
                all(event["recordedAt"].endswith("Z") for event in events),
                "机器事件时间必须继续使用 UTC",
            )
            timeline = Path(prepared["artifactDir"], "run-timeline.md").read_text(encoding="utf-8")
            self.assertIn("# 运行时间线", timeline)
            self.assertIn("USER_CONFIRMED", timeline)
            self.assertRegex(
                timeline,
                r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
            )
            self.assertIn("时间（东八区）", timeline)
            self.assertNotIn(events[-1]["recordedAt"], timeline)

    def test_frontier_computes_the_complete_automatic_agent_dispatch_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=two_task_capability_hierarchy(),
                host_runtime="codex",
            )
            freeze_hierarchy(
                root=temporary,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )

            frontier = get_graph_frontier(
                root=temporary,
                work_item_id=prepared["rootId"],
            )
            plan = frontier["dispatchPlan"]
            self.assertEqual(plan["authority"], "GRAPH_CONTROLLER")
            self.assertEqual(plan["strategy"], "AUTO_DISPATCH_ALL_SAFE")
            self.assertEqual(
                plan["dispatchTaskIds"],
                ["t-python-controller", "t-python-worker"],
            )
            self.assertEqual(plan["desiredNewAgentCount"], 2)
            self.assertEqual(plan["activeAgentCount"], 0)
            self.assertEqual(plan["desiredTotalAgentCount"], 2)
            self.assertFalse(plan["hostSelectionAllowed"])
            self.assertEqual(plan["capacityPolicy"], "QUEUE_REMAINDER_STABLE")
            self.assertEqual(
                plan["claimPolicy"],
                "JUST_IN_TIME_ON_WORKER_START",
            )
            self.assertTrue(plan["queuedTasksRemainUnclaimed"])
            dispatches = [
                action
                for action in frontier["actions"]
                if action["action"] == "DISPATCH_TASK"
            ]
            self.assertEqual(
                [action["dispatchOrdinal"] for action in dispatches],
                [1, 2],
            )
            self.assertTrue(all(action["autoDispatch"] for action in dispatches))

            dispatch_task(
                root=temporary,
                item_id="t-python-controller",
                owner="graph-agent-1",
                operation_id="op-auto-1",
            )
            recalculated = get_graph_frontier(
                root=temporary,
                work_item_id=prepared["rootId"],
            )["dispatchPlan"]
            self.assertEqual(recalculated["dispatchTaskIds"], ["t-python-worker"])
            self.assertEqual(recalculated["desiredNewAgentCount"], 1)
            self.assertEqual(recalculated["activeAgentCount"], 1)
            self.assertEqual(recalculated["desiredTotalAgentCount"], 2)

            dashboard = Path(
                prepared["artifactDir"], "frontier.md"
            ).read_text(encoding="utf-8")
            self.assertIn(
                "## 自动智能体调度计划",
                dashboard,
            )
            self.assertIn("Graph 已确定全部本轮安全任务及稳定顺序", dashboard)

    def test_dispatch_rejects_a_ready_task_excluded_by_the_graph_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = self._prepare_and_freeze(temporary)
            second = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(
                    id="t-python-controller-shadow",
                    title="Python controller shadow",
                ),
                host_runtime="codex",
            )
            freeze_hierarchy(
                root=temporary,
                root_id=second["rootId"],
                expected_hierarchy_fingerprint=second["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )
            dispatch_task(
                root=temporary,
                item_id=first["rootId"],
                owner="graph-agent-1",
                operation_id="op-active-scope",
            )

            frontier = get_graph_frontier(
                root=temporary,
                work_item_id=second["rootId"],
            )
            self.assertEqual(frontier["dispatchPlan"]["dispatchTaskIds"], [])
            shadow_block = next(
                item
                for item in frontier["blocked"]
                if item["workItemId"] == second["rootId"]
                and item["nodeKind"] == "TASK_EXECUTION"
            )
            self.assertEqual(shadow_block["recommendedAction"], "WAIT_FOR_SCOPE")

            with self.assertRaises(GatedLoopError) as raised:
                dispatch_task(
                    root=temporary,
                    item_id=second["rootId"],
                    owner="graph-agent-2",
                    operation_id="op-out-of-plan",
                )
            self.assertEqual(raised.exception.code, "WORK_ITEM_NOT_READY")

    def test_retry_creates_a_new_node_attempt_without_changing_the_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            task_id = prepared["rootId"]
            dispatch_task(root=temporary, item_id=task_id, owner="developer", operation_id="op-blocked")
            record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-blocked",
                status="BLOCKED",
                evidence=self._task_result(task_id, "op-blocked", status="BLOCKED"),
            )
            after = get_graph_status(root=temporary, work_item_id=task_id)
            after_execute = next(node for node in after["nodes"] if node["id"] == execution_node_id(task_id))
            self.assertEqual(after["graphFingerprint"], prepared["graphFingerprint"])
            self.assertEqual(after_execute["attempt"], 2)
            self.assertEqual(after_execute["status"], "READY")

            replay = get_graph_replay(root=temporary, work_item_id=task_id)
            execution_attempts = [
                (item["attempt"], item["status"])
                for item in replay["attempts"]
                if item["nodeId"] == execution_node_id(task_id)
            ]
            self.assertEqual(execution_attempts, [(1, "BLOCKED"), (2, "READY")])

            database = Path(temporary, ".layered-delivery", "governance.sqlite3")
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "DELETE FROM node_runs WHERE node_id = ? AND attempt = 1",
                    (execution_node_id(task_id),),
                )
                connection.commit()
            damaged = get_graph_replay(root=temporary, work_item_id=task_id)
            self.assertFalse(damaged["consistentWithSnapshots"])
            self.assertEqual(damaged["mismatches"][0]["attempt"], 1)
            repaired = rebuild_graph_run(root=temporary, work_item_id=task_id, confirmed=True)
            self.assertTrue(repaired["consistentWithSnapshots"])
            self.assertEqual(
                [
                    (item["attempt"], item["status"])
                    for item in repaired["attempts"]
                    if item["nodeId"] == execution_node_id(task_id)
                ],
                [(1, "BLOCKED"), (2, "READY")],
            )

    def test_frontier_projects_a_chinese_critical_path_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            task_id = prepared["rootId"]
            frontier = get_graph_frontier(root=temporary, work_item_id=task_id)
            self.assertEqual(
                frontier["criticalPath"]["nodeIds"],
                [
                    execution_node_id(task_id),
                    gate_node_id(task_id),
                    review_node_id(task_id),
                    confirmation_node_id(task_id),
                ],
            )
            self.assertEqual(frontier["criticalPath"]["remainingNodes"], 4)
            self.assertIsNone(frontier["criticalPath"]["nextJoinNodeId"])
            dashboard = Path(prepared["artifactDir"], "frontier.md").read_text(encoding="utf-8")
            self.assertIn("# 图前沿", dashboard)
            self.assertIn("## 关键路径", dashboard)
            self.assertIn("## 可执行动作", dashboard)
            self.assertIn("## 阻断节点", dashboard)
            self.assertIn("```mermaid", dashboard)
            self.assertIn("任务执行", dashboard)
            self.assertNotIn(" / Graph Frontier", dashboard)

    def test_graph_evidence_is_bound_to_run_node_attempt_and_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            artifacts = self._complete_root_task(temporary, prepared)
            events = list_graph_events(root=temporary, work_item_id=prepared["rootId"])
            evidence_events = [
                event
                for event in events
                if event["eventType"]
                in {"TASK_IMPLEMENTED", "GATE_PASSED", "REVIEW_PASSED", "USER_CONFIRMED"}
            ]
            self.assertEqual(len(evidence_events), 4)
            expected_artifacts = [
                artifacts["taskResult"], artifacts["gate"], artifacts["review"],
                artifacts["confirmation"],
            ]
            for event, artifact in zip(evidence_events, expected_artifacts):
                binding = event["payload"]["evidenceBinding"]
                self.assertEqual(
                    set(binding),
                    {
                        "schemaVersion", "runId", "nodeId", "attempt", "graphFingerprint",
                        "artifactSha256", "boundEvidenceSha256",
                    },
                )
                self.assertEqual(binding["runId"], prepared["frozen"]["graphRun"]["runId"])
                self.assertEqual(binding["nodeId"], event["nodeId"])
                self.assertEqual(binding["attempt"], event["attempt"])
                self.assertEqual(binding["graphFingerprint"], prepared["graphFingerprint"])
                self.assertEqual(binding["artifactSha256"], evidence_record(artifact)["sha256"])

            records = GovernanceRepository(temporary).read_graph_evidence(prepared["rootId"])
            self.assertEqual(len(records), 4)
            for record, artifact in zip(records, expected_artifacts):
                self.assertEqual(record["boundArtifact"]["artifact"], artifact)
                self.assertEqual(
                    record["boundArtifact"]["binding"]["boundEvidenceSha256"],
                    record["boundEvidenceSha256"],
                )

    def test_tampered_graph_evidence_binding_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            task_id = prepared["rootId"]
            dispatch_task(root=temporary, item_id=task_id, owner="developer", operation_id="op-tamper")
            record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-tamper",
                status="IMPLEMENTED",
                evidence=self._task_result(task_id, "op-tamper"),
            )
            database = Path(temporary, ".layered-delivery", "governance.sqlite3")
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT evidence_id, bound_artifact_json FROM graph_evidence"
                ).fetchone()
                artifact = json.loads(row[1])
                artifact["artifact"]["summary"] = "tampered"
                connection.execute(
                    "UPDATE graph_evidence SET bound_artifact_json = ? WHERE evidence_id = ?",
                    (json.dumps(artifact), row[0]),
                )
                connection.commit()
            with self.assertRaises(GatedLoopError) as raised:
                list_graph_events(root=temporary, work_item_id=task_id)
            self.assertEqual(raised.exception.code, "DELIVERY_GRAPH_EVIDENCE_INVALID")

    def test_event_replay_reconstructs_and_repairs_node_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_and_freeze(temporary)
            self._complete_root_task(temporary, prepared)
            root_id = prepared["rootId"]
            replay = get_graph_replay(root=temporary, work_item_id=root_id)
            self.assertTrue(replay["consistentWithSnapshots"])
            self.assertEqual(replay["status"], "COMPLETED")
            self.assertEqual(replay["eventCount"], 6)
            self.assertRegex(replay["replayFingerprint"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                next(
                    node for node in replay["nodes"]
                    if node["nodeId"] == confirmation_node_id(root_id)
                )["status"],
                "COMPLETED",
            )

            database = Path(temporary, ".layered-delivery", "governance.sqlite3")
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "UPDATE node_runs SET status = 'PENDING' WHERE node_id = ?",
                    (confirmation_node_id(root_id),),
                )
                connection.commit()
            replay = get_graph_replay(root=temporary, work_item_id=root_id)
            self.assertFalse(replay["consistentWithSnapshots"])
            self.assertEqual(replay["mismatches"][0]["nodeId"], confirmation_node_id(root_id))
            with self.assertRaises(GatedLoopError) as raised:
                get_graph_status(root=temporary, work_item_id=root_id)
            self.assertEqual(raised.exception.code, "DELIVERY_GRAPH_REPLAY_MISMATCH")

            repaired = rebuild_graph_run(
                root=temporary,
                work_item_id=root_id,
                confirmed=True,
            )
            self.assertTrue(repaired["consistentWithSnapshots"])
            self.assertEqual(
                get_graph_status(root=temporary, work_item_id=root_id)["run"]["status"],
                "COMPLETED",
            )

if __name__ == "__main__":
    unittest.main()
