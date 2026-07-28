from __future__ import annotations

import json
from typing import Any

from .display import DISPLAY_TIMEZONE_LABEL, format_display_timestamp
from .jsonio import fingerprint
from .svg_graphs import GRAPH_ASSET_PATHS


NODE_LABELS = {
    "TASK_EXECUTION": "任务执行",
    "TASK_GATE": "任务门禁",
    "CAPABILITY_GATE": "能力门禁",
    "DELIVERY_GATE": "交付门禁",
    "ROOT_REVIEW": "根级审查",
    "USER_CONFIRMATION": "用户确认",
}
PLANE_LABELS = {
    "EXECUTION": "执行",
    "GOVERNANCE": "治理",
}
STATE_LABELS = {
    "PENDING": "等待",
    "READY": "就绪",
    "CLAIMED": "执行中",
    "SUCCEEDED": "成功",
    "BLOCKED": "阻断",
    "PAUSED": "暂停",
    "CANCELLED": "取消",
    "COMPLETED": "完成",
    "NOT_STARTED": "尚未开始",
    "RUNNING": "运行中",
    "ACTIVE": "运行中",
}
STAGE_LABELS = {
    "DEVELOPMENT": "开发",
    "GATE": "门禁",
    "FINAL_REVIEW": "最终审查",
}


def _render_mcp_call(value: object) -> str:
    if not isinstance(value, dict):
        return "-"
    tool = value.get("tool")
    arguments = value.get("arguments")
    if not isinstance(tool, str) or not isinstance(arguments, dict):
        return "-"
    serialized = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{tool}({serialized})"


def _render_mcp_calls(record: object) -> str:
    if not isinstance(record, dict):
        return "-"
    calls: list[object] = []
    if "mcpCall" in record:
        calls.append(record["mcpCall"])
    options = record.get("mcpCallOptions")
    if isinstance(options, list):
        calls.extend(options)
    rendered = [_render_mcp_call(call) for call in calls]
    return "; ".join(item for item in rendered if item != "-") or "-"


def _mermaid(graph: dict[str, Any], plane: str) -> list[str]:
    nodes = [node for node in graph["nodes"] if plane in node["planes"]]
    node_ids = {node["id"] for node in nodes}
    aliases = {node["id"]: f"N{index + 1}" for index, node in enumerate(nodes)}
    lines = ["```mermaid", "flowchart TD"]
    for node in nodes:
        label = f"{node['workItemId']}<br/>{NODE_LABELS[node['kind']]}"
        lines.append(f"    {aliases[node['id']]}[\"{label}\"]")
    for edge in graph["edges"]:
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            continue
        if plane == "EXECUTION" and edge["plane"] != plane:
            continue
        edge_label = {
            "ON_SUCCESS": "成功",
            "REQUIRES_PASS": "通过后",
            "ALL_OF": "全部汇聚",
        }[edge["kind"]]
        lines.append(
            f"    {aliases[edge['source']]} -->|\"{edge_label}\"| {aliases[edge['target']]}"
        )
    lines.append("```")
    return lines


def _details(summary: str, contents: list[str]) -> list[str]:
    return [
        "<details>",
        f"<summary>{summary}</summary>",
        "",
        *contents,
        "",
        "</details>",
    ]


def render_delivery_graph(
    graph: dict[str, Any],
    *,
    graph_fingerprint: str,
    run: dict[str, Any] | None = None,
) -> str:
    lines = [
        "# 交付图",
        "",
        f"- 需求根：`{graph['rootId']}`",
        f"- 层级指纹：`{graph['hierarchyFingerprint']}`",
        f"- 图指纹：`{graph_fingerprint}`",
        f"- 运行状态：{STATE_LABELS.get(run['status'] if run else 'NOT_STARTED', run['status'] if run else 'NOT_STARTED')}",
        "- 共享运行时策略："
        "[state-transition-graph.md](../../state-transition-graph.md)",
        "",
        "> 本文件由治理数据库重建，仅供阅读；机器权威仍是 `governance.sqlite3`。",
        "",
        "## 执行图",
        "",
        "执行图描述任务依赖、并行、成功流转和分级汇聚。",
        "",
        f"![执行图]({GRAPH_ASSET_PATHS['execution']})",
        "",
        *_details(
            "查看 Mermaid 源图",
            _mermaid(graph, "EXECUTION"),
        ),
        "",
        "## 治理图",
        "",
        "治理图描述门禁、独立审查和用户最终确认。",
        "",
        f"![治理图]({GRAPH_ASSET_PATHS['governance']})",
        "",
        *_details(
            "查看 Mermaid 源图",
            _mermaid(graph, "GOVERNANCE"),
        ),
        "",
        "<details>",
        "<summary>查看节点审计表</summary>",
        "",
        "| 节点 | 类型 | 平面 | 工作项 |",
        "|---|---|---|---|",
    ]
    for node in graph["nodes"]:
        lines.append(
            f"| `{node['id']}` | {NODE_LABELS[node['kind']]} | "
            f"{', '.join(PLANE_LABELS[item] for item in node['planes'])} | `{node['workItemId']}` |"
        )
    lines.extend(["", "</details>"])
    return "\n".join(lines) + "\n"


def render_runtime_policy_summary(graph: dict[str, Any]) -> str:
    runtime = graph["runtime"]
    retry = runtime["retryPolicy"]
    claim = runtime["claimPolicy"]
    automatic = ", ".join(f"`{item}`" for item in retry["automaticFailureClasses"])
    return "\n".join([
        "## 运行时策略",
        "",
        f"- 最大尝试次数：**{retry['maxAttempts']}**",
        f"- 自动恢复失败类：{automatic}",
        f"- 尝试耗尽动作：`{retry['onExhausted']}`",
        f"- 认领租约：**{claim['leaseSeconds']} 秒**",
        f"- 心跳间隔：**{claim['heartbeatSeconds']} 秒**",
        f"- 竞争宽限：**{claim['graceSeconds']} 秒**",
        f"- 认领模式：`{claim['claimMode']}`",
        f"- 租约到期动作：`{claim['onExpired']}`",
        "- 完整状态迁移图："
        "[state-transition-graph.md](../../state-transition-graph.md)",
        "",
        "> 契约依赖图保持无环；失败回退、重试、暂停与恢复由运行时有限状态机表达。",
    ])


def render_state_transition_graph(
    runtime: dict[str, Any],
) -> str:
    lines = [
        "# 状态迁移图",
        "",
        f"- 运行时策略指纹：`{fingerprint(runtime)}`",
        "- 作用域：`workspace`",
        "",
        "> 本图由控制器当前 schema v3 的共享 `runtime` 策略生成；它是工作区级可审计投影，不是第二份规则。",
        "## 开发执行流程",
        "",
        f"![开发执行流程]({GRAPH_ASSET_PATHS['developmentFlow']})",
        "",
        "<details>",
        "<summary>查看 Mermaid 源图</summary>",
        "",
        "```mermaid",
        "flowchart TD",
        '    A["需求冻结"] --> B["自动计算图前沿"]',
        '    B --> C{"安全就绪任务"}',
        '    C --> S["图计算智能体数量与顺序"]',
        '    S --> C1["自动派发任务 A"]',
        '    S --> C2["自动派发任务 B"]',
        '    S -. "容量不足则稳定排队" .-> Q["执行队列"]',
        '    Q --> C1',
        '    Q --> C2',
        '    C1 --> D["结果汇合"]',
        '    C2 --> D',
        '    C1 -. "执行失败" .-> F["失败分类"]',
        '    C2 -. "执行失败" .-> F',
        '    D --> G{"门禁与审查"}',
        '    G -->|"通过"| H["后继节点或完成"]',
        '    G -. "未通过" .-> F',
        '    F -->|"可重试"| R{"仍有尝试预算？"}',
        '    R -->|"是"| B',
        '    R -->|"否"| X["尝试耗尽"]',
        '    F -->|"需修复"| M["提交修复"]',
        '    F -->|"合约变化"| V["人工评审"]',
        '    F -->|"外部授权"| U["请求用户授权"]',
        '    F -->|"不可重试"| I["人工干预"]',
        '    M --> B',
        '    V --> B',
        '    C1 -. "暂停" .-> P["暂停"]',
        '    P -->|"恢复"| B',
        '    B -. "确认取消" .-> Z["取消"]',
        "```",
        "",
        "</details>",
        "",
        "## 节点有限状态机",
        "",
        f"![节点有限状态机]({GRAPH_ASSET_PATHS['nodeStateMachine']})",
        "",
        "<details>",
        "<summary>查看 Mermaid 源图</summary>",
        "",
        "```mermaid",
        "stateDiagram-v2",
    ]
    aliases = {
        state: f"S{index + 1}"
        for index, state in enumerate(runtime["states"])
    }
    lines.append(f"    [*] --> {aliases['PENDING']}")
    for state in runtime["states"]:
        lines.append(f'    state "{STATE_LABELS[state]}" as {aliases[state]}')
    for transition in runtime["transitions"]:
        for from_state in transition["fromStates"]:
            for to_state in transition["toStates"]:
                lines.append(
                    f"    {aliases[from_state]} --> {aliases[to_state]}: "
                    f"{transition['eventType']} / {transition['routeCondition']}"
                )
    lines.extend([
        "```",
        "",
        "</details>",
        "",
        "<details>",
        "<summary>查看完整路由与迁移契约</summary>",
        "",
        "| 事件 | 起始状态 | 目标状态 | 路由条件 | 自动 | 新尝试 |",
        "|---|---|---|---|---|---|",
    ])
    for transition in runtime["transitions"]:
        from_states = "、".join(
            STATE_LABELS.get(item, item) for item in transition["fromStates"]
        )
        to_states = "、".join(
            STATE_LABELS.get(item, item) for item in transition["toStates"]
        )
        lines.append(
            f"| `{transition['eventType']}` | {from_states} | {to_states} | "
            f"`{transition['routeCondition']}` | "
            f"{'是' if transition['automatic'] else '否'} | "
            f"{'是' if transition['createsAttempt'] else '否'} |"
        )
    lines.extend([
        "",
        "</details>",
        "",
        "## 状态说明",
        "",
    ])
    for state in runtime["states"]:
        terminal = state in runtime["terminalStates"]
        lines.append(
            f"- {STATE_LABELS[state]}"
            f"{'；终止状态' if terminal else ''}"
        )
    return "\n".join(lines) + "\n"


def render_run_timeline(
    graph_status: dict[str, Any],
    events: list[dict[str, Any]],
) -> str:
    run = graph_status.get("run")
    lines = [
        "# 运行时间线",
        "",
        f"- 需求根：`{graph_status['rootId']}`",
        f"- 图指纹：`{graph_status['graphFingerprint']}`",
        f"- 运行：`{run['runId']}`" if run else "- 运行：尚未开始",
        f"- 状态：{STATE_LABELS.get(run['status'] if run else 'NOT_STARTED', run['status'] if run else 'NOT_STARTED')}",
        "",
        "## 当前节点",
        "",
        f"| 节点 | 状态 | 尝试 | 执行者 | 最近迁移 | 失败分类 | 租约到期（{DISPLAY_TIMEZONE_LABEL}） |",
        "|---|---|---:|---|---|---|---|",
    ]
    for node in graph_status["nodes"]:
        lines.append(
            f"| `{node['id']}` | {STATE_LABELS.get(node['status'], node['status'])} | "
            f"{node['attempt'] if node['attempt'] is not None else '-'} | "
            f"{node['owner'] or '-'} | `{node.get('lastTransition') or '-'}` | "
            f"`{node.get('failureClass') or '-'}` | "
            f"{format_display_timestamp(node['leaseExpiresAt']) if node.get('leaseExpiresAt') else '-'} |"
        )
    lines.extend(
        [
            "",
            "## 事件",
            "",
            f"| 序号 | 时间（{DISPLAY_TIMEZONE_LABEL}） | 事件 | 节点 | 操作 | 失败分类 | 路由条件 |",
            "|---:|---|---|---|---|---|---|",
        ]
    )
    for event in events:
        failure = event["payload"].get("failure") or {}
        lines.append(
            f"| {event['eventId']} | {format_display_timestamp(event['recordedAt'])} | `{event['eventType']}` | "
            f"`{event['nodeId'] or '-'}` | `{event['operationId'] or '-'}` | "
            f"`{failure.get('class') or event['payload'].get('failureClass') or '-'}` | "
            f"`{event['payload'].get('routeCondition') or '-'}` |"
        )
    if not events:
        lines.append("| - | - | 尚未开始 | - | - | - | - |")
    return "\n".join(lines) + "\n"


def render_frontier_dashboard(
    graph_status: dict[str, Any],
    frontier: dict[str, Any],
) -> str:
    run = graph_status.get("run")
    critical = frontier["criticalPath"]
    dispatch = frontier["dispatchPlan"]
    by_id = {node["id"]: node for node in graph_status["nodes"]}
    lines = [
        "# 图前沿",
        "",
        f"- 需求根：`{graph_status['rootId']}`",
        f"- 图指纹：`{graph_status['graphFingerprint']}`",
        f"- 运行：`{run['runId']}`" if run else "- 运行：尚未开始",
        f"- 状态：{STATE_LABELS.get(run['status'] if run else 'NOT_STARTED', run['status'] if run else 'NOT_STARTED')}",
        f"- 可执行动作：**{frontier['summary']['actionable']}**",
        f"- 阻断节点：**{frontier['summary']['blocked']}**",
        f"- 已认领：**{frontier['summary']['claimed']}**",
        f"- 执行中：**{frontier['summary'].get('inFlight', 0)}**",
        f"- 下一唤醒（{DISPLAY_TIMEZONE_LABEL}）：{format_display_timestamp(frontier['nextWakeAt']) if frontier.get('nextWakeAt') else '-'}",
        "",
        "> 本文件由事件回放和治理数据库重建，仅供阅读；机器权威是图事件链。",
        "",
        "## 自动智能体调度计划",
        "",
        f"- 决策权威：`{dispatch['authority']}`",
        f"- 调度策略：`{dispatch['strategy']}`",
        f"- 新增智能体目标数：**{dispatch['desiredNewAgentCount']}**",
        f"- 活动智能体数：**{dispatch['activeAgentCount']}**",
        f"- 总智能体目标数：**{dispatch['desiredTotalAgentCount']}**",
        f"- 并行组：`{dispatch['parallelGroup'] or '-'}`",
        f"- 容量策略：`{dispatch['capacityPolicy']}`",
        f"- 认领策略：`{dispatch.get('claimPolicy') or '-'}`",
        f"- 排队任务保持未认领：{'是' if dispatch.get('queuedTasksRemainUnclaimed') else '否'}",
        f"- 宿主可挑选子集：{'是' if dispatch['hostSelectionAllowed'] else '否'}",
        "",
        "> Graph 已确定全部本轮安全任务及稳定顺序。执行端必须消费完整队列；容量不足时余项保持未认领并稳定排队，只有 worker 真正启动时才 dispatch/claim。",
        "",
        "```mermaid",
        "flowchart LR",
        '    G["图前沿计算"] --> P["自动调度计划"]',
    ]
    task_ids = dispatch["dispatchTaskIds"]
    if task_ids:
        for index, task_id in enumerate(task_ids, start=1):
            lines.append(f'    P --> T{index}["{index}. {task_id}"]')
        lines.append('    P -. "容量不足" .-> Q["稳定排队"]')
    else:
        lines.append('    P --> N["无待派发任务"]')
    lines.extend([
        "```",
        "",
        "## 执行中与心跳计划",
        "",
        f"| 节点 | 工作项 | 操作 | 状态 | 心跳到期（{DISPLAY_TIMEZONE_LABEL}） | 租约到期（{DISPLAY_TIMEZONE_LABEL}） | 硬到期（{DISPLAY_TIMEZONE_LABEL}） | 计划动作 | MCP 调用 |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    for active in frontier.get("inFlight", []):
        active_mcp_calls = _render_mcp_calls(active).replace("|", "\\|")
        lines.append(
            f"| `{active['nodeId']}` | `{active['workItemId']}` | "
            f"`{active.get('operationId') or '-'}` | "
            f"{STATE_LABELS.get(active.get('status'), active.get('status') or '-')} | "
            f"{format_display_timestamp(active['heartbeatDueAt']) if active.get('heartbeatDueAt') else '-'} | "
            f"{format_display_timestamp(active['leaseExpiresAt']) if active.get('leaseExpiresAt') else '-'} | "
            f"{format_display_timestamp(active['hardExpiresAt']) if active.get('hardExpiresAt') else '-'} | "
            f"`{active.get('scheduledAction') or '-'}` | "
            f"`{active_mcp_calls}` |"
        )
    if not frontier.get("inFlight"):
        lines.append("| - | - | - | - | - | - | - | - | - |")
    lines.extend([
        "",
        "## 关键路径",
        "",
        f"- 剩余节点：**{critical['remainingNodes']}**",
        f"- 下一汇聚：`{critical['nextJoinNodeId'] or '-'}`",
        f"- 路径阻断：{'是' if critical['blocked'] else '否'}",
        f"- 路径暂停：{'是' if critical.get('paused') else '否'}",
        "",
    ])
    if critical["nodeIds"]:
        lines.extend(["```mermaid", "flowchart LR"])
        for index, node_id in enumerate(critical["nodeIds"]):
            node = by_id[node_id]
            alias = f"C{index + 1}"
            label = (
                f"{node['workItemId']}<br/>{NODE_LABELS[node['kind']]}"
                f"<br/>{STATE_LABELS.get(node['status'], node['status'])}"
            )
            lines.append(f"    {alias}[\"{label}\"]")
            if index:
                lines.append(f"    C{index} --> {alias}")
        lines.extend(["```", ""])
    else:
        lines.extend(["已无剩余关键路径。", ""])

    lines.extend([
        "## 可执行动作",
        "",
        "| 节点 | 动作 | 迁移 | 路由 | 工作项 | 尝试预算 | 并行组 | 关键 | 就绪原因 | 必须使用的技能 | MCP 调用 | 证据契约 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ])
    for action in frontier["actions"]:
        reasons = ", ".join(action["readyBecause"]).replace("|", "\\|")
        mcp_calls = _render_mcp_calls(action).replace("|", "\\|")
        contract_hints = [
            _render_mcp_calls(reference)
            for reference in (
                action.get("evidenceContractRef"),
                action.get("remediationContractRef"),
            )
            if isinstance(reference, dict)
        ]
        contract_hint = (
            "; ".join(item for item in contract_hints if item != "-") or "-"
        ).replace("|", "\\|")
        required_skills = ", ".join(
            f"{item['name']}@{STAGE_LABELS.get(item['stage'], item['stage'])}"
            for item in action.get("requiredSkills", [])
        ) or "-"
        lines.append(
            f"| `{action['nodeId']}` | `{action['action']}` | `{action.get('transition') or '-'}` | "
            f"`{action.get('routeCondition') or '-'}` | `{action['workItemId']}` | "
            f"{action['attempt']}/{action.get('maxAttempts') or '-'} "
            f"（剩余 {action.get('remainingAttempts', '-')}） | "
            f"`{action['parallelGroup'] or '-'}` | "
            f"{'是' if action['critical'] else '否'} | {reasons} | "
            f"`{required_skills}` | `{mcp_calls}` | `{contract_hint}` |"
        )
    if not frontier["actions"]:
        lines.append("| - | - | - | - | - | - | - | - | 无 | - | - | - |")

    lines.extend([
        "",
        "## 阻断节点",
        "",
        "| 节点 | 类型 | 工作项 | 状态 | 尝试 | 失败分类 | 剩余尝试 | 建议动作 | MCP 调用 | 最近迁移 | 阻断原因 | 证据契约 |",
        "|---|---|---|---|---:|---|---:|---|---|---|---|---|",
    ])
    for blocked in frontier["blocked"]:
        reasons = ", ".join(blocked["blockedBy"]).replace("|", "\\|")
        kind = NODE_LABELS.get(blocked["nodeKind"], "-")
        mcp_calls = _render_mcp_calls(blocked).replace("|", "\\|")
        contract_hint = _render_mcp_calls(
            blocked.get("evidenceContractRef")
        ).replace("|", "\\|")
        lines.append(
            f"| `{blocked['nodeId'] or '-'}` | {kind} | `{blocked['workItemId']}` | "
            f"{STATE_LABELS.get(blocked['status'], blocked['status'])} | {blocked['attempt'] or '-'} | "
            f"`{blocked.get('failureClass') or '-'}` | "
            f"{blocked.get('remainingAttempts') if blocked.get('remainingAttempts') is not None else '-'} | "
            f"`{blocked.get('recommendedAction') or '-'}` | "
            f"`{mcp_calls}` | "
            f"`{blocked.get('lastTransition') or '-'}` | {reasons} | "
            f"`{contract_hint}` |"
        )
    if not frontier["blocked"]:
        lines.append("| - | - | - | - | - | - | - | - | - | - | 无 | - |")
    return "\n".join(lines) + "\n"
