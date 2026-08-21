from __future__ import annotations

from datetime import datetime, timedelta, timezone

import json

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
    "CHOICE_READY": "基线已生成，待选择开发方式",
    "QUEUED": "排队中（等待工作区串行调度）",
    "HANDOFF_READY": "需求已冻结（手动开发，调度未启动）",
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
    "SUPERSEDED": "已被新修订取代",
    "ABANDONED": "已放弃",
    "COMPLETED": "已完成",
    "ARCHIVED": "已归档",
    "NOT_STARTED": "未启动",
    "UNKNOWN": "未知",
    "UNAVAILABLE": "不可用",
    "STATE_INVALID": "调度状态异常",
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

INTERFACE_REQUEST_LOCATION_PREFIXES = MappingProxyType(
    {
        "headers": "header",
        "pathParameters": "path",
        "queryParameters": "query",
        "body": "body",
        "businessParameters": "business",
        "contextDependencies": "context",
        "contextDerived": "context",
        "contextualInputs": "context",
        "parameters": "",
    }
)

REVIEW_FINDING_STATUS_TEXT = {
    "RESOLVED": "已修复",
    "ACCEPTED": "已接受",
    "OPEN": "待处理",
}

WORKSPACE_CHANGE_STATUS_TEXT = {
    "ADDED": "新增",
    "BROKEN": "配对异常",
    "COPIED": "复制",
    "DELETED": "删除",
    "MODIFIED": "修改",
    "RENAMED": "重命名",
    "TYPE_CHANGED": "类型变化",
    "UNMERGED": "未合并",
    "UNKNOWN": "未知",
    "UNTRACKED": "未跟踪",
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
        "databaseChanges": "数据库变更契约",
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

PROJECTION_TEMPLATE_VERSION = 20

WORK_ITEM_DIRECTORY = "work-items"

WORKSPACE_OVERVIEW_PROJECTION_TEMPLATE = Template(
    """# 未归档交付调度与进度总览

## 工作区状态

- 未归档交付数量：${delivery_count}
- 更新时间（UTC+8）：${updated_at}

本文件由控制器从 SQLite 权威状态统一生成，用于查看工作区内未归档交付需求。

## 未归档 Delivery 清单

| 交付标识 | 需求标题 | 当前阶段 | 上线状态 | 最近更新（UTC+8） | 交付详情 |
|---|---|---|---|---|---|
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
| [执行进展](progress.md) | TASK、TASK Review、可选 GROUP seam Review 与 Delivery Acceptance/Readiness 的运行状态 |
| [验收记录](acceptance.md) | 各层有界验收结论、证据引用与用户确认 |
| [修订历史](revisions.md) | 同一 Delivery 的历次冻结范围、授权与运行状态 |

实现规范、测试、门禁与 Skill 激活由各 Loop 内部负责。SQLite 是需求与
调度状态的机器权威；Graph 启动后由事件链记录运行历史。本目录中的 Markdown
仅为控制器生成的人类投影。

"""
)

BASELINE_PROJECTION_TEMPLATE = Template(
    """# 交付需求基线

## 基线标识

${baseline_status}

## 关联投影

- [查看执行进展](progress.md)
- [查看验收记录](acceptance.md)

## Git 分支绑定

${git_binding}

## 跨项目授权范围

${project_scopes}

## Skill 提示

${skill_hints}

## GROUP/TASK 清单

| 层级路径 | 节点类型 | 上级 | 前置依赖 | 标题 | 需求基线 | 执行进展 | 验收记录 | 接口契约 | 数据库契约 |
|---|---|---|---|---|---|---|---|---|---|
${checklist_rows}

每个 GROUP/TASK 的详细摘要、Loop 引用、资源声明和结构化执行输入位于
对应的 `work-items/<root-id>/children/.../<node-id>/baseline.md`；
Delivery 本文件只串联基线树，
不重复聚合节点级输入。

## Delivery Acceptance/Readiness 输入

${delivery_review_baseline}
"""
)

PROGRESS_PROJECTION_TEMPLATE = Template(
    """# 交付执行进展

## 运行状态

${progress_status}

## 实时进度监控

${progress_monitor}

## TASK 执行进展

${task_progress}
## GROUP 协调与 seam 验收进展

${group_progress}
## Delivery Acceptance/Readiness 进展

${delivery_review_progress}
"""
)

ACCEPTANCE_PROJECTION_TEMPLATE = Template(
    """# 交付验收记录

## 验收状态

${acceptance_status}

## 职责边界

- Controller：Graph 门禁、结果契约校验与持久化；不做技术验收。
- Delivery receiver：顶层技术验收与运行准备度判断；不重验每个下层 Loop。
- 用户：确认当前 Revision 完成，并在生产上线后单独关闭 Delivery。

## 根工作项验收

${root_acceptance}
## Delivery 最终技术验收与交付准备度

${delivery_acceptance}
## 当前 Revision 完成确认

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
接口声明不参与 Graph 调度决策。每个接口的字段级契约独立位于
`interfaces/` 目录，通过下方接口名称进入详情。

## 接口清单

${interface_rows}
"""
)

INTERFACE_DETAIL_PROJECTION_TEMPLATE = Template(
    """# ${protocol}：${name}

## 导航

- [返回接口清单](../interfaces.md)
- [返回 TASK 基线](../baseline.md)

## 接口定义

${interface_metadata}

## 入参

${request_table}

## 出参

${response_table}
"""
)

DATABASE_CHANGES_PROJECTION_TEMPLATE = Template(
    """# TASK 数据库变更契约

## 数据库基线

${database_status}
- TASK 需求基线：[返回 TASK 基线](baseline.md)
- Delivery 需求基线：[返回 Delivery 基线](${delivery_baseline})

本文件只投影 baseline 冻结前已确认的 `databaseChanges`。每张表的完整
before/after 结构与迁移方案位于 `database-changes/`。冻结后的 after 是
TASK Loop 唯一允许实施的表结构；需要偏离时必须返回 `REPLAN_REQUIRED`。

## 数据库变更清单

${database_rows}
"""
)

DATABASE_CHANGE_DETAIL_PROJECTION_TEMPLATE = Template(
    """# 数据库表：${table}

## 导航

- [返回数据库变更清单](../database-changes.md)
- [返回 TASK 基线](../baseline.md)

## 变更定义

${metadata}

## 字段级比较

${column_table}

## 修改前完整结构

${before_snapshot}

## 修改后完整结构（执行事实源）

${after_snapshot}

## 迁移与验证

${migration}
"""
)

TASK_BASELINE_PROJECTION_TEMPLATE = Template(
    """# TASK 调度基线

## 基线标识

- 交付标识：${delivery_id}
- 交付标题：${delivery_title}
- 任务标识：${task_id}
- 需求版本：${requirement_revision}
- 需求状态：${requirement_status}
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

${database_section}

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
        "revisions.md": None,
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

def _markdown_code(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    fence = "`"
    while fence in text:
        fence += "`"
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{padding}{text}{padding}{fence}"

def _markdown_diff_block(value: str) -> list[str]:
    fence = "```"
    while fence in value:
        fence += "`"
    return [f"{fence}diff", value.rstrip(), fence]

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

def _task_database_declarations(
    definition: dict[str, Any],
) -> list[dict[str, Any]]:
    if definition["kind"] != "TASK":
        return []
    value = definition["execution"]["loop"]["payload"].get(
        "databaseChanges"
    )
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]

def has_database_projection(hierarchy: dict[str, Any]) -> bool:
    return any(
        task_has_database_projection(node["definition"])
        for node in iter_hierarchy_nodes(hierarchy)
    )

def task_has_database_projection(
    definition: dict[str, Any],
) -> bool:
    return bool(_task_database_declarations(definition))

def _work_item_terminal_node_id(node: dict[str, Any]) -> str:
    definition = node["definition"]
    if definition["kind"] == "TASK":
        return (
            task_review_node_id(definition["id"])
            if node["reviewLoop"] is not None
            else loop_node_id(definition["id"])
        )
    return (
        group_review_node_id(definition["id"])
        if node["reviewLoop"] is not None
        else join_node_id(definition["id"])
    )

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

def _render_loop_baseline(
    label: str,
    loop: dict[str, Any] | None,
    *,
    heading_level: int,
    absent_message: str = "LIGHT 保障档不创建此独立 Review Loop。",
) -> str:
    heading = "#" * max(1, min(heading_level, 6))
    if loop is None:
        return "\n".join(
            [
                f"{heading} {label}",
                "",
                f"- {absent_message}",
            ]
        )
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
