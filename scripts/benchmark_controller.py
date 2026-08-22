from __future__ import annotations

import argparse
import json
import math
import platform
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any, Callable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from hdg.entry_routing import decide_entry_route
from hdg.graph_frontier import get_graph_frontier
from hdg.planning_hierarchy import freeze_hierarchy, prepare_hierarchy
from hdg.planning_status import workspace_status


BENCHMARK_VERSION = 1
DEFAULT_BUDGETS_MS = {
    "entryRouter": 10.0,
    "prepareAndFreeze": 1000.0,
    "workspaceStatus": 100.0,
    "graphFrontier": 250.0,
}
_BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _at(minutes: int) -> datetime:
    return _BASE_TIME + timedelta(minutes=minutes)


def _loop_descriptor(ref: str) -> dict[str, Any]:
    return {
        "ref": ref,
        "payload": {
            "goal": "Complete one synthetic controller operation.",
            "acceptance": ["The operation returns a valid result."],
        },
        "resourceClaims": [],
    }


def _task_hierarchy() -> dict[str, Any]:
    task_id = "t-benchmark"
    task = {
        "schemaVersion": 3,
        "id": task_id,
        "kind": "TASK",
        "parentId": None,
        "title": "Run synthetic benchmark task",
        "summary": "Exercise the controller without a business repository.",
        "execution": {
            "dependsOn": [],
            "loop": _loop_descriptor("benchmark/task-loop@1"),
        },
    }
    return {
        "delivery": {
            "id": "d-controller-benchmark",
            "title": "Benchmark controller",
            "summary": "Measure deterministic controller critical paths.",
            "reviewLoop": _loop_descriptor("benchmark/delivery-review@1"),
        },
        "root": {
            "schemaVersion": 3,
            "skillHints": [],
            "definition": task,
            "reviewLoop": _loop_descriptor("benchmark/task-review@1"),
            "children": [],
        },
    }


def _prepare_and_freeze(root: str) -> str:
    prepared = prepare_hierarchy(
        root=root,
        hierarchy=_task_hierarchy(),
        now=_at(0),
    )
    root_id = prepared["rootId"]
    freeze_hierarchy(
        root=root,
        root_id=root_id,
        expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
        confirmed=True,
        confirmed_by="synthetic-benchmark",
        now=_at(1),
    )
    return root_id


def _validate_sample_counts(iterations: int, warmup: int) -> None:
    if not isinstance(iterations, int) or isinstance(iterations, bool):
        raise ValueError("iterations must be a positive integer")
    if iterations <= 0:
        raise ValueError("iterations must be a positive integer")
    if not isinstance(warmup, int) or isinstance(warmup, bool):
        raise ValueError("warmup must be a non-negative integer")
    if warmup < 0:
        raise ValueError("warmup must be a non-negative integer")


def _summarize_durations(
    durations: Sequence[float],
    *,
    budget_ms: float,
) -> dict[str, Any]:
    if not durations:
        raise ValueError("at least one measured duration is required")
    ordered_ms = sorted(duration * 1000.0 for duration in durations)
    p95_index = max(0, math.ceil(len(ordered_ms) * 0.95) - 1)
    p95_ms = ordered_ms[p95_index]
    return {
        "iterations": len(ordered_ms),
        "totalMs": round(sum(ordered_ms), 3),
        "meanMs": round(fmean(ordered_ms), 3),
        "p95Ms": round(p95_ms, 3),
        "maxMs": round(ordered_ms[-1], 3),
        "budgetMs": round(budget_ms, 3),
        "passed": p95_ms <= budget_ms,
    }


def _measure(
    operation: Callable[[], object],
    *,
    iterations: int,
    warmup: int,
    budget_ms: float,
) -> dict[str, Any]:
    for _ in range(warmup):
        operation()
    durations = []
    for _ in range(iterations):
        started = perf_counter()
        operation()
        durations.append(perf_counter() - started)
    return _summarize_durations(durations, budget_ms=budget_ms)


def _router_operation() -> dict[str, Any]:
    return decide_entry_route(
        request_text="继续执行当前交付",
        workspace_state={
            "status": "ACTIVE",
            "rootId": "d-controller-benchmark",
        },
    )


def _prepare_operation() -> str:
    with TemporaryDirectory(prefix="hdg-controller-benchmark-") as root:
        return _prepare_and_freeze(root)


def run_benchmark(
    *,
    iterations: int = 10,
    warmup: int = 2,
    budget_scale: float = 1.0,
) -> dict[str, Any]:
    """Run synthetic controller scenarios and return a JSON-safe report."""

    _validate_sample_counts(iterations, warmup)
    if (
        not isinstance(budget_scale, (int, float))
        or isinstance(budget_scale, bool)
        or not math.isfinite(budget_scale)
        or budget_scale <= 0
    ):
        raise ValueError("budget_scale must be a positive finite number")

    def budget(name: str) -> float:
        return DEFAULT_BUDGETS_MS[name] * float(budget_scale)

    scenarios = {
        "entryRouter": _measure(
            _router_operation,
            iterations=iterations,
            warmup=warmup,
            budget_ms=budget("entryRouter"),
        ),
        "prepareAndFreeze": _measure(
            _prepare_operation,
            iterations=iterations,
            warmup=warmup,
            budget_ms=budget("prepareAndFreeze"),
        ),
    }
    with TemporaryDirectory(prefix="hdg-controller-benchmark-") as root:
        root_id = _prepare_and_freeze(root)
        scenarios["workspaceStatus"] = _measure(
            lambda: workspace_status(
                root=root,
                root_id=root_id,
                now=_at(2),
            ),
            iterations=iterations,
            warmup=warmup,
            budget_ms=budget("workspaceStatus"),
        )
        scenarios["graphFrontier"] = _measure(
            lambda: get_graph_frontier(
                root=root,
                root_id=root_id,
                now=_at(3),
            ),
            iterations=iterations,
            warmup=warmup,
            budget_ms=budget("graphFrontier"),
        )

    return {
        "benchmarkVersion": BENCHMARK_VERSION,
        "kind": "SYNTHETIC_CONTROLLER_BENCHMARK",
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
        "configuration": {
            "iterations": iterations,
            "warmup": warmup,
            "budgetScale": float(budget_scale),
        },
        "scenarios": scenarios,
        "passed": all(item["passed"] for item in scenarios.values()),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the delivery-graph synthetic controller benchmark.",
    )
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--budget-scale", type=float, default=1.0)
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit compact JSON instead of indented JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_benchmark(
        iterations=args.iterations,
        warmup=args.warmup,
        budget_scale=args.budget_scale,
    )
    if args.compact:
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
