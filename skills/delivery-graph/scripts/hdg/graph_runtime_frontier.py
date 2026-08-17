from __future__ import annotations

from .graph_runtime_common import (
    Any,
    LOOP_NODE_KINDS,
    Path,
    SchedulerRepository,
    _current_upstream_scope_snapshots,
    _loaded,
    _locked_timestamp,
    _node,
    _parse_timestamp,
    _retry_if_allowed,
    _upstream_loop_results,
    _validation_evidence_index,
    advisory_skill_hint_prompt,
    attach_progress_monitor,
    capture_verified_workspace_state,
    deepcopy,
    fail,
    graph_assurance_profile,
    iter_hierarchy_nodes,
    json,
    loop_completion_policy,
    loop_execution_policy,
    task_baseline_relative_path,
    task_has_database_projection,
    timestamp,
    work_item_projection_relative_path,
)


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
    materialized_changed = False
    with repository.transaction() as connection:
        graph, run, nodes = _loaded(connection, root_id)
        at = _locked_timestamp(now, run["updated_at"])
        for node in nodes:
            if (
                node["status"] != "CLAIMED"
                or node["leaseExpiresAt"] is None
                or _parse_timestamp(node["leaseExpiresAt"])
                > _parse_timestamp(at)
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
            materialized_changed = True
        materialized_changed = repository.refresh_ready(
            connection,
            graph,
            run["run_id"],
            at=at,
            touch_run=materialized_changed,
        ) or materialized_changed
    if materialized_changed:
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
    observed_now = timestamp(now)
    observation_at = (
        observed_now
        if _parse_timestamp(observed_now)
        >= _parse_timestamp(result["updatedAt"])
        else result["updatedAt"]
    )
    result = attach_progress_monitor(
        result,
        definition["graph"],
        observed_at=observation_at,
    )
    with repository.read() as connection:
        external_reservations = repository.claimed_resource_reservations(
            connection,
            at=observation_at,
            exclude_root_id=root_id,
        )
        dispatch_reservations = repository.active_dispatch_reservations(
            connection,
            at=observation_at,
        )
    # Import lazily because graph_frontier owns action rendering and imports
    # this module's transition functions. Reusing its pure builder gives a
    # read-only status call the exact action/deadline view without advancing
    # the scheduler or duplicating resource-conflict logic.
    from .graph_frontier import build_graph_frontier

    frontier_view = build_graph_frontier(
        definition["graph"],
        result,
        external_reservations=external_reservations,
        dispatch_reservations=dispatch_reservations,
    )
    result["nextWakeAt"] = frontier_view["nextWakeAt"]
    frontier_monitor = frontier_view.get("progressMonitor")
    if isinstance(frontier_monitor, dict):
        result["progressMonitor"] = frontier_monitor
        directive = deepcopy(frontier_monitor.get("waitDirective") or {})
        if directive.get("consumeActionsBeforeWaiting") is True:
            directive.update(
                {
                    "mode": "FRONTIER_ACTIONS_AVAILABLE",
                    "pollNotBefore": observation_at,
                    "pollTool": "graph_frontier",
                    "onTimeout": "CALL_GRAPH_FRONTIER_ONCE",
                    "consumeActionsBeforeWaiting": False,
                }
            )
            result["progressMonitor"] = deepcopy(frontier_monitor)
            result["progressMonitor"]["waitDirective"] = directive
    return result

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
    upstream_results = _upstream_loop_results(
        stored["graph"],
        states,
        node_id,
    )
    current_workspace_snapshots = (
        capture_verified_workspace_state(verified_project_scopes)
        if verified_project_scopes is not None
        else []
    )
    current_scope_snapshots = _current_upstream_scope_snapshots(
        upstream_results,
        verified_project_scopes,
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
        "upstreamLoopResults": upstream_results,
        "humanArtifacts": human_artifacts,
        "workspaceIsolation": workspace_isolation,
        "projectScopes": project_scopes,
        "projectScopeAnchors": project_scope_anchors,
        "currentWorkspaceSnapshots": current_workspace_snapshots,
        "executionPolicy": loop_execution_policy(assurance_profile),
        "completionPolicy": loop_completion_policy(
            assurance_profile,
            loop_kind=definition["kind"],
        ),
        "rules": {
            "payloadIsOpaqueToScheduler": True,
            "internalGateAndSkillPolicyOwnedByLoop": True,
            "implementationPlanMayAdaptWithinLoop": True,
            "actionableFindingsStayInsideLoop": True,
            "skillHintsAreAdvisory": True,
            "explicitSkillHintsShouldRunWhenApplicableAndAvailable": True,
            "skipSkillHintOnlyWhenStageInapplicableOrHostUnavailable": True,
            "selectSkillsAtRuntime": True,
            "prioritizeApplicableSkillHints": True,
            "returnOnlyStandardLoopOutcome": True,
            "independentReceiverRequired": True,
            "coordinatorMustNotExecuteLoopInline": True,
            "coordinatorMustNotReviewInline": True,
            "accessOnlyAuthorizedProjectScopes": True,
            "projectScopeWorkspaceRootsAreRuntimeVerified": (
                verified_project_scopes is not None
            ),
            "loopsMustNotCreateSwitchOrCheckoutGitBranches": True,
        },
    }
    skill_hint_prompt = advisory_skill_hint_prompt(context["skillHints"])
    if skill_hint_prompt is not None:
        context["skillHintPrompt"] = skill_hint_prompt
    if definition["kind"].endswith("_REVIEW_LOOP"):
        context["rules"].update(
            {
                "reuseValidUpstreamVerificationEvidence": True,
                "reviewIndependenceDoesNotRequireFullSuiteRerun": True,
            }
        )
        context["validationEvidenceIndex"] = _validation_evidence_index(
            upstream_results,
            current_workspace_snapshots,
            current_scope_snapshots,
        )
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
