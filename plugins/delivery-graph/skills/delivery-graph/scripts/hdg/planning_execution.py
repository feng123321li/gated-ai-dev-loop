from __future__ import annotations

from .planning_common import (
    Any,
    GatedLoopError,
    SchedulerRepository,
    development_baseline_contract,
    fail,
    verify_delivery_git_binding,
    verify_delivery_project_scopes,
)
from .planning_workspace import (
    _SERIAL_TERMINAL_STATUSES,
    _automatic_serial_workspace_preparation,
    _automatic_workspace_requests,
    _current_workspace_satisfies_request,
    _current_workspace_serial_preparation,
    _manual_serial_workspace_preparation,
)
from .planning_gates import (
    _attach_pending_interaction,
    _baseline_discovery,
    _capture_workspace_turn_start,
    _delivery_queue_marker,
    _frozen_automatic_result,
    _frozen_serial_workspace_gate,
    _manual_serial_workspace_gate,
    _manual_workspace_waiting_result,
    _resolve_serial_workspace_gate,
    _serial_turn_for_recorded_selection,
    _verify_frozen_delivery_workspace,
    _workspace_turn_waiting,
)
from .planning_hierarchy import freeze_hierarchy, prepare_hierarchy
from .planning_manual_handoff import (
    _assert_exact_project_authorization,
    _refresh_manual_handoff_runtime,
    create_manual_handoff,
)


_RECOVERABLE_MANUAL_GIT_ERRORS = frozenset(
    {
        "SCHEDULER_GIT_BASE_INVALID",
        "SCHEDULER_GIT_BINDING_REQUIRED",
        "SCHEDULER_GIT_BRANCH_MISMATCH",
        "SCHEDULER_GIT_DETACHED_HEAD",
        "SCHEDULER_GIT_CHECKOUT_REQUIRED",
    }
)

def _manual_baseline_reconfirmation(
    *,
    stored: dict[str, Any],
    repository: SchedulerRepository,
    workspace_root: str,
    host_adapter_id: str | None,
) -> dict[str, Any] | None:
    """Return a no-write manual-start blocker for recoverable Git drift."""

    delivery = stored["hierarchy"]["delivery"]
    try:
        verified_projects = verify_delivery_project_scopes(
            workspace_root,
            delivery,
            preparing=True,
        )
        if delivery.get("projectScopes") is None:
            verify_delivery_git_binding(
                workspace_root,
                delivery.get("gitBinding"),
                preparing=True,
            )
        elif not verified_projects:
            fail(
                "SCHEDULER_PROJECT_SCOPE_INVALID",
                "The manual handoff has no verified project",
                rootId=stored["rootId"],
            )
        return None
    except GatedLoopError as error:
        if error.code not in _RECOVERABLE_MANUAL_GIT_ERRORS:
            raise
        git_scopes = [
            scope
            for scope in delivery.get("projectScopes", [])
            if scope.get("gitBinding") is not None
        ]
        if len(git_scopes) > 1:
            fail(
                "SCHEDULER_MANUAL_MULTI_PROJECT_BASELINE_"
                "RECONFIRMATION_UNSUPPORTED",
                "Manual Git drift across multiple project scopes requires "
                "a complete explicit handoff revision; the single-workspace "
                "baseline selector cannot safely rewrite every repository",
                rootId=stored["rootId"],
                projectIds=sorted(scope["id"] for scope in git_scopes),
                driftReason=error.code,
                nextAction=(
                    "CREATE_MANUAL_REVISION_WITH_COMPLETE_PROJECT_BINDINGS"
                ),
            )
        context = _baseline_discovery(
            stored["hierarchy"],
            repository,
            workspace_root,
            host_adapter_id,
            expected_hierarchy_fingerprint=stored[
                "hierarchyFingerprint"
            ],
            expected_graph_fingerprint=stored["graphFingerprint"],
            expected_delivery_revision=stored["deliveryRevision"],
            interaction_context="MANUAL_HANDOFF_START",
        )
        if context is None:
            raise
        interaction = development_baseline_contract(
            host_adapter_id,
            git_binding=delivery.get("gitBinding"),
            candidate_branches=context["candidateBranches"],
            default_branch_ref=context["defaultBranchRef"],
            expected_hierarchy_fingerprint=stored[
                "hierarchyFingerprint"
            ],
            expected_graph_fingerprint=stored["graphFingerprint"],
            expected_delivery_revision=stored["deliveryRevision"],
            baseline_context_fingerprint=context[
                "baselineContextFingerprint"
            ],
            interaction_context="MANUAL_HANDOFF_START",
            working_tree=context["discovery"].get("workingTree"),
        )
        result = {
            "rootId": stored["rootId"],
            "deliveryRevision": stored["deliveryRevision"],
            "status": stored["status"],
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
            "graphFingerprint": stored["graphFingerprint"],
            "graphRunCreated": False,
            "manualStartState": (
                "BLOCKED_DEVELOPMENT_BASELINE_CONFIRMATION"
            ),
            "code": (
                "SCHEDULER_MANUAL_BASELINE_RECONFIRMATION_REQUIRED"
            ),
            "gitDrift": {
                "reason": error.code,
                "details": error.details,
            },
        }
        _attach_pending_interaction(result, interaction)
        result["nextAction"] = (
            "PRESENT_HOST_NATIVE_BASELINE_RECONFIRMATION"
        )
        return result

def start_manual_handoff(
    *,
    root: str,
    root_id: str,
    expected_hierarchy_fingerprint: str,
    expected_graph_fingerprint: str,
    started_by: str,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    host_adapter_id: str | None = None,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Bind and start one frozen manual handoff as a governed Graph run."""

    if not isinstance(started_by, str) or not started_by.strip():
        fail(
            "SCHEDULER_MANUAL_START_IDENTITY_REQUIRED",
            "started_by must identify the receiving orchestrator",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    graph_runtime_refresh: dict[str, Any] | None = None
    if (
        stored["hierarchyFingerprint"]
        != expected_hierarchy_fingerprint
        or stored["graphFingerprint"] != expected_graph_fingerprint
    ):
        fail(
            "SCHEDULER_MANUAL_HANDOFF_STALE",
            "The receiving CLI fingerprints do not match the frozen handoff",
            rootId=root_id,
            actualHierarchyFingerprint=stored["hierarchyFingerprint"],
            actualGraphFingerprint=stored["graphFingerprint"],
        )
    actual_workspace = workspace_root or root
    if stored["status"] == "FROZEN":
        existing_run = repository.run(root_id)
        if existing_run["executionMode"] != "manual":
            fail(
                "SCHEDULER_MANUAL_HANDOFF_START_CONFLICT",
                "This Delivery already has a non-manual Graph run",
                rootId=root_id,
                executionMode=existing_run["executionMode"],
            )
        serial_gate = _frozen_serial_workspace_gate(
            repository,
            stored=stored,
            workspace_root=actual_workspace,
        )
        if serial_gate["state"] != "ACQUIRED":
            return _manual_workspace_waiting_result(
                stored=stored,
                started_by=started_by.strip(),
                serial_gate=serial_gate,
                already_applied=True,
            )
        _verify_frozen_delivery_workspace(stored, actual_workspace)
        terminal = existing_run["status"] in _SERIAL_TERMINAL_STATUSES
        return {
            **existing_run,
            "graphRunCreated": True,
            "manualStartAlreadyApplied": True,
            "nextAction": (
                "GRAPH_RUN_ALREADY_TERMINAL"
                if terminal
                else "READ_GRAPH_FRONTIER"
            ),
        }
    if stored["status"] not in {"HANDOFF_READY", "PREPARED"}:
        fail(
            "SCHEDULER_MANUAL_HANDOFF_NOT_READY",
            "Only a HANDOFF_READY snapshot or its interrupted PREPARED "
            "adoption can start manual TASK execution",
            rootId=root_id,
            status=stored["status"],
        )
    history = repository.revision_history(root_id)
    current = next(
        (
            item
            for item in history["revisions"]
            if item["revision"] == history["currentRevision"]
        ),
        None,
    )
    if (
        current is None
        or current["status"] not in {"HANDOFF_READY", "PREPARED"}
        or not isinstance(current["confirmedBy"], str)
        or not current["confirmedBy"].strip()
    ):
        fail(
            "SCHEDULER_MANUAL_HANDOFF_STATE_INVALID",
            "The frozen manual revision is missing its confirmation record",
            rootId=root_id,
        )
    serial_gate = _manual_serial_workspace_gate(
        repository,
        stored=stored,
        workspace_root=actual_workspace,
    )
    if serial_gate["state"] != "ACQUIRED":
        return _manual_workspace_waiting_result(
            stored=stored,
            started_by=started_by.strip(),
            serial_gate=serial_gate,
            already_applied=False,
        )
    manual_preparation = _manual_serial_workspace_preparation(
        control_root=root,
        workspace_root=actual_workspace,
        hierarchy=stored["hierarchy"],
    )
    if manual_preparation is not None:
        return {
            "rootId": root_id,
            "deliveryRevision": stored["deliveryRevision"],
            "status": stored["status"],
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
            "graphFingerprint": stored["graphFingerprint"],
            "selection": "MANUAL",
            "graphRunCreated": False,
            "manualStartState": "WORKSPACE_PREPARATION_REQUIRED",
            "manualStartAlreadyApplied": False,
            "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceTurn": serial_gate["workspaceTurn"],
            "workspacePreparation": manual_preparation,
            "nextAction": manual_preparation["nextAction"],
        }
    if stored["status"] == "HANDOFF_READY":
        blocked = _manual_baseline_reconfirmation(
            stored=stored,
            repository=repository,
            workspace_root=actual_workspace,
            host_adapter_id=host_adapter_id,
        )
        if blocked is not None:
            return blocked
        graph_runtime_refresh = _refresh_manual_handoff_runtime(
            root=root,
            repository=repository,
            root_id=root_id,
            expected_hierarchy_fingerprint=(
                expected_hierarchy_fingerprint
            ),
            expected_graph_fingerprint=expected_graph_fingerprint,
        )
        if graph_runtime_refresh["refreshed"]:
            expected_graph_fingerprint = graph_runtime_refresh[
                "graphFingerprint"
            ]
            stored = repository.hierarchy(root_id)
        prepared = prepare_hierarchy(
            root=root,
            hierarchy=stored["hierarchy"],
            workspace_root=actual_workspace,
            explicit_dogfood=explicit_dogfood,
            now=now,
        )
        if prepared["graphFingerprint"] != expected_graph_fingerprint:
            fail(
                "SCHEDULER_MANUAL_HANDOFF_STALE",
                "The prepared Graph no longer matches the frozen handoff",
                rootId=root_id,
            )
    else:
        delivery = stored["hierarchy"]["delivery"]
        verified_projects = verify_delivery_project_scopes(
            actual_workspace,
            delivery,
            preparing=False,
        )
        if delivery.get("projectScopes") is None:
            verify_delivery_git_binding(
                actual_workspace,
                delivery.get("gitBinding"),
                preparing=False,
            )
        elif not verified_projects:
            fail(
                "SCHEDULER_PROJECT_SCOPE_INVALID",
                "The interrupted manual adoption has no verified project",
                rootId=root_id,
            )
    prepared_stored = repository.hierarchy(root_id)
    serial_gate = _manual_serial_workspace_gate(
        repository,
        stored=prepared_stored,
        workspace_root=actual_workspace,
    )
    if serial_gate["state"] != "ACQUIRED":
        return _manual_workspace_waiting_result(
            stored=prepared_stored,
            started_by=started_by.strip(),
            serial_gate=serial_gate,
            already_applied=False,
        )
    turn_start = _capture_workspace_turn_start(
        _automatic_workspace_requests(
            control_root=root,
            workspace_root=actual_workspace,
            hierarchy=stored["hierarchy"],
        ),
        actual_workspace,
    )
    try:
        result = repository.freeze_manual_handoff(
            root_id,
            expected_delivery_revision=current["revision"],
            expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
            authorized_project_ids=current["authorizedProjectIds"],
            confirmed_by=current["confirmedBy"].strip(),
            started_by=started_by.strip(),
            workspace_turn_start=turn_start,
        )
    except GatedLoopError as error:
        if error.code != "SCHEDULER_WORKSPACE_TURN_NOT_OWNED":
            raise
        return _manual_workspace_waiting_result(
            stored=repository.hierarchy(root_id),
            started_by=started_by.strip(),
            serial_gate={
                "state": "WAITING_FOR_WORKSPACE_TURN",
                "workspaceTurn": _workspace_turn_waiting(error),
            },
            already_applied=False,
        )
    return {
        **result,
        "startedBy": started_by.strip(),
        "graphRunCreated": True,
        "manualStartAlreadyApplied": False,
        **(
            {"graphRuntimeRefresh": graph_runtime_refresh}
            if graph_runtime_refresh is not None
            and graph_runtime_refresh["refreshed"]
            else {}
        ),
        "nextAction": "READ_GRAPH_FRONTIER",
    }

def resume_execution_mode(
    *,
    root: str,
    root_id: str,
    expected_hierarchy_fingerprint: str,
    expected_graph_fingerprint: str,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    host_adapter_id: str | None = None,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Continue one recorded AUTOMATIC choice in the ready workspace."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    if (
        stored["hierarchyFingerprint"]
        != expected_hierarchy_fingerprint
        or stored["graphFingerprint"] != expected_graph_fingerprint
    ):
        fail(
            "SCHEDULER_EXECUTION_CHOICE_STALE",
            "The recorded execution choice does not match the generated "
            "baseline",
            rootId=root_id,
            actualHierarchyFingerprint=stored["hierarchyFingerprint"],
            actualGraphFingerprint=stored["graphFingerprint"],
        )
    actual_workspace = workspace_root or root
    if stored["status"] == "FROZEN":
        return _frozen_automatic_result(
            repository,
            stored=stored,
            workspace_root=actual_workspace,
        )
    selection = repository.execution_selection(root_id)
    if selection is None:
        fail(
            "SCHEDULER_EXECUTION_SELECTION_MISSING",
            "Automatic execution cannot resume without the recorded human "
            "selection",
            rootId=root_id,
        )
    serial_gate = _resolve_serial_workspace_gate(
        repository,
        actual_workspace,
        root_id,
        _serial_turn_for_recorded_selection(
            repository,
            root_id=root_id,
            stored=stored,
            selection=selection,
            workspace_root=actual_workspace,
        ),
    )
    if serial_gate["state"] != "ACQUIRED":
        return {
            "rootId": root_id,
            "status": "QUEUED",
            "deliveryStatus": stored["status"],
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
            "graphFingerprint": stored["graphFingerprint"],
            "selection": "AUTOMATIC",
            "selectionRecorded": True,
            "selectionAlreadyApplied": True,
            "confirmationRequired": False,
            "automaticDispatchRequested": False,
            "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceTurn": serial_gate["workspaceTurn"],
            "deliveryQueue": _delivery_queue_marker(
                serial_gate,
                root_id,
            ),
            "selectionContinuation": {
                "tool": "resume_execution_mode",
                "confirmationRequired": False,
                "selectionPreserved": True,
            },
            "nextAction": "WAIT_FOR_AUTOMATIC_QUEUE_TURN",
        }
    preparation = _automatic_serial_workspace_preparation(
        control_root=root,
        workspace_root=actual_workspace,
        hierarchy=stored["hierarchy"],
    )
    if preparation is not None:
        return {
            "rootId": root_id,
            "status": stored["status"],
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
            "graphFingerprint": stored["graphFingerprint"],
            "selection": "AUTOMATIC",
            "selectionRecorded": True,
            "confirmationRequired": False,
            "automaticDispatchRequested": False,
            "workspacePreparation": preparation,
            "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceTurn": serial_gate["workspaceTurn"],
            "selectionContinuation": {
                "tool": "resume_execution_mode",
                "confirmationRequired": False,
                "selectionPreserved": True,
            },
            "nextAction": preparation["nextAction"],
        }
    if stored["status"] == "CHOICE_READY":
        prepared = prepare_hierarchy(
            root=root,
            hierarchy=stored["hierarchy"],
            workspace_root=actual_workspace,
            explicit_dogfood=explicit_dogfood,
            now=now,
        )
    elif stored["status"] == "PREPARED":
        repository.assert_delivery_workspace(root_id, actual_workspace)
        prepared = {
            "rootId": root_id,
            "deliveryRevision": stored["deliveryRevision"],
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
        }
        verified_projects = verify_delivery_project_scopes(
            actual_workspace,
            stored["hierarchy"]["delivery"],
            preparing=False,
        )
        if stored["hierarchy"]["delivery"].get("projectScopes") is not None:
            prepared["verifiedProjectScopes"] = verified_projects
    else:
        fail(
            "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
            "The Delivery cannot resume the recorded automatic choice",
            rootId=root_id,
            status=stored["status"],
        )
    turn_start = _capture_workspace_turn_start(
        _automatic_workspace_requests(
            control_root=root,
            workspace_root=actual_workspace,
            hierarchy=stored["hierarchy"],
        ),
        actual_workspace,
    )
    try:
        frozen = freeze_hierarchy(
            root=root,
            root_id=root_id,
            expected_delivery_revision=prepared["deliveryRevision"],
            expected_hierarchy_fingerprint=prepared[
                "hierarchyFingerprint"
            ],
            authorized_project_ids=selection["authorizedProjectIds"],
            confirmed=True,
            confirmed_by=selection["confirmedBy"],
            workspace_root=actual_workspace,
            workspace_turn_start=turn_start,
            explicit_dogfood=explicit_dogfood,
            now=now,
        )
    except GatedLoopError as error:
        if error.code != "SCHEDULER_WORKSPACE_TURN_NOT_OWNED":
            raise
        workspace_turn = _workspace_turn_waiting(error)
        serial_gate = {
            "state": "WAITING_FOR_WORKSPACE_TURN",
            "workspaceTurn": workspace_turn,
        }
        return {
            "rootId": root_id,
            "status": "QUEUED",
            "deliveryStatus": "PREPARED",
            "hierarchyFingerprint": prepared[
                "hierarchyFingerprint"
            ],
            "graphFingerprint": expected_graph_fingerprint,
            "selection": "AUTOMATIC",
            "selectionRecorded": True,
            "selectionAlreadyApplied": True,
            "confirmationRequired": False,
            "automaticDispatchRequested": False,
            "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceTurn": workspace_turn,
            "deliveryQueue": _delivery_queue_marker(
                serial_gate,
                root_id,
            ),
            "selectionContinuation": {
                "tool": "resume_execution_mode",
                "confirmationRequired": False,
                "selectionPreserved": True,
            },
            "nextAction": "WAIT_FOR_AUTOMATIC_QUEUE_TURN",
        }
    return {
        **frozen,
        "selection": "AUTOMATIC",
        "selectionAlreadyApplied": False,
        "confirmationRequired": False,
        "automaticDispatchRequested": True,
        "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
        **(
            {"verifiedProjectScopes": prepared["verifiedProjectScopes"]}
            if "verifiedProjectScopes" in prepared
            else {}
        ),
        "nextAction": "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH",
    }

def select_execution_mode(
    *,
    root: str,
    root_id: str,
    selection: str,
    expected_hierarchy_fingerprint: str,
    expected_graph_fingerprint: str,
    authorized_project_ids: list[str] | None,
    confirmed_by: str,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    host_adapter_id: str | None = None,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Apply the controller-owned automatic or manual execution choice."""

    if selection not in {"AUTOMATIC", "MANUAL"}:
        fail(
            "SCHEDULER_EXECUTION_CHOICE_INVALID",
            "selection must be AUTOMATIC or MANUAL",
            selection=selection,
        )
    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "confirmed_by must identify the confirming human",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    if (
        stored["hierarchyFingerprint"]
        != expected_hierarchy_fingerprint
        or stored["graphFingerprint"] != expected_graph_fingerprint
    ):
        fail(
            "SCHEDULER_EXECUTION_CHOICE_STALE",
            "The selected execution choice does not match the generated "
            "baseline",
            rootId=root_id,
            actualHierarchyFingerprint=stored["hierarchyFingerprint"],
            actualGraphFingerprint=stored["graphFingerprint"],
        )
    hierarchy = stored["hierarchy"]
    if selection == "MANUAL":
        existing_selection = repository.execution_selection(root_id)
        if (
            existing_selection is not None
            and existing_selection["selection"] != "MANUAL"
        ):
            fail(
                "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
                "Automatic execution has already been selected; continue "
                "it without another mode confirmation",
                rootId=root_id,
                selected=selection,
            )
        if stored["status"] not in {
            "CHOICE_READY",
            "HANDOFF_READY",
        }:
            fail(
                "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
                "Automatic execution has already started for this choice",
                rootId=root_id,
                status=stored["status"],
                selected=selection,
            )
        handoff = create_manual_handoff(
            root=root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                expected_hierarchy_fingerprint
            ),
            expected_graph_fingerprint=expected_graph_fingerprint,
            authorized_project_ids=authorized_project_ids,
            confirmed=True,
            confirmed_by=confirmed_by,
            explicit_dogfood=explicit_dogfood,
            now=now,
        )
        return {
            **handoff,
            "selection": "MANUAL",
            "selectionAlreadyApplied": (
                stored["status"] == "HANDOFF_READY"
            ),
        }

    selected_strategy = "CURRENT_WORKSPACE_SERIAL"
    if stored["status"] == "FROZEN":
        return _frozen_automatic_result(
            repository,
            stored=stored,
            workspace_root=workspace_root or root,
        )
    if stored["status"] == "HANDOFF_READY":
        fail(
            "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
            "Manual development has already been selected for this choice",
            rootId=root_id,
            status=stored["status"],
            selected=selection,
        )
    if stored["status"] not in {"CHOICE_READY", "PREPARED"}:
        fail(
            "SCHEDULER_EXECUTION_CHOICE_CONFLICT",
            "The Delivery is not waiting for an execution choice",
            rootId=root_id,
            status=stored["status"],
            selected=selection,
        )
    _assert_exact_project_authorization(
        hierarchy,
        authorized_project_ids,
    )
    actual_workspace = workspace_root or root
    workspace_requests = _automatic_workspace_requests(
        control_root=root,
        workspace_root=actual_workspace,
        hierarchy=hierarchy,
    )
    recorded = repository.record_automatic_selection(
        root_id,
        expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
        expected_graph_fingerprint=expected_graph_fingerprint,
        authorized_project_ids=authorized_project_ids or [],
        confirmed_by=confirmed_by.strip(),
        workspace_key=SchedulerRepository.workspace_key(actual_workspace),
    )
    serial_gate = _resolve_serial_workspace_gate(
        repository,
        actual_workspace,
        root_id,
        recorded["workspaceTurn"],
    )
    if serial_gate["state"] != "ACQUIRED":
        return {
            "rootId": root_id,
            "status": "QUEUED",
            "deliveryStatus": stored["status"],
            "selection": "AUTOMATIC",
            "workspaceStrategy": selected_strategy,
            "selectionRecorded": not recorded["selectionAlreadyApplied"],
            "selectionAlreadyApplied": recorded[
                "selectionAlreadyApplied"
            ],
            "automaticDispatchRequested": False,
            "hierarchyFingerprint": stored["hierarchyFingerprint"],
            "graphFingerprint": stored["graphFingerprint"],
            "workspaceTurn": serial_gate["workspaceTurn"],
            "deliveryQueue": _delivery_queue_marker(
                serial_gate,
                root_id,
            ),
            "nextAction": "WAIT_FOR_AUTOMATIC_QUEUE_TURN",
        }

    pending_requests = [
        request
        for request in workspace_requests
        if not _current_workspace_satisfies_request(
            request,
            actual_workspace,
        )
    ]
    if pending_requests:
        preparation = _current_workspace_serial_preparation(
            pending_requests,
            actual_workspace,
        )
        return {
            "rootId": root_id,
            "status": stored["status"],
            "selection": "AUTOMATIC",
            "workspaceStrategy": selected_strategy,
            "selectionRecorded": not recorded["selectionAlreadyApplied"],
            "selectionAlreadyApplied": recorded[
                "selectionAlreadyApplied"
            ],
            "automaticDispatchRequested": False,
            "hierarchyFingerprint": stored[
                "hierarchyFingerprint"
            ],
            "graphFingerprint": stored["graphFingerprint"],
            "workspaceTurn": serial_gate["workspaceTurn"],
            "workspacePreparation": preparation,
            "projectWorkspacePreparations": preparation[
                "projectPreparations"
            ],
            "selectionContinuation": {
                "tool": "resume_execution_mode",
                "selectionPreserved": True,
                "confirmationRequired": False,
            },
            "nextAction": preparation["nextAction"],
        }
    resumed = resume_execution_mode(
        root=root,
        root_id=root_id,
        expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
        expected_graph_fingerprint=expected_graph_fingerprint,
        workspace_root=workspace_root or root,
        explicit_dogfood=explicit_dogfood,
        host_adapter_id=host_adapter_id,
        now=now,
    )
    return {
        **resumed,
        "workspaceStrategy": selected_strategy,
        "selectionRecorded": not recorded["selectionAlreadyApplied"],
        "selectionAlreadyApplied": recorded["selectionAlreadyApplied"],
    }
