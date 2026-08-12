from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import ceil
from types import MappingProxyType
from typing import Any

from .errors import GatedLoopError, fail
from .jsonio import fingerprint


PROGRESS_PHASES = (
    "STARTING",
    "INSPECTING",
    "TESTING",
    "INVESTIGATING",
    "FIXING",
    "REVIEWING",
    "VERIFYING",
    "WAITING",
)
PROGRESS_PHASE_TEXT = MappingProxyType(
    {
        "STARTING": "准备执行",
        "INSPECTING": "检查代码",
        "TESTING": "运行测试",
        "INVESTIGATING": "分析问题",
        "FIXING": "修复问题",
        "REVIEWING": "复审",
        "VERIFYING": "最终验证",
        "WAITING": "等待内部操作",
    }
)
LOOP_KIND_TEXT = MappingProxyType(
    {
        "TASK_LOOP": "任务执行",
        "TASK_REVIEW_LOOP": "任务复审",
        "GROUP_REVIEW_LOOP": "分组复审",
        "DELIVERY_REVIEW_LOOP": "交付复审",
    }
)
STATE_PHASE_TEXT = MappingProxyType(
    {
        "PENDING": "等待依赖",
        "READY": "等待领取",
        "CLAIMED": "已领取",
        "SUCCEEDED": "已完成",
        "BLOCKED": "已阻塞",
        "PAUSED": "已暂停",
        "CANCELLED": "已取消",
        "COMPLETED": "已完成",
    }
)
STATE_HEALTH_TEXT = MappingProxyType(
    {
        "PENDING": "等待依赖",
        "READY": "等待领取",
        "SUCCEEDED": "已完成",
        "BLOCKED": "已阻塞",
        "PAUSED": "已暂停",
        "CANCELLED": "已取消",
        "COMPLETED": "已完成",
    }
)

FIRST_HEARTBEAT_WARNING_SECONDS = 90
PROGRESS_WARNING_SECONDS = 5 * 60
RECOMMENDED_POLL_SECONDS = FIRST_HEARTBEAT_WARNING_SECONDS


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )


def _human_text(
    value: object,
    *,
    field: str,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        fail(
            "SCHEDULER_PROGRESS_INVALID",
            f"{field} must be a user-visible progress description",
            field=field,
        )
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(
            ord(character) < 32 and character not in {"\n", "\t"}
            for character in normalized
        )
    ):
        fail(
            "SCHEDULER_PROGRESS_INVALID",
            f"{field} is empty, too long, or contains control characters",
            field=field,
        )
    return normalized


def normalize_progress_payload(
    *,
    phase: object,
    summary_zh: object,
    completed_zh: object = None,
    next_step_zh: object = None,
    progress_percent: object = None,
    tests: object = None,
) -> dict[str, Any]:
    if phase not in PROGRESS_PHASES:
        fail(
            "SCHEDULER_PROGRESS_PHASE_INVALID",
            "phase must be one of the supported Loop progress phases",
            supportedPhases=list(PROGRESS_PHASES),
        )
    summary = _human_text(
        summary_zh,
        field="summary_zh",
        maximum=500,
    )
    if completed_zh is None:
        completed_values: list[str] = []
    elif not isinstance(completed_zh, list) or len(completed_zh) > 8:
        fail(
            "SCHEDULER_PROGRESS_INVALID",
            "completed_zh must contain at most eight milestones",
            field="completed_zh",
        )
    else:
        completed_values = [
            _human_text(
                item,
                field=f"completed_zh[{index}]",
                maximum=200,
            )
            for index, item in enumerate(completed_zh)
        ]
    next_step = (
        _human_text(
            next_step_zh,
            field="next_step_zh",
            maximum=300,
        )
        if next_step_zh is not None
        else None
    )
    if progress_percent is not None and (
        not isinstance(progress_percent, int)
        or isinstance(progress_percent, bool)
        or not 0 <= progress_percent <= 100
    ):
        fail(
            "SCHEDULER_PROGRESS_INVALID",
            "progress_percent must be an integer from 0 through 100",
            field="progress_percent",
        )
    test_values = None
    if tests is not None:
        fields = {"passed", "failed", "skipped", "total"}
        if not isinstance(tests, dict) or set(tests) != fields:
            fail(
                "SCHEDULER_PROGRESS_INVALID",
                "tests must contain passed, failed, skipped, and total",
                field="tests",
            )
        if any(
            not isinstance(tests[field], int)
            or isinstance(tests[field], bool)
            or tests[field] < 0
            or tests[field] > 1_000_000
            for field in fields
        ):
            fail(
                "SCHEDULER_PROGRESS_INVALID",
                "test counters must be non-negative integers",
                field="tests",
            )
        counted_tests = (
            tests["passed"] + tests["failed"] + tests["skipped"]
        )
        if counted_tests > tests["total"]:
            fail(
                "SCHEDULER_PROGRESS_INVALID",
                "test counters cannot exceed the reported total",
                field="tests",
            )
        test_values = {field: tests[field] for field in sorted(fields)}
    return {
        "phase": str(phase),
        "summaryZh": summary,
        "completedZh": completed_values,
        **({"nextStepZh": next_step} if next_step is not None else {}),
        **(
            {"progressPercent": progress_percent}
            if progress_percent is not None
            else {}
        ),
        **({"tests": test_values} if test_values is not None else {}),
    }


def validate_progress_event_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        fail(
            "SCHEDULER_EVENT_REPLAY_INVALID",
            "Loop progress event payload must be an object",
        )
    try:
        normalized = normalize_progress_payload(
            phase=payload.get("phase"),
            summary_zh=payload.get("summaryZh"),
            completed_zh=payload.get("completedZh"),
            next_step_zh=payload.get("nextStepZh"),
            progress_percent=payload.get("progressPercent"),
            tests=payload.get("tests"),
        )
    except GatedLoopError:
        fail(
            "SCHEDULER_EVENT_REPLAY_INVALID",
            "Loop progress event payload is invalid",
        )
    if normalized != payload:
        fail(
            "SCHEDULER_EVENT_REPLAY_INVALID",
            "Loop progress event payload is not canonical",
        )
    return normalized


def _parse_timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _seconds_between(later: datetime, earlier: str | None) -> int | None:
    parsed = _parse_timestamp(earlier)
    if parsed is None:
        return None
    return max(0, int((later - parsed).total_seconds()))


def _duration_zh(seconds: int | None) -> str:
    if seconds is None:
        return "未知"
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 60 * 60:
        return f"{seconds // 60} 分钟"
    hours, remainder = divmod(seconds, 60 * 60)
    minutes = remainder // 60
    return f"{hours} 小时 {minutes} 分钟" if minutes else f"{hours} 小时"


def _markdown_text(value: object) -> str:
    text = str(value)
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    for character in ("\\", "`", "*", "_", "[", "]", "#", "|"):
        text = text.replace(character, f"\\{character}")
    return (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "<br>")
    )


def _tests_zh(progress: dict[str, Any] | None) -> str:
    tests = progress.get("tests") if isinstance(progress, dict) else None
    if not isinstance(tests, dict):
        return "未报告"
    return (
        f"{tests['passed']}/{tests['total']} 通过；"
        f"失败 {tests['failed']}；跳过 {tests['skipped']}"
    )


def _progress_summary(
    state: dict[str, Any],
) -> tuple[str, str, str, str, str]:
    progress = state.get("progress")
    if not isinstance(progress, dict):
        return (
            STATE_PHASE_TEXT.get(state["status"], "状态未知"),
            "尚未报告业务进度",
            "无",
            "等待 Agent 报告下一步",
            "未报告",
        )
    completed = progress.get("completedZh")
    phase_zh = PROGRESS_PHASE_TEXT.get(
        progress.get("phase"),
        "其他阶段",
    )
    percentage = progress.get("progressPercent")
    if isinstance(percentage, int) and not isinstance(percentage, bool):
        phase_zh = f"{phase_zh}（{percentage}%）"
    return (
        phase_zh,
        str(progress.get("summaryZh") or "尚未报告业务进度"),
        "；".join(completed) if isinstance(completed, list) and completed else "无",
        str(progress.get("nextStepZh") or "尚未报告"),
        _tests_zh(progress),
    )


def _claimed_health(
    state: dict[str, Any],
    *,
    observed: datetime,
    heartbeat_stale_seconds: int,
) -> tuple[str, str, str | None]:
    claim_age = _seconds_between(observed, state.get("claimedAt"))
    heartbeat_age = _seconds_between(observed, state.get("lastHeartbeatAt"))
    first_heartbeat = state.get("firstHeartbeatAt")
    progress = state.get("progress")
    progress_at = (
        progress.get("reportedAt") if isinstance(progress, dict) else None
    )
    progress_age = _seconds_between(
        observed,
        progress_at or state.get("claimedAt"),
    )
    lease = _parse_timestamp(state.get("leaseExpiresAt"))
    if lease is not None and lease <= observed:
        return (
            "LEASE_EXPIRED_PENDING_RECOVERY",
            "租约已过期，等待调度器回收",
            "租约已过期，应由 graph_frontier 自动进入失联恢复流程。",
        )
    if (
        first_heartbeat is None
        and (claim_age or 0) >= FIRST_HEARTBEAT_WARNING_SECONDS
    ):
        if isinstance(progress, dict):
            return (
                "HEARTBEAT_MISSING",
                "已开始但无独立心跳",
                "已有业务进度，但领取后仍没有首次独立心跳。",
            )
        return (
            "SUSPECT_NOT_STARTED",
            "疑似未启动",
            "领取后超过 90 秒仍没有首次独立心跳。",
        )
    if (heartbeat_age or 0) >= heartbeat_stale_seconds:
        if (progress_age or 0) >= heartbeat_stale_seconds:
            return (
                "SUSPECT_LOST",
                "疑似失联",
                "心跳和业务进度均已长时间停止。",
            )
        return (
            "HEARTBEAT_STALE",
            "进度有更新但心跳异常",
            "仍有业务进度，但心跳已超过预期窗口。",
        )
    if (progress_age or 0) >= PROGRESS_WARNING_SECONDS:
        return (
            "ALIVE_WITHOUT_PROGRESS",
            "存活但无可见进展",
            "心跳仍正常，但超过 5 分钟没有业务进度。",
        )
    return ("HEALTHY", "运行正常", None)


def _next_health_deadline(
    state: dict[str, Any],
    *,
    health: str,
    observed: datetime,
    heartbeat_stale_seconds: int,
) -> datetime | None:
    candidates: list[datetime] = []
    lease = _parse_timestamp(state.get("leaseExpiresAt"))
    if lease is not None and lease > observed:
        candidates.append(lease)
    claimed = _parse_timestamp(state.get("claimedAt"))
    heartbeat = _parse_timestamp(state.get("lastHeartbeatAt"))
    progress = state.get("progress")
    progress_at = _parse_timestamp(
        progress.get("reportedAt") if isinstance(progress, dict) else None
    )
    progress_base = progress_at or claimed
    if health == "HEALTHY":
        if state.get("firstHeartbeatAt") is None and claimed is not None:
            candidates.append(
                claimed + timedelta(seconds=FIRST_HEARTBEAT_WARNING_SECONDS)
            )
        else:
            if heartbeat is not None:
                candidates.append(
                    heartbeat + timedelta(seconds=heartbeat_stale_seconds)
                )
            if progress_base is not None:
                candidates.append(
                    progress_base + timedelta(seconds=PROGRESS_WARNING_SECONDS)
                )
    elif health == "HEARTBEAT_STALE" and progress_base is not None:
        candidates.append(
            progress_base + timedelta(seconds=heartbeat_stale_seconds)
        )
    elif health == "ALIVE_WITHOUT_PROGRESS" and heartbeat is not None:
        candidates.append(
            heartbeat + timedelta(seconds=heartbeat_stale_seconds)
        )
    future = [candidate for candidate in candidates if candidate > observed]
    return min(future, default=None)


def build_progress_monitor(
    run: dict[str, Any],
    graph: dict[str, Any],
    *,
    observed_at: str,
) -> dict[str, Any]:
    observed = _parse_timestamp(observed_at)
    if observed is None:
        fail("TIME_INVALID", "observed_at must be an ISO 8601 timestamp")
    definitions = {item["id"]: item for item in graph["nodes"]}
    claim_policy = graph["runtime"]["claimPolicy"]
    heartbeat_stale_seconds = int(claim_policy["heartbeatSeconds"]) + int(
        claim_policy["graceSeconds"]
    )
    rows: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    health_deadlines: list[datetime] = []
    for state in run["nodes"]:
        definition = definitions[state["nodeId"]]
        kind_text = LOOP_KIND_TEXT.get(definition["kind"])
        if kind_text is None:
            continue
        phase_zh, summary_zh, completed_zh, next_step_zh, tests_zh = (
            _progress_summary(state)
        )
        if state["status"] == "CLAIMED":
            health, health_zh, alert_message = _claimed_health(
                state,
                observed=observed,
                heartbeat_stale_seconds=heartbeat_stale_seconds,
            )
            health_deadline = _next_health_deadline(
                state,
                health=health,
                observed=observed,
                heartbeat_stale_seconds=heartbeat_stale_seconds,
            )
            if health_deadline is not None:
                health_deadlines.append(health_deadline)
            if state.get("firstHeartbeatAt") is None:
                heartbeat_zh = (
                    "尚无独立心跳；领取 "
                    f"{_duration_zh(_seconds_between(observed, state.get('claimedAt')))}"
                    "前"
                )
            else:
                heartbeat_zh = (
                    "最后心跳 "
                    f"{_duration_zh(_seconds_between(observed, state.get('lastHeartbeatAt')))}"
                    "前"
                )
            lease = _parse_timestamp(state.get("leaseExpiresAt"))
            lease_seconds = (
                max(0, int((lease - observed).total_seconds()))
                if lease is not None
                else None
            )
            heartbeat_zh += f"；租约剩余 {_duration_zh(lease_seconds)}"
            if alert_message is not None:
                alert: dict[str, Any] = {
                    "nodeId": state["nodeId"],
                    "code": health,
                    "messageZh": alert_message,
                }
                if health == "SUSPECT_LOST":
                    alert["diagnosis"] = {
                        "claimMatched": True,
                        "cause": "UNDETERMINED_CONTROL_PLANE_SILENCE",
                        "hostProcessAlive": None,
                        "safeRecovery": "WAIT_FOR_LEASE_EXPIRY",
                    }
                alerts.append(alert)
        else:
            health = state["status"]
            health_zh = STATE_HEALTH_TEXT.get(state["status"], "状态未知")
            heartbeat_zh = "不适用"
        agent_id = state.get("agentId") or state.get("owner") or "未分配"
        actual_model_id = state.get("actualModelId") or "未报告"
        row = {
            "nodeId": state["nodeId"],
            "displayNameZh": f"{definition['workItemId']} · {kind_text}",
            "attempt": state["attempt"],
            "agentId": state.get("agentId"),
            "actualModelId": state.get("actualModelId"),
            "actualModelSource": state.get("actualModelSource"),
            "executorZh": (
                f"第 {state['attempt']} 轮 · {agent_id} · "
                f"宿主观测模型 {actual_model_id}"
            ),
            "phaseZh": phase_zh,
            "summaryZh": summary_zh,
            "completedZh": completed_zh,
            "nextStepZh": next_step_zh,
            "testsZh": tests_zh,
            "heartbeatZh": heartbeat_zh,
            "health": health,
            "healthZh": health_zh,
        }
        rows.append(row)
    header = (
        "| 节点 | 执行器 | 当前阶段 | 当前说明 | 已完成 | 下一步 | "
        "测试 | 心跳与租约 | 健康状态 |"
    )
    separator = "|---|---|---|---|---|---|---|---|---|"
    rendered_rows = [
        "| "
        + " | ".join(
            _markdown_text(row[key])
            for key in (
                "displayNameZh",
                "executorZh",
                "phaseZh",
                "summaryZh",
                "completedZh",
                "nextStepZh",
                "testsZh",
                "heartbeatZh",
                "healthZh",
            )
        )
        + " |"
        for row in rows
    ]
    meaningful_rows = [
        {
            key: row.get(key)
            for key in (
                "nodeId",
                "attempt",
                "agentId",
                "actualModelId",
                "phaseZh",
                "summaryZh",
                "completedZh",
                "nextStepZh",
                "testsZh",
                "health",
            )
        }
        for row in rows
    ]
    meaningful_node_states = [
        {
            "nodeId": state["nodeId"],
            "attempt": state["attempt"],
            "status": state["status"],
        }
        for state in run["nodes"]
    ]
    active_receiver = any(
        state["status"] == "CLAIMED"
        and definitions[state["nodeId"]]["kind"] in LOOP_KIND_TEXT
        for state in run["nodes"]
    )
    advance_required = any(
        alert.get("code") == "LEASE_EXPIRED_PENDING_RECOVERY"
        for alert in alerts
    )
    next_health_deadline = min(health_deadlines, default=None)
    poll_not_before = (
        observed_at
        if advance_required
        else _timestamp(next_health_deadline)
        if active_receiver and next_health_deadline is not None
        else None
    )
    recommended_poll_seconds = (
        0
        if advance_required
        else max(
            1,
            ceil((next_health_deadline - observed).total_seconds()),
        )
        if active_receiver and next_health_deadline is not None
        else RECOMMENDED_POLL_SECONDS
    )
    return {
        "observedAt": observed_at,
        "recommendedPollSeconds": recommended_poll_seconds,
        "changeFingerprint": fingerprint(
            {
                "alerts": alerts,
                "runStatus": run["status"],
                "nodeStates": meaningful_node_states,
                "rows": meaningful_rows,
            }
        ),
        "waitDirective": {
            "mode": (
                "ADVANCE_REQUIRED"
                if advance_required
                else "HOST_NATIVE_EVENT_OR_DEADLINE"
                if active_receiver
                else "NO_ACTIVE_RECEIVER"
            ),
            "pollNotBefore": poll_not_before,
            "pollTool": (
                "graph_frontier"
                if advance_required
                else "graph_status"
                if active_receiver
                else None
            ),
            "advanceTool": "graph_frontier",
            "interruptOn": (
                [
                    "NATIVE_RECEIVER_COMPLETED",
                    "NATIVE_RECEIVER_NEEDS_ATTENTION",
                ]
                if active_receiver
                else []
            ),
            "onInterrupt": (
                "CALL_GRAPH_FRONTIER_ONCE" if active_receiver else "NONE"
            ),
            "onTimeout": (
                "CALL_GRAPH_FRONTIER_ONCE"
                if advance_required
                else "CALL_GRAPH_STATUS_ONCE"
                if active_receiver
                else "NONE"
            ),
            "consumeActionsBeforeWaiting": False,
            "immediateActions": [],
            "nextWakeAt": None,
            "onNextWakeAt": "CALL_GRAPH_FRONTIER_ONCE",
            "suppressUnchangedCommentary": True,
        },
        "alerts": alerts,
        "rows": rows,
        "markdownTable": "\n".join([header, separator, *rendered_rows]),
    }


def attach_progress_monitor(
    run: dict[str, Any],
    graph: dict[str, Any],
    *,
    observed_at: str,
) -> dict[str, Any]:
    enriched = deepcopy(run)
    monitor = build_progress_monitor(enriched, graph, observed_at=observed_at)
    monitor_by_node = {row["nodeId"]: row for row in monitor["rows"]}
    for state in enriched["nodes"]:
        if state["nodeId"] in monitor_by_node:
            state["monitor"] = monitor_by_node[state["nodeId"]]
    enriched["progressMonitor"] = monitor
    return enriched


__all__ = (
    "FIRST_HEARTBEAT_WARNING_SECONDS",
    "PROGRESS_PHASES",
    "PROGRESS_PHASE_TEXT",
    "PROGRESS_WARNING_SECONDS",
    "RECOMMENDED_POLL_SECONDS",
    "attach_progress_monitor",
    "build_progress_monitor",
    "normalize_progress_payload",
    "validate_progress_event_payload",
)
