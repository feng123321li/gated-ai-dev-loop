from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


EXECUTION_METRICS_VERSION = 1


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _seconds_between(start: object, end: object) -> float | None:
    started = _timestamp(start)
    finished = _timestamp(end)
    if started is None or finished is None or finished < started:
        return None
    return round((finished - started).total_seconds(), 3)


def _critical_path(
    graph: dict[str, Any],
    duration_by_node: dict[str, float],
    loop_node_ids: set[str],
) -> tuple[float, list[str]]:
    node_ids = [
        item["id"]
        for item in graph.get("nodes", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    outgoing = {node_id: [] for node_id in node_ids}
    indegree = {node_id: 0 for node_id in node_ids}
    for edge in graph.get("edges", []):
        if not isinstance(edge, dict):
            continue
        source = edge.get("source")
        target = edge.get("target")
        if source not in outgoing or target not in indegree:
            continue
        outgoing[source].append(target)
        indegree[target] += 1
    ready = sorted(
        node_id for node_id, count in indegree.items() if count == 0
    )
    best_seconds = {
        node_id: duration_by_node.get(node_id, 0.0)
        for node_id in node_ids
    }
    best_paths = {
        node_id: ([node_id] if node_id in loop_node_ids else [])
        for node_id in node_ids
    }
    visited: list[str] = []
    while ready:
        node_id = ready.pop(0)
        visited.append(node_id)
        for target in sorted(outgoing[node_id]):
            candidate_seconds = round(
                best_seconds[node_id]
                + duration_by_node.get(target, 0.0),
                3,
            )
            candidate_path = [
                *best_paths[node_id],
                *([target] if target in loop_node_ids else []),
            ]
            if (
                candidate_seconds > best_seconds[target]
                or (
                    candidate_seconds == best_seconds[target]
                    and tuple(candidate_path) < tuple(best_paths[target])
                )
            ):
                best_seconds[target] = candidate_seconds
                best_paths[target] = candidate_path
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    if len(visited) != len(node_ids) or not node_ids:
        return 0.0, []
    terminal = min(
        node_ids,
        key=lambda node_id: (
            -best_seconds[node_id],
            tuple(best_paths[node_id]),
            node_id,
        ),
    )
    return best_seconds[terminal], best_paths[terminal]


def build_execution_metrics(
    graph: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    """Derive deterministic wall-time and Graph critical-path metrics."""

    definitions = {
        item["id"]: item
        for item in graph.get("nodes", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    states = {
        item["nodeId"]: item
        for item in run.get("nodes", [])
        if isinstance(item, dict) and isinstance(item.get("nodeId"), str)
    }
    loop_node_ids = {
        node_id
        for node_id, item in definitions.items()
        if str(item.get("kind", "")).endswith("_LOOP")
    }
    durations: list[dict[str, Any]] = []
    duration_by_node: dict[str, float] = {}
    raw_attempts = run.get("attempts")
    attempts_by_node: dict[str, list[dict[str, Any]]] = {}
    if isinstance(raw_attempts, list):
        for attempt in raw_attempts:
            if (
                isinstance(attempt, dict)
                and isinstance(attempt.get("nodeId"), str)
                and attempt["nodeId"] in loop_node_ids
            ):
                attempts_by_node.setdefault(attempt["nodeId"], []).append(
                    attempt
                )
    else:
        for node_id, state in states.items():
            if node_id in loop_node_ids:
                attempts_by_node[node_id] = [state]
    measured_attempts = 0
    unmeasured_attempts = 0
    retried_loops = 0
    for node_id in sorted(loop_node_ids):
        state = states.get(node_id, {})
        attempts = sorted(
            attempts_by_node.get(node_id, []),
            key=lambda item: (
                item.get("attempt")
                if isinstance(item.get("attempt"), int)
                else 0
            ),
        )
        attempt_durations = [
            _seconds_between(
                attempt.get("claimedAt"),
                attempt.get("finishedAt"),
            )
            for attempt in attempts
        ]
        measured = [item for item in attempt_durations if item is not None]
        measured_attempts += len(measured)
        unmeasured_attempts += len(attempt_durations) - len(measured)
        if len(attempts) > 1:
            retried_loops += 1
        duration = round(sum(measured), 3) if measured else None
        if duration is not None:
            duration_by_node[node_id] = duration
        definition = definitions[node_id]
        durations.append(
            {
                "nodeId": node_id,
                "kind": definition["kind"],
                "workItemId": definition["workItemId"],
                "attempt": state.get("attempt"),
                "attemptCount": len(attempts),
                "measuredAttempts": len(measured),
                "status": state.get("status", "MISSING"),
                "durationSeconds": duration,
            }
        )
    recorded_loop_seconds = round(sum(duration_by_node.values()), 3)
    critical_path_seconds, critical_path = _critical_path(
        graph,
        duration_by_node,
        loop_node_ids,
    )
    end_at = (
        run.get("completedAt")
        or run.get("cancelledAt")
        or run.get("supersededAt")
        or run.get("updatedAt")
    )
    run_elapsed = _seconds_between(run.get("startedAt"), end_at)
    measured_loops = len(duration_by_node)
    return {
        "metricsVersion": EXECUTION_METRICS_VERSION,
        "runElapsedSeconds": run_elapsed,
        "recordedLoopSeconds": recorded_loop_seconds,
        "measuredLoops": measured_loops,
        "unmeasuredLoops": len(loop_node_ids) - measured_loops,
        "measuredAttempts": measured_attempts,
        "unmeasuredAttempts": unmeasured_attempts,
        "retriedLoops": retried_loops,
        "criticalPathSeconds": round(critical_path_seconds, 3),
        "criticalPathLoopIds": critical_path,
        "parallelizableLoopSeconds": round(
            max(recorded_loop_seconds - critical_path_seconds, 0.0),
            3,
        ),
        "slowestLoops": sorted(
            (
                item
                for item in durations
                if item["durationSeconds"] is not None
            ),
            key=lambda item: (
                -item["durationSeconds"],
                item["nodeId"],
            ),
        )[:5],
    }


__all__ = (
    "EXECUTION_METRICS_VERSION",
    "build_execution_metrics",
)
