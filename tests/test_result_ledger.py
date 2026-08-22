from __future__ import annotations

from copy import deepcopy
import unittest

from hdg.controller import CONTROLLER_OPERATIONS
from hdg.execution_metrics import build_execution_metrics
from hdg.graph_model import compile_delivery_graph
from hdg.mcp_tools import tool_definitions
from hdg.model import hierarchy_fingerprint
from hdg.result_ledger import (
    assemble_delivery_result,
    assert_result_ledger_complete,
    build_result_ledger,
)
from hdg.review_contracts import validate_review_result_contract
from hdg.errors import GatedLoopError

from .loop_architecture_support import task_hierarchy
from .scheduler_runtime_support import review_success, success


def _completed_run(graph: dict) -> dict:
    nodes = []
    for definition in graph["nodes"]:
        kind = definition["kind"]
        if kind == "USER_CONFIRMATION":
            status = "READY"
            outcome = None
        elif kind == "TASK_LOOP":
            status = "SUCCEEDED"
            outcome = success(f"{definition['id']} completed.")
        else:
            status = "SUCCEEDED"
            outcome = review_success(
                kind,
                f"{definition['id']} completed.",
            )
        nodes.append(
            {
                "nodeId": definition["id"],
                "attempt": 1,
                "status": status,
                "outcome": outcome,
            }
        )
    return {
        "rootId": graph["rootId"],
        "runId": "run-result-ledger",
        "deliveryRevision": 1,
        "status": "ACTIVE",
        "nodes": nodes,
    }


class ResultLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hierarchy = task_hierarchy()
        self.graph = compile_delivery_graph(
            self.hierarchy,
            hierarchy_fingerprint=hierarchy_fingerprint(self.hierarchy),
        )
        self.run = _completed_run(self.graph)

    def test_ledger_contains_every_loop_result_in_stable_graph_order(
        self,
    ) -> None:
        ledger = build_result_ledger(self.graph, self.run)

        expected_loop_ids = [
            node["id"]
            for node in self.graph["nodes"]
            if node["kind"].endswith("_LOOP")
        ]
        self.assertEqual(
            [entry["nodeId"] for entry in ledger["entries"]],
            expected_loop_ids,
        )
        self.assertTrue(ledger["complete"])
        self.assertEqual(ledger["issues"], [])
        self.assertEqual(
            ledger["summary"],
            {
                "expectedLoops": len(expected_loop_ids),
                "recordedResults": len(expected_loop_ids),
                "successfulLoops": len(expected_loop_ids),
                "incompleteLoops": 0,
            },
        )
        for entry in ledger["entries"]:
            self.assertIsInstance(entry["result"], dict)
            self.assertTrue(entry["summary"])

    def test_ledger_reports_missing_or_non_terminal_results_without_guessing(
        self,
    ) -> None:
        broken = deepcopy(self.run)
        broken_state = next(
            state
            for state in broken["nodes"]
            if state["nodeId"].startswith("loop:")
        )
        broken_state["status"] = "CLAIMED"
        broken_state["outcome"] = None

        ledger = build_result_ledger(self.graph, broken)

        self.assertFalse(ledger["complete"])
        self.assertEqual(ledger["summary"]["incompleteLoops"], 1)
        self.assertEqual(
            [issue["code"] for issue in ledger["issues"]],
            ["LOOP_NOT_TERMINAL", "LOOP_OUTCOME_MISSING"],
        )
        self.assertEqual(
            {issue["nodeId"] for issue in ledger["issues"]},
            {broken_state["nodeId"]},
        )

        with self.assertRaises(GatedLoopError) as caught:
            assert_result_ledger_complete(self.graph, broken)
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_RESULT_LEDGER_INCOMPLETE",
        )
        self.assertEqual(
            caught.exception.details["incompleteNodeIds"],
            [broken_state["nodeId"]],
        )

    def test_ledger_rejects_successful_task_without_scope_and_evidence(
        self,
    ) -> None:
        broken = deepcopy(self.run)
        task_state = next(
            state
            for state in broken["nodes"]
            if state["nodeId"].startswith("loop:")
        )
        task_state["outcome"]["result"] = {
            "implementationNotes": ["Changed the implementation."],
        }

        ledger = build_result_ledger(self.graph, broken)

        self.assertFalse(ledger["complete"])
        self.assertEqual(
            [issue["code"] for issue in ledger["issues"]],
            [
                "TASK_AFFECTED_SCOPES_MISSING",
                "TASK_VERIFICATION_EVIDENCE_MISSING",
            ],
        )
        self.assertEqual(
            {issue["nodeId"] for issue in ledger["issues"]},
            {task_state["nodeId"]},
        )

    def test_ledger_rejects_unpassed_or_unscoped_task_evidence(self) -> None:
        broken = deepcopy(self.run)
        task_state = next(
            state
            for state in broken["nodes"]
            if state["nodeId"].startswith("loop:")
        )
        task_state["outcome"]["result"] = {
            "affectedScopes": [
                {
                    "scopeId": "task-change",
                    "projectId": "primary",
                    "paths": [],
                    "modules": ["service"],
                    "contracts": [],
                    "dependencyBasis": "The changed service module.",
                    "exclusions": [],
                }
            ],
            "verificationEvidence": [
                {
                    "evidenceId": "skipped-check",
                    "kind": "TEST",
                    "check": "Focused service tests",
                    "command": "test service",
                    "scope": "Service module",
                    "scopeRefs": [],
                    "status": "SKIPPED",
                    "completedAt": "2030-01-01T00:00:00Z",
                }
            ],
        }

        ledger = build_result_ledger(self.graph, broken)

        self.assertFalse(ledger["complete"])
        self.assertEqual(
            [issue["code"] for issue in ledger["issues"]],
            [
                "TASK_EVIDENCE_NOT_PASSED",
                "TASK_SCOPE_NOT_VERIFIED",
            ],
        )

    def test_assembler_preserves_all_results_evidence_and_findings(
        self,
    ) -> None:
        task_state = next(
            state
            for state in self.run["nodes"]
            if state["nodeId"].startswith("loop:")
        )
        task_state["outcome"]["result"] = {
            "affectedScopes": [
                {
                    "scopeId": "task-change",
                    "projectId": "primary",
                    "paths": [],
                    "modules": ["service"],
                    "contracts": [],
                    "dependencyBasis": "The service module changed.",
                    "exclusions": [],
                }
            ],
            "verificationEvidence": [
                {
                    "evidenceId": "targeted-test",
                    "kind": "TEST",
                    "check": "Targeted service test",
                    "command": "test service",
                    "scope": "Service module",
                    "scopeRefs": ["task-change"],
                    "status": "PASSED",
                    "completedAt": "2030-01-01T00:00:00Z",
                }
            ],
            "implementationNotes": ["first", "second"],
        }
        review_state = next(
            state
            for state in self.run["nodes"]
            if state["nodeId"].startswith("review:task:")
        )
        review_state["outcome"]["result"]["reviewFindings"] = [
            {
                "severity": "P2",
                "summary": "Document the fallback.",
                "status": "ACCEPTED",
                "resolution": "Tracked for a later revision.",
                "evidence": "The fallback is bounded.",
            }
        ]

        assembled = assemble_delivery_result(
            self.hierarchy,
            self.graph,
            self.run,
        )

        self.assertTrue(assembled["completeness"]["complete"])
        self.assertEqual(
            assembled["loopResults"][0]["result"]["implementationNotes"],
            ["first", "second"],
        )
        self.assertEqual(
            assembled["verificationEvidence"][0]["nodeId"],
            task_state["nodeId"],
        )
        self.assertEqual(
            assembled["reviewFindings"][0]["nodeId"],
            review_state["nodeId"],
        )
        self.assertEqual(
            assembled["reviewFindings"][0]["finding"]["summary"],
            "Document the fallback.",
        )

    def test_review_evidence_decision_failures_are_closed(self) -> None:
        base = review_success("TASK_REVIEW_LOOP")["result"]

        invalid_evidence_array = deepcopy(base)
        invalid_evidence_array["verificationEvidence"] = {}

        duplicate_evidence = deepcopy(base)
        duplicate_evidence["verificationEvidence"].append(
            deepcopy(duplicate_evidence["verificationEvidence"][0])
        )

        failed_evidence = deepcopy(base)
        failed_evidence["verificationEvidence"][0]["status"] = "FAILED"

        missing_executed_ref = deepcopy(base)
        missing_executed_ref["validationDecision"][
            "executedEvidenceRefs"
        ] = []

        reused_without_source = deepcopy(base)
        reused_without_source["validationDecision"].update(
            {
                "decision": "REUSED",
                "reusedEvidenceRefs": [],
                "executedEvidenceRefs": [],
            }
        )

        dangling_acceptance = deepcopy(base)
        dangling_acceptance["taskAcceptance"]["acceptanceChecks"][0][
            "evidenceRefs"
        ] = ["missing-evidence"]

        cases = (
            invalid_evidence_array,
            duplicate_evidence,
            failed_evidence,
            missing_executed_ref,
            reused_without_source,
            dangling_acceptance,
        )
        for result in cases:
            with self.subTest(result=result):
                with self.assertRaises(GatedLoopError) as caught:
                    validate_review_result_contract(
                        "TASK_REVIEW_LOOP",
                        result,
                    )
                self.assertEqual(
                    caught.exception.code,
                    "LOOP_REVIEW_RESULT_INVALID",
                )

    def test_delivery_result_is_a_read_only_controller_operation(self) -> None:
        definition = next(
            tool
            for tool in tool_definitions()
            if tool["name"] == "delivery_result"
        )

        self.assertIn("delivery_result", CONTROLLER_OPERATIONS)
        self.assertTrue(definition["annotations"]["readOnlyHint"])
        self.assertTrue(definition["annotations"]["idempotentHint"])
        self.assertFalse(definition["annotations"]["destructiveHint"])

    def test_execution_metrics_expose_critical_path_and_slowest_loops(
        self,
    ) -> None:
        timed_run = deepcopy(self.run)
        timed_run.update(
            {
                "startedAt": "2030-01-01T00:00:00Z",
                "updatedAt": "2030-01-01T00:00:30Z",
                "completedAt": "2030-01-01T00:00:30Z",
            }
        )
        loop_index = 0
        for state in timed_run["nodes"]:
            definition = next(
                item
                for item in self.graph["nodes"]
                if item["id"] == state["nodeId"]
            )
            if not definition["kind"].endswith("_LOOP"):
                continue
            state["claimedAt"] = (
                f"2030-01-01T00:00:{loop_index * 10:02d}Z"
            )
            state["finishedAt"] = (
                f"2030-01-01T00:00:{(loop_index + 1) * 10:02d}Z"
            )
            loop_index += 1

        metrics = build_execution_metrics(self.graph, timed_run)
        assembled = assemble_delivery_result(
            self.hierarchy,
            self.graph,
            timed_run,
        )

        self.assertEqual(metrics["runElapsedSeconds"], 30.0)
        self.assertEqual(metrics["recordedLoopSeconds"], 30.0)
        self.assertEqual(metrics["criticalPathSeconds"], 30.0)
        self.assertEqual(len(metrics["criticalPathLoopIds"]), 3)
        self.assertEqual(len(metrics["slowestLoops"]), 3)
        self.assertEqual(assembled["executionMetrics"], metrics)

    def test_execution_metrics_include_retry_attempt_time(self) -> None:
        timed_run = deepcopy(self.run)
        timed_run.update(
            {
                "startedAt": "2030-01-01T00:00:00Z",
                "updatedAt": "2030-01-01T00:00:35Z",
                "completedAt": "2030-01-01T00:00:35Z",
            }
        )
        attempts = []
        loop_states = []
        for state in timed_run["nodes"]:
            definition = next(
                item
                for item in self.graph["nodes"]
                if item["id"] == state["nodeId"]
            )
            if definition["kind"].endswith("_LOOP"):
                loop_states.append(state)
        for index, state in enumerate(loop_states):
            start = 5 + index * 10
            state["claimedAt"] = f"2030-01-01T00:00:{start:02d}Z"
            state["finishedAt"] = f"2030-01-01T00:00:{start + 10:02d}Z"
            attempts.append(
                {
                    "nodeId": state["nodeId"],
                    "attempt": state["attempt"],
                    "status": state["status"],
                    "claimedAt": state["claimedAt"],
                    "finishedAt": state["finishedAt"],
                }
            )
        first = loop_states[0]
        first["attempt"] = 2
        attempts[0]["attempt"] = 2
        attempts.append(
            {
                "nodeId": first["nodeId"],
                "attempt": 1,
                "status": "BLOCKED",
                "claimedAt": "2030-01-01T00:00:00Z",
                "finishedAt": "2030-01-01T00:00:05Z",
            }
        )
        timed_run["attempts"] = attempts

        metrics = build_execution_metrics(self.graph, timed_run)

        self.assertEqual(metrics["recordedLoopSeconds"], 35.0)
        self.assertEqual(metrics["criticalPathSeconds"], 35.0)
        self.assertEqual(metrics["measuredAttempts"], 4)
        self.assertEqual(metrics["retriedLoops"], 1)
        slowest = next(
            item
            for item in metrics["slowestLoops"]
            if item["nodeId"] == first["nodeId"]
        )
        self.assertEqual(slowest["attemptCount"], 2)
        self.assertEqual(slowest["durationSeconds"], 15.0)


if __name__ == "__main__":
    unittest.main()
