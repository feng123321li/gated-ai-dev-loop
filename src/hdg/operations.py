from __future__ import annotations

from typing import Any, Callable

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


Operation = Callable[..., dict[str, Any]]

OPERATIONS: dict[str, Operation] = {
    "workspace_status": workspace_status,
    "hierarchy_contract": hierarchy_contract,
    "prepare_hierarchy": prepare_hierarchy,
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


def execute_operation(
    name: str,
    *,
    root: str,
    explicit_dogfood: bool = False,
    **arguments: Any,
) -> dict[str, Any]:
    operation = OPERATIONS.get(name)
    if operation is None:
        from .errors import fail

        fail("MCP_TOOL_UNKNOWN", f"Unknown scheduler tool: {name}")
    return operation(
        root=root,
        explicit_dogfood=explicit_dogfood,
        **arguments,
    )


__all__ = ("execute_operation",)
