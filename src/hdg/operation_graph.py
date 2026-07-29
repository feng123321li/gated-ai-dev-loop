from __future__ import annotations

from typing import Any

from .execution import list_ready_tasks
from .graph_runtime import (
    advance_graph,
    cancel_graph_run,
    get_evidence_contract,
    get_graph_frontier,
    get_graph_replay,
    get_graph_status,
    list_graph_events,
    rebuild_graph_run,
)
from .operation_support import (
    NOT_HANDLED,
    OperationContext,
    _bounded_event_page,
    _with_next_frontier,
)


def execute_graph_operation(
    name: str,
    arguments: dict[str, Any],
    *,
    context: OperationContext,
) -> Any:
    root = context.root
    dogfood = context.explicit_dogfood

    if name == "ready_tasks":
        return list_ready_tasks(
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "graph_status":
        return get_graph_status(
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "graph_frontier":
        return get_graph_frontier(
            root=root,
            work_item_id=arguments["item_id"],
            response_mode=arguments.get("response_mode", "compact"),
            since_revision=arguments.get("since_revision"),
            include_blocked_details=arguments.get(
                "include_blocked_details",
                arguments.get("response_mode") == "full",
            ),
        )
    if name == "graph_events":
        if "after_event_id" not in arguments and "limit" not in arguments:
            return list_graph_events(
                root=root,
                work_item_id=arguments["item_id"],
            )
        events = list_graph_events(
            root=root,
            work_item_id=arguments["item_id"],
            after_event_id=arguments["after_event_id"],
            limit=arguments["limit"] + 1,
        )
        return _bounded_event_page(
            events,
            after_event_id=arguments["after_event_id"],
            limit=arguments["limit"],
        )
    if name == "graph_replay":
        return get_graph_replay(
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "rebuild_graph_run":
        return rebuild_graph_run(
            root=root,
            work_item_id=arguments["item_id"],
            confirmed=arguments["confirmed"],
            explicit_dogfood=dogfood,
        )
    if name == "advance_graph":
        return _with_next_frontier(
            advance_graph(
                root=root,
                work_item_id=arguments["item_id"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "cancel_graph_run":
        return cancel_graph_run(
            root=root,
            work_item_id=arguments["item_id"],
            confirmed=arguments["confirmed"],
            explicit_dogfood=dogfood,
        )
    if name == "evidence_contract":
        return get_evidence_contract(
            root=root,
            work_item_id=arguments["item_id"],
            contract_kind=arguments["contract_kind"],
        )
    return NOT_HANDLED
