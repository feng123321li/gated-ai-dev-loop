from __future__ import annotations

from copy import deepcopy
from typing import Any


def minimize_loop_outcome_for_graph(
    outcome: dict[str, Any],
) -> dict[str, Any]:
    """Keep workspace evidence metadata in Graph state, never source diffs."""

    minimized = deepcopy(outcome)
    result = minimized.get("result")
    if not isinstance(result, dict):
        return minimized
    snapshots = result.get("workspaceChanges")
    if not isinstance(snapshots, list):
        return minimized
    manifests: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        manifest = deepcopy(snapshot)
        if "diff" in manifest:
            manifest.pop("diff")
            manifest["diffOmittedFromGraph"] = True
        manifests.append(manifest)
    result["workspaceChanges"] = manifests
    return minimized


def compact_loop_outcome_for_transport(outcome: object) -> object:
    """Bound legacy outcomes that may still contain pre-v0.43 diff bodies."""

    if not isinstance(outcome, dict):
        return deepcopy(outcome)
    compact = deepcopy(outcome)
    result = compact.get("result")
    if not isinstance(result, dict):
        return compact
    snapshots = result.get("workspaceChanges")
    if not isinstance(snapshots, list):
        return compact
    compact_snapshots: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        manifest = deepcopy(snapshot)
        if "diff" in manifest:
            manifest.pop("diff")
            manifest["diffOmittedFromTransport"] = True
        compact_snapshots.append(manifest)
    result["workspaceChanges"] = compact_snapshots
    return compact


def compact_run_for_transport(run: dict[str, Any]) -> dict[str, Any]:
    compact = deepcopy(run)
    for node in compact.get("nodes", []):
        if isinstance(node, dict):
            node["outcome"] = compact_loop_outcome_for_transport(
                node.get("outcome")
            )
    return compact


__all__ = (
    "compact_loop_outcome_for_transport",
    "compact_run_for_transport",
    "minimize_loop_outcome_for_graph",
)
