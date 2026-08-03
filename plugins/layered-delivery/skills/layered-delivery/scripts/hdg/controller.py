from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .agent_recommendation import available_agents, recommend_executors
from .dispatch_planning import plan_dispatch_batch
from .errors import fail
from .graph_frontier import get_graph_frontier
from .git_binding import (
    verify_delivery_git_binding,
    verify_delivery_project_scopes,
)
from .graph_runtime import (
    advance_graph,
    cancel_graph_run,
    dispatch_loop,
    graph_events,
    graph_status,
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
from .orchestrator_config import OrchestratorConfig
from .orchestrator_settings import (
    open_orchestrator_settings,
    update_orchestrator_settings,
)
from .planning import (
    create_manual_handoff,
    delivery_revision_history,
    freeze_hierarchy,
    prepare_delivery_revision,
    prepare_hierarchy,
    preview_hierarchy,
    workspace_status,
)
from .repository import SchedulerRepository


ControllerOperation = Callable[..., dict[str, Any]]

CONTROLLER_OPERATIONS: Mapping[str, ControllerOperation] = {
    "open_orchestrator_settings": open_orchestrator_settings,
    "update_orchestrator_settings": update_orchestrator_settings,
    "workspace_status": workspace_status,
    "available_agents": available_agents,
    "hierarchy_contract": hierarchy_contract,
    "preview_hierarchy": preview_hierarchy,
    "create_manual_handoff": create_manual_handoff,
    "prepare_hierarchy": prepare_hierarchy,
    "prepare_delivery_revision": prepare_delivery_revision,
    "delivery_revision_history": delivery_revision_history,
    "recommend_executors": recommend_executors,
    "plan_dispatch_batch": plan_dispatch_batch,
    "freeze_hierarchy": freeze_hierarchy,
    "graph_frontier": get_graph_frontier,
    "graph_status": graph_status,
    "graph_events": graph_events,
    "advance_graph": advance_graph,
    "loop_context": loop_context,
    "dispatch_loop": dispatch_loop,
    "heartbeat_loop": heartbeat_loop,
    "report_loop_progress": report_loop_progress,
    "pause_loop": pause_loop,
    "unfreeze_task_requirement": unfreeze_task_requirement,
    "refreeze_task_requirement": refreeze_task_requirement,
    "resume_loop": resume_loop,
    "record_loop_result": record_loop_result,
    "rebuild_graph_run": rebuild_graph_run,
    "record_user_confirmation": record_user_confirmation,
    "cancel_graph_run": cancel_graph_run,
}


@dataclass(frozen=True)
class ControllerContext:
    """Protocol-neutral execution context supplied by an outer adapter."""

    project_root: str
    workspace_root: str | None = None
    explicit_dogfood: bool = False
    host_native_agent_ids: tuple[str, ...] | None = None
    host_adapter_id: str | None = None
    orchestrator_config: OrchestratorConfig | None = None


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
        root_id = arguments_value.get("root_id")
        git_binding = None
        git_workspace = None
        if isinstance(root_id, str):
            repository = SchedulerRepository(context.project_root)
            repository.assert_delivery_workspace(
                root_id,
                workspace_root,
            )
            if name not in {
                "workspace_status",
                "prepare_delivery_revision",
            }:
                stored = repository.hierarchy(root_id)
                git_binding = stored["hierarchy"]["delivery"].get(
                    "gitBinding"
                )
                project_scopes = stored["hierarchy"]["delivery"].get(
                    "projectScopes"
                )
                verified_projects = verify_delivery_project_scopes(
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
            "prepare_hierarchy",
            "prepare_delivery_revision",
        }:
            arguments_value["workspace_root"] = workspace_root
        if name in {
            "recommend_executors",
            "plan_dispatch_batch",
            "dispatch_loop",
        }:
            arguments_value["host_native_agent_ids"] = (
                context.host_native_agent_ids
            )
        if name in {"recommend_executors", "plan_dispatch_batch"}:
            arguments_value["host_adapter_id"] = context.host_adapter_id
            arguments_value["orchestrator_config"] = (
                context.orchestrator_config
            )
        if name == "plan_dispatch_batch":
            arguments_value["enforce_route_review_window"] = True
        if name in {
            "open_orchestrator_settings",
            "update_orchestrator_settings",
        }:
            arguments_value["host_adapter_id"] = context.host_adapter_id
        if name == "open_orchestrator_settings":
            arguments_value["orchestrator_config"] = (
                context.orchestrator_config
            )
        if name == "dispatch_loop":
            arguments_value["host_adapter_id"] = context.host_adapter_id
            arguments_value["require_receiver_attestation"] = True
        result = operation(
            root=context.project_root,
            explicit_dogfood=context.explicit_dogfood,
            **arguments_value,
        )
        if git_binding is not None:
            result["gitBinding"] = git_binding
        if git_workspace is not None:
            result["gitWorkspace"] = git_workspace
        return result


DEFAULT_CONTROLLER = LayeredDeliveryController()


__all__ = (
    "CONTROLLER_OPERATIONS",
    "ControllerContext",
    "DEFAULT_CONTROLLER",
    "LayeredDeliveryController",
)
