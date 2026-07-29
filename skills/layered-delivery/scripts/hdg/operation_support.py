from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .constants import MAX_MCP_EVENT_PAGE_SIZE
from .errors import GatedLoopError
from .graph_runtime import get_graph_frontier


NOT_HANDLED = object()


@dataclass(frozen=True)
class OperationContext:
    """Trusted invocation context for MCP and internal service tests."""

    root: str
    explicit_dogfood: bool = False
    execution_host_runtime: str | None = None


def _bounded_event_page(
    events: list[dict[str, Any]],
    *,
    after_event_id: int,
    limit: int,
) -> dict[str, Any]:
    if (
        not isinstance(after_event_id, int)
        or isinstance(after_event_id, bool)
        or after_event_id < 0
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= MAX_MCP_EVENT_PAGE_SIZE
    ):
        raise GatedLoopError(
            "MCP_ARGUMENT_INVALID",
            "Event page cursor or limit is invalid",
        )
    candidates = [
        event
        for event in events
        if event.get("eventId", -1) > after_event_id
    ]
    items = candidates[:limit]
    has_more = len(candidates) > limit
    return {
        "items": items,
        "hasMore": has_more,
        "nextCursor": items[-1]["eventId"] if has_more and items else None,
    }


def _with_next_frontier(
    result: dict[str, Any],
    *,
    root: str,
    work_item_id: str,
) -> dict[str, Any]:
    return {
        **result,
        "nextFrontier": get_graph_frontier(
            root=root,
            work_item_id=work_item_id,
            response_mode="compact",
            include_blocked_details=False,
        ),
    }
