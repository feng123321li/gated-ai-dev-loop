from __future__ import annotations

from datetime import datetime, timedelta, timezone
import posixpath
from string import Template
from types import MappingProxyType
from typing import Any

from .graph_model import (
    confirmation_node_id,
    group_review_node_id,
    join_node_id,
    loop_node_id,
    review_node_id,
    task_review_node_id,
)
from .model_core import iter_hierarchy_nodes


KIND_TEXT = {
    "GROUP": "分组",
    "TASK": "任务",
}
STATUS_TEXT = {
    "PREPARED": "待冻结",
    "FROZEN": "已冻结",
    "ACTIVE": "运行中",
    "PENDING": "等待依赖",
    "READY": "可执行",
    "CLAIMED": "执行中",
    "SUCCEEDED": "已成功",
    "BLOCKED": "已阻塞",
    "REPLAN_REQUIRED": "需要重新规划",
    "PAUSED": "已暂停",
    "CANCELLED": "已取消",
    "COMPLETED": "已完成",
    "NOT_STARTED": "未启动",
    "UNKNOWN": "未知",
    "UNAVAILABLE": "不可用",
}
FAILURE_CLASS_TEXT = {
    "RETRYABLE_INFRA": "可重试的基础设施故障",
    "WORKER_LOST": "执行上下文失联",
    "REPLAN_REQUIRED": "需要重新规划",
    "NON_RETRYABLE": "不可重试故障",
}
INTERFACE_CHANGE_TYPE_TEXT = {
    "CREATE": "新增",
    "MODIFY": "修改",
    "DELETE": "删除",
}
REVIEW_FINDING_STATUS_TEXT = {
    "RESOLVED": "已修复",
    "ACCEPTED": "已接受",
    "OPEN": "待处理",
}
PAYLOAD_FIELD_TEXT = MappingProxyType(
    {
        "acceptance": "验收标准",
        "acceptanceCriteria": "验收标准",
        "assumptions": "前提假设",
        "businessRules": "业务规则",
        "constraints": "约束条件",
        "context": "背景信息",
        "deliverables": "交付物",
        "deliveryId": "交付标识",
        "dependencies": "依赖",
        "description": "说明",
        "evidence": "验证证据",
        "fields": "字段",
        "files": "相关文件",
        "goal": "目标",
        "identifier": "调用标识",
        "inputs": "输入",
        "interfaces": "接口契约",
        "method": "方法",
        "modules": "相关模块",
        "name": "名称",
        "nested": "嵌套信息",
        "notes": "补充说明",
        "output": "输出",
        "outputs": "输出",
        "path": "路径",
        "process": "处理流程",
        "protocol": "协议",
        "rawAuditMarker": "原始审计标记",
        "request": "入参",
        "required": "必填",
        "requirements": "需求说明",
        "response": "出参",
        "reviewFocus": "审查重点",
        "risks": "风险",
        "scope": "处理范围",
        "service": "服务",
        "services": "相关服务",
        "summary": "简介",
        "steps": "执行步骤",
        "successCriteria": "成功标准",
        "testRequirements": "测试要求",
        "tests": "测试要求",
        "type": "类型",
        "workItemId": "工作项标识",
    }
)
PAYLOAD_FIELD_ORDER = MappingProxyType(
    {
        key: index
        for index, key in enumerate(
            (
                "goal",
                "reviewFocus",
                "context",
                "requirements",
                "businessRules",
                "process",
                "steps",
                "inputs",
                "interfaces",
                "protocol",
                "name",
                "summary",
                "method",
                "path",
                "service",
                "request",
                "response",
                "fields",
                "type",
                "required",
                "description",
                "outputs",
                "deliverables",
                "dependencies",
                "constraints",
                "acceptance",
                "acceptanceCriteria",
                "successCriteria",
                "tests",
                "testRequirements",
                "evidence",
                "risks",
                "assumptions",
                "notes",
                "rawAuditMarker",
                "nested",
            )
        )
    }
)
UTC_PLUS_8 = timezone(timedelta(hours=8))
PROJECTION_TEMPLATE_VERSION = 7
WORK_ITEM_DIRECTORY = "work-items"
WORKSPACE_OVERVIEW_PROJECTION_TEMPLATE = Template(
    """# 全部交付调度与进度总览

## 工作区状态

- 交付数量：${delivery_count}
- 更新时间（UTC+8）：${updated_at}

本文件由控制器从 SQLite 权威状态统一生成，用于查看工作区内全部交付需求。

## Delivery 清单

| 交付标识 | 需求标题 | 当前状态 | 最近更新（UTC+8） | 交付详情 |
|---|---|---|---|---|
${delivery_rows}
"""
)
OVERVIEW_PROJECTION_TEMPLATE = Template(
    """# 交付文档总览

## 交付状态

${delivery_status}

## 投影导航

| 投影 | 用途 |
|---|---|
| [需求基线](baseline.md) | 需求、层级、依赖、Loop 输入与 TASK baseline |
| [执行进展](progress.md) | TASK、TASK Review、递归 GROUP Review 与 Delivery Review 的运行状态 |
| [验收记录](acceptance.md) | 已知验收输入、执行结果、审查结果与用户确认 |

实现规范、测试、门禁与 Skill 激活由各 Loop 内部负责。机器权威仍为
SQLite 与事件链；本目录中的 Markdown 仅为控制器生成的人类投影。

"""
)
BASELINE_PROJECTION_TEMPLATE = Template(
    """# 交付需求基线

## 基线标识

${baseline_status}

## 关联投影

- [查看执行进展](progress.md)
- [查看验收记录](acceptance.md)

## Skill 提示

${skill_hints}

## GROUP/TASK 清单

| 层级路径 | 节点类型 | 上级 | 前置依赖 | 标题 | 需求基线 | 执行进展 | 验收记录 | 接口契约 |
|---|---|---|---|---|---|---|---|---|
${checklist_rows}

每个 GROUP/TASK 的详细摘要、Loop 引用、资源声明和结构化执行输入位于
对应的 `work-items/<root-id>/children/.../<node-id>/baseline.md`；
Delivery 本文件只串联基线树，
不重复聚合节点级输入。

## Delivery 审查输入

${delivery_review_baseline}
"""
)
PROGRESS_PROJECTION_TEMPLATE = Template(
    """# 交付执行进展

## 运行状态

${progress_status}

## TASK 执行进展

${task_progress}
## GROUP 协调与审查进展

${group_progress}
## Delivery 审查进展

${delivery_review_progress}
"""
)
ACCEPTANCE_PROJECTION_TEMPLATE = Template(
    """# 交付验收记录

## 验收状态

${acceptance_status}

## 根工作项验收

${root_acceptance}
## Delivery 审查验收

${delivery_acceptance}
## 最终用户确认

${confirmation}
"""
)
INTERFACES_PROJECTION_TEMPLATE = Template(
    """# TASK 接口契约

## 接口基线

${interface_status}
- TASK 需求基线：[返回 TASK 基线](baseline.md)
- Delivery 需求基线：[返回 Delivery 基线](${delivery_baseline})

本文件只确定性投影本 TASK Loop payload 中显式声明的 `interfaces`；
接口声明不参与 Graph 调度决策。

## 接口清单

${interface_rows}
## 接口详情

${interface_details}
"""
)
TASK_BASELINE_PROJECTION_TEMPLATE = Template(
    """# TASK 调度基线

## 基线标识

- 交付标识：${delivery_id}
- 交付标题：${delivery_title}
- 任务标识：${task_id}
- 层级状态：${hierarchy_status}
- 层级指纹：${hierarchy_fingerprint}
- 调度图指纹：${graph_fingerprint}
- 更新时间（UTC+8）：${updated_at}

## 关联投影

- [Delivery 需求基线](${delivery_baseline})
${parent_baseline}
- [本 TASK 执行进展](progress.md)
- [本 TASK 验收记录](acceptance.md)

## 任务定义

- 标题：${task_title}
- 摘要：${task_summary}
- 上级：${parent_id}
- 前置依赖：${dependencies}

## 执行 Loop

- Loop 引用：${loop_ref}
- 资源锁：${resource_claims}

## 执行输入

${payload}

${interface_section}

${review_section}

## 共享 Skill 提示

${skill_hints}

实现方案、文件、测试、Gate、修正循环和实际 Skill 选择由本 TASK Loop
在独立执行上下文中负责；本文件只投影控制器可见的冻结调度输入。
"""
)
GROUP_BASELINE_PROJECTION_TEMPLATE = Template(
    """# GROUP 调度基线

## 基线标识

- 交付标识：${delivery_id}
- 交付标题：${delivery_title}
- 分组标识：${group_id}
- 层级状态：${hierarchy_status}
- 层级指纹：${hierarchy_fingerprint}
- 调度图指纹：${graph_fingerprint}
- 更新时间（UTC+8）：${updated_at}

## 关联投影

- [Delivery 需求基线](${delivery_baseline})
${parent_baseline}
- [本 GROUP 执行进展](progress.md)
- [本 GROUP 验收记录](acceptance.md)

## 分组定义

- 标题：${group_title}
- 摘要：${group_summary}
- 上级：${parent_id}
- 前置依赖：${dependencies}

## 直接子节点基线

| 节点类型 | 节点标识 | 标题 | 需求基线 | 执行进展 | 验收记录 |
|---|---|---|---|---|---|
${child_rows}

${review_section}

## 共享 Skill 提示

${skill_hints}

本文件只投影控制器可见的冻结 GROUP 协调与 Review 输入；子节点的详细
输入位于各自 baseline。
"""
)
WORK_ITEM_PROGRESS_PROJECTION_TEMPLATE = Template(
    """# ${kind_text} 执行进展

## 节点状态

${item_status}

## 关联投影

- [节点需求基线](baseline.md)
- [节点验收记录](acceptance.md)

${progress_sections}
"""
)
WORK_ITEM_ACCEPTANCE_PROJECTION_TEMPLATE = Template(
    """# ${kind_text} 验收记录

## 节点状态

${item_status}

## 关联投影

- [节点需求基线](baseline.md)
- [节点执行进展](progress.md)

${acceptance_sections}
"""
)
PROJECTION_TEMPLATES = MappingProxyType(
    {
        "overview.md": OVERVIEW_PROJECTION_TEMPLATE,
        "baseline.md": BASELINE_PROJECTION_TEMPLATE,
        "progress.md": PROGRESS_PROJECTION_TEMPLATE,
        "acceptance.md": ACCEPTANCE_PROJECTION_TEMPLATE,
    }
)


def _status_text(value: str) -> str:
    return STATUS_TEXT.get(value, "未知状态")


def _failure_class_text(value: str) -> str:
    return FAILURE_CLASS_TEXT.get(value, "其他故障")


def _utc_plus_8(value: str | None) -> str:
    if not value:
        return _status_text("UNAVAILABLE")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(UTC_PLUS_8).strftime("%Y-%m-%d %H:%M:%S")


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


def _payload_field_text(key: str) -> str:
    return PAYLOAD_FIELD_TEXT.get(key, key)


def _payload_field_sort_key(key: str) -> tuple[int, str]:
    return (PAYLOAD_FIELD_ORDER.get(key, len(PAYLOAD_FIELD_ORDER)), key)


def _payload_scalar_text(value: object) -> str:
    if value is None:
        return "无"
    if value is True:
        return "是"
    if value is False:
        return "否"
    if isinstance(value, str) and not value:
        return "（空）"
    return _markdown_text(value)


def _payload_value_lines(
    value: object,
    *,
    indent: int = 0,
) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{prefix}- 无"]
        lines: list[str] = []
        for key in sorted(value, key=_payload_field_sort_key):
            child = value[key]
            label = _markdown_text(_payload_field_text(key))
            if not isinstance(child, (dict, list)):
                lines.append(
                    f"{prefix}- **{label}**："
                    f"{_payload_scalar_text(child)}"
                )
                continue
            lines.append(f"{prefix}- **{label}**")
            lines.extend(
                _payload_value_lines(child, indent=indent + 2)
            )
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}- 无"]
        lines = []
        for index, child in enumerate(value, start=1):
            if not isinstance(child, (dict, list)):
                lines.append(
                    f"{prefix}- {_payload_scalar_text(child)}"
                )
                continue
            lines.append(f"{prefix}- **第 {index} 项**")
            lines.extend(
                _payload_value_lines(child, indent=indent + 2)
            )
        return lines
    return [f"{prefix}- {_payload_scalar_text(value)}"]


def _render_payload_markdown(
    payload: dict[str, Any],
    *,
    heading_level: int,
) -> str:
    if not payload:
        return "- 无"
    level = max(1, min(heading_level, 6))
    lines: list[str] = []
    for key in sorted(payload, key=_payload_field_sort_key):
        lines.extend(
            [
                (
                    f"{'#' * level} "
                    f"{_markdown_text(_payload_field_text(key))}"
                ),
                "",
                *_payload_value_lines(payload[key]),
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def raw_definition(
    definition: dict[str, Any],
) -> dict[str, Any]:
    return dict(definition)


def work_item_projection_directories(
    hierarchy: dict[str, Any],
) -> dict[str, str]:
    """Map work-item IDs to recursive directories below a Delivery."""

    directories: dict[str, str] = {}

    def visit(node: dict[str, Any], parent: str | None) -> None:
        item_id = node["definition"]["id"]
        directory = (
            f"{WORK_ITEM_DIRECTORY}/{item_id}"
            if parent is None
            else f"{parent}/children/{item_id}"
        )
        directories[item_id] = directory
        for child in node["children"]:
            visit(child, directory)

    visit(hierarchy["root"], None)
    return directories


def work_item_projection_relative_path(
    hierarchy: dict[str, Any],
    work_item_id: str,
    filename: str,
) -> str:
    directory = work_item_projection_directories(hierarchy)[work_item_id]
    return f"{directory}/{filename}"


def task_baseline_relative_path(
    hierarchy: dict[str, Any],
    task_id: str,
) -> str:
    return work_item_projection_relative_path(
        hierarchy,
        task_id,
        "baseline.md",
    )


def _task_interface_declarations(
    definition: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return structurally declared interfaces without validating semantics."""

    if definition["kind"] != "TASK":
        return []
    value = definition["execution"]["loop"]["payload"].get("interfaces")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def has_interface_projection(hierarchy: dict[str, Any]) -> bool:
    """Return whether any TASK declares a projectable interface."""

    return any(
        task_has_interface_projection(node["definition"])
        for node in iter_hierarchy_nodes(hierarchy)
    )


def task_has_interface_projection(
    definition: dict[str, Any],
) -> bool:
    """Return whether one TASK declares a projectable interface."""

    return bool(_task_interface_declarations(definition))


def _interface_scalar(
    value: object,
    *,
    fallback: str,
) -> str:
    if isinstance(value, (str, int, float)) and str(value).strip():
        return str(value).strip()
    return fallback


def _interface_call_identifier(
    protocol: str,
    snapshot: dict[str, Any] | None,
) -> str:
    if snapshot is None:
        return "不适用"
    if protocol == "HTTP":
        method = _interface_scalar(
            snapshot.get("method"),
            fallback="未声明方法",
        ).upper()
        path = _interface_scalar(
            snapshot.get("path"),
            fallback="未声明路径",
        )
        return f"{method} {path}"
    if protocol == "DUBBO":
        service = _interface_scalar(
            snapshot.get("service"),
            fallback="未声明服务",
        )
        method = _interface_scalar(
            snapshot.get("method"),
            fallback="未声明方法",
        )
        return f"{service}.{method}"
    return _interface_scalar(
        snapshot.get("identifier"),
        fallback="未声明",
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
        hierarchy = item["hierarchy"]
        run = item.get("run")
        status = (
            run["status"]
            if run is not None
            else item["status"]
        )
        updated_at = (
            run["updatedAt"]
            if run is not None
            else item["updatedAt"]
        )
        cells = [
            item["rootId"],
            hierarchy["delivery"]["title"],
            _status_text(status),
            _utc_plus_8(updated_at),
        ]
        detail_link = (
            f"[查看交付详情]({item['rootId']}/overview.md)"
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
    latest_update = (
        max(
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
    )
    return WORKSPACE_OVERVIEW_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        delivery_count=str(len(deliveries)),
        updated_at=_utc_plus_8(latest_update),
        delivery_rows=(
            "\n".join(rows)
            or "| 无 | 无 | 无 | 无 | 无 |"
        ),
    )


def render_work_item_baseline(
    node: dict[str, Any],
    *,
    delivery: dict[str, Any] | None = None,
    delivery_baseline: str = "../../baseline.md",
    skill_hints: list[dict[str, str]] | None = None,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
) -> str:
    """Render one TASK baseline from scheduler-visible metadata.

    Loop payloads remain semantically opaque and are structurally projected
    through a fixed human-readable Markdown renderer.
    """

    definition = node["definition"]
    if definition["kind"] != "TASK":
        raise ValueError("TASK baseline requires a TASK definition")
    loop = definition["execution"]["loop"]
    review_loop = node["reviewLoop"]
    hints = skill_hints or []
    rendered_hints = (
        "\n".join(
            f"- {_markdown_text(hint['name'])}："
            f"{_markdown_text(hint['purpose'])}"
            for hint in hints
        )
        if hints
        else "- 无"
    )
    delivery = delivery or {}
    interface_declarations = _task_interface_declarations(definition)
    parent_baseline = (
        "- [上级节点需求基线](../../baseline.md)"
        if definition["parentId"]
        else "- 上级节点需求基线：无（Delivery 根工作项）"
    )
    return TASK_BASELINE_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        delivery_id=_markdown_text(delivery.get("id", "不可用")),
        delivery_title=_markdown_text(
            delivery.get("title", "不可用")
        ),
        task_id=_markdown_text(definition["id"]),
        hierarchy_status=_status_text(
            hierarchy_status or "UNKNOWN"
        ),
        hierarchy_fingerprint=(
            hierarchy_fingerprint or "不可用"
        ),
        graph_fingerprint=graph_fingerprint or "不可用",
        updated_at=_utc_plus_8(updated_at),
        delivery_baseline=delivery_baseline,
        parent_baseline=parent_baseline,
        task_title=_markdown_text(definition["title"]),
        task_summary=_markdown_text(definition["summary"]),
        parent_id=_markdown_text(definition["parentId"] or "无"),
        dependencies=(
            "、".join(
                _markdown_text(item)
                for item in definition["execution"]["dependsOn"]
            )
            or "无"
        ),
        loop_ref=_markdown_text(loop["ref"]),
        resource_claims=(
            "、".join(
                _markdown_text(item)
                for item in loop["resourceClaims"]
            )
            or "无"
        ),
        payload=_render_payload_markdown(
            {
                key: value
                for key, value in loop["payload"].items()
                if key != "interfaces" or not interface_declarations
            },
            heading_level=3,
        ),
        interface_section=(
            "## 关联接口契约\n\n"
            "[查看本 TASK 的接口契约](interfaces.md)"
            if interface_declarations
            else ""
        ),
        review_section=_render_loop_baseline(
            "TASK Review Loop",
            review_loop,
            heading_level=2,
        ),
        skill_hints=rendered_hints,
    )


def render_group_baseline(
    node: dict[str, Any],
    *,
    delivery: dict[str, Any] | None = None,
    delivery_baseline: str = "../../baseline.md",
    skill_hints: list[dict[str, str]] | None = None,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
) -> str:
    definition = node["definition"]
    if definition["kind"] != "GROUP":
        raise ValueError("GROUP baseline requires a GROUP hierarchy node")
    loop = node["reviewLoop"]
    hints = skill_hints or []
    rendered_hints = (
        "\n".join(
            f"- {_markdown_text(hint['name'])}："
            f"{_markdown_text(hint['purpose'])}"
            for hint in hints
        )
        if hints
        else "- 无"
    )
    parent_baseline = (
        "- [上级节点需求基线](../../baseline.md)"
        if definition["parentId"]
        else "- 上级节点需求基线：无（Delivery 根工作项）"
    )
    child_rows = []
    for child in node["children"]:
        child_definition = child["definition"]
        child_id = child_definition["id"]
        child_rows.append(
            "| "
            + " | ".join(
                [
                    KIND_TEXT[child_definition["kind"]],
                    _markdown_text(child_id),
                    _markdown_text(child_definition["title"]),
                    f"[查看](children/{child_id}/baseline.md)",
                    f"[查看](children/{child_id}/progress.md)",
                    f"[查看](children/{child_id}/acceptance.md)",
                ]
            )
            + " |"
        )
    delivery = delivery or {}
    return GROUP_BASELINE_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        delivery_id=_markdown_text(delivery.get("id", "不可用")),
        delivery_title=_markdown_text(
            delivery.get("title", "不可用")
        ),
        group_id=_markdown_text(definition["id"]),
        hierarchy_status=_status_text(
            hierarchy_status or "UNKNOWN"
        ),
        hierarchy_fingerprint=hierarchy_fingerprint or "不可用",
        graph_fingerprint=graph_fingerprint or "不可用",
        updated_at=_utc_plus_8(updated_at),
        delivery_baseline=delivery_baseline,
        parent_baseline=parent_baseline,
        group_title=_markdown_text(definition["title"]),
        group_summary=_markdown_text(definition["summary"]),
        parent_id=_markdown_text(definition["parentId"] or "无"),
        dependencies=(
            "、".join(
                _markdown_text(item)
                for item in definition["decomposition"]["dependsOn"]
            )
            or "无"
        ),
        child_rows="\n".join(child_rows),
        review_section=_render_loop_baseline(
            "GROUP Review Loop",
            loop,
            heading_level=2,
        ),
        skill_hints=rendered_hints,
    )


def render_scheduling_plan(
    hierarchy: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
    run: dict[str, Any] | None = None,
) -> str:
    """Render the concise Delivery overview and projection navigation."""

    definitions = [
        node["definition"]
        for node in iter_hierarchy_nodes(hierarchy)
    ]
    tasks = [
        definition
        for definition in definitions
        if definition["kind"] == "TASK"
    ]
    groups = [
        definition
        for definition in definitions
        if definition["kind"] == "GROUP"
    ]
    states = {
        node["nodeId"]: node["status"]
        for node in (run or {}).get("nodes", [])
    }
    completed_tasks = sum(
        states.get(task_review_node_id(task["id"]))
        in {"SUCCEEDED", "COMPLETED"}
        for task in tasks
    )
    status = (
        run["status"]
        if run is not None
        else hierarchy_status or "UNKNOWN"
    )
    latest_update = (
        run["updatedAt"]
        if run is not None
        else updated_at
    )
    delivery = hierarchy["delivery"]
    status_row = "| " + " | ".join(
        _markdown_text(value)
        for value in (
            delivery["id"],
            delivery["title"],
            _status_text(status),
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
                    "| 交付标识 | 标题 | 当前状态 | TASK 进度 | "
                    "GROUP 数量 | 最近更新（UTC+8） |"
                ),
                "|---|---|---|---|---:|---|",
                status_row,
            ]
        ),
    )


def _projection_states(
    run: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    return {
        item["nodeId"]: item
        for item in (run or {}).get("nodes", [])
    }


def _work_item_terminal_node_id(node: dict[str, Any]) -> str:
    definition = node["definition"]
    if definition["kind"] == "TASK":
        return task_review_node_id(definition["id"])
    return group_review_node_id(definition["id"])


def _projection_state_values(
    states: dict[str, dict[str, Any]],
    node_id: str,
) -> dict[str, str]:
    state = states.get(node_id)
    if state is None:
        return {
            "nodeId": node_id,
            "status": _status_text("NOT_STARTED"),
            "attempt": "0",
            "owner": "无",
            "updatedAt": "无",
            "finishedAt": "无",
            "summary": "无",
        }
    outcome = state["outcome"]
    summary = "无"
    if isinstance(outcome, dict):
        if outcome.get("summary"):
            summary = str(outcome["summary"])
        elif outcome.get("confirmedBy"):
            summary = f"确认人：{outcome['confirmedBy']}"
    if (
        summary == "无"
        and state["status"] == "PAUSED"
        and isinstance(state.get("resumeAt"), str)
    ):
        summary = (
            f"等待至 {_utc_plus_8(state['resumeAt'])} 自动重新派遣"
        )
    if summary == "无" and state["failureClass"]:
        summary = (
            "失败分类："
            f"{_failure_class_text(state['failureClass'])}"
        )
    latest = (
        state["finishedAt"]
        or state["lastHeartbeatAt"]
        or state["claimedAt"]
    )
    return {
        "nodeId": node_id,
        "status": _status_text(state["status"]),
        "attempt": str(state["attempt"]),
        "owner": state["owner"] or "无",
        "updatedAt": _utc_plus_8(latest) if latest else "无",
        "finishedAt": (
            _utc_plus_8(state["finishedAt"])
            if state["finishedAt"]
            else "无"
        ),
        "summary": summary,
    }


def _table_row(
    values: list[object],
    *,
    raw_indices: set[int] | None = None,
) -> str:
    raw_indices = raw_indices or set()
    cells = [
        str(value) if index in raw_indices else _markdown_text(value)
        for index, value in enumerate(values)
    ]
    return "| " + " | ".join(cells) + " |"


def _progress_state_row(
    states: dict[str, dict[str, Any]],
    node_id: str,
    *,
    prefix: list[object] | None = None,
    suffix: list[object] | None = None,
) -> str:
    values = _projection_state_values(states, node_id)
    prefix_values = prefix or []
    suffix_values = suffix or []
    row_values = [
        *prefix_values,
        values["status"],
        values["attempt"],
        values["owner"],
        values["updatedAt"],
        values["summary"],
        *suffix_values,
    ]
    suffix_start = len(prefix_values) + 5
    return _table_row(
        row_values,
        raw_indices=set(range(suffix_start, len(row_values))),
    )


def _acceptance_state_table(
    states: dict[str, dict[str, Any]],
    node_id: str,
) -> list[str]:
    values = _projection_state_values(states, node_id)
    return [
        (
            "| 当前进度 | 尝试次数 | 执行者 | "
            "结束时间（UTC+8） | 结果摘要 |"
        ),
        "|---|---:|---|---|---|",
        _table_row(
            [
                values["status"],
                values["attempt"],
                values["owner"],
                values["finishedAt"],
                values["summary"],
            ]
        ),
    ]


def _render_loop_baseline(
    label: str,
    loop: dict[str, Any],
    *,
    heading_level: int,
) -> str:
    heading = "#" * max(1, min(heading_level, 6))
    payload_level = max(1, min(heading_level + 2, 6))
    claims = (
        "、".join(
            _markdown_text(item)
            for item in loop["resourceClaims"]
        )
        or "无"
    )
    return "\n".join(
        [
            f"{heading} {label}",
            "",
            f"- Loop 引用：{_markdown_text(loop['ref'])}",
            f"- 资源锁：{claims}",
            "",
            f"{heading}# 输入",
            "",
            _render_payload_markdown(
                loop["payload"],
                heading_level=payload_level,
            ),
        ]
    )


def _delivery_projection_status(
    hierarchy: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None,
    graph_fingerprint: str | None,
    hierarchy_status: str | None,
    updated_at: str | None,
    run: dict[str, Any] | None = None,
) -> list[str]:
    delivery = hierarchy["delivery"]
    lines = [
        f"- 交付标识：{_markdown_text(delivery['id'])}",
        f"- 标题：{_markdown_text(delivery['title'])}",
        f"- 摘要：{_markdown_text(delivery['summary'])}",
        f"- 数据结构版本：{hierarchy['root']['schemaVersion']}",
        f"- 层级状态：{_status_text(hierarchy_status or 'UNKNOWN')}",
        (
            "- 运行状态："
            f"{_status_text((run or {}).get('status', 'NOT_STARTED'))}"
        ),
        f"- 层级指纹：{hierarchy_fingerprint or '不可用'}",
        f"- 调度图指纹：{graph_fingerprint or '不可用'}",
    ]
    if run is not None:
        lines.extend(
            [
                f"- 运行标识：{_markdown_text(run['runId'])}",
                f"- 启动时间（UTC+8）：{_utc_plus_8(run['startedAt'])}",
            ]
        )
    lines.append(f"- 更新时间（UTC+8）：{_utc_plus_8(updated_at)}")
    return lines


def render_delivery_baseline(
    hierarchy: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
) -> str:
    hints = hierarchy["root"]["skillHints"]
    skill_hint_lines = (
        [
            f"- {_markdown_text(hint['name'])}："
            f"{_markdown_text(hint['purpose'])}"
            for hint in hints
        ]
        or ["- 无"]
    )
    indexed_nodes: list[tuple[dict[str, Any], str]] = []
    pending = [
        (
            hierarchy["root"],
            hierarchy["root"]["definition"]["id"],
        )
    ]
    while pending:
        node, path = pending.pop()
        indexed_nodes.append((node, path))
        pending.extend(
            (
                child,
                f"{path}/{child['definition']['id']}",
            )
            for child in reversed(node["children"])
        )

    checklist_rows: list[str] = []
    for node, path in indexed_nodes:
        definition = node["definition"]
        item_id = definition["id"]
        dependencies = (
            definition["execution"]["dependsOn"]
            if definition["kind"] == "TASK"
            else definition["decomposition"]["dependsOn"]
        )
        baseline_link = (
            f"[查看]("
            f"{work_item_projection_relative_path(hierarchy, item_id, 'baseline.md')}"
            ")"
        )
        progress_link = (
            f"[查看]("
            f"{work_item_projection_relative_path(hierarchy, item_id, 'progress.md')}"
            ")"
        )
        acceptance_link = (
            f"[查看]("
            f"{work_item_projection_relative_path(hierarchy, item_id, 'acceptance.md')}"
            ")"
        )
        interface_link = (
            f"[查看]("
            f"{work_item_projection_relative_path(hierarchy, item_id, 'interfaces.md')}"
            ")"
            if _task_interface_declarations(definition)
            else "无"
        )
        cells = [
            path,
            KIND_TEXT[definition["kind"]],
            definition["parentId"] or "无",
            "、".join(dependencies) or "无",
            definition["title"],
        ]
        checklist_rows.append(
            "| "
            + " | ".join(
                [
                    *(_markdown_text(cell) for cell in cells),
                    baseline_link,
                    progress_link,
                    acceptance_link,
                    interface_link,
                ]
            )
            + " |"
        )
    delivery_review = _render_loop_baseline(
        "交付审查 Loop",
        hierarchy["delivery"]["reviewLoop"],
        heading_level=3,
    )
    return BASELINE_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        baseline_status="\n".join(
            _delivery_projection_status(
                hierarchy,
                hierarchy_fingerprint=hierarchy_fingerprint,
                graph_fingerprint=graph_fingerprint,
                hierarchy_status=hierarchy_status,
                updated_at=updated_at,
            )
        ),
        skill_hints="\n".join(skill_hint_lines),
        checklist_rows="\n".join(checklist_rows),
        delivery_review_baseline=delivery_review,
    )


def render_delivery_progress(
    hierarchy: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
    run: dict[str, Any] | None = None,
) -> str:
    states = _projection_states(run)
    task_rows: list[str] = []
    group_rows: list[str] = []
    pending = [
        (
            hierarchy["root"],
            hierarchy["root"]["definition"]["id"],
        )
    ]
    indexed_nodes: list[tuple[dict[str, Any], str]] = []
    while pending:
        node, path = pending.pop()
        indexed_nodes.append((node, path))
        pending.extend(
            (
                child,
                f"{path}/{child['definition']['id']}",
            )
            for child in reversed(node["children"])
        )
    for node, path in indexed_nodes:
        definition = node["definition"]
        progress_link = (
            "[查看]("
            f"{work_item_projection_relative_path(hierarchy, definition['id'], 'progress.md')}"
            ")"
        )
        if definition["kind"] == "TASK":
            task_rows.extend(
                [
                    _progress_state_row(
                        states,
                        loop_node_id(definition["id"]),
                        prefix=[path, "TASK"],
                        suffix=[progress_link],
                    ),
                    _progress_state_row(
                        states,
                        task_review_node_id(definition["id"]),
                        prefix=[path, "TASK Review"],
                        suffix=[progress_link],
                    ),
                ]
            )
            continue
        group_rows.append(
            _progress_state_row(
                states,
                join_node_id(definition["id"]),
                prefix=[path, "GROUP 完成点"],
                suffix=[progress_link],
            )
        )
        group_rows.append(
            _progress_state_row(
                states,
                group_review_node_id(definition["id"]),
                prefix=[path, "GROUP Review"],
                suffix=[progress_link],
            )
        )
    table_header = (
        "| 层级路径 | 阶段 | 当前进度 | 尝试次数 | 执行者 | "
        "最近更新时间（UTC+8） | 结果摘要 | 节点进展 |"
    )
    table_separator = "|---|---|---|---:|---|---|---|---|"
    delivery_review_lines = [
        table_header,
        table_separator,
        _progress_state_row(
            states,
            review_node_id(hierarchy["delivery"]["id"]),
            prefix=[hierarchy["delivery"]["id"], "Delivery Review"],
            suffix=["[查看验收](acceptance.md)"],
        ),
    ]
    return PROGRESS_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        progress_status="\n".join(
            _delivery_projection_status(
                hierarchy,
                hierarchy_fingerprint=hierarchy_fingerprint,
                graph_fingerprint=graph_fingerprint,
                hierarchy_status=hierarchy_status,
                updated_at=updated_at,
                run=run,
            )
        ),
        task_progress="\n".join(
            [table_header, table_separator, *task_rows]
        ).rstrip()
        + "\n",
        group_progress=(
            "\n".join(
                [table_header, table_separator, *group_rows]
            ).rstrip()
            + "\n"
            if group_rows
            else "- 根工作项为 TASK，无 GROUP 协调节点。\n"
        ),
        delivery_review_progress="\n".join(delivery_review_lines),
    )


def _acceptance_payload(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "acceptance",
        "acceptanceCriteria",
        "successCriteria",
        "tests",
        "testRequirements",
    )
    return {key: payload[key] for key in keys if key in payload}


def _acceptance_result_lines(
    states: dict[str, dict[str, Any]],
    node_id: str,
    *,
    include_review_findings: bool = False,
) -> list[str]:
    lines = _acceptance_state_table(states, node_id)
    state = states.get(node_id)
    outcome = state.get("outcome") if state is not None else None
    result = outcome.get("result") if isinstance(outcome, dict) else None
    result_payload = dict(result) if isinstance(result, dict) else {}
    if include_review_findings:
        lines.extend(
            [
                "",
                *_review_finding_lines(result_payload),
            ]
        )
        result_payload.pop("reviewFindings", None)
    if result_payload:
        lines.extend(
            [
                "",
                "#### 结果证据",
                "",
                *_payload_value_lines(result_payload),
            ]
        )
    return lines


def _review_finding_lines(result: dict[str, Any]) -> list[str]:
    raw_findings = result.get("reviewFindings")
    if raw_findings is None:
        return [
            "#### Review 问题分级",
            "",
            "- 尚未提交结构化 P0/P1/P2 问题清单。",
        ]
    if not isinstance(raw_findings, list):
        return [
            "#### Review 问题分级",
            "",
            "- `reviewFindings` 格式无效，必须为问题数组。",
        ]
    findings = [
        item for item in raw_findings if isinstance(item, dict)
    ]
    counts = {severity: 0 for severity in ("P0", "P1", "P2")}
    unresolved = {severity: 0 for severity in ("P0", "P1")}
    rows: list[str] = []
    for finding in findings:
        severity = _interface_scalar(
            finding.get("severity"),
            fallback="未声明",
        ).upper()
        status = _interface_scalar(
            finding.get("status"),
            fallback="未声明",
        ).upper()
        if severity in counts:
            counts[severity] += 1
        if severity in unresolved and status != "RESOLVED":
            unresolved[severity] += 1
        rows.append(
            _table_row(
                [
                    severity,
                    _interface_scalar(
                        finding.get("summary"),
                        fallback="未提供问题说明",
                    ),
                    REVIEW_FINDING_STATUS_TEXT.get(status, "未声明"),
                    _interface_scalar(
                        finding.get("resolution"),
                        fallback="未提供处置说明",
                    ),
                    _interface_scalar(
                        finding.get("evidence"),
                        fallback="未提供证据",
                    ),
                ]
            )
        )
    lines = [
        "#### Review 问题分级",
        "",
        f"- P0：{counts['P0']} 项，未关闭 {unresolved['P0']} 项",
        f"- P1：{counts['P1']} 项，未关闭 {unresolved['P1']} 项",
        f"- P2：{counts['P2']} 项（必须逐项列示）",
        "",
    ]
    if not findings:
        return [*lines, "- 无 P0/P1/P2 问题。"]
    return [
        *lines,
        "| 级别 | 问题 | 状态 | 处置 | 证据 |",
        "|---|---|---|---|---|",
        *rows,
    ]


def render_delivery_acceptance(
    hierarchy: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
    run: dict[str, Any] | None = None,
) -> str:
    states = _projection_states(run)
    root_node = hierarchy["root"]
    root_definition = root_node["definition"]
    root_values = _projection_state_values(
        states,
        _work_item_terminal_node_id(root_node),
    )
    root_acceptance = "\n".join(
        [
            (
                "| 节点类型 | 节点标识 | 标题 | 当前进度 | "
                "结果摘要 | 验收记录 |"
            ),
            "|---|---|---|---|---|---|",
            _table_row(
                [
                    KIND_TEXT[root_definition["kind"]],
                    root_definition["id"],
                    root_definition["title"],
                    root_values["status"],
                    root_values["summary"],
                    (
                        "[查看]("
                        f"{work_item_projection_relative_path(
                            hierarchy,
                            root_definition['id'],
                            'acceptance.md',
                        )}"
                        ")"
                    ),
                ],
                raw_indices={5},
            ),
        ]
    )
    delivery = hierarchy["delivery"]
    delivery_lines = [
        "### Delivery Review",
        "",
        "#### 审查输入",
        "",
        _render_payload_markdown(
            delivery["reviewLoop"]["payload"],
            heading_level=5,
        ),
        "",
        "#### 审查结果",
        "",
        *_acceptance_result_lines(
            states,
            review_node_id(delivery["id"]),
            include_review_findings=True,
        ),
    ]
    confirmation_lines = _acceptance_result_lines(
        states,
        confirmation_node_id(delivery["id"]),
    )
    return ACCEPTANCE_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        acceptance_status="\n".join(
            _delivery_projection_status(
                hierarchy,
                hierarchy_fingerprint=hierarchy_fingerprint,
                graph_fingerprint=graph_fingerprint,
                hierarchy_status=hierarchy_status,
                updated_at=updated_at,
                run=run,
            )
        ),
        root_acceptance=root_acceptance,
        delivery_acceptance="\n".join(delivery_lines).rstrip() + "\n",
        confirmation="\n".join(confirmation_lines),
    )


def _work_item_status_lines(
    definition: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None,
    graph_fingerprint: str | None,
    hierarchy_status: str | None,
    updated_at: str | None,
) -> list[str]:
    return [
        f"- 节点类型：{KIND_TEXT[definition['kind']]}",
        f"- 节点标识：{_markdown_text(definition['id'])}",
        f"- 标题：{_markdown_text(definition['title'])}",
        f"- 上级：{_markdown_text(definition['parentId'] or '无')}",
        f"- 层级状态：{_status_text(hierarchy_status or 'UNKNOWN')}",
        f"- 层级指纹：{hierarchy_fingerprint or '不可用'}",
        f"- 调度图指纹：{graph_fingerprint or '不可用'}",
        f"- 更新时间（UTC+8）：{_utc_plus_8(updated_at)}",
    ]


def render_work_item_progress(
    node: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
    run: dict[str, Any] | None = None,
) -> str:
    definition = node["definition"]
    states = _projection_states(run)
    progress_header = (
        "| 阶段 | 当前进度 | 尝试次数 | 执行者 | "
        "最近更新时间（UTC+8） | 结果摘要 |"
    )
    progress_separator = "|---|---|---:|---|---|---|"
    if definition["kind"] == "TASK":
        sections = "\n".join(
            [
                "## TASK Loop 状态",
                "",
                progress_header,
                progress_separator,
                _progress_state_row(
                    states,
                    loop_node_id(definition["id"]),
                    prefix=["TASK"],
                ),
                _progress_state_row(
                    states,
                    task_review_node_id(definition["id"]),
                    prefix=["TASK Review"],
                ),
            ]
        )
    else:
        child_rows = []
        for child in node["children"]:
            child_definition = child["definition"]
            child_id = child_definition["id"]
            terminal_id = _work_item_terminal_node_id(child)
            values = _projection_state_values(states, terminal_id)
            child_rows.append(
                _table_row(
                    [
                        KIND_TEXT[child_definition["kind"]],
                        child_id,
                        child_definition["title"],
                        values["status"],
                        values["updatedAt"],
                        f"[查看](children/{child_id}/progress.md)",
                    ],
                    raw_indices={5},
                )
            )
        sections = "\n".join(
            [
                "## 直接子节点进展",
                "",
                (
                    "| 节点类型 | 节点标识 | 标题 | 当前进度 | "
                    "最近更新时间（UTC+8） | 执行进展 |"
                ),
                "|---|---|---|---|---|---|",
                *child_rows,
                "",
                "## GROUP 阶段进展",
                "",
                progress_header,
                progress_separator,
                _progress_state_row(
                    states,
                    join_node_id(definition["id"]),
                    prefix=["GROUP 完成点"],
                ),
                _progress_state_row(
                    states,
                    group_review_node_id(definition["id"]),
                    prefix=["GROUP Review"],
                ),
            ]
        )
    return WORK_ITEM_PROGRESS_PROJECTION_TEMPLATE.substitute(
        kind_text=KIND_TEXT[definition["kind"]],
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        item_status="\n".join(
            _work_item_status_lines(
                definition,
                hierarchy_fingerprint=hierarchy_fingerprint,
                graph_fingerprint=graph_fingerprint,
                hierarchy_status=hierarchy_status,
                updated_at=updated_at,
            )
        ),
        progress_sections=sections,
    )


def render_work_item_acceptance(
    node: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
    run: dict[str, Any] | None = None,
) -> str:
    definition = node["definition"]
    states = _projection_states(run)
    if definition["kind"] == "TASK":
        acceptance = _acceptance_payload(
            definition["execution"]["loop"]["payload"]
        )
        sections = "\n".join(
            [
                "## 已知验收输入",
                "",
                (
                    _render_payload_markdown(
                        acceptance,
                        heading_level=3,
                    )
                    if acceptance
                    else "- 未显式提供"
                ),
                "",
                "## TASK 结果与证据",
                "",
                *_acceptance_result_lines(
                    states,
                    loop_node_id(definition["id"]),
                ),
                "",
                "## TASK Review 输入",
                "",
                _render_payload_markdown(
                    node["reviewLoop"]["payload"],
                    heading_level=3,
                ),
                "",
                "## TASK Review 结果与证据",
                "",
                *_acceptance_result_lines(
                    states,
                    task_review_node_id(definition["id"]),
                    include_review_findings=True,
                ),
            ]
        )
    else:
        child_rows = []
        for child in node["children"]:
            child_definition = child["definition"]
            child_id = child_definition["id"]
            terminal_id = _work_item_terminal_node_id(child)
            values = _projection_state_values(states, terminal_id)
            child_rows.append(
                _table_row(
                    [
                        KIND_TEXT[child_definition["kind"]],
                        child_id,
                        child_definition["title"],
                        values["status"],
                        values["summary"],
                        f"[查看](children/{child_id}/acceptance.md)",
                    ],
                    raw_indices={5},
                )
            )
        group_sections = [
            "## 直接子节点验收",
            "",
            (
                "| 节点类型 | 节点标识 | 标题 | 当前进度 | "
                "结果摘要 | 验收记录 |"
            ),
            "|---|---|---|---|---|---|",
            *child_rows,
            "",
            "## GROUP 完成点结果",
            "",
            *_acceptance_result_lines(
                states,
                join_node_id(definition["id"]),
            ),
            "",
        ]
        group_sections.extend(
            [
                "## GROUP Review 输入",
                "",
                _render_payload_markdown(
                    node["reviewLoop"]["payload"],
                    heading_level=3,
                ),
                "",
                "## GROUP Review 结果与证据",
                "",
                *_acceptance_result_lines(
                    states,
                    group_review_node_id(definition["id"]),
                    include_review_findings=True,
                ),
            ]
        )
        sections = "\n".join(group_sections)
    return WORK_ITEM_ACCEPTANCE_PROJECTION_TEMPLATE.substitute(
        kind_text=KIND_TEXT[definition["kind"]],
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        item_status="\n".join(
            _work_item_status_lines(
                definition,
                hierarchy_fingerprint=hierarchy_fingerprint,
                graph_fingerprint=graph_fingerprint,
                hierarchy_status=hierarchy_status,
                updated_at=updated_at,
            )
        ),
        acceptance_sections=sections,
    )


def render_task_interfaces(
    definition: dict[str, Any],
    *,
    delivery_baseline: str = "../../baseline.md",
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
) -> str:
    if definition["kind"] != "TASK":
        raise ValueError("Interface projection requires a TASK definition")
    declarations = _task_interface_declarations(definition)
    rows: list[str] = []
    details: list[str] = []
    for interface in declarations:
        protocol = _interface_scalar(
            interface.get("protocol"),
            fallback="未声明",
        ).upper()
        name = _interface_scalar(
            interface.get("name"),
            fallback="未命名接口",
        )
        summary = _interface_scalar(
            interface.get("summary"),
            fallback="未提供简介",
        )
        change_type = _interface_scalar(
            interface.get("changeType"),
            fallback="UNSPECIFIED",
        ).upper()
        change_text = INTERFACE_CHANGE_TYPE_TEXT.get(
            change_type,
            "未声明",
        )
        before_value = interface.get("before")
        after_value = interface.get("after")
        before = (
            before_value if isinstance(before_value, dict) else None
        )
        after = after_value if isinstance(after_value, dict) else None
        before_identifier = _interface_call_identifier(
            protocol,
            before,
        )
        after_identifier = _interface_call_identifier(
            protocol,
            after,
        )
        rows.append(
            "| "
            + " | ".join(
                _markdown_text(value)
                for value in (
                    definition["id"],
                    protocol,
                    name,
                    change_text,
                    before_identifier,
                    after_identifier,
                    summary,
                )
            )
            + " |"
        )
        details.extend(
            [
                f"### { _markdown_text(protocol) }：{_markdown_text(name)}",
                "",
                f"- 来源 TASK：{_markdown_text(definition['id'])}",
                f"- 协议：{_markdown_text(protocol)}",
                f"- 接口名称：{_markdown_text(name)}",
                f"- 变更类型：{_markdown_text(change_text)}",
                f"- 简介：{_markdown_text(summary)}",
                "",
                "#### 修改前",
                "",
            ]
        )
        if before is None:
            details.extend(
                [
                    (
                        "- 不适用（新增接口）"
                        if change_type == "CREATE"
                        else "- 未声明修改前契约"
                    ),
                    "",
                ]
            )
        else:
            details.extend(
                [
                    (
                        "- 调用标识："
                        f"{_markdown_text(before_identifier)}"
                    ),
                    "",
                    "##### 入参",
                    "",
                    *_payload_value_lines(
                        before.get("request", "未声明")
                    ),
                    "",
                    "##### 出参",
                    "",
                    *_payload_value_lines(
                        before.get("response", "未声明")
                    ),
                    "",
                ]
            )
        details.extend(["#### 修改后", ""])
        if after is None:
            details.extend(
                [
                    (
                        "- 不适用（删除接口）"
                        if change_type == "DELETE"
                        else "- 未声明修改后契约"
                    ),
                    "",
                ]
            )
        else:
            details.extend(
                [
                    (
                        "- 调用标识："
                        f"{_markdown_text(after_identifier)}"
                    ),
                    "",
                    "##### 入参",
                    "",
                    *_payload_value_lines(
                        after.get("request", "未声明")
                    ),
                    "",
                    "##### 出参",
                    "",
                    *_payload_value_lines(
                        after.get("response", "未声明")
                    ),
                    "",
                ]
            )
    interface_rows = (
        "\n".join(
            [
                (
                    "| 来源 TASK | 协议 | 接口名称 | 变更类型 | "
                    "修改前调用标识 | 修改后调用标识 | 简介 |"
                ),
                "|---|---|---|---|---|---|---|",
                *rows,
            ]
        )
        if rows
        else "- 当前需求未显式声明接口契约。"
    )
    interface_details = (
        "\n".join(details).rstrip()
        if details
        else "- 无接口详情。"
    )
    return INTERFACES_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        interface_status="\n".join(
            _work_item_status_lines(
                definition,
                hierarchy_fingerprint=hierarchy_fingerprint,
                graph_fingerprint=graph_fingerprint,
                hierarchy_status=hierarchy_status,
                updated_at=updated_at,
            )
        ),
        interface_rows=interface_rows,
        interface_details=interface_details,
        delivery_baseline=delivery_baseline,
    )


def render_projection_documents(
    stored_definition: dict[str, Any],
    run: dict[str, Any] | None,
) -> dict[str, str]:
    """Render the complete controller-owned human projection set.

    The stored hierarchy and optional run are already loaded from SQLite by
    the repository. Callers cannot supply a template or filename.
    """

    hierarchy = stored_definition["hierarchy"]
    updated_at = (
        run["updatedAt"]
        if run is not None
        else stored_definition["updatedAt"]
    )
    human_projection_arguments = {
        "hierarchy_fingerprint": stored_definition[
            "hierarchyFingerprint"
        ],
        "graph_fingerprint": stored_definition["graphFingerprint"],
        "hierarchy_status": stored_definition["status"],
        "updated_at": updated_at,
    }
    return {
        "overview.md": render_scheduling_plan(
            hierarchy,
            **human_projection_arguments,
            run=run,
        ),
        "baseline.md": render_delivery_baseline(
            hierarchy,
            **human_projection_arguments,
        ),
        "progress.md": render_delivery_progress(
            hierarchy,
            **human_projection_arguments,
            run=run,
        ),
        "acceptance.md": render_delivery_acceptance(
            hierarchy,
            **human_projection_arguments,
            run=run,
        ),
    }


def render_work_item_projection_documents(
    stored_definition: dict[str, Any],
    run: dict[str, Any] | None,
) -> dict[str, str]:
    """Render the exact GROUP/TASK projection tree from SQLite state."""

    hierarchy = stored_definition["hierarchy"]
    projection_directories = work_item_projection_directories(hierarchy)
    baseline_arguments = {
        "delivery": hierarchy["delivery"],
        "skill_hints": hierarchy["root"]["skillHints"],
        "hierarchy_fingerprint": stored_definition[
            "hierarchyFingerprint"
        ],
        "graph_fingerprint": stored_definition["graphFingerprint"],
        "hierarchy_status": stored_definition["status"],
        "updated_at": stored_definition["updatedAt"],
    }
    dynamic_arguments = {
        "hierarchy_fingerprint": stored_definition[
            "hierarchyFingerprint"
        ],
        "graph_fingerprint": stored_definition["graphFingerprint"],
        "hierarchy_status": stored_definition["status"],
        "updated_at": (
            run["updatedAt"]
            if run is not None
            else stored_definition["updatedAt"]
        ),
        "run": run,
    }
    documents: dict[str, str] = {}
    for node in iter_hierarchy_nodes(hierarchy):
        definition = node["definition"]
        item_id = definition["id"]
        projection_directory = projection_directories[item_id]
        tree_directory = projection_directory.removeprefix(
            f"{WORK_ITEM_DIRECTORY}/"
        )
        delivery_baseline = posixpath.relpath(
            "baseline.md",
            start=projection_directory,
        )
        documents[f"{tree_directory}/baseline.md"] = (
            render_work_item_baseline(
                node,
                delivery_baseline=delivery_baseline,
                **baseline_arguments,
            )
            if definition["kind"] == "TASK"
            else render_group_baseline(
                node,
                delivery_baseline=delivery_baseline,
                **baseline_arguments,
            )
        )
        documents[f"{tree_directory}/progress.md"] = (
            render_work_item_progress(
                node,
                **dynamic_arguments,
            )
        )
        documents[f"{tree_directory}/acceptance.md"] = (
            render_work_item_acceptance(
                node,
                **dynamic_arguments,
            )
        )
        if _task_interface_declarations(definition):
            documents[f"{tree_directory}/interfaces.md"] = (
                render_task_interfaces(
                    definition,
                    delivery_baseline=delivery_baseline,
                    hierarchy_fingerprint=stored_definition[
                        "hierarchyFingerprint"
                    ],
                    graph_fingerprint=stored_definition[
                        "graphFingerprint"
                    ],
                    hierarchy_status=stored_definition["status"],
                    updated_at=stored_definition["updatedAt"],
                )
            )
    return documents


__all__ = (
    "ACCEPTANCE_PROJECTION_TEMPLATE",
    "BASELINE_PROJECTION_TEMPLATE",
    "INTERFACES_PROJECTION_TEMPLATE",
    "PROGRESS_PROJECTION_TEMPLATE",
    "PROJECTION_TEMPLATES",
    "PROJECTION_TEMPLATE_VERSION",
    "GROUP_BASELINE_PROJECTION_TEMPLATE",
    "TASK_BASELINE_PROJECTION_TEMPLATE",
    "WORK_ITEM_ACCEPTANCE_PROJECTION_TEMPLATE",
    "WORK_ITEM_DIRECTORY",
    "WORK_ITEM_PROGRESS_PROJECTION_TEMPLATE",
    "WORKSPACE_OVERVIEW_PROJECTION_TEMPLATE",
    "has_interface_projection",
    "raw_definition",
    "render_delivery_acceptance",
    "render_delivery_baseline",
    "render_delivery_progress",
    "render_group_baseline",
    "render_projection_documents",
    "render_scheduling_plan",
    "render_task_interfaces",
    "render_work_item_baseline",
    "render_work_item_acceptance",
    "render_work_item_progress",
    "render_work_item_projection_documents",
    "render_workspace_overview",
    "task_baseline_relative_path",
    "task_has_interface_projection",
    "work_item_projection_relative_path",
    "work_item_projection_directories",
)
