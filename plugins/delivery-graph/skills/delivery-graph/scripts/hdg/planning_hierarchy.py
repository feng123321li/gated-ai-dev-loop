from __future__ import annotations

from .planning_common import (
    Any,
    GOVERNANCE_DIRECTORY,
    Path,
    SchedulerRepository,
    atomic_write,
    compile_delivery_graph,
    deepcopy,
    execution_choice_contract,
    fail,
    git_repository_identity,
    graph_fingerprint,
    graph_summary,
    hierarchy_fingerprint,
    manual_receiver_prompt,
    render_manual_handoff,
    resolve_branch_binding,
    safe_path,
    validate_hierarchy_definition,
    verify_delivery_git_binding,
    verify_delivery_project_scopes,
)
from .planning_workspace import (
    _SERIAL_TERMINAL_STATUSES,
    _automatic_serial_workspace_preparation,
    _automatic_workspace_requests,
    _human_artifacts,
    _preview_values,
)
from .planning_gates import (
    _assert_project_baselines_complete,
    _attach_pending_interaction,
    _baseline_discovery,
    _capture_workspace_turn_start,
    _continue_workspace_turn_start,
    _delivery_queue_marker,
    _inject_remembered_baseline,
    _pending_interaction,
    _resolve_serial_workspace_gate,
)


def preview_hierarchy(
    *,
    root: str,
    hierarchy: object,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    host_adapter_id: str | None = None,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Validate a plan and stage artifacts before mode selection."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    normalized, graph, hierarchy_value, graph_value = _preview_values(
        hierarchy
    )
    root_id = normalized["delivery"]["id"]
    if _inject_remembered_baseline(normalized, repository, root_id):
        hierarchy_value = hierarchy_fingerprint(normalized)
        graph = compile_delivery_graph(
            normalized, hierarchy_fingerprint=hierarchy_value
        )
        graph_value = graph_fingerprint(graph)
    _assert_project_baselines_complete(
        normalized,
        workspace_root or root,
    )
    staged = repository.record_choice_ready(
        normalized,
        graph,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
    )
    artifacts_ready = staged["artifactsReady"]
    recorded_selection = repository.execution_selection(root_id)
    choice_ready = (
        artifacts_ready
        and staged["status"] == "CHOICE_READY"
        and recorded_selection is None
    )
    next_actions = {
        "HANDOFF_READY": (
            "OPEN_FROZEN_BUNDLE_AND_START_MANUAL_HANDOFF_IN_RECEIVING_CLI"
        ),
        "PREPARED": "FREEZE_PREPARED_HIERARCHY",
        "FROZEN": "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH",
        "ABANDONED": "DELIVERY_ABANDONED_NO_FURTHER_ACTION",
    }
    result = {
        "rootId": root_id,
        "status": staged["status"],
        "hierarchyFingerprint": hierarchy_value,
        "graphFingerprint": graph_value,
        "graphSummary": graph_summary(graph),
        "requiredProjectAuthorizations": normalized["delivery"].get(
            "projectScopes",
            [],
        ),
        "controlStateCreated": staged["controlStateCreated"],
        "artifactsReady": artifacts_ready,
        "nextAction": (
            "RESUME_RECORDED_AUTOMATIC_SELECTION_IN_READY_WORKSPACE"
            if recorded_selection is not None
            else next_actions.get(
                staged["status"],
                "REGENERATE_ARTIFACTS_BEFORE_EXECUTION_CHOICE",
            )
        ),
    }
    if artifacts_ready:
        result["humanArtifacts"] = _human_artifacts(normalized)
    if choice_ready:
        interaction = _pending_interaction(
            normalized,
            repository,
            workspace_root or root,
            host_adapter_id,
            expected_hierarchy_fingerprint=hierarchy_value,
            expected_graph_fingerprint=graph_value,
            expected_delivery_revision=staged["deliveryRevision"],
        )
        _attach_pending_interaction(result, interaction)
    if recorded_selection is not None:
        result["executionSelection"] = recorded_selection
    return result

def confirm_development_baseline(
    *,
    root: str,
    root_id: str,
    selection: str,
    expected_hierarchy_fingerprint: str,
    confirmed_by: str,
    branch_name: str | None = None,
    expected_graph_fingerprint: str | None = None,
    expected_delivery_revision: int | None = None,
    baseline_context_fingerprint: str | None = None,
    confirmed_dirty_state_fingerprint: str | None = None,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    host_adapter_id: str | None = None,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Apply one DEVELOPMENT_BASELINE option and re-stage the hierarchy.

    Persists the per-Delivery preference, computes the Git binding read-only,
    re-stages the hierarchy with the binding frozen in (via the idempotent
    ``record_choice_ready`` path), and returns the updated fingerprint plus the
    ``executionChoice``. The Controller performs no Git writes: a
    ``NEW_FROM_MAINLINE`` pins ``baseCommit`` to the current mainline HEAD.
    ``NEW_FROM_CURRENT_BRANCH`` pins it to the clean current feature HEAD and
    makes that parent feature both baseRef and integrationTarget. The host
    creates either branch during workspace preparation.
    """

    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "confirmed_by must identify the confirming human",
        )
    if not isinstance(selection, str) or not selection.strip():
        fail(
            "SCHEDULER_BASELINE_CHOICE_INVALID",
            "selection must be a local branch_ref, NEW_FROM_MAINLINE, or "
            "NEW_FROM_CURRENT_BRANCH",
            selection=selection,
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    if stored["hierarchyFingerprint"] != expected_hierarchy_fingerprint:
        fail(
            "SCHEDULER_BASELINE_CHOICE_STALE",
            "The baseline choice does not match the generated hierarchy",
            rootId=root_id,
            actualHierarchyFingerprint=stored["hierarchyFingerprint"],
        )
    if stored["status"] not in {"CHOICE_READY", "HANDOFF_READY"}:
        fail(
            "SCHEDULER_BASELINE_CHOICE_CONFLICT",
            "A development baseline can only be confirmed while the Delivery "
            "waits for an execution choice or a blocked manual start",
            rootId=root_id,
            status=stored["status"],
        )
    manual_reconfirmation = stored["status"] == "HANDOFF_READY"
    if not manual_reconfirmation and repository.execution_selection(root_id) is not None:
        fail(
            "SCHEDULER_BASELINE_CHOICE_CONFLICT",
            "An execution mode has already been selected for this Delivery",
            rootId=root_id,
        )
    if manual_reconfirmation:
        if (
            expected_delivery_revision != stored["deliveryRevision"]
            or expected_graph_fingerprint != stored["graphFingerprint"]
            or not isinstance(baseline_context_fingerprint, str)
            or not baseline_context_fingerprint
        ):
            fail(
                "SCHEDULER_MANUAL_BASELINE_CONTEXT_STALE",
                "Manual baseline reconfirmation must use the exact revision, "
                "dual fingerprints, and Git context that were presented",
                rootId=root_id,
                actualDeliveryRevision=stored["deliveryRevision"],
                actualGraphFingerprint=stored["graphFingerprint"],
            )
    workspace = workspace_root or root
    context = _baseline_discovery(
        stored["hierarchy"],
        repository,
        workspace,
        host_adapter_id,
        expected_hierarchy_fingerprint=stored["hierarchyFingerprint"],
        expected_graph_fingerprint=stored["graphFingerprint"],
        expected_delivery_revision=stored["deliveryRevision"],
        interaction_context=(
            "MANUAL_HANDOFF_START"
            if manual_reconfirmation
            else "INITIAL_DELIVERY"
        ),
    )
    if context is None:
        fail(
            "SCHEDULER_GIT_CHECKOUT_REQUIRED",
            "A development baseline can only be confirmed in a Git workspace",
            rootId=root_id,
        )
    if (
        baseline_context_fingerprint is not None
        and baseline_context_fingerprint
        != context["baselineContextFingerprint"]
    ):
        fail(
            (
                "SCHEDULER_MANUAL_BASELINE_CONTEXT_STALE"
                if manual_reconfirmation
                else "SCHEDULER_BASELINE_CHOICE_STALE"
            ),
            "The Git baseline context changed after it was presented",
            rootId=root_id,
            expectedBaselineContextFingerprint=(
                baseline_context_fingerprint
            ),
            actualBaselineContextFingerprint=context[
                "baselineContextFingerprint"
            ],
        )
    working_tree = context["discovery"].get("workingTree", {})
    current_branch = context["discovery"].get("gitWorkspace", {}).get(
        "branchRef"
    )
    dirty_current_branch_adoption = (
        manual_reconfirmation
        or selection.strip() == current_branch
        or selection == "NEW_FROM_CURRENT_BRANCH"
    )
    if (
        not working_tree.get("clean", False)
        and dirty_current_branch_adoption
    ):
        actual_dirty = working_tree.get("stateFingerprint")
        if confirmed_dirty_state_fingerprint != actual_dirty:
            fail(
                "SCHEDULER_GIT_DIRTY_CONFIRMATION_REQUIRED",
                "Adopting the current dirty branch requires all current "
                "workspace changes to be explicitly attributed to this "
                "Delivery using the presented state fingerprint",
                dirtyStateFingerprint=actual_dirty,
            )
    if selection in {"NEW_FROM_MAINLINE", "NEW_FROM_CURRENT_BRANCH"}:
        stacked_base = context.get("stackedBase")
        if (
            not isinstance(branch_name, str)
            or not branch_name.strip()
            or branch_name.strip() in {"main", "master"}
            or (
                selection == "NEW_FROM_CURRENT_BRANCH"
                and (
                    stacked_base is None
                    or branch_name.strip() == stacked_base["branchRef"]
                )
            )
        ):
            fail(
                "SCHEDULER_BASELINE_CHOICE_INVALID",
                f"{selection} requires a distinct new Delivery branch name",
                branchName=branch_name,
            )
        chosen_branch = branch_name.strip()
        available = {
            item["branchRef"] for item in context["candidateBranches"]
        }
        if (
            selection == "NEW_FROM_CURRENT_BRANCH"
            and chosen_branch in available
        ):
            fail(
                "SCHEDULER_BASELINE_CHOICE_INVALID",
                "NEW_FROM_CURRENT_BRANCH requires a branch name that does "
                "not already exist",
                branchName=chosen_branch,
            )
        source = selection
        base_ref = (
            stacked_base["branchRef"]
            if selection == "NEW_FROM_CURRENT_BRANCH"
            else None
        )
    else:
        chosen_branch = selection.strip()
        available = {
            item["branchRef"] for item in context["candidateBranches"]
        }
        if chosen_branch not in available:
            fail(
                "SCHEDULER_BASELINE_CHOICE_INVALID",
                "The selected branch is not an available local feature branch",
                selection=chosen_branch,
            )
        source = "LOCAL_BRANCH"
        base_ref = None
    binding = resolve_branch_binding(
        workspace,
        branch_ref=chosen_branch,
        base_ref=base_ref,
    )
    terminal_statuses = {
        "ARCHIVED",
        "COMPLETED",
        "CANCELLED",
        "SUPERSEDED",
    }
    conflicting = [
        item
        for item in repository.git_branch_usage(
            binding["branchRef"],
            repository_key=git_repository_identity(str(workspace)),
        )
        if item["rootId"] != root_id
        and item["status"] not in terminal_statuses
    ]
    if conflicting:
        fail(
            "SCHEDULER_BASELINE_BRANCH_IN_USE",
            "The selected branch is already bound to another active Delivery",
            branchRef=binding["branchRef"],
            conflictingDeliveries=conflicting,
        )
    if manual_reconfirmation:
        hierarchy = deepcopy(stored["hierarchy"])
        previous_binding = hierarchy["delivery"].get("gitBinding")
        if binding == previous_binding:
            preference = repository.record_development_preference(
                root_id,
                binding=binding,
                source=source,
                chosen_by=confirmed_by.strip(),
            )
            return {
                "rootId": root_id,
                "status": "HANDOFF_READY",
                "deliveryRevision": stored["deliveryRevision"],
                "hierarchyFingerprint": stored["hierarchyFingerprint"],
                "graphFingerprint": stored["graphFingerprint"],
                "developmentBaselineConfirmed": preference,
                "baselineConfirmationAlreadyApplied": False,
                "graphRunCreated": False,
                "nextAction": (
                    "RESTORE_CONFIRMED_BRANCH_THEN_RETRY_MANUAL_START"
                ),
            }
        hierarchy["delivery"]["gitBinding"] = binding
        normalized = validate_hierarchy_definition(hierarchy)
        hierarchy_value = hierarchy_fingerprint(normalized)
        graph = compile_delivery_graph(
            normalized,
            hierarchy_fingerprint=hierarchy_value,
        )
        graph_value = graph_fingerprint(graph)
        history = repository.revision_history(root_id)
        current = next(
            item
            for item in history["revisions"]
            if item["revision"] == stored["deliveryRevision"]
        )
        handoff = create_manual_handoff(
            root=root,
            hierarchy=normalized,
            expected_hierarchy_fingerprint=hierarchy_value,
            expected_graph_fingerprint=graph_value,
            authorized_project_ids=current["authorizedProjectIds"],
            confirmed=True,
            confirmed_by=confirmed_by.strip(),
            expected_current_revision=stored["deliveryRevision"],
            continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
            revision_reason=(
                "手动交接启动前重新确认实际开发基线。"
            ),
            explicit_dogfood=explicit_dogfood,
            now=now,
        )
        preference = repository.record_development_preference(
            root_id,
            binding=binding,
            source=source,
            chosen_by=confirmed_by.strip(),
        )
        return {
            **handoff,
            "developmentBaselineConfirmed": preference,
            "baselineConfirmationAlreadyApplied": False,
            "nextAction": "START_MANUAL_HANDOFF_WITH_UPDATED_FINGERPRINTS",
        }

    hierarchy = deepcopy(stored["hierarchy"])
    hierarchy["delivery"]["gitBinding"] = binding
    normalized = validate_hierarchy_definition(hierarchy)
    hierarchy_value = hierarchy_fingerprint(normalized)
    graph = compile_delivery_graph(
        normalized, hierarchy_fingerprint=hierarchy_value
    )
    graph_value = graph_fingerprint(graph)
    repository.record_choice_ready(
        normalized,
        graph,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
    )
    preference = repository.record_development_preference(
        root_id,
        binding=binding,
        source=source,
        chosen_by=confirmed_by.strip(),
    )
    result = {
        "rootId": root_id,
        "status": "CHOICE_READY",
        "hierarchyFingerprint": hierarchy_value,
        "graphFingerprint": graph_value,
        "developmentBaselineConfirmed": preference,
    }
    _attach_pending_interaction(
        result,
        execution_choice_contract(host_adapter_id, git_binding=binding),
    )
    return result

def _assert_exact_project_authorization(
    hierarchy: dict[str, Any],
    authorized_project_ids: list[str] | None,
) -> None:
    required = sorted(
        item["id"]
        for item in hierarchy["delivery"].get("projectScopes", [])
    )
    if (
        not isinstance(authorized_project_ids, list)
        or any(
            not isinstance(item, str) or not item
            for item in authorized_project_ids
        )
        or len(set(authorized_project_ids))
        != len(authorized_project_ids)
    ):
        fail(
            "SCHEDULER_PROJECT_AUTHORIZATION_REQUIRED",
            "authorized_project_ids must contain unique project IDs",
        )
    supplied = sorted(authorized_project_ids)
    if supplied != required:
        fail(
            "SCHEDULER_PROJECT_AUTHORIZATION_REQUIRED",
            "Manual handoff requires exact authorization of every project",
            requiredProjectIds=required,
            suppliedProjectIds=supplied,
            missingProjectIds=sorted(set(required) - set(supplied)),
            unexpectedProjectIds=sorted(set(supplied) - set(required)),
        )

def create_manual_handoff(
    *,
    root: str,
    hierarchy: object,
    expected_hierarchy_fingerprint: str,
    expected_graph_fingerprint: str,
    authorized_project_ids: list[str] | None,
    confirmed: bool,
    confirmed_by: str,
    expected_current_revision: int | None = None,
    continuity_basis: str | None = None,
    revision_reason: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Create a portable handoff bundle without preparing a Graph."""

    if confirmed is not True:
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "Creating a manual handoff requires explicit user confirmation",
        )
    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "confirmed_by must identify the confirming human",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    normalized, graph, hierarchy_value, graph_value = _preview_values(
        hierarchy
    )
    if hierarchy_value != expected_hierarchy_fingerprint:
        fail(
            "SCHEDULER_HANDOFF_PREVIEW_CONFLICT",
            "The confirmed hierarchy differs from the preview",
            expectedHierarchyFingerprint=(
                expected_hierarchy_fingerprint
            ),
            actualHierarchyFingerprint=hierarchy_value,
        )
    if graph_value != expected_graph_fingerprint:
        fail(
            "SCHEDULER_HANDOFF_PREVIEW_CONFLICT",
            "The confirmed Graph differs from the preview",
            expectedGraphFingerprint=expected_graph_fingerprint,
            actualGraphFingerprint=graph_value,
        )
    _assert_exact_project_authorization(
        normalized,
        authorized_project_ids,
    )
    root_id = normalized["delivery"]["id"]
    relative_path = (
        f"{GOVERNANCE_DIRECTORY}/{root_id}/"
        f"handoff-{hierarchy_value[:12]}.md"
    )
    receiver_prompt = manual_receiver_prompt(
        relative_path,
        normalized["root"]["skillHints"],
    )
    registration = repository.record_manual_handoff(
        normalized,
        graph,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
        authorized_project_ids=list(authorized_project_ids or []),
        expected_current_revision=expected_current_revision,
        continuity_basis=continuity_basis,
        revision_reason=revision_reason,
        confirmed_by=confirmed_by.strip(),
    )
    created_at = registration["recordedAt"]
    content = render_manual_handoff(
        normalized,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
        confirmed_by=confirmed_by.strip(),
        created_at=created_at,
        receiver_prompt=receiver_prompt,
    )
    atomic_write(safe_path(root, relative_path), content)
    return {
        "rootId": root_id,
        "status": "HANDOFF_READY",
        "deliveryRevision": registration["deliveryRevision"],
        "previousRevision": registration["previousRevision"],
        "requirementSnapshotStatus": "FROZEN",
        "hierarchyFingerprint": hierarchy_value,
        "graphFingerprint": graph_value,
        "confirmedBy": confirmed_by.strip(),
        "createdAt": created_at,
        "manualHandoff": {
            "path": relative_path,
            "format": "MARKDOWN",
            "selfContained": True,
            "receiverPrompt": receiver_prompt,
        },
        "humanArtifacts": _human_artifacts(
            normalized,
        ),
        "controlStateCreated": True,
        "graphRunCreated": False,
        "workspaceCreated": False,
        "nextAction": (
            "OPEN_FROZEN_BUNDLE_AND_START_MANUAL_HANDOFF_IN_RECEIVING_CLI"
        ),
    }

def prepare_hierarchy(
    *,
    root: str,
    hierarchy: object,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Validate and prepare scheduler metadata for human confirmation."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    normalized = validate_hierarchy_definition(hierarchy)
    _inject_remembered_baseline(
        normalized, repository, normalized["delivery"]["id"]
    )
    project_scopes = normalized["delivery"].get("projectScopes")
    verified_projects = verify_delivery_project_scopes(
        workspace_root or root,
        normalized["delivery"],
        preparing=True,
    )
    git_binding = normalized["delivery"].get("gitBinding")
    git_workspace = (
        verify_delivery_git_binding(
            workspace_root or root,
            git_binding,
            preparing=True,
        )
        if project_scopes is None
        else next(
            (
                item.get("gitWorkspace")
                for item in verified_projects
                if item["workspaceRoot"]
                == str(
                    Path(workspace_root or root)
                    .absolute()
                    .resolve(strict=True)
                )
            ),
            None,
        )
    )
    hierarchy_value = hierarchy_fingerprint(normalized)
    graph = compile_delivery_graph(
        normalized,
        hierarchy_fingerprint=hierarchy_value,
    )
    graph_value = graph_fingerprint(graph)
    prepared = repository.prepare(
        normalized,
        graph,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
        workspace_root=workspace_root or root,
    )
    human_artifacts = _human_artifacts(normalized)
    result = {
        **prepared,
        "graphSummary": graph_summary(graph),
        "humanArtifacts": human_artifacts,
        "nextAction": "FREEZE_HIERARCHY_AFTER_USER_CONFIRMATION",
        "requiredProjectAuthorizations": normalized["delivery"].get(
            "projectScopes",
            [],
        ),
    }
    if git_binding is not None:
        result["gitBinding"] = git_binding
    if git_workspace is not None:
        result["gitWorkspace"] = git_workspace
    if project_scopes is not None:
        result["verifiedProjectScopes"] = verified_projects
    return result

def prepare_delivery_revision(
    *,
    root: str,
    root_id: str,
    expected_current_revision: int,
    hierarchy: object,
    reason: str,
    continuity_basis: str,
    requested_by: str,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Prepare the next immutable revision of one active Delivery."""

    if not isinstance(reason, str) or not reason.strip():
        fail(
            "SCHEDULER_REVISION_REASON_REQUIRED",
            "A Delivery revision requires a concrete reason",
        )
    if not isinstance(requested_by, str) or not requested_by.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "requested_by must identify the requesting human",
        )
    if continuity_basis not in {
        "USER_EXPLICIT_SAME_DELIVERY",
        "ACTIVE_LOOP_REPLAN",
    }:
        fail(
            "SCHEDULER_REVISION_CONTINUITY_REQUIRED",
            "Revision continuity must come from explicit same-Delivery "
            "intent or an active Loop replan",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    normalized = validate_hierarchy_definition(hierarchy)
    if normalized["delivery"]["id"] != root_id:
        fail(
            "SCHEDULER_DELIVERY_IDENTITY_IMMUTABLE",
            "A Delivery revision must retain the original Delivery ID",
            rootId=root_id,
        )
    _inject_remembered_baseline(normalized, repository, root_id)
    verify_delivery_project_scopes(
        workspace_root or root,
        normalized["delivery"],
        preparing=True,
    )
    if normalized["delivery"].get("projectScopes") is None:
        verify_delivery_git_binding(
            workspace_root or root,
            normalized["delivery"].get("gitBinding"),
            preparing=True,
        )
    hierarchy_value = hierarchy_fingerprint(normalized)
    graph = compile_delivery_graph(
        normalized,
        hierarchy_fingerprint=hierarchy_value,
    )
    graph_value = graph_fingerprint(graph)
    prepared = repository.prepare_revision(
        normalized,
        graph,
        root_id=root_id,
        expected_current_revision=expected_current_revision,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
        reason=reason.strip(),
        continuity_basis=continuity_basis,
        requested_by=requested_by.strip(),
        workspace_root=workspace_root or root,
    )
    return {
        **prepared,
        "graphSummary": graph_summary(graph),
        "humanArtifacts": _human_artifacts(normalized),
        "requiredProjectAuthorizations": normalized["delivery"].get(
            "projectScopes",
            [],
        ),
        "nextAction": "FREEZE_HIERARCHY_AFTER_USER_CONFIRMATION",
    }

def delivery_revision_history(
    *,
    root: str,
    root_id: str,
    explicit_dogfood: bool = False,
    **_: Any,
) -> dict[str, Any]:
    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    return repository.revision_history(root_id)

def freeze_hierarchy(
    *,
    root: str,
    root_id: str,
    expected_delivery_revision: int = 1,
    expected_hierarchy_fingerprint: str,
    authorized_project_ids: list[str] | None = None,
    confirmed: bool,
    confirmed_by: str,
    workspace_root: str | None = None,
    workspace_turn_start: dict[str, Any] | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
    _execution_mode: str = "active",
) -> dict[str, Any]:
    """Freeze the graph after explicit human confirmation and start it."""

    if confirmed is not True:
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "Freezing a hierarchy requires explicit user confirmation",
        )
    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "confirmed_by must identify the confirming human",
        )
    if _execution_mode not in {"active", "manual"}:
        fail(
            "SCHEDULER_EXECUTION_MODE_INVALID",
            "Graph execution mode must be active or manual",
            executionMode=_execution_mode,
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    stored = repository.hierarchy(root_id)
    if stored["status"] == "ARCHIVED":
        fail(
            "SCHEDULER_DELIVERY_ARCHIVED",
            "An archived Delivery cannot be frozen again",
            rootId=root_id,
        )
    revision_record = next(
        (
            revision
            for revision in repository.revision_history(root_id)[
                "revisions"
            ]
            if revision["revision"] == expected_delivery_revision
        ),
        None,
    )
    if revision_record is None:
        fail(
            "SCHEDULER_REVISION_MISSING",
            "The requested Delivery revision is missing",
            rootId=root_id,
            deliveryRevision=expected_delivery_revision,
        )
    revision_graph_fingerprint = revision_record["graphFingerprint"]
    actual_workspace = workspace_root or root
    repository.assert_delivery_workspace(root_id, actual_workspace)
    serial_gate = _resolve_serial_workspace_gate(
        repository,
        actual_workspace,
        root_id,
        repository.serial_workspace_turn_state(root_id),
    )
    if serial_gate["state"] != "ACQUIRED":
        if (
            serial_gate["state"] == "RELEASED"
            and stored["status"] == "FROZEN"
            and stored["deliveryRevision"]
            == expected_delivery_revision
        ):
            if (
                revision_record["hierarchyFingerprint"]
                != expected_hierarchy_fingerprint
            ):
                fail(
                    "SCHEDULER_REVISION_CONFLICT",
                    "Hierarchy fingerprint is not current",
                )
            run = repository.run(root_id)
            return {
                **run,
                "confirmedBy": confirmed_by.strip(),
                "graphRunCreated": False,
                "automaticDispatchRequested": False,
                "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
                "workspaceTurn": serial_gate["workspaceTurn"],
                "nextAction": (
                    "GRAPH_RUN_ALREADY_TERMINAL"
                    if run["status"] in _SERIAL_TERMINAL_STATUSES
                    else "READ_GRAPH_FRONTIER"
                ),
            }
        return {
            "rootId": root_id,
            "status": "QUEUED",
            "deliveryStatus": stored["status"],
            "deliveryRevision": expected_delivery_revision,
            "hierarchyFingerprint": expected_hierarchy_fingerprint,
            "graphFingerprint": revision_graph_fingerprint,
            "graphRunCreated": False,
            "automaticDispatchRequested": False,
            "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
            "workspaceTurn": serial_gate["workspaceTurn"],
            "deliveryQueue": _delivery_queue_marker(
                serial_gate,
                root_id,
            ),
            "nextAction": "WAIT_FOR_AUTOMATIC_QUEUE_TURN",
        }
    revision_hierarchy = repository.revision_hierarchy(
        root_id,
        expected_delivery_revision,
    )
    previous_turn_released = (
        repository.workspace_turn_release(root_id) is not None
    )
    if previous_turn_released:
        preparation = _automatic_serial_workspace_preparation(
            control_root=root,
            workspace_root=actual_workspace,
            hierarchy=revision_hierarchy,
        )
        if preparation is not None:
            return {
                "rootId": root_id,
                "status": "PREPARED",
                "deliveryStatus": stored["status"],
                "deliveryRevision": expected_delivery_revision,
                "hierarchyFingerprint": expected_hierarchy_fingerprint,
                "graphFingerprint": revision_graph_fingerprint,
                "graphRunCreated": False,
                "automaticDispatchRequested": False,
                "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
                "workspaceTurn": serial_gate["workspaceTurn"],
                "workspacePreparation": preparation,
                "nextAction": preparation["nextAction"],
            }
    workspace_requests = _automatic_workspace_requests(
        control_root=root,
        workspace_root=actual_workspace,
        hierarchy=revision_hierarchy,
    )
    captured_turn_start = None
    if (
        stored["status"] == "FROZEN"
        and expected_delivery_revision
        == stored["deliveryRevision"] + 1
        and not previous_turn_released
    ):
        captured_turn_start = _continue_workspace_turn_start(
            workspace_requests,
            _automatic_workspace_requests(
                control_root=root,
                workspace_root=actual_workspace,
                hierarchy=stored["hierarchy"],
            ),
            actual_workspace,
            repository.workspace_turn_start(root_id),
        )
    if captured_turn_start is None:
        captured_turn_start = _capture_workspace_turn_start(
            workspace_requests,
            actual_workspace,
        )
    if (
        workspace_turn_start is not None
        and workspace_turn_start != captured_turn_start
    ):
        fail(
            "SCHEDULER_WORKSPACE_TURN_CHANGED",
            "The current workspace changed between turn capture and freeze",
            rootId=root_id,
        )
    freeze_arguments = {
        "expected_delivery_revision": expected_delivery_revision,
        "expected_hierarchy_fingerprint": (
            expected_hierarchy_fingerprint
        ),
        "authorized_project_ids": authorized_project_ids or [],
        "confirmed_by": confirmed_by.strip(),
        "workspace_turn_start": captured_turn_start,
    }
    if _execution_mode == "manual":
        result = repository.freeze_manual_handoff(
            root_id,
            started_by=confirmed_by.strip(),
            **freeze_arguments,
        )
    else:
        result = repository.freeze(
            root_id,
            **freeze_arguments,
        )
    return {
        **result,
        "confirmedBy": confirmed_by.strip(),
        "nextAction": "READ_GRAPH_FRONTIER",
    }
