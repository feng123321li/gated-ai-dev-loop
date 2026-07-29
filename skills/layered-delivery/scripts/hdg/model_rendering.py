from __future__ import annotations

from datetime import datetime, timedelta, timezone
from string import Template
from types import MappingProxyType
from typing import Any

from .graph_model import (
    confirmation_node_id,
    group_review_node_id,
    join_node_id,
    loop_node_id,
    review_node_id,
)
from .jsonio import pretty_json
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
        "evidence": "验证证据",
        "files": "相关文件",
        "goal": "目标",
        "inputs": "输入",
        "modules": "相关模块",
        "nested": "嵌套信息",
        "notes": "补充说明",
        "output": "输出",
        "outputs": "输出",
        "process": "处理流程",
        "rawAuditMarker": "原始审计标记",
        "requirements": "需求说明",
        "reviewFocus": "审查重点",
        "risks": "风险",
        "scope": "处理范围",
        "services": "相关服务",
        "steps": "执行步骤",
        "successCriteria": "成功标准",
        "testRequirements": "测试要求",
        "tests": "测试要求",
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
PROJECTION_TEMPLATE_VERSION = 3
TASK_BASELINE_DIRECTORY = "task-baselines"
JSON_PROJECTION_TEMPLATE = Template("${document}\n")
WORKSPACE_OVERVIEW_PROJECTION_TEMPLATE = Template(
    """# 全部交付调度与进度总览

## 工作区状态

- 投影模板版本：${template_version}
- 交付数量：${delivery_count}
- 更新时间（UTC+8）：${updated_at}

本文件由控制器从 SQLite 权威状态统一生成，用于查看工作区内全部交付需求。

## Delivery 清单

| 交付标识 | 需求标题 | 需求摘要 | 当前状态 | TASK 进度 | GROUP 数量 | 最近更新（UTC+8） | 交付详情 |
|---|---|---|---|---|---|---|---|
${delivery_rows}
"""
)
OVERVIEW_PROJECTION_TEMPLATE = Template(
    """# 交付调度与进度总览

## 交付状态

- 投影模板版本：${template_version}
${delivery_status}

实现规范、测试、门禁与 Skill 激活由各 Loop 内部负责。
TASK 调度基线和执行输入已拆分到固定的 `task-baselines/` 投影。
Skill 提示在 Loop 启动后按真实上下文选择，不预先绑定节点。

## Skill 提示

${skill_hints}

## GROUP/TASK 清单

| 层级路径 | 节点类型 | 上级 | 前置依赖 | 当前状态 | 标题 | 任务基线 |
|---|---|---|---|---|---|---|
${checklist_rows}

## TASK 执行进度

${task_progress}
## GROUP 协调与审查

${group_details}
## 交付最终审查

${delivery_review}
## 最终用户确认

${confirmation}
"""
)
TASK_BASELINE_PROJECTION_TEMPLATE = Template(
    """# TASK 调度基线

## 基线标识

- 投影模板版本：${template_version}
- 交付标识：${delivery_id}
- 交付标题：${delivery_title}
- 任务标识：${task_id}
- 层级状态：${hierarchy_status}
- 层级指纹：${hierarchy_fingerprint}
- 调度图指纹：${graph_fingerprint}
- 更新时间（UTC+8）：${updated_at}

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

## 共享 Skill 提示

${skill_hints}

实现方案、文件、测试、Gate、修正循环和实际 Skill 选择由本 TASK Loop
在独立执行上下文中负责；本文件只投影控制器可见的冻结调度输入。
"""
)
PROJECTION_TEMPLATES = MappingProxyType(
    {
        "hierarchy.json": JSON_PROJECTION_TEMPLATE,
        "graph.json": JSON_PROJECTION_TEMPLATE,
        "state.json": JSON_PROJECTION_TEMPLATE,
        "overview.md": OVERVIEW_PROJECTION_TEMPLATE,
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
    return parsed.astimezone(UTC_PLUS_8).isoformat(timespec="seconds")


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


def task_baseline_relative_path(task_id: str) -> str:
    return f"{TASK_BASELINE_DIRECTORY}/{task_id}.md"


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
            states.get(loop_node_id(task["id"]))
            in {"SUCCEEDED", "COMPLETED"}
            for task in tasks
        )
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
            hierarchy["delivery"]["summary"],
            _status_text(status),
            f"已完成 {completed_tasks}/{len(tasks)}",
            str(len(groups)),
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
            or "| 无 | 无 | 无 | 无 | 无 | 0 | 无 | 无 |"
        ),
    )


def render_work_item_baseline(
    definition: dict[str, Any],
    *,
    delivery: dict[str, Any] | None = None,
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

    if definition["kind"] != "TASK":
        raise ValueError("TASK baseline requires a TASK definition")
    loop = definition["execution"]["loop"]
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
            loop["payload"],
            heading_level=3,
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
    delivery = hierarchy["delivery"]
    states = {
        item["nodeId"]: item
        for item in (run or {}).get("nodes", [])
    }

    def state_text(node_id: str) -> str:
        state = states.get(node_id)
        raw = state["status"] if state is not None else "NOT_STARTED"
        return _status_text(raw)

    def state_lines(node_id: str) -> list[str]:
        state = states.get(node_id)
        if state is None:
            return [
                f"- 调度节点：{_markdown_text(node_id)}",
                f"- 当前进度：{_status_text('NOT_STARTED')}",
            ]
        lines = [
            f"- 调度节点：{_markdown_text(node_id)}",
            f"- 当前进度：{_status_text(state['status'])}",
            f"- 尝试次数：{state['attempt']}",
        ]
        if state["owner"]:
            lines.append(
                f"- 执行者：{_markdown_text(state['owner'])}"
            )
        if state["failureClass"]:
            lines.append(
                "- 失败分类："
                f"{_failure_class_text(state['failureClass'])}"
            )
        for key, label in (
            ("claimedAt", "认领时间"),
            ("lastHeartbeatAt", "最近心跳"),
            ("leaseExpiresAt", "租约到期"),
            ("finishedAt", "结束时间"),
        ):
            if state[key]:
                lines.append(
                    f"- {label}（UTC+8）：{_utc_plus_8(state[key])}"
                )
        outcome = state["outcome"]
        if outcome is not None:
            if outcome.get("status"):
                lines.append(
                    "- 结果状态："
                    f"{_status_text(outcome['status'])}"
                )
            if outcome.get("summary"):
                lines.append(
                    "- 结果摘要："
                    f"{_markdown_text(outcome['summary'])}"
                )
            if outcome.get("confirmedBy"):
                lines.append(
                    "- 确认人："
                    f"{_markdown_text(outcome['confirmedBy'])}"
                )
        return lines

    def loop_lines(
        label: str,
        loop: dict[str, Any],
        node_id: str,
    ) -> list[str]:
        lines = [
            f"#### {label}",
            "",
            f"- Loop 引用：{_markdown_text(loop['ref'])}",
            (
                "- 资源锁："
                + (
                    "、".join(
                        _markdown_text(item)
                        for item in loop["resourceClaims"]
                    )
                    or "无"
                )
            ),
        ]
        lines.extend(state_lines(node_id))
        lines.extend(
            [
                "",
                "##### 审查输入",
                "",
                _render_payload_markdown(
                    loop["payload"],
                    heading_level=6,
                ),
                "",
            ]
        )
        return lines

    baseline_lines = [
        f"- 交付标识：{_markdown_text(delivery['id'])}",
        f"- 标题：{_markdown_text(delivery['title'])}",
        f"- 摘要：{_markdown_text(delivery['summary'])}",
        f"- 数据结构版本：{hierarchy['root']['schemaVersion']}",
        (
            "- 层级状态："
            f"{_status_text(hierarchy_status or 'UNKNOWN')}"
        ),
        (
            "- 运行状态："
            f"{_status_text((run or {}).get('status', 'NOT_STARTED'))}"
        ),
        (
            "- 层级指纹："
            f"{hierarchy_fingerprint or '不可用'}"
        ),
        (
            "- 调度图指纹："
            f"{graph_fingerprint or '不可用'}"
        ),
    ]
    if run is not None:
        baseline_lines.extend(
            [
                f"- 运行标识：{_markdown_text(run['runId'])}",
                f"- 启动时间（UTC+8）：{_utc_plus_8(run['startedAt'])}",
            ]
        )
    baseline_lines.append(
        f"- 更新时间（UTC+8）：{_utc_plus_8(updated_at)}"
    )
    hints = hierarchy["root"]["skillHints"]
    if hints:
        skill_hint_lines = [
            f"- {_markdown_text(hint['name'])}："
            f"{_markdown_text(hint['purpose'])}"
            for hint in hints
        ]
    else:
        skill_hint_lines = ["- 无"]
    checklist_rows: list[str] = []
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
    for node, path in indexed_nodes:
        definition = node["definition"]
        dependencies = (
            definition["execution"]["dependsOn"]
            if definition["kind"] == "TASK"
            else definition["decomposition"]["dependsOn"]
        )
        if definition["kind"] == "TASK":
            progress = state_text(loop_node_id(definition["id"]))
        else:
            progress = (
                "汇合："
                f"{state_text(join_node_id(definition['id']))}；"
                "审查："
                f"{state_text(group_review_node_id(definition['id']))}"
            )
        cells = [
            path,
            KIND_TEXT[definition["kind"]],
            definition["parentId"] or "无",
            "、".join(dependencies) or "无",
            progress,
            definition["title"],
            (
                f"[{definition['id']}.md]"
                f"({task_baseline_relative_path(definition['id'])})"
                if definition["kind"] == "TASK"
                else "无"
            ),
        ]
        checklist_rows.append(
            "| "
            + " | ".join(
                [
                    *(_markdown_text(cell) for cell in cells[:-1]),
                    cells[-1],
                ]
            )
            + " |"
        )
    task_progress_lines: list[str] = []
    group_detail_lines: list[str] = []
    for node in iter_hierarchy_nodes(hierarchy):
        definition = node["definition"]
        if definition["kind"] == "TASK":
            task_node_id = loop_node_id(definition["id"])
            task_progress_lines.extend(
                [
                    (
                        "### TASK："
                        f"{_markdown_text(definition['title'])}"
                    ),
                    "",
                    (
                        "- 任务标识："
                        f"{_markdown_text(definition['id'])}"
                    ),
                    *state_lines(task_node_id),
                    "",
                ]
            )
            continue
        dependencies = definition["decomposition"]["dependsOn"]
        group_detail_lines.extend(
            [
                (
                    "### GROUP："
                    f"{_markdown_text(definition['title'])}"
                ),
                "",
                (
                    "- 分组标识："
                    f"{_markdown_text(definition['id'])}"
                ),
                f"- 摘要：{_markdown_text(definition['summary'])}",
                (
                    "- 上级："
                    f"{_markdown_text(definition['parentId'] or '无')}"
                ),
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
            ]
        )
        group_detail_lines.extend(
            [
                (
                    "- 直接子级："
                    + "、".join(
                        (
                            f"{_markdown_text(child['id'])}"
                            f"（{KIND_TEXT[child['kind']]}）"
                        )
                        for child in definition["children"]
                    )
                ),
                "",
                "#### 分组汇合进度",
                "",
                *state_lines(join_node_id(definition["id"])),
                "",
            ]
        )
        group_detail_lines.extend(
            loop_lines(
                "分组审查 Loop",
                node["reviewLoop"],
                group_review_node_id(definition["id"]),
            )
        )
    delivery_review_id = review_node_id(delivery["id"])
    confirmation_id = confirmation_node_id(delivery["id"])
    delivery_review_lines = loop_lines(
        "交付审查 Loop",
        delivery["reviewLoop"],
        delivery_review_id,
    )
    confirmation_lines = state_lines(confirmation_id)
    return OVERVIEW_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        delivery_status="\n".join(baseline_lines),
        skill_hints="\n".join(skill_hint_lines),
        checklist_rows="\n".join(checklist_rows),
        task_progress=(
            "\n".join(task_progress_lines).rstrip() + "\n"
        ),
        group_details=(
            "\n".join(group_detail_lines).rstrip() + "\n"
            if group_detail_lines
            else "- 根工作项为 TASK，无 GROUP 协调节点。\n"
        ),
        delivery_review=(
            "\n".join(delivery_review_lines).rstrip() + "\n"
        ),
        confirmation="\n".join(confirmation_lines),
    )


def _render_json_projection(
    filename: str,
    value: object,
) -> str:
    template = PROJECTION_TEMPLATES.get(filename)
    if template is not JSON_PROJECTION_TEMPLATE:
        raise ValueError(f"unsupported JSON projection: {filename}")
    return template.substitute(
        document=pretty_json(value).rstrip("\n")
    )


def render_projection_documents(
    stored_definition: dict[str, Any],
    run: dict[str, Any] | None,
) -> dict[str, str]:
    """Render the complete controller-owned projection document set.

    The stored hierarchy, graph and optional run are already loaded from
    SQLite by the repository. Callers cannot supply a template or filename.
    """

    hierarchy = stored_definition["hierarchy"]
    documents = {
        "hierarchy.json": _render_json_projection(
            "hierarchy.json",
            hierarchy,
        ),
        "graph.json": _render_json_projection(
            "graph.json",
            stored_definition["graph"],
        ),
        "overview.md": render_scheduling_plan(
            hierarchy,
            hierarchy_fingerprint=stored_definition[
                "hierarchyFingerprint"
            ],
            graph_fingerprint=stored_definition[
                "graphFingerprint"
            ],
            hierarchy_status=stored_definition["status"],
            updated_at=(
                run["updatedAt"]
                if run is not None
                else stored_definition["updatedAt"]
            ),
            run=run,
        ),
    }
    if run is not None:
        documents["state.json"] = _render_json_projection(
            "state.json",
            run,
        )
    return {
        filename: documents[filename]
        for filename in PROJECTION_TEMPLATES
        if filename in documents
    }


def render_task_baseline_documents(
    stored_definition: dict[str, Any],
) -> dict[str, str]:
    """Render the exact TASK baseline file set from SQLite-loaded state."""

    hierarchy = stored_definition["hierarchy"]
    updated_at = stored_definition["updatedAt"]
    return {
        f"{definition['id']}.md": render_work_item_baseline(
            definition,
            delivery=hierarchy["delivery"],
            skill_hints=hierarchy["root"]["skillHints"],
            hierarchy_fingerprint=stored_definition[
                "hierarchyFingerprint"
            ],
            graph_fingerprint=stored_definition[
                "graphFingerprint"
            ],
            hierarchy_status=stored_definition["status"],
            updated_at=updated_at,
        )
        for node in iter_hierarchy_nodes(hierarchy)
        for definition in [node["definition"]]
        if definition["kind"] == "TASK"
    }


__all__ = (
    "PROJECTION_TEMPLATES",
    "PROJECTION_TEMPLATE_VERSION",
    "TASK_BASELINE_DIRECTORY",
    "TASK_BASELINE_PROJECTION_TEMPLATE",
    "WORKSPACE_OVERVIEW_PROJECTION_TEMPLATE",
    "raw_definition",
    "render_projection_documents",
    "render_scheduling_plan",
    "render_task_baseline_documents",
    "render_work_item_baseline",
    "render_workspace_overview",
    "task_baseline_relative_path",
)
