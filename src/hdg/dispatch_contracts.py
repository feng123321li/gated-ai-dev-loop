from __future__ import annotations

from copy import deepcopy

from .jsonio import fingerprint


DISPATCH_POLICY_VERSION = "HOST_NATIVE_RESERVATION_ROUTING_V8"
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
    elif host_adapter_id == "claude-code":
        native_invocation = "、".join(
            f"`{item['name']}`" for item in hints
        )
        host_instruction = (
            "当前宿主是 Claude Code，优先通过原生 Skill tool 按 catalog 名 "
            f"{native_invocation} 调用对应 Skill。"
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
) -> str:
    """Route one isolated Loop receiver to its mandatory role Skill."""

    skill_name = RECEIVER_SKILLS.get(loop_kind)
    if skill_name is None:
        raise ValueError(f"Unsupported receiver Loop kind: {loop_kind}")
    if host_adapter_id == "codex":
        invocation = f"`${skill_name}`"
        host_instruction = f"先原生触发 {invocation}。"
    elif host_adapter_id == "claude-code":
        invocation = f"`{skill_name}`"
        host_instruction = (
            "先通过原生 Skill tool 按 catalog 名 "
            f"{invocation} 调用角色 Skill。"
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
    )
    advisory = advisory_skill_hint_prompt(
        skill_hints,
        host_adapter_id=host_adapter_id,
    )
    return required + (advisory or "")


def automatic_dispatch_decision_fingerprint(
    *,
    graph_fingerprint: str,
    node_id: str,
    attempt: int,
    host_adapter_id: str,
    receiver_agent_id: str,
    dispatch_transport: str,
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
