from __future__ import annotations

import json
import re
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .dispatch_contracts import (
    HOST_ADAPTER_RECEIVER_AGENTS,
    HOST_NATIVE_DISPATCH_TRANSPORT,
    automatic_dispatch_decision_fingerprint,
)
from .errors import fail
from .git_binding import inspect_frozen_git_workspace_provenance
from .graph_model import (
    FAILURE_CLASSES,
    LOOP_NODE_KINDS,
    compile_delivery_graph,
    graph_assurance_profile,
    graph_fingerprint,
)
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
        and _parse_timestamp(lease_expires_at) >= _parse_timestamp(at)
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
        results.append(
            {
                "nodeId": predecessor,
                "kind": definition["kind"],
                "workItemId": definition["workItemId"],
                "attempt": state["attempt"],
                "status": state["status"],
                "outcome": state["outcome"],
            }
        )
    return sorted(results, key=lambda item: item["nodeId"])


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


def advance_graph(
    *,
    root: str,
    root_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Advance leases, retries, joins, and dependency readiness."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        if (
            run["host_capacity_reset_at"] is not None
            and _parse_timestamp(run["host_capacity_reset_at"])
            <= _parse_timestamp(at)
        ):
            capacity_key = run["host_capacity_key"]
            reset_at = run["host_capacity_reset_at"]
            capacity_report = connection.execute(
                "SELECT report_id, reported_at FROM "
                "host_capacity_breakers WHERE capacity_key = ? "
                "AND reset_at = ?",
                (capacity_key, reset_at),
            ).fetchone()
            report_id = (
                capacity_report["report_id"]
                if capacity_report is not None
                else None
            )
            reported_at = (
                capacity_report["reported_at"]
                if capacity_report is not None
                else None
            )
            if report_id is None:
                exhausted_event = connection.execute(
                    "SELECT payload_json, recorded_at FROM graph_events "
                    "WHERE run_id = ? AND event_type = "
                    "'HOST_CAPACITY_EXHAUSTED' ORDER BY event_id DESC "
                    "LIMIT 1",
                    (run["run_id"],),
                ).fetchone()
                if exhausted_event is not None:
                    exhausted_payload = json.loads(
                        exhausted_event["payload_json"]
                    )
                    if (
                        exhausted_payload.get("capacityKey") == capacity_key
                        and exhausted_payload.get("resetAt") == reset_at
                    ):
                        event_report_id = exhausted_payload.get("reportId")
                        if isinstance(event_report_id, str):
                            report_id = event_report_id
                        event_reported_at = exhausted_payload.get(
                            "reportedAt"
                        )
                        reported_at = (
                            event_reported_at
                            if isinstance(event_reported_at, str)
                            else exhausted_event["recorded_at"]
                        )
            connection.execute(
                "UPDATE runs SET host_capacity_key = NULL, "
                "host_capacity_reset_at = NULL, "
                "host_capacity_reported_at = NULL, "
                "host_capacity_reason = NULL WHERE run_id = ?",
                (run["run_id"],),
            )
            connection.execute(
                "UPDATE host_capacity_breakers SET status = 'RESTORED', "
                "restored_at = ? WHERE capacity_key = ? "
                "AND status = 'OPEN' AND reset_at = ? "
                "AND (? IS NULL OR report_id = ?)",
                (at, capacity_key, reset_at, report_id, report_id),
            )
            repository.append_event(
                connection,
                run_id=run["run_id"],
                node_id=None,
                attempt=None,
                event_type="HOST_CAPACITY_RESTORED",
                actor="CONTROLLER",
                operation_id=None,
                payload={
                    "capacityKey": capacity_key,
                    "resetAt": reset_at,
                    **(
                        {"reportId": report_id}
                        if report_id is not None
                        else {}
                    ),
                    **(
                        {"reportedAt": reported_at}
                        if reported_at is not None
                        else {}
                    ),
                },
                at=at,
            )
        for node in nodes:
            resume_at = node.get("resumeAt")
            if (
                node["status"] != "PAUSED"
                or not isinstance(resume_at, str)
                or _parse_timestamp(resume_at) > _parse_timestamp(at)
            ):
                continue
            connection.execute(
                "UPDATE node_runs SET status = 'PENDING', owner = NULL, "
                "operation_id = NULL, claimed_at = NULL, "
                "last_heartbeat_at = NULL, lease_expires_at = NULL, "
                "finished_at = NULL, outcome_json = NULL "
                "WHERE run_id = ? AND node_id = ? "
                "AND attempt = ?",
                (
                    run["run_id"],
                    node["nodeId"],
                    node["attempt"],
                ),
            )
            repository.append_event(
                connection,
                run_id=run["run_id"],
                node_id=node["nodeId"],
                attempt=node["attempt"],
                event_type="NODE_AUTO_RESUMED",
                actor="CONTROLLER",
                operation_id=None,
                payload={"resumeAt": resume_at},
                at=at,
            )
        for node in nodes:
            if (
                node["status"] != "CLAIMED"
                or node["leaseExpiresAt"] is None
                or _parse_timestamp(node["leaseExpiresAt"])
                >= _parse_timestamp(at)
            ):
                continue
            connection.execute(
                "UPDATE node_runs SET status = 'BLOCKED', "
                "finished_at = ?, failure_class = 'WORKER_LOST' "
                "WHERE run_id = ? AND node_id = ? AND attempt = ?",
                (
                    at,
                    run["run_id"],
                    node["nodeId"],
                    node["attempt"],
                ),
            )
            repository.append_event(
                connection,
                run_id=run["run_id"],
                node_id=node["nodeId"],
                attempt=node["attempt"],
                event_type="CLAIM_LEASE_EXPIRED",
                actor="CONTROLLER",
                operation_id=node["operationId"],
                payload={"failureClass": "WORKER_LOST"},
                at=at,
            )
            _retry_if_allowed(
                repository,
                connection,
                graph=graph,
                run_id=run["run_id"],
                node=node,
                failure_class="WORKER_LOST",
                at=at,
            )
        repository.refresh_ready(
            connection,
            graph,
            run["run_id"],
            at=at,
        )
    repository.write_projections(root_id)
    return repository.run(root_id)


def graph_status(
    *,
    root: str,
    root_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    definition = repository.hierarchy(root_id)
    run = repository.run(root_id)
    node_by_id = {
        node["id"]: node
        for node in definition["graph"]["nodes"]
    }
    result = {
        **run,
        "nodes": [
            {
                **state,
                "kind": node_by_id[state["nodeId"]]["kind"],
                "workItemId": node_by_id[state["nodeId"]][
                    "workItemId"
                ],
            }
            for state in run["nodes"]
        ],
    }
    observation_at = max(timestamp(now), result["updatedAt"])
    return attach_progress_monitor(
        result,
        definition["graph"],
        observed_at=observation_at,
    )


def loop_context(
    *,
    root: str,
    root_id: str,
    node_id: str,
    workspace_root: str | None = None,
    verified_project_scopes: list[dict[str, Any]] | None = None,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    run = repository.run(root_id)
    definition, state = _node(
        stored["graph"],
        run["nodes"],
        node_id,
    )
    if definition["kind"] not in LOOP_NODE_KINDS:
        fail(
            "SCHEDULER_LOOP_REQUIRED",
            f"{node_id} is not a Loop node",
        )
    predecessors = sorted(
        edge["source"]
        for edge in stored["graph"]["edges"]
        if edge["target"] == node_id
    )
    states = {
        node["nodeId"]: node
        for node in run["nodes"]
    }
    human_artifacts: dict[str, Any] = {}
    work_item_kind = {
        "TASK_LOOP": "TASK",
        "TASK_REVIEW_LOOP": "TASK",
        "GROUP_REVIEW_LOOP": "GROUP",
    }.get(definition["kind"])
    if work_item_kind is not None:
        item_id = definition["workItemId"]
        projection_prefix = f".layered-delivery/{root_id}/"
        work_item_artifacts = {
            "kind": work_item_kind,
            "baseline": (
                projection_prefix
                + work_item_projection_relative_path(
                    stored["hierarchy"],
                    item_id,
                    "baseline.md",
                )
            ),
            "progress": (
                projection_prefix
                + work_item_projection_relative_path(
                    stored["hierarchy"],
                    item_id,
                    "progress.md",
                )
            ),
            "acceptance": (
                projection_prefix
                + work_item_projection_relative_path(
                    stored["hierarchy"],
                    item_id,
                    "acceptance.md",
                )
            ),
        }
        work_item_definition = next(
            node["definition"]
            for node in iter_hierarchy_nodes(stored["hierarchy"])
            if node["definition"]["id"] == item_id
        )
        interfaces = (
            work_item_definition["execution"]["loop"]["payload"].get(
                "interfaces"
            )
            if work_item_kind == "TASK"
            else None
        )
        if (
            work_item_kind == "TASK"
            and isinstance(interfaces, list)
            and any(isinstance(item, dict) for item in interfaces)
        ):
            work_item_artifacts["interfaces"] = (
                projection_prefix
                + work_item_projection_relative_path(
                    stored["hierarchy"],
                    item_id,
                    "interfaces.md",
                )
            )
        if task_has_database_projection(work_item_definition):
            work_item_artifacts["databaseChanges"] = (
                projection_prefix
                + work_item_projection_relative_path(
                    stored["hierarchy"],
                    item_id,
                    "database-changes.md",
                )
            )
        human_artifacts["workItem"] = work_item_artifacts
        if work_item_kind == "TASK":
            human_artifacts["taskBaseline"] = (
                projection_prefix
                + task_baseline_relative_path(
                    stored["hierarchy"],
                    item_id,
                )
            )
    assurance_profile = graph_assurance_profile(stored["graph"])
    project_scope_anchors = stored["hierarchy"]["delivery"].get(
        "projectScopes",
        [],
    )
    project_scopes = (
        project_scope_anchors
        if verified_project_scopes is None
        else deepcopy(verified_project_scopes)
    )
    workspace_isolation = deepcopy(run["workspaceIsolation"])
    if workspace_root is not None:
        workspace_isolation["workspaceRoot"] = str(
            Path(workspace_root).absolute().resolve(strict=True)
        )
    context = {
        "rootId": root_id,
        "deliveryRevision": run["deliveryRevision"],
        "runId": run["runId"],
        "nodeId": node_id,
        "kind": definition["kind"],
        "workItemId": definition["workItemId"],
        "loop": definition["loop"],
        "skillHints": stored["hierarchy"]["root"]["skillHints"],
        "attempt": state["attempt"],
        "status": state["status"],
        "predecessors": [
            {
                "nodeId": predecessor,
                "status": states[predecessor]["status"],
                "outcome": states[predecessor]["outcome"],
            }
            for predecessor in predecessors
        ],
        "upstreamLoopResults": _upstream_loop_results(
            stored["graph"],
            states,
            node_id,
        ),
        "humanArtifacts": human_artifacts,
        "workspaceIsolation": workspace_isolation,
        "projectScopes": project_scopes,
        "projectScopeAnchors": project_scope_anchors,
        "executionPolicy": loop_execution_policy(assurance_profile),
        "completionPolicy": loop_completion_policy(assurance_profile),
        "rules": {
            "payloadIsOpaqueToScheduler": True,
            "internalGateAndSkillPolicyOwnedByLoop": True,
            "implementationPlanMayAdaptWithinLoop": True,
            "actionableFindingsStayInsideLoop": True,
            "skillHintsAreAdvisory": True,
            "selectSkillsAtRuntime": True,
            "prioritizeApplicableSkillHints": True,
            "returnOnlyStandardLoopOutcome": True,
            "coordinatorMustNotExecuteLoopInline": True,
            "accessOnlyAuthorizedProjectScopes": True,
            "projectScopeWorkspaceRootsAreRuntimeVerified": (
                verified_project_scopes is not None
            ),
            "loopsMustNotCreateSwitchOrCheckoutGitBranches": True,
        },
    }
    git_binding = stored["hierarchy"]["delivery"].get("gitBinding")
    if git_binding is not None:
        context["gitBinding"] = git_binding
    if definition["kind"] == "TASK_LOOP":
        requirement_state = next(
            (
                item
                for item in run["taskRequirements"]
                if item["taskId"] == definition["workItemId"]
            ),
            None,
        )
        if requirement_state is None:
            fail(
                "SCHEDULER_STATE_INVALID",
                "TASK requirement state is missing",
            )
        context["taskRequirement"] = requirement_state
    return context


def dispatch_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    owner: str,
    operation_id: str,
    agent_id: str | None = None,
    actual_model_id: str | None = None,
    receiver_context_id: str | None = None,
    receiver_attestation_id: str | None = None,
    dispatch_mode: str,
    dispatch_transport: str | None = None,
    dispatch_reservation_id: str | None = None,
    dispatch_decision_fingerprint: str | None = None,
    host_native_agent_ids: tuple[str, ...] | None = None,
    host_adapter_id: str | None = None,
    require_receiver_attestation: bool = False,
    host_receiver_parent_context_id: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    owner = _identity(owner, "owner")
    operation_id = _identity(operation_id, "operation_id")
    supplied_agent_id = (
        _executor_descriptor(agent_id, "agent_id")
        if agent_id is not None
        else None
    )
    observed_actual_model_id = (
        _executor_descriptor(actual_model_id, "actual_model_id")
        if actual_model_id is not None
        else None
    )
    if dispatch_mode not in DISPATCH_MODES:
        fail(
            "SCHEDULER_DISPATCH_MODE_INVALID",
            "dispatch_mode must be AUTO or MANUAL",
        )
    if dispatch_mode == "AUTO":
        if host_adapter_id not in HOST_ADAPTER_AGENTS:
            fail(
                "SCHEDULER_HOST_ADAPTER_UNTRUSTED",
                "Automatic dispatch requires an exact trusted host adapter",
            )
        actual_agent_id = HOST_ADAPTER_AGENTS[host_adapter_id]
        if (
            supplied_agent_id is not None
            and supplied_agent_id != actual_agent_id
        ):
            fail(
                "SCHEDULER_HOST_NATIVE_EXECUTOR_MISMATCH",
                "The current host cannot create the supplied receiver Agent",
                hostAdapterId=host_adapter_id,
                suppliedAgentId=supplied_agent_id,
            )
    else:
        actual_agent_id = supplied_agent_id
    actual_receiver_context_id = (
        _identity(receiver_context_id, "receiver_context_id")
        if receiver_context_id is not None
        else owner
    )
    actual_receiver_attestation_id = (
        _identity(
            receiver_attestation_id,
            "receiver_attestation_id",
        )
        if receiver_attestation_id is not None
        else None
    )
    actual_host_receiver_parent_context_id = (
        _identity(
            host_receiver_parent_context_id,
            "host_receiver_parent_context_id",
        )
        if host_receiver_parent_context_id is not None
        else None
    )
    if (
        actual_host_receiver_parent_context_id is not None
        and not require_receiver_attestation
    ):
        fail(
            "SCHEDULER_RECEIVER_ATTESTATION_INVALID",
            "Host-side identity issuance requires attested dispatch",
        )
    if require_receiver_attestation:
        if dispatch_mode != "AUTO":
            fail(
                "SCHEDULER_RECEIVER_ATTESTATION_INVALID",
                "Only automatic host-native dispatch uses receiver attestation",
            )
        if (
            actual_receiver_attestation_id is None
            and actual_host_receiver_parent_context_id is None
        ):
            fail(
                "SCHEDULER_RECEIVER_ATTESTATION_REQUIRED",
                "MCP dispatch requires a host-issued receiver attestation",
            )
        if (
            actual_receiver_attestation_id is not None
            and actual_host_receiver_parent_context_id is not None
        ):
            fail(
                "SCHEDULER_RECEIVER_ATTESTATION_INVALID",
                "Receiver identity may be supplied or host-issued, not both",
            )
    if dispatch_mode == "AUTO" and (
        dispatch_transport != HOST_NATIVE_DISPATCH_TRANSPORT
    ):
        fail(
            "SCHEDULER_DISPATCH_TRANSPORT_REQUIRED",
            (
                "Automatic dispatch requires HOST_NATIVE transport; "
                "external processes, CLI commands, and companion scripts "
                "cannot claim an automatic assignment"
            ),
        )
    if dispatch_transport is not None and dispatch_mode != "AUTO":
        fail(
            "SCHEDULER_DISPATCH_TRANSPORT_INVALID",
            "dispatch_transport is only valid for automatic dispatch",
        )
    if dispatch_mode == "AUTO" and (
        not isinstance(dispatch_decision_fingerprint, str)
        or SHA256_FINGERPRINT.fullmatch(
            dispatch_decision_fingerprint
        )
        is None
    ):
        fail(
            "SCHEDULER_DISPATCH_DECISION_REQUIRED",
            (
                "Automatic dispatch requires the exact decision "
                "fingerprint returned by the host dispatch plan"
            ),
        )
    if (
        dispatch_decision_fingerprint is not None
        and dispatch_mode != "AUTO"
    ):
        fail(
            "SCHEDULER_DISPATCH_DECISION_INVALID",
            (
                "dispatch_decision_fingerprint is only valid for "
                "automatic dispatch"
            ),
        )
    if dispatch_mode == "AUTO" and dispatch_reservation_id is None:
        fail(
            "SCHEDULER_DISPATCH_RESERVATION_REQUIRED",
            (
                "Automatic dispatch requires the reservation issued before "
                "the host created the receiving Agent"
            ),
        )
    if dispatch_reservation_id is not None and dispatch_mode != "AUTO":
        fail(
            "SCHEDULER_DISPATCH_RESERVATION_INVALID",
            (
                "dispatch_reservation_id is only valid for automatic "
                "dispatch"
            ),
        )
    actual_reservation_id = (
        _identity(
            dispatch_reservation_id,
            "dispatch_reservation_id",
        )
        if dispatch_reservation_id is not None
        else None
    )
    if (
        dispatch_mode == "AUTO"
        and host_native_agent_ids is not None
        and host_native_agent_ids != (actual_agent_id,)
    ):
        fail(
            "SCHEDULER_HOST_NATIVE_EXECUTOR_MISMATCH",
            "The current MCP host cannot natively create the reported Agent",
            supportedAgentIds=sorted(host_native_agent_ids),
            suppliedAgentId=actual_agent_id,
        )
    if dispatch_mode == "MANUAL" and (
        actual_agent_id is None
        or receiver_context_id is None
    ):
        fail(
            "SCHEDULER_EXECUTOR_METADATA_INVALID",
            "Manual TASK dispatch requires explicit Agent and receiving context IDs",
        )
    if (
        dispatch_mode == "MANUAL"
        and host_adapter_id in HOST_ADAPTER_AGENTS
        and actual_agent_id != HOST_ADAPTER_AGENTS[host_adapter_id]
    ):
        fail(
            "SCHEDULER_HOST_NATIVE_EXECUTOR_MISMATCH",
            "The receiving host cannot report another Agent for a manual TASK",
            hostAdapterId=host_adapter_id,
            suppliedAgentId=actual_agent_id,
        )
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        if actual_agent_id is not None:
            global_breaker = repository.open_host_capacity_breaker(
                connection,
                agent_id=actual_agent_id,
                at=at,
            )
            if global_breaker is not None:
                fail(
                    "SCHEDULER_HOST_CAPACITY_EXHAUSTED",
                    "Dispatch is paused by a shared host capacity breaker",
                    **global_breaker,
                )
        if (
            run["host_capacity_reset_at"] is not None
            and _parse_timestamp(run["host_capacity_reset_at"])
            > _parse_timestamp(at)
        ):
            fail(
                "SCHEDULER_HOST_CAPACITY_EXHAUSTED",
                "Automatic dispatch is paused by the host capacity breaker",
                capacityKey=run["host_capacity_key"],
                resetAt=run["host_capacity_reset_at"],
            )
        _assert_graph_not_replanning(nodes)
        definition, state = _node(graph, nodes, node_id)
        if not _dispatch_mode_allowed(
            run["execution_mode"],
            definition["kind"],
            dispatch_mode,
            manual_handoff_enabled=bool(
                state.get("manualHandoffEnabled")
            ),
        ):
            fail(
                "SCHEDULER_DISPATCH_MODE_INVALID",
                "The dispatch mode is not allowed for this Graph mode and Loop kind",
                executionMode=run["execution_mode"],
                nodeKind=definition["kind"],
                dispatchMode=dispatch_mode,
            )
        if dispatch_mode == "AUTO":
            expected_dispatch_decision = (
                automatic_dispatch_decision_fingerprint(
                    graph_fingerprint=graph_fingerprint(graph),
                    node_id=node_id,
                    attempt=state["attempt"],
                    host_adapter_id=str(host_adapter_id),
                    receiver_agent_id=str(actual_agent_id),
                    dispatch_transport=str(dispatch_transport),
                )
            )
            if dispatch_decision_fingerprint != expected_dispatch_decision:
                fail(
                    "SCHEDULER_DISPATCH_DECISION_MISMATCH",
                    (
                        "The automatic dispatch decision does not match "
                        "this Graph attempt and native receiver"
                    ),
                )
        receiver_attestation = None
        if require_receiver_attestation:
            if actual_host_receiver_parent_context_id is not None:
                if (
                    host_adapter_id != "codex"
                    or actual_reservation_id is None
                ):
                    fail(
                        "SCHEDULER_RECEIVER_ATTESTATION_INVALID",
                        "Host-side identity issuance is Codex AUTO only",
                    )
                reservation = connection.execute(
                    "SELECT d.* FROM dispatch_reservations d "
                    "LEFT JOIN host_receiver_identities h "
                    "ON h.reservation_id = d.reservation_id "
                    "WHERE d.reservation_id = ? AND d.run_id = ? "
                    "AND d.root_id = ? AND d.node_id = ? "
                    "AND d.attempt = ? AND d.agent_id = 'codex' "
                    "AND d.status = 'RESERVED' "
                    "AND d.expires_at >= ? "
                    "AND h.attestation_digest IS NULL LIMIT 1",
                    (
                        actual_reservation_id,
                        run["run_id"],
                        root_id,
                        node_id,
                        state["attempt"],
                        at,
                    ),
                ).fetchone()
                if reservation is None:
                    fail(
                        "SCHEDULER_CODEX_RECEIVER_RESERVATION_MISSING",
                        "Codex SubagentStart has no matching live reservation",
                    )
                actual_receiver_attestation_id = (
                    repository.issue_host_receiver_identity(
                        connection,
                        run_id=run["run_id"],
                        root_id=root_id,
                        node_id=node_id,
                        attempt=state["attempt"],
                        reservation_id=actual_reservation_id,
                        host_adapter_id="codex",
                        agent_id="codex",
                        receiver_context_id=actual_receiver_context_id,
                        parent_context_id=(
                            actual_host_receiver_parent_context_id
                        ),
                        at=at,
                    )
                )
            receiver_attestation = (
                repository.consume_receiver_attestation(
                    connection,
                    attestation_id=actual_receiver_attestation_id,
                    run_id=run["run_id"],
                    root_id=root_id,
                    node_id=node_id,
                    attempt=state["attempt"],
                    receiver_context_id=actual_receiver_context_id,
                    host_adapter_id=str(host_adapter_id),
                    agent_id=str(actual_agent_id),
                    reservation_id=actual_reservation_id,
                    operation_id=operation_id,
                    at=at,
                )
            )
        if (
            definition["kind"] not in LOOP_NODE_KINDS
            or state["status"] != "READY"
        ):
            fail(
                "SCHEDULER_LOOP_NOT_READY",
                f"{node_id} is not ready for dispatch",
            )
        if definition["kind"].endswith("_REVIEW_LOOP"):
            upstream_context_ids = _upstream_receiver_context_ids(
                graph,
                nodes,
                node_id,
            )
            if not upstream_context_ids:
                fail(
                    "SCHEDULER_REVIEW_CONTEXT_UNVERIFIED",
                    "Review dispatch requires upstream context evidence",
                    nodeId=node_id,
                )
            if actual_receiver_context_id in upstream_context_ids:
                fail(
                    "SCHEDULER_REVIEW_CONTEXT_NOT_INDEPENDENT",
                    "Review must use a receiving context distinct from all "
                    "upstream implementation and review contexts",
                    nodeId=node_id,
                    receiverContextId=actual_receiver_context_id,
                )
            if (
                receiver_attestation is not None
                and receiver_attestation["parentContextId"]
                in upstream_context_ids
            ):
                fail(
                    "SCHEDULER_REVIEW_CONTEXT_NOT_INDEPENDENT",
                    "Review receiver cannot be spawned by an upstream Loop context",
                    nodeId=node_id,
                    parentContextId=receiver_attestation[
                        "parentContextId"
                    ],
                )
        if definition["kind"] == "TASK_LOOP":
            task_requirement = connection.execute(
                "SELECT status FROM task_requirement_states "
                "WHERE run_id = ? AND task_id = ?",
                (run["run_id"], definition["workItemId"]),
            ).fetchone()
            if task_requirement is None:
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "TASK requirement state is missing",
                )
            if task_requirement["status"] != "FROZEN":
                fail(
                    "SCHEDULER_TASK_REQUIREMENT_UNFROZEN",
                    "An unfrozen TASK requirement cannot be dispatched",
                    taskId=definition["workItemId"],
                )
        used = connection.execute(
            "SELECT 1 FROM graph_events WHERE operation_id = ? LIMIT 1",
            (operation_id,),
        ).fetchone()
        if used is not None:
            fail(
                "SCHEDULER_OPERATION_REUSED",
                "operation_id must be globally unique",
            )
        definitions = {
            item["id"]: item
            for item in graph["nodes"]
        }
        for reservation in repository.claimed_resource_reservations(
            connection,
            at=at,
            exclude_root_id=root_id,
        ):
            if resource_claims_overlap(
                definition["loop"]["resourceClaims"],
                reservation["resourceClaims"],
            ):
                fail(
                    "SCHEDULER_RESOURCE_CONFLICT",
                    f"{node_id} conflicts with active Loop "
                    f"{reservation['nodeId']} in Delivery "
                    f"{reservation['rootId']}",
                    conflictingRootId=reservation["rootId"],
                    conflictingNodeId=reservation["nodeId"],
                )
        for reservation in repository.active_dispatch_reservations(
            connection,
            at=at,
        ):
            if (
                reservation["dispatchReservationId"]
                == actual_reservation_id
            ):
                continue
            if resource_claims_overlap(
                definition["loop"]["resourceClaims"],
                reservation["resourceClaims"],
            ):
                fail(
                    "SCHEDULER_RESOURCE_CONFLICT",
                    f"{node_id} conflicts with dispatch-reserved Loop "
                    f"{reservation['nodeId']} in Delivery "
                    f"{reservation['rootId']}",
                    conflictingRootId=reservation["rootId"],
                    conflictingNodeId=reservation["nodeId"],
                    conflictingDispatchReservationId=reservation[
                        "dispatchReservationId"
                    ],
                )
        for active in nodes:
            if active["status"] != "CLAIMED":
                continue
            active_definition = definitions[active["nodeId"]]
            if resource_claims_overlap(
                definition["loop"]["resourceClaims"],
                active_definition["loop"]["resourceClaims"],
            ):
                fail(
                    "SCHEDULER_RESOURCE_CONFLICT",
                    f"{node_id} conflicts with active Loop "
                    f"{active['nodeId']}",
                    conflictingNodeId=active["nodeId"],
                )
        if dispatch_mode == "AUTO":
            repository.consume_dispatch_reservation(
                connection,
                reservation_id=actual_reservation_id,
                run_id=run["run_id"],
                node_id=node_id,
                attempt=state["attempt"],
                graph_fingerprint=graph_fingerprint(graph),
                decision_fingerprint=dispatch_decision_fingerprint,
                operation_id=operation_id,
                at=at,
            )
        lease = graph["runtime"]["claimPolicy"]["leaseSeconds"]
        expires = _after(at, lease)
        connection.execute(
            "UPDATE node_runs SET status = 'CLAIMED', owner = ?, "
            "operation_id = ?, claimed_at = ?, last_heartbeat_at = ?, "
            "lease_expires_at = ? WHERE run_id = ? AND node_id = ? "
            "AND attempt = ?",
            (
                owner,
                operation_id,
                at,
                at,
                expires,
                run["run_id"],
                node_id,
                state["attempt"],
            ),
        )
        repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=node_id,
            attempt=state["attempt"],
            event_type="LOOP_CLAIMED",
            actor=owner,
            operation_id=operation_id,
            payload={
                "leaseExpiresAt": expires,
                "receiverContextId": actual_receiver_context_id,
                "receiverAttested": receiver_attestation is not None,
                **(
                    {
                        "receiverParentContextId": receiver_attestation[
                            "parentContextId"
                        ],
                        "hostAdapterId": receiver_attestation[
                            "hostAdapterId"
                        ],
                    }
                    if receiver_attestation is not None
                    else {}
                ),
                **(
                    {"hostAdapterId": host_adapter_id}
                    if (
                        dispatch_mode == "MANUAL"
                        and host_adapter_id in HOST_ADAPTER_AGENTS
                    )
                    else {}
                ),
                **(
                    {
                        "agentId": actual_agent_id,
                        **(
                            {
                                "actualModelId": observed_actual_model_id,
                                "actualModelSource": "HOST_REPORTED",
                            }
                            if observed_actual_model_id is not None
                            else {}
                        ),
                    }
                    if actual_agent_id is not None
                    else {}
                ),
                "dispatchMode": dispatch_mode,
                "dispatchTransport": dispatch_transport,
                "dispatchReservationId": actual_reservation_id,
                "dispatchDecisionFingerprint": (
                    dispatch_decision_fingerprint
                ),
            },
            at=at,
        )
        connection.execute(
            "UPDATE runs SET status = 'ACTIVE', updated_at = ? "
            "WHERE run_id = ?",
            (at, run["run_id"]),
        )
    repository.write_projections(root_id)
    return {
        **loop_context(
            root=root,
            root_id=root_id,
            node_id=node_id,
            explicit_dogfood=explicit_dogfood,
        ),
        "owner": owner,
        "agentId": actual_agent_id,
        "actualModelId": observed_actual_model_id,
        "actualModelSource": (
            "HOST_REPORTED"
            if observed_actual_model_id is not None
            else None
        ),
        "receiverContextId": actual_receiver_context_id,
        "receiverAttested": receiver_attestation is not None,
        "dispatchMode": dispatch_mode,
        "dispatchTransport": dispatch_transport,
        "dispatchReservationId": actual_reservation_id,
        "dispatchDecisionFingerprint": (
            dispatch_decision_fingerprint
        ),
        "operationId": operation_id,
        "leaseExpiresAt": expires,
    }


def handoff_ready_automatic_task(
    *,
    root: str,
    root_id: str,
    node_id: str,
    expected_graph_fingerprint: str,
    handoff_request_id: str,
    confirmed_no_code_changes: bool,
    confirmed_by: str,
    reason: str,
    workspace_root: str | None = None,
    verified_project_scopes: list[dict[str, Any]] | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Switch one never-claimed READY automatic TASK to manual receipt."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    request_id = _identity(handoff_request_id, "handoff_request_id")
    actor = _identity(confirmed_by, "confirmed_by")
    if confirmed_no_code_changes is not True:
        fail(
            "SCHEDULER_MANUAL_HANDOFF_CONFIRMATION_REQUIRED",
            "Manual recovery requires explicit confirmation that no code "
            "changes were made for this TASK attempt",
        )
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1024:
        fail(
            "SCHEDULER_MANUAL_HANDOFF_REASON_INVALID",
            "Manual recovery requires a concise non-empty reason",
        )
    normalized_reason = reason.strip()
    stored = repository.hierarchy(root_id)
    if expected_graph_fingerprint != stored["graphFingerprint"]:
        fail(
            "SCHEDULER_GRAPH_FINGERPRINT_MISMATCH",
            "The expected Graph fingerprint is stale",
            expectedGraphFingerprint=expected_graph_fingerprint,
            actualGraphFingerprint=stored["graphFingerprint"],
        )
    actual_workspace = workspace_root or root
    repository.assert_delivery_workspace(root_id, actual_workspace)
    delivery = stored["hierarchy"]["delivery"]
    git_workspaces: list[tuple[str, object]] = []
    git_binding = delivery.get("gitBinding")
    if git_binding is not None:
        git_workspaces.append((actual_workspace, git_binding))
    elif delivery.get("projectScopes") is not None:
        if verified_project_scopes is None:
            fail(
                "SCHEDULER_MANUAL_HANDOFF_PROJECT_SCOPES_REQUIRED",
                "Multi-project manual recovery requires verified project worktrees",
            )
        git_workspaces.extend(
            (scope["workspaceRoot"], scope["gitBinding"])
            for scope in verified_project_scopes
            if scope.get("access") == "READ_WRITE"
            and scope.get("gitBinding") is not None
        )
    for git_workspace_root, workspace_binding in git_workspaces:
        provenance = inspect_frozen_git_workspace_provenance(
            git_workspace_root,
            workspace_binding,
        )
        working_tree = provenance["workingTree"]
        if not working_tree["clean"]:
            fail(
                "SCHEDULER_MANUAL_HANDOFF_WORKTREE_DIRTY",
                "Automatic TASK recovery requires every Delivery worktree to be clean",
                workspaceRoot=git_workspace_root,
                workingTreeStateFingerprint=working_tree[
                    "stateFingerprint"
                ],
            )

    replayed = False
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        existing_operation = connection.execute(
            "SELECT * FROM graph_events WHERE operation_id = ? LIMIT 1",
            (request_id,),
        ).fetchone()
        if existing_operation is not None:
            if (
                existing_operation["run_id"] != run["run_id"]
                or existing_operation["node_id"] != node_id
                or existing_operation["event_type"]
                != "LOOP_MANUAL_HANDOFF_ENABLED"
                or existing_operation["actor"] != actor
            ):
                fail(
                    "SCHEDULER_OPERATION_REUSED",
                    "handoff_request_id must be globally unique",
                )
            payload = json.loads(existing_operation["payload_json"])
            if payload.get("reason") != normalized_reason:
                fail(
                    "SCHEDULER_OPERATION_REUSED",
                    "A replayed handoff request must keep the same reason",
                )
            replayed = True
        else:
            if run["execution_mode"] != "active":
                fail(
                    "SCHEDULER_MANUAL_HANDOFF_AUTOMATIC_ONLY",
                    "Only an active AUTOMATIC Graph can use TASK recovery handoff",
                    executionMode=run["execution_mode"],
                )
            definition, state = _node(graph, nodes, node_id)
            if definition["kind"] != "TASK_LOOP":
                fail(
                    "SCHEDULER_MANUAL_HANDOFF_TASK_ONLY",
                    "Only a TASK implementation Loop can be handed to a manual receiver",
                    nodeKind=definition["kind"],
                )
            if state["status"] != "READY":
                fail(
                    "SCHEDULER_MANUAL_HANDOFF_NOT_READY",
                    "Manual recovery requires an unclaimed READY TASK Loop",
                    status=state["status"],
                )
            if state.get("manualHandoffEnabled"):
                fail(
                    "SCHEDULER_MANUAL_HANDOFF_ALREADY_ENABLED",
                    "This TASK Loop is already reserved for manual receipt",
                )
            claimed = connection.execute(
                "SELECT 1 FROM graph_events WHERE run_id = ? AND node_id = ? "
                "AND attempt = ? AND event_type = 'LOOP_CLAIMED' LIMIT 1",
                (run["run_id"], node_id, state["attempt"]),
            ).fetchone()
            if claimed is not None:
                fail(
                    "SCHEDULER_MANUAL_HANDOFF_ALREADY_CLAIMED",
                    "A previously claimed TASK attempt cannot be converted to manual recovery",
                )
            repository.expire_dispatch_reservations(connection, at=at)
            live_reservation = connection.execute(
                "SELECT reservation_id, expires_at FROM dispatch_reservations "
                "WHERE run_id = ? AND node_id = ? AND attempt = ? "
                "AND status = 'RESERVED' AND expires_at >= ? LIMIT 1",
                (run["run_id"], node_id, state["attempt"], at),
            ).fetchone()
            if live_reservation is not None:
                fail(
                    "SCHEDULER_MANUAL_HANDOFF_RESERVATION_ACTIVE",
                    "Wait for the current automatic dispatch reservation to expire before manual recovery",
                    dispatchReservationId=live_reservation[
                        "reservation_id"
                    ],
                    reservationExpiresAt=live_reservation["expires_at"],
                )
            repository.append_event(
                connection,
                run_id=run["run_id"],
                node_id=node_id,
                attempt=state["attempt"],
                event_type="LOOP_MANUAL_HANDOFF_ENABLED",
                actor=actor,
                operation_id=request_id,
                payload={
                    "reason": normalized_reason,
                    "confirmedNoCodeChanges": True,
                    "dispatchMode": "MANUAL",
                },
                at=at,
            )
            connection.execute(
                "UPDATE runs SET updated_at = ? WHERE run_id = ?",
                (at, run["run_id"]),
            )
    repository.write_projections(root_id)
    run_status = repository.run(root_id)
    state = next(
        item for item in run_status["nodes"] if item["nodeId"] == node_id
    )
    return {
        "rootId": root_id,
        "nodeId": node_id,
        "attempt": state["attempt"],
        "executionMode": run_status["executionMode"],
        "handoffRequestId": request_id,
        "handoffRequestReplayed": replayed,
        "manualTaskHandoff": {
            "state": "READY",
            "dispatchMode": "MANUAL",
            "receiverPrompt": (
                "在独立人工接收上下文中继续已冻结 Delivery Graph；不要重新 "
                "preview、确认 baseline 或选择执行模式。先读取 graph_frontier，"
                f"再对 {root_id}/{node_id} 调用 dispatch_loop，明确提交 "
                "dispatch_mode=MANUAL；完成 TASK 后照常上报结果，后续 Review "
                "仍由 AUTOMATIC 独立 receiver 执行。"
            ),
        },
    }


def attest_loop_receiver(
    *,
    root: str,
    root_id: str,
    node_id: str,
    receiver_context_id: str,
    parent_context_id: str,
    host_adapter_id: str,
    dispatch_reservation_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Issue a one-time claim grant from a model-external host adapter."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    receiver_context_id = _identity(
        receiver_context_id,
        "receiver_context_id",
    )
    parent_context_id = _identity(parent_context_id, "parent_context_id")
    if host_adapter_id not in HOST_ADAPTER_AGENTS:
        fail(
            "SCHEDULER_HOST_ADAPTER_UNTRUSTED",
            "Receiver attestations require an exact trusted host adapter",
        )
    reservation_id = _identity(
        dispatch_reservation_id,
        "dispatch_reservation_id",
    )
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        definition, state = _node(graph, nodes, node_id)
        if state["status"] != "READY":
            fail(
                "SCHEDULER_RECEIVER_ATTESTATION_NOT_READY",
                "Only a ready Loop can receive a host attestation",
                nodeId=node_id,
                status=state["status"],
            )
        if not _dispatch_mode_allowed(
            run["execution_mode"],
            definition["kind"],
            "AUTO",
        ):
            fail(
                "SCHEDULER_STATE_INVALID",
                "Receiver attestations require an automatically dispatched Loop",
            )
        reservation = connection.execute(
            "SELECT * FROM dispatch_reservations "
            "WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if (
            reservation is None
            or reservation["status"] != "RESERVED"
            or reservation["expires_at"] < at
            or reservation["run_id"] != run["run_id"]
            or reservation["node_id"] != node_id
            or reservation["attempt"] != state["attempt"]
        ):
            fail(
                "SCHEDULER_RECEIVER_ATTESTATION_RESERVATION_INVALID",
                "Automatic receiver attestation requires its live reservation",
            )
        attestation_id = repository.issue_receiver_attestation(
            connection,
            run_id=run["run_id"],
            root_id=root_id,
            node_id=node_id,
            attempt=state["attempt"],
            receiver_context_id=receiver_context_id,
            parent_context_id=parent_context_id,
            host_adapter_id=host_adapter_id,
            reservation_id=reservation_id,
            at=at,
        )
    return {
        "rootId": root_id,
        "nodeId": node_id,
        "receiverContextId": receiver_context_id,
        "receiverAttestationId": attestation_id,
        "hostAdapterId": host_adapter_id,
    }


def claim_codex_subagent_receiver(
    *,
    root: str,
    root_id: str,
    workspace_root: str,
    receiver_context_id: str,
    parent_context_id: str,
    actual_model_id: str | None,
    dispatch_reservation_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Atomically claim or recover one Codex-native AUTO assignment."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    repository.assert_delivery_workspace(root_id, workspace_root)
    receiver_context_id = _identity(
        receiver_context_id,
        "receiver_context_id",
    )
    parent_context_id = _identity(parent_context_id, "parent_context_id")
    observed_actual_model_id = (
        _executor_descriptor(actual_model_id, "actual_model_id")
        if actual_model_id is not None
        else None
    )
    reservation_id = _identity(
        dispatch_reservation_id,
        "dispatch_reservation_id",
    )

    def committed_assignment(connection: Any) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT h.operation_id, d.node_id, "
            "d.decision_fingerprint, n.lease_expires_at "
            "FROM host_receiver_identities h "
            "JOIN dispatch_reservations d "
            "ON d.reservation_id = h.reservation_id "
            "JOIN node_runs n ON n.run_id = h.run_id "
            "AND n.node_id = h.node_id AND n.attempt = h.attempt "
            "WHERE h.root_id = ? AND h.reservation_id = ? "
            "AND h.host_adapter_id = 'codex' "
            "AND h.agent_id = 'codex' "
            "AND h.receiver_context_id = ? "
            "AND h.parent_context_id = ? AND h.status = 'CONSUMED' "
            "AND n.status = 'CLAIMED' "
            "AND n.operation_id = h.operation_id LIMIT 1",
            (
                root_id,
                reservation_id,
                receiver_context_id,
                parent_context_id,
            ),
        ).fetchone()
        if row is None:
            return None
        return {
            "rootId": root_id,
            "nodeId": row["node_id"],
            "agentId": "codex",
            "actualModelId": observed_actual_model_id,
            "actualModelSource": (
                "HOST_REPORTED"
                if observed_actual_model_id is not None
                else None
            ),
            "receiverContextId": receiver_context_id,
            "receiverAttested": True,
            "dispatchMode": "AUTO",
            "dispatchTransport": HOST_NATIVE_DISPATCH_TRANSPORT,
            "dispatchReservationId": reservation_id,
            "dispatchDecisionFingerprint": row[
                "decision_fingerprint"
            ],
            "operationId": row["operation_id"],
            "leaseExpiresAt": row["lease_expires_at"],
        }

    with repository.transaction() as connection:
        graph, run, _nodes = _loaded(connection, root_id)
        recovered = committed_assignment(connection)
        if recovered is not None:
            return recovered
        at = _locked_timestamp(now, run["updated_at"])
        if run["execution_mode"] not in GRAPH_EXECUTION_MODES:
            fail(
                "SCHEDULER_CODEX_RECEIVER_RESERVATION_MISSING",
                "Codex SubagentStart claim requires a governed Graph run",
            )
        repository.expire_dispatch_reservations(connection, at=at)
        reservation = connection.execute(
            "SELECT d.* FROM dispatch_reservations d "
            "LEFT JOIN host_receiver_identities h "
            "ON h.reservation_id = d.reservation_id "
            "WHERE d.reservation_id = ? AND d.run_id = ? "
            "AND d.root_id = ? AND d.agent_id = 'codex' "
            "AND d.status = 'RESERVED' "
            "AND d.expires_at >= ? AND h.attestation_digest IS NULL "
            "LIMIT 1",
            (
                reservation_id,
                run["run_id"],
                root_id,
                at,
            ),
        ).fetchone()
        if reservation is None:
            fail(
                "SCHEDULER_CODEX_RECEIVER_RESERVATION_MISSING",
                "Codex SubagentStart has no matching live AUTO reservation",
            )
        reservation_values = dict(reservation)

    operation_id = "codex-claim-" + secrets.token_hex(24)
    try:
        return dispatch_loop(
            root=root,
            root_id=root_id,
            node_id=reservation_values["node_id"],
            owner=receiver_context_id,
            operation_id=operation_id,
            agent_id="codex",
            actual_model_id=observed_actual_model_id,
            receiver_context_id=receiver_context_id,
            dispatch_mode="AUTO",
            dispatch_transport=HOST_NATIVE_DISPATCH_TRANSPORT,
            dispatch_reservation_id=reservation_id,
            dispatch_decision_fingerprint=reservation_values[
                "decision_fingerprint"
            ],
            host_native_agent_ids=("codex",),
            host_adapter_id="codex",
            require_receiver_attestation=True,
            host_receiver_parent_context_id=parent_context_id,
            explicit_dogfood=explicit_dogfood,
            now=now,
        )
    except Exception:
        with repository.transaction() as connection:
            recovered = committed_assignment(connection)
        if recovered is not None:
            return recovered
        raise


def authorize_codex_subagent_operation(
    *,
    root: str,
    root_id: str,
    node_id: str,
    workspace_root: str,
    receiver_context_id: str,
    parent_context_id: str,
    dispatch_reservation_id: str | None,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Resolve a claimed Codex child operation for a PreToolUse hook."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    repository.assert_delivery_workspace(root_id, workspace_root)
    receiver_context_id = _identity(
        receiver_context_id,
        "receiver_context_id",
    )
    parent_context_id = _identity(parent_context_id, "parent_context_id")
    reservation_id = (
        _identity(
            dispatch_reservation_id,
            "dispatch_reservation_id",
        )
        if dispatch_reservation_id is not None
        else None
    )
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        definition, state = _node(graph, nodes, node_id)
        if (
            not _active_claim(
                state,
                operation_id=state["operationId"],
                at=at,
            )
            or state["owner"] != receiver_context_id
        ):
            fail(
                "SCHEDULER_CODEX_RECEIVER_OPERATION_UNAUTHORIZED",
                "The current Codex context does not own this live Loop",
            )
        claimed_event = connection.execute(
            "SELECT payload_json FROM graph_events "
            "WHERE run_id = ? AND node_id = ? AND attempt = ? "
            "AND event_type = 'LOOP_CLAIMED' AND operation_id = ? "
            "ORDER BY event_id DESC LIMIT 1",
            (
                run["run_id"],
                node_id,
                state["attempt"],
                state["operationId"],
            ),
        ).fetchone()
        payload = (
            json.loads(claimed_event["payload_json"])
            if claimed_event is not None
            else None
        )
        if (
            isinstance(payload, dict)
            and payload.get("dispatchMode") == "MANUAL"
        ):
            if (
                run["execution_mode"] != "manual"
                or definition["kind"] != "TASK_LOOP"
                or payload.get("receiverContextId")
                != receiver_context_id
                or payload.get("hostAdapterId") != "codex"
                or payload.get("agentId") != "codex"
                or payload.get("receiverAttested") is not False
                or reservation_id is not None
            ):
                fail(
                    "SCHEDULER_CODEX_RECEIVER_OPERATION_UNAUTHORIZED",
                    "The current Codex context does not own this manual TASK",
                )
            return {
                "rootId": root_id,
                "nodeId": node_id,
                "receiverContextId": receiver_context_id,
                "operationId": state["operationId"],
            }
        if reservation_id is None:
            fail(
                "SCHEDULER_CODEX_RECEIVER_OPERATION_UNAUTHORIZED",
                "An automatic Codex Loop requires its consumed reservation",
            )
        identity = connection.execute(
            "SELECT 1 FROM host_receiver_identities "
            "WHERE run_id = ? AND root_id = ? AND node_id = ? "
            "AND attempt = ? AND reservation_id = ? "
            "AND host_adapter_id = 'codex' AND agent_id = 'codex' "
            "AND receiver_context_id = ? "
            "AND parent_context_id = ? AND status = 'CONSUMED' "
            "AND operation_id = ? LIMIT 1",
            (
                run["run_id"],
                root_id,
                node_id,
                state["attempt"],
                reservation_id,
                receiver_context_id,
                parent_context_id,
                state["operationId"],
            ),
        ).fetchone()
        if identity is None:
            fail(
                "SCHEDULER_CODEX_RECEIVER_OPERATION_UNAUTHORIZED",
                "The current Codex context has no attested Loop operation",
            )
    return {
        "rootId": root_id,
        "nodeId": node_id,
        "receiverContextId": receiver_context_id,
        "operationId": state["operationId"],
    }


def authorize_claude_subagent_operation(
    *,
    root: str,
    root_id: str,
    node_id: str,
    workspace_root: str,
    receiver_context_id: str,
    parent_context_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Resolve a claimed Claude child operation for a PreToolUse hook."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    repository.assert_delivery_workspace(root_id, workspace_root)
    receiver_context_id = _identity(
        receiver_context_id,
        "receiver_context_id",
    )
    parent_context_id = _identity(parent_context_id, "parent_context_id")
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        definition, state = _node(graph, nodes, node_id)
        if not _active_claim(
            state,
            operation_id=state["operationId"],
            at=at,
        ):
            fail(
                "SCHEDULER_CLAUDE_RECEIVER_OPERATION_UNAUTHORIZED",
                "The current Claude context has no live Loop",
            )
        attestation = connection.execute(
            "SELECT 1 FROM receiver_attestations "
            "WHERE run_id = ? AND root_id = ? AND node_id = ? "
            "AND attempt = ? AND receiver_context_id = ? "
            "AND parent_context_id = ? "
            "AND host_adapter_id = 'claude-code' "
            "AND status = 'CONSUMED' AND operation_id = ? LIMIT 1",
            (
                run["run_id"],
                root_id,
                node_id,
                state["attempt"],
                receiver_context_id,
                parent_context_id,
                state["operationId"],
            ),
        ).fetchone()
        claimed_event = connection.execute(
            "SELECT payload_json FROM graph_events "
            "WHERE run_id = ? AND node_id = ? AND attempt = ? "
            "AND event_type = 'LOOP_CLAIMED' AND operation_id = ? "
            "ORDER BY event_id DESC LIMIT 1",
            (
                run["run_id"],
                node_id,
                state["attempt"],
                state["operationId"],
            ),
        ).fetchone()
        payload = (
            json.loads(claimed_event["payload_json"])
            if claimed_event is not None
            else None
        )
        if (
            isinstance(payload, dict)
            and payload.get("dispatchMode") == "MANUAL"
        ):
            if (
                run["execution_mode"] != "manual"
                or definition["kind"] != "TASK_LOOP"
                or payload.get("receiverContextId")
                != receiver_context_id
                or payload.get("hostAdapterId") != "claude-code"
                or payload.get("agentId") != "claude-code"
                or payload.get("receiverAttested") is not False
            ):
                fail(
                    "SCHEDULER_CLAUDE_RECEIVER_OPERATION_UNAUTHORIZED",
                    "The current Claude context does not own this manual TASK",
                )
            return {
                "rootId": root_id,
                "nodeId": node_id,
                "receiverContextId": receiver_context_id,
                "operationId": state["operationId"],
            }
        if (
            attestation is None
            or not isinstance(payload, dict)
            or payload.get("receiverContextId") != receiver_context_id
            or payload.get("receiverParentContextId")
            != parent_context_id
            or payload.get("hostAdapterId") != "claude-code"
            or payload.get("agentId") != "claude-code"
        ):
            fail(
                "SCHEDULER_CLAUDE_RECEIVER_OPERATION_UNAUTHORIZED",
                "The current Claude context does not own this Loop",
            )
    return {
        "rootId": root_id,
        "nodeId": node_id,
        "receiverContextId": receiver_context_id,
        "operationId": state["operationId"],
    }


def _task_requirement_snapshot(
    hierarchy: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    task = next(
        (
            node["definition"]
            for node in iter_hierarchy_nodes(hierarchy)
            if (
                node["definition"]["kind"] == "TASK"
                and node["definition"]["id"] == task_id
            )
        ),
        None,
    )
    if task is None:
        fail(
            "SCHEDULER_TASK_MISSING",
            f"TASK is missing from the Delivery: {task_id}",
        )
    return {
        "title": task["title"],
        "summary": task["summary"],
        "payload": deepcopy(task["execution"]["loop"]["payload"]),
    }


def _task_requirement_row(
    connection: Any,
    *,
    run_id: str,
    task_id: str,
) -> Any:
    row = connection.execute(
        "SELECT * FROM task_requirement_states "
        "WHERE run_id = ? AND task_id = ?",
        (run_id, task_id),
    ).fetchone()
    if row is None:
        fail(
            "SCHEDULER_TASK_MISSING",
            f"TASK requirement is missing: {task_id}",
        )
    return row


def _expected_requirement_revision(
    value: object,
    current: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value != current
    ):
        fail(
            "SCHEDULER_TASK_REQUIREMENT_REVISION_CONFLICT",
            "TASK requirement revision is not current",
            currentRevision=current,
        )
    return value


def _assert_task_requirement_unstarted(
    connection: Any,
    *,
    run_id: str,
    node_id: str,
    task_id: str,
    state: dict[str, Any],
) -> None:
    previously_claimed = connection.execute(
        "SELECT 1 FROM graph_events "
        "WHERE run_id = ? AND node_id = ? "
        "AND event_type = 'LOOP_CLAIMED' LIMIT 1",
        (run_id, node_id),
    ).fetchone()
    if (
        state["status"] not in {"PENDING", "READY"}
        or previously_claimed is not None
    ):
        fail(
            "SCHEDULER_TASK_ALREADY_STARTED",
            "Only an unstarted TASK requirement can be changed",
            taskId=task_id,
            taskStatus=state["status"],
        )


def unfreeze_task_requirement(
    *,
    root: str,
    root_id: str,
    task_id: str,
    expected_revision: int,
    authorized_by: str,
    reason: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    authorized_by = _identity(authorized_by, "authorized_by")
    if not isinstance(reason, str) or not reason.strip():
        fail(
            "SCHEDULER_TASK_REQUIREMENT_CHANGE_INVALID",
            "reason must be non-empty",
        )
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        definition = next(
            (
                node
                for node in graph["nodes"]
                if (
                    node["kind"] == "TASK_LOOP"
                    and node["workItemId"] == task_id
                )
            ),
            None,
        )
        if definition is None:
            fail(
                "SCHEDULER_TASK_MISSING",
                f"TASK is missing from the Delivery: {task_id}",
            )
        _, state = _node(graph, nodes, definition["id"])
        _assert_task_requirement_unstarted(
            connection,
            run_id=run["run_id"],
            node_id=definition["id"],
            task_id=task_id,
            state=state,
        )
        requirement = _task_requirement_row(
            connection,
            run_id=run["run_id"],
            task_id=task_id,
        )
        _expected_requirement_revision(
            expected_revision,
            requirement["revision"],
        )
        if requirement["status"] != "FROZEN":
            fail(
                "SCHEDULER_TASK_REQUIREMENT_ALREADY_UNFROZEN",
                "TASK requirement is already unfrozen",
                taskId=task_id,
            )
        connection.execute(
            "UPDATE task_requirement_states "
            "SET status = 'UNFROZEN', updated_at = ? "
            "WHERE run_id = ? AND task_id = ?",
            (at, run["run_id"], task_id),
        )
        repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=definition["id"],
            attempt=state["attempt"],
            event_type="TASK_REQUIREMENT_UNFROZEN",
            actor=authorized_by,
            operation_id=None,
            payload={
                "taskId": task_id,
                "revision": requirement["revision"],
                "reason": reason.strip(),
            },
            at=at,
        )
        connection.execute(
            "UPDATE runs SET updated_at = ? WHERE run_id = ?",
            (at, run["run_id"]),
        )
        hierarchy_row = connection.execute(
            "SELECT * FROM hierarchies WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        assert hierarchy_row is not None
        hierarchy, _ = _validated_stored_definition(hierarchy_row)
        result = {
            "rootId": root_id,
            "taskRequirement": {
                "taskId": task_id,
                "revision": requirement["revision"],
                "status": "UNFROZEN",
                "updatedAt": at,
                "requirement": _task_requirement_snapshot(
                    hierarchy,
                    task_id,
                ),
            },
            "nextAction": "REFREEZE_TASK_REQUIREMENT",
        }
    repository.write_projections(root_id)
    return result


def refreeze_task_requirement(
    *,
    root: str,
    root_id: str,
    task_id: str,
    expected_revision: int,
    requirement: object,
    confirmed_by: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    confirmed_by = _identity(confirmed_by, "confirmed_by")
    if not isinstance(requirement, dict) or set(requirement) != {
        "title",
        "summary",
        "payload",
    }:
        fail(
            "SCHEDULER_TASK_REQUIREMENT_CHANGE_INVALID",
            "requirement fields must be title, summary, and payload",
        )
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        definition = next(
            (
                node
                for node in graph["nodes"]
                if (
                    node["kind"] == "TASK_LOOP"
                    and node["workItemId"] == task_id
                )
            ),
            None,
        )
        if definition is None:
            fail(
                "SCHEDULER_TASK_MISSING",
                f"TASK is missing from the Delivery: {task_id}",
            )
        _, state = _node(graph, nodes, definition["id"])
        _assert_task_requirement_unstarted(
            connection,
            run_id=run["run_id"],
            node_id=definition["id"],
            task_id=task_id,
            state=state,
        )
        requirement_row = _task_requirement_row(
            connection,
            run_id=run["run_id"],
            task_id=task_id,
        )
        _expected_requirement_revision(
            expected_revision,
            requirement_row["revision"],
        )
        if requirement_row["status"] != "UNFROZEN":
            fail(
                "SCHEDULER_TASK_REQUIREMENT_NOT_UNFROZEN",
                "TASK requirement must be unfrozen before replacement",
                taskId=task_id,
            )
        hierarchy_row = connection.execute(
            "SELECT * FROM hierarchies WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        assert hierarchy_row is not None
        hierarchy, _ = _validated_stored_definition(hierarchy_row)
        replacement = deepcopy(hierarchy)
        task_definition = next(
            node["definition"]
            for node in iter_hierarchy_nodes(replacement)
            if (
                node["definition"]["kind"] == "TASK"
                and node["definition"]["id"] == task_id
            )
        )
        candidate_loop = {
            **task_definition["execution"]["loop"],
            "payload": requirement.get("payload"),
        }
        task_definition["title"] = requirement.get("title")
        task_definition["summary"] = requirement.get("summary")
        task_definition["execution"]["loop"] = (
            validate_loop_descriptor(candidate_loop)
        )
        normalized = validate_hierarchy_definition(replacement)
        hierarchy_value = hierarchy_fingerprint(normalized)
        revised_graph = compile_delivery_graph(
            normalized,
            hierarchy_fingerprint=hierarchy_value,
        )
        graph_value = graph_fingerprint(revised_graph)
        revision = requirement_row["revision"] + 1
        connection.execute(
            "UPDATE hierarchies SET hierarchy_fingerprint = ?, "
            "graph_fingerprint = ?, hierarchy_json = ?, graph_json = ?, "
            "updated_at = ? WHERE root_id = ?",
            (
                hierarchy_value,
                graph_value,
                json.dumps(
                    normalized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                json.dumps(
                    revised_graph,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                at,
                root_id,
            ),
        )
        connection.execute(
            "UPDATE task_requirement_states SET revision = ?, "
            "status = 'FROZEN', updated_at = ? "
            "WHERE run_id = ? AND task_id = ?",
            (revision, at, run["run_id"], task_id),
        )
        repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=definition["id"],
            attempt=state["attempt"],
            event_type="TASK_REQUIREMENT_REFROZEN",
            actor=confirmed_by,
            operation_id=None,
            payload={
                "taskId": task_id,
                "revision": revision,
                "requirement": _task_requirement_snapshot(
                    normalized,
                    task_id,
                ),
                "hierarchyFingerprint": hierarchy_value,
                "graphFingerprint": graph_value,
            },
            at=at,
        )
        connection.execute(
            "UPDATE runs SET updated_at = ? WHERE run_id = ?",
            (at, run["run_id"]),
        )
        result = {
            "rootId": root_id,
            "hierarchyFingerprint": hierarchy_value,
            "graphFingerprint": graph_value,
            "taskRequirement": {
                "taskId": task_id,
                "revision": revision,
                "status": "FROZEN",
                "updatedAt": at,
                "requirement": _task_requirement_snapshot(
                    normalized,
                    task_id,
                ),
            },
            "nextAction": "READ_GRAPH_FRONTIER",
        }
    repository.write_projections(root_id)
    return result


def heartbeat_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    operation_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        _, state = _node(graph, nodes, node_id)
        if not _active_claim(
            state,
            operation_id=operation_id,
            at=at,
        ):
            fail(
                "SCHEDULER_OPERATION_INVALID",
                "Loop claim is missing, mismatched, or expired",
            )
        expires = _after(
            at,
            graph["runtime"]["claimPolicy"]["leaseSeconds"],
        )
        connection.execute(
            "UPDATE node_runs SET last_heartbeat_at = ?, "
            "lease_expires_at = ? WHERE run_id = ? AND node_id = ? "
            "AND attempt = ?",
            (
                at,
                expires,
                run["run_id"],
                node_id,
                state["attempt"],
            ),
        )
        repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=node_id,
            attempt=state["attempt"],
            event_type="LOOP_HEARTBEAT",
            actor=state["owner"],
            operation_id=operation_id,
            payload={"leaseExpiresAt": expires},
            at=at,
        )
        connection.execute(
            "UPDATE runs SET updated_at = ? WHERE run_id = ?",
            (at, run["run_id"]),
        )
    repository.write_projections(root_id)
    return {
        "rootId": root_id,
        "nodeId": node_id,
        "status": "CLAIMED",
        "leaseExpiresAt": expires,
    }


def report_loop_progress(
    *,
    root: str,
    root_id: str,
    node_id: str,
    operation_id: str,
    phase: str,
    summary_zh: str,
    completed_zh: list[str] | None = None,
    next_step_zh: str | None = None,
    progress_percent: int | None = None,
    tests: dict[str, int] | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Record bounded user-visible progress without renewing a lease."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    payload = normalize_progress_payload(
        phase=phase,
        summary_zh=summary_zh,
        completed_zh=completed_zh,
        next_step_zh=next_step_zh,
        progress_percent=progress_percent,
        tests=tests,
    )
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        definition, state = _node(graph, nodes, node_id)
        if definition["kind"] not in LOOP_NODE_KINDS or not _active_claim(
            state,
            operation_id=operation_id,
            at=at,
        ):
            fail(
                "SCHEDULER_OPERATION_INVALID",
                "Loop claim is missing, mismatched, or expired",
            )
        event = repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=node_id,
            attempt=state["attempt"],
            event_type="LOOP_PROGRESS_REPORTED",
            actor=state["owner"],
            operation_id=operation_id,
            payload=payload,
            at=at,
        )
        connection.execute(
            "UPDATE runs SET updated_at = ? WHERE run_id = ?",
            (at, run["run_id"]),
        )
        lease_expires_at = state["leaseExpiresAt"]
    repository.write_projections(root_id)
    return {
        "rootId": root_id,
        "nodeId": node_id,
        "attempt": event["attempt"],
        "eventUuid": event["eventUuid"],
        "reportedAt": event["recordedAt"],
        "phase": payload["phase"],
        "phaseZh": PROGRESS_PHASE_TEXT[payload["phase"]],
        "summaryZh": payload["summaryZh"],
        "completedZh": payload["completedZh"],
        **(
            {"nextStepZh": payload["nextStepZh"]}
            if "nextStepZh" in payload
            else {}
        ),
        **(
            {"progressPercent": payload["progressPercent"]}
            if "progressPercent" in payload
            else {}
        ),
        **({"tests": payload["tests"]} if "tests" in payload else {}),
        "leaseExpiresAt": lease_expires_at,
        "leaseRenewed": False,
    }


def pause_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    operation_id: str,
    resume_at: str | None = None,
    capacity_scope: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    return _change_claimed_loop(
        root=root,
        root_id=root_id,
        node_id=node_id,
        operation_id=operation_id,
        target_status="PAUSED",
        event_type="NODE_PAUSED",
        resume_at=resume_at,
        capacity_scope=capacity_scope,
        explicit_dogfood=explicit_dogfood,
        now=now,
    )


def report_host_capacity_exhausted(
    *,
    root: str,
    root_id: str,
    node_id: str,
    reset_at: str,
    host_adapter_id: str,
    receiver_context_id: str,
    report_id: str,
    reason: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Trip a host-side hard-quota breaker without a live model call."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if host_adapter_id not in HOST_ADAPTER_AGENTS:
        fail(
            "SCHEDULER_HOST_ADAPTER_UNTRUSTED",
            "Hard quota reports require an exact trusted host adapter",
        )
    receiver_context_id = _identity(
        receiver_context_id,
        "receiver_context_id",
    )
    report_id = _identity(report_id, "report_id")
    capacity_key = HOST_CAPACITY_KEYS[host_adapter_id]
    affected_agent_id = HOST_ADAPTER_AGENTS[host_adapter_id]
    if not isinstance(reason, str) or not reason.strip():
        fail(
            "SCHEDULER_HOST_CAPACITY_REPORT_INVALID",
            "reason must describe the observed host capacity failure",
        )
    normalized_reason = reason.strip()
    if len(normalized_reason) > 1024:
        fail(
            "SCHEDULER_HOST_CAPACITY_REPORT_INVALID",
            "reason must not exceed 1024 characters",
        )
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        normalized_reset_at = _future_timestamp(reset_at, at=at)
        if (
            _parse_timestamp(normalized_reset_at) - _parse_timestamp(at)
            > MAX_HOST_CAPACITY_RESET
        ):
            fail(
                "SCHEDULER_HOST_CAPACITY_REPORT_INVALID",
                "Host capacity reset time cannot exceed 24 hours",
            )
        replay = connection.execute(
            "SELECT * FROM host_capacity_breakers WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        if replay is not None:
            return {
                "rootId": root_id,
                "status": replay["status"],
                "capacityKey": replay["capacity_key"],
                "resetAt": replay["reset_at"],
                "affectedNodeIds": [],
                "cancelRecurringMonitors": True,
                "wakeMode": "HOST_NATIVE_ONE_SHOT",
                "idempotentReplay": True,
            }
        existing = connection.execute(
            "SELECT * FROM host_capacity_breakers "
            "WHERE capacity_key = ? AND status = 'OPEN'",
            (capacity_key,),
        ).fetchone()
        if existing is not None:
            if _parse_timestamp(normalized_reset_at) > _parse_timestamp(
                existing["reset_at"]
            ):
                fail(
                    "SCHEDULER_HOST_CAPACITY_REPORT_INVALID",
                    "A later report cannot extend an open capacity breaker",
                )
            return {
                "rootId": root_id,
                "status": "OPEN",
                "capacityKey": capacity_key,
                "resetAt": existing["reset_at"],
                "affectedNodeIds": [],
                "cancelRecurringMonitors": True,
                "wakeMode": "HOST_NATIVE_ONE_SHOT",
                "idempotentReplay": True,
            }
        _, target = _node(graph, nodes, node_id)
        if (
            target["status"] != "CLAIMED"
            or target.get("agentId") != affected_agent_id
            or target.get("receiverContextId") != receiver_context_id
        ):
            fail(
                "SCHEDULER_HOST_CAPACITY_REPORT_INVALID",
                "Hard quota evidence must match the claimed host receiver",
                nodeId=node_id,
                status=target["status"],
            )
        connection.execute(
            "INSERT INTO host_capacity_breakers("
            "capacity_key, host_adapter_id, agent_id, reset_at, report_id, "
            "status, reported_at, reason) "
            "VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?) "
            "ON CONFLICT(capacity_key) DO UPDATE SET "
            "host_adapter_id = excluded.host_adapter_id, "
            "agent_id = excluded.agent_id, reset_at = excluded.reset_at, "
            "report_id = excluded.report_id, status = 'OPEN', "
            "reported_at = excluded.reported_at, restored_at = NULL, "
            "reason = excluded.reason",
            (
                capacity_key,
                host_adapter_id,
                affected_agent_id,
                normalized_reset_at,
                report_id,
                at,
                normalized_reason,
            ),
        )
        pause_metadata = json.dumps(
            {"schedulerPause": {"capacityScope": "HOST"}},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        affected_by_run: dict[str, list[str]] = {}
        active_runs = connection.execute(
            "SELECT * FROM runs WHERE status NOT IN "
            "('COMPLETED', 'CANCELLED', 'SUPERSEDED')"
        ).fetchall()
        for affected_run in active_runs:
            affected_nodes = repository.latest_nodes(
                connection,
                affected_run["run_id"],
            )
            matching_nodes = [
                state
                for state in affected_nodes
                if state["status"] == "CLAIMED"
                and state.get("agentId") == affected_agent_id
            ]
            if not matching_nodes:
                continue
            revision = connection.execute(
                "SELECT * FROM delivery_revisions "
                "WHERE root_id = ? AND revision = ?",
                (affected_run["root_id"], affected_run["revision"]),
            ).fetchone()
            if revision is None:
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "Capacity breaker found a run without its revision",
                )
            _, affected_graph = _validated_stored_definition(revision)
            repository.append_event(
                connection,
                run_id=affected_run["run_id"],
                node_id=None,
                attempt=None,
                event_type="HOST_CAPACITY_EXHAUSTED",
                actor=host_adapter_id,
                operation_id=None,
                payload={
                    "capacityKey": capacity_key,
                    "resetAt": normalized_reset_at,
                    "reportedAt": at,
                    "reason": normalized_reason,
                    "reportId": report_id,
                    "affectedNodeIds": sorted(
                        state["nodeId"] for state in matching_nodes
                    ),
                },
                at=at,
            )
            for state in matching_nodes:
                connection.execute(
                    "UPDATE node_runs SET status = 'PAUSED', "
                    "finished_at = ?, lease_expires_at = NULL, "
                    "outcome_json = ? WHERE run_id = ? AND node_id = ? "
                    "AND attempt = ?",
                    (
                        normalized_reset_at,
                        pause_metadata,
                        affected_run["run_id"],
                        state["nodeId"],
                        state["attempt"],
                    ),
                )
                repository.append_event(
                    connection,
                    run_id=affected_run["run_id"],
                    node_id=state["nodeId"],
                    attempt=state["attempt"],
                    event_type="NODE_PAUSED",
                    actor=host_adapter_id,
                    operation_id=state["operationId"],
                    payload={
                        "resumeAt": normalized_reset_at,
                        "capacityScope": "HOST",
                        "hard429": True,
                        "capacityKey": capacity_key,
                    },
                    at=at,
                )
            connection.execute(
                "UPDATE runs SET host_capacity_key = ?, "
                "host_capacity_reset_at = ?, host_capacity_reported_at = ?, "
                "host_capacity_reason = ?, updated_at = ? WHERE run_id = ?",
                (
                    capacity_key,
                    normalized_reset_at,
                    at,
                    normalized_reason,
                    at,
                    affected_run["run_id"],
                ),
            )
            repository.refresh_ready(
                connection,
                affected_graph,
                affected_run["run_id"],
                at=at,
            )
            affected_by_run[affected_run["root_id"]] = sorted(
                state["nodeId"] for state in matching_nodes
            )
        affected = affected_by_run.get(root_id, [])
    for affected_root_id in affected_by_run:
        repository.write_projections(affected_root_id)
    return {
        "rootId": root_id,
        "status": "OPEN",
        "capacityKey": capacity_key,
        "resetAt": normalized_reset_at,
        "affectedNodeIds": affected,
        "cancelRecurringMonitors": True,
        "wakeMode": "HOST_NATIVE_ONE_SHOT",
    }


def _change_claimed_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    operation_id: str,
    target_status: str,
    event_type: str,
    resume_at: str | None,
    capacity_scope: str | None,
    explicit_dogfood: bool,
    now: object,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        _, state = _node(graph, nodes, node_id)
        if not _active_claim(
            state,
            operation_id=operation_id,
            at=at,
        ):
            fail(
                "SCHEDULER_OPERATION_INVALID",
                "Loop does not have the supplied active operation",
            )
        normalized_resume_at = (
            _future_timestamp(resume_at, at=at)
            if resume_at is not None
            else None
        )
        normalized_capacity_scope = _capacity_scope(
            capacity_scope,
            has_resume_at=normalized_resume_at is not None,
        )
        pause_metadata = (
            json.dumps(
                {
                    "schedulerPause": {
                        "capacityScope": normalized_capacity_scope,
                    }
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if normalized_capacity_scope is not None
            else None
        )
        connection.execute(
            "UPDATE node_runs SET status = ?, finished_at = ?, "
            "lease_expires_at = NULL, outcome_json = ? "
            "WHERE run_id = ? AND node_id = ? AND attempt = ?",
            (
                target_status,
                normalized_resume_at,
                pause_metadata,
                run["run_id"],
                node_id,
                state["attempt"],
            ),
        )
        repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=node_id,
            attempt=state["attempt"],
            event_type=event_type,
            actor=state["owner"],
            operation_id=operation_id,
            payload=(
                {
                    "resumeAt": normalized_resume_at,
                    "capacityScope": normalized_capacity_scope,
                }
                if normalized_resume_at is not None
                else {}
            ),
            at=at,
        )
        repository.refresh_ready(
            connection,
            graph,
            run["run_id"],
            at=at,
        )
    repository.write_projections(root_id)
    result = {
        "rootId": root_id,
        "nodeId": node_id,
        "status": target_status,
    }
    if target_status == "PAUSED":
        result.update(
            {
                "executionPolicy": loop_execution_policy(
                    graph_assurance_profile(graph)
                ),
                "handoff": {
                    "rootId": root_id,
                    "nodeId": node_id,
                    "resumeSequence": [
                        "graph_frontier",
                        "resume_loop",
                        "graph_frontier",
                        "loop_context",
                        "dispatch_loop",
                    ],
                    "reuseFrozenGraph": True,
                    "reprepare": False,
                    "refreeze": False,
                },
            }
        )
        if normalized_resume_at is not None:
            result["handoff"]["resumeSequence"] = [
                "workspace_status",
                "graph_frontier",
                "loop_context",
                "dispatch_loop",
            ]
            result.update(
                {
                    "resumeAt": normalized_resume_at,
                    "capacityScope": normalized_capacity_scope,
                    "nextAction": (
                        "WAIT_FOR_HOST_CAPACITY"
                        if normalized_capacity_scope == "HOST"
                        else "WAIT_FOR_EXECUTOR_CAPACITY"
                    ),
                }
            )
    return result


def resume_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        _assert_graph_not_replanning(nodes)
        definition, state = _node(graph, nodes, node_id)
        if (
            definition["kind"] not in LOOP_NODE_KINDS
            or state["status"] != "PAUSED"
        ):
            fail(
                "SCHEDULER_LOOP_NOT_PAUSED",
                f"{node_id} is not paused",
            )
        connection.execute(
            "UPDATE node_runs SET status = 'PENDING', owner = NULL, "
            "operation_id = NULL, claimed_at = NULL, "
            "last_heartbeat_at = NULL, lease_expires_at = NULL, "
            "finished_at = NULL, outcome_json = NULL "
            "WHERE run_id = ? AND node_id = ? AND attempt = ?",
            (run["run_id"], node_id, state["attempt"]),
        )
        repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=node_id,
            attempt=state["attempt"],
            event_type="NODE_RESUMED",
            actor="CONTROLLER",
            operation_id=None,
            payload={},
            at=at,
        )
        repository.refresh_ready(
            connection,
            graph,
            run["run_id"],
            at=at,
        )
    repository.write_projections(root_id)
    return {
        "rootId": root_id,
        "nodeId": node_id,
        "status": "READY",
        "executionPolicy": loop_execution_policy(
            graph_assurance_profile(graph)
        ),
        "nextAction": (
            "READ_GRAPH_FRONTIER_AND_REDISPATCH_"
            "IN_INDEPENDENT_CONTEXT"
        ),
    }


def record_loop_result(
    *,
    root: str,
    root_id: str,
    node_id: str,
    operation_id: str,
    outcome: object,
    failure_class: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    normalized = validate_loop_outcome(outcome)
    if normalized["status"] == "BLOCKED":
        if failure_class is None:
            fail(
                "SCHEDULER_FAILURE_CLASS_REQUIRED",
                "BLOCKED is reserved for a concrete condition that leaves "
                "no in-scope path to progress; provide failure_class only "
                "after internal correction and reevaluation are exhausted",
            )
        if failure_class not in FAILURE_CLASSES:
            fail(
                "SCHEDULER_FAILURE_CLASS_INVALID",
                "failure_class is not supported",
            )
    elif failure_class is not None:
        fail(
            "SCHEDULER_FAILURE_CLASS_INVALID",
            "failure_class is only valid for BLOCKED outcomes",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    event_by_status = {
        "SUCCEEDED": "LOOP_SUCCEEDED",
        "BLOCKED": "LOOP_BLOCKED",
        "REPLAN_REQUIRED": "LOOP_REPLAN_REQUIRED",
        "CANCELLED": "LOOP_CANCELLED",
    }
    state_by_status = {
        "SUCCEEDED": "SUCCEEDED",
        "BLOCKED": "BLOCKED",
        "REPLAN_REQUIRED": "BLOCKED",
        "CANCELLED": "CANCELLED",
    }
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        definition, state = _node(graph, nodes, node_id)
        if (
            definition["kind"] not in LOOP_NODE_KINDS
            or not _active_claim(
                state,
                operation_id=operation_id,
                at=at,
            )
        ):
            fail(
                "SCHEDULER_OPERATION_INVALID",
                "Loop does not have the supplied active operation",
            )
        scheduler_status = state_by_status[normalized["status"]]
        effective_failure = (
            "REPLAN_REQUIRED"
            if normalized["status"] == "REPLAN_REQUIRED"
            else failure_class
        )
        connection.execute(
            "UPDATE node_runs SET status = ?, finished_at = ?, "
            "outcome_json = ?, failure_class = ? WHERE run_id = ? "
            "AND node_id = ? AND attempt = ?",
            (
                scheduler_status,
                at,
                json.dumps(
                    normalized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                effective_failure,
                run["run_id"],
                node_id,
                state["attempt"],
            ),
        )
        repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=node_id,
            attempt=state["attempt"],
            event_type=event_by_status[normalized["status"]],
            actor=state["owner"],
            operation_id=operation_id,
            payload={
                "outcome": normalized,
                "failureClass": effective_failure,
            },
            at=at,
        )
        retried = False
        if normalized["status"] == "BLOCKED":
            retried = _retry_if_allowed(
                repository,
                connection,
                graph=graph,
                run_id=run["run_id"],
                node=state,
                failure_class=str(effective_failure),
                at=at,
            )
        repository.refresh_ready(
            connection,
            graph,
            run["run_id"],
            at=at,
        )
    repository.write_projections(root_id)
    latest = next(
        node
        for node in repository.run(root_id)["nodes"]
        if node["nodeId"] == node_id
    )
    return {
        "rootId": root_id,
        "nodeId": node_id,
        "outcome": normalized,
        "schedulerStatus": latest["status"],
        "retried": retried,
        "nextAttempt": latest["attempt"] if retried else None,
    }


def record_user_confirmation(
    *,
    root: str,
    root_id: str,
    confirmed: bool,
    confirmed_by: str,
    summary: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    if confirmed is not True:
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "Final completion requires explicit user confirmation",
        )
    confirmed_by = _identity(confirmed_by, "confirmed_by")
    if not isinstance(summary, str) or not summary.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "summary must be non-empty",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        definition = next(
            node
            for node in graph["nodes"]
            if node["kind"] == "USER_CONFIRMATION"
        )
        _, state = _node(graph, nodes, definition["id"])
        if state["status"] != "READY":
            fail(
                "SCHEDULER_CONFIRMATION_NOT_READY",
                "Final user confirmation is not ready",
            )
        connection.execute(
            "UPDATE node_runs SET status = 'COMPLETED', "
            "finished_at = ?, outcome_json = ? WHERE run_id = ? "
            "AND node_id = ? AND attempt = ?",
            (
                at,
                json.dumps(
                    {
                        "confirmedBy": confirmed_by,
                        "summary": summary.strip(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                run["run_id"],
                definition["id"],
                state["attempt"],
            ),
        )
        repository.append_event(
            connection,
            run_id=run["run_id"],
            node_id=definition["id"],
            attempt=state["attempt"],
            event_type="USER_CONFIRMED",
            actor=confirmed_by,
            operation_id=None,
            payload={"summary": summary.strip()},
            at=at,
        )
        repository.refresh_ready(
            connection,
            graph,
            run["run_id"],
            at=at,
        )
    repository.write_projections(root_id)
    return repository.run(root_id)


def cancel_graph_run(
    *,
    root: str,
    root_id: str,
    cancelled_by: str,
    reason: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    cancelled_by = _identity(cancelled_by, "cancelled_by")
    if not isinstance(reason, str) or not reason.strip():
        fail("SCHEDULER_CANCEL_INVALID", "reason must be non-empty")
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as connection:
        hierarchy_row = connection.execute(
            "SELECT status, updated_at FROM hierarchies WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        if hierarchy_row is None:
            fail(
                "SCHEDULER_RUN_MISSING",
                f"No Delivery to cancel: {root_id}",
            )
        if hierarchy_row["status"] == "ARCHIVED":
            fail(
                "SCHEDULER_RUN_TERMINAL",
                "An archived Delivery cannot be cancelled",
            )
        if hierarchy_row["status"] != "FROZEN":
            at = _locked_timestamp(now, hierarchy_row["updated_at"])
            connection.execute(
                "UPDATE hierarchies SET status = 'ABANDONED', "
                "updated_at = ? WHERE root_id = ?",
                (at, root_id),
            )
            connection.execute(
                "UPDATE delivery_revisions SET status = 'ABANDONED', "
                "updated_at = ? WHERE root_id = ?",
                (at, root_id),
            )
            connection.execute(
                "UPDATE worktree_setup_reservations SET status = "
                "'RELEASED' WHERE root_id = ? AND status IN "
                "('PENDING', 'IN_PROGRESS', 'FAILED', 'EXPIRED')",
                (root_id,),
            )
            abandoned = True
        else:
            graph, run, nodes = _loaded(connection, root_id)
            at = _locked_timestamp(now, run["updated_at"])
            if run["status"] in {
                "COMPLETED",
                "CANCELLED",
                "SUPERSEDED",
            }:
                fail(
                    "SCHEDULER_RUN_TERMINAL",
                    "A terminal scheduler run cannot be cancelled",
                )
            for node in nodes:
                if node["status"] in {
                    "SUCCEEDED",
                    "COMPLETED",
                    "CANCELLED",
                }:
                    continue
                connection.execute(
                    "UPDATE node_runs SET status = 'CANCELLED', "
                    "finished_at = ? WHERE run_id = ? AND node_id = ? "
                    "AND attempt = ?",
                    (
                        at,
                        run["run_id"],
                        node["nodeId"],
                        node["attempt"],
                    ),
                )
            repository.append_event(
                connection,
                run_id=run["run_id"],
                node_id=None,
                attempt=None,
                event_type="GRAPH_RUN_CANCELLED",
                actor=cancelled_by,
                operation_id=None,
                payload={"reason": reason.strip()},
                at=at,
            )
            connection.execute(
                "UPDATE runs SET status = 'CANCELLED', updated_at = ?, "
                "cancelled_at = ? WHERE run_id = ?",
                (at, at, run["run_id"]),
            )
            abandoned = False
    repository.write_projections(root_id)
    if abandoned:
        return {
            "rootId": root_id,
            "runId": None,
            "runStatus": "ABSENT",
            "deliveryStatus": "ABANDONED",
            "cancelledBy": cancelled_by,
            "reason": reason.strip(),
        }
    return repository.run(root_id)


def archive_delivery(
    *,
    root: str,
    root_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Hide one completed Delivery while retaining its audit history."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as connection:
        hierarchy = connection.execute(
            "SELECT status, revision, updated_at FROM hierarchies "
            "WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        if hierarchy is None:
            fail(
                "SCHEDULER_DELIVERY_MISSING",
                f"No Delivery to archive: {root_id}",
            )
        run = connection.execute(
            "SELECT status, updated_at FROM runs "
            "WHERE root_id = ? AND revision = ?",
            (root_id, hierarchy["revision"]),
        ).fetchone()
        revision = connection.execute(
            "SELECT status FROM delivery_revisions "
            "WHERE root_id = ? AND revision = ?",
            (root_id, hierarchy["revision"]),
        ).fetchone()
        if hierarchy["status"] == "ARCHIVED":
            if (
                run is None
                or run["status"] != "COMPLETED"
                or revision is None
                or revision["status"] != "ARCHIVED"
            ):
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "An archived Delivery must retain its completed run "
                    "and revision",
                    rootId=root_id,
                )
            archived_at = hierarchy["updated_at"]
            already_archived = True
        else:
            if run is None or run["status"] != "COMPLETED":
                fail(
                    "SCHEDULER_DELIVERY_NOT_COMPLETED",
                    "Only a completed Delivery can be archived",
                    rootId=root_id,
                    runStatus=(run["status"] if run is not None else None),
                )
            if hierarchy["status"] != "FROZEN":
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "A completed Delivery has an invalid hierarchy status",
                    rootId=root_id,
                    hierarchyStatus=hierarchy["status"],
                )
            if revision is None or revision["status"] != "FROZEN":
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "The completed Delivery revision is not frozen",
                    rootId=root_id,
                )
            archived_at = _locked_timestamp(
                now,
                max(hierarchy["updated_at"], run["updated_at"]),
            )
            connection.execute(
                "UPDATE hierarchies SET status = 'ARCHIVED', updated_at = ? "
                "WHERE root_id = ?",
                (archived_at, root_id),
            )
            updated_revision = connection.execute(
                "UPDATE delivery_revisions SET status = 'ARCHIVED', "
                "updated_at = ? WHERE root_id = ? AND revision = ?",
                (archived_at, root_id, hierarchy["revision"]),
            )
            if updated_revision.rowcount != 1:
                fail(
                    "SCHEDULER_STATE_INVALID",
                    "The completed Delivery revision is missing",
                    rootId=root_id,
                )
            already_archived = False
    repository.write_projections(root_id)
    return {
        "rootId": root_id,
        "status": "ARCHIVED",
        "runStatus": "COMPLETED",
        "archivedAt": archived_at,
        "alreadyArchived": already_archived,
    }


def graph_events(
    *,
    root: str,
    root_id: str,
    after_event_id: int = 0,
    limit: int = 200,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    events = repository.events(
        root_id,
        after_event_id=after_event_id,
        limit=limit,
    )
    return {
        "rootId": root_id,
        "events": events,
        "nextCursor": (
            events[-1]["eventId"]
            if events
            else after_event_id
        ),
    }


def _rebuild_graph_run_locked(
    *,
    repository: SchedulerRepository,
    root_id: str,
) -> dict[str, Any]:
    stored = repository.hierarchy(root_id)
    current_run = repository.run(root_id)
    events: list[dict[str, Any]] = []
    cursor = 0
    while True:
        page = repository.events(
            root_id,
            after_event_id=cursor,
            limit=200,
        )
        events.extend(page)
        if len(page) < 200:
            break
        cursor = page[-1]["eventId"]

    graph = stored["graph"]
    definitions = {node["id"]: node for node in graph["nodes"]}
    initial = {
        node["id"]: {
            "nodeId": node["id"],
            "attempt": 1,
            "status": "PENDING",
            "owner": None,
            "operationId": None,
            "claimedAt": None,
            "lastHeartbeatAt": None,
            "leaseExpiresAt": None,
            "resumeAt": None,
            "capacityScope": None,
            "finishedAt": None,
            "outcome": None,
            "failureClass": None,
            "manualHandoffEnabled": False,
            "manualTaskHandoff": None,
        }
        for node in graph["nodes"]
    }
    histories: dict[str, list[dict[str, Any]]] = {
        node_id: [state]
        for node_id, state in initial.items()
    }
    latest = dict(initial)
    requirement_states = {
        node["workItemId"]: {
            "taskId": node["workItemId"],
            "revision": 1,
            "status": "FROZEN",
            "updatedAt": current_run["startedAt"],
        }
        for node in graph["nodes"]
        if node["kind"] == "TASK_LOOP"
    }
    completed_at: str | None = None
    cancelled_at: str | None = None
    execution_mode = current_run["executionMode"]
    if execution_mode not in GRAPH_EXECUTION_MODES:
        fail(
            "SCHEDULER_EVENT_REPLAY_INVALID",
            "Graph replay found an unsupported execution mode",
            executionMode=execution_mode,
        )
    host_capacity: dict[str, str] | None = None
    restored_capacity_events: dict[str, dict[str, str]] = {}

    for event in events:
        event_type = event["eventType"]
        node_id = event["nodeId"]
        at = event["recordedAt"]
        payload = event["payload"]
        if event_type == "GRAPH_RUN_STARTED":
            event_execution_mode = payload.get("executionMode")
            if event_execution_mode != execution_mode:
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Graph run event execution mode does not match the run",
                    executionMode=event_execution_mode,
                )
            continue
        if event_type == "HOST_CAPACITY_EXHAUSTED":
            required_capacity_fields = (
                "capacityKey",
                "resetAt",
                "reason",
            )
            if not all(
                isinstance(payload.get(field), str)
                for field in required_capacity_fields
            ):
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Host capacity event is missing breaker metadata",
                )
            host_adapter_id = event["actor"]
            if host_adapter_id not in HOST_ADAPTER_AGENTS:
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Host capacity event has an unknown host adapter",
                )
            host_capacity = {
                "capacityKey": payload["capacityKey"],
                "resetAt": payload["resetAt"],
                "reportedAt": (
                    payload.get("reportedAt")
                    if isinstance(payload.get("reportedAt"), str)
                    else at
                ),
                "reason": payload["reason"],
                "hostAdapterId": host_adapter_id,
                "agentId": HOST_ADAPTER_AGENTS[host_adapter_id],
                "reportId": (
                    payload.get("reportId")
                    if isinstance(payload.get("reportId"), str)
                    else event["eventUuid"]
                ),
            }
            continue
        if event_type == "HOST_CAPACITY_RESTORED":
            restored_key = payload.get("capacityKey")
            restored_reset_at = payload.get("resetAt")
            restored_report_id = payload.get("reportId")
            if (
                isinstance(restored_key, str)
                and isinstance(restored_reset_at, str)
                and isinstance(restored_report_id, str)
            ):
                restored_capacity_events[restored_key] = {
                    "capacityKey": restored_key,
                    "resetAt": restored_reset_at,
                    "reportId": restored_report_id,
                    "restoredAt": at,
                }
            host_capacity = None
            continue
        if event_type == "GRAPH_RUN_CANCELLED":
            cancelled_at = at
            for state in latest.values():
                if state["status"] not in {
                    "SUCCEEDED",
                    "COMPLETED",
                    "CANCELLED",
                }:
                    state["status"] = "CANCELLED"
                    state["finishedAt"] = at
            continue
        if node_id not in latest:
            fail(
                "SCHEDULER_EVENT_REPLAY_INVALID",
                "Event references an unknown scheduler node",
            )
        if event_type == "LOOP_RETRY_SCHEDULED":
            state = {
                "nodeId": node_id,
                "attempt": event["attempt"],
                "status": "PENDING",
                "owner": None,
                "operationId": None,
                "claimedAt": None,
                "lastHeartbeatAt": None,
                "leaseExpiresAt": None,
                "resumeAt": None,
                "capacityScope": None,
                "finishedAt": None,
                "outcome": None,
                "failureClass": None,
                "manualHandoffEnabled": latest[node_id].get(
                    "manualHandoffEnabled",
                    False,
                ),
                "manualTaskHandoff": latest[node_id].get(
                    "manualTaskHandoff"
                ),
            }
            if state["attempt"] != latest[node_id]["attempt"] + 1:
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Loop retry attempt sequence is invalid",
                )
            histories[node_id].append(state)
            latest[node_id] = state
            continue
        state = latest[node_id]
        if event["attempt"] != state["attempt"]:
            fail(
                "SCHEDULER_EVENT_REPLAY_INVALID",
                "Event does not reference the latest Loop attempt",
            )
        if event_type == "RECEIVER_ROOT_ROTATED":
            if (
                state["status"] != "READY"
                or event["actor"] not in HOST_ADAPTER_AGENTS
                or payload.get("reason") != "WORKER_LOST_RETRY"
                or not isinstance(
                    payload.get("previousOrchestratorContextDigest"),
                    str,
                )
                or SHA256_FINGERPRINT.fullmatch(
                    payload["previousOrchestratorContextDigest"]
                )
                is None
                or not isinstance(
                    payload.get("orchestratorContextDigest"),
                    str,
                )
                or SHA256_FINGERPRINT.fullmatch(
                    payload["orchestratorContextDigest"]
                )
                is None
            ):
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Receiver root rotation event is invalid",
                )
            continue
        if event_type == "NODE_RESULT_CARRIED_FORWARD":
            if state["status"] != "PENDING":
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Only a pending node can receive a carried result",
                )
            state["status"] = "SUCCEEDED"
            state["finishedAt"] = at
            state["outcome"] = payload.get("outcome")
            state["failureClass"] = payload.get("failureClass")
            task_id = payload.get("taskId")
            requirement = requirement_states.get(task_id)
            if requirement is not None:
                requirement["revision"] = payload.get(
                    "requirementRevision",
                    1,
                )
                requirement["updatedAt"] = at
            continue
        if event_type == "TASK_REQUIREMENT_UNFROZEN":
            task_id = payload.get("taskId")
            requirement = requirement_states.get(task_id)
            if (
                requirement is None
                or requirement["revision"] != payload.get("revision")
                or requirement["status"] != "FROZEN"
            ):
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "TASK requirement unfreeze sequence is invalid",
                )
            requirement["status"] = "UNFROZEN"
            requirement["updatedAt"] = at
            continue
        if event_type == "TASK_REQUIREMENT_REFROZEN":
            task_id = payload.get("taskId")
            requirement = requirement_states.get(task_id)
            if (
                requirement is None
                or requirement["status"] != "UNFROZEN"
                or payload.get("revision")
                != requirement["revision"] + 1
            ):
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "TASK requirement refreeze sequence is invalid",
                )
            requirement["revision"] = payload["revision"]
            requirement["status"] = "FROZEN"
            requirement["updatedAt"] = at
            continue
        if event_type == "LOOP_MANUAL_HANDOFF_ENABLED":
            definition = definitions[node_id]
            if (
                execution_mode != "active"
                or definition["kind"] != "TASK_LOOP"
                or state["status"] != "READY"
                or state.get("manualHandoffEnabled")
                or payload.get("dispatchMode") != "MANUAL"
                or payload.get("confirmedNoCodeChanges") is not True
                or not isinstance(payload.get("reason"), str)
                or not payload["reason"].strip()
            ):
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Automatic TASK manual handoff event is invalid",
                )
            state["manualHandoffEnabled"] = True
            state["manualTaskHandoff"] = {
                "confirmedBy": event["actor"],
                "reason": payload["reason"],
                "handoffRequestId": event["operationId"],
                "enabledAt": at,
            }
            continue
        if event_type == "NODE_READY":
            state["status"] = "READY"
        elif event_type == "JOIN_COMPLETED":
            state["status"] = "SUCCEEDED"
            state["finishedAt"] = at
        elif event_type == "LOOP_CLAIMED":
            definition = next(
                node for node in graph["nodes"] if node["id"] == node_id
            )
            if not _dispatch_mode_allowed(
                execution_mode,
                definition["kind"],
                payload.get("dispatchMode"),
                manual_handoff_enabled=bool(
                    state.get("manualHandoffEnabled")
                ),
            ):
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Loop claim dispatch mode is inconsistent with the Graph run",
                    nodeId=node_id,
                )
            state.update(
                {
                    "status": "CLAIMED",
                    "owner": event["actor"],
                    "operationId": event["operationId"],
                    "claimedAt": at,
                    "lastHeartbeatAt": at,
                    "leaseExpiresAt": payload["leaseExpiresAt"],
                }
            )
        elif event_type == "LOOP_HEARTBEAT":
            state["lastHeartbeatAt"] = at
            state["leaseExpiresAt"] = payload["leaseExpiresAt"]
        elif event_type == "LOOP_PROGRESS_REPORTED":
            if (
                state["status"] != "CLAIMED"
                or event["operationId"] != state["operationId"]
                or event["actor"] != state["owner"]
            ):
                fail(
                    "SCHEDULER_EVENT_REPLAY_INVALID",
                    "Loop progress event does not belong to the live claim",
                )
            validate_progress_event_payload(payload)
        elif event_type == "NODE_PAUSED":
            state.update(
                {
                    "status": "PAUSED",
                    "leaseExpiresAt": None,
                    "resumeAt": payload.get("resumeAt"),
                    "capacityScope": payload.get("capacityScope"),
                }
            )
        elif event_type in {"NODE_RESUMED", "NODE_AUTO_RESUMED"}:
            state.update(
                {
                    "status": "PENDING",
                    "owner": None,
                    "operationId": None,
                    "claimedAt": None,
                    "lastHeartbeatAt": None,
                    "leaseExpiresAt": None,
                    "resumeAt": None,
                    "capacityScope": None,
                }
            )
        elif event_type in {
            "LOOP_SUCCEEDED",
            "LOOP_BLOCKED",
            "LOOP_REPLAN_REQUIRED",
            "LOOP_CANCELLED",
        }:
            state["status"] = {
                "LOOP_SUCCEEDED": "SUCCEEDED",
                "LOOP_BLOCKED": "BLOCKED",
                "LOOP_REPLAN_REQUIRED": "BLOCKED",
                "LOOP_CANCELLED": "CANCELLED",
            }[event_type]
            state["finishedAt"] = at
            state["outcome"] = payload["outcome"]
            state["failureClass"] = payload.get("failureClass")
        elif event_type == "CLAIM_LEASE_EXPIRED":
            state["status"] = "BLOCKED"
            state["finishedAt"] = at
            state["failureClass"] = "WORKER_LOST"
        elif event_type == "USER_CONFIRMED":
            state["status"] = "COMPLETED"
            state["finishedAt"] = at
            state["outcome"] = {
                "confirmedBy": event["actor"],
                "summary": payload["summary"],
            }
            completed_at = at
        elif event_type == "RETRY_EXHAUSTED":
            continue
        else:
            fail(
                "SCHEDULER_EVENT_REPLAY_INVALID",
                f"Unsupported scheduler event: {event_type}",
            )

    states = [
        state
        for node_id in sorted(histories)
        for state in histories[node_id]
    ]
    latest_states = [
        histories[node_id][-1]
        for node_id in sorted(histories)
    ]
    if cancelled_at is not None:
        run_status = "CANCELLED"
    elif completed_at is not None:
        run_status = "COMPLETED"
    elif any(
        state["status"] in {"BLOCKED", "CANCELLED"}
        for state in latest_states
    ):
        run_status = "BLOCKED"
    elif any(
        state["status"] == "PAUSED"
        for state in latest_states
    ) and not any(
        state["status"] in {"READY", "CLAIMED"}
        for state in latest_states
    ):
        run_status = "PAUSED"
    else:
        run_status = "ACTIVE"
    updated_at = (
        events[-1]["recordedAt"]
        if events
        else current_run["startedAt"]
    )
    with repository.transaction() as connection:
        for restored_capacity in restored_capacity_events.values():
            connection.execute(
                "UPDATE host_capacity_breakers SET status = 'RESTORED', "
                "restored_at = ? WHERE capacity_key = ? "
                "AND report_id = ? AND reset_at = ? AND status = 'OPEN'",
                (
                    restored_capacity["restoredAt"],
                    restored_capacity["capacityKey"],
                    restored_capacity["reportId"],
                    restored_capacity["resetAt"],
                ),
            )
        if host_capacity is not None:
            connection.execute(
                "INSERT INTO host_capacity_breakers("
                "capacity_key, host_adapter_id, agent_id, reset_at, "
                "report_id, status, reported_at, reason) "
                "VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?) "
                "ON CONFLICT(capacity_key) DO UPDATE SET "
                "host_adapter_id = excluded.host_adapter_id, "
                "agent_id = excluded.agent_id, reset_at = excluded.reset_at, "
                "report_id = excluded.report_id, status = 'OPEN', "
                "reported_at = excluded.reported_at, restored_at = NULL, "
                "reason = excluded.reason "
                "WHERE (host_capacity_breakers.report_id = "
                "excluded.report_id AND host_capacity_breakers.status = "
                "'OPEN') OR host_capacity_breakers.reported_at < "
                "excluded.reported_at",
                (
                    host_capacity["capacityKey"],
                    host_capacity["hostAdapterId"],
                    host_capacity["agentId"],
                    host_capacity["resetAt"],
                    host_capacity["reportId"],
                    host_capacity["reportedAt"],
                    host_capacity["reason"],
                ),
            )
        connection.execute(
            "DELETE FROM node_runs WHERE run_id = ?",
            (current_run["runId"],),
        )
        for state in states:
            connection.execute(
                "INSERT INTO node_runs(run_id, node_id, attempt, status, "
                "owner, operation_id, claimed_at, last_heartbeat_at, "
                "lease_expires_at, finished_at, outcome_json, failure_class) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    current_run["runId"],
                    state["nodeId"],
                    state["attempt"],
                    state["status"],
                    state["owner"],
                    state["operationId"],
                    state["claimedAt"],
                    state["lastHeartbeatAt"],
                    state["leaseExpiresAt"],
                    (
                        state["resumeAt"]
                        if state["status"] == "PAUSED"
                        else state["finishedAt"]
                    ),
                    (
                        json.dumps(
                            {
                                "schedulerPause": {
                                    "capacityScope": state[
                                        "capacityScope"
                                    ],
                                }
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if state["status"] == "PAUSED"
                        and state["capacityScope"] is not None
                        else (
                            json.dumps(
                                state["outcome"],
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                            if state["outcome"] is not None
                            else None
                        )
                    ),
                    state["failureClass"],
                ),
            )
        connection.execute(
            "DELETE FROM task_requirement_states WHERE run_id = ?",
            (current_run["runId"],),
        )
        for requirement in requirement_states.values():
            connection.execute(
                "INSERT INTO task_requirement_states("
                "run_id, task_id, revision, status, updated_at"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    current_run["runId"],
                    requirement["taskId"],
                    requirement["revision"],
                    requirement["status"],
                    requirement["updatedAt"],
                ),
            )
        connection.execute(
            "UPDATE runs SET status = ?, execution_mode = ?, "
            "updated_at = ?, completed_at = ?, cancelled_at = ?, "
            "host_capacity_key = ?, host_capacity_reset_at = ?, "
            "host_capacity_reported_at = ?, host_capacity_reason = ? "
            "WHERE run_id = ?",
            (
                run_status,
                execution_mode,
                updated_at,
                completed_at,
                cancelled_at,
                (
                    host_capacity["capacityKey"]
                    if host_capacity is not None
                    else None
                ),
                (
                    host_capacity["resetAt"]
                    if host_capacity is not None
                    else None
                ),
                (
                    host_capacity["reportedAt"]
                    if host_capacity is not None
                    else None
                ),
                (
                    host_capacity["reason"]
                    if host_capacity is not None
                    else None
                ),
                current_run["runId"],
            ),
        )
    repository.write_projections(root_id)
    return {
        **repository.run(root_id),
        "rebuiltFromEvents": len(events),
    }


def rebuild_graph_run(
    *,
    root: str,
    root_id: str,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    """Rebuild materialized state from one locked event snapshot."""

    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.scheduler_lock():
        return _rebuild_graph_run_locked(
            repository=repository,
            root_id=root_id,
        )


__all__ = (
    "advance_graph",
    "archive_delivery",
    "attest_loop_receiver",
    "cancel_graph_run",
    "claim_codex_subagent_receiver",
    "dispatch_loop",
    "graph_events",
    "graph_status",
    "handoff_ready_automatic_task",
    "heartbeat_loop",
    "loop_context",
    "pause_loop",
    "report_host_capacity_exhausted",
    "report_loop_progress",
    "refreeze_task_requirement",
    "record_loop_result",
    "record_user_confirmation",
    "rebuild_graph_run",
    "resume_loop",
    "unfreeze_task_requirement",
)
