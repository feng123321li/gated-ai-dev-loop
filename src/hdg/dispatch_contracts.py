from __future__ import annotations

from .jsonio import fingerprint


DISPATCH_POLICY_VERSION = "HOST_NATIVE_MODEL_ROUTING_V3"
HOST_NATIVE_DISPATCH_TRANSPORT = "HOST_NATIVE"
DISPATCH_TRANSPORTS = frozenset(
    {HOST_NATIVE_DISPATCH_TRANSPORT, "EXTERNAL_PROCESS"}
)
ANALYZED_DISPATCH_REASONING_CLASSES = frozenset(
    {"ROUTINE", "STANDARD", "HIGH"}
)
DISPATCH_REASONING_CLASSES = frozenset(
    {*ANALYZED_DISPATCH_REASONING_CLASSES, "UNCLASSIFIED"}
)


def dispatch_model_selection(reasoning_class: str) -> str:
    """Return the model-selection contract bound to a reasoning class."""

    return (
        "CURRENT_HOST_DEFAULT"
        if reasoning_class == "UNCLASSIFIED"
        else "EXPLICIT_OVERRIDE"
    )


def automatic_dispatch_decision_fingerprint(
    *,
    graph_fingerprint: str,
    node_id: str,
    agent_id: str,
    model_id: str,
    reasoning_class: str,
    dispatch_transport: str,
) -> str:
    """Bind one automatic route to its Graph, executor, and transport."""

    return fingerprint(
        {
            "policyVersion": DISPATCH_POLICY_VERSION,
            "graphFingerprint": graph_fingerprint,
            "nodeId": node_id,
            "agentId": agent_id,
            "modelId": model_id,
            "reasoningClass": reasoning_class,
            "modelSelection": dispatch_model_selection(reasoning_class),
            "dispatchTransport": dispatch_transport,
        }
    )


__all__ = (
    "DISPATCH_POLICY_VERSION",
    "DISPATCH_TRANSPORTS",
    "HOST_NATIVE_DISPATCH_TRANSPORT",
    "ANALYZED_DISPATCH_REASONING_CLASSES",
    "DISPATCH_REASONING_CLASSES",
    "automatic_dispatch_decision_fingerprint",
    "dispatch_model_selection",
)
