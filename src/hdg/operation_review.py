from __future__ import annotations

from typing import Any

from .acceptance import (
    accept_work_item,
    record_acceptance,
    record_work_item_gate,
)
from .operation_support import (
    NOT_HANDLED,
    OperationContext,
    _with_next_frontier,
)


def execute_review_operation(
    name: str,
    arguments: dict[str, Any],
    *,
    context: OperationContext,
) -> Any:
    root = context.root
    dogfood = context.explicit_dogfood

    if name == "gate_item":
        return _with_next_frontier(
            record_work_item_gate(
                root=root,
                item_id=arguments["item_id"],
                status=arguments["status"],
                evidence=arguments["evidence"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "accept_item":
        return _with_next_frontier(
            accept_work_item(
                root=root,
                item_id=arguments["item_id"],
                evidence=arguments["evidence"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "record_acceptance":
        return _with_next_frontier(
            record_acceptance(
                root=root,
                item_id=arguments["item_id"],
                action=arguments["action"],
                evidence=arguments["evidence"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "record_independent_review_pass":
        return _with_next_frontier(
            record_acceptance(
                root=root,
                item_id=arguments["item_id"],
                action="INDEPENDENT_REVIEW_PASS",
                evidence=arguments["evidence"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "record_independent_review_blocked":
        return _with_next_frontier(
            record_acceptance(
                root=root,
                item_id=arguments["item_id"],
                action="REVIEW_BLOCKED",
                evidence=arguments["evidence"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "record_human_review_acceptance":
        return _with_next_frontier(
            record_acceptance(
                root=root,
                item_id=arguments["item_id"],
                action="HUMAN_REVIEW_ACCEPTED",
                evidence=arguments["evidence"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "record_user_confirmation":
        return _with_next_frontier(
            record_acceptance(
                root=root,
                item_id=arguments["item_id"],
                action="USER_CONFIRMED",
                evidence=arguments["evidence"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    return NOT_HANDLED
