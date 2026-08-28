from __future__ import annotations

from .planning_common import (
    Any,
    GatedLoopError,
    SchedulerRepository,
    git_repository_identity,
    inspect_delivery_git_workspace,
    inspect_frozen_git_workspace_provenance,
    verify_delivery_git_binding,
    verify_delivery_project_scopes,
)
from .planning_gates import (
    _attach_pending_interaction,
    _delivery_queue_marker,
    _pending_interaction,
    _resolve_serial_workspace_gate,
    _serial_workspace_release_handshake,
    _serial_turn_for_recorded_selection,
)
from .planning_workspace import (
    _automatic_serial_workspace_preparation,
    _manual_serial_workspace_preparation,
)


def _attach_release_handshake(
    result: dict[str, Any],
    handshake: dict[str, Any],
) -> None:
    lifecycle_next_action = result.get("nextAction")
    result.update(handshake)
    release = handshake.get("workspaceRelease")
    preserve_lifecycle_action = result.get("status") == "COMPLETED" or (
        result.get("status") == "CANCELLED"
        and result.get("canPrepareRevision") is True
        and isinstance(release, dict)
        and release.get("state") == "RELEASED"
    )
    if preserve_lifecycle_action and isinstance(lifecycle_next_action, str):
        result["workspaceNextAction"] = result.get("nextAction")
        result["nextAction"] = lifecycle_next_action


def workspace_status(
    *,
    root: str,
    root_id: str | None = None,
    base_ref: str | None = None,
    confirmed_dirty_state_fingerprint: str | None = None,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    host_adapter_id: str | None = None,
    now: object = None,
) -> dict[str, Any]:
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    result = repository.workspace_status(
        root_id=root_id,
        workspace_root=workspace_root,
    )
    if result["status"] == "DELIVERY_SELECTION_REQUIRED":
        for candidate in result.get("candidateDeliveries", []):
            candidate_root_id = candidate.get("rootId")
            if not isinstance(candidate_root_id, str):
                continue
            selection = repository.execution_selection(candidate_root_id)
            if selection is None:
                continue
            try:
                workspace_turn = repository.serial_workspace_turn_state(
                    candidate_root_id
                )
            except GatedLoopError as error:
                if error.code == "SCHEDULER_DELIVERY_WORKSPACE_MISSING":
                    continue
                raise
            if workspace_turn["state"] != "ACQUIRED":
                serial_gate = {
                    "state": workspace_turn["state"],
                    "workspaceTurn": workspace_turn,
                }
                candidate["deliveryStatus"] = candidate["status"]
                candidate["status"] = "QUEUED"
                candidate["deliveryQueue"] = _delivery_queue_marker(
                    serial_gate,
                    candidate_root_id,
                    selection=selection["selection"],
                )
        return result
    selected_root_id = result.get("rootId")
    if isinstance(selected_root_id, str):
        if result["status"] == "ARCHIVED":
            return result
        stored = repository.hierarchy(selected_root_id)
        if stored["graphCompatibility"]["state"] != "CURRENT":
            result["graphCompatibility"] = stored["graphCompatibility"]
        delivery = stored["hierarchy"]["delivery"]
        git_binding = delivery.get("gitBinding")
        project_scopes = delivery.get("projectScopes")
        selection = repository.execution_selection(selected_root_id)
        if selection is not None:
            result["executionSelection"] = selection
        if stored["status"] == "CHOICE_READY" and selection is None:
            interaction = _pending_interaction(
                stored["hierarchy"],
                repository,
                workspace_root or root,
                host_adapter_id,
                expected_hierarchy_fingerprint=stored[
                    "hierarchyFingerprint"
                ],
                expected_graph_fingerprint=stored["graphFingerprint"],
                expected_delivery_revision=stored["deliveryRevision"],
            )
            result["hierarchyFingerprint"] = stored[
                "hierarchyFingerprint"
            ]
            result["graphFingerprint"] = stored["graphFingerprint"]
            _attach_pending_interaction(result, interaction)
            return result
        if selection is None and result["status"] in {
            "ACTIVE",
            "BLOCKED",
            "PAUSED",
            "COMPLETED",
            "CANCELLED",
            "SUPERSEDED",
        }:
            try:
                unselected_workspace_turn = (
                    repository.serial_workspace_turn_state(
                        selected_root_id
                    )
                )
            except GatedLoopError as error:
                if error.code != "SCHEDULER_DELIVERY_WORKSPACE_MISSING":
                    raise
            else:
                serial_gate = _resolve_serial_workspace_gate(
                    repository,
                    workspace_root or root,
                    selected_root_id,
                    unselected_workspace_turn,
                )
                result["workspaceStrategy"] = "CURRENT_WORKSPACE_SERIAL"
                result["workspaceTurn"] = serial_gate["workspaceTurn"]
                if serial_gate["state"] in {
                    "RELEASED",
                    "WAITING_FOR_WORKSPACE_COMMIT",
                    "WAITING_FOR_WORKSPACE_QUIESCENCE",
                }:
                    _attach_release_handshake(
                        result,
                        _serial_workspace_release_handshake(
                            repository,
                            workspace_root or root,
                            selected_root_id,
                        ),
                    )
                    return result
        if selection is not None:
            serial_gate = _resolve_serial_workspace_gate(
                repository,
                workspace_root or root,
                selected_root_id,
                _serial_turn_for_recorded_selection(
                    repository,
                    root_id=selected_root_id,
                    stored=stored,
                    selection=selection,
                    workspace_root=workspace_root or root,
                ),
            )
            result["workspaceStrategy"] = "CURRENT_WORKSPACE_SERIAL"
            result["workspaceTurn"] = serial_gate["workspaceTurn"]
            if serial_gate["state"] in {
                "RELEASED",
                "WAITING_FOR_WORKSPACE_COMMIT",
                "WAITING_FOR_WORKSPACE_QUIESCENCE",
            } and (
                serial_gate["state"] == "RELEASED"
                or serial_gate["workspaceTurn"].get("ownerRootId")
                == selected_root_id
            ):
                _attach_release_handshake(
                    result,
                    _serial_workspace_release_handshake(
                        repository,
                        workspace_root or root,
                        selected_root_id,
                    ),
                )
                return result
            if serial_gate["state"] != "ACQUIRED":
                previous_status = result["status"]
                result["deliveryStatus"] = previous_status
                result["status"] = "QUEUED"
                result["deliveryQueue"] = _delivery_queue_marker(
                    serial_gate,
                    selected_root_id,
                    selection=selection["selection"],
                )
                result["automaticDispatchRequested"] = False
                result["nextAction"] = (
                    "WAIT_FOR_AUTOMATIC_QUEUE_TURN"
                    if selection["selection"] == "AUTOMATIC"
                    else "WAIT_FOR_MANUAL_QUEUE_TURN"
                )
                if git_binding is not None:
                    result["gitBinding"] = git_binding
                result["projectScopes"] = project_scopes or []
                return result
            recorded_preparation = (
                (
                    _automatic_serial_workspace_preparation
                    if selection["selection"] == "AUTOMATIC"
                    else _manual_serial_workspace_preparation
                )(
                    control_root=root,
                    workspace_root=workspace_root or root,
                    hierarchy=stored["hierarchy"],
                )
            )
            if recorded_preparation is not None:
                result["workspacePreparation"] = recorded_preparation
                result["nextAction"] = recorded_preparation["nextAction"]
                if git_binding is not None:
                    result["gitBinding"] = git_binding
                result["projectScopes"] = project_scopes or []
                return result
        discovery = (
            inspect_delivery_git_workspace(
                workspace_root or root,
                base_ref=(
                    git_binding.get("baseRef")
                    if isinstance(git_binding, dict)
                    else None
                ),
                host_adapter_id=host_adapter_id,
            )
            if stored["status"] == "CHOICE_READY"
            else None
        )
        if (
            isinstance(discovery, dict)
            and discovery.get("workspacePreparation") is not None
        ):
            result.update(discovery)
            if git_binding is not None:
                result["gitBinding"] = git_binding
            result["projectScopes"] = project_scopes or []
            return result
        verified_projects = verify_delivery_project_scopes(
            workspace_root or root,
            delivery,
            preparing=False,
        )
        current_project = (
            None
            if project_scopes is None
            else next(
                (
                    item
                    for item in verified_projects
                    if SchedulerRepository.workspace_key(
                        item["workspaceRoot"]
                    )
                    == SchedulerRepository.workspace_key(
                        workspace_root or root
                    )
                ),
                None,
            )
        )
        current_binding = (
            git_binding
            if current_project is None
            else current_project.get("gitBinding")
        )
        git_workspace = (
            verify_delivery_git_binding(
                workspace_root or root,
                current_binding,
                preparing=False,
            )
            if project_scopes is None
            else (
                current_project.get("gitWorkspace")
                if current_project is not None
                else None
            )
        )
        if git_binding is not None:
            result["gitBinding"] = git_binding
        if git_workspace is not None:
            result["gitWorkspace"] = git_workspace
        if current_binding is not None:
            result.update(
                inspect_frozen_git_workspace_provenance(
                    workspace_root or root,
                    current_binding,
                    host_adapter_id=host_adapter_id,
                )
            )
        if project_scopes is not None:
            result["projectScopes"] = project_scopes
            result["verifiedProjectScopes"] = verified_projects
        if selection is not None:
            result["nextAction"] = (
                "RESUME_RECORDED_AUTOMATIC_SELECTION"
                if selection["selection"] == "AUTOMATIC"
                else "START_QUEUED_MANUAL_HANDOFF"
            )
    else:
        discovery = inspect_delivery_git_workspace(
            workspace_root or root,
            base_ref=base_ref,
            confirmed_dirty_state_fingerprint=(
                confirmed_dirty_state_fingerprint
            ),
            host_adapter_id=host_adapter_id,
        )
        if discovery is not None:
            candidate_binding = discovery.get(
                "suggestedGitBinding",
                discovery.get("candidateGitBinding"),
            )
            if isinstance(candidate_binding, dict):
                branch_usage = repository.git_branch_usage(
                    candidate_binding["branchRef"],
                    repository_key=git_repository_identity(
                        workspace_root or root
                    ),
                )
                if branch_usage:
                    discovery.pop("suggestedGitBinding", None)
                    discovery.pop("candidateGitBinding", None)
                    terminal_statuses = {
                        "ARCHIVED",
                        "COMPLETED",
                        "CANCELLED",
                        "SUPERSEDED",
                    }
                    active_usage = any(
                        item["status"] not in terminal_statuses
                        for item in branch_usage
                    )
                    discovery["branchAdoption"] = {
                        "state": (
                            "BRANCH_BOUND_TO_OTHER_DELIVERY"
                            if active_usage
                            else "BRANCH_USED_BY_HISTORICAL_DELIVERY"
                        ),
                        "nextAction": "CREATE_DELIVERY_FEATURE_BRANCH",
                        "workingTreeClean": discovery["workingTree"][
                            "clean"
                        ],
                        "conflictingDeliveries": branch_usage,
                    }
            result.update(discovery)
    return result
