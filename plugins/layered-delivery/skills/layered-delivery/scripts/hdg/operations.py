from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .acceptance import accept_work_item, record_acceptance, record_work_item_gate
from .constants import MAX_MCP_EVENT_PAGE_SIZE
from .errors import GatedLoopError
from .execution import (
    build_task_context,
    claim_task,
    dispatch_task,
    heartbeat_task,
    list_ready_tasks,
    pause_task,
    record_task_result,
    resume_task,
)
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
from .interactions import list_interactions, record_interaction
from .payloads import (
    abort_payload_upload,
    append_payload_chunk,
    begin_payload_upload,
    finalize_payload_upload,
    get_payload_upload_status,
)
from .planning import (
    freeze_hierarchy,
    prepare_hierarchy,
    refresh_work_item_projections,
    retry_work_item,
)
from .remediation import record_validation_remediation
from .repository import GovernanceRepository
from .skill_execution import (
    record_skill_activation,
    record_skill_conformance,
)


@dataclass(frozen=True)
class OperationContext:
    """Trusted invocation context for the MCP adapter and internal service tests."""

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


def execute_operation(
    name: str,
    arguments: dict[str, Any],
    *,
    context: OperationContext,
) -> Any:
    """Execute one structured controller operation without a shell boundary."""

    root = context.root
    dogfood = context.explicit_dogfood

    if name == "workspace_status":
        return GovernanceRepository(root).inspect_workspace_state()
    if name == "begin_payload_upload":
        return begin_payload_upload(
            root=root,
            upload_id=arguments["upload_id"],
            target_tool=arguments["target_tool"],
            total_chunks=arguments["total_chunks"],
            explicit_dogfood=dogfood,
        )
    if name == "append_payload_chunk":
        return append_payload_chunk(
            root=root,
            upload_id=arguments["upload_id"],
            generation_id=arguments["generation_id"],
            chunk_index=arguments["chunk_index"],
            data=arguments["data"],
            explicit_dogfood=dogfood,
        )
    if name == "finalize_payload_upload":
        return finalize_payload_upload(
            root=root,
            upload_id=arguments["upload_id"],
            generation_id=arguments["generation_id"],
            explicit_dogfood=dogfood,
        )
    if name == "payload_upload_status":
        return get_payload_upload_status(
            root=root,
            upload_id=arguments["upload_id"],
            generation_id=arguments["generation_id"],
        )
    if name == "abort_payload_upload":
        return abort_payload_upload(
            root=root,
            upload_id=arguments["upload_id"],
            generation_id=arguments["generation_id"],
            explicit_dogfood=dogfood,
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
        return freeze_hierarchy(
            root=root,
            root_id=arguments["item_id"],
            expected_hierarchy_fingerprint=arguments[
                "expected_hierarchy_fingerprint"
            ],
            development_mode=arguments["development_mode"],
            confirmed=arguments["confirmed"],
            explicit_dogfood=dogfood,
        )
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
        return advance_graph(
            root=root,
            work_item_id=arguments["item_id"],
            explicit_dogfood=dogfood,
        )
    if name == "cancel_graph_run":
        return cancel_graph_run(
            root=root,
            work_item_id=arguments["item_id"],
            confirmed=arguments["confirmed"],
            explicit_dogfood=dogfood,
        )
    if name == "task_context":
        return build_task_context(
            root=root,
            item_id=arguments["item_id"],
            explicit_dogfood=dogfood,
        )
    if name == "evidence_contract":
        return get_evidence_contract(
            root=root,
            work_item_id=arguments["item_id"],
            contract_kind=arguments["contract_kind"],
        )
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
        return dispatch_task(
            root=root,
            item_id=arguments["item_id"],
            owner=arguments["owner"],
            operation_id=arguments["operation_id"],
            explicit_dogfood=dogfood,
        )
    if name == "heartbeat_task":
        return heartbeat_task(
            root=root,
            item_id=arguments["item_id"],
            operation_id=arguments["operation_id"],
            explicit_dogfood=dogfood,
        )
    if name == "pause_task":
        return pause_task(
            root=root,
            item_id=arguments["item_id"],
            operation_id=arguments["operation_id"],
            explicit_dogfood=dogfood,
        )
    if name == "resume_task":
        return resume_task(
            root=root,
            item_id=arguments["item_id"],
            explicit_dogfood=dogfood,
        )
    if name == "claim_task":
        return claim_task(
            root=root,
            item_id=arguments["item_id"],
            owner=arguments["owner"],
            operation_id=arguments["operation_id"],
            explicit_dogfood=dogfood,
        )
    if name == "task_result":
        return record_task_result(
            root=root,
            item_id=arguments["item_id"],
            operation_id=arguments["operation_id"],
            status=arguments["status"],
            evidence=arguments["evidence"],
            explicit_dogfood=dogfood,
        )
    if name == "remediate_task":
        return record_validation_remediation(
            root=root,
            item_id=arguments["item_id"],
            expected_baseline_fingerprint=arguments[
                "expected_baseline_fingerprint"
            ],
            evidence=arguments["evidence"],
            explicit_dogfood=dogfood,
        )
    if name == "retry_item":
        return retry_work_item(
            root=root,
            item_id=arguments["item_id"],
            expected_baseline_fingerprint=arguments[
                "expected_baseline_fingerprint"
            ],
            explicit_dogfood=dogfood,
        )
    if name == "gate_item":
        return record_work_item_gate(
            root=root,
            item_id=arguments["item_id"],
            status=arguments["status"],
            evidence=arguments["evidence"],
            explicit_dogfood=dogfood,
        )
    if name == "accept_item":
        return accept_work_item(
            root=root,
            item_id=arguments["item_id"],
            evidence=arguments["evidence"],
            explicit_dogfood=dogfood,
        )
    if name == "record_acceptance":
        return record_acceptance(
            root=root,
            item_id=arguments["item_id"],
            action=arguments["action"],
            evidence=arguments["evidence"],
            explicit_dogfood=dogfood,
        )
    if name == "record_independent_review_pass":
        return record_acceptance(
            root=root,
            item_id=arguments["item_id"],
            action="INDEPENDENT_REVIEW_PASS",
            evidence=arguments["evidence"],
            explicit_dogfood=dogfood,
        )
    if name == "record_independent_review_blocked":
        return record_acceptance(
            root=root,
            item_id=arguments["item_id"],
            action="REVIEW_BLOCKED",
            evidence=arguments["evidence"],
            explicit_dogfood=dogfood,
        )
    if name == "record_human_review_acceptance":
        return record_acceptance(
            root=root,
            item_id=arguments["item_id"],
            action="HUMAN_REVIEW_ACCEPTED",
            evidence=arguments["evidence"],
            explicit_dogfood=dogfood,
        )
    if name == "record_user_confirmation":
        return record_acceptance(
            root=root,
            item_id=arguments["item_id"],
            action="USER_CONFIRMED",
            evidence=arguments["evidence"],
            explicit_dogfood=dogfood,
        )
    if name == "refresh_projections":
        return refresh_work_item_projections(
            root=root,
            explicit_dogfood=dogfood,
        )
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
    raise GatedLoopError(
        "UNKNOWN_OPERATION",
        f"Unknown structured controller operation: {name}",
    )
