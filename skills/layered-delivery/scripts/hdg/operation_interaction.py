from __future__ import annotations

from typing import Any

from .interactions import list_interactions, record_interaction
from .operation_support import (
    NOT_HANDLED,
    OperationContext,
    _bounded_event_page,
)


def execute_interaction_operation(
    name: str,
    arguments: dict[str, Any],
    *,
    context: OperationContext,
) -> Any:
    root = context.root
    dogfood = context.explicit_dogfood

    if name == "record_interaction":
        return record_interaction(
            root=root,
            item_id=arguments["item_id"],
            interaction=arguments["interaction"],
            explicit_dogfood=dogfood,
        )
    if name == "interaction_log":
        if "after_event_id" not in arguments and "limit" not in arguments:
            return list_interactions(
                root=root,
                item_id=arguments["item_id"],
            )
        events = list_interactions(
            root=root,
            item_id=arguments["item_id"],
            after_event_id=arguments["after_event_id"],
            limit=arguments["limit"] + 1,
        )
        return _bounded_event_page(
            events,
            after_event_id=arguments["after_event_id"],
            limit=arguments["limit"],
        )
    return NOT_HANDLED
