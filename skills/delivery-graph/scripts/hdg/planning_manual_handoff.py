from __future__ import annotations

from .graph_model import GRAPH_COMPILER_CONTRACT
from .planning_common import (
    Any,
    GOVERNANCE_DIRECTORY,
    SchedulerRepository,
    atomic_write,
    fail,
    manual_receiver_prompt,
    render_manual_handoff,
    safe_path,
)
from .planning_gates import (
    _delivery_queue_marker,
    _resolve_serial_workspace_gate,
)
from .planning_workspace import _human_artifacts, _preview_values


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
    workspace_root: str | None = None,
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
        workspace_key=SchedulerRepository.workspace_key(
            workspace_root or root
        ),
    )
    created_at = registration["recordedAt"]
    content = render_manual_handoff(
        normalized,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
        graph_compiler_contract=GRAPH_COMPILER_CONTRACT,
        confirmed_by=confirmed_by.strip(),
        created_at=created_at,
        receiver_prompt=receiver_prompt,
    )
    atomic_write(safe_path(root, relative_path), content)
    serial_gate = _resolve_serial_workspace_gate(
        repository,
        workspace_root or root,
        root_id,
        registration["workspaceTurn"],
    )
    queued = serial_gate["state"] != "ACQUIRED"
    result = {
        "rootId": root_id,
        "status": "QUEUED" if queued else "HANDOFF_READY",
        **({"deliveryStatus": "HANDOFF_READY"} if queued else {}),
        "deliveryRevision": registration["deliveryRevision"],
        "previousRevision": registration["previousRevision"],
        "requirementSnapshotStatus": "FROZEN",
        "hierarchyFingerprint": hierarchy_value,
        "graphFingerprint": graph_value,
        "graphCompilerContract": GRAPH_COMPILER_CONTRACT,
        "confirmedBy": confirmed_by.strip(),
        "createdAt": created_at,
        "manualHandoff": {
            "path": relative_path,
            "format": "MARKDOWN",
            "selfContained": True,
            "receiverPrompt": receiver_prompt,
        },
        "humanArtifacts": _human_artifacts(normalized),
        "controlStateCreated": True,
        "graphRunCreated": False,
        "workspaceCreated": False,
        "workspaceBound": True,
        "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
        "workspaceTurn": serial_gate["workspaceTurn"],
        "nextAction": (
            "WAIT_FOR_MANUAL_QUEUE_TURN"
            if queued
            else (
                "OPEN_FROZEN_BUNDLE_AND_START_MANUAL_HANDOFF_"
                "IN_RECEIVING_CLI"
            )
        ),
    }
    if queued:
        result["deliveryQueue"] = _delivery_queue_marker(
            serial_gate,
            root_id,
            selection="MANUAL",
        )
    return result


def _refresh_manual_handoff_runtime(
    *,
    root: str,
    repository: SchedulerRepository,
    root_id: str,
    expected_hierarchy_fingerprint: str,
    expected_graph_fingerprint: str,
) -> dict[str, Any]:
    """Refresh an unstarted handoff and its portable file as one protocol."""

    refresh = repository.refresh_manual_handoff_graph(
        root_id,
        expected_hierarchy_fingerprint=expected_hierarchy_fingerprint,
        expected_graph_fingerprint=expected_graph_fingerprint,
    )
    if not refresh["refreshed"]:
        return refresh
    stored = repository.hierarchy(root_id)
    history = repository.revision_history(root_id)
    current = next(
        item
        for item in history["revisions"]
        if item["revision"] == history["currentRevision"]
    )
    relative_path = (
        f"{GOVERNANCE_DIRECTORY}/{root_id}/"
        f"handoff-{expected_hierarchy_fingerprint[:12]}.md"
    )
    receiver_prompt = manual_receiver_prompt(
        relative_path,
        stored["hierarchy"]["root"]["skillHints"],
    )
    content = render_manual_handoff(
        stored["hierarchy"],
        hierarchy_fingerprint=expected_hierarchy_fingerprint,
        graph_fingerprint=refresh["graphFingerprint"],
        graph_compiler_contract=refresh["compilerContract"],
        confirmed_by=current["confirmedBy"],
        created_at=current["createdAt"],
        receiver_prompt=receiver_prompt,
    )
    atomic_write(safe_path(root, relative_path), content)
    return refresh
