from __future__ import annotations

import re
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .host_runtime import is_agent_runtime
from .repository import GovernanceRepository, timestamp


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
EVENT_TYPE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
ACTORS = {"USER", "AGENT", "SUBAGENT"}
INTERACTION_FIELDS = {
    "schemaVersion",
    "sessionId",
    "actor",
    "eventType",
    "summary",
    "operationId",
    "hostRuntime",
}


def _validated_interaction(value: object) -> dict[str, Any]:
    valid = (
        isinstance(value, dict)
        and set(value) == INTERACTION_FIELDS
        and value.get("schemaVersion") == SCHEMA_VERSION
        and isinstance(value.get("sessionId"), str)
        and bool(IDENTIFIER.fullmatch(value["sessionId"]))
        and value.get("actor") in ACTORS
        and isinstance(value.get("eventType"), str)
        and bool(EVENT_TYPE.fullmatch(value["eventType"]))
        and isinstance(value.get("summary"), str)
        and bool(value["summary"].strip())
        and len(value["summary"]) <= 2000
        and (
            value.get("operationId") is None
            or (
                isinstance(value.get("operationId"), str)
                and bool(IDENTIFIER.fullmatch(value["operationId"]))
            )
        )
        and is_agent_runtime(value.get("hostRuntime"))
    )
    if not valid:
        fail(
            "WORK_ITEM_INTERACTION_INVALID",
            "Interaction must use the current strict schema and contain a concise auditable summary",
        )
    return dict(value)


def record_interaction(
    *,
    root: str,
    item_id: str,
    interaction: object,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Append one user/Agent interaction summary to the SQLite audit chain."""
    value = _validated_interaction(interaction)
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    with repository.transaction() as registry:
        repository.item_by_id(registry, item_id)
        event = repository.append_interaction_event(
            work_item_id=item_id,
            session_id=value["sessionId"],
            actor=value["actor"],
            event_type=value["eventType"],
            summary=value["summary"].strip(),
            operation_id=value["operationId"],
            host_runtime=value["hostRuntime"],
            payload={"schemaVersion": SCHEMA_VERSION, "source": "record-interaction"},
            registry_revision=registry["revision"],
            recorded_at=at,
        )
        repository.refresh_interaction_logs(registry)
        return event


def list_interactions(
    *,
    root: str,
    item_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return append-only interaction events, optionally for one work item."""
    repository = GovernanceRepository(root)
    registry = repository.read_registry()
    if item_id is None:
        return repository.read_interaction_events()
    entry = repository.item_by_id(registry, item_id)
    by_id = {item["id"]: item for item in registry["workItems"]}

    def subtree_ids(item: dict[str, Any]) -> list[str]:
        result = [item["id"]]
        for child_id in item["childIds"]:
            result.extend(subtree_ids(by_id[child_id]))
        return result

    return repository.read_interaction_events(subtree_ids(entry))
