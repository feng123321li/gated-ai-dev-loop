from __future__ import annotations

from typing import Any

from .constants import MAX_IDENTIFIER_LENGTH, SCHEMA_VERSION
from .errors import fail


def _object(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or list(properties),
        "additionalProperties": False,
    }


def _identifier(description: str) -> dict[str, Any]:
    return {
        "type": "string",
        "pattern": (
            f"^[a-z0-9][a-z0-9._-]"
            f"{{0,{MAX_IDENTIFIER_LENGTH - 1}}}$"
        ),
        "description": description,
    }


def _text(description: str) -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "description": description,
    }


def _loop_schema() -> dict[str, Any]:
    return _object(
        {
            "ref": {
                "type": "string",
                "pattern": "^[a-z0-9][a-z0-9._:/@-]{0,191}$",
                "description": "Stable Task Loop implementation reference.",
            },
            "payload": {
                "type": "object",
                "description": (
                    "Opaque Loop-owned input. It may contain implementation "
                    "plans, acceptance rules, tests, gates, and Skills."
                ),
                "additionalProperties": True,
            },
            "resourceClaims": {
                "type": "array",
                "items": {
                    "type": "string",
                    "pattern": "^[a-z0-9][a-z0-9._:/@-]{0,255}$",
                },
                "uniqueItems": True,
                "description": (
                    "Exact exclusive scheduler lock keys; not file scopes."
                ),
            },
        }
    )


def _skill_hint_schema() -> dict[str, Any]:
    return _object(
        {
            "name": {
                "type": "string",
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
                "description": "Exact host Skill catalog name.",
            },
            "purpose": _text(
                "Why a later Loop should prefer this Skill when applicable."
            ),
        }
    )


def _child_schema(kind: str) -> dict[str, Any]:
    return _object(
        {
            "id": _identifier("Direct child ID."),
            "kind": {"const": kind},
            "title": _text("Direct child title."),
        }
    )


def _task_definition(*, root: bool) -> dict[str, Any]:
    return _object(
        {
            "schemaVersion": {"const": SCHEMA_VERSION},
            "id": _identifier("Task scheduler ID."),
            "kind": {"const": "TASK"},
            "parentId": (
                {"const": None}
                if root
                else _identifier("Parent Capability ID.")
            ),
            "title": _text("Task Loop title."),
            "summary": _text("Scheduler-facing outcome summary."),
            "execution": _object(
                {
                    "dependsOn": {
                        "type": "array",
                        "items": _identifier(
                            "Sibling Task dependency ID."
                        ),
                        "uniqueItems": True,
                    },
                    "loop": _loop_schema(),
                }
            ),
        }
    )


def _capability_definition(*, root: bool) -> dict[str, Any]:
    return _object(
        {
            "schemaVersion": {"const": SCHEMA_VERSION},
            "id": _identifier("Capability join ID."),
            "kind": {"const": "CAPABILITY"},
            "parentId": (
                {"const": None}
                if root
                else _identifier("Parent Delivery ID.")
            ),
            "title": _text("Capability title."),
            "summary": _text("Scheduler-facing join summary."),
            "decomposition": _object(
                {
                    "dependsOn": {
                        "type": "array",
                        "items": _identifier(
                            "Sibling Capability dependency ID."
                        ),
                        "uniqueItems": True,
                    }
                }
            ),
            "children": {
                "type": "array",
                "items": _child_schema("TASK"),
                "minItems": 1,
            },
        }
    )


def _delivery_definition() -> dict[str, Any]:
    return _object(
        {
            "schemaVersion": {"const": SCHEMA_VERSION},
            "id": _identifier("Delivery join ID."),
            "kind": {"const": "DELIVERY"},
            "title": _text("Delivery title."),
            "summary": _text("Scheduler-facing delivery summary."),
            "decomposition": _object({}),
            "children": {
                "type": "array",
                "items": _child_schema("CAPABILITY"),
                "minItems": 1,
            },
        }
    )


def _node_schema(kind: str, *, root: bool) -> dict[str, Any]:
    if kind == "TASK":
        definition = _task_definition(root=root)
        children: dict[str, Any] = {
            "type": "array",
            "maxItems": 0,
        }
    elif kind == "CAPABILITY":
        definition = _capability_definition(root=root)
        children = {
            "type": "array",
            "items": _node_schema("TASK", root=False),
            "minItems": 1,
        }
    else:
        definition = _delivery_definition()
        children = {
            "type": "array",
            "items": _node_schema("CAPABILITY", root=False),
            "minItems": 1,
        }
    return _object(
        {
            "definition": definition,
            "children": children,
        }
    )


def _loop(
    reference: str,
    goal: str,
    claims: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ref": reference,
        "payload": {
            "goal": goal,
            "acceptance": ["Return one standard Loop outcome."],
        },
        "resourceClaims": claims or [],
    }


def _task(
    item_id: str,
    parent_id: str | None,
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": item_id,
        "kind": "TASK",
        "parentId": parent_id,
        "title": "Run the example Task Loop",
        "summary": "Produce one independently schedulable result.",
        "execution": {
            "dependsOn": [],
            "loop": _loop(
                "project/example-task-loop@1",
                "Implement and verify the example internally.",
                ["project:example/module:core"],
            ),
        },
    }


def _example(root_kind: str) -> dict[str, Any]:
    task_node = {
        "definition": _task(
            "t-example",
            None if root_kind == "TASK" else "c-example",
        ),
        "children": [],
    }
    if root_kind == "TASK":
        root = task_node
    else:
        capability = {
            "schemaVersion": SCHEMA_VERSION,
            "id": "c-example",
            "kind": "CAPABILITY",
            "parentId": (
                None if root_kind == "CAPABILITY" else "d-example"
            ),
            "title": "Join example Task Loops",
            "summary": "Complete when every child Loop succeeds.",
            "decomposition": {"dependsOn": []},
            "children": [
                {
                    "id": "t-example",
                    "kind": "TASK",
                    "title": "Run the example Task Loop",
                }
            ],
        }
        capability_node = {
            "definition": capability,
            "children": [task_node],
        }
        if root_kind == "CAPABILITY":
            root = capability_node
        else:
            root = {
                "definition": {
                    "schemaVersion": SCHEMA_VERSION,
                    "id": "d-example",
                    "kind": "DELIVERY",
                    "title": "Schedule the example delivery",
                    "summary": "Join all example capabilities.",
                    "decomposition": {},
                    "children": [
                        {
                            "id": "c-example",
                            "kind": "CAPABILITY",
                            "title": "Join example Task Loops",
                        }
                    ],
                },
                "children": [capability_node],
            }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "skillHints": [
            {
                "name": "springboot-tdd",
                "purpose": (
                    "Prefer this Skill in a later Loop when its actual "
                    "task is Spring Boot development."
                ),
            }
        ],
        "reviewLoop": _loop(
            "root/independent-review-loop@1",
            "Independently review the completed root result.",
        ),
        "root": root,
    }


def hierarchy_contract(
    *,
    root_kind: str,
    **_: Any,
) -> dict[str, Any]:
    if root_kind not in {"TASK", "CAPABILITY", "DELIVERY"}:
        fail(
            "WORK_ITEM_HIERARCHY_CONTRACT_INVALID",
            "root_kind must be TASK, CAPABILITY, or DELIVERY",
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "rootKind": root_kind,
        "inputSchema": _object(
            {
                "schemaVersion": {"const": SCHEMA_VERSION},
                "skillHints": {
                    "type": "array",
                    "items": _skill_hint_schema(),
                    "uniqueItems": True,
                    "description": (
                        "Shared advisory Skill preferences. Every Loop "
                        "receives them at runtime, then selects only the "
                        "hints applicable to its actual context."
                    ),
                },
                "reviewLoop": _loop_schema(),
                "root": _node_schema(root_kind, root=True),
            }
        ),
        "example": _example(root_kind),
        "invariants": [
            "The outer graph schedules Task Loops and joins only.",
            "Loop payloads own implementation, tests, gates, and Skills.",
            (
                "skillHints are advisory, shared, and late-bound; they "
                "are never assigned to Tasks during requirement planning."
            ),
            "resourceClaims are exact scheduler locks, not file scopes.",
            "Only standard Loop outcomes cross the Loop boundary.",
        ],
    }


__all__ = ("hierarchy_contract",)
