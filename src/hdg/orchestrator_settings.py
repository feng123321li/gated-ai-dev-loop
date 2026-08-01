from __future__ import annotations

from typing import Any

from .agent_discovery import discover_available_agents
from .orchestrator_config import (
    OrchestratorConfig,
    built_in_orchestrator_config,
    save_orchestrator_config,
)


KNOWN_ADAPTER_NAMES = {
    "codex": "Codex Native",
    "claude-code": "Claude Native",
    "gemini": "Gemini Adapter",
}


def _adapter_catalog(
    *,
    config: OrchestratorConfig,
    host_adapter_id: str | None,
    discovered_agents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    discovered = {
        agent["id"]: agent
        for agent in discovered_agents
        if isinstance(agent.get("id"), str)
    }
    adapter_ids = set(config.allowed_adapters)
    adapter_ids.update(discovered)
    adapter_ids.update(KNOWN_ADAPTER_NAMES)
    if host_adapter_id:
        adapter_ids.add(host_adapter_id)

    catalog: list[dict[str, Any]] = []
    for adapter_id in sorted(adapter_ids):
        agent = discovered.get(adapter_id)
        current_host_native = adapter_id == host_adapter_id
        terminal_detected = agent is not None
        if current_host_native:
            registration_state = "CURRENT_HOST_NATIVE"
        elif terminal_detected:
            registration_state = "TERMINAL_ONLY"
        else:
            registration_state = "NOT_DETECTED"
        catalog.append(
            {
                "id": adapter_id,
                "displayName": (
                    agent.get("displayName")
                    if agent is not None
                    else KNOWN_ADAPTER_NAMES.get(adapter_id, adapter_id)
                ),
                "enabled": adapter_id in config.allowed_adapters,
                "currentHostNative": current_host_native,
                "localTerminalDetected": terminal_detected,
                "registrationState": registration_state,
                "configuredModel": (
                    agent.get("model")
                    if agent is not None
                    else None
                ),
            }
        )
    return catalog


def open_orchestrator_settings(
    *,
    root: str,
    explicit_dogfood: bool = False,
    orchestrator_config: OrchestratorConfig | None = None,
    host_adapter_id: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Return panel data without reading or writing Graph scheduler state."""

    del root, explicit_dogfood
    config = orchestrator_config or built_in_orchestrator_config()
    discovery = discover_available_agents()
    agents = discovery.get("agents", [])
    if not isinstance(agents, list):
        agents = []
    return {
        "config": config.public_summary(),
        "currentHostAdapter": host_adapter_id,
        "adapters": _adapter_catalog(
            config=config,
            host_adapter_id=host_adapter_id,
            discovered_agents=agents,
        ),
        "discoveryWarnings": discovery.get("warnings", []),
        "dispatchBoundary": {
            "crossAdapterSwitchIsAuthorizationOnly": True,
            "currentHostNativeOnly": True,
            "terminalDiscoveryDoesNotImplyNativeDispatch": True,
        },
    }


def update_orchestrator_settings(
    *,
    root: str,
    config: dict[str, Any],
    explicit_dogfood: bool = False,
    host_adapter_id: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Persist one explicitly approved complete user-level policy."""

    del root, explicit_dogfood
    saved = save_orchestrator_config(config)
    result = open_orchestrator_settings(
        root=".",
        orchestrator_config=saved,
        host_adapter_id=host_adapter_id,
    )
    result["saved"] = True
    result["appliesToCurrentMcpConnection"] = True
    return result


__all__ = (
    "KNOWN_ADAPTER_NAMES",
    "open_orchestrator_settings",
    "update_orchestrator_settings",
)
