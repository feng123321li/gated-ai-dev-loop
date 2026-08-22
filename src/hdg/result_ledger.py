from __future__ import annotations

from copy import deepcopy
from typing import Any

from .errors import fail
from .execution_metrics import build_execution_metrics
from .outcome_compaction import compact_run_for_transport
from .repository import SchedulerRepository


RESULT_LEDGER_VERSION = 1
_LOOP_TERMINAL_STATES = frozenset(
    {"SUCCEEDED", "BLOCKED", "CANCELLED"}
)


def _issue(
    code: str,
    definition: dict[str, Any],
    message: str,
) -> dict[str, str]:
    return {
        "code": code,
        "nodeId": str(definition["id"]),
        "kind": str(definition["kind"]),
        "workItemId": str(definition["workItemId"]),
        "message": message,
    }


def build_result_ledger(
    graph: dict[str, Any],
    run: dict[str, Any],
    *,
    compacted: bool = False,
) -> dict[str, Any]:
    """Build a deterministic, complete ledger for every Graph Loop.

    The ledger never infers a missing result. It keeps the compacted result
    payload with stable Graph identity so a later assembler cannot silently
    drop one Loop while summarizing the Delivery.
    """

    compact_run = run if compacted else compact_run_for_transport(run)
    states = {
        state["nodeId"]: state
        for state in compact_run.get("nodes", [])
        if isinstance(state, dict) and isinstance(state.get("nodeId"), str)
    }
    definitions = [
        definition
        for definition in graph.get("nodes", [])
        if isinstance(definition, dict)
        and str(definition.get("kind", "")).endswith("_LOOP")
    ]
    entries: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    recorded_results = 0
    successful_loops = 0

    for definition in definitions:
        state = states.get(definition["id"])
        if state is None:
            issues.append(
                _issue(
                    "LOOP_STATE_MISSING",
                    definition,
                    "The materialized run has no state for this Graph Loop.",
                )
            )
            entries.append(
                {
                    "nodeId": definition["id"],
                    "kind": definition["kind"],
                    "workItemId": definition["workItemId"],
                    "attempt": None,
                    "status": "MISSING",
                    "outcomeStatus": None,
                    "summary": None,
                    "result": None,
                }
            )
            continue

        status = state.get("status")
        if status not in _LOOP_TERMINAL_STATES:
            issues.append(
                _issue(
                    "LOOP_NOT_TERMINAL",
                    definition,
                    "The Loop has not reached a terminal result state.",
                )
            )
        outcome = state.get("outcome")
        outcome_status = None
        summary = None
        result = None
        if not isinstance(outcome, dict):
            issues.append(
                _issue(
                    "LOOP_OUTCOME_MISSING",
                    definition,
                    "The terminal Loop result payload is missing.",
                )
            )
        else:
            recorded_results += 1
            outcome_status = outcome.get("status")
            summary = outcome.get("summary")
            result = outcome.get("result")
            if not isinstance(summary, str) or not summary.strip():
                issues.append(
                    _issue(
                        "LOOP_SUMMARY_MISSING",
                        definition,
                        "The Loop result summary is missing.",
                    )
                )
            if not isinstance(result, dict):
                issues.append(
                    _issue(
                        "LOOP_RESULT_MISSING",
                        definition,
                        "The Loop result object is missing.",
                    )
                )
            expected_outcome_statuses = {
                "SUCCEEDED": {"SUCCEEDED"},
                "BLOCKED": {"BLOCKED", "REPLAN_REQUIRED"},
                "CANCELLED": {"CANCELLED"},
            }.get(str(status), set())
            if (
                expected_outcome_statuses
                and outcome_status not in expected_outcome_statuses
            ):
                issues.append(
                    _issue(
                        "LOOP_OUTCOME_STATUS_MISMATCH",
                        definition,
                        "The Loop outcome status does not match its materialized state.",
                    )
                )
        if status == "SUCCEEDED" and isinstance(outcome, dict):
            successful_loops += 1
        entries.append(
            {
                "nodeId": definition["id"],
                "kind": definition["kind"],
                "workItemId": definition["workItemId"],
                "attempt": state.get("attempt"),
                "status": status,
                "outcomeStatus": outcome_status,
                "summary": summary.strip()
                if isinstance(summary, str) and summary.strip()
                else None,
                "result": deepcopy(result) if isinstance(result, dict) else None,
            }
        )

    incomplete_nodes = {issue["nodeId"] for issue in issues}
    complete = (
        not issues
        and len(definitions) == successful_loops
        and all(entry["status"] == "SUCCEEDED" for entry in entries)
    )
    return {
        "ledgerVersion": RESULT_LEDGER_VERSION,
        "rootId": graph.get("rootId"),
        "runId": compact_run.get("runId"),
        "deliveryRevision": compact_run.get("deliveryRevision"),
        "complete": complete,
        "summary": {
            "expectedLoops": len(definitions),
            "recordedResults": recorded_results,
            "successfulLoops": successful_loops,
            "incompleteLoops": len(incomplete_nodes),
        },
        "issues": issues,
        "entries": entries,
    }


def assemble_delivery_result(
    hierarchy: dict[str, Any],
    graph: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    """Assemble every persisted Loop result into one deterministic report."""

    ledger = build_result_ledger(graph, run)
    evidence: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    acceptance: list[dict[str, Any]] = []
    for entry in ledger["entries"]:
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        provenance = {
            "nodeId": entry["nodeId"],
            "kind": entry["kind"],
            "workItemId": entry["workItemId"],
        }
        for item in result.get("verificationEvidence", []):
            if isinstance(item, dict):
                evidence.append({**provenance, "evidence": deepcopy(item)})
        for item in result.get("reviewFindings", []):
            if isinstance(item, dict):
                findings.append({**provenance, "finding": deepcopy(item)})
        task_acceptance = result.get("taskAcceptance")
        if isinstance(task_acceptance, dict):
            for item in task_acceptance.get("acceptanceChecks", []):
                if isinstance(item, dict):
                    acceptance.append(
                        {
                            **provenance,
                            "layer": "TASK",
                            "acceptance": deepcopy(item),
                        }
                    )
        group_integration = result.get("groupIntegration")
        if isinstance(group_integration, dict):
            for item in group_integration.get("seams", []):
                if isinstance(item, dict):
                    acceptance.append(
                        {
                            **provenance,
                            "layer": "GROUP",
                            "acceptance": deepcopy(item),
                        }
                    )
        delivery_readiness = result.get("deliveryReadiness")
        if isinstance(delivery_readiness, dict):
            for item in delivery_readiness.get("requirementCoverage", []):
                if isinstance(item, dict):
                    acceptance.append(
                        {
                            **provenance,
                            "layer": "DELIVERY",
                            "acceptance": deepcopy(item),
                        }
                    )

    delivery = hierarchy["delivery"]
    return {
        "resultVersion": 1,
        "rootId": graph.get("rootId"),
        "deliveryRevision": run.get("deliveryRevision"),
        "delivery": {
            "id": delivery["id"],
            "title": delivery["title"],
            "summary": delivery["summary"],
        },
        "completeness": {
            "complete": ledger["complete"],
            "summary": deepcopy(ledger["summary"]),
            "issues": deepcopy(ledger["issues"]),
        },
        "loopResults": deepcopy(ledger["entries"]),
        "acceptanceCoverage": acceptance,
        "verificationEvidence": evidence,
        "reviewFindings": findings,
        "executionMetrics": build_execution_metrics(graph, run),
    }


def assert_result_ledger_complete(
    graph: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed when a Delivery result would omit a Graph Loop."""

    ledger = build_result_ledger(graph, run)
    if ledger["complete"]:
        return ledger
    fail(
        "SCHEDULER_RESULT_LEDGER_INCOMPLETE",
        "Every Graph Loop must have one successful persisted result before "
        "Revision completion can be confirmed",
        incompleteNodeIds=sorted(
            {issue["nodeId"] for issue in ledger["issues"]}
        ),
        issues=deepcopy(ledger["issues"]),
        ledgerSummary=deepcopy(ledger["summary"]),
    )


def delivery_result(
    *,
    root: str,
    root_id: str,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    """Read the authoritative run and return its deterministic result report."""

    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    run = repository.run(root_id)
    return assemble_delivery_result(
        stored["hierarchy"],
        stored["graph"],
        run,
    )


__all__ = (
    "RESULT_LEDGER_VERSION",
    "assemble_delivery_result",
    "assert_result_ledger_complete",
    "build_result_ledger",
    "delivery_result",
)
