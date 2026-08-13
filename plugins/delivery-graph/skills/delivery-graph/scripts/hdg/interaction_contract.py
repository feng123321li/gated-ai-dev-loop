from __future__ import annotations

from typing import Any

from .dispatch_contracts import advisory_skill_hint_prompt


EXECUTION_CHOICE_MARKDOWN = """请选择开发方式（默认：自动执行）：

1. 自动执行（默认）：复用当前 workspace 串行执行；选择自动执行后若已有 Delivery 调度运行，本 Delivery 标记为排队。轮到队首后，宿主按精确工作树指纹自动 stash 既有业务改动（排除 `.layered-delivery/**`），创建或切换独立 Delivery 分支并继续调度；前一 Delivery 仍须先有可验证提交、工作树和索引干净、HEAD 未漂移且接收方已安全释放。
2. 手动开发：生成 handoff；接收 CLI 启动同一 Graph，手动完成 TASK，后续审查与自动执行一致。

也可直接输入修改意见，继续需求沟通。
"""


HOST_NATIVE_QUESTION_TOOLS = {
    "codex": "request_user_input",
    "claude-code": "AskUserQuestion",
    "zcode": "AskUserQuestion",
}


def execution_choice_contract(
    host_adapter_id: str | None = None,
    *,
    git_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the controller-owned execution-mode interaction contract."""

    active_tool = HOST_NATIVE_QUESTION_TOOLS.get(host_adapter_id or "")
    base_ref = None
    integration_target = None
    if isinstance(git_binding, dict):
        base_ref = git_binding.get("baseRef")
        integration_target = git_binding.get("integrationTarget")
    return {
        "schemaVersion": 2,
        "owner": "CONTROLLER",
        "kind": "EXECUTION_MODE",
        "applyTool": "select_execution_mode",
        "baseRef": base_ref,
        "integrationTarget": integration_target,
        "selectionRequired": True,
        "defaultOptionId": "AUTOMATIC",
        "recommendedOptionId": "AUTOMATIC",
        "presentationPolicy": {
            "preferredMode": "HOST_NATIVE_SELECTOR",
            "nativeSelectorRequiredWhenAvailable": True,
            "availabilityRule": (
                "MAPPED_TOOL_CALLABLE_IN_CURRENT_CONTEXT"
            ),
            "optionSourceField": "options",
            "selectionValueField": "id",
            "preserveOptionOrder": True,
            "preserveOptionCopy": True,
            "hostMappings": {
                host_id: {"tool": tool}
                for host_id, tool in HOST_NATIVE_QUESTION_TOOLS.items()
            },
            "fallback": {
                "allowedOnlyWhen": (
                    "MAPPED_NATIVE_SELECTOR_UNAVAILABLE"
                ),
                "mode": "EXACT_CONTROLLER_MARKDOWN",
                "contentField": "markdown",
                "agentRewriteAllowed": False,
                "typedOptionPromptAllowed": False,
            },
        },
        "activeHostMapping": (
            {
                "hostAdapterId": host_adapter_id,
                "tool": active_tool,
                "requiredWhenCallable": True,
            }
            if active_tool is not None
            else None
        ),
        "freeformInput": {
            "allowed": True,
            "nextAction": "CONTINUE_REQUIREMENT_DISCUSSION",
        },
        "options": [
            {
                "id": "AUTOMATIC",
                "label": "自动执行（当前 workspace 串行）",
                "description": (
                    "复用当前 workspace 串行执行；选择后若已有调度运行，"
                    "本 Delivery 标记排队。轮到队首后由宿主自动 stash 既有"
                    "业务改动、创建或切换独立 Delivery 分支并继续调度。"
                    "前一 Delivery 仍须先满足可验证提交、clean、HEAD 与"
                    "receiver 释放边界。"
                ),
                "recommended": True,
                "requiresAdditionalConfirmation": False,
                "nextAction": (
                    "RECORD_SELECTION_THEN_WAIT_OR_PREPARE_CURRENT_WORKSPACE"
                ),
                "workspaceContinuation": (
                    "RESUME_EXECUTION_MODE_WITHOUT_CONFIRMATION"
                ),
                "workspacePreparationAuthorization": (
                    "STASH_CREATE_OR_SWITCH_BRANCH_WITHOUT_RECONFIRMATION"
                ),
                "workspaceStrategy": "CURRENT_WORKSPACE_SERIAL",
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


DEVELOPMENT_BASELINE_MARKDOWN = """请先选择开发基线（在确认开发方式之前确定开发分支）：

从本地分支中选择一个作为开发基线，或新建 Delivery 分支。当前 workspace 位于已有 feature 分支且状态干净时，可显式选择从当前 feature 创建子分支，完成后合回该父分支。仅列出本地分支，不含远端。选择会被记住，同一 Delivery 的后续 Revision 不再重复询问；Controller 不执行任何 Git 写操作，宿主仅在 CURRENT_WORKSPACE_SERIAL 的干净、安全释放边界创建或切换分支，不创建新的 worktree。
"""


def _markdown_text(value: object) -> str:
    text = str(value)
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    for character in ("\\", "`", "*", "_", "[", "]", "#", "|"):
        text = text.replace(character, f"\\{character}")
    return (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "<br>")
    )


def _development_baseline_markdown(
    options: list[dict[str, Any]],
    default_option_id: str,
) -> str:
    lines = [DEVELOPMENT_BASELINE_MARKDOWN.rstrip(), ""]
    for index, option in enumerate(options, start=1):
        markers: list[str] = []
        if option["id"] == default_option_id:
            markers.append("默认")
        if option.get("recommended", False):
            markers.append("推荐")
        marker = f"（{'、'.join(markers)}）" if markers else ""
        lines.append(
            f"{index}. {_markdown_text(option['label'])}{marker}："
            f"{_markdown_text(option['description'])}"
        )
    lines.extend(["", "也可直接输入修改意见，继续需求沟通。", ""])
    return "\n".join(lines)


def development_baseline_contract(
    host_adapter_id: str | None = None,
    *,
    git_binding: dict[str, Any] | None = None,
    candidate_branches: list[dict[str, Any]],
    default_branch_ref: str | None,
    expected_hierarchy_fingerprint: str,
    expected_graph_fingerprint: str | None = None,
    expected_delivery_revision: int | None = None,
    baseline_context_fingerprint: str | None = None,
    interaction_context: str = "INITIAL_DELIVERY",
    working_tree: dict[str, Any] | None = None,
    stacked_base: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return the controller-owned development-baseline interaction contract.

    Sits before ``EXECUTION_MODE``: in a Git workspace with no remembered
    baseline the host presents this selector over local feature branches plus
    new-from-mainline and, when eligible, new-stacked-child options. Adopting
    the current dirty branch requires exact state-fingerprint attribution;
    choosing a different Delivery branch defers the dirty workspace to the
    explicit stash-or-wait execution preparation. The host applies the choice via
    ``confirm_development_baseline``. The presentation machinery mirrors
    ``execution_choice_contract`` verbatim so the same native question tool is
    used.
    """

    active_tool = HOST_NATIVE_QUESTION_TOOLS.get(host_adapter_id or "")
    options: list[dict[str, Any]] = [
        {
            "id": branch["branchRef"],
            "label": branch["branchRef"],
            "description": (
                f"本地分支；基线 {branch['baseRef']} @ "
                f"{branch['baseCommit'][:12]}"
            ),
            "branchRef": branch["branchRef"],
            "baseRef": branch["baseRef"],
            "baseCommit": branch["baseCommit"],
            "integrationTarget": branch["integrationTarget"],
            "headCommit": branch["headCommit"],
            "adoptable": branch["adoptable"],
            "inUseBy": branch.get("inUseBy", []),
            "recommended": branch["branchRef"] == default_branch_ref,
            "nextAction": "CONFIRM_BASELINE_THEN_PRESENT_EXECUTION_CHOICE",
        }
        for branch in candidate_branches
    ]
    if stacked_base is not None:
        options.append(
            {
                "id": "NEW_FROM_CURRENT_BRANCH",
                "label": (
                    f"从当前分支 {stacked_base['branchRef']} 创建子分支"
                ),
                "description": (
                    f"以 {stacked_base['branchRef']} @ "
                    f"{stacked_base['headCommit'][:12]} 为基线新建 Delivery "
                    "子分支，完成后合回该父分支（需提供子分支名）"
                ),
                "requiresBranchName": True,
                "stackedDelivery": True,
                "baseRef": stacked_base["branchRef"],
                "baseCommit": stacked_base["headCommit"],
                "integrationTarget": stacked_base["branchRef"],
                "recommended": True,
                "nextAction": (
                    "CONFIRM_STACKED_BASELINE_THEN_PRESENT_EXECUTION_CHOICE"
                ),
            }
        )
    options.append(
        {
            "id": "NEW_FROM_MAINLINE",
            "label": "从主线创建新分支",
            "description": "从当前主线新建一个开发分支（需提供分支名）",
            "requiresBranchName": True,
            "recommended": (
                default_branch_ref is None and stacked_base is None
            ),
            "nextAction": "CONFIRM_BASELINE_THEN_PRESENT_EXECUTION_CHOICE",
        }
    )
    base_ref = None
    if isinstance(git_binding, dict):
        base_ref = git_binding.get("baseRef")
    default_option = (
        "NEW_FROM_CURRENT_BRANCH"
        if stacked_base is not None
        else (
            default_branch_ref
            if default_branch_ref is not None
            else "NEW_FROM_MAINLINE"
        )
    )
    result = {
        "schemaVersion": 2,
        "owner": "CONTROLLER",
        "kind": "DEVELOPMENT_BASELINE",
        "applyTool": "confirm_development_baseline",
        "interactionContext": interaction_context,
        "baseRef": base_ref,
        "expectedHierarchyFingerprint": expected_hierarchy_fingerprint,
        "selectionRequired": True,
        "defaultOptionId": default_option,
        "recommendedOptionId": default_option,
        "presentationPolicy": {
            "preferredMode": "HOST_NATIVE_SELECTOR",
            "nativeSelectorRequiredWhenAvailable": True,
            "availabilityRule": (
                "MAPPED_TOOL_CALLABLE_IN_CURRENT_CONTEXT"
            ),
            "optionSourceField": "options",
            "selectionValueField": "id",
            "preserveOptionOrder": True,
            "preserveOptionCopy": True,
            "hostMappings": {
                host_id: {"tool": tool}
                for host_id, tool in HOST_NATIVE_QUESTION_TOOLS.items()
            },
            "fallback": {
                "allowedOnlyWhen": (
                    "MAPPED_NATIVE_SELECTOR_UNAVAILABLE"
                ),
                "mode": "EXACT_CONTROLLER_MARKDOWN",
                "contentField": "markdown",
                "agentRewriteAllowed": False,
                "typedOptionPromptAllowed": False,
            },
        },
        "activeHostMapping": (
            {
                "hostAdapterId": host_adapter_id,
                "tool": active_tool,
                "requiredWhenCallable": True,
            }
            if active_tool is not None
            else None
        ),
        "freeformInput": {
            "allowed": True,
            "nextAction": "CONTINUE_REQUIREMENT_DISCUSSION",
        },
        "options": options,
        "markdown": _development_baseline_markdown(options, default_option),
    }
    if expected_graph_fingerprint is not None:
        result["expectedGraphFingerprint"] = expected_graph_fingerprint
    if expected_delivery_revision is not None:
        result["expectedDeliveryRevision"] = expected_delivery_revision
    if baseline_context_fingerprint is not None:
        result["baselineContextFingerprint"] = (
            baseline_context_fingerprint
        )
    if working_tree is not None:
        result["workingTree"] = working_tree
        if not working_tree.get("clean", False):
            result["dirtyStateConfirmationRequired"] = True
            result["dirtyStateConfirmationScope"] = (
                "CURRENT_BRANCH_ADOPTION_ONLY"
            )
            result["branchTransitionDirtyHandling"] = (
                "AUTOMATIC_STASH_OR_KEEP_WAIT_AT_QUEUE_HEAD"
            )
            result["dirtyStateFingerprint"] = working_tree.get(
                "stateFingerprint"
            )
    return result


def manual_receiver_prompt(
    relative_handoff_path: str,
    skill_hints: list[dict[str, str]] | None = None,
) -> str:
    """Return the exact prompt shown to and embedded for a manual receiver."""

    base_prompt = (
        f"请完整读取 `{relative_handoff_path}` 以及同目录的 baseline、"
        "progress、acceptance、revisions 和 work-items，校验其中的双指纹；"
        "在任何代码检查、分析、修改或测试前，必须在实际开发工作区调用 "
        "start_manual_handoff 显式启动同一冻结 Graph。总协调上下文不得实现 TASK；"
        "每个 frontier 的 CLAIM_MANUAL_TASK 都由宿主原生 child 独立接收；"
        "MANUAL claim 不携带 AUTO reservation，但 child 必须提交自己的 "
        "receiver_context_id 与新 operation_id，并按标准 Loop 协议显式携带 "
        "operation_id heartbeat、上报进度和提交结果。"
        "随后持续消费 frontier，"
        "对 TASK Review、已配置的 GROUP seam Review 和 Delivery Acceptance/"
        "Readiness 完整执行与自动模式相同的宿主原生路由、独立判断、findings "
        "闭环和最终用户确认。不要重新规划，不要直接修改任何"
        "控制器投影，不要跳过或手工替代 Review；Plugin MCP 不可用时停止并报告 "
        "PLUGIN_MCP_UNAVAILABLE。"
    )
    skill_prompt = advisory_skill_hint_prompt(skill_hints or [])
    return (
        f"{base_prompt}{skill_prompt}"
        if skill_prompt is not None
        else base_prompt
    )


__all__ = (
    "DEVELOPMENT_BASELINE_MARKDOWN",
    "EXECUTION_CHOICE_MARKDOWN",
    "HOST_NATIVE_QUESTION_TOOLS",
    "development_baseline_contract",
    "execution_choice_contract",
    "manual_receiver_prompt",
)
