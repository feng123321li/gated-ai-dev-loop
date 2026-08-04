from __future__ import annotations

from typing import Any

from .orchestrator_config import (
    OrchestratorConfig,
    built_in_orchestrator_config,
    save_orchestrator_config,
)


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
    return {
        "config": config.public_summary(),
        "currentHostAdapter": host_adapter_id,
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
    "open_orchestrator_settings",
    "update_orchestrator_settings",
)
