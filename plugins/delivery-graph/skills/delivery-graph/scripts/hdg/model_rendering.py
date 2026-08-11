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
PROJECTION_TEMPLATE_VERSION = 17
WORK_ITEM_DIRECTORY = "work-items"
WORKSPACE_OVERVIEW_PROJECTION_TEMPLATE = Template(
    """# 未归档交付调度与进度总览

## 工作区状态

- 未归档交付数量：${delivery_count}
- 更新时间（UTC+8）：${updated_at}

本文件由控制器从 SQLite 权威状态统一生成，用于查看工作区内未归档交付需求。

## 未归档 Delivery 清单

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

## Delivery 审查输入

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


def _interface_required_text(value: object) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "未声明"


def _interface_field_rows(
    value: object,
    *,
    section: str,
) -> list[dict[str, str]]:
    """Normalize common interface field declarations for table rendering."""

    rows: list[dict[str, str]] = []
    used_paths: set[str] = set()

    def unique_path(path: str) -> str:
        candidate = path or "（整体）"
        if candidate not in used_paths:
            used_paths.add(candidate)
            return candidate
        index = 2
        while f"{candidate}（{index}）" in used_paths:
            index += 1
        resolved = f"{candidate}（{index}）"
        used_paths.add(resolved)
        return resolved

    def add_row(
        path: str,
        *,
        field_type: object = None,
        required: object = None,
        max_length: object = None,
        description: object = None,
        example: object = None,
    ) -> None:
        rows.append(
            {
                "path": unique_path(path),
                "type": _interface_scalar(
                    field_type,
                    fallback="未声明",
                ),
                "required": _interface_required_text(required),
                "maxLength": _interface_scalar(
                    max_length,
                    fallback="—",
                ),
                "description": _interface_scalar(
                    description,
                    fallback="未声明",
                ),
                "example": _interface_scalar(
                    example,
                    fallback="—",
                ),
            }
        )

    def joined_path(prefix: str, name: object) -> str:
        normalized = _interface_scalar(name, fallback="未命名字段")
        return f"{prefix}.{normalized}" if prefix else normalized

    def parse_named(
        name: object,
        specification: object,
        *,
        prefix: str,
        required_override: bool | None = None,
    ) -> None:
        path = joined_path(prefix, name)
        if not isinstance(specification, dict):
            add_row(
                path,
                field_type=specification,
                required=required_override,
            )
            return
        nested = specification.get(
            "properties",
            specification.get("fields"),
        )
        nested_required = specification.get("required")
        add_row(
            path,
            field_type=specification.get(
                "type",
                "object" if nested is not None else None,
            ),
            required=(
                nested_required
                if isinstance(nested_required, bool)
                else required_override
            ),
            max_length=specification.get(
                "maxLength",
                specification.get("max_length"),
            ),
            description=specification.get(
                "description",
                specification.get("summary"),
            ),
            example=specification.get(
                "example",
                specification.get("exampleValue"),
            ),
        )
        if nested is not None:
            required_names = (
                {
                    str(item)
                    for item in nested_required
                    if isinstance(item, str)
                }
                if isinstance(nested_required, list)
                else None
            )
            parse_collection(
                nested,
                prefix=path,
                required_names=required_names,
            )

    def parse_collection(
        collection: object,
        *,
        prefix: str,
        required_names: set[str] | None = None,
    ) -> None:
        has_required_declaration = required_names is not None
        required_names = required_names or set()
        if isinstance(collection, list):
            for index, item in enumerate(collection, start=1):
                if isinstance(item, dict):
                    name = item.get("name", f"第 {index} 项")
                    parse_named(
                        name,
                        item,
                        prefix=prefix,
                        required_override=(
                            str(name) in required_names
                            if has_required_declaration
                            else None
                        ),
                    )
                else:
                    parse_named(
                        item,
                        None,
                        prefix=prefix,
                        required_override=(
                            str(item) in required_names
                            if has_required_declaration
                            else None
                        ),
                    )
            return
        if isinstance(collection, dict):
            for name in sorted(collection):
                parse_named(
                    name,
                    collection[name],
                    prefix=prefix,
                    required_override=(
                        str(name) in required_names
                        if has_required_declaration
                        else None
                    ),
                )
            return
        add_row(
            prefix or "（整体）",
            description=collection,
        )

    def first_declared(
        specification: dict[str, Any],
        keys: tuple[str, ...],
    ) -> object:
        for key in keys:
            if key in specification:
                return specification[key]
        return None

    def parse_request_locations(specification: dict[str, Any]) -> bool:
        if not any(
            key in specification
            for key in INTERFACE_REQUEST_LOCATION_PREFIXES
        ):
            return False
        for key, prefix in INTERFACE_REQUEST_LOCATION_PREFIXES.items():
            if key not in specification:
                continue
            collection = specification[key]
            if collection is None or collection == [] or collection == {}:
                continue
            if key == "body" and isinstance(collection, dict):
                parse_named("body", collection, prefix="")
            else:
                parse_collection(collection, prefix=prefix)
        return True

    def normalize_response_aliases(
        specification: dict[str, Any],
    ) -> dict[str, Any]:
        uses_controller_alias = any(
            key in specification
            for key in (
                "controllerReturnType",
                "controllerReturnFields",
            )
        )
        if not uses_controller_alias:
            return specification
        normalized: dict[str, Any] = {}
        response_type = first_declared(
            specification,
            ("type", "controllerReturnType"),
        )
        response_fields = first_declared(
            specification,
            ("fields", "properties", "controllerReturnFields"),
        )
        response_description = first_declared(
            specification,
            ("description", "summary"),
        )
        if response_type is not None:
            normalized["type"] = response_type
        if response_fields is not None:
            normalized["fields"] = response_fields
        if response_description is not None:
            normalized["description"] = response_description
        return normalized

    if isinstance(value, list):
        parse_collection(value, prefix="")
    elif isinstance(value, dict):
        if section == "request" and parse_request_locations(value):
            return rows
        if section == "response":
            value = normalize_response_aliases(value)
        nested = value.get("properties", value.get("fields"))
        looks_like_schema = nested is not None or any(
            key in value
            for key in ("name", "type", "required", "description", "summary")
        )
        if value.get("name") is not None:
            parse_named(value["name"], value, prefix="")
        elif looks_like_schema:
            add_row(
                "（整体）",
                field_type=value.get(
                    "type",
                    "object" if nested is not None else None,
                ),
                description=value.get(
                    "description",
                    value.get("summary"),
                ),
            )
            if nested is not None:
                required_names = (
                    {
                        str(item)
                        for item in value.get("required", [])
                        if isinstance(item, str)
                    }
                    if isinstance(value.get("required"), list)
                    else None
                )
                parse_collection(
                    nested,
                    prefix="",
                    required_names=required_names,
                )
        else:
            parse_collection(value, prefix="")
    else:
        add_row("（整体）", description=value)
    return rows


def _interface_transition(
    before: dict[str, str] | None,
    after: dict[str, str] | None,
    field: str,
) -> str:
    if before is None:
        return after[field] if after is not None else "未声明"
    if after is None:
        return before[field]
    before_value = before[field]
    after_value = after[field]
    if before_value == after_value:
        return before_value
    return f"{before_value} → {after_value}"


def _interface_change_table(
    before: object,
    after: object,
    *,
    section: str,
    include_required: bool = True,
    include_max_length: bool = False,
    include_example: bool = False,
    path_group: str | None = None,
    strip_path_group: bool = False,
    omit_container_rows: bool = False,
    render_empty: bool = True,
) -> list[str]:
    before_rows = (
        _interface_field_rows(before, section=section)
        if before is not None
        else []
    )
    after_rows = (
        _interface_field_rows(after, section=section)
        if after is not None
        else []
    )
    location_prefixes = tuple(
        prefix
        for prefix in INTERFACE_REQUEST_LOCATION_PREFIXES.values()
        if prefix
    )

    def in_path_group(path: str) -> bool:
        if path_group is None:
            return True
        if path_group == "":
            return not any(
                path == prefix or path.startswith(f"{prefix}.")
                for prefix in location_prefixes
            )
        return path == path_group or path.startswith(f"{path_group}.")

    before_rows = [
        row for row in before_rows if in_path_group(row["path"])
    ]
    after_rows = [
        row for row in after_rows if in_path_group(row["path"])
    ]
    if omit_container_rows:
        all_paths = {
            row["path"] for row in [*before_rows, *after_rows]
        }

        def is_container(path: str) -> bool:
            return len(all_paths) > 1 and (
                path == "（整体）"
                or (
                    path_group not in (None, "")
                    and path == path_group
                )
            )

        before_rows = [
            row for row in before_rows if not is_container(row["path"])
        ]
        after_rows = [
            row for row in after_rows if not is_container(row["path"])
        ]
    before_by_path = {row["path"]: row for row in before_rows}
    after_by_path = {row["path"]: row for row in after_rows}
    paths = [
        *(row["path"] for row in before_rows),
        *(
            row["path"]
            for row in after_rows
            if row["path"] not in before_by_path
        ),
    ]
    rendered_rows: list[str] = []
    for path in paths:
        before_row = before_by_path.get(path)
        after_row = after_by_path.get(path)
        comparison_fields = ["type"]
        if include_required:
            comparison_fields.append("required")
        if include_max_length:
            comparison_fields.append("maxLength")
        comparison_fields.append("description")
        if include_example:
            comparison_fields.append("example")
        if before_row is None:
            change = "新增"
        elif after_row is None:
            change = "删除"
        elif any(
            before_row[field] != after_row[field]
            for field in comparison_fields
        ):
            change = "修改"
        else:
            change = "未变"
        rendered_path = path
        if strip_path_group and path_group:
            rendered_path = (
                "（整体）"
                if path == path_group
                else path.removeprefix(f"{path_group}.")
            )
        values = [
            rendered_path,
            change,
            _interface_transition(before_row, after_row, "type"),
        ]
        if include_required:
            values.append(
                _interface_transition(
                    before_row,
                    after_row,
                    "required",
                )
            )
        if include_max_length:
            values.append(
                _interface_transition(
                    before_row,
                    after_row,
                    "maxLength",
                )
            )
        values.append(
            _interface_transition(
                before_row,
                after_row,
                "description",
            )
        )
        if include_example:
            values.append(
                _interface_transition(
                    before_row,
                    after_row,
                    "example",
                )
            )
        if before_row is not None and after_row is None:
            values = [
                value if index == 1 else f"~~{value}~~"
                for index, value in enumerate(values)
            ]
        rendered_rows.append(
            _table_row(values)
        )
    if not rendered_rows and not render_empty:
        return []
    columns = [
        "字段路径",
        "变更",
        "类型（修改前 → 修改后）",
    ]
    if include_required:
        columns.append("必填（修改前 → 修改后）")
    if include_max_length:
        columns.append("最大长度（修改前 → 修改后）")
    columns.append("说明（修改前 → 修改后）")
    if include_example:
        columns.append("示例值（修改前 → 修改后）")
    header = _table_row(columns)
    separator = f"|{'---|' * len(columns)}"
    empty_values = [
        "（无）",
        "无入参" if section == "request" else "无出参",
        *(["—"] * (len(columns) - 2)),
    ]
    return [
        header,
        separator,
        *(rendered_rows or [_table_row(empty_values)]),
    ]


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
            else item["status"]
        )
        updated_at = (
            run["updatedAt"]
            if run is not None
            else item["updatedAt"]
        )
        cells = [
            item["rootId"],
            (
                "调度状态不可读取"
                if state_error is not None
                else hierarchy["delivery"]["title"]
            ),
            _status_text(status),
            _utc_plus_8(updated_at),
        ]
        detail_link = (
            _markdown_text(
                f"需修复 SQLite 状态（{state_error['code']}）"
            )
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
    task_requirement: dict[str, Any] | None = None,
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
    database_declarations = _task_database_declarations(definition)
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
        requirement_revision=(
            str(task_requirement["revision"])
            if task_requirement is not None
            else "未冻结"
        ),
        requirement_status=(
            {
                "FROZEN": "已冻结",
                "UNFROZEN": "已解冻，禁止派遣",
            }.get(task_requirement["status"], "未知")
            if task_requirement is not None
            else "待 Delivery 冻结"
        ),
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
                if not (
                    (key == "interfaces" and interface_declarations)
                    or (
                        key == "databaseChanges"
                        and database_declarations
                    )
                )
            },
            heading_level=3,
        ),
        interface_section=(
            "## 关联接口契约\n\n"
            "[查看本 TASK 的接口契约](interfaces.md)"
            if interface_declarations
            else ""
        ),
        database_section=(
            "## 关联数据库变更契约\n\n"
            "[查看本 TASK 的数据库变更契约](database-changes.md)"
            if database_declarations
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

    hierarchy_nodes = iter_hierarchy_nodes(hierarchy)
    definitions = [node["definition"] for node in hierarchy_nodes]
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
    latest_update = (
        updated_at
        if archived or run is None
        else run["updatedAt"]
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
        return (
            task_review_node_id(definition["id"])
            if node["reviewLoop"] is not None
            else loop_node_id(definition["id"])
        )
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
            "agent": "无",
            "model": "无",
            "owner": "无",
            "attempt": "0",
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
            f"等待至 {_utc_plus_8(state['resumeAt'])} 由 Agent 恢复派遣"
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
        "agent": state.get("agentId") or "无",
        "model": state.get("actualModelId") or "未报告",
        "owner": state["owner"] or "无",
        "attempt": str(state["attempt"]),
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
        values["agent"],
        values["model"],
        values["owner"],
        values["attempt"],
        values["updatedAt"],
        values["summary"],
        *suffix_values,
    ]
    suffix_start = len(prefix_values) + 7
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
            "| 当前进度 | 认领身份 | 执行轮次 | "
            "结束时间（UTC+8） | 结果摘要 |"
        ),
        "|---|---|---:|---|---|",
        _table_row(
            [
                values["status"],
                values["owner"],
                values["attempt"],
                values["finishedAt"],
                values["summary"],
            ]
        ),
    ]


def _render_loop_baseline(
    label: str,
    loop: dict[str, Any] | None,
    *,
    heading_level: int,
) -> str:
    heading = "#" * max(1, min(heading_level, 6))
    if loop is None:
        return "\n".join(
            [
                f"{heading} {label}",
                "",
                "- LIGHT 保障档不创建此独立 Review Loop。",
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
        (
            "- 保障档："
            f"{_markdown_text(delivery.get('assuranceProfile', 'STANDARD'))}"
        ),
    ]
    if delivery.get("requirementKey") is not None:
        lines.insert(
            1,
            "- 外部需求标识："
            f"{_markdown_text(delivery['requirementKey'])}",
        )
    if delivery.get("assuranceRationale") is not None:
        lines.append(
            "- 保障判断："
            f"{_markdown_text(delivery['assuranceRationale'])}"
        )
    lines.extend(
        [
            f"- 数据结构版本：{hierarchy['root']['schemaVersion']}",
            f"- 层级状态：{_status_text(hierarchy_status or 'UNKNOWN')}",
            (
                "- 运行状态："
                f"{_status_text((run or {}).get('status', 'NOT_STARTED'))}"
            ),
            f"- 层级指纹：{hierarchy_fingerprint or '不可用'}",
            f"- 调度图指纹：{graph_fingerprint or '不可用'}",
        ]
    )
    if run is not None:
        lines.extend(
            [
                f"- 运行标识：{_markdown_text(run['runId'])}",
                (
                    "- Delivery 修订："
                    f"{run.get('deliveryRevision', 1)}"
                ),
                f"- 启动时间（UTC+8）：{_utc_plus_8(run['startedAt'])}",
            ]
        )
    lines.append(f"- 更新时间（UTC+8）：{_utc_plus_8(updated_at)}")
    return lines


def _render_git_binding_baseline(
    delivery: dict[str, Any],
) -> str:
    binding = delivery.get("gitBinding")
    if binding is None:
        return "本 Delivery 未声明 Git 分支绑定。"
    branch_ref = _markdown_text(binding["branchRef"])
    base_ref = _markdown_text(binding["baseRef"])
    base_commit = _markdown_text(binding["baseCommit"])
    integration_target = _markdown_text(
        binding["integrationTarget"]
    )
    return "\n".join(
        [
            f"- Delivery feature 分支：{branch_ref}",
            f"- 创建来源分支：{base_ref}",
            f"- 创建基线提交：{base_commit}",
            f"- 最终集成目标：{integration_target}",
            (
                "- 分支关系："
                f"{base_ref}@{base_commit} → {branch_ref} → "
                f"{integration_target}"
            ),
            (
                "- 约束：feature HEAD 可随本 Delivery 提交前进，但必须"
                "继承创建基线；最终合入目标不随运行自动改变。"
            ),
        ]
    )


def _render_project_scopes(
    delivery: dict[str, Any],
) -> str:
    scopes = delivery.get("projectScopes", [])
    if not scopes:
        return "未声明跨项目范围；仅使用当前 Delivery 工作区。"
    rows = []
    for scope in scopes:
        binding = scope.get("gitBinding") or {}
        rows.append(
            "| "
            + " | ".join(
                _markdown_text(value)
                for value in (
                    scope["id"],
                    scope["workspaceRoot"],
                    scope["access"],
                    binding.get("branchRef", "非 Git"),
                    binding.get("baseCommit", "不适用"),
                    binding.get("integrationTarget", "不适用"),
                )
            )
            + " |"
        )
    return "\n".join(
        [
            "| 项目标识 | 本地仓库 | 访问上限 | Delivery 分支 | 基线提交 | 集成目标 |",
            "|---|---|---|---|---|---|",
            *rows,
            "",
            "冻结要求：必须精确授权以上全部项目；所有可写 Git 项目使用同名分支。",
        ]
    )


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
        database_link = (
            f"[查看]("
            f"{work_item_projection_relative_path(hierarchy, item_id, 'database-changes.md')}"
            ")"
            if _task_database_declarations(definition)
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
                    database_link,
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
        git_binding=_render_git_binding_baseline(
            hierarchy["delivery"]
        ),
        project_scopes=_render_project_scopes(
            hierarchy["delivery"]
        ),
        skill_hints="\n".join(skill_hint_lines),
        checklist_rows="\n".join(checklist_rows),
        delivery_review_baseline=delivery_review,
    )


def render_manual_handoff(
    hierarchy: dict[str, Any],
    *,
    hierarchy_fingerprint: str,
    graph_fingerprint: str,
    confirmed_by: str,
    created_at: str,
    receiver_prompt: str,
) -> str:
    """Render one portable, self-contained development handoff file."""

    delivery = hierarchy["delivery"]
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

    item_rows: list[str] = []
    item_details: list[str] = []
    for node, path in indexed_nodes:
        definition = node["definition"]
        dependencies = (
            definition["execution"]["dependsOn"]
            if definition["kind"] == "TASK"
            else definition["decomposition"]["dependsOn"]
        )
        item_rows.append(
            _table_row(
                [
                    path,
                    KIND_TEXT[definition["kind"]],
                    definition["id"],
                    definition["parentId"] or "无",
                    "、".join(dependencies) or "无",
                    definition["title"],
                    definition["summary"],
                ]
            )
        )
        item_details.extend(
            [
                f"### {KIND_TEXT[definition['kind']]}："
                f"{_markdown_text(definition['id'])}",
                "",
                f"- 层级路径：{_markdown_text(path)}",
                f"- 标题：{_markdown_text(definition['title'])}",
                f"- 摘要：{_markdown_text(definition['summary'])}",
                f"- 上级：{_markdown_text(definition['parentId'] or '无')}",
                (
                    "- 前置依赖："
                    + (
                        "、".join(
                            _markdown_text(item)
                            for item in dependencies
                        )
                        or "无"
                    )
                ),
                "",
            ]
        )
        if definition["kind"] == "TASK":
            item_details.extend(
                [
                    _render_loop_baseline(
                        "执行 Loop",
                        definition["execution"]["loop"],
                        heading_level=4,
                    ),
                    "",
                    _render_loop_baseline(
                        "TASK Review Loop",
                        node["reviewLoop"],
                        heading_level=4,
                    ),
                    "",
                ]
            )
        else:
            item_details.extend(
                [
                    "#### 直接子节点",
                    "",
                    (
                        "、".join(
                            _markdown_text(child["definition"]["id"])
                            for child in node["children"]
                        )
                        or "无"
                    ),
                    "",
                    _render_loop_baseline(
                        "GROUP Review Loop",
                        node["reviewLoop"],
                        heading_level=4,
                    ),
                    "",
                ]
            )

    hints = hierarchy["root"]["skillHints"]
    skill_lines = [
        f"- {_markdown_text(item['name'])}："
        f"{_markdown_text(item['purpose'])}"
        for item in hints
    ] or ["- 无"]
    machine_hierarchy = json.dumps(
        hierarchy,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return "\n".join(
        [
            "# 开发内容交接",
            "",
            "## 交接状态",
            "",
            "| 项目 | 当前结论 |",
            "|---|---|",
            f"| Delivery | {_markdown_text(delivery['id'])} |",
            f"| 标题 | {_markdown_text(delivery['title'])} |",
            f"| 确认人 | {_markdown_text(confirmed_by)} |",
            f"| 生成时间（UTC+8） | {_utc_plus_8(created_at)} |",
            "| 需求内容快照 | 已冻结（由双指纹锁定） |",
            "| Graph 调度状态 | 待接收 CLI 在实际工作区显式启动 |",
            "| 接收执行者与模型 | 交接前不指定；由接收宿主开始开发时确定并展示 |",
            "| 开发工作区 | 交接阶段不创建；开始实际开发时再创建或选择 |",
            "",
            "需求内容快照已冻结。本文件在交接阶段不创建接收任务、不认领 Loop，"
            "也不预先绑定任何 Agent、原生模型或实际代理模型；接收后必须先启动"
            "同一 Graph，不能脱离调度直接开发。",
            "",
            "## 接收 CLI 启动提示词",
            "",
            receiver_prompt,
            "",
            "## 完整性标识",
            "",
            f"- 数据结构版本：{hierarchy['root']['schemaVersion']}",
            f"- 层级指纹：{hierarchy_fingerprint}",
            f"- 调度图指纹：{graph_fingerprint}",
            "",
            "## Delivery 目标",
            "",
            f"- 标题：{_markdown_text(delivery['title'])}",
            f"- 摘要：{_markdown_text(delivery['summary'])}",
            "",
            "### 规划时 Git 信息",
            "",
            _render_git_binding_baseline(delivery),
            "",
            "### 规划时项目范围",
            "",
            _render_project_scopes(delivery),
            "",
            "接收方开始开发时必须按实际工作区重新校准路径、Git 分支绑定"
            "和项目授权；交接文件中的规划时路径不等于已创建开发环境。",
            "",
            "## GROUP/TASK 总览",
            "",
            "| 层级路径 | 类型 | 标识 | 上级 | 前置依赖 | 标题 | 摘要 |",
            "|---|---|---|---|---|---|---|",
            *item_rows,
            "",
            "## GROUP/TASK 开发输入",
            "",
            *item_details,
            "## Delivery Review 输入",
            "",
            _render_loop_baseline(
                "Delivery Review Loop",
                delivery["reviewLoop"],
                heading_level=3,
            ),
            "",
            "## 共享 Skill 提示",
            "",
            *skill_lines,
            "",
            "## 接收后启动完整交付流程",
            "",
            "1. 切换到任意 CLI，读取本目录中的 overview、baseline、progress、"
            "acceptance、revisions、work-items 和本交接文件。",
            "2. 校验本文件记录的层级指纹与调度图指纹；二者共同标识本次已冻结需求。",
            "3. 在当前实际 workspace 中串行接收本 Delivery。上一 Delivery 必须"
            "已有可验证提交、工作树和索引干净、HEAD 未漂移且所有接收方已安全释放；"
            "否则等待或停止，不切换分支，也不创建新的 worktree。",
            "4. 按实际工作区校准 projectScopes、gitBinding 和本地路径，但不得"
            "静默改变已冻结的业务目标、TASK、依赖或验收标准。",
            "5. 在任何代码检查、分析、修改或测试前，使用本文件的 Delivery ID 和"
            "双指纹调用 start_manual_handoff；该操作绑定实际工作区并创建 manual "
            "Graph Run。",
            "6. 总协调上下文只消费 frontier。每个 CLAIM_MANUAL_TASK 在独立接收"
            "上下文中以 dispatch_mode=MANUAL claim，随后 heartbeat、上报进度、"
            "完成实现与验证并提交标准结果。",
            "7. TASK 成功后继续消费 frontier；TASK Review、各层 GROUP Review 和"
            "Delivery Review 必须使用与自动执行相同的宿主原生自动派遣、独立上下文、"
            "问题分级闭环和验证协议，全部成功后等待真实用户确认。",
            "8. progress、acceptance 和 work-items 均由控制器事件投影刷新；不得"
            "直接编辑，不得用手动记录替代任何 Review Loop。",
            "9. 若需求范围发生变化，停止使用旧快照并回到需求会话重新生成；"
            "不要直接改写旧指纹所代表的需求。",
            "",
            "如果当前 workspace 尚未达到上述串行切换边界，这只表示本 Delivery"
            "仍在等待；不要宣称 Graph 已启动，也不要创建新的 worktree。边界满足并"
            "切到冻结分支后调用 start_manual_handoff；若调用结果未知，先用"
            "workspace_status 和明确的 rootId 核对同一双指纹的 manual run，已启动"
            "则从 graph_frontier 幂等恢复，不创建重复 TASK。",
            "",
            "## 机器可读 schema v3",
            "",
            "以下附录与上面的开发内容属于同一个已冻结快照。接收方可读取后"
            "校准工作区字段，不应把规划时 Agent 或模型补写进 hierarchy。",
            "",
            "```json",
            machine_hierarchy,
            "```",
            "",
        ]
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
            task_rows.append(
                _progress_state_row(
                    states,
                    loop_node_id(definition["id"]),
                    prefix=[path, "TASK"],
                    suffix=[progress_link],
                )
            )
            if node["reviewLoop"] is not None:
                task_rows.append(
                    _progress_state_row(
                        states,
                        task_review_node_id(definition["id"]),
                        prefix=[path, "TASK Review"],
                        suffix=[progress_link],
                    )
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
        "| 层级路径 | 阶段 | 当前进度 | 执行代理 | 宿主观测模型 | "
        "认领身份 | 执行轮次 | "
        "最近更新时间（UTC+8） | 结果摘要 | 节点进展 |"
    )
    table_separator = (
        "|---|---|---|---|---|---|---:|---|---|---|"
    )
    delivery_review_lines = [table_header, table_separator]
    if hierarchy["delivery"]["reviewLoop"] is None:
        delivery_review_lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_text(hierarchy["delivery"]["id"]),
                    "LIGHT：不创建独立 Delivery Review",
                    "不适用",
                    "不适用",
                    "不适用",
                    "不适用",
                    "0",
                    "不适用",
                    "由用户直接确认",
                    "[查看验收](acceptance.md)",
                ]
            )
            + " |"
        )
    else:
        delivery_review_lines.append(
            _progress_state_row(
                states,
                review_node_id(hierarchy["delivery"]["id"]),
                prefix=[
                    hierarchy["delivery"]["id"],
                    "Delivery Review",
                ],
                suffix=["[查看验收](acceptance.md)"],
            )
        )
    progress_monitor = (
        run.get("progressMonitor", {}).get("markdownTable")
        if isinstance(run, dict)
        and isinstance(run.get("progressMonitor"), dict)
        else None
    )
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
        progress_monitor=(
            progress_monitor
            or "- 尚无运行监控数据；主 Agent 将在下一次调度轮询时刷新。"
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


def _workspace_change_lines(result: dict[str, Any]) -> list[str]:
    raw_snapshots = result.get("workspaceChanges")
    if raw_snapshots is None:
        return []
    lines = [
        "#### 工作区变更证据",
        "",
        (
            "- 证据语义：以下内容是 Loop 提交结果时，相对冻结 "
            "`baseCommit` 的已验证 Git 工作区快照；它不声明这些变更只由"
            "当前 TASK、Loop 或 Delivery 产生。多个 Delivery 共享同一实际 "
            "workspace 时，快照可能包含其他 Delivery 的改动。"
        ),
    ]
    if not isinstance(raw_snapshots, list):
        return [
            *lines,
            "- `workspaceChanges` 格式无效，必须为 Controller 快照数组。",
        ]
    snapshots = [
        item for item in raw_snapshots if isinstance(item, dict)
    ]
    if not snapshots:
        return [
            *lines,
            "- 本 Loop 没有可采集的 READ_WRITE Git project scope。",
        ]
    for snapshot in snapshots:
        project_id = snapshot.get("projectId", "未声明")
        workspace_root = snapshot.get("workspaceRoot", "未声明")
        base_commit = snapshot.get("baseCommit", "未声明")
        head_commit = snapshot.get("headCommit", "未声明")
        snapshot_fingerprint = snapshot.get(
            "snapshotFingerprint",
            "未声明",
        )
        lines.extend(
            [
                "",
                f"##### 项目 {_markdown_code(project_id)}",
                "",
                f"- 实际工作区：{_markdown_code(workspace_root)}",
                f"- 冻结基线：{_markdown_code(base_commit)}",
                f"- 当前 HEAD：{_markdown_code(head_commit)}",
                (
                    "- 快照指纹："
                    f"{_markdown_code(snapshot_fingerprint)}"
                ),
                "",
                "###### 变更文件",
                "",
            ]
        )
        raw_files = snapshot.get("changedFiles")
        changed_files = (
            [item for item in raw_files if isinstance(item, dict)]
            if isinstance(raw_files, list)
            else []
        )
        if not changed_files:
            lines.append("- 相对冻结基线没有文件变化。")
        else:
            for item in changed_files:
                status = str(item.get("status", "UNKNOWN"))
                status_text = WORKSPACE_CHANGE_STATUS_TEXT.get(
                    status,
                    status,
                )
                previous_path = item.get("previousPath")
                path_text = _markdown_code(item.get("path", "未声明"))
                if previous_path is not None:
                    path_text = (
                        f"{_markdown_code(previous_path)} → {path_text}"
                    )
                lines.append(f"- {status_text}：{path_text}")
        diff = snapshot.get("diff")
        lines.extend(["", "###### Diff 快照", ""])
        if isinstance(diff, str) and diff:
            lines.extend(_markdown_diff_block(diff))
        else:
            lines.append("- 相对冻结基线没有可展示的文本 diff。")
        if snapshot.get("diffTruncated") is True:
            lines.extend(
                [
                    "",
                    (
                        "- Diff 已按 Controller 上限截断；截断前 UTF-8 "
                        f"字节数：{snapshot.get('diffByteCount', '未知')}。"
                    ),
                ]
            )
    return lines


def _task_workspace_change_sections(
    node: dict[str, Any],
    states: dict[str, dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    definition = node["definition"]
    if definition["kind"] != "TASK":
        return []
    stage_nodes = [
        ("TASK_LOOP", loop_node_id(definition["id"])),
    ]
    if node["reviewLoop"] is not None:
        stage_nodes.append(
            (
                "TASK_REVIEW_LOOP",
                task_review_node_id(definition["id"]),
            )
        )
    sections: list[tuple[str, dict[str, Any]]] = []
    for stage, node_id in stage_nodes:
        state = states.get(node_id)
        outcome = state.get("outcome") if state is not None else None
        result = outcome.get("result") if isinstance(outcome, dict) else None
        raw_snapshots = (
            result.get("workspaceChanges")
            if isinstance(result, dict)
            else None
        )
        if not isinstance(raw_snapshots, list):
            continue
        sections.extend(
            (stage, snapshot)
            for snapshot in raw_snapshots
            if isinstance(snapshot, dict)
        )
    return sections


def _patch_header_value(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ")


def _render_task_workspace_changes_patch(
    node: dict[str, Any],
    states: dict[str, dict[str, Any]],
) -> str | None:
    sections = _task_workspace_change_sections(node, states)
    if not sections:
        return None
    lines = [
        "# delivery-graph workspace change snapshots",
        "#",
        (
            "# Snapshot evidence only: shared workspaces may contain changes "
            "from other Deliveries."
        ),
        "# This bundle does not assert exclusive TASK/Loop/Delivery ownership.",
    ]
    for position, (stage, snapshot) in enumerate(sections, start=1):
        lines.extend(
            [
                "",
                "# ============================================================",
                f"# Snapshot: {position}",
                f"# Stage: {_patch_header_value(stage)}",
                (
                    "# Project: "
                    f"{_patch_header_value(snapshot.get('projectId', 'unknown'))}"
                ),
                (
                    "# Workspace: "
                    f"{_patch_header_value(snapshot.get('workspaceRoot', 'unknown'))}"
                ),
                (
                    "# Frozen baseCommit: "
                    f"{_patch_header_value(snapshot.get('baseCommit', 'unknown'))}"
                ),
                (
                    "# Current HEAD: "
                    f"{_patch_header_value(snapshot.get('headCommit', 'unknown'))}"
                ),
                (
                    "# Snapshot fingerprint: "
                    + _patch_header_value(
                        snapshot.get("snapshotFingerprint", "unknown")
                    )
                ),
                (
                    "# Attribution: "
                    f"{_patch_header_value(snapshot.get('attribution', 'unknown'))}"
                ),
            ]
        )
        if snapshot.get("diffTruncated") is True:
            lines.append(
                "# Diff truncated by Controller; original UTF-8 bytes: "
                + _patch_header_value(
                    snapshot.get("diffByteCount", "unknown")
                )
            )
        diff = snapshot.get("diff")
        if isinstance(diff, str) and diff:
            lines.extend(["", diff.rstrip()])
        else:
            lines.extend(["", "# No displayable text diff in this snapshot."])
    return "\n".join(lines).rstrip() + "\n"


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
    workspace_change_lines = _workspace_change_lines(result_payload)
    result_payload.pop("workspaceChanges", None)
    if workspace_change_lines:
        lines.extend(["", *workspace_change_lines])
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
    if delivery["reviewLoop"] is None:
        delivery_lines = [
            "### Delivery Review",
            "",
            "- LIGHT 保障档不创建 Delivery Review Loop；TASK 定向验证"
            "完成后直接进入用户确认。",
        ]
    else:
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
        "| 阶段 | 当前进度 | 执行代理 | 宿主观测模型 | "
        "认领身份 | 执行轮次 | "
        "最近更新时间（UTC+8） | 结果摘要 |"
    )
    progress_separator = "|---|---|---|---|---|---:|---|---|"
    if definition["kind"] == "TASK":
        task_progress_rows = [
            _progress_state_row(
                states,
                loop_node_id(definition["id"]),
                prefix=["TASK"],
            )
        ]
        if node["reviewLoop"] is not None:
            task_progress_rows.append(
                _progress_state_row(
                    states,
                    task_review_node_id(definition["id"]),
                    prefix=["TASK Review"],
                )
            )
        sections = "\n".join(
            [
                "## TASK Loop 状态",
                "",
                progress_header,
                progress_separator,
                *task_progress_rows,
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
        task_sections = []
        if _task_workspace_change_sections(node, states):
            task_sections.extend(
                [
                    "## 工作区变更附件",
                    "",
                    (
                        "- [打开工作区变更补丁]"
                        "(workspace-changes.patch)"
                    ),
                    (
                        "- 附件与下方 inline diff 均为 Controller 持久化的"
                        "提交时 workspace 快照，不代表当前 TASK/Delivery 的"
                        "独占归属。"
                    ),
                    "",
                ]
            )
        task_sections.extend([
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
        ])
        if node["reviewLoop"] is None:
            task_sections.extend(
                [
                    "",
                    "## 独立 Review",
                    "",
                    "- LIGHT 保障档不创建 TASK Review Loop；由用户直接确认结果。",
                ]
            )
        else:
            task_sections.extend(
                [
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
        sections = "\n".join(task_sections)
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


def _interface_projection_values(
    interface: dict[str, Any],
) -> dict[str, Any]:
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
    before_value = interface.get("before")
    after_value = interface.get("after")
    before = before_value if isinstance(before_value, dict) else None
    after = after_value if isinstance(after_value, dict) else None
    return {
        "protocol": protocol,
        "name": name,
        "summary": summary,
        "changeType": change_type,
        "changeText": INTERFACE_CHANGE_TYPE_TEXT.get(
            change_type,
            "未声明",
        ),
        "before": before,
        "after": after,
        "beforeIdentifier": _interface_call_identifier(protocol, before),
        "afterIdentifier": _interface_call_identifier(protocol, after),
    }


def _interface_filename_slug(value: str) -> str:
    characters: list[str] = []
    pending_separator = False
    for character in value.casefold():
        if character.isascii() and character.isalnum():
            if pending_separator and characters:
                characters.append("-")
            characters.append(character)
            pending_separator = False
        else:
            pending_separator = True
    return "".join(characters).strip("-")


def _interface_document_filename(
    position: int,
    values: dict[str, Any],
) -> str:
    name_slug = _interface_filename_slug(values["name"])
    identity = values["name"] if name_slug else values["afterIdentifier"]
    if identity == "不适用":
        identity = values["beforeIdentifier"]
    slug = _interface_filename_slug(
        f"{values['protocol']}-{identity}"
    )[:64].rstrip("-")
    return f"{position:03d}-{slug or 'interface'}.md"


def _interface_schema_type(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("type", "controllerReturnType"):
        candidate = value.get(key)
        if isinstance(candidate, (str, int, float)) and str(candidate).strip():
            return str(candidate).strip()
    return None


def _dubbo_method_signature(snapshot: dict[str, Any]) -> str:
    explicit = snapshot.get("signature")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    method = _interface_scalar(
        snapshot.get("method"),
        fallback="未声明方法",
    )
    response_type = _interface_schema_type(snapshot.get("response"))
    request = snapshot.get("request")
    parameters: list[str] = []
    if isinstance(request, dict):
        request_type = _interface_schema_type(request)
        request_name = request.get("name")
        if request_type is not None:
            parameters.append(
                " ".join(
                    part
                    for part in (
                        request_type,
                        (
                            str(request_name).strip()
                            if isinstance(request_name, str)
                            and request_name.strip()
                            else ""
                        ),
                    )
                    if part
                )
            )
    elif isinstance(request, list):
        for parameter in request:
            if not isinstance(parameter, dict):
                continue
            parameter_type = _interface_scalar(
                parameter.get("type"),
                fallback="Object",
            )
            parameter_name = _interface_scalar(
                parameter.get("name"),
                fallback="arg",
            )
            parameters.append(f"{parameter_type} {parameter_name}")
    return (
        f"{response_type or '未声明返回类型'} {method}"
        f"({', '.join(parameters)})"
    )


def _interface_protocol_metadata(
    values: dict[str, Any],
) -> list[str]:
    snapshot = values["after"] or values["before"]
    if snapshot is None:
        return []
    protocol = values["protocol"]
    if protocol == "HTTP":
        lines = [
            (
                "- 方法："
                f"{_markdown_text(_interface_scalar(snapshot.get('method'), fallback='未声明'))}"
            ),
            (
                "- 路径："
                f"{_markdown_text(_interface_scalar(snapshot.get('path'), fallback='未声明'))}"
            ),
        ]
        content_type = snapshot.get("contentType")
        if isinstance(content_type, str) and content_type.strip():
            lines.append(f"- Content-Type：{_markdown_text(content_type)}")
        response_type = _interface_schema_type(snapshot.get("response"))
        if response_type is not None:
            lines.append(f"- 返回类型：{_markdown_text(response_type)}")
        return lines
    if protocol == "DUBBO":
        return [
            (
                "- 接口："
                f"{_markdown_text(_interface_scalar(snapshot.get('service'), fallback='未声明'))}"
            ),
            f"- 方法：{_markdown_text(_dubbo_method_signature(snapshot))}",
        ]
    return []


def _interface_table_section(
    heading: str,
    table: list[str],
) -> str:
    return "\n".join([f"### {heading}", "", *table])


def _http_request_contract(
    before: object,
    after: object,
) -> str:
    sections: list[str] = []
    for path_group, heading in (
        ("path", "Path 参数"),
        ("query", "Query 参数"),
        ("header", "请求头"),
        ("body", "请求体"),
        ("business", "业务参数"),
        ("context", "上下文参数"),
        ("", "请求参数"),
    ):
        table = _interface_change_table(
            before,
            after,
            section="request",
            include_example=True,
            path_group=path_group,
            strip_path_group=bool(path_group),
            omit_container_rows=True,
            render_empty=False,
        )
        if table:
            sections.append(_interface_table_section(heading, table))
    return "\n\n".join(sections) if sections else "无"


def _http_response_contract(
    before: object,
    after: object,
) -> str:
    table = _interface_change_table(
        before,
        after,
        section="response",
        include_required=False,
        include_example=True,
        omit_container_rows=True,
        render_empty=False,
    )
    return _interface_table_section("响应参数", table) if table else "无"


def _dubbo_contract_table(
    before: object,
    after: object,
    *,
    section: str,
    heading: str,
) -> str:
    table = _interface_change_table(
        before,
        after,
        section=section,
        include_required=True,
        include_max_length=True,
        include_example=True,
        omit_container_rows=True,
        render_empty=False,
    )
    return _interface_table_section(heading, table) if table else "无"


def _render_task_interface_detail(
    definition: dict[str, Any],
    values: dict[str, Any],
) -> str:
    before = values["before"]
    after = values["after"]
    metadata = "\n".join(
        [
            f"- 来源 TASK：{_markdown_text(definition['id'])}",
            f"- 协议：{_markdown_text(values['protocol'])}",
            f"- 接口名称：{_markdown_text(values['name'])}",
            f"- 变更类型：{_markdown_text(values['changeText'])}",
            f"- 简介：{_markdown_text(values['summary'])}",
            (
                "- 调用标识（修改前 → 修改后）："
                f"{_markdown_text(values['beforeIdentifier'])} → "
                f"{_markdown_text(values['afterIdentifier'])}"
            ),
            *_interface_protocol_metadata(values),
        ]
    )
    before_request = (
        before.get("request", "未声明") if before is not None else None
    )
    after_request = (
        after.get("request", "未声明") if after is not None else None
    )
    before_response = (
        before.get("response", "未声明") if before is not None else None
    )
    after_response = (
        after.get("response", "未声明") if after is not None else None
    )
    if values["protocol"] == "HTTP":
        request_table = _http_request_contract(
            before_request,
            after_request,
        )
        response_table = _http_response_contract(
            before_response,
            after_response,
        )
    elif values["protocol"] == "DUBBO":
        request_table = _dubbo_contract_table(
            before_request,
            after_request,
            section="request",
            heading="调用参数",
        )
        response_table = _dubbo_contract_table(
            before_response,
            after_response,
            section="response",
            heading="返回结果",
        )
    else:
        request_table = "\n".join(
            _interface_change_table(
                before_request,
                after_request,
                section="request",
            )
        )
        response_table = "\n".join(
            _interface_change_table(
                before_response,
                after_response,
                section="response",
                include_required=False,
            )
        )
    return INTERFACE_DETAIL_PROJECTION_TEMPLATE.substitute(
        protocol=_markdown_text(values["protocol"]),
        name=_markdown_text(values["name"]),
        interface_metadata=metadata,
        request_table=request_table,
        response_table=response_table,
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
    for position, interface in enumerate(declarations, start=1):
        values = _interface_projection_values(interface)
        filename = _interface_document_filename(position, values)
        name_link = (
            f"[{_markdown_text(values['name'])}]"
            f"(interfaces/{filename})"
        )
        if values["changeType"] == "DELETE":
            name_link = f"~~{name_link}~~"
        rows.append(
            _table_row(
                [
                    definition["id"],
                    values["protocol"],
                    name_link,
                    values["changeText"],
                    values["beforeIdentifier"],
                    values["afterIdentifier"],
                    values["summary"],
                ],
                raw_indices={2},
            )
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
        delivery_baseline=delivery_baseline,
    )


def render_task_interface_documents(
    definition: dict[str, Any],
    **index_arguments: Any,
) -> dict[str, str]:
    if definition["kind"] != "TASK":
        raise ValueError("Interface projection requires a TASK definition")
    documents = {
        "interfaces.md": render_task_interfaces(
            definition,
            **index_arguments,
        )
    }
    for position, interface in enumerate(
        _task_interface_declarations(definition),
        start=1,
    ):
        values = _interface_projection_values(interface)
        filename = _interface_document_filename(position, values)
        documents[f"interfaces/{filename}"] = (
            _render_task_interface_detail(definition, values)
        )
    return documents


def _database_identity(change: dict[str, Any]) -> str:
    return ".".join(
        str(change[key]).strip()
        for key in ("projectId", "database", "schema", "table")
        if key in change and str(change[key]).strip()
    )


def _database_document_filename(
    position: int,
    change: dict[str, Any],
) -> str:
    slug = _interface_filename_slug(_database_identity(change))[
        :64
    ].rstrip("-")
    return f"{position:03d}-{slug or 'table'}.md"


def _database_scalar(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _database_column_table(change: dict[str, Any]) -> str:
    before = change.get("before")
    after = change.get("after")
    before_columns = (
        before.get("columns", []) if isinstance(before, dict) else []
    )
    after_columns = (
        after.get("columns", []) if isinstance(after, dict) else []
    )
    before_by_name = {
        item["name"]: item
        for item in before_columns
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    after_by_name = {
        item["name"]: item
        for item in after_columns
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    names = [
        *before_by_name,
        *(name for name in after_by_name if name not in before_by_name),
    ]
    attributes = ("type", "nullable", "default", "comment")
    rows: list[str] = []
    for name in names:
        old = before_by_name.get(name)
        new = after_by_name.get(name)
        if old is None:
            state = "新增"
        elif new is None:
            state = "删除"
        elif any(old.get(key) != new.get(key) for key in attributes):
            state = "修改"
        else:
            state = "未变"

        def transition(key: str) -> str:
            if old is None:
                return _database_scalar(new.get(key))
            if new is None:
                return _database_scalar(old.get(key))
            old_value = _database_scalar(old.get(key))
            new_value = _database_scalar(new.get(key))
            return (
                old_value
                if old_value == new_value
                else f"{old_value} → {new_value}"
            )

        values = [name, state, *(transition(key) for key in attributes)]
        if new is None:
            values = [
                value if index == 1 else f"~~{value}~~"
                for index, value in enumerate(values)
            ]
        rows.append(_table_row(values))
    return "\n".join(
        [
            (
                "| 字段 | 变更 | 类型（修改前 → 修改后） | "
                "可空（修改前 → 修改后） | 默认值（修改前 → 修改后） | "
                "注释（修改前 → 修改后） |"
            ),
            "|---|---|---|---|---|---|",
            *rows,
        ]
    )


def _database_snapshot(value: object) -> str:
    if value is None:
        return "- 不适用"
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return "\n".join(f"    {line}" for line in serialized.splitlines())


def _render_database_change_detail(
    change: dict[str, Any],
) -> str:
    migration = change["migration"]
    metadata = "\n".join(
        [
            f"- 表标识：{_markdown_text(_database_identity(change))}",
            (
                "- 变更类型："
                + _markdown_text(
                    INTERFACE_CHANGE_TYPE_TEXT.get(
                        change["changeType"],
                        change["changeType"],
                    )
                )
            ),
            f"- 简介：{_markdown_text(change['summary'])}",
            f"- 排他资源锁：{_markdown_text(change['resourceClaim'])}",
        ]
    )
    migration_lines = [
        f"- 正向迁移：{_markdown_text(migration['forward'])}",
        f"- 回滚方案：{_markdown_text(migration['rollback'])}",
        f"- 数据回填：{_markdown_text(migration['backfill'])}",
        f"- 发布兼容：{_markdown_text(migration['compatibility'])}",
        "- 验证要求：",
        *(
            f"  - {_markdown_text(item)}"
            for item in migration["verification"]
        ),
    ]
    return DATABASE_CHANGE_DETAIL_PROJECTION_TEMPLATE.substitute(
        table=_markdown_text(_database_identity(change)),
        metadata=metadata,
        column_table=_database_column_table(change),
        before_snapshot=_database_snapshot(change.get("before")),
        after_snapshot=_database_snapshot(change.get("after")),
        migration="\n".join(migration_lines),
    )


def render_task_database_changes(
    definition: dict[str, Any],
    *,
    delivery_baseline: str = "../../baseline.md",
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
) -> str:
    if definition["kind"] != "TASK":
        raise ValueError("Database projection requires a TASK definition")
    rows: list[str] = []
    for position, change in enumerate(
        _task_database_declarations(definition),
        start=1,
    ):
        filename = _database_document_filename(position, change)
        name = (
            f"[{_markdown_text(_database_identity(change))}]"
            f"(database-changes/{filename})"
        )
        if change["changeType"] == "DELETE":
            name = f"~~{name}~~"
        before = change.get("before")
        after = change.get("after")
        rows.append(
            _table_row(
                [
                    definition["id"],
                    name,
                    INTERFACE_CHANGE_TYPE_TEXT[change["changeType"]],
                    str(len(before["columns"])) if before else "不适用",
                    str(len(after["columns"])) if after else "不适用",
                    change["resourceClaim"],
                    change["summary"],
                ],
                raw_indices={1},
            )
        )
    database_rows = "\n".join(
        [
            (
                "| 来源 TASK | 表标识 | 变更类型 | 修改前字段数 | "
                "修改后字段数 | 排他资源锁 | 简介 |"
            ),
            "|---|---|---|---|---|---|---|",
            *rows,
        ]
    )
    return DATABASE_CHANGES_PROJECTION_TEMPLATE.substitute(
        database_status="\n".join(
            _work_item_status_lines(
                definition,
                hierarchy_fingerprint=hierarchy_fingerprint,
                graph_fingerprint=graph_fingerprint,
                hierarchy_status=hierarchy_status,
                updated_at=updated_at,
            )
        ),
        delivery_baseline=delivery_baseline,
        database_rows=database_rows,
    )


def render_task_database_documents(
    definition: dict[str, Any],
    **index_arguments: Any,
) -> dict[str, str]:
    documents = {
        "database-changes.md": render_task_database_changes(
            definition,
            **index_arguments,
        )
    }
    for position, change in enumerate(
        _task_database_declarations(definition),
        start=1,
    ):
        filename = _database_document_filename(position, change)
        documents[f"database-changes/{filename}"] = (
            _render_database_change_detail(change)
        )
    return documents


def render_projection_documents(
    stored_definition: dict[str, Any],
    run: dict[str, Any] | None,
    revision_history: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Render the complete Delivery human projection set.

    The definition is either controller-owned SQLite state or a validated,
    portable manual-handoff snapshot. Callers cannot supply a template or
    filename.
    """

    hierarchy = stored_definition["hierarchy"]
    updated_at = (
        stored_definition["updatedAt"]
        if stored_definition["status"] == "ARCHIVED" or run is None
        else run["updatedAt"]
    )
    human_projection_arguments = {
        "hierarchy_fingerprint": stored_definition[
            "hierarchyFingerprint"
        ],
        "graph_fingerprint": stored_definition["graphFingerprint"],
        "hierarchy_status": stored_definition["status"],
        "updated_at": updated_at,
    }
    documents = {
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
    history = revision_history or {
        "currentRevision": stored_definition.get(
            "deliveryRevision",
            1,
        ),
        "revisions": [],
    }
    rows = []
    for item in history["revisions"]:
        rows.append(
            "| "
            + " | ".join(
                [
                    str(item["revision"]),
                    _markdown_text(item["status"]),
                    _markdown_text(item["runStatus"] or "NOT_STARTED"),
                    _markdown_text(item["reason"] or "初始范围"),
                    _markdown_text(
                        "、".join(item["authorizedProjectIds"]) or "无"
                    ),
                    _utc_plus_8(item["updatedAt"]),
                ]
            )
            + " |"
        )
    documents["revisions.md"] = "\n".join(
        [
            "# Delivery 修订历史",
            "",
            f"- 交付标识：{_markdown_text(stored_definition['rootId'])}",
            f"- 当前修订：{history['currentRevision']}",
            "",
            "每个修订的 Graph 与需求指纹均保存在 SQLite；旧修订只读保留。",
            "",
            "| 修订 | 范围状态 | 运行状态 | 变更原因 | 已授权项目 | 最近更新（UTC+8） |",
            "|---|---|---|---|---|---|",
            *(
                rows
                or [
                    "| 1 | PREPARED | NOT_STARTED | 初始范围 | 无 | "
                    + _utc_plus_8(stored_definition["updatedAt"])
                    + " |"
                ]
            ),
            "",
        ]
    )
    return documents


def render_work_item_projection_documents(
    stored_definition: dict[str, Any],
    run: dict[str, Any] | None,
) -> dict[str, str]:
    """Render the exact GROUP/TASK tree from validated Delivery state."""

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
    task_requirements = {
        item["taskId"]: item
        for item in (
            run.get("taskRequirements", [])
            if run is not None
            else []
        )
    }
    dynamic_arguments = {
        "hierarchy_fingerprint": stored_definition[
            "hierarchyFingerprint"
        ],
        "graph_fingerprint": stored_definition["graphFingerprint"],
        "hierarchy_status": stored_definition["status"],
        "updated_at": (
            stored_definition["updatedAt"]
            if stored_definition["status"] == "ARCHIVED" or run is None
            else run["updatedAt"]
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
                task_requirement=task_requirements.get(item_id),
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
        workspace_changes_patch = _render_task_workspace_changes_patch(
            node,
            _projection_states(run),
        )
        if workspace_changes_patch is not None:
            documents[
                f"{tree_directory}/workspace-changes.patch"
            ] = workspace_changes_patch
        if _task_interface_declarations(definition):
            interface_documents = render_task_interface_documents(
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
            for filename, content in interface_documents.items():
                documents[f"{tree_directory}/{filename}"] = content
        if _task_database_declarations(definition):
            database_documents = render_task_database_documents(
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
            for filename, content in database_documents.items():
                documents[f"{tree_directory}/{filename}"] = content
    return documents


__all__ = (
    "ACCEPTANCE_PROJECTION_TEMPLATE",
    "BASELINE_PROJECTION_TEMPLATE",
    "DATABASE_CHANGE_DETAIL_PROJECTION_TEMPLATE",
    "DATABASE_CHANGES_PROJECTION_TEMPLATE",
    "INTERFACE_DETAIL_PROJECTION_TEMPLATE",
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
    "has_database_projection",
    "raw_definition",
    "render_delivery_acceptance",
    "render_delivery_baseline",
    "render_delivery_progress",
    "render_group_baseline",
    "render_manual_handoff",
    "render_projection_documents",
    "render_scheduling_plan",
    "render_task_interface_documents",
    "render_task_interfaces",
    "render_task_database_changes",
    "render_task_database_documents",
    "render_work_item_baseline",
    "render_work_item_acceptance",
    "render_work_item_progress",
    "render_work_item_projection_documents",
    "render_workspace_overview",
    "task_baseline_relative_path",
    "task_has_interface_projection",
    "task_has_database_projection",
    "work_item_projection_relative_path",
    "work_item_projection_directories",
)
