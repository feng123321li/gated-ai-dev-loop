from __future__ import annotations

import json

import re

from copy import deepcopy

from datetime import datetime, timedelta, timezone

from pathlib import Path

from typing import Any

from .dispatch_contracts import (
    HOST_ADAPTER_RECEIVER_AGENTS,
    HOST_NATIVE_DISPATCH_TRANSPORT,
    advisory_skill_hint_prompt,
    automatic_dispatch_decision_fingerprint,
    receiver_skill_prompt,
)

from .errors import fail

from .git_binding import (
    capture_verified_evidence_scope_state,
    capture_verified_workspace_changes,
    capture_verified_workspace_state,
    inspect_frozen_git_workspace_provenance,
    verify_runtime_delivery_project_scopes,
)

from .graph_model import (
    FAILURE_CLASSES,
    LOOP_NODE_KINDS,
    compile_delivery_graph,
    graph_assurance_profile,
    graph_fingerprint,
)

from .jsonio import fingerprint

from .loop_contracts import (
    loop_completion_policy,
    loop_execution_policy,
    resource_claims_overlap,
    validate_loop_descriptor,
    validate_loop_outcome,
)

from .model_rendering import (
    task_baseline_relative_path,
    task_has_database_projection,
    work_item_projection_relative_path,
)

from .model_core import (
    hierarchy_fingerprint,
    iter_hierarchy_nodes,
    validate_hierarchy_definition,
)

from .repository import (
    SchedulerRepository,
    _validated_stored_definition,
    timestamp,
)

from .review_contracts import validate_review_result_contract

from .progress_reporting import (
    PROGRESS_PHASE_TEXT,
    attach_progress_monitor,
    normalize_progress_payload,
    validate_progress_event_payload,
)

IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,191}$")

CAPACITY_SCOPES = frozenset({"EXECUTOR", "HOST"})

DISPATCH_MODES = frozenset({"AUTO", "MANUAL"})

GRAPH_EXECUTION_MODES = frozenset({"active", "manual"})

SHA256_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")

HOST_ADAPTER_AGENTS = dict(HOST_ADAPTER_RECEIVER_AGENTS)

HOST_CAPACITY_KEYS = {
    "claude-code": "claude-code:default",
    "codex": "codex:default",
    "zcode": "zcode:default",
}

MAX_HOST_CAPACITY_RESET = timedelta(hours=24)

def _dispatch_mode_allowed(
    execution_mode: str,
    node_kind: str,
    dispatch_mode: str,
    *,
    manual_handoff_enabled: bool = False,
) -> bool:
    if execution_mode == "active":
        if manual_handoff_enabled and node_kind == "TASK_LOOP":
            return dispatch_mode == "MANUAL"
        if node_kind == "TASK_LOOP":
            return dispatch_mode == "AUTO"
        return dispatch_mode == "AUTO"
    if execution_mode == "manual":
        if node_kind == "TASK_LOOP":
            return dispatch_mode == "MANUAL"
        if node_kind.endswith("_REVIEW_LOOP"):
            return dispatch_mode == "AUTO"
    return False

def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        fail(
            "SCHEDULER_IDENTITY_INVALID",
            f"{field} is not a portable scheduler identity",
            field=field,
        )
    return value

def _executor_descriptor(value: object, field: str) -> str:
    if not isinstance(value, str):
        fail(
            "SCHEDULER_EXECUTOR_METADATA_INVALID",
            f"{field} must identify the actual Loop executor",
            field=field,
        )
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 256
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in normalized
        )
    ):
        fail(
            "SCHEDULER_EXECUTOR_METADATA_INVALID",
            f"{field} must identify the actual Loop executor",
            field=field,
        )
    return normalized

def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def _future_timestamp(value: object, *, at: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(
            "SCHEDULER_RESUME_TIME_INVALID",
            "resume_at must be a future ISO 8601 timestamp",
        )
    try:
        parsed = _parse_timestamp(value.strip())
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        normalized = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        fail(
            "SCHEDULER_RESUME_TIME_INVALID",
            "resume_at must be a future ISO 8601 timestamp",
        )
    if normalized <= _parse_timestamp(at):
        fail(
            "SCHEDULER_RESUME_TIME_INVALID",
            "resume_at must be later than the current scheduler time",
        )
    return normalized.isoformat().replace("+00:00", "Z")

def _capacity_scope(
    value: object,
    *,
    has_resume_at: bool,
) -> str | None:
    if not has_resume_at and value is None:
        return None
    if (
        not has_resume_at
        or not isinstance(value, str)
        or value not in CAPACITY_SCOPES
    ):
        fail(
            "SCHEDULER_CAPACITY_SCOPE_INVALID",
            "capacity_scope must be EXECUTOR or HOST when resume_at is set",
        )
    return str(value)

def _after(value: str, seconds: int) -> str:
    return (
        _parse_timestamp(value) + timedelta(seconds=seconds)
    ).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _locked_timestamp(now: object, current: str) -> str:
    """Resolve commit time under the scheduler lock without regression."""

    candidate = timestamp(now)
    if _parse_timestamp(candidate) < _parse_timestamp(current):
        return current
    return candidate

def _loaded(
    connection: Any,
    root_id: str,
) -> tuple[dict[str, Any], Any, list[dict[str, Any]]]:
    hierarchy = connection.execute(
        "SELECT * FROM hierarchies "
        "WHERE root_id = ? "
        "AND status = 'FROZEN'",
        (root_id,),
    ).fetchone()
    if hierarchy is None:
        fail(
            "SCHEDULER_RUN_MISSING",
            f"Frozen scheduler run is missing: {root_id}",
        )
    run = connection.execute(
        "SELECT * FROM runs WHERE root_id = ? "
        "AND revision = ?",
        (root_id, hierarchy["revision"]),
    ).fetchone()
    if run is None:
        fail(
            "SCHEDULER_RUN_MISSING",
            f"Frozen scheduler run is missing: {root_id}",
        )
    _, graph = _validated_stored_definition(hierarchy)
    return (
        graph,
        run,
        SchedulerRepository.latest_nodes(connection, run["run_id"]),
    )

def _node(
    graph: dict[str, Any],
    nodes: list[dict[str, Any]],
    node_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    definition = next(
        (item for item in graph["nodes"] if item["id"] == node_id),
        None,
    )
    state = next(
        (item for item in nodes if item["nodeId"] == node_id),
        None,
    )
    if definition is None or state is None:
        fail(
            "SCHEDULER_NODE_MISSING",
            f"Scheduler node is missing: {node_id}",
        )
    return definition, state

def _assert_graph_not_replanning(
    nodes: list[dict[str, Any]],
) -> None:
    replan_nodes = sorted(
        item["nodeId"]
        for item in nodes
        if (
            item["status"] == "BLOCKED"
            and item["failureClass"] == "REPLAN_REQUIRED"
        )
    )
    if replan_nodes:
        fail(
            "SCHEDULER_REPLAN_REQUIRED",
            "The frozen Graph requires replanning and cannot start "
            "additional Loop work",
            nodeIds=replan_nodes,
        )

def _upstream_receiver_context_ids(
    graph: dict[str, Any],
    nodes: list[dict[str, Any]],
    node_id: str,
) -> set[str]:
    incoming: dict[str, list[str]] = {
        node["id"]: [] for node in graph["nodes"]
    }
    for edge in graph["edges"]:
        incoming[edge["target"]].append(edge["source"])
    states = {node["nodeId"]: node for node in nodes}
    contexts: set[str] = set()
    pending = list(incoming.get(node_id, []))
    visited: set[str] = set()
    while pending:
        upstream_id = pending.pop()
        if upstream_id in visited:
            continue
        visited.add(upstream_id)
        state = states.get(upstream_id)
        if state is not None:
            context_id = state.get("receiverContextId")
            if isinstance(context_id, str):
                contexts.add(context_id)
        pending.extend(incoming.get(upstream_id, []))
    return contexts

def _active_claim(
    state: dict[str, Any],
    *,
    operation_id: str,
    at: str,
) -> bool:
    lease_expires_at = state["leaseExpiresAt"]
    return (
        state["status"] == "CLAIMED"
        and state["operationId"] == operation_id
        and isinstance(lease_expires_at, str)
        and _parse_timestamp(lease_expires_at) > _parse_timestamp(at)
    )

def _upstream_loop_results(
    graph: dict[str, Any],
    states: dict[str, dict[str, Any]],
    node_id: str,
) -> list[dict[str, Any]]:
    definitions = {
        node["id"]: node
        for node in graph["nodes"]
    }
    incoming: dict[str, list[str]] = {
        node["id"]: []
        for node in graph["nodes"]
    }
    for edge in graph["edges"]:
        incoming[edge["target"]].append(edge["source"])
    pending = list(incoming[node_id])
    visited: set[str] = set()
    results: list[dict[str, Any]] = []
    while pending:
        predecessor = pending.pop()
        if predecessor in visited:
            continue
        visited.add(predecessor)
        pending.extend(incoming[predecessor])
        definition = definitions[predecessor]
        if definition["kind"] not in LOOP_NODE_KINDS:
            continue
        state = states[predecessor]
        outcome = deepcopy(state["outcome"])
        outcome_result = (
            outcome.get("result") if isinstance(outcome, dict) else None
        )
        workspace_changes = (
            outcome_result.get("workspaceChanges")
            if isinstance(outcome_result, dict)
            else None
        )
        if isinstance(workspace_changes, list):
            compact_snapshots: list[dict[str, Any]] = []
            for snapshot in workspace_changes:
                if not isinstance(snapshot, dict):
                    continue
                compact = deepcopy(snapshot)
                if "diff" in compact:
                    compact.pop("diff")
                    compact["diffOmittedFromLoopContext"] = True
                compact_snapshots.append(compact)
            outcome_result["workspaceChanges"] = compact_snapshots
        results.append(
            {
                "nodeId": predecessor,
                "kind": definition["kind"],
                "workItemId": definition["workItemId"],
                "attempt": state["attempt"],
                "status": state["status"],
                "outcome": outcome,
            }
        )
    return sorted(results, key=lambda item: item["nodeId"])

def _validation_evidence_index(
    upstream_results: list[dict[str, Any]],
    current_workspace_snapshots: list[dict[str, Any]],
    current_scope_snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    def binding_key(items: object) -> tuple[tuple[str, str, str], ...] | None:
        if not isinstance(items, list) or not items:
            return None
        normalized: list[tuple[str, str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                return None
            if item.get("bindingState", "BOUND") != "BOUND":
                return None
            project_id = item.get("projectId")
            head_commit = item.get("headCommit")
            tree_fingerprint = item.get("workingTreeStateFingerprint")
            if not all(
                isinstance(value, str) and value
                for value in (
                    project_id,
                    head_commit,
                    tree_fingerprint,
                )
            ):
                return None
            normalized.append(
                (project_id, head_commit, tree_fingerprint)
            )
        return tuple(sorted(normalized))

    current_key = binding_key(current_workspace_snapshots)
    current_scope_by_ref = {
        (
            item.get("nodeId"),
            item.get("attempt"),
            item.get("scopeId"),
        ): item
        for item in current_scope_snapshots
        if isinstance(item, dict)
    }
    evidence_items: list[dict[str, Any]] = []
    for source in upstream_results:
        outcome = source.get("outcome")
        result = outcome.get("result") if isinstance(outcome, dict) else None
        if not isinstance(result, dict):
            continue
        evidence = result.get("verificationEvidence")
        if not isinstance(evidence, list):
            continue
        result_binding = result.get("evidenceWorkspaceSnapshots")
        result_key = binding_key(result_binding)
        result_scope_by_id = {
            item.get("scopeId"): item
            for item in result.get("evidenceScopeSnapshots", [])
            if isinstance(item, dict)
            and isinstance(item.get("scopeId"), str)
        }
        for item in evidence:
            if not isinstance(item, dict):
                continue
            tested_key = binding_key(item.get("testedWorkspaceSnapshots"))
            item_freshness = "UNBOUND"
            reason = None
            if tested_key is None:
                item_freshness = "UNBOUND"
                reason = "TESTED_STATE_NOT_BOUND"
            elif result_key is None:
                item_freshness = "UNBOUND"
                reason = "RESULT_STATE_NOT_BOUND"
            elif tested_key != result_key:
                item_freshness = "UNBOUND"
                reason = "TESTED_STATE_DIFFERS_FROM_RECORDED_RESULT"
            else:
                scope_refs = item.get("scopeRefs")
                if isinstance(scope_refs, list) and scope_refs:
                    result_scopes = [
                        result_scope_by_id.get(scope_id)
                        for scope_id in scope_refs
                    ]
                    current_scopes = [
                        current_scope_by_ref.get(
                            (
                                source["nodeId"],
                                source["attempt"],
                                scope_id,
                            )
                        )
                        for scope_id in scope_refs
                    ]
                    if any(
                        not isinstance(scope, dict)
                        or scope.get("bindingState") != "BOUND"
                        or not isinstance(scope.get("stateFingerprint"), str)
                        for scope in [*result_scopes, *current_scopes]
                    ):
                        item_freshness = "UNBOUND"
                        reason = "RELEVANT_SCOPE_STATE_NOT_BOUND"
                    elif any(
                        recorded["stateFingerprint"]
                        != current["stateFingerprint"]
                        for recorded, current in zip(
                            result_scopes,
                            current_scopes,
                        )
                    ):
                        item_freshness = "CHANGED"
                        reason = "RELEVANT_SCOPE_CHANGED"
                    else:
                        item_freshness = "EXACT_MATCH"
                elif current_key is None:
                    item_freshness = "UNBOUND"
                    reason = "CURRENT_STATE_NOT_BOUND"
                elif result_key == current_key:
                    item_freshness = "EXACT_MATCH"
                else:
                    item_freshness = "CHANGED"
                    reason = "WORKSPACE_CHANGED_WITHOUT_RELEVANT_SCOPE_BINDING"
            record = {
                "evidenceRef": {
                    "nodeId": source["nodeId"],
                    "attempt": source["attempt"],
                    "evidenceId": item.get("evidenceId"),
                },
                "kind": item.get("kind"),
                "check": item.get("check"),
                "scope": item.get("scope"),
                "scopeRefs": item.get("scopeRefs", []),
                "status": item.get("status"),
                "freshness": item_freshness,
            }
            if reason is not None:
                record["freshnessReason"] = reason
            evidence_items.append(record)
    return {
        "automaticReuse": "PASSED_AND_EXACT_MATCH_ONLY",
        "currentWorkspaceSnapshots": current_workspace_snapshots,
        "currentScopeSnapshots": current_scope_snapshots,
        "evidence": evidence_items,
    }

def _current_upstream_scope_snapshots(
    upstream_results: list[dict[str, Any]],
    verified_project_scopes: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if verified_project_scopes is None:
        return []
    snapshots: list[dict[str, Any]] = []
    pending: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unique_scopes: dict[
        tuple[str, tuple[str, ...]],
        dict[str, Any],
    ] = {}
    for source in upstream_results:
        outcome = source.get("outcome")
        result = outcome.get("result") if isinstance(outcome, dict) else None
        if not isinstance(result, dict):
            continue
        affected_scopes = result.get("affectedScopes")
        if not isinstance(affected_scopes, list):
            continue
        for affected_scope in affected_scopes:
            if not isinstance(affected_scope, dict):
                continue
            project_id = affected_scope.get("projectId")
            paths = affected_scope.get("paths")
            cache_key = (
                str(project_id),
                tuple(sorted(paths))
                if isinstance(paths, list)
                and all(isinstance(path, str) for path in paths)
                else (),
            )
            unique_scopes.setdefault(
                cache_key,
                {
                    "scopeId": fingerprint(
                        {
                            "projectId": cache_key[0],
                            "paths": cache_key[1],
                        }
                    ),
                    "projectId": project_id,
                    "paths": paths if isinstance(paths, list) else [],
                },
            )
            pending.append((source, {**affected_scope, "cacheKey": cache_key}))
    captured = capture_verified_evidence_scope_state(
        verified_project_scopes,
        list(unique_scopes.values()),
    )
    captured_by_scope_id = {
        item.get("scopeId"): item
        for item in captured
        if isinstance(item, dict)
    }
    for source, affected_scope in pending:
        cache_key = affected_scope["cacheKey"]
        unique_scope = unique_scopes[cache_key]
        cached = captured_by_scope_id.get(unique_scope["scopeId"])
        if cached is None:
            cached = {
                "paths": unique_scope["paths"],
                "bindingState": "UNBOUND",
            }
        snapshot = {
            **cached,
            "scopeId": affected_scope.get("scopeId"),
            "projectId": affected_scope.get("projectId"),
        }
        snapshots.append(
            {
                "nodeId": source["nodeId"],
                "attempt": source["attempt"],
                **snapshot,
            }
        )
    return snapshots

def _validate_reused_evidence_refs(
    *,
    graph: dict[str, Any],
    nodes: list[dict[str, Any]],
    node_id: str,
    result_payload: dict[str, Any],
    verified_project_scopes: list[dict[str, Any]],
) -> None:
    decision = result_payload.get("validationDecision")
    if not isinstance(decision, dict):
        return
    reused_refs = decision.get("reusedEvidenceRefs")
    if not isinstance(reused_refs, list) or not reused_refs:
        return
    definitions = {item["id"]: item for item in graph["nodes"]}
    current_definition = definitions.get(node_id)
    if (
        not isinstance(current_definition, dict)
        or not str(current_definition.get("kind", "")).endswith("_REVIEW_LOOP")
    ):
        fail(
            "LOOP_OUTCOME_INVALID",
            "Only Review Loops may reuse upstream verification evidence",
        )
    states = {item["nodeId"]: item for item in nodes}
    upstream_results = _upstream_loop_results(graph, states, node_id)
    current_workspace_snapshots = capture_verified_workspace_state(
        verified_project_scopes
    )
    current_scope_snapshots = _current_upstream_scope_snapshots(
        upstream_results,
        verified_project_scopes,
    )
    evidence_index = _validation_evidence_index(
        upstream_results,
        current_workspace_snapshots,
        current_scope_snapshots,
    )
    reusable = {
        (
            item.get("evidenceRef", {}).get("nodeId"),
            item.get("evidenceRef", {}).get("attempt"),
            item.get("evidenceRef", {}).get("evidenceId"),
        )
        for item in evidence_index["evidence"]
        if item.get("status") == "PASSED"
        and item.get("freshness") == "EXACT_MATCH"
    }
    requested = {
        (
            item.get("nodeId"),
            item.get("attempt"),
            item.get("evidenceId"),
        )
        for item in reused_refs
        if isinstance(item, dict)
    }
    stale = sorted(requested - reusable, key=lambda item: tuple(map(str, item)))
    if stale:
        fail(
            "LOOP_EVIDENCE_STALE",
            "Reused verification evidence is no longer passing and "
            "EXACT_MATCH at Review completion",
            staleEvidenceRefs=[
                {
                    "nodeId": item[0],
                    "attempt": item[1],
                    "evidenceId": item[2],
                }
                for item in stale
            ],
        )

def _retry_if_allowed(
    repository: SchedulerRepository,
    connection: Any,
    *,
    graph: dict[str, Any],
    run_id: str,
    node: dict[str, Any],
    failure_class: str,
    at: str,
) -> bool:
    policy = graph["runtime"]["retryPolicy"]
    if (
        failure_class
        not in policy["automaticFailureClasses"]
        or node["attempt"] >= policy["maxAttempts"]
    ):
        if (
            failure_class in policy["automaticFailureClasses"]
            and node["attempt"] >= policy["maxAttempts"]
        ):
            repository.append_event(
                connection,
                run_id=run_id,
                node_id=node["nodeId"],
                attempt=node["attempt"],
                event_type="RETRY_EXHAUSTED",
                actor="CONTROLLER",
                operation_id=None,
                payload={
                    "failureClass": failure_class,
                    "maxAttempts": policy["maxAttempts"],
                },
                at=at,
            )
        return False
    next_attempt = node["attempt"] + 1
    connection.execute(
        "INSERT INTO node_runs(run_id, node_id, attempt, status) "
        "VALUES (?, ?, ?, 'PENDING')",
        (run_id, node["nodeId"], next_attempt),
    )
    repository.append_event(
        connection,
        run_id=run_id,
        node_id=node["nodeId"],
        attempt=next_attempt,
        event_type="LOOP_RETRY_SCHEDULED",
        actor="CONTROLLER",
        operation_id=None,
        payload={
            "failureClass": failure_class,
            "previousAttempt": node["attempt"],
        },
        at=at,
    )
    return True
