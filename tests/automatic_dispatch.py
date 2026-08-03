"""Test-only helper for claiming Loops through the automatic contract.

Runtime tests exercise Graph state transitions rather than route planning.
They still need a real, decision-bound reservation now that manual Graph runs
no longer exist. This helper creates that prerequisite in the test database
and then calls the production ``dispatch_loop`` unchanged.
"""

from __future__ import annotations

import uuid
from typing import Any

from hdg.dispatch_contracts import (
    HOST_NATIVE_DISPATCH_TRANSPORT,
    automatic_dispatch_decision_fingerprint,
)
from hdg.graph_runtime import dispatch_loop as runtime_dispatch_loop
from hdg.errors import GatedLoopError
from hdg.repository import SchedulerRepository, timestamp


def reserve_loop(
    *,
    root: str,
    root_id: str,
    node_id: str,
    agent_id: str = "codex",
    model_id: str = "gpt-test",
    now: object = None,
) -> dict[str, Any]:
    """Create one decision-bound test reservation for a Graph Loop."""

    repository = SchedulerRepository(root, now=now)
    stored = repository.hierarchy(root_id)
    run = repository.run(root_id)
    state = next(node for node in run["nodes"] if node["nodeId"] == node_id)
    reasoning_class = "STANDARD"
    decision_fingerprint = automatic_dispatch_decision_fingerprint(
        graph_fingerprint=stored["graphFingerprint"],
        node_id=node_id,
        agent_id=agent_id,
        model_id=model_id,
        reasoning_class=reasoning_class,
        dispatch_transport=HOST_NATIVE_DISPATCH_TRANSPORT,
    )
    with repository.transaction() as connection:
        existing = connection.execute(
            "SELECT reservation_id, decision_fingerprint "
            "FROM dispatch_reservations "
            "WHERE run_id = ? AND node_id = ? AND attempt = ? "
            "AND status = 'RESERVED'",
            (run["runId"], node_id, state["attempt"]),
        ).fetchone()
        if existing is None:
            reservation_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO dispatch_reservations("
                "reservation_id, run_id, root_id, node_id, attempt, "
                "agent_id, model_id, reasoning_class, graph_fingerprint, "
                "decision_fingerprint, status, reserved_at, expires_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?)",
                (
                    reservation_id,
                    run["runId"],
                    root_id,
                    node_id,
                    state["attempt"],
                    agent_id,
                    model_id,
                    reasoning_class,
                    stored["graphFingerprint"],
                    decision_fingerprint,
                    timestamp(now),
                    "9999-12-31T23:59:59Z",
                ),
            )
            created = True
        else:
            reservation_id = existing["reservation_id"]
            decision_fingerprint = existing["decision_fingerprint"]
            created = False
    return {
        "agentId": agent_id,
        "modelId": model_id,
        "dispatchMode": "AUTO",
        "dispatchTransport": HOST_NATIVE_DISPATCH_TRANSPORT,
        "dispatchReservationId": reservation_id,
        "dispatchReasoningClass": reasoning_class,
        "dispatchDecisionFingerprint": decision_fingerprint,
        "created": created,
    }


def dispatch_loop(**arguments: Any) -> dict[str, Any]:
    """Claim a Loop with a test reservation unless provenance is explicit."""

    provenance_fields = {
        "dispatch_mode",
        "dispatch_transport",
        "dispatch_reservation_id",
        "dispatch_reasoning_class",
        "dispatch_decision_fingerprint",
    }
    if provenance_fields.intersection(arguments):
        return runtime_dispatch_loop(**arguments)

    agent_id = arguments.get("agent_id")
    model_id = arguments.get("model_id")
    if (agent_id is None) != (model_id is None):
        return runtime_dispatch_loop(**arguments)
    if agent_id is None:
        agent_id = "codex"
        model_id = "gpt-test"
    if arguments.get("actual_model_id") is not None and model_id is None:
        return runtime_dispatch_loop(**arguments)

    root = arguments["root"]
    root_id = arguments["root_id"]
    node_id = arguments["node_id"]
    now = arguments.get("now")
    reservation = reserve_loop(
        root=root,
        root_id=root_id,
        node_id=node_id,
        agent_id=agent_id,
        model_id=model_id,
        now=now,
    )

    claimed_arguments = dict(arguments)
    claimed_arguments.update(
        {
            "agent_id": agent_id,
            "model_id": model_id,
            "dispatch_mode": reservation["dispatchMode"],
            "dispatch_transport": reservation["dispatchTransport"],
            "dispatch_reservation_id": reservation[
                "dispatchReservationId"
            ],
            "dispatch_reasoning_class": reservation[
                "dispatchReasoningClass"
            ],
            "dispatch_decision_fingerprint": reservation[
                "dispatchDecisionFingerprint"
            ],
        }
    )
    try:
        return runtime_dispatch_loop(**claimed_arguments)
    except GatedLoopError:
        if reservation["created"]:
            repository = SchedulerRepository(root, now=now)
            with repository.transaction() as connection:
                connection.execute(
                    "DELETE FROM dispatch_reservations "
                    "WHERE reservation_id = ? AND status = 'RESERVED'",
                    (reservation["dispatchReservationId"],),
                )
        raise


__all__ = ("dispatch_loop", "reserve_loop")
