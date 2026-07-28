from __future__ import annotations

from typing import Any

from .jsonio import fingerprint
from .svg_graphs import GRAPH_ASSET_PATHS


NODE_LABELS = {
    "TASK_EXECUTION": "任务执行 / Task Execution",
    "TASK_GATE": "任务门禁 / Task Gate",
    "CAPABILITY_GATE": "能力门禁 / Capability Gate",
    "DELIVERY_GATE": "交付门禁 / Delivery Gate",
    "ROOT_REVIEW": "根级审查 / Root Review",
    "USER_CONFIRMATION": "用户确认 / User Confirmation",
}
PLANE_LABELS = {
    "EXECUTION": "执行 / Execution",
    "GOVERNANCE": "治理 / Governance",
}
STATE_LABELS = {
    "PENDING": "等待 / Pending",
    "READY": "就绪 / Ready",
    "CLAIMED": "执行中 / Claimed",
    "SUCCEEDED": "成功 / Succeeded",
    "BLOCKED": "阻断 / Blocked",
    "PAUSED": "暂停 / Paused",
    "CANCELLED": "取消 / Cancelled",
    "COMPLETED": "完成 / Completed",
}


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
            "ON_SUCCESS": "成功 / Success",
            "REQUIRES_PASS": "通过后 / Requires Pass",
            "ALL_OF": "全部汇聚 / All Of",
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
        "# 交付图 / Delivery Graph",
        "",
        f"- 需求根 / Root: `{graph['rootId']}`",
        f"- 层级指纹 / Hierarchy fingerprint: `{graph['hierarchyFingerprint']}`",
        f"- 图指纹 / Graph fingerprint: `{graph_fingerprint}`",
        f"- 运行状态 / Run status: `{run['status'] if run else 'NOT_STARTED'}`",
        "- 共享运行时策略 / Shared runtime policy: "
        "[state-transition-graph.md](../../state-transition-graph.md)",
        "",
        "> 本文件由治理数据库重建，仅供阅读；机器权威仍是 `governance.sqlite3`。",
        "",
        "## 执行图 / Execution Graph",
        "",
        "执行图描述任务依赖、并行、成功流转和分级汇聚。",
        "",
        f"![执行图 / Execution Graph]({GRAPH_ASSET_PATHS['execution']})",
        "",
        *_details(
            "查看 Mermaid 源图 / Show Mermaid source",
            _mermaid(graph, "EXECUTION"),
        ),
        "",
        "## 治理图 / Governance Graph",
        "",
        "治理图描述门禁、独立审查和用户最终确认。",
        "",
        f"![治理图 / Governance Graph]({GRAPH_ASSET_PATHS['governance']})",
        "",
        *_details(
            "查看 Mermaid 源图 / Show Mermaid source",
            _mermaid(graph, "GOVERNANCE"),
        ),
        "",
        "<details>",
        "<summary>查看节点审计表 / Show node audit table</summary>",
        "",
        "| 节点 / Node | 类型 / Kind | 平面 / Planes | 工作项 / Work item |",
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
        "## 运行时策略 / Runtime Policy",
        "",
        f"- 最大尝试次数 / Max attempts: **{retry['maxAttempts']}**",
        f"- 自动恢复失败类 / Auto-recovery failure classes: {automatic}",
        f"- 尝试耗尽动作 / On retry exhausted: `{retry['onExhausted']}`",
        f"- 认领租约 / Claim lease: **{claim['leaseSeconds']} 秒 / seconds**",
        f"- 心跳间隔 / Heartbeat interval: **{claim['heartbeatSeconds']} 秒 / seconds**",
        f"- 竞争宽限 / Expiry grace: **{claim['graceSeconds']} 秒 / seconds**",
        f"- 认领模式 / Claim mode: `{claim['claimMode']}`",
        f"- 租约到期动作 / On lease expired: `{claim['onExpired']}`",
        "- 完整状态迁移图 / Full state transition graph: "
        "[state-transition-graph.md](../../state-transition-graph.md)",
        "",
        "> 契约依赖图保持无环；失败回退、重试、暂停与恢复由运行时有限状态机表达。",
        "> The contract dependency graph remains acyclic; runtime cycles live in the FSM.",
        "",
    ])


def render_state_transition_graph(
    runtime: dict[str, Any],
) -> str:
    lines = [
        "# 状态迁移图 / State Transition Graph",
        "",
        f"- 运行时策略指纹 / Runtime policy fingerprint: `{fingerprint(runtime)}`",
        "- 作用域 / Scope: `workspace`",
        "",
        "> 本图由控制器当前 schema v3 的共享 `runtime` 策略生成；它是工作区级可审计投影，不是第二份规则。",
        "> Generated from the shared schema v3 runtime policy used by every requirement graph in this workspace.",
        "",
        "## 开发执行流程 / Development Execution Flow",
        "",
        f"![开发执行流程 / Development Execution Flow]({GRAPH_ASSET_PATHS['developmentFlow']})",
        "",
        "<details>",
        "<summary>查看 Mermaid 源图 / Show Mermaid source</summary>",
        "",
        "```mermaid",
        "flowchart TD",
        '    A["需求冻结 / Requirement Frozen"] --> B["自动前沿计算 / Automatic Frontier Calculation"]',
        '    B --> C{"安全 READY Task / Safe READY Tasks"}',
        '    C --> S["Graph 计算 Agent 数与顺序 / Graph Calculates Agent Count & Order"]',
        '    S --> C1["自动派发任务 A / Auto-dispatch Task A"]',
        '    S --> C2["自动派发任务 B / Auto-dispatch Task B"]',
        '    S -. "容量不足则稳定排队 / Queue When Capacity Is Limited" .-> Q["执行队列 / Execution Queue"]',
        '    Q --> C1',
        '    Q --> C2',
        '    C1 --> D["结果汇合 / Result Join"]',
        '    C2 --> D',
        '    C1 -. "执行失败 / Failure" .-> F["失败分类 / Failure Classification"]',
        '    C2 -. "执行失败 / Failure" .-> F',
        '    D --> G{"门禁与审查 / Gate & Review"}',
        '    G -->|"通过 / Pass"| H["后继节点或完成 / Successor or Complete"]',
        '    G -. "未通过 / Fail" .-> F',
        '    F -->|"可重试 / Retryable"| R{"仍有尝试预算？ / Attempts Remaining?"}',
        '    R -->|"是 / Yes"| B',
        '    R -->|"否 / No"| X["尝试耗尽 / Retry Exhausted"]',
        '    F -->|"需修复 / Remediation"| M["提交修复 / Submit Remediation"]',
        '    F -->|"合约变化 / Contract Change"| V["人工评审 / Human Review"]',
        '    F -->|"外部授权 / External Authority"| U["请求用户授权 / Request Authority"]',
        '    F -->|"不可重试 / Non-retryable"| I["人工干预 / Intervention"]',
        '    M --> B',
        '    V --> B',
        '    C1 -. "暂停 / Pause" .-> P["暂停 / Paused"]',
        '    P -->|"恢复 / Resume"| B',
        '    B -. "确认取消 / Confirm Cancel" .-> Z["取消 / Cancelled"]',
        "```",
        "",
        "</details>",
        "",
        "## 节点有限状态机 / Node FSM",
        "",
        f"![节点有限状态机 / Node FSM]({GRAPH_ASSET_PATHS['nodeStateMachine']})",
        "",
        "<details>",
        "<summary>查看 Mermaid 源图 / Show Mermaid source</summary>",
        "",
        "```mermaid",
        "stateDiagram-v2",
        "    [*] --> PENDING",
    ]
    for state in runtime["states"]:
        lines.append(f'    state "{STATE_LABELS[state]}" as {state}')
    for transition in runtime["transitions"]:
        for from_state in transition["fromStates"]:
            for to_state in transition["toStates"]:
                lines.append(
                    f"    {from_state} --> {to_state}: "
                    f"{transition['eventType']} / {transition['routeCondition']}"
                )
    lines.extend([
        "```",
        "",
        "</details>",
        "",
        "<details>",
        "<summary>查看完整路由与迁移契约 / Show routing and transition contract</summary>",
        "",
        "| 事件 / Event | 起始状态 / From | 目标状态 / To | 路由条件 / Route | 自动 / Automatic | 新尝试 / New attempt |",
        "|---|---|---|---|---|---|",
    ])
    for transition in runtime["transitions"]:
        from_states = ", ".join(transition["fromStates"])
        to_states = ", ".join(transition["toStates"])
        lines.append(
            f"| `{transition['eventType']}` | `{from_states}` | `{to_states}` | "
            f"`{transition['routeCondition']}` | "
            f"{'是 / Yes' if transition['automatic'] else '否 / No'} | "
            f"{'是 / Yes' if transition['createsAttempt'] else '否 / No'} |"
        )
    lines.extend([
        "",
        "</details>",
        "",
        "## 状态说明 / State Legend",
        "",
    ])
    for state in runtime["states"]:
        terminal = state in runtime["terminalStates"]
        lines.append(
            f"- `{state}` — {STATE_LABELS[state]}"
            f"{'；终止状态 / terminal' if terminal else ''}"
        )
    return "\n".join(lines) + "\n"


def render_run_timeline(
    graph_status: dict[str, Any],
    events: list[dict[str, Any]],
) -> str:
    run = graph_status.get("run")
    lines = [
        "# 运行时间线 / Run Timeline",
        "",
        f"- 需求根 / Root: `{graph_status['rootId']}`",
        f"- 图指纹 / Graph fingerprint: `{graph_status['graphFingerprint']}`",
        f"- 运行 / Run: `{run['runId'] if run else 'NOT_STARTED'}`",
        f"- 状态 / Status: `{run['status'] if run else 'NOT_STARTED'}`",
        "",
        "## 当前节点 / Current Nodes",
        "",
        "| 节点 / Node | 状态 / Status | 尝试 / Attempt | 执行者 / Owner | 最近迁移 / Last transition | 失败分类 / Failure class | 租约到期 / Lease expires |",
        "|---|---|---:|---|---|---|---|",
    ]
    for node in graph_status["nodes"]:
        lines.append(
            f"| `{node['id']}` | `{node['status']}` | "
            f"{node['attempt'] if node['attempt'] is not None else '-'} | "
            f"{node['owner'] or '-'} | `{node.get('lastTransition') or '-'}` | "
            f"`{node.get('failureClass') or '-'}` | {node.get('leaseExpiresAt') or '-'} |"
        )
    lines.extend(
        [
            "",
            "## 事件 / Events",
            "",
            "| 序号 / ID | 时间 / Time | 事件 / Event | 节点 / Node | 操作 / Operation | 失败分类 / Failure class | 路由条件 / Route |",
            "|---:|---|---|---|---|---|---|",
        ]
    )
    for event in events:
        failure = event["payload"].get("failure") or {}
        lines.append(
            f"| {event['eventId']} | {event['recordedAt']} | `{event['eventType']}` | "
            f"`{event['nodeId'] or '-'}` | `{event['operationId'] or '-'}` | "
            f"`{failure.get('class') or event['payload'].get('failureClass') or '-'}` | "
            f"`{event['payload'].get('routeCondition') or '-'}` |"
        )
    if not events:
        lines.append("| - | - | 尚未开始 / Not started | - | - | - | - |")
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
        "# 图前沿 / Graph Frontier",
        "",
        f"- 需求根 / Root: `{graph_status['rootId']}`",
        f"- 图指纹 / Graph fingerprint: `{graph_status['graphFingerprint']}`",
        f"- 运行 / Run: `{run['runId'] if run else 'NOT_STARTED'}`",
        f"- 状态 / Status: `{run['status'] if run else 'NOT_STARTED'}`",
        f"- 可执行动作 / Actionable: **{frontier['summary']['actionable']}**",
        f"- 阻断节点 / Blocked: **{frontier['summary']['blocked']}**",
        f"- 已认领 / Claimed: **{frontier['summary']['claimed']}**",
        f"- 执行中 / In flight: **{frontier['summary'].get('inFlight', 0)}**",
        f"- 下一唤醒 / Next wake: `{frontier.get('nextWakeAt') or '-'}`",
        "",
        "> 本文件由事件回放和治理数据库重建，仅供阅读；机器权威是图事件链。",
        "",
        "## 自动 Agent 调度计划 / Automatic Agent Dispatch Plan",
        "",
        f"- 决策权威 / Authority: `{dispatch['authority']}`",
        f"- 调度策略 / Strategy: `{dispatch['strategy']}`",
        f"- 新增 Agent 目标数 / Desired new agents: **{dispatch['desiredNewAgentCount']}**",
        f"- 活动 Agent 数 / Active agents: **{dispatch['activeAgentCount']}**",
        f"- 总 Agent 目标数 / Desired total agents: **{dispatch['desiredTotalAgentCount']}**",
        f"- 并行组 / Parallel group: `{dispatch['parallelGroup'] or '-'}`",
        f"- 容量策略 / Capacity policy: `{dispatch['capacityPolicy']}`",
        f"- 认领策略 / Claim policy: `{dispatch.get('claimPolicy') or '-'}`",
        "- 排队任务保持未认领 / Queued tasks remain unclaimed: "
        f"`{'YES' if dispatch.get('queuedTasksRemainUnclaimed') else 'NO'}`",
        "- 宿主可挑选子集 / Host may select subset: "
        f"`{'YES' if dispatch['hostSelectionAllowed'] else 'NO'}`",
        "",
        "> Graph 已确定全部本轮安全任务及稳定顺序。执行端必须消费完整队列；容量不足时余项保持未认领并稳定排队，只有 worker 真正启动时才 dispatch/claim。",
        "",
        "```mermaid",
        "flowchart LR",
        '    G["Graph 前沿计算 / Graph Frontier"] --> P["自动调度计划 / Automatic Dispatch Plan"]',
    ]
    task_ids = dispatch["dispatchTaskIds"]
    if task_ids:
        for index, task_id in enumerate(task_ids, start=1):
            lines.append(f'    P --> T{index}["{index}. {task_id}"]')
        lines.append('    P -. "容量不足 / Limited Capacity" .-> Q["稳定排队 / Stable Queue"]')
    else:
        lines.append('    P --> N["无待派发 Task / No Task to Dispatch"]')
    lines.extend([
        "```",
        "",
        "## 执行中与心跳计划 / In Flight and Heartbeat Schedule",
        "",
        "| 节点 / Node | 工作项 / Work item | Operation | 状态 / Status | 心跳到期 / Heartbeat due | 租约到期 / Lease expires | 硬到期 / Hard expires | 计划动作 / Scheduled action | 命令提示 / Command hint |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    for active in frontier.get("inFlight", []):
        lines.append(
            f"| `{active['nodeId']}` | `{active['workItemId']}` | "
            f"`{active.get('operationId') or '-'}` | "
            f"`{active.get('status') or '-'}` | "
            f"{active.get('heartbeatDueAt') or '-'} | "
            f"{active.get('leaseExpiresAt') or '-'} | "
            f"{active.get('hardExpiresAt') or '-'} | "
            f"`{active.get('scheduledAction') or '-'}` | "
            f"`{active.get('commandHint') or '-'}` |"
        )
    if not frontier.get("inFlight"):
        lines.append("| - | - | - | - | - | - | - | - | - |")
    lines.extend([
        "",
        "## 关键路径 / Critical Path",
        "",
        f"- 剩余节点 / Remaining nodes: **{critical['remainingNodes']}**",
        f"- 下一汇聚 / Next join: `{critical['nextJoinNodeId'] or '-'}`",
        f"- 路径阻断 / Path blocked: `{'YES' if critical['blocked'] else 'NO'}`",
        f"- 路径暂停 / Path paused: `{'YES' if critical.get('paused') else 'NO'}`",
        "",
    ])
    if critical["nodeIds"]:
        lines.extend(["```mermaid", "flowchart LR"])
        for index, node_id in enumerate(critical["nodeIds"]):
            node = by_id[node_id]
            alias = f"C{index + 1}"
            label = f"{node['workItemId']}<br/>{NODE_LABELS[node['kind']]}<br/>{node['status']}"
            lines.append(f"    {alias}[\"{label}\"]")
            if index:
                lines.append(f"    C{index} --> {alias}")
        lines.extend(["```", ""])
    else:
        lines.extend(["已无剩余关键路径 / No remaining critical path.", ""])

    lines.extend([
        "## 可执行动作 / Actionable Actions",
        "",
        "| 节点 / Node | 动作 / Action | 迁移 / Transition | 路由 / Route | 工作项 / Work item | 尝试预算 / Attempt budget | 并行组 / Parallel group | 关键 / Critical | 就绪原因 / Ready because | Required Skills | 命令提示 / Command hint | Evidence contract |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ])
    for action in frontier["actions"]:
        reasons = ", ".join(action["readyBecause"]).replace("|", "\\|")
        hint = action["commandHint"].replace("|", "\\|")
        contract_hints = [
            reference["commandHint"]
            for reference in (
                action.get("evidenceContractRef"),
                action.get("remediationContractRef"),
            )
            if isinstance(reference, dict) and reference.get("commandHint")
        ]
        contract_hint = ("; ".join(contract_hints) or "-").replace("|", "\\|")
        required_skills = ", ".join(
            f"{item['name']}@{item['stage']}"
            for item in action.get("requiredSkills", [])
        ) or "-"
        lines.append(
            f"| `{action['nodeId']}` | `{action['action']}` | `{action.get('transition') or '-'}` | "
            f"`{action.get('routeCondition') or '-'}` | `{action['workItemId']}` | "
            f"{action['attempt']}/{action.get('maxAttempts') or '-'} "
            f"(剩余 / remaining {action.get('remainingAttempts', '-')}) | "
            f"`{action['parallelGroup'] or '-'}` | "
            f"{'是 / Yes' if action['critical'] else '否 / No'} | {reasons} | "
            f"`{required_skills}` | `{hint}` | `{contract_hint}` |"
        )
    if not frontier["actions"]:
        lines.append("| - | - | - | - | - | - | - | - | 无 / None | - | - | - |")

    lines.extend([
        "",
        "## 阻断节点 / Blocked Nodes",
        "",
        "| 节点 / Node | 类型 / Kind | 工作项 / Work item | 状态 / Status | 尝试 / Attempt | 失败分类 / Failure class | 剩余尝试 / Remaining | 建议动作 / Recommended | 最近迁移 / Last transition | 阻断原因 / Blocked by | Evidence contract |",
        "|---|---|---|---|---:|---|---:|---|---|---|---|",
    ])
    for blocked in frontier["blocked"]:
        reasons = ", ".join(blocked["blockedBy"]).replace("|", "\\|")
        kind = NODE_LABELS.get(blocked["nodeKind"], "-")
        contract_hint = (
            (blocked.get("evidenceContractRef") or {}).get("commandHint")
            or "-"
        ).replace("|", "\\|")
        lines.append(
            f"| `{blocked['nodeId'] or '-'}` | {kind} | `{blocked['workItemId']}` | "
            f"`{blocked['status']}` | {blocked['attempt'] or '-'} | "
            f"`{blocked.get('failureClass') or '-'}` | "
            f"{blocked.get('remainingAttempts') if blocked.get('remainingAttempts') is not None else '-'} | "
            f"`{blocked.get('recommendedAction') or '-'}` | "
            f"`{blocked.get('lastTransition') or '-'}` | {reasons} | "
            f"`{contract_hint}` |"
        )
    if not frontier["blocked"]:
        lines.append("| - | - | - | - | - | - | - | - | - | 无 / None | - |")
    return "\n".join(lines) + "\n"
