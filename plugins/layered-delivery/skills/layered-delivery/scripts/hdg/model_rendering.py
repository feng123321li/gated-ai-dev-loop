from __future__ import annotations

import json
import re
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
    "GROUP": "递归分组",
    "TASK": "任务 Loop",
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
UTC_PLUS_8 = timezone(timedelta(hours=8))
PROJECTION_TEMPLATE_VERSION = 2
TASK_BASELINE_DIRECTORY = "task-baselines"
JSON_PROJECTION_TEMPLATE = Template("${document}\n")
OVERVIEW_PROJECTION_TEMPLATE = Template(
    """# 交付调度与进度总览

## 交付状态

- 投影模板版本：${template_version}
${delivery_status}

实现规范、测试、门禁与 Skill 激活由各 Loop 内部负责。
TASK 调度基线和原始输入已拆分到固定的 `task-baselines/` 投影。
Skill 提示在 Loop 启动后按真实上下文选择，不预先绑定节点。

## Skill 提示

${skill_hints}

## GROUP/TASK 清单

| 路径 | 类型 | 父级 | 同级依赖（dependsOn） | 当前状态 | 标题 | TASK baseline |
|---|---|---|---|---|---|---|
${checklist_rows}

## TASK Loop 运行快照

${task_progress}
## GROUP 协调与 Review

${group_details}
## 交付最终审查 Loop

${delivery_review}
## 最终用户确认

${confirmation}
"""
)
TASK_BASELINE_PROJECTION_TEMPLATE = Template(
    """# TASK 调度基线

## 基线标识

- 投影模板版本：${template_version}
- 交付 ID（delivery.id）：${delivery_id}
- 交付标题：${delivery_title}
- TASK ID：${task_id}
- 层级状态（hierarchyStatus）：${hierarchy_status}
- 层级指纹（hierarchyFingerprint）：${hierarchy_fingerprint}
- 图指纹（graphFingerprint）：${graph_fingerprint}
- 更新时间（UTC+8）：${updated_at}

## TASK 定义

- 标题：${task_title}
- 摘要：${task_summary}
- 父级：${parent_id}
- 同级依赖（dependsOn）：${dependencies}

## Task Loop

- Loop 引用：${loop_ref}
- 资源锁声明（resourceClaims）：${resource_claims}
- 原始输入（payload）：
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
    return f"{STATUS_TEXT.get(value, value)}（{value}）"


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


def raw_definition(
    definition: dict[str, Any],
) -> dict[str, Any]:
    return dict(definition)


def _json_block_lines(value: object) -> list[str]:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    longest = max(
        (
            len(match.group(0))
            for match in re.finditer(r"`+", rendered)
        ),
        default=0,
    )
    fence = "`" * max(3, longest + 1)
    return [f"{fence}json", rendered, fence]


def task_baseline_relative_path(task_id: str) -> str:
    return f"{TASK_BASELINE_DIRECTORY}/{task_id}.md"


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

    Loop payloads remain opaque and are shown as JSON for auditability.
    """

    if definition["kind"] != "TASK":
        raise ValueError("TASK baseline requires a TASK definition")
    loop = definition["execution"]["loop"]
    hints = skill_hints or []
    rendered_hints = (
        "\n".join(
            f"- {hint['name']}：{hint['purpose']}"
            for hint in hints
        )
        if hints
        else "- 无"
    )
    delivery = delivery or {}
    return TASK_BASELINE_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        delivery_id=delivery.get("id", "UNAVAILABLE"),
        delivery_title=delivery.get("title", "UNAVAILABLE"),
        task_id=definition["id"],
        hierarchy_status=_status_text(
            hierarchy_status or "UNKNOWN"
        ),
        hierarchy_fingerprint=(
            hierarchy_fingerprint or "UNAVAILABLE"
        ),
        graph_fingerprint=graph_fingerprint or "UNAVAILABLE",
        updated_at=_utc_plus_8(updated_at),
        task_title=definition["title"],
        task_summary=definition["summary"],
        parent_id=definition["parentId"] or "无",
        dependencies=(
            ", ".join(definition["execution"]["dependsOn"]) or "无"
        ),
        loop_ref=loop["ref"],
        resource_claims=(
            ", ".join(loop["resourceClaims"]) or "无"
        ),
        payload="\n".join(_json_block_lines(loop["payload"])),
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
                f"- 当前进度：{node_id} = {_status_text('NOT_STARTED')}"
            ]
        lines = [
            (
                f"- 当前进度：{node_id} = "
                f"{_status_text(state['status'])}"
            ),
            f"- 尝试次数（attempt）：{state['attempt']}",
        ]
        if state["owner"]:
            lines.append(f"- 执行者（owner）：{state['owner']}")
        if state["failureClass"]:
            lines.append(
                f"- 失败分类（failureClass）：{state['failureClass']}"
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
                    "- 结果状态（outcome）："
                    f"{_status_text(outcome['status'])}"
                )
            if outcome.get("summary"):
                lines.append(f"- 结果摘要：{outcome['summary']}")
            if outcome.get("confirmedBy"):
                lines.append(
                    f"- 确认人：{outcome['confirmedBy']}"
                )
        return lines

    def json_block(value: object) -> list[str]:
        return _json_block_lines(value)

    def loop_lines(
        label: str,
        loop: dict[str, Any],
        node_id: str,
    ) -> list[str]:
        lines = [
            f"#### {label}",
            "",
            f"- 节点：{node_id}",
            f"- Loop 引用：{loop['ref']}",
            (
                "- 资源锁声明（resourceClaims）："
                f"{', '.join(loop['resourceClaims']) or '无'}"
            ),
        ]
        lines.extend(state_lines(node_id))
        lines.extend(
            ["- 原始输入（payload）：", *json_block(loop["payload"]), ""]
        )
        return lines

    baseline_lines = [
        f"- 交付 ID（delivery.id）：{delivery['id']}",
        f"- 标题：{delivery['title']}",
        f"- 摘要：{delivery['summary']}",
        f"- Schema 版本：{hierarchy['root']['schemaVersion']}",
        (
            "- 层级状态（hierarchyStatus）："
            f"{_status_text(hierarchy_status or 'UNKNOWN')}"
        ),
        (
            "- 运行状态（runStatus）："
            f"{_status_text((run or {}).get('status', 'NOT_STARTED'))}"
        ),
        (
            "- 层级指纹（hierarchyFingerprint）："
            f"{hierarchy_fingerprint or 'UNAVAILABLE'}"
        ),
        (
            "- 图指纹（graphFingerprint）："
            f"{graph_fingerprint or 'UNAVAILABLE'}"
        ),
    ]
    if run is not None:
        baseline_lines.extend(
            [
                f"- 运行 ID（runId）：{run['runId']}",
                f"- 启动时间（UTC+8）：{_utc_plus_8(run['startedAt'])}",
            ]
        )
    baseline_lines.append(
        f"- 更新时间（UTC+8）：{_utc_plus_8(updated_at)}"
    )
    hints = hierarchy["root"]["skillHints"]
    if hints:
        skill_hint_lines = [
            f"- {hint['name']}：{hint['purpose']}"
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
                f"JOIN={state_text(join_node_id(definition['id']))}; "
                "REVIEW="
                f"{state_text(group_review_node_id(definition['id']))}"
            )
        cells = [
            path,
            f"{KIND_TEXT[definition['kind']]}（{definition['kind']}）",
            definition["parentId"] or "无",
            ", ".join(dependencies) or "无",
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
            "| " + " | ".join(
                str(cell).replace("|", r"\|").replace("\n", " ")
                for cell in cells
            ) + " |"
        )
    task_progress_lines: list[str] = []
    group_detail_lines: list[str] = []
    for node in iter_hierarchy_nodes(hierarchy):
        definition = node["definition"]
        if definition["kind"] == "TASK":
            task_node_id = loop_node_id(definition["id"])
            task_progress_lines.extend(
                [
                    f"### {task_node_id}",
                    "",
                    *state_lines(task_node_id),
                    "",
                ]
            )
            continue
        dependencies = definition["decomposition"]["dependsOn"]
        group_detail_lines.extend(
            [
                (
                    f"### {definition['id']} "
                    f"[{KIND_TEXT[definition['kind']]}]"
                ),
                "",
                f"- 标题：{definition['title']}",
                f"- 摘要：{definition['summary']}",
                f"- 父级：{definition['parentId'] or '无'}",
                (
                    "- 同级依赖（dependsOn）："
                    f"{', '.join(dependencies) or '无'}"
                ),
            ]
        )
        group_detail_lines.extend(
            [
                (
                    "- 直接子级："
                    + ", ".join(
                        f"{child['id']} [{child['kind']}]"
                        for child in definition["children"]
                    )
                ),
                (
                    "- 分组汇合节点（GROUP_JOIN）："
                    f"{join_node_id(definition['id'])}"
                ),
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
    confirmation_lines = [
        f"- 节点：{confirmation_id}",
        *state_lines(confirmation_id),
    ]
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
    "raw_definition",
    "render_projection_documents",
    "render_scheduling_plan",
    "render_task_baseline_documents",
    "render_work_item_baseline",
    "task_baseline_relative_path",
)
