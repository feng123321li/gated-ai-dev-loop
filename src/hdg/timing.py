from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import os
import sys
from time import perf_counter
from typing import Any, Iterator


@dataclass
class TimingCollector:
    """Collect opt-in controller timings without changing stdout contracts."""

    command: str
    started_at: float = field(default_factory=perf_counter)
    stages: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started_at = perf_counter()
        try:
            yield
        finally:
            duration_ms = (perf_counter() - started_at) * 1000
            existing = next(
                (stage for stage in self.stages if stage["name"] == name),
                None,
            )
            if existing is None:
                self.stages.append({
                    "name": name,
                    "durationMs": round(duration_ms, 3),
                    "count": 1,
                })
            else:
                existing["durationMs"] = round(
                    existing["durationMs"] + duration_ms,
                    3,
                )
                existing["count"] += 1

    def metric(self, name: str, value: Any) -> None:
        self.metrics[name] = value

    def result(self, *, ok: bool) -> dict[str, Any]:
        return {
            "command": self.command,
            "ok": ok,
            "totalMs": round((perf_counter() - self.started_at) * 1000, 3),
            "stages": self.stages,
            "metrics": self.metrics,
        }


_ACTIVE_TIMING: ContextVar[TimingCollector | None] = ContextVar(
    "hdg_active_timing",
    default=None,
)


@contextmanager
def timing_session(
    *,
    command: str,
    enabled: bool,
) -> Iterator[TimingCollector | None]:
    collector = TimingCollector(command) if enabled else None
    token = _ACTIVE_TIMING.set(collector)
    try:
        yield collector
    finally:
        _ACTIVE_TIMING.reset(token)


@contextmanager
def timed_stage(name: str) -> Iterator[None]:
    collector = _ACTIVE_TIMING.get()
    if collector is None:
        yield
        return
    with collector.stage(name):
        yield


def timing_metric(name: str, value: Any) -> None:
    collector = _ACTIVE_TIMING.get()
    if collector is not None:
        collector.metric(name, value)


def timing_increment(name: str, amount: int = 1) -> None:
    collector = _ACTIVE_TIMING.get()
    if collector is not None:
        collector.metric(name, collector.metrics.get(name, 0) + amount)


def controller_timing_enabled() -> bool:
    return os.environ.get("HDG_TIMING", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def emit_controller_timing(
    collector: TimingCollector,
    *,
    ok: bool,
) -> None:
    event = {"event": "controller.timing", **collector.result(ok=ok)}
    try:
        sys.stderr.write(
            json.dumps(
                event,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        sys.stderr.flush()
    except (OSError, TypeError, ValueError):
        return


__all__ = (
    "TimingCollector",
    "controller_timing_enabled",
    "emit_controller_timing",
    "timed_stage",
    "timing_increment",
    "timing_metric",
    "timing_session",
)
