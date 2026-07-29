from __future__ import annotations

from typing import Any

from .display import DISPLAY_TIMEZONE_LABEL, format_display_timestamp



from .model_core import (
    KIND_TEXT,
    GATE_LEVEL_TEXT,
    AUTHORITY_TEXT,
    SKILL_STAGE_TEXT,
    iter_hierarchy_nodes,
)


def raw_definition(definition: dict[str, Any]) -> dict[str, Any]:
    omitted = {"authorityKind", "parentContractFingerprint"}
    if definition.get("kind") == "DELIVERY":
        omitted.add("parentId")
    return {key: value for key, value in definition.items() if key not in omitted}

def _list(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values)

def render_work_item_baseline(definition: dict[str, Any]) -> str:
    lines = [
        "# 工作项基线",
        "",
        f"工作项：{definition['id']}",
        f"类型：{KIND_TEXT[definition['kind']]}",
        f"门禁等级：{GATE_LEVEL_TEXT[definition['gateLevel']]}",
        f"权威类型：{AUTHORITY_TEXT[definition['authorityKind']]}",
        f"父级：{definition['parentId'] or '无'}",
        f"父级契约：{definition['parentContractFingerprint'] or '无'}",
        "",
        "## 目标",
        definition["goal"],
        "",
        "## 范围",
        _list(definition["scope"]),
        "",
        "## 非目标",
        _list(definition["nonGoals"]),
        "",
        "## 需求",
    ]
    for requirement in definition["requirements"]:
        lines.extend([f"### {requirement['id']}", requirement["text"], ""])
    lines.append("## 验收项")
    for acceptance in definition["acceptance"]:
        lines.extend([
            f"### {acceptance['id']} [{','.join(acceptance['requirementIds'])}]",
            acceptance["expectedResult"],
            "",
        ])
    if "children" in definition:
        lines.extend([
            "## 分解",
            f"- 状态：{'已规划' if definition['decomposition']['status'] == 'PLANNED' else definition['decomposition']['status']}",
        ])
        if definition["kind"] == "CAPABILITY":
            lines.append(f"- 能力依赖：{', '.join(definition['decomposition']['dependsOn']) or '无'}")
        lines.extend(["", "## 子级"])
        for child in definition["children"]:
            lines.append(
                f"- {child['id']} [{KIND_TEXT[child['kind']]}] [{','.join(child['requirementIds'])}] "
                f"[{','.join(child['acceptanceIds'])}] {child['title']}"
            )
    else:
        lines.extend([
            "## 执行",
            f"- 依赖：{', '.join(definition['execution']['dependsOn']) or '无'}",
            f"- 输入：{'; '.join(definition['execution']['inputs']) or '无'}",
            f"- 输出：{'; '.join(definition['execution']['outputs'])}",
        ])
    import json

    lines.extend(["", "## 测试命令"])
    lines.extend(f"- {json.dumps(argv, ensure_ascii=False, separators=(',', ':'))}" for argv in definition["testCommands"])
    lines.extend(["", "## 必须使用的技能"])
    if definition["requiredSkills"]:
        lines.extend(
            f"- {item['name']} [{'、'.join(SKILL_STAGE_TEXT[stage] for stage in item['stages'])}]：{item['purpose']}"
            for item in definition["requiredSkills"]
        )
    else:
        lines.append("- 无")
    lines.extend([
        "",
        "## 开发方案契约",
        definition["developmentPlan"]["purpose"],
        "",
        "- 完整可读方案：[development-plan.md](development-plan.md)",
        "- 结构化方案权威：项目治理 SQLite 数据库",
        "",
        "## 风险",
        _list(definition["risks"]),
        "",
        "## 决策",
        _list(definition["decisions"]),
        "",
    ])
    return "\n".join(lines)

def _review_status_text(state: dict[str, Any]) -> str:
    review = state.get("review", {})
    if review.get("status") == "APPROVED":
        return (
            f"已由人工确认（{review['reviewedBy']}，"
            f"{format_display_timestamp(review['reviewedAt'])}，{DISPLAY_TIMEZONE_LABEL}）"
        )
    return "等待人工评审；尚未冻结，禁止开始开发"

def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")

def render_development_plan(definition: dict[str, Any], state: dict[str, Any]) -> str:
    plan = definition["developmentPlan"]
    lines = [
        f"# 开发方案：{definition['title']}",
        "",
        f"- 工作项：{definition['id']}",
        f"- 层级：{KIND_TEXT[definition['kind']]}",
        f"- 门禁等级：{GATE_LEVEL_TEXT[definition['gateLevel']]}",
        f"- 基线指纹：{state['baselineFingerprint']}",
        f"- 评审状态：{_review_status_text(state)}",
        f"- 开发目的：{plan['purpose']}",
        "",
        "## 需求与验收边界",
        "",
        "| 需求 | 内容 |",
        "| --- | --- |",
    ]
    lines.extend(f"| {item['id']} | {_markdown_cell(item['text'])} |" for item in definition["requirements"])
    lines.extend([
        "",
        "| 验收 | 覆盖需求 | 预期结果 |",
        "| --- | --- | --- |",
    ])
    lines.extend(
        f"| {item['id']} | {', '.join(item['requirementIds'])} | {_markdown_cell(item['expectedResult'])} |"
        for item in definition["acceptance"]
    )
    lines.extend([
        "",
        "## 必须使用的技能",
        "",
        "> 名称是可移植的技能目录标识；`/skill`、`$skill` 等宿主调用语法不进入基线。",
        "> 必需技能是执行指令，不是业务需求分析输入；用户仅指定开发使用时，不预分析、不递归展开，也不自动加入门禁。",
        "> 本方案中的必需技能已在准备前由宿主级与项目级技能目录联合验证存在；执行阶段仍须由实际执行者原生调用。",
        "",
        "| 技能 | 适用阶段 | 使用目的 |",
        "| --- | --- | --- |",
    ])
    if definition["requiredSkills"]:
        lines.extend(
            f"| `{item['name']}` | {'、'.join(SKILL_STAGE_TEXT[stage] for stage in item['stages'])} | "
            f"{_markdown_cell(item['purpose'])} |"
            for item in definition["requiredSkills"]
        )
    else:
        lines.append("| 无 | - | 本节点未追加技能要求；仍须遵守从祖先基线继承的要求。 |")
    lines.append("")

    if definition["kind"] == "TASK":
        lines.extend([
            "## 变更场景",
            "",
            "| 场景 | 标题 | 开发内容 | 覆盖需求 |",
            "| --- | --- | --- | --- |",
        ])
        lines.extend(
            f"| {item['kind']} | {_markdown_cell(item['title'])} | {_markdown_cell(item['description'])} | {', '.join(item['requirementIds'])} |"
            for item in plan["scenarios"]
        )
        lines.extend([
            "",
            "## 文件改动",
            "",
            "| 动作 | 文件 | 目的 |",
            "| --- | --- | --- |",
        ])
        lines.extend(
            f"| {item['action']} | `{item['path']}` | {_markdown_cell(item['purpose'])} |"
            for item in plan["fileChanges"]
        )
        if plan["generatedFileRoots"]:
            lines.extend([
                "",
                "### 仅新增生成目录",
                "",
                "> 这些目录只授权新增生成文件；修改或删除既有文件仍须逐文件登记。",
                "",
                "| 目录 | 目的 |",
                "| --- | --- |",
            ])
            lines.extend(
                f"| `{item['path']}` | {_markdown_cell(item['purpose'])} |"
                for item in plan["generatedFileRoots"]
            )
        lines.extend(["", "## 接口与功能契约", ""])
        if not plan["interfaces"]:
            lines.append("- 本任务不新增、修改或删除外部/内部接口。")
        else:
            lines.extend([
                "| 动作 | 类型 | 名称与位置 | 当前契约 | 目标契约 | 覆盖需求 |",
                "| --- | --- | --- | --- | --- | --- |",
            ])
            lines.extend(
                f"| {item['action']} | {item['kind']} | {_markdown_cell(item['name'])}<br>"
                f"{_markdown_cell(item['location'])} | {_markdown_cell(item['currentContract'])} | "
                f"{_markdown_cell(item['targetContract'])} | {', '.join(item['requirementIds'])} |"
                for item in plan["interfaces"]
            )
        lines.extend(["", "## 实现逻辑", ""])
        lines.extend(f"- {item}" for item in plan["logic"])
        lines.extend(["", "## 数据与事务", ""])
        lines.extend(
            [f"- {item}" for item in plan["dataAndTransactions"]]
            or ["- 不涉及数据模型、持久化或事务边界变更。"]
        )
        lines.extend(["", "## 兼容性", ""])
        lines.extend(f"- {item}" for item in plan["compatibility"])
    else:
        child_label = "能力" if definition["kind"] == "DELIVERY" else "任务"
        lines.extend([
            f"## {child_label}开发内容",
            "",
            f"| {child_label} | 开发目的 | 交付内容 | 依赖 | 需求/验收 |",
            "| --- | --- | --- | --- | --- |",
        ])
        lines.extend(
            f"| {item['id']} | {_markdown_cell(item['purpose'])} | "
            f"{_markdown_cell('；'.join(item['deliverables']))} | {', '.join(item['dependsOn']) or '无'} | "
            f"{', '.join(item['requirementIds'])} / {', '.join(item['acceptanceIds'])} |"
            for item in plan["childPlans"]
        )
        lines.extend(["", f"## 跨{child_label}接口与共享契约", ""])
        if not plan["sharedContracts"]:
            lines.append(f"- 无跨{child_label}共享接口；子级仅通过冻结输出和聚合门禁组合。")
        else:
            lines.extend([
                "| 类型 | 契约 | 提供方 | 消费方 | 说明 | 覆盖需求 |",
                "| --- | --- | --- | --- | --- | --- |",
            ])
            lines.extend(
                f"| {item['kind']} | {_markdown_cell(item['name'])} | {', '.join(item['providerChildIds'])} | "
                f"{', '.join(item['consumerChildIds'])} | {_markdown_cell(item['description'])} | "
                f"{', '.join(item['requirementIds'])} |"
                for item in plan["sharedContracts"]
            )
        lines.extend(["", "## 集成流程", ""])
        lines.extend(f"- {item}" for item in plan["integrationFlow"])
        lines.extend([
            "",
            "## 开发与集成波次",
            "",
            "| 波次 | 名称 | 子级 | 退出条件 |",
            "| --- | --- | --- | --- |",
        ])
        lines.extend(
            f"| {item['order']} | {_markdown_cell(item['name'])} | {', '.join(item['childIds'])} | "
            f"{_markdown_cell(item['exitCriteria'])} |"
            for item in plan["deliveryWaves"]
        )
    lines.extend([
        "",
        "## 测试与验收映射",
        "",
        "| 验收项 | 验证方法 | 冻结命令序号 |",
        "| --- | --- | --- |",
    ])
    lines.extend(
        f"| {', '.join(item['acceptanceIds'])} | {_markdown_cell(item['approach'])} | "
        f"{', '.join(str(index) for index in item['commandIndexes'])} |"
        for item in plan["testPlan"]
    )
    lines.extend(["", "## 人工评审重点", ""])
    lines.extend(f"- {item}" for item in plan["reviewPoints"])
    lines.extend([
        "",
        "## 冻结说明",
        "",
        "- 请先评审本文件中的开发目的、内容、文件、接口/共享契约、依赖波次和测试映射。",
        "- 如需修改，先修改结构化定义并重新准备；不要冻结错误版本。",
        "- 人工评审当前开发方案并选择 `active`（自动）或 `manual`（手动）后一次确认，无需复制或复述指纹。",
        "- 智能体必须使用展示本方案时保存的当前指纹调用冻结；方案已变化时控制器会拒绝旧确认。",
        "",
    ])
    return "\n".join(lines)

def render_hierarchy_plan(
    hierarchy: dict[str, Any],
    states: dict[str, dict[str, Any]],
    hierarchy_state: dict[str, Any],
) -> str:
    """Render the single human plan for one complete requirement tree."""
    kind_text = {"DELIVERY": "交付", "CAPABILITY": "能力", "TASK": "任务"}
    review = hierarchy_state["review"]
    review_text = (
        f"已由人工确认（{review['reviewedBy']}，{format_display_timestamp(review['reviewedAt'])}，{DISPLAY_TIMEZONE_LABEL}）"
        if review["status"] == "APPROVED"
        else "等待人工评审；尚未冻结，禁止开始开发"
    )
    lines = [
        "# 需求层级开发方案",
        "",
        f"- 根工作项：{hierarchy_state['rootId']}",
        f"- 层级指纹：{hierarchy_state['hierarchyFingerprint']}",
        f"- 方案状态：{review_text}",
        "- 确认方式：人工评审本文件、选择 `active`（自动）或 `manual`（手动）后一次确认，无需复制或复述指纹。",
        "",
        "## 层级结构",
        "",
    ]

    def append_tree(node: dict[str, Any], prefix: str, connector: str) -> None:
        definition = node["definition"]
        lines.append(
            f"{prefix}{connector}{kind_text[definition['kind']]} "
            f"[`{definition['id']}`](#work-item-{definition['id']})：{definition['title']}"
        )
        children = node["children"]
        for index, child in enumerate(children):
            last = index == len(children) - 1
            child_prefix = prefix + (
                "" if connector == "" else ("   " if connector == "└─ " else "│  ")
            )
            append_tree(child, child_prefix, "└─ " if last else "├─ ")

    append_tree(hierarchy["root"], "", "")

    for node in iter_hierarchy_nodes(hierarchy):
        definition = node["definition"]
        item_plan = render_development_plan(definition, states[definition["id"]]).splitlines()
        lines.extend([
            "",
            f'<a id="work-item-{definition["id"]}"></a>',
            "",
            f"## {kind_text[definition['kind']]}：{definition['id']} — {definition['title']}",
            "",
        ])
        for line in item_plan[1:]:
            lines.append("#" + line if line.startswith("## ") else line)

    lines.extend([
        "",
        "## 统一冻结说明",
        "",
        "- 本文件一次展示并绑定整棵当前需求树的所有 baseline、接口、文件、依赖波次和测试映射。",
        "- 需要修改时重新准备整棵树；旧层级指纹会自动失效。",
        "- 人工选择根级开发方式并确认本文件后，智能体使用已保存的层级指纹一次记录方式并冻结全部节点。",
        "- 冻结后不得静默新增或修改节点；需求边界变化时停止执行并重新规划完整需求树。",
        "",
    ])
    return "\n".join(lines)
