from __future__ import annotations

from typing import Any


EXECUTION_CHOICE_MARKDOWN = """请选择开发方式（默认：自动执行）：

1. 自动执行（默认）：立即开始自动开发。
2. 手动开发：生成 handoff；接收 CLI 启动同一 Graph，手动完成 TASK，后续审查与自动执行一致。

也可直接输入修改意见，继续需求沟通。
"""


def execution_choice_contract() -> dict[str, Any]:
    """Return the controller-owned execution-mode interaction contract."""

    return {
        "schemaVersion": 1,
        "owner": "CONTROLLER",
        "kind": "EXECUTION_MODE",
        "selectionRequired": True,
        "defaultOptionId": "AUTOMATIC",
        "recommendedOptionId": "AUTOMATIC",
        "hostQuestionToolAllowed": True,
        "freeformInput": {
            "allowed": True,
            "nextAction": "CONTINUE_REQUIREMENT_DISCUSSION",
        },
        "options": [
            {
                "id": "AUTOMATIC",
                "label": "自动执行",
                "description": "立即开始自动开发。",
                "recommended": True,
                "requiresAdditionalConfirmation": False,
                "nextAction": (
                    "PREPARE_FREEZE_AND_DISPATCH_AUTOMATICALLY"
                ),
            },
            {
                "id": "MANUAL",
                "label": "手动开发",
                "description": (
                    "生成 handoff；接收 CLI 启动同一 Graph，手动完成 TASK，"
                    "后续审查与自动执行一致。"
                ),
                "recommended": False,
                "requiresAdditionalConfirmation": False,
                "nextAction": (
                    "CREATE_HANDOFF_THEN_START_GOVERNED_MANUAL_GRAPH"
                ),
            },
        ],
        "markdown": EXECUTION_CHOICE_MARKDOWN,
    }


def manual_receiver_prompt(relative_handoff_path: str) -> str:
    """Return the exact prompt shown to and embedded for a manual receiver."""

    return (
        f"请完整读取 `{relative_handoff_path}` 以及同目录的 baseline、"
        "progress、acceptance、revisions 和 work-items，校验其中的双指纹；"
        "在任何代码检查、分析、修改或测试前，必须在实际开发工作区调用 "
        "start_manual_handoff 显式启动同一冻结 Graph。总协调上下文不得实现 TASK；"
        "每个 frontier 的 CLAIM_MANUAL_TASK 都由独立接收上下文以 MANUAL claim，"
        "并按标准 Loop 协议 heartbeat、上报进度和提交结果。随后持续消费 frontier，"
        "对 TASK/GROUP/Delivery Review 完整执行与自动模式相同的宿主原生路由、"
        "独立审查、findings 闭环和最终用户确认。不要重新规划，不要直接修改任何"
        "控制器投影，不要跳过或手工替代 Review；Plugin MCP 不可用时停止并报告 "
        "PLUGIN_MCP_UNAVAILABLE。"
    )


__all__ = (
    "EXECUTION_CHOICE_MARKDOWN",
    "execution_choice_contract",
    "manual_receiver_prompt",
)
