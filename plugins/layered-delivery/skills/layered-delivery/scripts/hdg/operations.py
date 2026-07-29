from __future__ import annotations

from typing import Any

from .errors import GatedLoopError
from .operation_payload import execute_payload_operation
from .operation_planning import execute_planning_operation
from .operation_graph import execute_graph_operation
from .operation_task import execute_task_operation
from .operation_review import execute_review_operation
from .operation_interaction import execute_interaction_operation
from .operation_support import (
    NOT_HANDLED,
    OperationContext,
    _bounded_event_page,
    _with_next_frontier,
)


_OPERATION_HANDLERS = (
    execute_payload_operation,
    execute_planning_operation,
    execute_graph_operation,
    execute_task_operation,
    execute_review_operation,
    execute_interaction_operation,
)


def execute_operation(
    name: str,
    arguments: dict[str, Any],
    *,
    context: OperationContext,
) -> Any:
    """Execute one structured controller operation without a shell boundary."""

    for handler in _OPERATION_HANDLERS:
        result = handler(name, arguments, context=context)
        if result is not NOT_HANDLED:
            return result
    raise GatedLoopError(
        "UNKNOWN_OPERATION",
        f"Unknown structured controller operation: {name}",
    )
