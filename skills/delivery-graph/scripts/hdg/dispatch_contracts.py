from __future__ import annotations

from copy import deepcopy

from .jsonio import fingerprint


DISPATCH_POLICY_VERSION = "HOST_NATIVE_RESERVATION_ROUTING_V9"
HOST_NATIVE_DISPATCH_TRANSPORT = "HOST_NATIVE"

# Receiver Agent IDs select host-native receiver families. A new Adapter must
# provide a trusted request workspace and honor the same reservation,
# operation, lease, and project-scope boundary.
HOST_ADAPTER_RECEIVER_AGENTS = {
    "claude-code": "claude-code",
    "codex": "codex",
    "zcode": "zcode",
}

RECEIVER_SKILLS = {
    "TASK_LOOP": "delivery-graph-task",
    "TASK_REVIEW_LOOP": "delivery-graph-review",
    "GROUP_REVIEW_LOOP": "delivery-graph-review",
    "DELIVERY_REVIEW_LOOP": "delivery-graph-review",
}

# Hosts whose receivers invoke Skills through a native Skill tool by catalog
# name. Any other host keeps the dual-form fallback instruction.
_HOST_NATIVE_SKILL_TOOL_LABELS = {
    "claude-code": "Claude Code",
    "zcode": "ZCode",
}


def advisory_skill_hint_prompt(
    skill_hints: list[dict[str, str]],
    *,
    host_adapter_id: str | None = None,
) -> str | None:
    """Render a concrete, non-blocking native Skill hint for a receiver."""

    if not skill_hints:
        return None
    hints = deepcopy(skill_hints)
    rendered_hints = "；".join(
        f"`{item['name']}`（{item['purpose']}）" for item in hints
    )
    if host_adapter_id == "codex":
        native_invocation = "、".join(
            f"`${item['name']}`" for item in hints
        )
        host_instruction = (
            f"当前宿主是 Codex，优先用 {native_invocation} 原生触发对应 Skill。"
        )
    elif host_adapter_id in _HOST_NATIVE_SKILL_TOOL_LABELS:
        host_label = _HOST_NATIVE_SKILL_TOOL_LABELS[host_adapter_id]
        catalog_names = "、".join(f"`{item['name']}`" for item in hints)
        host_instruction = (
            f"当前宿主是 {host_label}，优先通过原生 Skill tool 按 catalog 名 "
            f"{catalog_names} 调用对应 Skill。"
        )
    else:
        codex_invocation = "、".join(
            f"`${item['name']}`" for item in hints
        )
        catalog_names = "、".join(f"`{item['name']}`" for item in hints)
        host_instruction = (
            f"Codex 使用 {codex_invocation}；其他宿主通过原生 Skill tool/命令"
            f"按 catalog 名 {catalog_names} 调用。"
        )
    return (
        "用户明确指定的共享 Skill Hint（强偏好、非 Controller 成功门禁）："
        f"{rendered_hints}。先结合真实 Loop 判断每项是否适用且当前宿主可用；"
        f"适用且可用时应在当前相应阶段优先原生触发；实现、生成器、测试和编码规范类 Skill 多数在 TASK 阶段使用。{host_instruction}"
        "只有当前阶段不适用或宿主不可用时才跳过；不阻塞 Loop、不要求用户再次确认，也不伪造已使用。"
    )


def receiver_skill_prompt(
    loop_kind: str,
    skill_hints: list[dict[str, str]],
    *,
    host_adapter_id: str | None = None,
    agent_profile_id: str | None = None,
    team_plan: dict[str, object] | None = None,
) -> str:
    """Route one isolated Loop receiver to its mandatory role Skill."""

    skill_name = RECEIVER_SKILLS.get(loop_kind)
    if skill_name is None:
        raise ValueError(f"Unsupported receiver Loop kind: {loop_kind}")
    if host_adapter_id == "codex":
        invocation = f"`${skill_name}`"
        host_instruction = f"先原生触发 {invocation}。"
    elif host_adapter_id in _HOST_NATIVE_SKILL_TOOL_LABELS:
        host_label = _HOST_NATIVE_SKILL_TOOL_LABELS[host_adapter_id]
        host_instruction = (
            f"当前宿主是 {host_label}，先通过原生 Skill tool 按 catalog 名 "
            f"`{skill_name}` 调用角色 Skill。"
        )
    else:
        host_instruction = (
            f"Codex 先原生触发 `${skill_name}`；其他宿主先通过原生 "
            f"Skill 入口按 catalog 名 `{skill_name}` 调用。"
        )
    role = "TASK 实现" if loop_kind == "TASK_LOOP" else "独立 Review"
    required = (
        f"这是 {role} receiver；{host_instruction}"
        "只处理 assignment 指定的 node，不规划、派遣或接管其他 Loop。"
        "claim 成功后立即调用 heartbeat_loop，必须早于 loop_context 解读以及"
        "任何代码检查、文件检索、依赖分析、构建、测试或 Review；即使首次返回 "
        "leaseRenewed=false / NOT_REQUIRED，原 leaseExpiresAt 继续有效，仍须每约 "
        "60 秒继续 heartbeat，直到 record_loop_result 或显式释放 claim。"
        "progress 不续租，也不改变 heartbeat 计划；primary 不得代发 heartbeat。"
        "任何预计超过 60 秒的单次阻塞操作，包括整文件 Write、"
        "大 patch、批量编辑或命令，都必须先用 "
        "heartbeat_loop(expected_command_seconds=...) 申请有上限的覆盖"
        "租约；可拆分的编辑必须改为语义小 patch，并在分块之间 heartbeat。"
    )
    team_instruction = ""
    if agent_profile_id is not None and team_plan is not None:
        helpers = team_plan.get("helpers")
        helper_ids = (
            [
                item.get("profileId")
                for item in helpers
                if isinstance(item, dict)
                and isinstance(item.get("profileId"), str)
            ]
            if isinstance(helpers, list)
            else []
        )
        rendered_helpers = "、".join(
            f"`{item}`" for item in helper_ids
        ) or "无"
        team_instruction = (
            f"本 Loop 使用专用 Team：owner profile 为 "
            f"`{agent_profile_id}`，可按需并行使用辅助 profile "
            f"{rendered_helpers}。owner 是唯一 reservation/operation/lease "
            "持有者并负责最终 record_loop_result；辅助 Agent 只返回建议性结果，"
            "不得获得控制面凭据，不得调用 dispatch、heartbeat、progress、pause "
            "或 result 等生命周期工具。"
        )
    advisory = advisory_skill_hint_prompt(
        skill_hints,
        host_adapter_id=host_adapter_id,
    )
    return required + team_instruction + (advisory or "")


def automatic_dispatch_decision_fingerprint(
    *,
    graph_fingerprint: str,
    node_id: str,
    attempt: int,
    host_adapter_id: str,
    receiver_agent_id: str,
    dispatch_transport: str,
    agent_profile_id: str,
    agent_catalog_fingerprint: str,
    team_plan_fingerprint: str,
) -> str:
    """Bind one reservation to its Graph attempt and native receiver."""

    return fingerprint(
        {
            "policyVersion": DISPATCH_POLICY_VERSION,
            "graphFingerprint": graph_fingerprint,
            "nodeId": node_id,
            "attempt": attempt,
            "hostAdapterId": host_adapter_id,
            "receiverAgentId": receiver_agent_id,
            "dispatchTransport": dispatch_transport,
            "agentProfileId": agent_profile_id,
            "agentCatalogFingerprint": agent_catalog_fingerprint,
            "teamPlanFingerprint": team_plan_fingerprint,
        }
    )


__all__ = (
    "DISPATCH_POLICY_VERSION",
    "HOST_ADAPTER_RECEIVER_AGENTS",
    "HOST_NATIVE_DISPATCH_TRANSPORT",
    "RECEIVER_SKILLS",
    "advisory_skill_hint_prompt",
    "automatic_dispatch_decision_fingerprint",
    "receiver_skill_prompt",
)
