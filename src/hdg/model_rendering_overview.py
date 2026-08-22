from __future__ import annotations

from .model_rendering_common import (
    Any,
    OVERVIEW_PROJECTION_TEMPLATE,
    PROJECTION_TEMPLATE_VERSION,
    WORKSPACE_OVERVIEW_PROJECTION_TEMPLATE,
    _markdown_text,
    _status_text,
    _utc_plus_8,
    _work_item_terminal_node_id,
    iter_hierarchy_nodes,
)


def render_workspace_overview(
    deliveries: list[dict[str, Any]],
) -> str:
    """Render the workspace-wide Delivery summary from SQLite-loaded state."""

    ordered = sorted(
        deliveries,
        key=lambda item: (
            (
                item["run"]["updatedAt"]
                if item.get("run") is not None
                else item["updatedAt"]
            ),
            item["rootId"],
        ),
        reverse=True,
    )
    rows: list[str] = []
    for item in ordered:
        state_error = item.get("stateError")
        hierarchy = item.get("hierarchy")
        run = item.get("run")
        status = (
            run["status"]
            if run is not None
            else item.get("queueState", item["status"])
        )
        updated_at = (
            run["updatedAt"] if run is not None else item["updatedAt"]
        )
        cells = [
            item["rootId"],
            (
                "调度状态不可读取"
                if state_error is not None
                else hierarchy["delivery"]["title"]
            ),
            _status_text(status),
            (
                item.get("deliveryClosure", {}).get("label", "—")
                if state_error is None
                else "—"
            ),
            _utc_plus_8(updated_at),
        ]
        detail_link = (
            _markdown_text(f"需修复 SQLite 状态（{state_error['code']}）")
            if state_error is not None
            else f"[查看交付详情]({item['rootId']}/overview.md)"
        )
        rows.append(
            "| "
            + " | ".join(
                [
                    *(_markdown_text(cell) for cell in cells),
                    detail_link,
                ]
            )
            + " |"
        )
    latest_update = max(
        (
            (
                item["run"]["updatedAt"]
                if item.get("run") is not None
                else item["updatedAt"]
            )
            for item in deliveries
        ),
        default=None,
    )
    return WORKSPACE_OVERVIEW_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        delivery_count=str(len(deliveries)),
        updated_at=_utc_plus_8(latest_update),
        delivery_rows=("\n".join(rows) or "| 无 | 无 | 无 | 无 | 无 |"),
    )


def render_scheduling_plan(
    hierarchy: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
    run: dict[str, Any] | None = None,
    delivery_closure: dict[str, Any] | None = None,
) -> str:
    """Render the concise Delivery overview and projection navigation."""

    hierarchy_nodes = iter_hierarchy_nodes(hierarchy)
    definitions = [node["definition"] for node in hierarchy_nodes]
    tasks = [item for item in definitions if item["kind"] == "TASK"]
    groups = [item for item in definitions if item["kind"] == "GROUP"]
    states = {
        node["nodeId"]: node["status"]
        for node in (run or {}).get("nodes", [])
    }
    completed_tasks = sum(
        states.get(_work_item_terminal_node_id(node))
        in {"SUCCEEDED", "COMPLETED"}
        for node in hierarchy_nodes
        if node["definition"]["kind"] == "TASK"
    )
    archived = hierarchy_status == "ARCHIVED"
    status = (
        "ARCHIVED"
        if archived
        else (
            run["status"]
            if run is not None
            else hierarchy_status or "UNKNOWN"
        )
    )
    latest_update = updated_at if archived or run is None else run["updatedAt"]
    delivery = hierarchy["delivery"]
    status_row = "| " + " | ".join(
        _markdown_text(value)
        for value in (
            delivery["id"],
            delivery["title"],
            _status_text(status),
            (delivery_closure or {}).get("label", "未上线"),
            f"已完成 {completed_tasks}/{len(tasks)}",
            str(len(groups)),
            _utc_plus_8(latest_update),
        )
    ) + " |"
    return OVERVIEW_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        delivery_status="\n".join(
            [
                (
                    "| 交付标识 | 标题 | 当前阶段 | 上线状态 | TASK 进度 | "
                    "GROUP 数量 | 最近更新（UTC+8） |"
                ),
                "|---|---|---|---|---|---:|---|",
                status_row,
            ]
        ),
    )


__all__ = ("render_scheduling_plan", "render_workspace_overview")
