from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .dispatch_planning import plan_dispatch_batch
from .entry_routing import route_entry_intent
from .dashboard import open_delivery_dashboard
from .errors import GatedLoopError, fail
from .graph_frontier import get_graph_frontier
from .git_binding import (
    verify_delivery_git_binding,
    verify_runtime_delivery_project_scopes,
)
from .graph_runtime import (
    advance_graph,
    archive_delivery,
    cancel_graph_run,
    close_delivery,
    dispatch_loop,
    graph_events,
    graph_status,
    handoff_ready_automatic_task,
    heartbeat_loop,
    loop_context,
    pause_loop,
    report_loop_progress,
    refreeze_task_requirement,
    rebuild_graph_run,
    record_loop_result,
    record_user_confirmation,
    resume_loop,
    unfreeze_task_requirement,
)
from .hierarchy_contract import hierarchy_contract
from .planning import (
    confirm_development_baseline,
    create_manual_handoff,
    delivery_revision_history,
    freeze_hierarchy,
    prepare_delivery_revision,
    prepare_hierarchy,
    preview_hierarchy,
    resume_execution_mode,
    select_execution_mode,
    start_manual_handoff,
    workspace_status,
)
from .planning_gates import _serial_workspace_release_handshake
from .repository import SchedulerRepository
from .result_ledger import delivery_result


ControllerOperation = Callable[..., dict[str, Any]]

CONTROL_ROOT_MONITOR_TOOLS = frozenset(
    {
        "graph_frontier",
        "graph_status",
        "delivery_result",
        "route_entry_intent",
        "graph_events",
        "open_delivery_dashboard",
        "close_delivery",
        "record_user_confirmation",
        "resume_loop",
    }
)

CONTROLLER_OPERATIONS: Mapping[str, ControllerOperation] = {
    "workspace_status": workspace_status,
    "route_entry_intent": route_entry_intent,
    "hierarchy_contract": hierarchy_contract,
    "preview_hierarchy": preview_hierarchy,
    "confirm_development_baseline": confirm_development_baseline,
    "select_execution_mode": select_execution_mode,
    "resume_execution_mode": resume_execution_mode,
    "create_manual_handoff": create_manual_handoff,
    "start_manual_handoff": start_manual_handoff,
    "prepare_hierarchy": prepare_hierarchy,
    "prepare_delivery_revision": prepare_delivery_revision,
    "delivery_revision_history": delivery_revision_history,
    "plan_dispatch_batch": plan_dispatch_batch,
    "freeze_hierarchy": freeze_hierarchy,
    "graph_frontier": get_graph_frontier,
    "graph_status": graph_status,
    "delivery_result": delivery_result,
    "open_delivery_dashboard": open_delivery_dashboard,
    "graph_events": graph_events,
    "advance_graph": advance_graph,
    "loop_context": loop_context,
    "dispatch_loop": dispatch_loop,
    "handoff_ready_automatic_task": handoff_ready_automatic_task,
    "heartbeat_loop": heartbeat_loop,
    "report_loop_progress": report_loop_progress,
    "pause_loop": pause_loop,
    "unfreeze_task_requirement": unfreeze_task_requirement,
    "refreeze_task_requirement": refreeze_task_requirement,
    "resume_loop": resume_loop,
    "record_loop_result": record_loop_result,
    "rebuild_graph_run": rebuild_graph_run,
    "record_user_confirmation": record_user_confirmation,
    "close_delivery": close_delivery,
    "cancel_graph_run": cancel_graph_run,
    "archive_delivery": archive_delivery,
}


@dataclass(frozen=True)
class ControllerContext:
    """Protocol-neutral execution context supplied by an outer adapter."""

    project_root: str
    workspace_root: str | None = None
    explicit_dogfood: bool = False
    host_native_agent_ids: tuple[str, ...] | None = None
    host_adapter_id: str | None = None


class LayeredDeliveryController:
    """Shared application controller used by every transport adapter."""

    def __init__(
        self,
        operations: Mapping[str, ControllerOperation] = CONTROLLER_OPERATIONS,
    ) -> None:
        self._operations = dict(operations)

    @property
    def operation_names(self) -> frozenset[str]:
        return frozenset(self._operations)

    def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        context: ControllerContext,
    ) -> dict[str, Any]:
        operation = self._operations.get(name)
        if operation is None:
            fail(
                "CONTROLLER_OPERATION_UNKNOWN",
                f"Unknown scheduler operation: {name}",
            )
        workspace_root = context.workspace_root or context.project_root
        arguments_value = dict(arguments)
        protected_receiver_mutation = name in {
            "heartbeat_loop",
            "report_loop_progress",
            "pause_loop",
            "record_loop_result",
        }
        if protected_receiver_mutation and not (
            isinstance(arguments_value.get("operation_id"), str)
            and arguments_value["operation_id"].strip()
        ):
            fail(
                "SCHEDULER_OPERATION_ID_REQUIRED",
                "Loop mutations require the explicit claimed operation_id",
            )
        root_id = arguments_value.get("root_id")
        git_binding = None
        git_workspace = None
        verified_projects = None
        monitoring_from_control_root = False
        if isinstance(root_id, str):
            repository = SchedulerRepository(context.project_root)
            if name in {
                "select_execution_mode",
                "resume_execution_mode",
                "start_manual_handoff",
                "confirm_development_baseline",
            }:
                selected = repository.hierarchy(root_id)
                unbound_statuses = (
                    {"CHOICE_READY", "PREPARED", "HANDOFF_READY"}
                    if name in {
                        "select_execution_mode",
                        "resume_execution_mode",
                        "confirm_development_baseline",
                    }
                    else {"HANDOFF_READY"}
                )
                if selected["status"] not in unbound_statuses:
                    repository.assert_delivery_workspace(
                        root_id,
                        workspace_root,
                    )
            else:
                try:
                    repository.assert_delivery_workspace(
                        root_id,
                        workspace_root,
                        allow_unbound_manual=(
                            name
                            in {
                                "workspace_status",
                                "delivery_revision_history",
                                "route_entry_intent",
                            }
                        ),
                        allow_unbound_choice=(
                            name
                            in {
                                "workspace_status",
                                "cancel_graph_run",
                                "route_entry_intent",
                            }
                        ),
                    )
                except GatedLoopError as error:
                    same_control_root = (
                        context.host_adapter_id in {"claude-code", "codex", "zcode"}
                        and name in CONTROL_ROOT_MONITOR_TOOLS
                        and os.path.normcase(workspace_root)
                        == os.path.normcase(context.project_root)
                    )
                    if (
                        error.code
                        not in {
                            "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
                            "SCHEDULER_GIT_BRANCH_MISMATCH",
                        }
                        or not same_control_root
                    ):
                        raise
                    monitoring_from_control_root = True
            if name not in {
                "archive_delivery",
                "close_delivery",
                "record_user_confirmation",
                "workspace_status",
                "confirm_development_baseline",
                "prepare_delivery_revision",
                "select_execution_mode",
                "resume_execution_mode",
                "start_manual_handoff",
                "resume_loop",
            } and not monitoring_from_control_root:
                stored = repository.hierarchy(root_id)
                git_binding = stored["hierarchy"]["delivery"].get(
                    "gitBinding"
                )
                project_scopes = stored["hierarchy"]["delivery"].get(
                    "projectScopes"
                )
                verified_projects = verify_runtime_delivery_project_scopes(
                    workspace_root,
                    stored["hierarchy"]["delivery"],
                    preparing=False,
                )
                git_workspace = (
                    verify_delivery_git_binding(
                        workspace_root,
                        git_binding,
                        preparing=False,
                    )
                    if project_scopes is None
                    else next(
                        (
                            item.get("gitWorkspace")
                            for item in verified_projects
                            if repository.workspace_key(
                                item["workspaceRoot"]
                            )
                            == repository.workspace_key(workspace_root)
                        ),
                        None,
                    )
                )
        if name in {
            "workspace_status",
            "route_entry_intent",
            "preview_hierarchy",
            "create_manual_handoff",
            "prepare_hierarchy",
            "prepare_delivery_revision",
            "plan_dispatch_batch",
            "freeze_hierarchy",
            "confirm_development_baseline",
            "select_execution_mode",
            "resume_execution_mode",
            "start_manual_handoff",
            "handoff_ready_automatic_task",
            "resume_loop",
        }:
            arguments_value["workspace_root"] = workspace_root
        if name == "loop_context":
            arguments_value["workspace_root"] = workspace_root
            arguments_value["verified_project_scopes"] = (
                verified_projects
            )
        if name == "record_loop_result":
            arguments_value["verified_project_scopes"] = (
                verified_projects
            )
        if name == "handoff_ready_automatic_task":
            arguments_value["verified_project_scopes"] = (
                verified_projects
            )
        if name in {
            "plan_dispatch_batch",
            "dispatch_loop",
        }:
            arguments_value["host_native_agent_ids"] = (
                context.host_native_agent_ids
            )
        if name in {
            "workspace_status",
            "preview_hierarchy",
            "confirm_development_baseline",
            "select_execution_mode",
            "resume_execution_mode",
            "start_manual_handoff",
        }:
            arguments_value["host_adapter_id"] = context.host_adapter_id
        if name == "plan_dispatch_batch":
            arguments_value["host_adapter_id"] = context.host_adapter_id
        if name == "dispatch_loop":
            arguments_value["host_adapter_id"] = context.host_adapter_id
            arguments_value["verified_project_scopes"] = verified_projects
        result = operation(
            root=context.project_root,
            explicit_dogfood=context.explicit_dogfood,
            **arguments_value,
        )
        if (
            isinstance(root_id, str)
            and name
            in {
                "pause_loop",
                "record_user_confirmation",
                "cancel_graph_run",
            }
            and result.get("status")
            in {"PAUSED", "COMPLETED", "CANCELLED"}
        ):
            lifecycle_next_action = result.get("nextAction")
            result.update(
                _serial_workspace_release_handshake(
                    SchedulerRepository(context.project_root),
                    workspace_root,
                    root_id,
                )
            )
            if name == "record_user_confirmation":
                result["workspaceNextAction"] = result.get("nextAction")
                result["nextAction"] = lifecycle_next_action
        if git_binding is not None:
            result["gitBinding"] = git_binding
        if git_workspace is not None:
            result["gitWorkspace"] = git_workspace
        if monitoring_from_control_root:
            result["coordinationRole"] = "MONITOR_ONLY"
            result["executionWorkspaceMutationAllowed"] = False
        return result


DEFAULT_CONTROLLER = LayeredDeliveryController()


__all__ = (
    "CONTROLLER_OPERATIONS",
    "ControllerContext",
    "DEFAULT_CONTROLLER",
    "LayeredDeliveryController",
)
