from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .agent_recommendation import available_agents, recommend_executors
from .errors import fail
from .graph_frontier import get_graph_frontier
from .graph_runtime import (
    advance_graph,
    cancel_graph_run,
    dispatch_loop,
    graph_events,
    graph_status,
    heartbeat_loop,
    loop_context,
    pause_loop,
    rebuild_graph_run,
    record_loop_result,
    record_user_confirmation,
    resume_loop,
)
from .hierarchy_contract import hierarchy_contract
from .planning import (
    freeze_hierarchy,
    prepare_hierarchy,
    workspace_status,
)


ControllerOperation = Callable[..., dict[str, Any]]

CONTROLLER_OPERATIONS: Mapping[str, ControllerOperation] = {
    "workspace_status": workspace_status,
    "available_agents": available_agents,
    "hierarchy_contract": hierarchy_contract,
    "prepare_hierarchy": prepare_hierarchy,
    "recommend_executors": recommend_executors,
    "freeze_hierarchy": freeze_hierarchy,
    "graph_frontier": get_graph_frontier,
    "graph_status": graph_status,
    "graph_events": graph_events,
    "advance_graph": advance_graph,
    "loop_context": loop_context,
    "dispatch_loop": dispatch_loop,
    "heartbeat_loop": heartbeat_loop,
    "pause_loop": pause_loop,
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
    explicit_dogfood: bool = False


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
        return operation(
            root=context.project_root,
            explicit_dogfood=context.explicit_dogfood,
            **dict(arguments),
        )


DEFAULT_CONTROLLER = LayeredDeliveryController()


__all__ = (
    "CONTROLLER_OPERATIONS",
    "ControllerContext",
    "DEFAULT_CONTROLLER",
    "LayeredDeliveryController",
)
