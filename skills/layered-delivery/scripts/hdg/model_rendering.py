from __future__ import annotations

import json
from typing import Any

from .model_core import iter_hierarchy_nodes


KIND_TEXT = {
    "DELIVERY": "交付",
    "CAPABILITY": "能力",
    "TASK": "任务 Loop",
}


def raw_definition(
    definition: dict[str, Any],
) -> dict[str, Any]:
    return dict(definition)


def render_work_item_baseline(
    definition: dict[str, Any],
) -> str:
    """Render only scheduler-visible metadata.

    Loop payloads remain opaque and are shown as JSON for auditability.
    """

    lines = [
        "# 调度基线",
        "",
        f"工作项：{definition['id']}",
        f"类型：{KIND_TEXT[definition['kind']]}",
        f"标题：{definition['title']}",
        f"摘要：{definition['summary']}",
    ]
    if definition["kind"] == "TASK":
        loop = definition["execution"]["loop"]
        lines.extend(
            [
                f"父级：{definition['parentId'] or '无'}",
                f"依赖：{', '.join(definition['execution']['dependsOn']) or '无'}",
                "",
                "## Task Loop",
                "",
                f"- 引用：{loop['ref']}",
                f"- 资源声明：{', '.join(loop['resourceClaims']) or '无'}",
                "- Payload：",
                "```json",
                json.dumps(
                    loop["payload"],
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "```",
            ]
        )
    else:
        dependencies = definition["decomposition"].get(
            "dependsOn",
            [],
        )
        lines.extend(
            [
                f"父级：{definition.get('parentId') or '无'}",
                f"依赖：{', '.join(dependencies) or '无'}",
                "",
                "## 子级调度单元",
                "",
            ]
        )
        lines.extend(
            f"- {child['id']} [{KIND_TEXT[child['kind']]}] {child['title']}"
            for child in definition["children"]
        )
    return "\n".join(lines) + "\n"


def render_scheduling_plan(
    hierarchy: dict[str, Any],
) -> str:
    lines = [
        "# Graph 调度总览",
        "",
        "实现规范、测试、门禁与 Skill 激活由各 Loop 内部负责。",
        "Skill 提示在 Loop 启动后按真实上下文选择，不预先绑定节点。",
        "",
        "## Skill 提示",
        "",
    ]
    hints = hierarchy["skillHints"]
    if hints:
        lines.extend(
            f"- {hint['name']}：{hint['purpose']}"
            for hint in hints
        )
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 节点",
            "",
        ]
    )
    for node in iter_hierarchy_nodes(hierarchy):
        definition = node["definition"]
        lines.append(
            f"- {definition['id']} [{KIND_TEXT[definition['kind']]}] "
            f"{definition['title']}"
        )
    review = hierarchy["reviewLoop"]
    lines.extend(
        [
            "",
            "## 最终审查 Loop",
            "",
            f"- 引用：{review['ref']}",
            f"- 资源声明：{', '.join(review['resourceClaims']) or '无'}",
        ]
    )
    return "\n".join(lines) + "\n"


__all__ = (
    "raw_definition",
    "render_scheduling_plan",
    "render_work_item_baseline",
)
