from __future__ import annotations

from typing import Any


GRAPH_ASSET_PATHS = {
    "execution": "assets/execution-graph.svg",
    "governance": "assets/governance-graph.svg",
    "developmentFlow": "assets/development-flow.svg",
    "nodeStateMachine": "assets/node-state-machine.svg",
}

KIND_LABELS = {
    "TASK_EXECUTION": "任务执行 / Task Execution",
    "TASK_GATE": "任务门禁 / Task Gate",
    "CAPABILITY_GATE": "能力门禁 / Capability Gate",
    "DELIVERY_GATE": "交付门禁 / Delivery Gate",
    "ROOT_REVIEW": "根级审查 / Root Review",
    "USER_CONFIRMATION": "用户确认 / User Confirmation",
}

KIND_COLORS = {
    "TASK_EXECUTION": ("#e0f2fe", "#0284c7"),
    "TASK_GATE": ("#dcfce7", "#16a34a"),
    "CAPABILITY_GATE": ("#fef3c7", "#d97706"),
    "DELIVERY_GATE": ("#ffedd5", "#ea580c"),
    "ROOT_REVIEW": ("#ede9fe", "#7c3aed"),
    "USER_CONFIRMATION": ("#fee2e2", "#dc2626"),
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

STATE_COLORS = {
    "PENDING": ("#f1f5f9", "#64748b"),
    "READY": ("#dbeafe", "#2563eb"),
    "CLAIMED": ("#e0f2fe", "#0284c7"),
    "SUCCEEDED": ("#dcfce7", "#16a34a"),
    "BLOCKED": ("#fee2e2", "#dc2626"),
    "PAUSED": ("#fef3c7", "#d97706"),
    "CANCELLED": ("#e5e7eb", "#4b5563"),
    "COMPLETED": ("#d1fae5", "#059669"),
}

EDGE_LABELS = {
    "ON_SUCCESS": "成功 / Success",
    "REQUIRES_PASS": "通过后 / Requires Pass",
    "ALL_OF": "全部汇聚 / All Of",
}


def _escape(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _short(value: object, limit: int = 30) -> str:
    text = str(value)
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def _svg_document(
    *,
    width: int,
    height: int,
    title: str,
    subtitle: str,
    body: list[str],
) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">'
        ),
        f"  <title id=\"title\">{_escape(title)}</title>",
        f"  <desc id=\"desc\">{_escape(subtitle)}</desc>",
        "  <defs>",
        '    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">',
        '      <path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"/>',
        "    </marker>",
        '    <filter id="shadow" x="-10%" y="-10%" width="120%" height="130%">',
        '      <feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#0f172a" flood-opacity="0.12"/>',
        "    </filter>",
        "    <style>",
        "      text { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; fill: #0f172a; }",
        "      .title { font-size: 26px; font-weight: 700; }",
        "      .subtitle { font-size: 14px; fill: #475569; }",
        "      .node-title { font-size: 15px; font-weight: 700; }",
        "      .node-subtitle { font-size: 12px; fill: #334155; }",
        "      .edge-label { font-size: 11px; fill: #475569; }",
        "      .lane-label { font-size: 14px; font-weight: 700; fill: #475569; }",
        "    </style>",
        "  </defs>",
        f'  <rect x="0" y="0" width="{width}" height="{height}" rx="18" fill="#f8fafc"/>',
        f'  <text x="36" y="42" class="title">{_escape(title)}</text>',
        f'  <text x="36" y="68" class="subtitle">{_escape(subtitle)}</text>',
        *body,
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


def _rounded_box(
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    title: str,
    subtitle: str = "",
    fill: str = "#ffffff",
    stroke: str = "#64748b",
    dashed: bool = False,
) -> list[str]:
    dash = ' stroke-dasharray="7 5"' if dashed else ""
    center = x + width / 2
    title_y = y + (height / 2 - 3 if subtitle else height / 2 + 5)
    lines = [
        (
            f'  <rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" '
            f'rx="14" fill="{fill}" stroke="{stroke}" stroke-width="2"{dash} filter="url(#shadow)"/>'
        ),
        f'  <text x="{center:.1f}" y="{title_y:.1f}" text-anchor="middle" class="node-title">{_escape(_short(title, 38))}</text>',
    ]
    if subtitle:
        lines.append(
            f'  <text x="{center:.1f}" y="{title_y + 22:.1f}" text-anchor="middle" class="node-subtitle">{_escape(_short(subtitle, 42))}</text>'
        )
    return lines


def _edge(
    *,
    source: tuple[float, float],
    target: tuple[float, float],
    label: str = "",
    dashed: bool = False,
    bend: float | None = None,
) -> list[str]:
    sx, sy = source
    tx, ty = target
    middle = bend if bend is not None else (sy + ty) / 2
    dash = ' stroke-dasharray="7 5"' if dashed else ""
    lines = [
        (
            f'  <path d="M {sx:.1f} {sy:.1f} C {sx:.1f} {middle:.1f}, {tx:.1f} {middle:.1f}, {tx:.1f} {ty:.1f}" '
            f'fill="none" stroke="#64748b" stroke-width="2" marker-end="url(#arrow)"{dash}/>'
        )
    ]
    if label:
        lx = (sx + tx) / 2
        ly = middle - 6
        label_width = max(70, min(180, len(label) * 7))
        lines.extend([
            f'  <rect x="{lx - label_width / 2:.1f}" y="{ly - 14:.1f}" width="{label_width}" height="20" rx="8" fill="#f8fafc" opacity="0.95"/>',
            f'  <text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" class="edge-label">{_escape(label)}</text>',
        ])
    return lines


def _plane_nodes_edges(
    graph: dict[str, Any],
    plane: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = [node for node in graph["nodes"] if plane in node["planes"]]
    node_ids = {node["id"] for node in nodes}
    edges = []
    for edge in graph["edges"]:
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            continue
        if plane == "EXECUTION" and edge["plane"] != plane:
            continue
        edges.append(edge)
    return nodes, edges


def _node_levels(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, int]:
    order = {node["id"]: index for index, node in enumerate(nodes)}
    indegree = {node["id"]: 0 for node in nodes}
    successors = {node["id"]: [] for node in nodes}
    for edge in edges:
        indegree[edge["target"]] += 1
        successors[edge["source"]].append(edge["target"])
    queue = sorted(
        (node_id for node_id, count in indegree.items() if count == 0),
        key=order.__getitem__,
    )
    levels = {node["id"]: 0 for node in nodes}
    visited: list[str] = []
    while queue:
        current = queue.pop(0)
        visited.append(current)
        for target in sorted(successors[current], key=order.__getitem__):
            levels[target] = max(levels[target], levels[current] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
                queue.sort(key=order.__getitem__)
    if len(visited) != len(nodes):
        return {node["id"]: index for index, node in enumerate(nodes)}
    return levels


def render_plane_svg(graph: dict[str, Any], plane: str) -> str:
    nodes, edges = _plane_nodes_edges(graph, plane)
    levels = _node_levels(nodes, edges)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for node in nodes:
        grouped.setdefault(levels[node["id"]], []).append(node)

    node_width = 220
    node_height = 74
    horizontal_gap = 34
    vertical_gap = 92
    margin_x = 44
    top = 105
    max_columns = max((len(items) for items in grouped.values()), default=1)
    width = max(900, margin_x * 2 + max_columns * node_width + (max_columns - 1) * horizontal_gap)
    level_count = max(grouped, default=0) + 1
    height = top + level_count * node_height + max(0, level_count - 1) * vertical_gap + 105
    positions: dict[str, tuple[float, float]] = {}
    for level in sorted(grouped):
        items = grouped[level]
        row_width = len(items) * node_width + max(0, len(items) - 1) * horizontal_gap
        start_x = (width - row_width) / 2
        y = top + level * (node_height + vertical_gap)
        for index, node in enumerate(items):
            positions[node["id"]] = (start_x + index * (node_width + horizontal_gap), y)

    body: list[str] = []
    for edge in edges:
        sx, sy = positions[edge["source"]]
        tx, ty = positions[edge["target"]]
        body.extend(_edge(
            source=(sx + node_width / 2, sy + node_height),
            target=(tx + node_width / 2, ty),
            label=EDGE_LABELS[edge["kind"]],
        ))
    for node in nodes:
        x, y = positions[node["id"]]
        fill, stroke = KIND_COLORS[node["kind"]]
        body.extend(_rounded_box(
            x=x,
            y=y,
            width=node_width,
            height=node_height,
            title=node["workItemId"],
            subtitle=KIND_LABELS[node["kind"]],
            fill=fill,
            stroke=stroke,
        ))

    legend_y = height - 40
    body.extend([
        f'  <circle cx="{margin_x + 8}" cy="{legend_y}" r="6" fill="#0284c7"/>',
        f'  <text x="{margin_x + 22}" y="{legend_y + 5}" class="subtitle">任务执行 / Task</text>',
        f'  <circle cx="{margin_x + 170}" cy="{legend_y}" r="6" fill="#16a34a"/>',
        f'  <text x="{margin_x + 184}" y="{legend_y + 5}" class="subtitle">门禁 / Gate</text>',
        f'  <circle cx="{margin_x + 300}" cy="{legend_y}" r="6" fill="#7c3aed"/>',
        f'  <text x="{margin_x + 314}" y="{legend_y + 5}" class="subtitle">审查 / Review</text>',
        f'  <circle cx="{margin_x + 440}" cy="{legend_y}" r="6" fill="#dc2626"/>',
        f'  <text x="{margin_x + 454}" y="{legend_y + 5}" class="subtitle">确认 / Confirmation</text>',
    ])
    title = "执行图 / Execution Graph" if plane == "EXECUTION" else "治理图 / Governance Graph"
    subtitle = (
        "任务依赖、并行分支与分级汇聚"
        if plane == "EXECUTION"
        else "门禁、独立审查与用户最终确认"
    )
    return _svg_document(width=width, height=height, title=title, subtitle=subtitle, body=body)


def render_development_flow_svg(runtime: dict[str, Any]) -> str:
    retry = runtime["retryPolicy"]
    width = 1380
    height = 850
    body = []
    boxes = {
        "plan": (55, 125, 215, 70, "人工：方案确认", "Plan Review & Confirm", "#ffedd5", "#ea580c"),
        "freeze": (325, 125, 215, 70, "Graph：冻结合同", "Freeze Contract Graph", "#dbeafe", "#2563eb"),
        "frontier": (595, 125, 220, 70, "Graph：计算 Frontier", "Dependencies & Claims", "#dbeafe", "#2563eb"),
        "dispatch": (870, 125, 240, 70, "Graph：自动 Agent 计划", "Count, Order & Queue", "#e0f2fe", "#0284c7"),
        "agent_a": (710, 320, 220, 70, "Agent A：任务执行", "Implement & Test", "#dcfce7", "#16a34a"),
        "agent_b": (980, 320, 220, 70, "Agent B：任务执行", "Implement & Test", "#dcfce7", "#16a34a"),
        "queue": (1125, 460, 210, 70, "容量不足：排队", "Stable Queue", "#f1f5f9", "#64748b"),
        "join": (835, 500, 220, 70, "Graph：结果汇合", "Result Join", "#ede9fe", "#7c3aed"),
        "gate": (835, 650, 220, 70, "Graph：门禁与审查", "Gate & Review", "#dcfce7", "#16a34a"),
        "failure": (555, 650, 220, 70, "Graph：失败分类", "Failure Router", "#fee2e2", "#dc2626"),
        "retry": (275, 650, 220, 70, "Graph：重试与恢复", f"Max attempts: {retry['maxAttempts']}", "#fef3c7", "#d97706"),
        "final": (1110, 650, 215, 70, "人工：最终验收", "Final Acceptance", "#fee2e2", "#dc2626"),
    }
    for x, y, box_width, box_height, title, subtitle, fill, stroke in boxes.values():
        body.extend(_rounded_box(
            x=x,
            y=y,
            width=box_width,
            height=box_height,
            title=title,
            subtitle=subtitle,
            fill=fill,
            stroke=stroke,
        ))

    def bottom(name: str) -> tuple[float, float]:
        x, y, box_width, box_height, *_ = boxes[name]
        return x + box_width / 2, y + box_height

    def top(name: str) -> tuple[float, float]:
        x, y, box_width, *_ = boxes[name]
        return x + box_width / 2, y

    def left(name: str) -> tuple[float, float]:
        x, y, _, box_height, *_ = boxes[name]
        return x, y + box_height / 2

    def right(name: str) -> tuple[float, float]:
        x, y, box_width, box_height, *_ = boxes[name]
        return x + box_width, y + box_height / 2

    body.extend(_edge(source=right("plan"), target=left("freeze"), label="确认 / Confirm"))
    body.extend(_edge(source=right("freeze"), target=left("frontier")))
    body.extend(_edge(source=right("frontier"), target=left("dispatch")))
    body.extend(_edge(source=bottom("dispatch"), target=top("agent_a"), label="自动派发 / Auto"))
    body.extend(_edge(source=bottom("dispatch"), target=top("agent_b"), label="自动派发 / Auto"))
    body.extend(_edge(source=bottom("dispatch"), target=top("queue"), label="容量不足 / Limited", dashed=True))
    body.extend(_edge(source=bottom("agent_a"), target=top("join")))
    body.extend(_edge(source=bottom("agent_b"), target=top("join")))
    body.extend(_edge(source=left("queue"), target=right("join"), dashed=True))
    body.extend(_edge(source=bottom("join"), target=top("gate")))
    body.extend(_edge(source=right("gate"), target=left("final"), label="通过 / Pass"))
    body.extend(_edge(source=left("gate"), target=right("failure"), label="失败 / Fail", dashed=True))
    body.extend(_edge(source=left("failure"), target=right("retry"), label="可恢复 / Recover"))
    body.extend(_edge(source=top("retry"), target=bottom("frontier"), label="重新计算 / Recompute", dashed=True))
    return _svg_document(
        width=width,
        height=height,
        title="开发执行流程 / Development Execution Flow",
        subtitle="人工只确认方案与最终验收；中间依赖、调度、门禁与失败恢复由 Graph 管理",
        body=body,
    )


def render_node_state_machine_svg(runtime: dict[str, Any]) -> str:
    present = set(runtime["states"])
    width = 1280
    height = 690
    node_width = 178
    node_height = 64
    positions = {
        "PENDING": (70, 145),
        "READY": (300, 145),
        "CLAIMED": (530, 145),
        "SUCCEEDED": (780, 145),
        "COMPLETED": (1030, 145),
        "PAUSED": (530, 355),
        "BLOCKED": (780, 355),
        "CANCELLED": (1030, 500),
    }
    body: list[str] = [
        '  <text x="36" y="98" class="subtitle">不同节点类型共享状态集合；具体允许迁移由冻结的 nodeKinds 与 routeCondition 限制。</text>',
    ]

    def top(state: str) -> tuple[float, float]:
        x, y = positions[state]
        return x + node_width / 2, y

    def bottom(state: str) -> tuple[float, float]:
        x, y = positions[state]
        return x + node_width / 2, y + node_height

    def right(state: str) -> tuple[float, float]:
        x, y = positions[state]
        return x + node_width, y + node_height / 2

    def left(state: str) -> tuple[float, float]:
        x, y = positions[state]
        return x, y + node_height / 2

    events = {transition["eventType"] for transition in runtime["transitions"]}
    if {"PENDING", "READY"} <= present:
        body.extend(_edge(source=right("PENDING"), target=left("READY"), label="前置满足 / Ready"))
    if "TASK_CLAIMED" in events:
        body.extend(_edge(source=right("READY"), target=left("CLAIMED"), label="派发 / Dispatch"))
    if "TASK_IMPLEMENTED" in events:
        body.extend(_edge(source=right("CLAIMED"), target=left("SUCCEEDED"), label="成功 / Success"))
    if "USER_CONFIRMED" in events:
        body.extend(_edge(source=right("SUCCEEDED"), target=left("COMPLETED"), label="根完成 / Complete"))
    if {"TASK_BLOCKED", "CLAIM_LEASE_EXPIRED"} & events:
        body.extend(_edge(source=bottom("CLAIMED"), target=top("BLOCKED"), label="失败 / 失联"))
    if "NODE_RETRY_SCHEDULED" in events:
        body.extend(_edge(source=left("BLOCKED"), target=bottom("READY"), label="新 attempt / Retry"))
    if "NODE_PAUSED" in events:
        body.extend(_edge(source=bottom("CLAIMED"), target=top("PAUSED"), label="暂停 / Pause"))
    if "NODE_RESUMED" in events:
        body.extend(_edge(source=left("PAUSED"), target=bottom("READY"), label="恢复 / Resume"))
    if "GRAPH_RUN_CANCELLED" in events:
        body.extend(_edge(source=bottom("BLOCKED"), target=top("CANCELLED"), label="确认取消 / Cancel", dashed=True))
    if "GRAPH_INVALIDATED" in events:
        body.extend(_edge(source=top("SUCCEEDED"), target=bottom("READY"), label="修正失效 / Remediation", dashed=True))

    for state in runtime["states"]:
        if state not in positions:
            continue
        x, y = positions[state]
        fill, stroke = STATE_COLORS[state]
        body.extend(_rounded_box(
            x=x,
            y=y,
            width=node_width,
            height=node_height,
            title=state,
            subtitle=STATE_LABELS[state],
            fill=fill,
            stroke=stroke,
        ))
    retry = runtime["retryPolicy"]
    claim = runtime["claimPolicy"]
    body.extend([
        f'  <text x="70" y="620" class="subtitle">自动恢复：{_escape(", ".join(retry["automaticFailureClasses"]))}；最大尝试 {retry["maxAttempts"]} 次</text>',
        f'  <text x="70" y="646" class="subtitle">Claim 租约：{claim["leaseSeconds"]} 秒；心跳：{claim["heartbeatSeconds"]} 秒；宽限：{claim["graceSeconds"]} 秒</text>',
    ])
    return _svg_document(
        width=width,
        height=height,
        title="节点有限状态机 / Node Finite State Machine",
        subtitle="成功、阻断、重试、暂停、恢复、修正与取消的可视化摘要",
        body=body,
    )


def render_delivery_graph_svg_assets(graph: dict[str, Any]) -> dict[str, str]:
    return {
        GRAPH_ASSET_PATHS["execution"]: render_plane_svg(graph, "EXECUTION"),
        GRAPH_ASSET_PATHS["governance"]: render_plane_svg(graph, "GOVERNANCE"),
    }


def render_runtime_policy_svg_assets(runtime: dict[str, Any]) -> dict[str, str]:
    return {
        GRAPH_ASSET_PATHS["developmentFlow"]: render_development_flow_svg(runtime),
        GRAPH_ASSET_PATHS["nodeStateMachine"]: render_node_state_machine_svg(runtime),
    }
