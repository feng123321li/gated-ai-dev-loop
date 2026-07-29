from __future__ import annotations

from typing import Any

from .execution import (
    build_task_context,
    claim_task,
    dispatch_task,
    heartbeat_task,
    pause_task,
    record_task_result,
    resume_task,
)
from .planning import retry_work_item
from .projections import compact_task_context
from .remediation import record_validation_remediation
from .skill_execution import (
    record_skill_activation,
    record_skill_conformance,
)
from .operation_support import (
    NOT_HANDLED,
    OperationContext,
    _with_next_frontier,
)


def execute_task_operation(
    name: str,
    arguments: dict[str, Any],
    *,
    context: OperationContext,
) -> Any:
    root = context.root
    dogfood = context.explicit_dogfood

    if name == "task_context":
        context = build_task_context(
            root=root,
            item_id=arguments["item_id"],
            explicit_dogfood=dogfood,
        )
        if arguments.get("response_mode", "compact") == "full":
            return context
        return {
            "contextMode": "COMPACT",
            "context": compact_task_context(context),
            "detailRef": {
                "tool": "task_context",
                "arguments": {
                    "item_id": arguments["item_id"],
                    "response_mode": "full",
                },
            },
        }
    if name == "record_skill_activation":
        return record_skill_activation(
            root=root,
            item_id=arguments["item_id"],
            stage=arguments["stage"],
            skill_name=arguments["skill_name"],
            activation=arguments["activation"],
            execution_host_runtime=context.execution_host_runtime,
            explicit_dogfood=dogfood,
        )
    if name == "record_skill_conformance":
        return record_skill_conformance(
            root=root,
            item_id=arguments["item_id"],
            activation_receipt_id=arguments["activation_receipt_id"],
            conformance=arguments["conformance"],
            execution_host_runtime=context.execution_host_runtime,
            explicit_dogfood=dogfood,
        )
    if name == "dispatch_task":
        return _with_next_frontier(
            dispatch_task(
                root=root,
                item_id=arguments["item_id"],
                owner=arguments["owner"],
                operation_id=arguments["operation_id"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "heartbeat_task":
        return heartbeat_task(
            root=root,
            item_id=arguments["item_id"],
            operation_id=arguments["operation_id"],
            explicit_dogfood=dogfood,
        )
    if name == "pause_task":
        return _with_next_frontier(
            pause_task(
                root=root,
                item_id=arguments["item_id"],
                operation_id=arguments["operation_id"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "resume_task":
        return _with_next_frontier(
            resume_task(
                root=root,
                item_id=arguments["item_id"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "claim_task":
        return _with_next_frontier(
            claim_task(
                root=root,
                item_id=arguments["item_id"],
                owner=arguments["owner"],
                operation_id=arguments["operation_id"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "task_result":
        return _with_next_frontier(
            record_task_result(
                root=root,
                item_id=arguments["item_id"],
                operation_id=arguments["operation_id"],
                status=arguments["status"],
                evidence=arguments["evidence"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "remediate_task":
        return _with_next_frontier(
            record_validation_remediation(
                root=root,
                item_id=arguments["item_id"],
                expected_baseline_fingerprint=arguments[
                    "expected_baseline_fingerprint"
                ],
                evidence=arguments["evidence"],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    if name == "retry_item":
        return _with_next_frontier(
            retry_work_item(
                root=root,
                item_id=arguments["item_id"],
                expected_baseline_fingerprint=arguments[
                    "expected_baseline_fingerprint"
                ],
                explicit_dogfood=dogfood,
            ),
            root=root,
            work_item_id=arguments["item_id"],
        )
    return NOT_HANDLED
