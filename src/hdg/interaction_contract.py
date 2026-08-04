from __future__ import annotations

from typing import Any


EXECUTION_CHOICE_MARKDOWN = """请选择开发方式（默认：自动执行）：

1. 自动执行（默认）：立即开始自动开发。
2. 手动开发：生成 handoff，供任意 CLI 开发。

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
                "description": "生成 handoff，供任意 CLI 开发。",
                "recommended": False,
                "requiresAdditionalConfirmation": False,
                "nextAction": (
                    "CREATE_HANDOFF_AND_PRESENT_RECEIVER_PROMPT"
                ),
            },
        ],
        "markdown": EXECUTION_CHOICE_MARKDOWN,
    }


def manual_receiver_prompt(relative_handoff_path: str) -> str:
    """Return the exact prompt shown to and embedded for a manual receiver."""

    return (
        f"请完整读取 `{relative_handoff_path}` 以及同目录的 baseline、"
        "progress、acceptance、revisions 和 work-items，校验其中的双指纹，"
        "在实际开发工作区直接开发，严格遵循冻结内容并维护 progress/acceptance；"
        "不要重新规划，不要修改 handoff、overview、baseline、revisions、interfaces "
        "或双指纹，也不要隐式启动 Graph。"
    )


__all__ = (
    "EXECUTION_CHOICE_MARKDOWN",
    "execution_choice_contract",
    "manual_receiver_prompt",
)
