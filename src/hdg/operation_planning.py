from __future__ import annotations

from typing import Any

from .planning import (
    freeze_hierarchy,
    prepare_hierarchy,
    refresh_work_item_projections,
)
from .hierarchy_contract import hierarchy_contract
from .repository import GovernanceRepository
from .operation_support import (
    NOT_HANDLED,
    OperationContext,
    _with_next_frontier,
)


def execute_planning_operation(
    name: str,
    arguments: dict[str, Any],
    *,
    context: OperationContext,
) -> Any:
    root = context.root
    dogfood = context.explicit_dogfood

    if name == "workspace_status":
        return GovernanceRepository(root).inspect_workspace_state()
    if name == "hierarchy_contract":
        return hierarchy_contract(
            root_kind=arguments["root_kind"],
            input_mode=arguments["input_mode"],
        )
    if name == "prepare_hierarchy":
        return prepare_hierarchy(
            root=root,
            hierarchy=arguments["hierarchy"],
            host_runtime=arguments["host_runtime"],
            available_skills=arguments["available_skills"],
            explicit_dogfood=dogfood,
        )
    if name == "freeze_hierarchy":
        return _with_next_frontier(
            freeze_hierarchy(
                root=root,
                root_id=arguments["item_id"],
                expected_hierarchy_fingerprint=arguments[
                    "expected_hierarchy_fingerprint"
                ],
                development_mode=arguments["development_mode"],
                confirmed=arguments["confirmed"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "refresh_projections":
        return refresh_work_item_projections(
            root=root,
            explicit_dogfood=dogfood,
        )
    return NOT_HANDLED
