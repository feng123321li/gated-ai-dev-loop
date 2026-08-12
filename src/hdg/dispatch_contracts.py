from __future__ import annotations

from .jsonio import fingerprint


DISPATCH_POLICY_VERSION = "HOST_NATIVE_RESERVATION_ROUTING_V7"
HOST_NATIVE_DISPATCH_TRANSPORT = "HOST_NATIVE"

# Receiver Agent IDs select host-native receiver families, not development
# models. A new Adapter must provide a trusted request workspace and honor
# the same reservation, operation, lease, and project-scope boundary.
HOST_ADAPTER_RECEIVER_AGENTS = {
    "claude-code": "claude-code",
    "codex": "codex",
    "zcode": "zcode",
}


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
    "automatic_dispatch_decision_fingerprint",
)
