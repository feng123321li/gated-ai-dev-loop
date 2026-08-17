from __future__ import annotations

from .graph_runtime_common import (
    Any,
    DISPATCH_MODES,
    HOST_ADAPTER_AGENTS,
    HOST_NATIVE_DISPATCH_TRANSPORT,
    LOOP_NODE_KINDS,
    SHA256_FINGERPRINT,
    SchedulerRepository,
    _active_claim,
    _after,
    _assert_graph_not_replanning,
    _dispatch_mode_allowed,
    _executor_descriptor,
    _identity,
    _loaded,
    _locked_timestamp,
    _node,
    _parse_timestamp,
    _upstream_receiver_context_ids,
    automatic_dispatch_decision_fingerprint,
    fail,
    graph_fingerprint,
    inspect_frozen_git_workspace_provenance,
    json,
    receiver_skill_prompt,
    resource_claims_overlap,
)
from .graph_runtime_frontier import graph_status, loop_context


def dispatch_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    owner: str,
    operation_id: str,
    agent_id: str | None = None,
    receiver_context_id: str | None = None,
    dispatch_mode: str,
    dispatch_transport: str | None = None,
    dispatch_reservation_id: str | None = None,
    dispatch_decision_fingerprint: str | None = None,
    host_native_agent_ids: tuple[str, ...] | None = None,
    host_adapter_id: str | None = None,
    verified_project_scopes: list[dict[str, Any]] | None = None,
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
    if receiver_context_id is None:
        fail(
            "SCHEDULER_RECEIVER_CONTEXT_REQUIRED",
            "dispatch_loop requires an explicit receiving context ID",
        )
    actual_receiver_context_id = _identity(
        receiver_context_id, "receiver_context_id"
    )
    expected_transport = (
        HOST_NATIVE_DISPATCH_TRANSPORT if dispatch_mode == "AUTO" else None
    )
    if expected_transport is not None and (
        dispatch_transport != expected_transport
    ):
        fail(
            "SCHEDULER_DISPATCH_TRANSPORT_REQUIRED",
            (
                "Automatic dispatch requires the HOST_NATIVE orchestration "
                "marker. The marker is required protocol input and does "
                "not prove the caller process, session, or identity"
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
            current_graph_fingerprint = graph_fingerprint(graph)
            expected_dispatch_decision = (
                automatic_dispatch_decision_fingerprint(
                    graph_fingerprint=current_graph_fingerprint,
                    node_id=node_id,
                    attempt=state["attempt"],
                    host_adapter_id=str(host_adapter_id),
                    receiver_agent_id=str(actual_agent_id),
                    dispatch_transport=str(dispatch_transport),
                )
            )
            reservation = connection.execute(
                "SELECT * FROM dispatch_reservations "
                "WHERE reservation_id = ?",
                (actual_reservation_id,),
            ).fetchone()
            if dispatch_decision_fingerprint != expected_dispatch_decision:
                retry_with_same_reservation = bool(
                    reservation is not None
                    and reservation["status"] == "RESERVED"
                    and reservation["run_id"] == run["run_id"]
                    and reservation["root_id"] == root_id
                    and reservation["node_id"] == node_id
                    and reservation["attempt"] == state["attempt"]
                    and reservation["agent_id"] == actual_agent_id
                    and reservation["graph_fingerprint"]
                    == current_graph_fingerprint
                    and reservation["decision_fingerprint"]
                    == expected_dispatch_decision
                    and _parse_timestamp(reservation["expires_at"])
                    > _parse_timestamp(at)
                )
                fail(
                    "SCHEDULER_DISPATCH_DECISION_MISMATCH",
                    (
                        "The automatic dispatch decision does not match "
                        "this Graph attempt and native receiver"
                    ),
                    expectedAttempt=state["attempt"],
                    expectedHostAdapterId=str(host_adapter_id),
                    expectedReceiverAgentId=str(actual_agent_id),
                    expectedGraphFingerprint=current_graph_fingerprint,
                    submittedDecisionFingerprint=(
                        dispatch_decision_fingerprint
                    ),
                    retryWithSameReservation=retry_with_same_reservation,
                    **(
                        {
                            "expectedDecisionFingerprint": (
                                expected_dispatch_decision
                            ),
                            "reservationExpiresAt": reservation[
                                "expires_at"
                            ],
                            "recoveryAction": (
                                "RETRY_DISPATCH_WITH_SAME_RESERVATION"
                            ),
                        }
                        if retry_with_same_reservation
                        else {
                            "recoveryAction": "PLAN_NEW_DISPATCH_BATCH"
                        }
                    ),
                )
            if (
                reservation is not None
                and reservation["status"] == "CLAIMED"
                and reservation["operation_id"] == operation_id
            ):
                claimed_event = connection.execute(
                    "SELECT payload_json FROM graph_events "
                    "WHERE run_id = ? AND node_id = ? AND attempt = ? "
                    "AND event_type = 'LOOP_CLAIMED' "
                    "AND operation_id = ? ORDER BY event_id DESC LIMIT 1",
                    (
                        run["run_id"],
                        node_id,
                        state["attempt"],
                        operation_id,
                    ),
                ).fetchone()
                payload = (
                    json.loads(claimed_event["payload_json"])
                    if claimed_event is not None
                    else None
                )
                if (
                    not _active_claim(
                        state,
                        operation_id=operation_id,
                        at=at,
                    )
                    or state["owner"] != owner
                    or reservation["run_id"] != run["run_id"]
                    or reservation["root_id"] != root_id
                    or reservation["node_id"] != node_id
                    or reservation["attempt"] != state["attempt"]
                    or reservation["agent_id"] != actual_agent_id
                    or reservation["graph_fingerprint"]
                    != current_graph_fingerprint
                    or reservation["decision_fingerprint"]
                    != dispatch_decision_fingerprint
                    or not isinstance(payload, dict)
                    or payload.get("receiverContextId")
                    != actual_receiver_context_id
                    or payload.get("hostAdapterId") != host_adapter_id
                    or payload.get("agentId") != actual_agent_id
                    or payload.get("dispatchMode") != "AUTO"
                    or payload.get("dispatchTransport")
                    != dispatch_transport
                    or payload.get("dispatchReservationId")
                    != actual_reservation_id
                    or payload.get("dispatchDecisionFingerprint")
                    != dispatch_decision_fingerprint
                ):
                    fail(
                        "SCHEDULER_DISPATCH_REPLAY_MISMATCH",
                        "The claimed reservation does not match this dispatch replay",
                    )
                status = graph_status(
                    root=root,
                    root_id=root_id,
                    explicit_dogfood=explicit_dogfood,
                    now=now,
                )
                return {
                    **loop_context(
                        root=root,
                        root_id=root_id,
                        node_id=node_id,
                        verified_project_scopes=verified_project_scopes,
                        explicit_dogfood=explicit_dogfood,
                    ),
                    "owner": owner,
                    "agentId": actual_agent_id,
                    "receiverContextId": actual_receiver_context_id,
                    "dispatchMode": "AUTO",
                    "dispatchTransport": dispatch_transport,
                    "dispatchReservationId": actual_reservation_id,
                    "dispatchDecisionFingerprint": dispatch_decision_fingerprint,
                    "operationId": operation_id,
                    "leaseExpiresAt": state["leaseExpiresAt"],
                    "dispatchReplayed": True,
                    "progressMonitor": status["progressMonitor"],
                }
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
                **(
                    {"hostAdapterId": host_adapter_id}
                    if host_adapter_id in HOST_ADAPTER_AGENTS
                    else {}
                ),
                **(
                    {
                        "agentId": actual_agent_id,
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
    status = graph_status(
        root=root,
        root_id=root_id,
        explicit_dogfood=explicit_dogfood,
        now=now,
    )
    return {
        **loop_context(
            root=root,
            root_id=root_id,
            node_id=node_id,
            verified_project_scopes=verified_project_scopes,
            explicit_dogfood=explicit_dogfood,
        ),
        "owner": owner,
        "agentId": actual_agent_id,
        "receiverContextId": actual_receiver_context_id,
        "dispatchMode": dispatch_mode,
        "dispatchTransport": dispatch_transport,
        "dispatchReservationId": actual_reservation_id,
        "dispatchDecisionFingerprint": (
            dispatch_decision_fingerprint
        ),
        "operationId": operation_id,
        "leaseExpiresAt": expires,
        "dispatchReplayed": False,
        "progressMonitor": status["progressMonitor"],
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
                "Multi-project manual recovery requires verified project workspaces",
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
                "SCHEDULER_MANUAL_HANDOFF_WORKSPACE_DIRTY",
                "Automatic TASK recovery requires every Delivery workspace to be clean",
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
                "AND status = 'RESERVED' AND expires_at > ? LIMIT 1",
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
                "在宿主原生 child 独立接收上下文中继续已冻结 Delivery Graph；"
                "本次 MANUAL claim 不携带 AUTO reservation，但必须提交 child "
                "自己的 receiver_context_id 与新 operation_id。不要重新 preview、"
                "确认 baseline 或选择执行模式。先读取 graph_frontier，"
                f"再对 {root_id}/{node_id} 调用 dispatch_loop，明确提交 "
                "dispatch_mode=MANUAL；完成 TASK 后照常上报结果，后续 Review "
                "仍由 AUTOMATIC 独立 receiver 执行。"
            )
            + (
                receiver_skill_prompt(
                    "TASK_LOOP",
                    stored["hierarchy"]["root"]["skillHints"],
                )
            ),
        },
    }
