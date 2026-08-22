from __future__ import annotations

from typing import Any

from .errors import GatedLoopError, fail
from .repository import SchedulerRepository
from .supervisor_profiles import (
    build_supervisor_routing,
    built_in_supervisor_registry,
    load_supervisor_registry,
)


ENTRY_ROUTER_VERSION = 3
_RUNTIME_STATUSES = frozenset(
    {"ACTIVE", "BLOCKED", "PAUSED", "QUEUED", "HANDOFF_READY"}
)
_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ARCHIVE_DELIVERY", ("归档", "archive delivery", "archive")),
    (
        "CLOSE_DELIVERY",
        ("关闭交付", "标记上线", "已经上线", "已上线", "close delivery"),
    ),
    (
        "CONFIRM_REVISION",
        ("确认完成", "确认验收", "验收通过", "confirm revision", "accept revision"),
    ),
    (
        "REPLAN",
        ("重新规划", "修改需求", "需求变更", "修订需求", "replan", "revise requirement"),
    ),
    (
        "RESUME_PAUSED",
        ("恢复执行", "恢复任务", "恢复", "resume"),
    ),
    (
        "QUERY_STATUS",
        ("查看状态", "当前状态", "查看进度", "当前进度", "看板", "status", "progress"),
    ),
    (
        "NEW_DELIVERY",
        ("新需求", "新建交付", "新的交付", "new delivery", "new requirement"),
    ),
    (
        "CONTINUE_DELIVERY",
        ("继续执行", "继续", "接着", "continue"),
    ),
)


def _classify_explicit_intent(request_text: str) -> str | None:
    normalized = " ".join(request_text.casefold().split())
    for intent, patterns in _PATTERNS:
        if any(pattern in normalized for pattern in patterns):
            return intent
    return None


def _decision(
    *,
    intent: str,
    status: str,
    root_id: str | None,
    target_skill: str | None,
    allowed: bool,
    reason_codes: list[str],
    requires_clarification: bool = False,
    candidate_root_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "routerVersion": ENTRY_ROUTER_VERSION,
        "intent": intent,
        "targetSkill": target_skill,
        "rootId": root_id,
        "observedStatus": status,
        "allowed": allowed,
        "requiresClarification": requires_clarification,
        "reasonCodes": reason_codes,
        "candidateRootIds": candidate_root_ids or [],
        "decisionBasis": "DETERMINISTIC_RULE_AND_STATE",
        "fallback": (
            "NONE"
            if allowed
            else "MODEL_CLASSIFICATION_OR_USER_CONFIRMATION"
        ),
    }


def _state_conflict(
    intent: str,
    status: str,
    root_id: str | None,
    *reason_codes: str,
) -> dict[str, Any]:
    return _decision(
        intent=intent,
        status=status,
        root_id=root_id,
        target_skill=None,
        allowed=False,
        requires_clarification=True,
        reason_codes=["ROUTE_STATE_CONFLICT", *reason_codes],
    )


def _decide_entry_route(
    *,
    request_text: str,
    workspace_state: dict[str, Any],
) -> dict[str, Any]:
    """Return a deterministic route or an explicit ambiguity decision."""

    if not isinstance(request_text, str) or not request_text.strip():
        fail(
            "ENTRY_ROUTE_INVALID",
            "request_text must be non-empty",
        )
    if not isinstance(workspace_state, dict):
        fail(
            "ENTRY_ROUTE_INVALID",
            "workspace_state must be an object",
        )
    status = str(workspace_state.get("status", "ABSENT"))
    root_value = workspace_state.get("rootId")
    root_id = root_value if isinstance(root_value, str) else None
    explicit_intent = _classify_explicit_intent(request_text)

    if explicit_intent == "NEW_DELIVERY":
        return _decision(
            intent="NEW_DELIVERY",
            status=status,
            root_id=None,
            target_skill="delivery-graph",
            allowed=True,
            reason_codes=["EXPLICIT_NEW_DELIVERY"],
        )

    if status == "DELIVERY_SELECTION_REQUIRED":
        candidate_root_ids = sorted(
            item["rootId"]
            for item in workspace_state.get("candidateDeliveries", [])
            if isinstance(item, dict) and isinstance(item.get("rootId"), str)
        )
        return _decision(
            intent="SELECT_DELIVERY",
            status=status,
            root_id=None,
            target_skill="delivery-graph",
            allowed=False,
            requires_clarification=True,
            reason_codes=["MULTIPLE_DELIVERIES_REQUIRE_EXPLICIT_ROOT"],
            candidate_root_ids=candidate_root_ids,
        )

    if explicit_intent is None:
        return _decision(
            intent="AMBIGUOUS",
            status=status,
            root_id=root_id,
            target_skill=None,
            allowed=False,
            requires_clarification=True,
            reason_codes=["NO_HIGH_CONFIDENCE_ENTRY_RULE"],
        )

    if explicit_intent == "QUERY_STATUS":
        target = (
            "delivery-graph-dispatch"
            if status in _RUNTIME_STATUSES
            else "delivery-graph"
        )
        return _decision(
            intent="QUERY_STATUS",
            status=status,
            root_id=root_id,
            target_skill=target,
            allowed=True,
            reason_codes=["EXPLICIT_STATUS_QUERY", f"STATE_{status}"],
        )

    if explicit_intent == "RESUME_PAUSED":
        if status == "PAUSED":
            return _decision(
                intent="RESUME_PAUSED",
                status=status,
                root_id=root_id,
                target_skill="delivery-graph-dispatch",
                allowed=True,
                reason_codes=["EXPLICIT_RESUME", "STATE_PAUSED"],
            )
        if status in {"ACTIVE", "BLOCKED"}:
            return _decision(
                intent="DISPATCH_ACTIVE",
                status=status,
                root_id=root_id,
                target_skill="delivery-graph-dispatch",
                allowed=True,
                reason_codes=["EXPLICIT_RESUME", f"STATE_{status}"],
            )
        return _state_conflict(
            "RESUME_PAUSED",
            status,
            root_id,
            "RESUME_REQUIRES_PAUSED_OR_ACTIVE_RUN",
        )

    if explicit_intent == "CONTINUE_DELIVERY":
        if status in {"ACTIVE", "BLOCKED", "QUEUED", "HANDOFF_READY"}:
            return _decision(
                intent="DISPATCH_ACTIVE",
                status=status,
                root_id=root_id,
                target_skill="delivery-graph-dispatch",
                allowed=True,
                reason_codes=["EXPLICIT_CONTINUE", f"STATE_{status}"],
            )
        if status == "PAUSED":
            return _decision(
                intent="RESUME_PAUSED",
                status=status,
                root_id=root_id,
                target_skill="delivery-graph-dispatch",
                allowed=True,
                reason_codes=["EXPLICIT_CONTINUE", "STATE_PAUSED"],
            )
        if status in {"CHOICE_READY", "PREPARED", "COMPLETED"}:
            return _decision(
                intent="CONTINUE_DELIVERY",
                status=status,
                root_id=root_id,
                target_skill="delivery-graph",
                allowed=True,
                reason_codes=["EXPLICIT_CONTINUE", f"STATE_{status}"],
            )
        return _state_conflict(
            "CONTINUE_DELIVERY",
            status,
            root_id,
            "CONTINUE_REQUIRES_EXISTING_DELIVERY",
        )

    if explicit_intent == "REPLAN":
        if status not in {"ACTIVE", "BLOCKED", "PAUSED", "COMPLETED"}:
            return _state_conflict(
                "REPLAN",
                status,
                root_id,
                "REPLAN_REQUIRES_EXISTING_DELIVERY",
            )
        return _decision(
            intent="REPLAN",
            status=status,
            root_id=root_id,
            target_skill="delivery-graph",
            allowed=True,
            reason_codes=["EXPLICIT_REPLAN", f"STATE_{status}"],
        )

    if explicit_intent == "CONFIRM_REVISION":
        if workspace_state.get("nextAction") != "RECORD_USER_CONFIRMATION":
            return _state_conflict(
                "CONFIRM_REVISION",
                status,
                root_id,
                "REVISION_CONFIRMATION_NOT_READY",
            )
        return _decision(
            intent="CONFIRM_REVISION",
            status=status,
            root_id=root_id,
            target_skill="delivery-graph",
            allowed=True,
            reason_codes=["EXPLICIT_CONFIRMATION", "CONFIRMATION_READY"],
        )

    if explicit_intent == "CLOSE_DELIVERY":
        if status != "COMPLETED":
            return _state_conflict(
                "CLOSE_DELIVERY",
                status,
                root_id,
                "CLOSE_REQUIRES_COMPLETED_REVISION",
            )
        return _decision(
            intent="CLOSE_DELIVERY",
            status=status,
            root_id=root_id,
            target_skill="delivery-graph",
            allowed=True,
            reason_codes=["EXPLICIT_CLOSE", "STATE_COMPLETED"],
        )

    if explicit_intent == "ARCHIVE_DELIVERY":
        if not (
            status == "ARCHIVED"
            or workspace_state.get("deliveryClosure") == "CLOSED"
        ):
            return _state_conflict(
                "ARCHIVE_DELIVERY",
                status,
                root_id,
                "ARCHIVE_REQUIRES_CLOSED_DELIVERY",
            )
        return _decision(
            intent="ARCHIVE_DELIVERY",
            status=status,
            root_id=root_id,
            target_skill="delivery-graph",
            allowed=True,
            reason_codes=["EXPLICIT_ARCHIVE", "DELIVERY_CLOSED"],
        )

    return _state_conflict(
        explicit_intent,
        status,
        root_id,
        "UNSUPPORTED_ENTRY_INTENT",
    )


def decide_entry_route(
    *,
    request_text: str,
    workspace_state: dict[str, Any],
    supervisor_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the deterministic route plus optional decision-only supervision."""

    decision = _decide_entry_route(
        request_text=request_text,
        workspace_state=workspace_state,
    )
    explicit_intent = _classify_explicit_intent(request_text) or "AMBIGUOUS"
    registry = supervisor_registry or built_in_supervisor_registry()
    return {
        **decision,
        "supervisorRouting": build_supervisor_routing(
            registry,
            explicit_intent=explicit_intent,
            route_decision=decision,
        ),
    }


def route_entry_intent(
    *,
    root: str,
    request_text: str,
    root_id: str | None = None,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
) -> dict[str, Any]:
    """Read authoritative workspace state and return one entry decision."""

    repository = SchedulerRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    state = repository.workspace_status(
        root_id=root_id,
        workspace_root=workspace_root or root,
    )
    selected_root_id = state.get("rootId")
    if isinstance(selected_root_id, str) and state.get("status") not in {
        "ARCHIVED",
        "COMPLETED",
    }:
        try:
            stored = repository.hierarchy(selected_root_id)
            run = repository.run(selected_root_id)
        except GatedLoopError as error:
            if error.code != "SCHEDULER_RUN_MISSING":
                raise
            stored = None
            run = None
        if isinstance(stored, dict) and isinstance(run, dict):
            confirmation_ids = {
                item["id"]
                for item in stored["graph"]["nodes"]
                if item["kind"] == "USER_CONFIRMATION"
            }
            if any(
                item.get("nodeId") in confirmation_ids
                and item.get("status") == "READY"
                for item in run.get("nodes", [])
            ):
                state["nextAction"] = "RECORD_USER_CONFIRMATION"
    supervisor_registry = load_supervisor_registry(workspace_root or root)
    return decide_entry_route(
        request_text=request_text,
        workspace_state=state,
        supervisor_registry=supervisor_registry,
    )


__all__ = (
    "ENTRY_ROUTER_VERSION",
    "decide_entry_route",
    "route_entry_intent",
)
