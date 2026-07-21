from __future__ import annotations

from typing import Any


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
        "",
        "> 本文件由治理数据库重建，仅供阅读；机器权威仍是 `governance.sqlite3`。",
        "",
        "## 执行图 / Execution Graph",
        "",
        "执行图描述任务依赖、并行、成功流转和分级汇聚。",
        "",
        *_mermaid(graph, "EXECUTION"),
        "",
        "## 治理图 / Governance Graph",
        "",
        "治理图描述门禁、独立审查和用户最终确认。",
        "",
        *_mermaid(graph, "GOVERNANCE"),
        "",
        "## 节点 / Nodes",
        "",
        "| 节点 / Node | 类型 / Kind | 平面 / Planes | 工作项 / Work item |",
        "|---|---|---|---|",
    ]
    for node in graph["nodes"]:
        lines.append(
            f"| `{node['id']}` | {NODE_LABELS[node['kind']]} | "
            f"{', '.join(PLANE_LABELS[item] for item in node['planes'])} | `{node['workItemId']}` |"
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
        "| 节点 / Node | 状态 / Status | 尝试 / Attempt | 执行者 / Owner |",
        "|---|---|---:|---|",
    ]
    for node in graph_status["nodes"]:
        lines.append(
            f"| `{node['id']}` | `{node['status']}` | "
            f"{node['attempt'] if node['attempt'] is not None else '-'} | "
            f"{node['owner'] or '-'} |"
        )
    lines.extend(
        [
            "",
            "## 事件 / Events",
            "",
            "| 序号 / ID | 时间 / Time | 事件 / Event | 节点 / Node | 操作 / Operation |",
            "|---:|---|---|---|---|",
        ]
    )
    for event in events:
        lines.append(
            f"| {event['eventId']} | {event['recordedAt']} | `{event['eventType']}` | "
            f"`{event['nodeId'] or '-'}` | `{event['operationId'] or '-'}` |"
        )
    if not events:
        lines.append("| - | - | 尚未开始 / Not started | - | - |")
    return "\n".join(lines) + "\n"


def render_frontier_dashboard(
    graph_status: dict[str, Any],
    frontier: dict[str, Any],
) -> str:
    run = graph_status.get("run")
    critical = frontier["criticalPath"]
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
        "",
        "> 本文件由事件回放和治理数据库重建，仅供阅读；机器权威是图事件链。",
        "",
        "## 关键路径 / Critical Path",
        "",
        f"- 剩余节点 / Remaining nodes: **{critical['remainingNodes']}**",
        f"- 下一汇聚 / Next join: `{critical['nextJoinNodeId'] or '-'}`",
        f"- 路径阻断 / Path blocked: `{'YES' if critical['blocked'] else 'NO'}`",
        "",
    ]
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
        "| 节点 / Node | 动作 / Action | 工作项 / Work item | 尝试 / Attempt | 并行组 / Parallel group | 关键 / Critical | 就绪原因 / Ready because | 命令提示 / Command hint |",
        "|---|---|---|---:|---|---|---|---|",
    ])
    for action in frontier["actions"]:
        reasons = ", ".join(action["readyBecause"]).replace("|", "\\|")
        hint = action["commandHint"].replace("|", "\\|")
        lines.append(
            f"| `{action['nodeId']}` | `{action['action']}` | `{action['workItemId']}` | "
            f"{action['attempt']} | `{action['parallelGroup'] or '-'}` | "
            f"{'是 / Yes' if action['critical'] else '否 / No'} | {reasons} | `{hint}` |"
        )
    if not frontier["actions"]:
        lines.append("| - | - | - | - | - | - | 无 / None | - |")

    lines.extend([
        "",
        "## 阻断节点 / Blocked Nodes",
        "",
        "| 节点 / Node | 类型 / Kind | 工作项 / Work item | 状态 / Status | 尝试 / Attempt | 阻断原因 / Blocked by |",
        "|---|---|---|---|---:|---|",
    ])
    for blocked in frontier["blocked"]:
        reasons = ", ".join(blocked["blockedBy"]).replace("|", "\\|")
        kind = NODE_LABELS.get(blocked["nodeKind"], "-")
        lines.append(
            f"| `{blocked['nodeId'] or '-'}` | {kind} | `{blocked['workItemId']}` | "
            f"`{blocked['status']}` | {blocked['attempt'] or '-'} | {reasons} |"
        )
    if not frontier["blocked"]:
        lines.append("| - | - | - | - | - | 无 / None |")
    return "\n".join(lines) + "\n"
