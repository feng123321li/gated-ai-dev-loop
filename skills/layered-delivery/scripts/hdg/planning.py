from __future__ import annotations

from typing import Any

from .errors import fail
from .graph_model import (
    compile_delivery_graph,
    graph_fingerprint,
    graph_summary,
)
from .model_core import (
    hierarchy_fingerprint,
    iter_hierarchy_nodes,
    validate_hierarchy_definition,
)
from .model_rendering import (
    task_baseline_relative_path,
    task_has_interface_projection,
    work_item_projection_relative_path,
)
from .repository import SchedulerRepository


def workspace_status(
    *,
    root: str,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    return repository.workspace_status()


def prepare_hierarchy(
    *,
    root: str,
    hierarchy: object,
    explicit_dogfood: bool = False,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Validate and prepare scheduler metadata for human confirmation."""

    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    normalized = validate_hierarchy_definition(hierarchy)
    hierarchy_value = hierarchy_fingerprint(normalized)
    graph = compile_delivery_graph(
        normalized,
        hierarchy_fingerprint=hierarchy_value,
    )
    graph_value = graph_fingerprint(graph)
    prepared = repository.prepare(
        normalized,
        graph,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_value,
    )
    projection_root = (
        f".layered-delivery/{normalized['delivery']['id']}"
    )
    task_baselines = {
        node["definition"]["id"]: (
            f"{projection_root}/"
            f"{task_baseline_relative_path(node['definition']['id'])}"
        )
        for node in iter_hierarchy_nodes(normalized)
        if node["definition"]["kind"] == "TASK"
    }
    work_items = {}
    for node in iter_hierarchy_nodes(normalized):
        definition = node["definition"]
        item_id = definition["id"]
        artifacts = {
            "kind": definition["kind"],
            "baseline": (
                f"{projection_root}/"
                f"{work_item_projection_relative_path(item_id, 'baseline.md')}"
            ),
            "progress": (
                f"{projection_root}/"
                f"{work_item_projection_relative_path(item_id, 'progress.md')}"
            ),
            "acceptance": (
                f"{projection_root}/"
                f"{work_item_projection_relative_path(item_id, 'acceptance.md')}"
            ),
        }
        if task_has_interface_projection(definition):
            artifacts["interfaces"] = (
                f"{projection_root}/"
                f"{work_item_projection_relative_path(item_id, 'interfaces.md')}"
            )
        work_items[item_id] = artifacts
    human_artifacts = {
        "workspaceOverview": ".layered-delivery/overview.md",
        "overview": f"{projection_root}/overview.md",
        "baseline": f"{projection_root}/baseline.md",
        "progress": f"{projection_root}/progress.md",
        "acceptance": f"{projection_root}/acceptance.md",
        "taskBaselines": task_baselines,
        "workItems": work_items,
    }
    return {
        **prepared,
        "graphSummary": graph_summary(graph),
        "humanArtifacts": human_artifacts,
        "nextAction": "FREEZE_HIERARCHY_AFTER_USER_CONFIRMATION",
    }


def freeze_hierarchy(
    *,
    root: str,
    root_id: str,
    expected_hierarchy_fingerprint: str,
    confirmed: bool,
    confirmed_by: str,
    explicit_dogfood: bool = False,
    now: object = None,
    **_: Any,
) -> dict[str, Any]:
    """Freeze the graph after explicit human confirmation and start it."""

    if confirmed is not True:
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "Freezing a hierarchy requires explicit user confirmation",
        )
    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        fail(
            "SCHEDULER_USER_CONFIRMATION_REQUIRED",
            "confirmed_by must identify the confirming human",
        )
    repository = SchedulerRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    result = repository.freeze(
        root_id,
        expected_hierarchy_fingerprint=(
            expected_hierarchy_fingerprint
        ),
    )
    return {
        **result,
        "confirmedBy": confirmed_by.strip(),
        "nextAction": "READ_GRAPH_FRONTIER",
    }


__all__ = (
    "freeze_hierarchy",
    "prepare_hierarchy",
    "workspace_status",
)
