from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .evidence import (
    confirmation_evidence_contract,
    gate_evidence_contract,
    review_evidence_contract,
    task_result_evidence_contract,
    validation_remediation_evidence_contract,
)
from .graph_model import graph_summary, runtime_transition
from .jsonio import fingerprint
from .model import required_skill_policy, scope_patterns_overlap
from .graph_contracts import (
    _gate_contract,
    _remediation_contract,
    _result_contract,
    evidence_contract_ref,
    mcp_call,
)

from .graph_state import (
    materialized_graph_states,
    retry_budget,
    failure_routing_decision,
    hierarchy_root_entry,
    is_descendant,
    _base_node_state,
    derive_node_states,
    _set_replay_node,
    _refresh_replay_readiness,
    _new_replay_attempts,
    replay_graph_events,
    replay_mismatches,
    critical_path,
    _runtime_time,
    _runtime_timestamp_after,
)

from .graph_queries import (
    _root_for_requested_item,
    _load_graph_view,
    get_evidence_contract,
    get_graph_status,
    get_graph_replay,
)

from .graph_frontier import (
    _task_write_scope,
    build_graph_frontier,
    compact_graph_frontier,
    get_graph_frontier,
)

def rebuild_graph_run(
    *,
    root: str,
    work_item_id: str,
    confirmed: bool = False,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    if not confirmed:
        fail("CONFIRMATION_REQUIRED", "Graph snapshot rebuild requires explicit confirmation")
    from .repository import GovernanceRepository, timestamp

    repository = GovernanceRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(repository.now)
    with repository.transaction() as registry:
        requested, root_entry = _root_for_requested_item(registry, work_item_id)
        repository.rebuild_graph_run_from_events(root_entry["id"])
        registry["currentFocus"] = {
            "workItemId": requested["id"],
            "purpose": "GRAPH_REPLAY_REBUILD",
        }
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_registry(registry, changed_item_ids=set())
    return get_graph_replay(root=root, work_item_id=work_item_id)


def list_graph_events(
    *,
    root: str,
    work_item_id: str,
    after_event_id: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    repository, registry, _, _, _ = _load_graph_view(root=root, work_item_id=work_item_id)
    root_entry = hierarchy_root_entry(
        registry,
        next(item for item in registry["workItems"] if item["id"] == work_item_id),
    )
    return repository.read_graph_events(
        root_entry["id"],
        after_event_id=after_event_id,
        limit=limit,
    )


def advance_graph(
    *,
    root: str,
    work_item_id: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Apply deterministic controller routes such as expired-claim recovery."""
    from .repository import GovernanceRepository, timestamp

    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    decisions: list[dict[str, Any]] = []
    with repository.transaction() as registry:
        requested, root_entry = _root_for_requested_item(registry, work_item_id)
        stored = repository.read_graph_definition(root_entry["id"])
        run = repository.read_graph_run(root_entry["id"])
        replay = replay_graph_events(
            stored["graph"],
            run,
            repository.read_graph_events(root_entry["id"]),
        )
        if replay["status"] in {"CANCELLED", "COMPLETED"}:
            return {
                "rootId": root_entry["id"],
                "runId": run["runId"],
                "status": replay["status"],
                "decisions": [],
            }
        by_item = {item["id"]: item for item in registry["workItems"]}
        for state in replay["nodes"]:
            entry = by_item[state["workItemId"]]
            if (
                state["kind"] != "TASK_EXECUTION"
                or state["status"] != "CLAIMED"
                or not is_descendant(registry, entry, requested["id"])
                or not state.get("leaseExpiresAt")
                or _runtime_time(at) < _runtime_time(
                    _runtime_timestamp_after(
                        state["leaseExpiresAt"],
                        stored["graph"]["runtime"]["claimPolicy"][
                            "graceSeconds"
                        ],
                    )
                )
            ):
                continue
            claim = entry.get("claim") or {}
            if claim.get("operationId") != state.get("operationId"):
                fail(
                    "DELIVERY_GRAPH_REPLAY_MISMATCH",
                    "Graph claim and work item claim disagree during automatic recovery",
                )
            repository.append_graph_event(
                root_id=root_entry["id"],
                node_id=state["nodeId"],
                event_type="CLAIM_LEASE_EXPIRED",
                actor="CONTROLLER",
                operation_id=state["operationId"],
                payload={
                    "leaseExpiresAt": state["leaseExpiresAt"],
                    "hardExpiresAt": _runtime_timestamp_after(
                        state["leaseExpiresAt"],
                        stored["graph"]["runtime"]["claimPolicy"][
                            "graceSeconds"
                        ],
                    ),
                    "failureClass": "WORKER_LOST",
                },
                recorded_at=at,
            )
            entry["claim"] = None
            entry["status"] = "BLOCKED"
            decision = failure_routing_decision(
                stored["graph"],
                attempt=state["attempt"],
                failure_class="WORKER_LOST",
            )
            if decision["action"] == "RETRY_NODE":
                attempts = repository.begin_graph_attempts(
                    root_entry["id"], [state["nodeId"]], at=at
                )
                repository.append_graph_event(
                    root_id=root_entry["id"],
                    node_id=state["nodeId"],
                    event_type="NODE_RETRY_SCHEDULED",
                    actor="CONTROLLER",
                    operation_id=None,
                    payload={
                        "attempts": attempts,
                        "failureClass": "WORKER_LOST",
                        "routeCondition": decision["routeCondition"],
                    },
                    recorded_at=at,
                )
                entry["status"] = "FROZEN"
            else:
                repository.append_graph_event(
                    root_id=root_entry["id"],
                    node_id=state["nodeId"],
                    event_type="RETRY_EXHAUSTED",
                    actor="CONTROLLER",
                    operation_id=None,
                    payload={
                        "failureClass": "WORKER_LOST",
                        "routeCondition": decision["routeCondition"],
                        "maxAttempts": decision["maxAttempts"],
                    },
                    recorded_at=at,
                )
            entry["recordRevision"] += 1
            entry["updatedAt"] = at
            decisions.append({"nodeId": state["nodeId"], **decision})
        if decisions:
            registry["currentFocus"] = {
                "workItemId": requested["id"],
                "purpose": "GRAPH_ADVANCED",
            }
            registry["revision"] += 1
            registry["updatedAt"] = at
            repository.write_registry(registry)
    status = get_graph_status(root=root, work_item_id=work_item_id)
    return {
        "rootId": status["rootId"],
        "runId": status["run"]["runId"],
        "status": status["run"]["status"],
        "decisions": decisions,
    }


def cancel_graph_run(
    *,
    root: str,
    work_item_id: str,
    confirmed: bool = False,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    if not confirmed:
        fail("CONFIRMATION_REQUIRED", "Graph run cancellation requires explicit confirmation")
    from .repository import GovernanceRepository, timestamp

    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    with repository.transaction() as registry:
        requested, root_entry = _root_for_requested_item(registry, work_item_id)
        stored = repository.read_graph_definition(root_entry["id"])
        run = repository.read_graph_run(root_entry["id"])
        replay = replay_graph_events(
            stored["graph"],
            run,
            repository.read_graph_events(root_entry["id"]),
        )
        if replay["status"] == "CANCELLED":
            return {
                "rootId": root_entry["id"],
                "runId": run["runId"],
                "status": "CANCELLED",
                "cancelledAt": replay["cancelledAt"],
            }
        if replay["status"] == "COMPLETED":
            fail("DELIVERY_GRAPH_ALREADY_COMPLETED", "A completed graph run cannot be cancelled")
        for entry in registry["workItems"]:
            if is_descendant(registry, entry, root_entry["id"]) and entry.get("claim"):
                entry["claim"] = None
                entry["status"] = "FROZEN"
                entry["recordRevision"] += 1
                entry["updatedAt"] = at
        repository.append_graph_event(
            root_id=root_entry["id"],
            node_id=None,
            event_type="GRAPH_RUN_CANCELLED",
            actor="USER",
            operation_id=None,
            payload={"confirmed": True, "requestedItemId": requested["id"]},
            recorded_at=at,
        )
        registry["currentFocus"] = {
            "workItemId": requested["id"],
            "purpose": "GRAPH_RUN_CANCELLED",
        }
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_registry(registry)
        return {
            "rootId": root_entry["id"],
            "runId": run["runId"],
            "status": "CANCELLED",
            "cancelledAt": at,
        }
