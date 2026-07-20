from __future__ import annotations

import json
import posixpath
from datetime import datetime
from typing import Any

from .constants import SCHEMA_VERSION

GOVERNANCE_DIRECTORY = ".hierarchical-delivery-governance"
WORK_ITEMS_DIRECTORY = "work-items"


def _node_progress_filename(entry: dict[str, Any]) -> str:
    """Keep the requirement-wide progress separate from the root node's progress."""
    return "node-progress.md" if entry["parentId"] is None else "progress.md"


def _local_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()


def _local_date(value: str) -> str:
    return _local_datetime(value).strftime("%Y-%m-%d")


def _local_minute(value: str) -> str:
    return _local_datetime(value).strftime("%Y-%m-%d %H:%M")


def human_status(value: object) -> str:
    return {
        "DELIVERY": "交付",
        "CAPABILITY": "能力",
        "TASK": "任务",
        "WAITING_FOR_BASELINE_CONFIRMATION": "等待开发方案确认",
        "BASELINE_FROZEN": "开发方案已冻结",
        "PREPARED": "等待开发方案评审",
        "FROZEN": "已冻结",
        "CLAIMED": "开发中",
        "IMPLEMENTED": "等待门禁验收",
        "BLOCKED": "已阻断",
        "VERIFIED": "门禁已通过",
        "NOT_READY": "尚未就绪",
        "WAITING_FOR_INDEPENDENT_REVIEW": "等待独立验收",
        "WAITING_FOR_USER_CONFIRMATION": "等待用户确认",
        "COMPLETED": "已完成",
        "NOT_RUN": "未运行",
        "PASS": "通过",
        "FAIL": "未通过",
    }.get(value, str(value) if value is not None else "无")


def _requirement_root(
    entry: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    current = entry
    while current["parentId"] is not None:
        current = by_id[current["parentId"]]
    return current


def _development_mode(
    entry: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
) -> str:
    mode = _requirement_root(entry, by_id).get("developmentMode")
    return mode["mode"] if mode else "未选择"


def item_human_artifacts(
    item: str | dict[str, Any],
    acceptance_report: dict[str, Any] | None = None,
    *,
    root_package_path: str | None = None,
) -> dict[str, str | None]:
    package_path = item["packagePath"] if isinstance(item, dict) else posixpath.join(WORK_ITEMS_DIRECTORY, item)
    base = posixpath.join(GOVERNANCE_DIRECTORY, package_path)
    plan_base = posixpath.join(GOVERNANCE_DIRECTORY, root_package_path or package_path)
    requirement_handoff = (
        posixpath.join(base, "requirement-handoff.md")
        if isinstance(item, dict)
        and item["parentId"] is None
        and item["stage"] == "BASELINE_FROZEN"
        and (item.get("developmentMode") or {}).get("mode") == "manual"
        else None
    )
    node_progress = posixpath.join(
        base,
        _node_progress_filename(item) if isinstance(item, dict) else "progress.md",
    )
    return {
        "overview": posixpath.join(base, "overview.md"),
        "developmentPlan": posixpath.join(base, "development-plan.md"),
        "hierarchyDevelopmentPlan": posixpath.join(plan_base, "development-plan.md"),
        "baseline": posixpath.join(base, "baseline.md"),
        "progress": posixpath.join(base, "progress.md"),
        "nodeProgress": node_progress,
        "interactionLog": posixpath.join(base, "interaction-log.md")
        if isinstance(item, dict) and item["parentId"] is None
        else None,
        "requirementHandoff": requirement_handoff,
        "developmentReview": posixpath.join(base, "development-review.md")
        if isinstance(item, dict) and item.get("latestResult")
        else None,
        "acceptanceReport": acceptance_report.get("markdownPath") if acceptance_report else None,
    }


def next_action(
    entry: dict[str, Any],
    by_id: dict[str, dict[str, Any]] | None = None,
) -> str:
    if entry["stage"] == "WAITING_FOR_BASELINE_CONFIRMATION":
        return "人工评审根级 development-plan.md、选择开发方式并一次确认冻结；无需复述指纹。"
    if (
        entry["status"] == "FROZEN"
        and by_id is not None
        and _development_mode(entry, by_id) == "manual"
    ):
        root = _requirement_root(entry, by_id)
        if entry["id"] == root["id"]:
            return "使用 requirement-handoff.md 一次性交接整棵需求树；接收会话按依赖推进，无需人工逐 Task 启动。"
        return "由根级需求交接会话按依赖调度；无需人工逐 Task 启动。"
    if entry["status"] == "FROZEN" and entry["kind"] == "TASK":
        return "Agent 按依赖自主调度，循环实现、回归测试和复测。"
    if entry["status"] == "FROZEN":
        return "等待当前树中的子级完成后运行聚合门禁。"
    if entry["status"] == "CLAIMED":
        return "等待开发结果按 operationId 写回。"
    if entry["status"] == "IMPLEMENTED":
        return "形成严格 evidence 并执行 accept-item 门禁验收。"
    if entry["status"] == "BLOCKED":
        return "Agent 处理阻断后按当前指纹自动执行 retry-item，并继续开发与复测。"
    if entry["status"] == "VERIFIED" and entry["parentId"] is None:
        acceptance = entry.get("acceptance")
        if acceptance and acceptance["status"] == "WAITING_FOR_INDEPENDENT_REVIEW":
            return "执行独立验收或记录人工验收接受。"
        if acceptance and acceptance["status"] == "WAITING_FOR_USER_CONFIRMATION":
            return "等待用户最终确认。"
    return "等待父级聚合门禁。" if entry["status"] == "VERIFIED" else "查看当前状态与门禁证据。"


def render_workspace_overview(
    registry: dict[str, Any],
    *,
    isolated_item_ids: set[str] | None = None,
) -> str:
    lines = [
        "# 需求层级总览",
        "",
        "> 本文件是面向用户和协作者的可读投影；机器权威为 `governance.sqlite3`。",
        f"> 注册表版本：{registry['revision']}",
        f"> 当前焦点：{registry['currentFocus']['workItemId'] or '无'}",
    ]
    isolated = sorted(isolated_item_ids or set())
    if isolated:
        lines.extend([
            "",
            "> 只读隔离：以下历史工作项不符合当前数据契约，控制器不会迁移、改写或删除它们；",
            "> 其他有效工作项和新需求可以继续，直接操作隔离项仍会被拒绝。",
            f"> 隔离工作项：{', '.join(f'`{item_id}`' for item_id in isolated)}",
        ])

    roots = _workspace_roots(registry)
    lines.extend([
        "",
        "## 需求索引",
        "",
        "> 按最近更新时间倒序排列；目录继续使用稳定根 ID，日期只用于检索和浏览。",
        "",
        "| 最近更新（本机时区） | 创建时间（本机时区） | 需求根 | 类型 | 状态 | 门禁 | 后代进度 | 入口 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    if not roots:
        lines.append("| - | - | 暂无需求 | - | - | - | - | - |")
    for root in roots:
        descendants = root["progress"]["descendants"]
        descendant_progress = (
            "不适用"
            if descendants["total"] == 0
            else f"{descendants['verified']}/{descendants['total']} 已验证"
        )
        overview = posixpath.join(root["packagePath"], "overview.md")
        plan = posixpath.join(root["packagePath"], "development-plan.md")
        progress = posixpath.join(root["packagePath"], "progress.md")
        month = _workspace_month(root)
        monthly_detail = f"workspace-overview/{month}/{root['id']}.md"
        lines.append(
            f"| {_local_minute(root['updatedAt'])} | {_local_minute(root['createdAt'])} | "
            f"[`{root['id']}`]({overview}) | "
            f"{human_status(root['kind'])} | {human_status(root['status'])} | "
            f"{human_status(root['gate']['status'])} | "
            f"{descendant_progress} | "
            f"[方案]({plan})、[整树进度]({progress})、[月度明细]({monthly_detail}) |"
        )
    lines.append("")
    return "\n".join(lines)


def _workspace_roots(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        (item for item in registry["workItems"] if item["parentId"] is None),
        key=lambda item: (item["updatedAt"], item["id"]),
        reverse=True,
    )


def _workspace_month(root: dict[str, Any]) -> str:
    return _local_datetime(root["createdAt"]).strftime("%Y-%m")


def _requirement_completion_date(root: dict[str, Any]) -> str:
    acceptance = root.get("acceptance") or {}
    confirmation = acceptance.get("userConfirmation") or {}
    recorded_at = confirmation.get("recordedAt")
    if acceptance.get("status") != "COMPLETED" or not isinstance(recorded_at, str):
        return "未完成"
    return _local_date(recorded_at)


def render_workspace_month_overviews(registry: dict[str, Any]) -> dict[str, str]:
    by_id = {item["id"]: item for item in registry["workItems"]}
    roots = _workspace_roots(registry)
    months = sorted({_workspace_month(root) for root in roots}, reverse=True)
    rendered: dict[str, str] = {}

    for month in months:
        month_roots = [root for root in roots if _workspace_month(root) == month]
        month_lines = [
            f"# {month} 需求索引",
            "",
            "> 本文件按需求创建月份归档；每个需求使用独立明细文件，避免依赖跨文件标题锚点。",
            "> 机器权威为 `../governance.sqlite3`，物理目录继续使用稳定根 ID。",
            "> 日期按运行控制器的电脑本地时区显示和归档。",
            "",
            "[返回全局需求索引](../workspace-overview.md)",
            "",
            "| 创建时间（本机时区） | 完成日期（本机时区） | 最近更新（本机时区） | 需求根 | 状态 | 门禁 | 入口 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]

        for root in month_roots:
            detail_path = f"{month}/{root['id']}.md"
            month_lines.append(
                f"| {_local_minute(root['createdAt'])} | {_requirement_completion_date(root)} | "
                f"{_local_minute(root['updatedAt'])} | `{root['id']}` | "
                f"{human_status(root['status'])} | {human_status(root['gate']['status'])} | "
                f"[查看需求明细]({detail_path}) |"
            )
            rendered[detail_path] = _render_workspace_requirement_detail(
                root,
                by_id,
                month,
            )
        month_lines.append("")
        rendered[f"{month}.md"] = "\n".join(month_lines)
    return rendered


def _render_workspace_requirement_detail(
    root: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    month: str,
) -> str:
    acceptance = root.get("acceptance")
    plan = posixpath.join("..", "..", root["packagePath"], "development-plan.md")
    lines = [
        f"# 需求：{root['id']}",
        "",
        "> 本文件是单个需求的可读投影；状态与门禁会随 SQLite 写回自动刷新。",
        "> 机器权威为 `../../governance.sqlite3`，日期按运行控制器的电脑本地时区显示。",
        "",
        f"[返回 {month} 月度索引](../{month}.md) · [返回全局需求索引](../../workspace-overview.md)",
        "",
        f"- 需求开始时间（本机时区）：{_local_minute(root['createdAt'])}",
        f"- 需求完成日期（本机时区）：{_requirement_completion_date(root)}",
        f"- 最近更新（本机时区）：{_local_minute(root['updatedAt'])}",
        f"- 开发方案：[查看整树 development-plan.md]({plan})",
        f"- 开发建议：{_development_mode(root, by_id)}（需求评审时选择）",
        f"- 最终验收：{human_status(acceptance['status']) if acceptance else '不适用'}",
        f"- 进度：{root['progress']['descendants']['verified']}/{root['progress']['descendants']['total']} 个后代已验证",
        "",
        "| 层级工作项 | 状态 | 门禁 | 开发方式 | 节点文件 |",
        "| --- | --- | --- | --- | --- |",
    ]

    def append_node(item: dict[str, Any], depth: int, connector: str) -> None:
        overview = posixpath.join("..", "..", item["packagePath"], "overview.md")
        progress = posixpath.join(
            "..",
            "..",
            item["packagePath"],
            _node_progress_filename(item),
        )
        mode = _development_mode(item, by_id)
        indentation = "　" * max(depth - 1, 0)
        hierarchy_item = (
            f"{indentation}{connector}{human_status(item['kind'])} `{item['id']}`"
        )
        lines.append(
            f"| {hierarchy_item} | {human_status(item['status'])} | "
            f"{human_status(item['gate']['status'])} | {mode} | "
            f"[概览]({overview})、[节点进度]({progress}) |"
        )
        children = [by_id[child_id] for child_id in item["childIds"]]
        for index, child in enumerate(children):
            last = index == len(children) - 1
            append_node(child, depth + 1, "└─ " if last else "├─ ")

    append_node(root, 0, "")
    lines.append("")
    return "\n".join(lines)


def render_item_overview(entry: dict[str, Any], by_id: dict[str, dict[str, Any]] | None = None) -> str:
    by_id = by_id or {entry["id"]: entry}
    parent = by_id.get(entry["parentId"]) if entry["parentId"] else None
    parent_link = (
        f"[{parent['id']}]({posixpath.relpath(posixpath.join(parent['packagePath'], 'overview.md'), entry['packagePath'])})"
        if parent
        else "无"
    )
    child_links = [
        f"[{child_id}]({posixpath.relpath(posixpath.join(by_id[child_id]['packagePath'], 'overview.md'), entry['packagePath'])})"
        for child_id in entry["childIds"]
    ]
    progress_lines = (
        [
            "- 节点进度：[node-progress.md](node-progress.md)",
            "- 整树进度：[progress.md](progress.md)",
        ]
        if entry["parentId"] is None
        else ["- 节点进度：[progress.md](progress.md)"]
    )
    return "\n".join([
        f"# {entry['id']} 工作项概览",
        "",
        f"- 类型：{entry['kind']}",
        f"- 门禁等级：{entry['gateLevel']}",
        f"- 权限性质：{entry['authorityKind']}",
        f"- 父级：{parent_link}",
        "- 基线：[baseline.md](baseline.md)",
        "- 开发方案：[development-plan.md](development-plan.md)",
        *progress_lines,
        f"- 父契约指纹：{entry['parentContractFingerprint'] or '无'}",
        f"- 子级：{', '.join(child_links) or '无'}",
        f"- 开发复核：{'[development-review.md](development-review.md)' if entry.get('latestResult') else '开发结果写回后生成'}",
        f"- 验收报告：{'[acceptance-report.md](acceptance-report.md)' if entry.get('acceptanceReport') else '尚未生成'}",
        f"- 下一步：{next_action(entry, by_id)}",
        "",
    ])


def render_requirement_handoff(
    root: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
) -> str:
    """Render one copyable handoff for a complete frozen manual requirement tree."""
    lines = [
        f"# 需求级开发交接：{root['id']}",
        "",
        "> 这是需求级一次性交接，不是某个 Task 的单独开工提示。",
        "> 接收会话负责一次接管整棵需求树，并在内部按依赖逐 Task 认领、开发、回归、复测和验收。",
        "",
        "## 权威入口",
        "",
        f"- 根工作项：`{root['id']}`",
        "- 开发方式：manual",
        "- 完整冻结方案：[development-plan.md](development-plan.md)",
        "- 实时进度：[progress.md](progress.md)",
        "",
        "## 冻结需求树",
        "",
    ]

    def append_node(item: dict[str, Any], depth: int) -> None:
        lines.append(
            f"{'  ' * depth}- {human_status(item['kind'])} `{item['id']}` — {human_status(item['status'])}"
        )
        for child_id in item["childIds"]:
            append_node(by_id[child_id], depth + 1)

    append_node(root, 0)
    lines.extend([
        "",
        "## 接收会话执行规则",
        "",
        "1. 在项目根目录使用当前 `hierarchical-delivery-governance` Skill，先读取 SQLite 治理状态、完整冻结方案和实时进度；不要重新准备或重新冻结需求。",
        f"2. 以根工作项 `{root['id']}` 调用 `ready-tasks`，按依赖动态计算 READY Task；一次交接不等于一次认领全部 Task。",
        "3. 对本轮 READY Task 分别生成唯一 operationId，并在实际开工前调用 `dispatch-task`。可安全并行时使用隔离子 Agent；不可并行时自动串行。",
        "4. 每个 Task 严格使用自己的 context、scope、结果和证据，循环实现、回归测试、修复和复测；写回 `IMPLEMENTED` 或 `BLOCKED` 后完成该 Task 门禁。",
        "5. 每次状态写回后重新计算 READY Task，自动推进后续波次；全部子级 VERIFIED 后运行 Capability/Delivery 聚合门禁。",
        "6. 不要要求用户逐 Task 回复启动，也不要在正常 Task 切换、并发降级或自动重试时请求人工确认。",
        "7. 只有冻结目标、范围、接口、授权必须改变或出现无法自动消除的真实阻断时才返回用户；根门禁通过后提交最终交付，由用户人工验收。",
        "8. 不修改 SQLite、baseline、治理投影或 `.git/**`；未获得单独授权时不提交、推送、合并、发布或改变外部状态。",
        "",
    ])
    return "\n".join(lines)


def render_item_progress(
    entry: dict[str, Any],
    by_id: dict[str, dict[str, Any]] | None = None,
    *,
    include_hierarchy: bool = False,
) -> str:
    by_id = by_id or {entry["id"]: entry}
    acceptance = entry.get("acceptance") if entry["parentId"] is None else None
    mode = _development_mode(entry, by_id)
    current_execution = _current_execution(entry)
    requirement_handoff = (
        entry["parentId"] is None
        and entry["stage"] == "BASELINE_FROZEN"
        and mode == "manual"
    )
    lines = [
        f"# {entry['id']} {'整树进度' if include_hierarchy else '节点进度'}",
        "",
        f"- 记录版本：{entry['recordRevision']}",
        f"- 阶段：{entry['stage']}",
        f"- 当前状态：{human_status(entry['status'])}",
        f"- 门禁等级：{entry['gateLevel']}",
        f"- 最终验收：{human_status(acceptance['status']) if acceptance else '不适用'}",
        f"- 门禁：{human_status(entry['gate']['status'])}",
        f"- 开发建议：{mode}",
        "- 开发方案：[development-plan.md](development-plan.md)",
        *(["- 交互记录：[interaction-log.md](interaction-log.md)"] if entry["parentId"] is None else []),
        *(["- 需求级交接：[requirement-handoff.md](requirement-handoff.md)"] if requirement_handoff else []),
        f"- 当前执行：{current_execution}",
        f"- 直接子级：{entry['progress']['directChildren']['verified']}/{entry['progress']['directChildren']['total']} 已验证；"
        f"{entry['progress']['directChildren']['blocked']} 阻断；{entry['progress']['directChildren']['active']} 活动",
        f"- 全部后代：{entry['progress']['descendants']['verified']}/{entry['progress']['descendants']['total']} 已验证；"
        f"{entry['progress']['descendants']['blocked']} 阻断；{entry['progress']['descendants']['active']} 活动",
        f"- 验收报告：{'[acceptance-report.md](acceptance-report.md)' if entry.get('acceptanceReport') else '尚未生成'}",
        f"- 下一步：{next_action(entry, by_id)}",
        f"- 更新时间：{entry['updatedAt']}",
        "",
    ]
    if include_hierarchy:
        lines.extend(_render_hierarchy_progress(entry, by_id))
    return "\n".join(lines)


def render_interaction_log(root: dict[str, Any], events: list[dict[str, Any]]) -> str:
    """Render the append-only interaction audit trail for one requirement tree."""
    lines = [
        f"# {root['id']} 交互记录",
        "",
        "> SQLite 保存结构化交互事件，本文件仅供人工查看。",
        "> 只记录指令、决策和状态摘要，不记录隐藏思考过程或敏感原文。",
        "",
        "| 序号 | 时间 | 工作项 | 参与者 | 事件 | 摘要 | 操作 |",
        "| ---: | --- | --- | --- | --- | --- | --- |",
    ]
    if not events:
        lines.append("| - | - | - | - | - | 暂无记录 | - |")
    for event in events:
        summary = str(event["summary"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {event['eventId']} | {event['recordedAt']} | `{event['workItemId']}` | "
            f"{event['actor']} | `{event['eventType']}` | {summary} | "
            f"{event['operationId'] or '无'} |"
        )
    lines.append("")
    return "\n".join(lines)


def _current_execution(entry: dict[str, Any]) -> str:
    if entry["kind"] != "TASK":
        return "不适用"
    if entry.get("claim"):
        return f"{entry['claim']['owner']} / {entry['claim']['operationId']}"
    if entry["status"] in {"IMPLEMENTED", "BLOCKED", "VERIFIED"}:
        return "已释放"
    return "未认领"


def _render_hierarchy_progress(
    root: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
) -> list[str]:
    lines = [
        "## 整树进度明细",
        "",
        "> 本明细与 [development-plan.md](development-plan.md) 使用相同的工作项 ID、父子顺序和层级结构。",
        "> 表格第一列保留层级；点击工作项可跳转到对应开发方案。每次控制器状态写回都会重建本文件。",
        "",
        "| 层级工作项 | 阶段 | 状态 | 门禁 | 当前执行 | 节点文件 | 阶段产物 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    def item_paths(item: dict[str, Any]) -> tuple[str, str, str, str]:
        package_path = item["packagePath"]
        progress_path = posixpath.relpath(
            posixpath.join(package_path, _node_progress_filename(item)),
            root["packagePath"],
        )
        plan_path = posixpath.relpath(
            posixpath.join(package_path, "development-plan.md"),
            root["packagePath"],
        )
        review_path = posixpath.relpath(
            posixpath.join(package_path, "development-review.md"),
            root["packagePath"],
        )
        report_path = posixpath.relpath(
            posixpath.join(package_path, "acceptance-report.md"),
            root["packagePath"],
        )
        return plan_path, progress_path, review_path, report_path

    def artifact_links(item: dict[str, Any]) -> str:
        _, _, review_path, report_path = item_paths(item)
        links = []
        if (
            item["parentId"] is None
            and item["stage"] == "BASELINE_FROZEN"
            and _development_mode(item, by_id) == "manual"
        ):
            links.append("[需求交接](requirement-handoff.md)")
        if item.get("latestResult"):
            links.append(f"[开发复核]({review_path})")
        if item.get("acceptanceReport"):
            links.append(f"[验收报告]({report_path})")
        return "、".join(links) or "无"

    def append_node(item: dict[str, Any], depth: int, connector: str) -> None:
        current_execution = _current_execution(item)
        plan_anchor = f"development-plan.md#work-item-{item['id']}"
        plan_path, progress_path, _, _ = item_paths(item)
        indentation = "　" * max(depth - 1, 0)
        hierarchy_item = (
            f"{indentation}{connector}[{human_status(item['kind'])} `{item['id']}`]({plan_anchor})"
        )
        lines.append(
            f"| {hierarchy_item} | {human_status(item['stage'])} | {human_status(item['status'])} | "
            f"{human_status(item['gate']['status'])} | {current_execution} | "
            f"[方案]({plan_path})、[进度]({progress_path}) | {artifact_links(item)} |"
        )
        children = [by_id[child_id] for child_id in item["childIds"]]
        for index, child in enumerate(children):
            last = index == len(children) - 1
            append_node(child, depth + 1, "└─ " if last else "├─ ")

    append_node(root, 0, "")
    lines.append("")
    return lines


def report_status(entry: dict[str, Any]) -> str:
    acceptance = entry.get("acceptance") if entry["parentId"] is None else None
    if acceptance and acceptance["status"] != "NOT_READY":
        return acceptance["status"]
    if entry["status"] == "IMPLEMENTED":
        return "WAITING_FOR_GATE"
    if entry["status"] in {"BLOCKED", "VERIFIED"}:
        return entry["status"]
    return "NOT_READY"


def _validation_remediation_changes(report: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for record in report.get("validationRemediations", []):
        changes.extend(record["artifact"]["fileChanges"])
    return changes


def _append_validation_remediations(
    lines: list[str],
    report: dict[str, Any],
) -> None:
    remediations = report.get("validationRemediations", [])
    lines.extend(["", "## 验证修正", ""])
    if not remediations:
        lines.append("- 无。")
        return
    lines.extend([
        "> 以下内容是原冻结契约下的追加式验证修正；原 baseline、需求 ID 和层级结构保持不变。",
        "",
        "| 序号 | 发现阶段 | 对应验收项 | 修正原因 | 补充授权文件 | 记录时间 |",
        "| ---: | --- | --- | --- | --- | --- |",
    ])
    for index, record in enumerate(remediations, start=1):
        artifact = record["artifact"]
        summary = artifact["summary"].replace("|", "\\|").replace("\n", " ")
        files = "、".join(item["path"] for item in artifact["fileChanges"])
        lines.append(
            f"| {index} | `{artifact['source']}` | "
            f"{', '.join(f'`{item}`' for item in artifact['acceptanceIds'])} | "
            f"{summary} | {files} | {record['recordedAt']} |"
        )


def render_development_review(report: dict[str, Any]) -> str:
    plan = report["developmentPlan"]
    result = (report.get("result") or {}).get("artifact") or {}
    planned_files = [item["path"] for item in plan.get("fileChanges", [])]
    remediation_files = [item["path"] for item in _validation_remediation_changes(report)]
    authorized_files = planned_files + [item for item in remediation_files if item not in planned_files]
    actual_files = [item.replace("\\", "/") for item in result.get("changedFiles", [])]
    tests = result.get("tests", [])
    lines = [
        f"# 开发复核：{report['workItem']['title']}",
        "",
        f"- 工作项：{report['workItem']['id']}",
        f"- Baseline 指纹：{report['workItem']['baselineFingerprint']}",
        f"- 开发结果：{report['status']}",
        f"- 写回时间：{report['generatedAt']}",
        "",
        "## 冻结计划与实际改动",
        "",
        f"- 开发目的：{plan['purpose']}",
        f"- 冻结计划文件：{'、'.join(planned_files) or '无'}",
        f"- 验证修正补充文件：{'、'.join(remediation_files) or '无'}",
        f"- 实际文件：{'、'.join(actual_files) or '无'}",
        f"- 未授权文件：{'、'.join(item for item in actual_files if item not in authorized_files) or '无'}",
        f"- 尚未观察到的授权文件：{'、'.join(item for item in authorized_files if item not in actual_files) or '无'}",
        "",
        "## 接口与功能复核",
        "",
    ]
    interfaces = plan.get("interfaces", [])
    if interfaces:
        lines.extend(
            f"- {item['action']} {item['kind']} `{item['name']}`：{item['targetContract']}"
            for item in interfaces
        )
    else:
        lines.append("- 冻结计划未声明接口改动。")
    _append_validation_remediations(lines, report)
    lines.extend(["", "## 开发者结果", "", f"- 摘要：{result.get('summary', '未提供')}"])
    blockers = result.get("blockers", [])
    lines.append(f"- 阻断：{'；'.join(blockers) or '无'}")
    lines.extend(["", "## 测试事实", ""])
    if tests:
        for test in tests:
            argv = json.dumps(test["argv"], ensure_ascii=False, separators=(",", ":"))
            lines.append(
                f"- `{argv}`：退出码 {test['exitCode']}；执行 {test.get('testsRun', '未记录')} 项测试"
            )
    else:
        lines.append("- 未提供测试事实。")
    lines.extend([
        "",
        "## 复核结论",
        "",
        "- 本文件只对照冻结计划和开发者写回事实，不代表门禁通过。",
        "- 下一步必须形成独立门禁 evidence 并执行 accept-item。",
        "",
    ])
    return "\n".join(lines)


def render_acceptance_report(report: dict[str, Any]) -> str:
    status_text = {
        "NOT_READY": "尚未就绪",
        "WAITING_FOR_GATE": "等待门禁验收",
        "BLOCKED": "已阻断",
        "VERIFIED": "门禁已通过",
        "WAITING_FOR_INDEPENDENT_REVIEW": "等待独立验收",
        "WAITING_FOR_USER_CONFIRMATION": "等待用户确认",
        "COMPLETED": "已完成",
    }
    gate_text = {"NOT_RUN": "未运行", "PASS": "通过", "FAIL": "未通过"}
    gate_artifact = report["gate"].get("artifact")
    lines = [
        f"# 验收报告：{report['workItem']['title']}",
        "",
        f"- 工作项：{report['workItem']['id']}",
        f"- 类型：{report['workItem']['kind']}",
        f"- 门禁等级：{report['workItem']['gateLevel']}",
        f"- 基线指纹：{report['workItem']['baselineFingerprint']}",
        f"- 最终状态：{status_text.get(report['status'], report['status'])}",
        f"- 门禁结论：{gate_text.get(report['gate']['status'], report['gate']['status'])}",
        f"- 生成时间：{report['generatedAt']}",
        "",
        "## 验收项",
        "",
        "| 编号 | 预期结果 | 结论 | 证据 |",
        "| --- | --- | --- | --- |",
    ]
    results = {item["id"]: item for item in (gate_artifact or {}).get("acceptance", [])}
    for item in report["criteria"]:
        result = results.get(item["id"])
        conclusion = gate_text.get(result["status"], result["status"]) if result else "待验收"
        lines.append(f"| {item['id']} | {item['expectedResult']} | {conclusion} | {result['evidence'] if result else '无'} |")
    plan = report["developmentPlan"]
    lines.extend(["", "## 冻结开发方案", ""])
    if "interfaces" in plan:
        contracts = "；".join(f"{item['action']} {item['kind']} {item['name']}" for item in plan["interfaces"])
        lines.extend([f"- 开发目的：{plan['purpose']}", f"- 接口契约：{contracts or '无接口改动'}"])
    else:
        children = "；".join(f"{item['id']}：{item['purpose']}" for item in plan["childPlans"])
        lines.extend([f"- 协调目的：{plan['purpose']}", f"- 子级内容：{children}"])
    _append_validation_remediations(lines, report)
    tests = (gate_artifact or {}).get("tests") or ((report.get("development") or {}).get("artifact") or {}).get("tests", [])
    lines.extend(["", "## 测试结果", ""])
    if not tests:
        lines.append("- 尚无测试证据。")
    for result in tests:
        argv = json.dumps(result["argv"], ensure_ascii=False, separators=(",", ":"))
        summary = result.get("summary", f"Tests run: {result.get('testsRun', '未记录')}")
        lines.append(f"- `{argv}`：退出码 {result['exitCode']}；{summary}")
    scope = (gate_artifact or {}).get("scope", {})
    lines.extend(["", "## 变更范围", ""])
    if "fileChanges" in plan:
        planned = [item["path"] for item in plan["fileChanges"]]
        remediation = [item["path"] for item in _validation_remediation_changes(report)]
        authorized = planned + [item for item in remediation if item not in planned]
        actual = scope.get("changedFiles") or (((report.get("development") or {}).get("artifact") or {}).get("changedFiles", []))
        actual_portable = [item.replace("\\", "/") for item in actual]
        lines.extend([
            f"- 冻结计划文件：{'、'.join(planned) or '无'}",
            f"- 验证修正补充文件：{'、'.join(remediation) or '无'}",
            f"- 未授权文件：{'、'.join(item for item, portable in zip(actual, actual_portable) if portable not in authorized) or '无'}",
            f"- 尚未观察到的授权文件：{'、'.join(item for item in authorized if item not in actual_portable) or '无'}",
        ])
    else:
        lines.extend([
            f"- 冻结子级计划：{'、'.join(item['id'] for item in plan['childPlans'])}",
            f"- 冻结共享契约：{'、'.join(item['name'] for item in plan['sharedContracts']) or '无'}",
        ])
    development_files = (((report.get("development") or {}).get("artifact") or {}).get("changedFiles", []))
    lines.extend([
        f"- 已记录变更：{'、'.join(scope.get('changedFiles') or development_files) or '无'}",
        f"- 范围外变更：{'、'.join(scope.get('outOfScopeFiles', [])) or '无'}",
        "",
        "## 问题与建议",
        "",
    ])
    findings = (gate_artifact or {}).get("findings", {})
    lines.extend([
        f"- P0：{len(findings.get('p0', []))}",
        f"- P1：{len(findings.get('p1', []))}",
        f"- P2：{len(findings.get('p2', []))}",
        "",
        "## 独立验收",
        "",
        f"- {report['review']['artifact']['reviewer']}：{report['review']['artifact']['verdict']}" if report.get("review") else "- 尚未完成。",
        "",
        "## 用户确认",
        "",
        f"- {report['userConfirmation']['artifact']['confirmedBy']}：已确认" if report.get("userConfirmation") else "- 尚未确认。",
        "",
    ])
    return "\n".join(lines)


def render_task_handoff(context: dict[str, Any]) -> str:
    operation_id = context["operation"]["operationId"] if context.get("operation") else "<claim-required>"
    result_template = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "TASK_RESULT",
        "taskId": context["task"]["id"],
        "operationId": operation_id,
        "status": "IMPLEMENTED|BLOCKED",
        "summary": "<development facts>",
        "changedFiles": [],
        "tests": [{"argv": ["<exact frozen argv>"], "exitCode": 0, "testsRun": 0}],
        "blockers": [],
    }
    display_operation = context["operation"]["operationId"] if context.get("operation") else "尚未认领；不得开始开发"
    pretty = lambda value: json.dumps(value, ensure_ascii=False, indent=2, separators=(",", ": "))
    return "\n".join([
        "请在一个全新的开发会话中实现以下已冻结 Task。",
        "",
        f"Task：{context['task']['id']}",
        f"Baseline fingerprint：{context['task']['baselineFingerprint']}",
        f"Gate level：{context['gateLevel']}",
        f"开发建议：{context['developmentMode']}",
        f"Operation ID：{display_operation}",
        "",
        "以下冻结上下文是完整权威。不要重新分析原始需求、改变验收标准或继承其他会话的隐含假设。",
        "",
        "执行规则：",
        "- 只实现这个冻结的叶子 Task，并且只写入 task.authorizedFileChanges；验证修正文件是原 Task 的追加授权，不是新需求。",
        "- 不修改 SQLite、baseline、进度投影、`.git/**` 或外部状态。",
        "- 运行列出的测试命令，只报告真实存在的证据。",
        "- 不提交、推送、发布，也不得自行报告 PASS。",
        "- 最终只返回 IMPLEMENTED 或 BLOCKED，并携带当前 Operation ID、变更文件和测试事实。",
        "- 宿主必须用 task-result 回收结果；返回开发结果后必须继续验收，IMPLEMENTED 不是完成状态。",
        "- 门禁通过后仍需独立验收、生成用户验收报告并取得用户确认。",
        "",
        "结果返回格式（由治理宿主以 --evidence - 从 stdin 直接交给 task-result；不要生成临时 JSON 文件）：",
        "```json",
        pretty(result_template),
        "```",
        "",
        "冻结上下文：",
        "```json",
        pretty(context),
        "```",
        "",
    ])
