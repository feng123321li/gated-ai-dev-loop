from __future__ import annotations

from .jsonio import fingerprint


DISPATCH_POLICY_VERSION = "HOST_NATIVE_RECEIVER_ROUTING_V5"
HOST_NATIVE_DISPATCH_TRANSPORT = "HOST_NATIVE"
DISPATCH_TRANSPORTS = frozenset(
    {HOST_NATIVE_DISPATCH_TRANSPORT, "EXTERNAL_PROCESS"}
)

# Receiver Agent IDs are host lifecycle identities, not development-model
# recommendations.  A new Adapter must implement the same native receiver
# attestation boundary before it is added here.
HOST_ADAPTER_RECEIVER_AGENTS = {
    "claude-code": "claude-code",
    "codex": "codex",
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
    "DISPATCH_TRANSPORTS",
    "HOST_ADAPTER_RECEIVER_AGENTS",
    "HOST_NATIVE_DISPATCH_TRANSPORT",
    "automatic_dispatch_decision_fingerprint",
)
