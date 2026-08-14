from __future__ import annotations

from copy import deepcopy

import unittest

from hdg.errors import GatedLoopError

from hdg.graph_model import (
    compile_delivery_graph,
    confirmation_node_id,
    graph_assurance_profile,
    graph_fingerprint,
    graph_summary,
    group_review_node_id,
    join_node_id,
    loop_node_id,
    review_node_id,
    validate_delivery_graph,
)

from hdg.loop_contracts import (
    loop_completion_policy,
    loop_execution_policy,
    validate_loop_descriptor,
    validate_loop_outcome,
)

from hdg.review_contracts import validate_review_result_contract

from hdg.model import (
    hierarchy_fingerprint,
    validate_hierarchy_definition,
)

def loop_descriptor(
    ref: str = "project/java-service-loop@1",
    *,
    claims: list[str] | None = None,
) -> dict:
    return {
        "ref": ref,
        "payload": {
            "goal": "Deliver one observable result.",
            "acceptance": ["The loop returns verified evidence."],
        },
        "resourceClaims": claims or [],
    }

def skill_hint(
    name: str,
    purpose: str = "Prefer this Skill when it fits the active Loop.",
) -> dict:
    return {"name": name, "purpose": purpose}

def task_definition(
    *,
    item_id: str = "t-service",
    parent_id: str | None = None,
    depends_on: list[str] | None = None,
    claims: list[str] | None = None,
) -> dict:
    return {
        "schemaVersion": 3,
        "id": item_id,
        "kind": "TASK",
        "parentId": parent_id,
        "title": f"Run {item_id}",
        "summary": "Schedule one opaque TASK Loop.",
        "execution": {
            "dependsOn": depends_on or [],
            "loop": loop_descriptor(claims=claims),
        },
    }

def group_definition(
    *,
    item_id: str,
    parent_id: str | None,
    children: list[dict],
    depends_on: list[str] | None = None,
) -> dict:
    return {
        "schemaVersion": 3,
        "id": item_id,
        "kind": "GROUP",
        "parentId": parent_id,
        "title": f"Coordinate {item_id}",
        "summary": "Join and review direct GROUP/TASK children.",
        "decomposition": {"dependsOn": depends_on or []},
        "children": [
            {
                "id": child["definition"]["id"],
                "kind": child["definition"]["kind"],
                "title": child["definition"]["title"],
            }
            for child in children
        ],
    }

def node(
    definition: dict,
    children: list[dict] | None = None,
    *,
    review_loop: dict | None = None,
) -> dict:
    if definition["kind"] == "TASK":
        return {
            "definition": definition,
            "reviewLoop": review_loop
            or loop_descriptor("task/independent-review-loop@1"),
            "children": children or [],
        }
    return {
        "definition": definition,
        "reviewLoop": review_loop
        or loop_descriptor("group/independent-review-loop@1"),
        "children": children or [],
    }

def delivery(root: dict, *, delivery_id: str = "d-service") -> dict:
    return {
        "delivery": {
            "id": delivery_id,
            "title": f"Deliver {delivery_id}",
            "summary": "Complete and independently review the Delivery.",
            "reviewLoop": loop_descriptor(
                "delivery/independent-review-loop@1"
            ),
        },
        "root": {
            "schemaVersion": 3,
            "skillHints": [],
            **root,
        },
    }

def task_hierarchy() -> dict:
    return delivery(node(task_definition()))

def group_hierarchy() -> dict:
    children = [
        node(
            task_definition(
                item_id="t-api",
                parent_id="g-service",
                claims=["project:erp/module:api"],
            )
        ),
        node(
            task_definition(
                item_id="t-core",
                parent_id="g-service",
                depends_on=["t-api"],
                claims=["project:erp/module:core"],
            )
        ),
    ]
    return delivery(
        node(
            group_definition(
                item_id="g-service",
                parent_id=None,
                children=children,
            ),
            children,
        )
    )

def recursive_hierarchy() -> dict:
    domain_tasks = [
        node(
            task_definition(
                item_id="t-model",
                parent_id="g-domain",
            )
        ),
        node(
            task_definition(
                item_id="t-repository",
                parent_id="g-domain",
                depends_on=["t-model"],
            )
        ),
    ]
    domain = node(
        group_definition(
            item_id="g-domain",
            parent_id="g-backend",
            children=domain_tasks,
        ),
        domain_tasks,
    )
    api = node(
        task_definition(
            item_id="t-api",
            parent_id="g-backend",
            depends_on=["g-domain"],
        )
    )
    backend_children = [domain, api]
    backend = node(
        group_definition(
            item_id="g-backend",
            parent_id="g-root",
            children=backend_children,
            depends_on=["t-bootstrap"],
        ),
        backend_children,
    )
    quality_task = node(
        task_definition(item_id="t-e2e", parent_id="g-quality")
    )
    quality = node(
        group_definition(
            item_id="g-quality",
            parent_id="g-root",
            children=[quality_task],
            depends_on=["g-backend"],
        ),
        [quality_task],
    )
    root_children = [
        node(task_definition(item_id="t-bootstrap", parent_id="g-root")),
        backend,
        quality,
        node(
            task_definition(
                item_id="t-docs",
                parent_id="g-root",
                depends_on=["g-quality"],
            )
        ),
    ]
    return delivery(
        node(
            group_definition(
                item_id="g-root",
                parent_id=None,
                children=root_children,
            ),
            root_children,
        ),
        delivery_id="d-recursive",
    )
