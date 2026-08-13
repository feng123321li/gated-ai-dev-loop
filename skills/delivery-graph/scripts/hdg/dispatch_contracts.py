from __future__ import annotations

from copy import deepcopy

from .jsonio import fingerprint


DISPATCH_POLICY_VERSION = "HOST_NATIVE_RESERVATION_ROUTING_V8"
HOST_NATIVE_DISPATCH_TRANSPORT = "HOST_NATIVE"

# Receiver Agent IDs select host-native receiver families, not development
# models. A new Adapter must provide a trusted request workspace and honor
# the same reservation, operation, lease, and project-scope boundary.
HOST_ADAPTER_RECEIVER_AGENTS = {
    "claude-code": "claude-code",
    "codex": "codex",
    "zcode": "zcode",
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
    "advisory_skill_hint_prompt",
    "automatic_dispatch_decision_fingerprint",
)
