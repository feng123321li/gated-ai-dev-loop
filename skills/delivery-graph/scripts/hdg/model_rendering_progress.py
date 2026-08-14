from __future__ import annotations

from .model_rendering_common import (
    Any,
    KIND_TEXT,
    PROGRESS_PROJECTION_TEMPLATE,
    PROJECTION_TEMPLATE_VERSION,
    _markdown_text,
    _render_loop_baseline,
    _table_row,
    _utc_plus_8,
    group_review_node_id,
    join_node_id,
    json,
    loop_node_id,
    review_node_id,
    task_review_node_id,
    work_item_projection_relative_path,
)
from .model_rendering_state import (
    _delivery_projection_status,
    _progress_state_row,
    _projection_states,
    _render_git_binding_baseline,
    _render_project_scopes,
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
                        "GROUP seam Review Loop",
                        node["reviewLoop"],
                        heading_level=4,
                        absent_message=(
                            "未配置独立 GROUP seam Review；"
                            "GROUP 完成点是本节点终态。"
                        ),
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
            "## Delivery Acceptance/Readiness 输入",
            "",
            _render_loop_baseline(
                "Delivery Acceptance/Readiness Loop",
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
            "7. TASK 成功后继续消费 frontier；TASK Review、已配置的 GROUP seam "
            "Review 和 Delivery Acceptance/Readiness 必须使用与自动执行相同的宿主"
            "原生自动派遣、独立上下文、问题分级闭环和验证协议，全部成功后等待"
            "真实用户确认。",
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
        if node["reviewLoop"] is not None:
            group_rows.append(
                _progress_state_row(
                    states,
                    group_review_node_id(definition["id"]),
                    prefix=[path, "GROUP seam Review"],
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
                    "LIGHT：不创建 Delivery Acceptance/Readiness",
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
                    "Delivery Acceptance/Readiness",
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
