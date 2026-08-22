"""Test-only helper for claiming Loops through the automatic contract.

Runtime tests exercise Graph state transitions rather than route planning.
They still need a real, decision-bound reservation for every AUTO claim. This
helper creates that prerequisite in the test database and then calls the
production ``dispatch_loop`` unchanged; manual Graph TASK claims bypass it.
"""

from __future__ import annotations

import uuid
from typing import Any

from hdg.agent_profiles import (
    built_in_agent_profile_catalog,
    profile_for_loop,
    team_plan_for_profile,
)
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
    now: object = None,
) -> dict[str, Any]:
    """Create one decision-bound test reservation for a Graph Loop."""

    repository = SchedulerRepository(root, now=now)
    stored = repository.hierarchy(root_id)
    run = repository.run(root_id)
    state = next(node for node in run["nodes"] if node["nodeId"] == node_id)
    definition = next(
        node for node in stored["graph"]["nodes"] if node["id"] == node_id
    )
    catalog = built_in_agent_profile_catalog()
    profile = profile_for_loop(catalog, definition["kind"])
    team_plan = team_plan_for_profile(catalog, profile)
    decision_fingerprint = automatic_dispatch_decision_fingerprint(
        graph_fingerprint=stored["graphFingerprint"],
        node_id=node_id,
        attempt=state["attempt"],
        host_adapter_id=agent_id,
        receiver_agent_id=agent_id,
        dispatch_transport=HOST_NATIVE_DISPATCH_TRANSPORT,
        agent_profile_id=profile["id"],
        agent_catalog_fingerprint=catalog["catalogFingerprint"],
        team_plan_fingerprint=team_plan["teamPlanFingerprint"],
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
                "agent_id, graph_fingerprint, "
                "decision_fingerprint, agent_profile_id, "
                "agent_catalog_fingerprint, team_plan_fingerprint, "
                "status, reserved_at, expires_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'RESERVED', ?, ?)",
                (
                    reservation_id,
                    run["runId"],
                    root_id,
                    node_id,
                    state["attempt"],
                    agent_id,
                    stored["graphFingerprint"],
                    decision_fingerprint,
                    profile["id"],
                    catalog["catalogFingerprint"],
                    team_plan["teamPlanFingerprint"],
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
        "dispatchMode": "AUTO",
        "dispatchTransport": HOST_NATIVE_DISPATCH_TRANSPORT,
        "dispatchReservationId": reservation_id,
        "dispatchDecisionFingerprint": decision_fingerprint,
        "created": created,
    }


def dispatch_loop(**arguments: Any) -> dict[str, Any]:
    """Claim a Loop with a test reservation unless provenance is explicit."""

    provenance_fields = {
        "dispatch_mode",
        "dispatch_transport",
        "dispatch_reservation_id",
        "dispatch_decision_fingerprint",
    }
    if provenance_fields.intersection(arguments):
        direct_arguments = dict(arguments)
        direct_arguments.setdefault(
            "receiver_context_id",
            arguments.get("owner", f"receiver-{arguments['node_id']}"),
        )
        return runtime_dispatch_loop(**direct_arguments)

    agent_id = arguments.get("agent_id")
    if agent_id is None:
        agent_id = "codex"

    root = arguments["root"]
    root_id = arguments["root_id"]
    node_id = arguments["node_id"]
    now = arguments.get("now")
    reservation = reserve_loop(
        root=root,
        root_id=root_id,
        node_id=node_id,
        agent_id=agent_id,
        now=now,
    )

    claimed_arguments = dict(arguments)
    claimed_arguments.setdefault(
        "receiver_context_id",
        arguments.get("owner", f"receiver-{node_id}"),
    )
    claimed_arguments.update(
        {
            "agent_id": agent_id,
            "host_adapter_id": agent_id,
            "host_native_agent_ids": (agent_id,),
            "dispatch_mode": reservation["dispatchMode"],
            "dispatch_transport": reservation["dispatchTransport"],
            "dispatch_reservation_id": reservation[
                "dispatchReservationId"
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
