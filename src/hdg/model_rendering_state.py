from __future__ import annotations

from .model_rendering_common import (
    Any,
    BASELINE_PROJECTION_TEMPLATE,
    KIND_TEXT,
    PROJECTION_TEMPLATE_VERSION,
    _failure_class_text,
    _markdown_text,
    _render_loop_baseline,
    _status_text,
    _table_row,
    _task_database_declarations,
    _task_interface_declarations,
    _utc_plus_8,
    work_item_projection_relative_path,
)


def _projection_states(
    run: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    return {
        item["nodeId"]: item
        for item in (run or {}).get("nodes", [])
    }

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
        values["owner"],
        values["attempt"],
        values["updatedAt"],
        values["summary"],
        *suffix_values,
    ]
    suffix_start = len(prefix_values) + 6
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
        "Delivery Acceptance/Readiness Loop",
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
